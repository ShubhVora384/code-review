"""Each strategy must prove a genuinely-bad snippet and refute its fixed twin.

A strategy that only ever says "vulnerable" is worse than no strategy: it
recreates the false-positive problem the proof engine exists to remove.
"""

import pytest

from app.proof.models import ProofStatus
from app.proof.strategies import (
    NOT_VULNERABLE,
    VULNERABLE,
    Target,
    interpret,
    prove_hardcoded_secret,
    prove_missing_auth,
    recipe_for,
    static_strategy_for,
)


def target(source: str, *, title: str = "finding", path: str = "app/x.py", **kw) -> Target:
    return Target(
        path=path,
        source=source,
        category=kw.pop("category", "security"),
        severity=kw.pop("severity", "critical"),
        title=title,
        description=kw.pop("description", ""),
        **kw,
    )


# --------------------------------------------------------------- secrets


@pytest.mark.parametrize(
    "source,label",
    [
        ('STRIPE = "sk_' 'live_4eC39HqLyjWDarjtT1zdp7dc"', "stripe"),
        ('AWS_KEY = "AKIA2QT4RBWZ9KLMN3PQ"', "aws"),
        ('GH = "ghp_' '16C7e42F292c6912E7710c838347Ae178B4a"', "github"),
        ('SECRET_KEY = "8f14e45fceea167a5a36dedd4bea2543aa77b1e9"', "high entropy"),
    ],
)
def test_real_credentials_are_proven(source, label):
    result = prove_hardcoded_secret(target(source))
    assert result.status is ProofStatus.PROVEN, f"{label} should be proven"
    assert result.evidence, "a proof must say what it observed"


@pytest.mark.parametrize(
    "source",
    [
        'STRIPE = "sk_' 'test_4eC39HqLyjWDarjtT1zdp7dc"',      # test key
        'SECRET_KEY = os.environ["SECRET_KEY"]',             # env lookup
        'PASSWORD = "your-password-here"',                   # placeholder
        'API_KEY = "changeme"',                              # placeholder
        'TIMEOUT_SECONDS = "30"',                            # not a secret at all
    ],
)
def test_non_credentials_are_refuted(source):
    assert prove_hardcoded_secret(target(source)).status is ProofStatus.REFUTED


def test_supabase_service_role_key_is_proven():
    # header.payload.signature where payload declares role=service_role
    jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJyb2xlIjoic2VydmljZV9yb2xlIiwiaXNzIjoic3VwYWJhc2UifQ."
        "dGhpcy1pcy1ub3QtYS1yZWFsLXNpZ25hdHVyZQ"
    )
    result = prove_hardcoded_secret(target(f'SUPABASE_KEY = "{jwt}"'))
    assert result.status is ProofStatus.PROVEN
    assert "service_role" in result.evidence
    assert "row-level security" in result.evidence


def test_secret_proof_reports_the_line():
    source = 'import os\n\nAPI_KEY = "AKIA2QT4RBWZ9KLMN3PQ"\n'
    result = prove_hardcoded_secret(target(source))
    assert ":3" in result.evidence


@pytest.mark.parametrize(
    "source",
    [
        'CACHE_KEY = "user:123"',
        'SORT_KEY = "created_at"',
        'PRIMARY_KEY = "id"',
        'AUTH_HEADER_NAME = "Authorization"',
        'TOKEN_TTL_LABEL = "3600 seconds"',
    ],
)
def test_key_shaped_names_holding_ordinary_values_are_refuted(source):
    """The name pattern matches a bare `key`, so the value-side entropy and
    length gates are what prevent this from becoming a noise machine."""
    assert prove_hardcoded_secret(target(source)).status is ProofStatus.REFUTED


def test_secret_evidence_does_not_print_the_whole_key():
    key = "AKIA2QT4RBWZ9KLMN3PQ"
    result = prove_hardcoded_secret(target(f'AWS = "{key}"'))
    assert key not in result.evidence, "the report must not restate the secret"


# ------------------------------------------------------------- route auth


UNPROTECTED = '''
from fastapi import FastAPI
app = FastAPI()

@app.get("/items")
def list_items():
    return {"items": []}

@app.post("/items")
def create_item(payload: dict):
    return payload
'''

