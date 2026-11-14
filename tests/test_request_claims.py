from app.request_claims import extract_request_claims


def test_explicit_prose_claims_and_context():
    text = """# API
POST /items request body field `name` is required
POST /items request body field `count` defaults to `10`
POST /items includes request body field `tags`
"""
    claims = extract_request_claims(text)
    assert [(c.method, c.path, c.field, c.attribute, c.expected) for c in claims] == [
        ("POST", "/items", "name", "required", True),
        ("POST", "/items", "count", "default", 10),
        ("POST", "/items", "tags", "presence", True),
    ]
    assert claims[0].citation == {"file": "<markdown>", "start_line": 2, "end_line": 2}
    assert claims[0].headings == ("API",)
    assert all(c.reason is None for c in claims)


def test_route_scoped_table_separate_claims():
    text = """## POST /items
### Request body
| Field | Required | Default |
| --- | --- | --- |
| name | yes | `null` |
| count | no | `10` |
"""
    claims = extract_request_claims(text)
    assert [(c.field, c.attribute, c.expected) for c in claims] == [
        ("name", "required", True), ("name", "default", None),
        ("count", "required", False), ("count", "default", 10),
    ]
    assert claims[0].headings == ("POST /items", "Request body")
    count_claims = [claim for claim in claims if claim.field == "count"]
    assert all(claim.reason is None for claim in count_claims)


def test_table_data_values_do_not_count_as_qualifying_prose():
    text = """## POST /items
### Request body
| Field | Required | Default |
| --- | --- | --- |
| count | no | `8` |
"""
    claims = extract_request_claims(text)
    assert [(claim.attribute, claim.expected, claim.reason) for claim in claims] == [
        ("required", False, None), ("default", 8, None),
    ]


def test_unsafe_scope_and_values_remain_unverified():
    text = """| Field | Required | Default |
| --- | --- | --- |
| name | true | `10` |
## POST /items
POST /items request body field `count` defaults to `compute()`
POST /items request body field `slug` is optional when enabled
## Examples
POST /items request body field `name` is required
"""
    claims = extract_request_claims(text)
    assert len(claims) == 3
    assert claims[0].reason == "unsupported default value"
    assert claims[1].reason == "conditional or qualified claim"
    assert claims[1].expected is False
    assert claims[2].role == "example" and claims[2].reason == "example context"


def test_needs_request_body_table_scope_and_fenced_claim_is_example():
    text = """## POST /items
| Field | Required | Default |
| --- | --- | --- |
| name | true | `10` |
```md
POST /items request body field `name` is required
```
"""
    claims = extract_request_claims(text)
    assert len(claims) == 1
    assert claims[0].role == "example"
    assert claims[0].reason == "example context"


def test_ambiguous_prose_and_nonfinite_json_abstain():
    claims = extract_request_claims("""POST /items request body field `name` is required or optional
POST /items request body field `count` defaults to `10` or `20`
POST /items request body field `ratio` defaults to `NaN`
POST /items and POST /other request body field `id` is required
""")
    assert len(claims) == 5
    assert claims[0].reason == "ambiguous required value"
    assert claims[1].reason == "ambiguous default value"
    assert claims[2].reason == "unsupported default value"
    assert all(c.reason == "ambiguous route or field claim" for c in claims[3:])


def test_heading_context_and_qualification_are_preserved():
    claim = extract_request_claims("""# API
## Items
### Request body
#### When authorized
POST /items request body field `name` is required
""")[0]
    assert claim.headings == ("API", "Items", "Request body", "When authorized")
    assert claim.condition == "API Items Request body When authorized"
    assert claim.reason == "conditional or qualified claim"


def test_historical_context_and_unsupported_required_value():
    text = """## Historical API
POST /items request body field `name` is required
## POST /items
### Request body
| Field | Required | Default |
| --- | --- | --- |
| name | maybe | `1 + 2` |
"""
    claims = extract_request_claims(text)
    assert claims[0].reason == "historical context"
    assert claims[1].reason == "unsupported required value"
    assert claims[2].reason == "unsupported default value"


def test_accepts_duck_typed_source_without_importing_doc_alignment():
    class Source:
        path = "guide.md"
        content = "PUT /items includes request body field `id`"

    claim = extract_request_claims([Source()])[0]
    assert claim.citation["file"] == "guide.md"
    assert (claim.method, claim.path, claim.field) == ("PUT", "/items", "id")
