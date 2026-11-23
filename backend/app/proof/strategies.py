"""Per-category proof strategies.

Two kinds:

* **Static** — deterministic and free. Where a flaw can be established by
  reading structure (a live credential shape, a route with no auth in its
  dependency chain), executing anything would be theatre.
* **Executed** — the model writes an experiment, the sandbox runs it, and the
  *verdict comes from the exit state*, never from the model's opinion of its
  own output. The script must print a machine-readable marker; anything else
  is inconclusive.
"""

from __future__ import annotations

import ast
import base64
import binascii
import json
import math
import re
from dataclasses import dataclass
from typing import Any

from app.proof.models import ProofResult

# The contract every executed proof must satisfy.
VERDICT_MARKER = "PROOF_VERDICT:"
EVIDENCE_MARKER = "PROOF_EVIDENCE:"
VULNERABLE = "VULNERABLE"
NOT_VULNERABLE = "NOT_VULNERABLE"


@dataclass
class Target:
    """The finding under examination, plus the file it lives in."""

    path: str
    source: str
    category: str
    severity: str
    title: str
    description: str
    line_start: int | None = None
    code_snippet: str | None = None

    @property
    def language(self) -> str:
        return "Python" if self.path.endswith((".py", ".pyi")) else "other"

    def window(self, radius: int = 12) -> str:
        """The lines around the finding, numbered."""
        lines = self.source.splitlines()
        if not self.line_start:
            return "\n".join(f"{i:>4}| {ln}" for i, ln in enumerate(lines[:60], 1))
        lo = max(0, self.line_start - radius - 1)
        hi = min(len(lines), self.line_start + radius)
        return "\n".join(
            f"{i:>4}| {ln}" for i, ln in enumerate(lines[lo:hi], lo + 1)
        )


# ---------------------------------------------------------------------------
# Static: hardcoded credentials
# ---------------------------------------------------------------------------

# Prefixes that identify a *live* credential, not a test or sample value.
_LIVE_SHAPES: list[tuple[str, re.Pattern[str], str]] = [
    ("stripe_live", re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}"), "Stripe live secret key"),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key id"),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), "GitHub access token"),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "Slack token"),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), "Google API key"),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{32,}\b"), "OpenAI API key"),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{32,}\b"), "Anthropic API key"),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"), "Private key block"),
]

# Values that look like credentials but are clearly not real.
_PLACEHOLDER = re.compile(
    r"(?i)(example|changeme|placeholder|your[_-]?|xxx+|\*{4,}|dummy|sample|"
    r"^test$|fake|redacted|<[^>]+>|\$\{|os\.environ|getenv|process\.env)"
)
_TEST_KEY = re.compile(r"(?i)\b(?:sk|pk|rk)_test_")

_ASSIGNMENT = re.compile(
    r"""(?im)^\s*(?P<name>[A-Za-z_][\w.]*)\s*[:=]\s*["'](?P<value>[^"'\s]{12,})["']"""
)
# A bare `key` is included deliberately (SUPABASE_KEY, SERVICE_KEY,
# ENCRYPTION_KEY all matter). The value-side gates below — placeholder
# filtering plus an entropy and length floor — are what keep CACHE_KEY = "u:1"
# from being reported.
_SECRETY_NAME = re.compile(
    r"(?i)(secret|password|passwd|token|key|credential|auth|jwt|bearer|dsn)"
)


def _shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts: dict[str, int] = {}
    for ch in value:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(value)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _supabase_service_role(value: str) -> bool:
    """A Supabase service-role JWT bypasses row-level security entirely."""
    parts = value.split(".")
    if len(parts) != 3:
        return False
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return False
    return payload.get("role") == "service_role"


# A secret found 40 lines away is not evidence for a finding about line 5.
_PROXIMITY_LINES = 12


