from app.doc_alignment import SourceFile, check_alignment


def check(doc, status=200):
    sources = [
        SourceFile("app/main.py", 'from fastapi import FastAPI\nfrom app.routers import jobs\napp = FastAPI()\napp.include_router(jobs.router, prefix="/api")\n'),
        SourceFile("app/routers/jobs.py", f'from fastapi import APIRouter\nrouter = APIRouter(prefix="/jobs")\n@router.post("", status_code={status})\ndef create():\n    return {{}}\n'),
    ]
    return check_alignment([SourceFile("docs/api.md", doc)], sources)[0]


def test_status_change_and_corrected_documentation():
    assert check("POST /api/jobs returns 200")["status"] == "aligned"
    mismatch = check("POST /api/jobs returns 200", 202)
    assert mismatch["status"] == "outdated"
    assert mismatch["doc"] == {"file": "docs/api.md", "start_line": 1, "end_line": 1}
    assert mismatch["code"] == {"file": "app/routers/jobs.py", "start_line": 3, "end_line": 3}
    assert check("POST /api/jobs returns 202", 202)["status"] == "aligned"


def test_ambiguous_status_and_unknown_route():
    assert check("POST /api/jobs returns 200", "compute_status()")["status"] == "unverified"
    assert check("POST /api/unknown returns 200")["status"] == "unverified"
    assert check("POST /api/jobs returns 200 or HTTP 202")["status"] == "unverified"


def test_route_reference_without_status_is_counted_as_unverified():
    result = check("POST /api/jobs is available")
    assert result["status"] == "unverified"
    assert result["message"] == "Cannot verify POST /api/jobs: no supported HTTP status claim was found."
    assert result["code"] == {"file": "app/routers/jobs.py", "start_line": 3, "end_line": 3}


def test_duplicate_route_is_unverified():
    code = SourceFile("app/main.py", 'from fastapi import FastAPI\napp = FastAPI()\n@app.get("/x", status_code=200)\ndef a(): pass\n@app.get("/x", status_code=202)\ndef b(): pass\n')
    assert check_alignment([SourceFile("api.md", "GET /x returns 200")], [code])[0]["status"] == "unverified"


def test_response_override_is_unverified():
    code = SourceFile("app/main.py", 'from fastapi import FastAPI\napp = FastAPI()\n@app.get("/x", status_code=200)\ndef a():\n    if condition:\n        return JSONResponse({}, status_code=202)\n    return {}\n')
    assert check_alignment([SourceFile("api.md", "GET /x returns 200")], [code])[0]["status"] == "unverified"


def test_unconditional_literal_response_status():
    code = SourceFile("app/main.py", 'from fastapi import FastAPI\nfrom fastapi.responses import JSONResponse\napp = FastAPI()\n@app.get("/x")\ndef a():\n    return JSONResponse({}, status_code=202)\n')
    result = check_alignment([SourceFile("api.md", "GET /x returns 200")], [code])[0]
    assert result["status"] == "outdated"
    assert result["code"]["start_line"] == 6


def test_table_status_constant_and_qualified_claims():
    code = SourceFile("app/main.py", 'from fastapi import FastAPI, status\napp = FastAPI()\n@app.get("/x", status_code=status.HTTP_202_ACCEPTED)\ndef a(): return {}\n')
    def result(line):
        return check_alignment([SourceFile("api.md", line)], [code])[0]["status"]
    assert result("| `GET /x` | Returns status 202 |") == "aligned"
    assert result("| `GET /x` | Returns HTTP 404 on error |") == "unverified"
    assert result("GET /x does not return HTTP 200") == "unverified"
    assert result("GET /x returns HTTP 200 when unavailable") == "unverified"


def test_response_classes_and_fake_response_are_unverified():
    docs = [SourceFile("api.md", "GET /x returns 200")]
    for source in (
        'from fastapi import FastAPI\napp = FastAPI(default_response_class=Custom)\n@app.get("/x")\ndef a(): return {}\n',
        'from fastapi import FastAPI\napp = FastAPI()\n@app.get("/x", response_class=Custom)\ndef a(): return {}\n',
        'from fastapi import FastAPI\nclass JSONResponse: pass\napp = FastAPI()\n@app.get("/x")\ndef a(): return JSONResponse({}, status_code=202)\n',
    ):
        assert check_alignment(docs, [SourceFile("app/main.py", source)])[0]["status"] == "unverified"


