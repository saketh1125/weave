import json
import os
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import uuid

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("SENTRA_TEST_DATABASE_URL"), reason="No isolated test database"),
]


def _patch_doc_chat(monkeypatch, chat):
    async def async_chat(**kwargs):
        return await asyncio.to_thread(chat, **kwargs)
    monkeypatch.setattr("app.routers.docs.chat", async_chat)
    monkeypatch.setattr("app.routers.docs.configured", lambda workflow: True)


def test_generation_streams_progress_and_rejects_concurrent_requests(monkeypatch):
    import psycopg
    from fastapi.testclient import TestClient
    from app.config import settings
    from app.main import app

    entered, release = Event(), Event()
    calls = []
    def chat(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5), "Test did not release the provider"
        return json.dumps({
            "summary": "Returns an object", "key_behaviors": ["Returns an object"],
            "complexity_note": "Constant work",
        }), {}, 0

    _patch_doc_chat(monkeypatch, chat)
    monkeypatch.setattr(settings, "sentra_access_token", "integration-docs-token-0123456789abcdef")
    repo, file = uuid.uuid4(), uuid.uuid4()
    url = os.environ["SENTRA_TEST_DATABASE_URL"].replace("+psycopg", "")
    with TestClient(app) as client:
        with psycopg.connect(url) as db:
            db.execute("INSERT INTO repositories(id,name,remote_url,status) VALUES (%s,'docs-test','https://example.invalid/repo','ready')", (repo,))
            db.execute("INSERT INTO files(id,repo_id,path,language) VALUES (%s,%s,'app.py','python')", (file, repo))
            for index in range(2):
                db.execute("""
                    INSERT INTO chunks(id,file_id,repo_id,kind,name,qualified_name,start_line,end_line,
                    byte_start,byte_end,content,content_hash)
                    VALUES (%s,%s,%s,'function',%s,%s,%s,%s,0,20,'def f(): return {}',%s)
                """, (uuid.uuid4(), file, repo, f"f{index}", f"f{index}", index+1, index+1, f"hash{index}"))
        try:
            from app.auth import issue_session_token
            client.headers["Authorization"] = "Bearer " + issue_session_token(settings.sentra_access_token)
            path = f"/api/repos/{repo}/docs"
            with ThreadPoolExecutor(max_workers=1) as executor:
                first = executor.submit(client.post, path, json={"limit": 1})
                try:
                    assert entered.wait(5), "Provider was not called"
                    assert client.post(path, json={"limit": 1}).status_code == 409
                finally:
                    release.set()
                initial = first.result(timeout=5)
            assert initial.status_code == 200, initial.text
            following = client.post(path, json={"limit": 1})
            assert following.status_code == 200, following.text
            assert following.json()["docs"][0]["chunk_id"] != initial.json()["docs"][0]["chunk_id"]
            assert len(calls) == 2
        finally:
            release.set()
            with psycopg.connect(url) as db:
                db.execute("DELETE FROM repositories WHERE id=%s", (repo,))


def test_long_unit_persists_only_after_complete_generation(monkeypatch):
    import psycopg
    from fastapi.testclient import TestClient
    from app.config import settings
    from app.main import app
    from app.auth import issue_session_token

    calls = []
    fail = [True]
    def chat(**kwargs):
        calls.append(kwargs)
        if fail[0] and len(calls) == 2:
            raise RuntimeError("provider unavailable")
        return json.dumps({"summary": "Returns data", "key_behaviors": ["Returns data"],
                           "complexity_note": "Unknown"}), {}, 0

    _patch_doc_chat(monkeypatch, chat)
    monkeypatch.setattr(settings, "sentra_access_token", "integration-docs-token-0123456789abcdef")
    repo, file, unit = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    url = os.environ["SENTRA_TEST_DATABASE_URL"].replace("+psycopg", "")
    with TestClient(app) as client:
        with psycopg.connect(url) as db:
            db.execute("INSERT INTO repositories(id,name,remote_url,status) VALUES (%s,'docs-long','https://example.invalid/repo','ready')", (repo,))
            db.execute("INSERT INTO files(id,repo_id,path,language) VALUES (%s,%s,'long.py','python')", (file, repo))
            db.execute("""INSERT INTO chunks(id,file_id,repo_id,kind,name,qualified_name,start_line,end_line,
                       byte_start,byte_end,content,content_hash)
                       VALUES (%s,%s,%s,'function','f','f',1,2,0,3001,%s,'long-hash')""",
                       (unit, file, repo, "x" * 3001))
        try:
            client.headers["Authorization"] = "Bearer " + issue_session_token(settings.sentra_access_token)
            path = f"/api/repos/{repo}/docs"
            failed = client.post(path, json={"limit": 1}).json()
            assert failed["docs"][0]["error"] == "Documentation generation failed"
            with psycopg.connect(url) as db:
                assert db.execute("SELECT count(*) FROM documents WHERE chunk_id=%s", (unit,)).fetchone()[0] == 0
            fail[0] = False
            generated = client.post(path, json={"limit": 1}).json()["docs"][0]
            assert generated["context_complete"] and generated["doc"]["coverage"]["sections_processed"] == 2
            count = len(calls)
            cached = client.post(path, json={"limit": 1}).json()["docs"][0]
            assert cached["cached"] and cached["doc"]["coverage"] == generated["doc"]["coverage"]
            assert len(calls) == count
        finally:
            with psycopg.connect(url) as db:
                db.execute("DELETE FROM repositories WHERE id=%s", (repo,))