def _too_far(target: Target, line: int) -> bool:
    """Is this match plausibly the thing the finding was about?

    Without this check, any finding mentioning "secret" on a file that happens
    to contain a real key elsewhere would be marked proven using unrelated
    evidence — a false proof, which is worse than no proof.
    """
    if not target.line_start:
        return False
    return abs(line - target.line_start) > _PROXIMITY_LINES


def prove_hardcoded_secret(target: Target) -> ProofResult:
    """Classify by shape. We never call the provider to test a key."""
    source = target.source
    near_misses: list[str] = []

    for kind, pattern, label in _LIVE_SHAPES:
        match = pattern.search(source)
        if not match:
            continue
        value = match.group(0)
        if _PLACEHOLDER.search(value) or _TEST_KEY.search(value):
            continue
        line = source[: match.start()].count("\n") + 1
        if _too_far(target, line):
            near_misses.append(f"{label} at line {line}")
            continue
        return ProofResult.proven(
            kind=f"secret_shape:{kind}",
            summary=f"A real {label} is committed in the source code.",
            evidence=(
                f"{target.path}:{line} contains a value matching the live "
                f"{label} format ({value[:6]}…{value[-4:]}, {len(value)} chars). "
                "Anyone with read access to this repository can use it."
            ),
        )

    for match in _ASSIGNMENT.finditer(source):
        name, value = match.group("name"), match.group("value")
        if _PLACEHOLDER.search(value) or _TEST_KEY.search(value):
            continue
        line = source[: match.start()].count("\n") + 1
        if _too_far(target, line):
            near_misses.append(f"credential in `{name}` at line {line}")
            continue

        # A service-role JWT is unambiguous whatever the variable is called,
        # so this check does not wait for a secret-looking name.
        if _supabase_service_role(value):
            return ProofResult.proven(
                kind="secret_shape:supabase_service_role",
                summary="A Supabase service-role key is committed in the source.",
                evidence=(
                    f"{target.path}:{line} assigns `{name}` a JWT whose payload "
                    'declares "role":"service_role". That key bypasses row-level '
                    "security and can read or delete every row in the database."
                ),
            )

        if not _SECRETY_NAME.search(name):
            continue
        entropy = _shannon_entropy(value)
        if entropy >= 3.2 and len(value) >= 16:
            return ProofResult.proven(
                kind="secret_shape:high_entropy",
                summary=f"A credential is hardcoded in `{name}`.",
                evidence=(
                    f"{target.path}:{line} assigns `{name}` a literal "
                    f"{len(value)}-character value with {entropy:.1f} bits/char of "
                    "entropy — consistent with a real secret, not a placeholder. "
                    "It is readable by anyone with repository access."
                ),
            )

    if near_misses:
        # There are real credentials in this file, but none near the reported
        # line. Proving the wrong one would be a false proof.
        return ProofResult.inconclusive(
            kind="secret_shape",
            summary=(
                "Credentials exist in this file ("
                + "; ".join(near_misses[:3])
                + f") but none within {_PROXIMITY_LINES} lines of the reported "
                "location, so this particular finding was not confirmed."
            ),
        )

    return ProofResult.refuted(
        kind="secret_shape",
        summary=(
            "No live-credential shape found — the values here look like "
            "placeholders, test keys or environment lookups."
        ),
    )


# ---------------------------------------------------------------------------
# Static: routes with no authentication
# ---------------------------------------------------------------------------

_ROUTE_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "route"}
_AUTH_HINT = re.compile(
    r"(?i)(auth|login|token|session|current_user|require|permission|jwt|"
    r"api_key|verify|credential|principal|identity|guard)"
)


def _decorator_route(node: ast.AST) -> str | None:
    """Return 'METHOD /path' when this decorator registers an HTTP route."""
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    if node.func.attr.lower() not in _ROUTE_METHODS:
        return None
    owner = node.func.value
    owner_name = getattr(owner, "id", None) or getattr(owner, "attr", "")
    if owner_name not in {"app", "router", "api", "bp", "blueprint"}:
        return None
    path = ""
    if node.args and isinstance(node.args[0], ast.Constant):
        path = str(node.args[0].value)
    return f"{node.func.attr.upper()} {path}".strip()


