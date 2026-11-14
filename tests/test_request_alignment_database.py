"""Exercise indexed-source alignment, persisted coverage and citation provenance."""
import os
import uuid

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not os.environ.get("SENTRA_TEST_DATABASE_URL"), reason="No isolated test database")]


def test_request_field_drift_persists_claim_coverage_and_commit_citations(monkeypatch):
    import psycopg
    from fastapi.testclient import TestClient
    from app.auth import issue_session_token
    from app.config import settings
    from app.main import app

    monkeypatch.setattr(settings, "sentra_access_token", "request-alignment-test-token-0123456789")
    monkeypatch.setattr(settings, "mistral_api_key", "")
    repo = uuid.uuid4()
    commit = "b" * 40
    source = "from fastapi import FastAPI\nfrom pydantic import BaseModel\napp=FastAPI()\nclass Payload(BaseModel):\n    name: str = 'guest'\n    count: int = 3\n@app.post('/items')\ndef create(body: Payload): return {}\n"
    markdown = "POST /items request body field `name` is required\nPOST /items request body field `count` defaults to `3`\nPOST /items request body field `count` defaults to `3` when enabled\n"
    url = os.environ["SENTRA_TEST_DATABASE_URL"].replace("+psycopg", "")
    with TestClient(app) as client:
        with psycopg.connect(url) as db:
            db.execute("INSERT INTO repositories(id,name,remote_url,status,commit_sha) VALUES (%s,'alignment-test','https://github.com/example/fixture','ready',%s)", (repo, commit))
            for path, language, kind, content in [("app.py", "python", "module", source), ("api.md", "markdown", "markdown", markdown)]:
                file = uuid.uuid4()
                db.execute("INSERT INTO files(id,repo_id,path,language) VALUES (%s,%s,%s,%s)", (file, repo, path, language))
                db.execute("""INSERT INTO chunks(id,file_id,repo_id,kind,name,qualified_name,start_line,end_line,
                              byte_start,byte_end,content,content_hash)
                              VALUES (%s,%s,%s,%s,'fixture','fixture',1,%s,0,%s,%s,'fixture-hash')""",
                           (uuid.uuid4(), file, repo, kind, len(content.splitlines()), len(content.encode()), content))
        try:
            client.headers["Authorization"] = "Bearer " + issue_session_token(settings.sentra_access_token)
            path = f"/api/repos/{repo}/drift"
            response = client.post(path, json={"behavioral": False})
            assert response.status_code == 200
            result = response.json()
            assert (result["checked"], result["aligned"], result["drifted"], result["unverified"]) == (2, 1, 1, 1)
            assert result["coverage"]["by_claim_type"]["request_body_required"]["outdated"] == 1
            assert result["coverage"]["by_claim_type"]["request_body_default"]["checked"] == 1
            assert result["coverage"]["unverified_reasons"] == {"conditional or qualified claim": 1}
            for item in result["items"]:
                assert item["doc"]["url"].startswith(f"https://github.com/example/fixture/blob/{commit}/api.md#L")
                if item["code"]:
                    assert f"/blob/{commit}/app.py#L" in item["code"]["url"]
            assert client.get(path).json() == result
        finally:
            with psycopg.connect(url) as db:
                db.execute("DELETE FROM repositories WHERE id=%s", (repo,))