def test_custom_response_class_does_not_disable_unrelated_routes():
    code = SourceFile("main.py", """
from fastapi import FastAPI
app = FastAPI()
@app.get("/custom", response_class=Custom)
def custom(): return {}
@app.get("/normal", status_code=202)
def normal(): return {}
""")
    docs = SourceFile("api.md", "GET /custom returns 200\nGET /normal returns 200")
    assert [r["status"] for r in check_alignment([docs], [code])] == ["unverified", "outdated"]


def test_extract_claim_context_and_source():
    from app.doc_alignment import extract_claims

    claims = extract_claims([SourceFile("api.md", "# API\n## Jobs\nPOST /api/jobs returns 200 when ready")])
    assert len(claims) == 1
    claim = claims[0]
    assert (claim.method, claim.path, claim.expected_status) == ("POST", "/api/jobs", 200)
    assert claim.citation == {"file": "api.md", "start_line": 3, "end_line": 3}
    assert claim.headings == ("API", "Jobs")
    assert claim.role == "guidance"
    assert claim.condition and claim.reason == "conditional or qualified claim"
    assert check("# API\n## Jobs\nPOST /api/jobs returns 200 when ready", 202)["status"] == "unverified"


def test_table_header_supplies_status_and_separate_method_path():
    doc = "| Method | Path | Status code |\n| --- | --- | --- |\n| POST | /api/jobs | `200` |"
    result = check(doc, 202)
    assert result["status"] == "outdated"
    assert result["doc"]["start_line"] == 3
    assert check(doc.replace("`200`", "200 or 202"), 202)["status"] == "unverified"


def test_fenced_and_historical_examples_are_unverified():
    from app.doc_alignment import extract_claims

    doc = "# API\n```http\nPOST /api/jobs returns 200\n```\n## Historical plan\nPOST /api/jobs returns 200"
    claims = extract_claims([SourceFile("api.md", doc)])
    assert [claim.role for claim in claims] == ["example", "historical"]
    assert [claim.reason for claim in claims] == ["example context", "historical context"]
    code = [SourceFile("main.py", 'from fastapi import FastAPI\napp = FastAPI()\n@app.post("/api/jobs", status_code=202)\ndef create(): return {}\n')]
    assert [r["status"] for r in check_alignment([SourceFile("api.md", doc)], code)] == ["unverified", "unverified"]


def test_alignment_exposes_context_and_unverified_reason():
    result = check("# API\n## Examples\nPOST /api/jobs returns 200", 202)
    assert result["context"] == {
        "headings": ["API", "Examples"],
        "role": "example",
        "condition": None,
        "reason": "example context",
    }
    assert check("POST /api/jobs returns 200")["context"]["reason"] is None
    assert check("POST /api/unknown returns 200")["context"]["reason"] == "route or status is ambiguous"


def test_environment_default_mismatch_and_correction():
    code = [SourceFile("app/settings.py", 'import os\nVALUE = os.getenv("APP_PORT", "8080")\n')]
    def result(doc):
        return check_alignment([SourceFile("docs/config.md", doc)], code)[0]
    mismatch = result("# Configuration\n`APP_PORT` defaults to `8000`")
    assert mismatch["status"] == "outdated"
    assert mismatch["doc"] == {"file": "docs/config.md", "start_line": 2, "end_line": 2}
    assert mismatch["code"] == {"file": "app/settings.py", "start_line": 2, "end_line": 2}
    assert mismatch["context"]["headings"] == ["Configuration"]
    assert result("`APP_PORT` defaults to `8080`")["status"] == "aligned"


def test_environment_defaults_are_conservative():
    doc = [SourceFile("config.md", "`APP_PORT` default is `8080`")]
    for text in (
        'import os\nvalue = os.getenv("APP_PORT", compute())',
        'import os\na = os.getenv("APP_PORT", "8080")\nb = os.environ.get("APP_PORT", "8080")',
        'import os\nvalue = os.getenv("OTHER_PORT", "8080")',
        'import os\nos = fake\nvalue = os.getenv("APP_PORT", "8080")',
        'import os\nif enabled:\n    value = os.getenv("APP_PORT", "8080")',
        'import os\nvalue = os.getenv("APP_PORT", "8080")\nother = os.getenv(name, "x")',
    ):
        assert check_alignment(doc, [SourceFile("settings.py", text)])[0]["status"] == "unverified"
    code = [SourceFile("settings.py", 'import os\nvalue = os.environ.get("APP_PORT", "8080")')]
    for text in ("`APP_PORT` defaults to `8080` or `9000`",
                 "# Examples\n`APP_PORT` defaults to `8080`",
                 "`APP_PORT` defaults to `8080` when enabled"):
        assert check_alignment([SourceFile("config.md", text)], code)[0]["status"] == "unverified"
    assert check_alignment(doc, code)[0]["status"] == "aligned"