def _handler_has_auth(fn: ast.FunctionDef | ast.AsyncFunctionDef, source: str) -> bool:
    """Does anything in this handler's signature or body check the caller?"""
    args = fn.args
    for arg in [*args.args, *args.posonlyargs, *args.kwonlyargs]:
        if _AUTH_HINT.search(arg.arg):
            return True
        if arg.annotation is not None and _AUTH_HINT.search(ast.unparse(arg.annotation)):
            return True
    for default in [*args.defaults, *(d for d in args.kw_defaults if d)]:
        if _AUTH_HINT.search(ast.unparse(default)):
            return True
    for dec in fn.decorator_list:
        if _AUTH_HINT.search(ast.unparse(dec)):
            return True
    body = ast.unparse(fn) if fn.body else ""
    return bool(_AUTH_HINT.search(body))


def prove_missing_auth(target: Target) -> ProofResult:
    """Trace every route in the file and report the ones with no auth check."""
    if target.language != "Python":
        return ProofResult.inconclusive(
            kind="route_reachability",
            summary="Route analysis currently understands Python only.",
        )
    try:
        tree = ast.parse(target.source)
    except SyntaxError as exc:
        return ProofResult.inconclusive(
            kind="route_reachability", summary=f"File does not parse: {exc.msg}"
        )

    unprotected: list[tuple[str, str, int]] = []
    total = 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        routes = [r for r in (_decorator_route(d) for d in node.decorator_list) if r]
        if not routes:
            continue
        total += len(routes)
        if not _handler_has_auth(node, target.source):
            for route in routes:
                unprotected.append((route, node.name, node.lineno))

    if not total:
        return ProofResult.inconclusive(
            kind="route_reachability",
            summary="No HTTP routes were found in this file.",
        )
    if not unprotected:
        return ProofResult.refuted(
            kind="route_reachability",
            summary=f"All {total} route(s) in this file check the caller.",
        )

    listed = "\n".join(
        f"  · {route} → {fn}() at {target.path}:{line} — no caller check"
        for route, fn, line in unprotected[:8]
    )
    return ProofResult.proven(
        kind="route_reachability",
        summary=(
            f"{len(unprotected)} of {total} HTTP endpoint(s) in this file accept "
            "requests from anyone."
        ),
        evidence=(
            "Tracing each route to its handler, these have no authentication in "
            f"the signature, decorators or body:\n{listed}\n"
            "Anyone who knows the URL can call them."
        ),
    )


# ---------------------------------------------------------------------------
# Static: passwords hashed with a fast digest
# ---------------------------------------------------------------------------

# Fast, unsalted digests. Fine for checksums, disastrous for passwords: a
# commodity GPU tries billions of candidates per second against them.
_FAST_DIGESTS = {"md5", "sha1", "sha256", "sha512", "sha224", "sha384"}
_PASSWORD_ARG = re.compile(r"(?i)(password|passwd|pwd|passphrase|secret)")
# Purpose-built password hashes, which are the correct answer.
_SLOW_HASHES = re.compile(
    r"(?i)\b(bcrypt|argon2|scrypt|pbkdf2|passlib|werkzeug\.security|"
    r"generate_password_hash|CryptContext)\b"
)


def _hashes_a_password(call: ast.Call) -> str | None:
    """Return the digest name when this call hashes something password-shaped."""
    func = call.func
    name = None
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        if func.value.id == "hashlib" and func.attr.lower() in _FAST_DIGESTS:
            name = func.attr.lower()
        elif func.value.id == "hashlib" and func.attr == "new" and call.args:
            first = call.args[0]
            if isinstance(first, ast.Constant) and str(first.value).lower() in _FAST_DIGESTS:
                name = str(first.value).lower()
    if name is None:
        return None
    # The digest only matters if what it consumes is a credential.
    return name if _PASSWORD_ARG.search(ast.unparse(call)) else None