def test_legacy_malformed_cache_is_regenerated_and_then_reused(monkeypatch):
    import psycopg
    from fastapi.testclient import TestClient
    from app.config import settings
    from app.main import app
    from app.auth import issue_session_token

    calls = []
    def chat(**kwargs):
        calls.append(kwargs)
        return json.dumps({"summary": "Returns an object", "key_behaviors": ["Returns an object"],
                           "complexity_note": "Constant work"}), {}, 0

    _patch_doc_chat(monkeypatch, chat)
    monkeypatch.setattr(settings, "sentra_access_token", "integration-docs-token-0123456789abcdef")
    repo, file, unit = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    url = os.environ["SENTRA_TEST_DATABASE_URL"].replace("+psycopg", "")
    with TestClient(app) as client:
        with psycopg.connect(url) as db:
            db.execute("INSERT INTO repositories(id,name,remote_url,status) VALUES (%s,'docs-legacy','https://example.invalid/repo','ready')", (repo,))
            db.execute("INSERT INTO files(id,repo_id,path,language) VALUES (%s,%s,'legacy.py','python')", (file, repo))
            db.execute("""INSERT INTO chunks(id,file_id,repo_id,kind,name,qualified_name,start_line,end_line,
                       byte_start,byte_end,content,content_hash)
                       VALUES (%s,%s,%s,'function','legacy','legacy',1,1,0,18,'def f(): return {}','legacy-hash')""",
                       (unit, file, repo))
            db.execute("INSERT INTO documents(chunk_id,text,source_hash,generator_version) VALUES (%s,%s,'legacy-hash',NULL)",
                       (unit, json.dumps({"summary": "Old summary", "key_behaviors": [{"unsafe": "dict"}],
                                         "complexity_note": "Unknown"})))
        try:
            client.headers["Authorization"] = "Bearer " + issue_session_token(settings.sentra_access_token)
            path = f"/api/repos/{repo}/docs"
            response = client.post(path, json={"limit": 1})
            assert response.status_code == 200, response.text
            generated = response.json()["docs"][0]
            assert generated["cached"] is False
            assert generated["doc"]["key_behaviors"] == ["Returns an object"]
            assert all(isinstance(value, str) for value in generated["doc"]["key_behaviors"])
            assert isinstance(generated["doc"]["coverage"], dict)
            assert len(calls) == 1
            with psycopg.connect(url) as db:
                stored_text, version = db.execute(
                    "SELECT text,generator_version FROM documents WHERE chunk_id=%s", (unit,)
                ).fetchone()
                stored = json.loads(stored_text)
                assert version is not None
                assert all(isinstance(value, str) for value in stored["key_behaviors"])
                assert isinstance(stored["coverage"], dict)

            reused_response = client.post(path, json={"limit": 1})
            assert reused_response.status_code == 200, reused_response.text
            reused = reused_response.json()["docs"][0]
            assert reused["cached"] is True
            assert reused["doc"]["key_behaviors"] == ["Returns an object"]
            assert isinstance(reused["doc"]["coverage"], dict)
            assert len(calls) == 1
        finally:
            with psycopg.connect(url) as db:
                db.execute("DELETE FROM repositories WHERE id=%s", (repo,))