PROTECTED = '''
from fastapi import Depends, FastAPI
from .auth import current_user
app = FastAPI()

@app.get("/items")
def list_items(user=Depends(current_user)):
    return {"items": []}

@app.post("/items")
def create_item(payload: dict, user=Depends(current_user)):
    return payload
'''


def test_unprotected_routes_are_proven():
    result = prove_missing_auth(target(UNPROTECTED, path="app/main.py"))
    assert result.status is ProofStatus.PROVEN
    assert "2 of 2" in result.summary
    assert "GET /items" in result.evidence


def test_protected_routes_are_refuted():
    result = prove_missing_auth(target(PROTECTED, path="app/main.py"))
    assert result.status is ProofStatus.REFUTED


def test_auth_inside_the_handler_body_counts():
    source = '''
from fastapi import FastAPI
app = FastAPI()

@app.get("/admin")
def admin(token: str = None):
    if not validate_token(token):
        return {"error": "unauthorized"}
    return {"ok": True}
'''
    assert prove_missing_auth(target(source)).status is ProofStatus.REFUTED


def test_file_without_routes_is_inconclusive():
    result = prove_missing_auth(target("def helper():\n    return 1\n"))
    assert result.status is ProofStatus.INCONCLUSIVE


def test_unparseable_file_is_inconclusive_not_proven():
    result = prove_missing_auth(target("def broken(:\n"))
    assert result.status is ProofStatus.INCONCLUSIVE


def test_non_python_is_inconclusive():
    result = prove_missing_auth(target("router.get('/x', h)", path="app/routes.js"))
    assert result.status is ProofStatus.INCONCLUSIVE


# ------------------------------------------------------------- dispatch


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Command injection via unsanitised arguments", "command_injection"),
        ("Path traversal vulnerability in upload handler", "path_traversal"),
        ("Unsafe deserialization with pickle", "deserialization"),
        ("SQL injection via string concatenation", "sql_injection"),
        ("Missing validation of incoming data", "input_validation"),
        ("File handle not closed when reading", "resource_leak"),
    ],
)
def test_recipe_selection(title, expected):
    assert recipe_for(target("x = 1", title=title)) == expected


def test_unknown_category_has_no_recipe():
    assert recipe_for(target("x = 1", title="Variable naming could be clearer")) is None


def test_static_strategy_dispatch():
    assert static_strategy_for(target("x=1", title="Hard-coded secret key")) is prove_hardcoded_secret
    assert static_strategy_for(target("x=1", title="Endpoint has no authentication")) is prove_missing_auth
    assert static_strategy_for(target("x=1", title="Function is too long")) is None


# ------------------------------------------------------------- verdicts


def test_verdict_is_read_from_the_scripts_own_output():
    out = "setting up\nPROOF_VERDICT: VULNERABLE\nPROOF_EVIDENCE: canary.txt was created\n"
    verdict, evidence = interpret(out, 0)
    assert verdict == VULNERABLE
    assert evidence == "canary.txt was created"


def test_not_vulnerable_verdict_is_honoured():
    verdict, _ = interpret("PROOF_VERDICT: NOT_VULNERABLE\n", 0)
    assert verdict == NOT_VULNERABLE


def test_missing_marker_yields_no_verdict():
    """A crashed script is not evidence in either direction."""
    verdict, _ = interpret("Traceback (most recent call last): ImportError\n", 1)
    assert verdict is None


def test_prose_claiming_vulnerability_is_not_a_verdict():
    """Only the marker counts — the model cannot talk its way to 'proven'."""
    verdict, _ = interpret("I am confident this is definitely VULNERABLE code.\n", 0)
    assert verdict is None


# ------------------------------------------------- evidence must match the finding

_FILLER = "\n".join(f"def helper_{i}():\n    return {i}\n" for i in range(10))

# The key sits at line 4; make_token is far below it.
AUTH_LIKE = f'''
import hashlib

SECRET_KEY = "{"sk_" "live_4eC39HqLyjWDarjtT1zdp7dc"}"

{_FILLER}

def make_token(user_id):
    return hashlib.md5(str(user_id).encode()).hexdigest()
'''
_MD5_LINE = next(
    i for i, ln in enumerate(AUTH_LIKE.splitlines(), 1) if "md5" in ln
)


def test_secret_near_the_reported_line_is_proven():
    result = prove_hardcoded_secret(target(AUTH_LIKE, line_start=4))
    assert result.status is ProofStatus.PROVEN


