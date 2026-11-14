"""Compare explicit body-field claims with statically resolved declarations."""
from __future__ import annotations

import json

from app.request_claims import extract_request_claims
from app.request_schema import resolve_request_schemas


def _same_scalar(expected, actual):
    # Python considers True == 1; those are different JSON default values.
    if isinstance(expected, bool) or isinstance(actual, bool):
        return type(expected) is type(actual) and expected == actual
    return expected == actual


def check_request_alignment(markdown, code, *, conflicting_routes=()):
    schemas = resolve_request_schemas(code)
    results = []
    for claim in extract_request_claims(markdown):
        route = (claim.method, claim.path)
        matches = schemas.get(route, [])
        reason = claim.reason
        schema = matches[0] if len(matches) == 1 else None
        field = schema.fields.get(claim.field) if schema else None
        citation = None if claim.reason else field.citation if field else schema.citation if schema else None
        if reason is None:
            if len(matches) != 1 or route in conflicting_routes:
                reason = "request body route is missing or ambiguous"
            elif schema.reason:
                reason = schema.reason
            elif field is not None and claim.attribute == "required" and field.required is None:
                reason = field.reason or "field requiredness is not statically known"
            elif field is not None and claim.attribute == "default" and not field.default_known:
                reason = field.reason or "field default is not a supported literal"
        result = {
            "status": "unverified",
            "claim_type": f"request_body_{claim.attribute}",
            "claim": {"method": claim.method, "path": claim.path, "field": claim.field,
                      "attribute": claim.attribute, "expected": claim.expected},
            "message": f"Cannot verify {claim.method} {claim.path} body field {claim.field!r}: {reason}.",
            "doc": claim.citation,
            "code": citation,
            "context": {"headings": list(claim.headings), "role": claim.role,
                        "condition": claim.condition, "reason": reason},
        }
        if reason is None:
            if field is None:
                result["status"] = "outdated"
                result["message"] = f"{claim.method} {claim.path} documents body field {claim.field!r}; the resolved model does not declare it."
            else:
                actual = True if claim.attribute == "presence" else field.required if claim.attribute == "required" else field.default
                result["status"] = "aligned" if _same_scalar(claim.expected, actual) else "outdated"
                result["message"] = (
                    f"{claim.method} {claim.path} body field {claim.field!r} documents "
                    f"{claim.attribute} {json.dumps(claim.expected, ensure_ascii=False)}; "
                    f"code declares {json.dumps(actual, ensure_ascii=False)}."
                )
        results.append(result)
    return results