def prove_weak_password_hash(target: Target) -> ProofResult:
    """A password run through a fast digest is a fact readable from the AST."""
    if target.language != "Python":
        return ProofResult.inconclusive(
            kind="weak_hash", summary="Hash analysis currently understands Python only."
        )
    try:
        tree = ast.parse(target.source)
    except SyntaxError as exc:
        return ProofResult.inconclusive(
            kind="weak_hash", summary=f"File does not parse: {exc.msg}"
        )

    hits: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            digest = _hashes_a_password(node)
            if digest and not _too_far(target, node.lineno):
                hits.append((digest, node.lineno))

    if not hits:
        if _SLOW_HASHES.search(target.source):
            return ProofResult.refuted(
                kind="weak_hash",
                summary="This file uses a purpose-built password hash, not a fast digest.",
            )
        return ProofResult.inconclusive(
            kind="weak_hash",
            summary="No password being passed through a fast digest was found here.",
        )

    # Cite the call closest to the reported line. A file can hash in several
    # places, and evidence pointing at a different function reads as sloppy
    # even when the finding itself is correct.
    if target.line_start:
        hits.sort(key=lambda h: abs(h[1] - target.line_start))
    digest, line = hits[0]
    return ProofResult.proven(
        kind=f"weak_hash:{digest}",
        summary=f"Passwords are hashed with {digest.upper()}.",
        evidence=(
            f"{target.path}:{line} passes a password through hashlib.{digest}. "
            f"{digest.upper()} is a fast, unsalted digest: commodity hardware tests "
            "billions of guesses per second against it, so a stolen database "
            "yields plaintext passwords in hours. Password hashing needs bcrypt, "
            "scrypt or argon2."
        ),
    )


# ---------------------------------------------------------------------------
# Executed proofs
# ---------------------------------------------------------------------------

# Category-specific guidance for the experiment the model has to design.
EXECUTION_RECIPES: dict[str, str] = {
    "command_injection": (
        "Import the vulnerable function and call it with an argument containing a "
        "shell metacharacter that creates the file `./canary.txt` (for example "
        "`; touch canary.txt` or `$(touch canary.txt)`). Afterwards check whether "
        "`canary.txt` exists. If it does, the argument escaped into a shell."
    ),
    "path_traversal": (
        "Create a file `secret.txt` in the sandbox working directory and a "
        "subdirectory that the code treats as its safe root. Call the function "
        "with a traversing filename such as `../secret.txt`. If the function "
        "reads or writes outside its intended root, the traversal is real."
    ),
    "deserialization": (
        "Build a pickle payload whose __reduce__ writes the file `./canary.txt`, "
        "feed it through the code path that deserialises untrusted data, and then "
        "check whether `canary.txt` exists."
    ),
    "sql_injection": (
        "Create a scratch SQLite database with the table the code expects and two "
        "rows. Call the vulnerable function with an input containing "
        "`' OR '1'='1` (or an equivalent for the query shape). The flaw is proven "
        "if the call returns a row it should not have matched, or if the SQL "
        "clearly changes shape. Do not use an in-memory DB if the code opens its "
        "own connection — create the file the code will open."
    ),
    "input_validation": (
        "Call the function or handler with input that a real caller could send "
        "(missing key, wrong type, negative number) and show that it raises an "
        "unhandled exception rather than rejecting the input cleanly."
    ),
    "resource_leak": (
        "Call the function repeatedly and assert that the resource it opens is "
        "never closed — for example by inspecting the returned object's `closed` "
        "attribute, or by counting open file descriptors before and after."
    ),
}


