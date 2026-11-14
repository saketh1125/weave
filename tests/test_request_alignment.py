import pytest

from app.doc_alignment import SourceFile, check_alignment


def _check(claim, field="count: int = 1"):
    source = f"""from fastapi import FastAPI
from pydantic import BaseModel
app = FastAPI()
class Payload(BaseModel):
    {field}
@app.post('/items')
def create(body: Payload): return {{}}
"""
    return [item for item in check_alignment([SourceFile("api.md", claim)], [SourceFile("main.py", source)])
            if item["claim_type"].startswith("request_body_")]


def test_json_boolean_is_not_a_numeric_default():
    result = _check("POST /items request body field `count` defaults to `true`")[0]
    assert result["status"] == "outdated"
    assert result["claim"]["expected"] is True
    assert result["code"] == {"file": "main.py", "start_line": 5, "end_line": 5}


def test_presence_is_only_a_field_declaration_check():
    result = _check("POST /items includes request body field `name`")[0]
    assert result["status"] == "outdated"
    assert result["code"] == {"file": "main.py", "start_line": 4, "end_line": 5}
    result = _check("POST /items includes request body field `count`")[0]
    assert result["status"] == "aligned"
    assert result["claim_type"] == "request_body_presence"


@pytest.mark.parametrize("prefix,suffix", [("# Examples\n", ""), ("# Historical\n", ""), ("", " when enabled")])
def test_qualified_claims_do_not_receive_code_verification(prefix, suffix):
    result = _check(prefix + "POST /items request body field `count` defaults to `1`" + suffix)[0]
    assert result["status"] == "unverified"
    assert result["code"] is None


def test_body_claim_without_status_does_not_double_count_endpoint_reference():
    results = _check("POST /items request body field `count` defaults to `1`")
    assert len(results) == 1


def test_explicit_status_and_body_claim_remain_separate():
    source = SourceFile("main.py", "from fastapi import FastAPI\nfrom pydantic import BaseModel\napp=FastAPI()\nclass Payload(BaseModel):\n    name: str\n@app.post('/items')\ndef create(body: Payload): return {}\n")
    result = check_alignment([SourceFile("api.md", "POST /items returns 200 and includes request body field `name`")], [source])
    assert {item["claim_type"] for item in result} == {"http_status", "request_body_presence"}
    assert all(item["status"] == "aligned" for item in result)