def test_a_distant_secret_is_not_evidence_for_this_finding():
    """The failure mode this guards: any finding mentioning 'secret' on a file
    that happens to contain a key elsewhere would otherwise be marked proven
    with unrelated evidence."""
    assert _MD5_LINE - 4 > 12, "fixture must place the key outside the window"
    result = prove_hardcoded_secret(
        target(AUTH_LIKE, line_start=_MD5_LINE, title="Token generation uses MD5")
    )
    assert result.status is ProofStatus.INCONCLUSIVE
    assert "not confirmed" in result.summary


def test_a_finding_without_a_line_still_matches_the_file():
    result = prove_hardcoded_secret(target(AUTH_LIKE, line_start=None))
    assert result.status is ProofStatus.PROVEN


def test_auth_cue_wins_over_secret_cue():
    """'Endpoint has no authentication token check' cues both strategies;
    route reachability is the more specific answer."""
    chosen = static_strategy_for(
        target("x=1", title="Endpoint has no authentication token check")
    )
    assert chosen is prove_missing_auth


def test_unicode_dashes_in_model_output_still_dispatch():
    for dash in ("-", "‐", "‑", "–"):  # noqa: RUF001 — the dashes are the test
        chosen = static_strategy_for(target("x=1", title=f"Hard{dash}coded secret key"))
        assert chosen is prove_hardcoded_secret, f"failed for U+{ord(dash):04X}"


# ------------------------------------------------------- weak password hashes

MD5_AUTH = '''
import hashlib


def hash_password(password):
    return hashlib.md5(password.encode()).hexdigest()
'''

BCRYPT_AUTH = '''
import bcrypt


def hash_password(password):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt())
'''

CHECKSUM_ONLY = '''
import hashlib


def file_checksum(contents):
    return hashlib.sha256(contents).hexdigest()
'''


def test_md5_password_hashing_is_proven():
    from app.proof.strategies import prove_weak_password_hash

    result = prove_weak_password_hash(target(MD5_AUTH, path="app/auth.py"))
    assert result.status is ProofStatus.PROVEN
    assert "MD5" in result.evidence
    assert "bcrypt" in result.evidence, "the fix belongs in the evidence"


def test_bcrypt_is_refuted():
    from app.proof.strategies import prove_weak_password_hash

    assert prove_weak_password_hash(
        target(BCRYPT_AUTH, path="app/auth.py")
    ).status is ProofStatus.REFUTED


def test_a_fast_digest_on_non_password_data_is_not_flagged():
    """sha256 over file contents is correct usage, not a vulnerability."""
    from app.proof.strategies import prove_weak_password_hash

    result = prove_weak_password_hash(target(CHECKSUM_ONLY, path="app/files.py"))
    assert result.status is not ProofStatus.PROVEN


def test_hashlib_new_form_is_caught():
    from app.proof.strategies import prove_weak_password_hash

    source = "import hashlib\n\n\ndef f(password):\n    return hashlib.new('md5', password).hexdigest()\n"
    assert prove_weak_password_hash(
        target(source, path="app/auth.py")
    ).status is ProofStatus.PROVEN


def test_hash_cue_beats_secret_cue():
    """'Token generation uses MD5 and includes the secret key' is a hashing
    finding, not a hardcoded-credential one."""
    from app.proof.strategies import prove_weak_password_hash

    chosen = static_strategy_for(
        target("x=1", title="Token generation uses MD5 and includes secret key")
    )
    assert chosen is prove_weak_password_hash


def test_weak_hash_evidence_cites_the_nearest_call():
    """A file can hash in several places; the evidence must point at the one
    the finding is about."""
    from app.proof.strategies import prove_weak_password_hash

    source = '''
import hashlib


def hash_password(password):
    return hashlib.md5(password.encode()).hexdigest()


def make_token(user_id, password):
    return hashlib.md5(password.encode()).hexdigest()
'''
    near_token = next(
        i for i, ln in enumerate(source.splitlines(), 1) if "make_token" in ln
    )
    result = prove_weak_password_hash(
        target(source, path="app/auth.py", line_start=near_token + 1,
               title="Insecure token generation using MD5")
    )
    assert result.status is ProofStatus.PROVEN
    assert f":{near_token + 1}" in result.evidence, result.evidence
