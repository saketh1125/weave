from types import SimpleNamespace

from app.score import analysis_coverage, compute_score, score_input_fingerprint, snapshot_is_fresh


def finding(module="m2", severity="low", status="open", **extra):
    return {"module": module, "severity": severity, "status": status, **extra}


def test_severity_weights_unknown_and_module_caps():
    result = compute_score(
        [finding("m2", "critical"), finding("m2", "high"), finding("m2", "medium"), finding("m2", "low"), finding("m2", "n/a")]
    )
    assert result["breakdown"]["m2"] == {
        "count": 5,
        "raw": 18,
        "deduction": 18,
        "by_severity": {"critical": 1, "high": 1, "low": 1, "medium": 1, "unknown": 1},
        "cap": 35,
    }
    capped = compute_score([finding("m2", "critical") for _ in range(5)])
    assert capped["breakdown"]["m2"]["raw"] == 50
    assert capped["breakdown"]["m2"]["deduction"] == 35


def test_score_is_deterministic_for_reordered_inputs_and_unlimited_modules():
    inputs = [finding("z-other", "low"), finding("m4", "high"), finding("a-other", "medium")]
    assert compute_score(inputs) == compute_score(list(reversed(inputs)))
    result = compute_score(inputs)
    assert "other" in result["breakdown"]
    assert result["breakdown"]["other"]["deduction"] == 2.5


def test_closed_statuses_do_not_penalize_but_missing_or_unknown_status_remains_active():
    result = compute_score(
        [finding("m4", "critical", "open"), finding("m4", "critical", "resolved"),
         finding("m4", "high", "ignored"), finding("m4", "medium", "dismissed"),
         {"module": "m4", "severity": "low"}, finding("m4", "low", "reopened")]
    )
    assert result["breakdown"]["m4"]["count"] == 3
    assert result["breakdown"]["m4"]["raw"] == 11.0


def test_snapshot_fingerprint_tracks_finding_content_status_and_repository_revision():
    rows = [finding("m2", "high", id="finding-1", location={"path": "a.py"})]
    fingerprint = score_input_fingerprint(rows, "commit-a")
    snapshot = SimpleNamespace(config_snapshot={"input_fingerprint": fingerprint})
    assert snapshot_is_fresh(snapshot, list(reversed(rows)), "commit-a")
    assert not snapshot_is_fresh(snapshot, [finding("m2", "high", id="finding-1", location={"path": "b.py"})], "commit-a")
    assert not snapshot_is_fresh(snapshot, rows, "commit-b")
    assert not snapshot_is_fresh(SimpleNamespace(config_snapshot={}), rows, "commit-a")


def test_coverage_requires_revision_matched_successful_job_results():
    assert analysis_coverage(None, [{"commit_sha": None, "modules": ["m2", "m4"]}]) == {
        "revision": None, "status": "unknown", "completed_modules": [], "missing_modules": ["m2", "m4"]}
    assert analysis_coverage("a", [{"modules": ["m2", "m4"]}, {"commit_sha": "b", "modules": ["m2"]}])["status"] == "unknown"
    partial = analysis_coverage("a", [{"commit_sha": "a", "modules": ["m2", "invalid"]}])
    assert partial == {"revision": "a", "status": "incomplete", "completed_modules": ["m2"], "missing_modules": ["m4"]}
    full = analysis_coverage("a", [{"commit_sha": "a", "modules": ["m2"]}, {"commit_sha": "a", "modules": ["m4"]}])
    assert full["status"] == "complete" and full["missing_modules"] == []
    assert analysis_coverage("a", [{"commit_sha": "a", "modules": "m2"}])["status"] == "unknown"
