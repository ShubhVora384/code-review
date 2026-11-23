from app.tools.static_analysis import analyze, number_lines, python_metrics, scan_secrets


def test_python_metrics_counts_structure():
    src = '''
def simple():
    return 1


class Thing:
    """Doc."""

    def method(self, a, b):
        if a:
            for _ in range(b):
                if b > 2 and a < 5:
                    return 1
        try:
            pass
        except:
            pass
        return 0
'''
    m = python_metrics(src)
    assert m["functions"] == 2
    assert m["classes"] == 1
    assert m["max_complexity"] > 4
    assert m["bare_excepts"] == [16]


def test_python_metrics_flags_mutable_defaults():
    m = python_metrics("def f(items=[]):\n    return items\n")
    assert m["mutable_default_args"]


def test_python_metrics_reports_syntax_errors():
    m = python_metrics("def broken(:\n")
    assert "parse_error" in m


def test_secret_scanner_finds_real_secrets():
    # Deliberately not AWS's own ...EXAMPLE key -- that is filtered as a placeholder.
    hits = scan_secrets('AWS = "AKIA2QT4RBWZ9KLMN3PQ"\npassword = "hunter2hunter2"\n')
    kinds = {h["kind"] for h in hits}
    assert "aws_access_key" in kinds
    assert all(h["line"] > 0 for h in hits)


def test_secret_scanner_filters_the_aws_documentation_key():
    assert scan_secrets('AWS = "AKIAIOSFODNN7EXAMPLE"') == []


def test_secret_scanner_catches_suffixed_variable_names():
    """SECRET_KEY / DB_PASSWORD must match -- a word boundary after the
    keyword stops at the underscore and silently misses these."""
    for line in (
        'SECRET_KEY = "sk_' 'live_4f8a2b9c1d7e3f6a"',
        'DB_PASSWORD = "hunter2hunter2"',
        'MY_API_KEY_ID = "abcdefgh12345678"',
    ):
        assert scan_secrets(line), f"missed: {line}"


def test_secret_scanner_ignores_env_lookups():
    assert scan_secrets('SECRET_KEY = os.environ["SECRET_KEY"]') == []
    assert scan_secrets("token_ttl = 3600") == []


def test_secret_scanner_skips_obvious_placeholders():
    hits = scan_secrets('password = "your-password-here"\napi_key = "changeme123"\n')
    assert hits == []


def test_number_lines_gutter():
    out = number_lines("a\nb\nc")
    assert out.splitlines()[0].endswith("| a")
    assert "   3| c" in out


def test_analyze_bundles_generic_and_python_metrics():
    m = analyze("# TODO: fix\ndef f():\n    return 1\n", "Python")
    assert m["language"] == "Python"
    assert m["functions"] == 1
    assert m["todo_markers"]