def recipe_for(target: Target) -> str | None:
    """Pick the experiment recipe that matches this finding."""
    haystack = f"{target.title} {target.description} {target.category}".lower()
    ordered = [
        ("command_injection", ("command injection", "shell=true", "os.system", "subprocess")),
        ("path_traversal", ("path traversal", "directory traversal", "../", "arbitrary file")),
        ("deserialization", ("deserial", "pickle", "yaml.load", "marshal")),
        ("sql_injection", ("sql injection", "sql inject", "concatenat", "query string")),
        ("input_validation", ("validation", "unvalidated", "keyerror", "missing error handling")),
        ("resource_leak", ("not closed", "leak", "unclosed", "file handle", "connection")),
    ]
    for name, needles in ordered:
        if any(n in haystack for n in needles):
            return name
    return None


def build_proof_prompt(target: Target, recipe: str) -> str:
    """The user message that asks the model to design one experiment."""
    return f"""\
## Finding to test
File: {target.path}
Severity claimed: {target.severity} | Category: {target.category}
Title: {target.title}
Description: {target.description}

## Code around the finding
```python
{target.window()}
```

## The full file is available in the sandbox as `target_module.py`
Import it with `import target_module`.

## Experiment to run
{EXECUTION_RECIPES[recipe]}

## Contract your script MUST follow
- Print exactly one line `{VERDICT_MARKER} {VULNERABLE}` or `{VERDICT_MARKER} {NOT_VULNERABLE}`.
- Print one line `{EVIDENCE_MARKER} <what you observed>` describing the concrete
  observation — the canary file that appeared, the row that came back, the
  exception that escaped.
- Never modify `target_module.py`. Proving a bug by editing the source proves
  nothing and will be rejected.
- Do not access the network. Do not read outside the working directory except
  to demonstrate a traversal.
- Wrap everything in try/except. If the code cannot be exercised (missing
  dependency, needs a live server), print `{VERDICT_MARKER} {NOT_VULNERABLE}`
  and explain why in the evidence line.
- Output only the Python script, no prose and no markdown fences.
"""


def interpret(result_output: str, exit_code: int) -> tuple[str | None, str]:
    """Read the verdict out of the script's own output.

    The verdict comes from what ran, not from anyone's interpretation of it.
    """
    verdict: str | None = None
    evidence = ""
    for line in result_output.splitlines():
        stripped = line.strip()
        if stripped.startswith(VERDICT_MARKER):
            token = stripped[len(VERDICT_MARKER):].strip().upper()
            if token in (VULNERABLE, NOT_VULNERABLE):
                verdict = token
        elif stripped.startswith(EVIDENCE_MARKER):
            evidence = stripped[len(EVIDENCE_MARKER):].strip()
    return verdict, evidence


# Models write "hard-coded" with an ASCII hyphen, a non-breaking hyphen or a
# unicode dash depending on the day, so normalise before matching.
# The non-ASCII dashes are the whole point of this table, hence the noqa.
_DASHES = str.maketrans({"‐": "-", "‑": "-", "–": "-", "—": "-"})  # noqa: RUF001

_SECRET_CUES = ("hardcoded", "hard-coded", "secret", "credential", "api key",
                "apikey", "token", "private key")
_AUTH_CUES = ("no auth", "without auth", "missing auth", "unauthenticated",
              "no authentication", "authorization", "publicly accessible",
              "anyone can", "unprotected endpoint", "lacks authentication")
_HASH_CUES = ("md5", "sha1", "sha-1", "weak hash", "insecure hash",
              "password hashing", "hashed with", "unsalted", "fast hash",
              "insecure password")


def static_strategy_for(target: Target) -> Any:
    """The deterministic strategy for this finding, if one applies.

    Order matters: several cues overlap ("MD5 token includes the secret key"
    hits all three), and the most specific strategy should win.
    """
    haystack = f"{target.title} {target.description}".lower().translate(_DASHES)

    if any(cue in haystack for cue in _HASH_CUES):
        return prove_weak_password_hash
    if any(cue in haystack for cue in _AUTH_CUES):
        return prove_missing_auth
    if any(cue in haystack for cue in _SECRET_CUES):
        return prove_hardcoded_secret
    return None
