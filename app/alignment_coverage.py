"""Report what was checked separately from whether individual claims matched."""
from collections import Counter


def alignment_coverage(items: list[dict]) -> dict:
    by_type = {}
    reasons = Counter()
    for item in items:
        kind = item.get("claim_type") or ("behavioral_review" if item.get("assessment") else "unspecified")
        counts = by_type.setdefault(kind, {"checked": 0, "aligned": 0, "outdated": 0, "unverified": 0})
        status = item["status"]
        counts[status] += 1
        if status in {"aligned", "outdated"}:
            counts["checked"] += 1
        else:
            reasons[(item.get("context") or {}).get("reason") or "No supported evidence"] += 1
    return {"by_claim_type": dict(sorted(by_type.items())), "unverified_reasons": dict(sorted(reasons.items()))}
