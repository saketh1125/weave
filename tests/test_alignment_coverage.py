from app.alignment_coverage import alignment_coverage


def test_coverage_keeps_unverified_claims_out_of_checked_totals():
    items = [
        {"claim_type": "request_body_required", "status": "aligned"},
        {"claim_type": "request_body_required", "status": "outdated"},
        {"claim_type": "request_body_default", "status": "unverified", "context": {"reason": "dynamic default"}},
        {"claim_type": "request_body_default", "status": "unverified", "context": {"reason": "dynamic default"}},
        {"status": "unverified", "assessment": {"source": "model"}},
    ]
    result = alignment_coverage(items)
    assert result["by_claim_type"]["request_body_required"] == {"checked": 2, "aligned": 1, "outdated": 1, "unverified": 0}
    assert result["by_claim_type"]["request_body_default"] == {"checked": 0, "aligned": 0, "outdated": 0, "unverified": 2}
    assert result["by_claim_type"]["behavioral_review"]["checked"] == 0
    assert result["unverified_reasons"] == {"No supported evidence": 1, "dynamic default": 2}
    assert alignment_coverage([]) == {"by_claim_type": {}, "unverified_reasons": {}}
