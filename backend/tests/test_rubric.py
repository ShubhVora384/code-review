import pytest

from app.scoring.rubric import (
    aggregate_repo_score,
    compute_file_score,
    finding_penalty,
    grade_for,
)


def test_grade_bands():
    assert grade_for(95) == "A"
    assert grade_for(85) == "B"
    assert grade_for(75) == "C"
    assert grade_for(65) == "D"
    assert grade_for(10) == "F"


def test_penalty_is_zero_without_findings():
    assert finding_penalty([]) == 0.0


def test_penalty_ordered_by_severity():
    crit = finding_penalty([{"severity": "critical", "confidence": 1.0}])
    low = finding_penalty([{"severity": "low", "confidence": 1.0}])
    assert crit > low > 0


def test_penalty_has_diminishing_returns_and_a_ceiling():
    ten = finding_penalty([{"severity": "medium", "confidence": 1.0}] * 10)
    forty = finding_penalty([{"severity": "medium", "confidence": 1.0}] * 40)
    assert forty > ten
    assert forty <= 45.0  # capped so one bad file cannot go infinitely negative


def test_low_confidence_findings_hurt_less():
    sure = finding_penalty([{"severity": "high", "confidence": 1.0}])
    unsure = finding_penalty([{"severity": "high", "confidence": 0.3}])
    assert sure > unsure


def test_file_score_drops_with_findings():
    dims = {"correctness": 90, "security": 90, "maintainability": 90}
    clean = compute_file_score(dims, [])
    dirty = compute_file_score(dims, [{"severity": "critical", "confidence": 0.9}])
    assert clean > dirty
    assert 0 <= dirty <= 100


def test_file_score_defaults_without_dimensions():
    assert 0 <= compute_file_score(None, []) <= 100


def test_missing_tests_caps_the_testing_dimension():
    rows = [{
        "path": "a.py", "loc": 100, "findings": [],
        "dimension_scores": {"testing": 95, "correctness": 90},
    }]
    agg = aggregate_repo_score(rows, [], test_ratio=0.0)
    assert agg["dimensions"]["testing"] <= 30


def test_large_files_weigh_more_than_small_ones():
    rows = [
        {"path": "big.py", "loc": 2000, "findings": [], "dimension_scores": {"correctness": 20}},
        {"path": "tiny.py", "loc": 5, "findings": [], "dimension_scores": {"correctness": 100}},
    ]
    agg = aggregate_repo_score(rows, [], test_ratio=0.5)
    assert agg["dimensions"]["correctness"] < 60


def test_cross_cutting_findings_lower_the_overall_score():
    rows = [{"path": "a.py", "loc": 50, "findings": [], "dimension_scores": {"correctness": 90}}]
    clean = aggregate_repo_score(rows, [], test_ratio=0.5)
    hit = aggregate_repo_score(
        rows, [{"severity": "critical", "confidence": 1.0}], test_ratio=0.5
    )
    assert hit["overall"] < clean["overall"]


@pytest.mark.parametrize("score", [0, 50, 100])
def test_overall_always_in_range(score):
    rows = [{"path": "a.py", "loc": 10, "findings": [], "dimension_scores": {"correctness": score}}]
    assert 0 <= aggregate_repo_score(rows, [], test_ratio=1.0)["overall"] <= 100


# --------------------------------------------------------------- proof weight

GOOD_DIMS = {"correctness": 90, "security": 85, "maintainability": 88, "readability": 90}


def _with_finding(severity: str, proof_status: str, **extra):
    rows = [{
        "path": "a.py", "loc": 100, "dimension_scores": GOOD_DIMS,
        "findings": [{
            "severity": severity, "confidence": 1.0,
            "proof_status": proof_status, **extra,
        }],
    }]
    return aggregate_repo_score(rows, [], test_ratio=0.5)


def test_a_demonstrated_finding_hurts_more_than_a_suspected_one():
    suspected = _with_finding("high", "not_attempted")["overall"]
    proven = _with_finding("high", "proven")["overall"]
    assert proven < suspected


def test_refuted_findings_carry_no_weight():
    from app.scoring.rubric import finding_penalty

    assert finding_penalty([{"severity": "critical", "confidence": 1.0,
                             "proof_status": "refuted"}]) == 0.0


def test_a_proven_critical_caps_the_grade():
    """A report cannot say 'do not launch' next to a passing grade."""
    result = _with_finding("critical", "proven")
    assert result["overall"] <= 45.0
    assert result["grade"] == "F"
    assert result["launch_verdict"]["decision"] == "do_not_launch"


def test_a_proven_high_caps_lower_than_clean_but_above_critical():
    high = _with_finding("high", "proven")["overall"]
    critical = _with_finding("critical", "proven")["overall"]
    clean = aggregate_repo_score(
        [{"path": "a.py", "loc": 100, "dimension_scores": GOOD_DIMS, "findings": []}],
        [], test_ratio=0.5,
    )["overall"]
    assert critical < high < clean


def test_a_proven_medium_does_not_cap_the_grade():
    """Only critical and high are launch-blocking."""
    assert _with_finding("medium", "proven")["overall"] > 70


def test_unproven_criticals_do_not_block_launch():
    verdict = _with_finding("critical", "not_attempted")["launch_verdict"]
    assert verdict["decision"] == "fix_first"
    assert "could not be automatically demonstrated" in verdict["reason"]


def test_launch_reason_is_grammatical_for_one_and_many():
    one = _with_finding("critical", "proven")["launch_verdict"]["reason"]
    assert "1 serious vulnerability was demonstrated" in one

    rows = [{
        "path": "a.py", "loc": 100, "dimension_scores": GOOD_DIMS,
        "findings": [
            {"severity": "critical", "confidence": 1.0, "proof_status": "proven"},
            {"severity": "critical", "confidence": 1.0, "proof_status": "proven"},
        ],
    }]
    many = aggregate_repo_score(rows, [], test_ratio=0.5)["launch_verdict"]["reason"]
    assert "2 serious vulnerabilities were demonstrated" in many


def test_proven_counts_are_reported():
    result = _with_finding("critical", "proven")
    assert result["proven_findings"] == 1
    assert result["proven_critical"] == 1


def test_clean_codebase_is_cleared_to_launch():
    result = aggregate_repo_score(
        [{"path": "a.py", "loc": 50, "dimension_scores": GOOD_DIMS, "findings": []}],
        [], test_ratio=0.6,
    )
    assert result["launch_verdict"]["decision"] == "ready"
