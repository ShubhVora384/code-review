"""Deterministic code metrics.

Cheap, exact facts computed locally and handed to the model as context. Every
metric here is one the LLM would otherwise have to eyeball and get wrong.
"""

from __future__ import annotations

import ast
import re
from typing import Any

_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private_key_block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    (
        "hardcoded_credential",
        # \w* on both sides so SECRET_KEY, DB_PASSWORD and MY_API_KEY_ID are
        # caught -- a trailing \b would stop at the underscore and miss them.
        re.compile(
            r"""(?i)\b\w*(?:password|passwd|secret|api[_-]?key|token|access[_-]?key|credential)\w*\s*[:=]\s*["'][^"'\s]{8,}["']"""
        ),
    ),
]

# Values that look like secrets but are obviously not.
_SECRET_FALSE_POSITIVES = re.compile(
    r"(?i)(example|changeme|placeholder|your[_-]?|xxx+|\*{4,}|dummy|sample|test|fake|redacted|<[^>]+>|\$\{)"
)


def scan_secrets(source: str) -> list[dict[str, Any]]:
    """Regex secret sweep. Reported as evidence, not as a verdict."""
    hits: list[dict[str, Any]] = []
    lines = source.splitlines()
    for kind, pattern in _SECRET_PATTERNS:
        for match in pattern.finditer(source):
            snippet = match.group(0)
            if _SECRET_FALSE_POSITIVES.search(snippet):
                continue
            line_no = source[: match.start()].count("\n") + 1
            raw_line = lines[line_no - 1].strip() if line_no <= len(lines) else ""
            hits.append({
                "kind": kind,
                "line": line_no,
                "evidence": (raw_line[:120] or snippet[:120]),
            })
            if len(hits) >= 12:
                return hits
    return hits


def python_metrics(source: str) -> dict[str, Any]:
    """AST-derived facts for Python files."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return {"parse_error": f"line {exc.lineno}: {exc.msg}"}

    functions: list[dict[str, Any]] = []
    classes: list[dict[str, Any]] = []
    imports: set[str] = set()
    bare_excepts: list[int] = []
    mutable_defaults: list[dict[str, Any]] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", node.lineno) or node.lineno
            args = node.args
            n_args = (
                len(args.args) + len(args.posonlyargs) + len(args.kwonlyargs)
                + (1 if args.vararg else 0) + (1 if args.kwarg else 0)
            )
            functions.append({
                "name": node.name,
                "line": node.lineno,
                "length": end - node.lineno + 1,
                "args": n_args,
                "complexity": _cyclomatic(node),
                "is_async": isinstance(node, ast.AsyncFunctionDef),
                "has_docstring": ast.get_docstring(node) is not None,
            })
            for default in args.defaults + [d for d in args.kw_defaults if d]:
                if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                    mutable_defaults.append({"function": node.name, "line": node.lineno})

        elif isinstance(node, ast.ClassDef):
            classes.append({
                "name": node.name,
                "line": node.lineno,
                "methods": sum(
                    1 for n in node.body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                ),
                "has_docstring": ast.get_docstring(node) is not None,
            })

        elif isinstance(node, ast.Import):
            imports.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                imports.add(node.module.split(".")[0])

        elif isinstance(node, ast.ExceptHandler) and node.type is None:
            bare_excepts.append(node.lineno)

    complexities = [f["complexity"] for f in functions] or [0]
    return {
        "functions": len(functions),
        "classes": len(classes),
        "max_complexity": max(complexities),
        "avg_complexity": round(sum(complexities) / len(complexities), 1),
        "complex_functions": sorted(
            [f for f in functions if f["complexity"] >= 11],
            key=lambda f: -f["complexity"],
        )[:8],
        "long_functions": sorted(
            [f for f in functions if f["length"] > 60], key=lambda f: -f["length"]
        )[:8],
        "bare_excepts": bare_excepts[:10],
        "mutable_default_args": mutable_defaults[:8],
        "imports": sorted(imports)[:40],
        "undocumented_public": [
            f["name"] for f in functions
            if not f["has_docstring"] and not f["name"].startswith("_")
        ][:10],
    }


def _cyclomatic(node: ast.AST) -> int:
    """McCabe complexity: one plus every branch point."""
    score = 1
    for child in ast.walk(node):
        if isinstance(child, (ast.If, ast.While, ast.For, ast.AsyncFor,
                              ast.ExceptHandler, ast.With, ast.AsyncWith, ast.Assert)):
            score += 1
        elif isinstance(child, ast.BoolOp):
            score += len(child.values) - 1
        elif isinstance(child, ast.IfExp):
            score += 1
        elif isinstance(child, ast.comprehension):
            score += 1 + len(child.ifs)
        elif isinstance(child, ast.Match):
            score += len(child.cases)
    return score


_BRACE_FN = re.compile(
    r"(?:^|\s)(?:function\s+(\w+)|(?:async\s+)?(\w+)\s*\([^)]*\)\s*\{|"
    r"(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>)",
    re.MULTILINE,
)
_TODO = re.compile(r"(?i)\b(TODO|FIXME|HACK|XXX|BUG)\b[:\s]")


def generic_metrics(source: str, language: str) -> dict[str, Any]:
    """Language-agnostic signals for everything Python's AST can't parse."""
    lines = source.splitlines()
    non_empty = [ln for ln in lines if ln.strip()]

    comment_markers = ("#", "//", "/*", "*", "--", "<!--")
    comments = sum(1 for ln in non_empty if ln.strip().startswith(comment_markers))
    longest = max((len(ln) for ln in lines), default=0)

    todos = [
        {"line": i + 1, "text": ln.strip()[:100]}
        for i, ln in enumerate(lines)
        if _TODO.search(ln)
    ][:10]

    deepest = 0
    for ln in lines:
        stripped = ln.lstrip()
        if not stripped:
            continue
        indent = len(ln) - len(stripped)
        deepest = max(deepest, indent // 4 if indent % 4 == 0 else indent // 2)

    return {
        "language": language,
        "total_lines": len(lines),
        "code_lines": len(non_empty) - comments,
        "comment_lines": comments,
        "comment_ratio": round(comments / max(len(non_empty), 1), 3),
        "longest_line": longest,
        "max_indent_depth": deepest,
        "todo_markers": todos,
        "approx_functions": len(_BRACE_FN.findall(source)) or None,
    }


def analyze(source: str, language: str) -> dict[str, Any]:
    """Full metric bundle for one file."""
    metrics = generic_metrics(source, language)
    if language == "Python":
        metrics |= python_metrics(source)
    secrets = scan_secrets(source)
    if secrets:
        metrics["possible_secrets"] = secrets
    return metrics


def number_lines(source: str, *, start: int = 1) -> str:
    """`NNN| code` gutter so the model can cite real line numbers."""
    out = []
    for i, line in enumerate(source.splitlines(), start=start):
        out.append(f"{i:>4}| {line}")
    return "\n".join(out)
