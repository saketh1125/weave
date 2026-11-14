import pytest
from fastapi.testclient import TestClient

from app.auth import issue_session_token
from app.config import settings
from app.main import app


@pytest.fixture(autouse=True)
def mock_login_limit(monkeypatch):
    async def allow(_host):
        return True, 60
    monkeypatch.setattr("app.main.check_login_limit", allow)


def _client(token: str = "s" * 48) -> TestClient:
    settings.sentra_access_token = token
    return TestClient(app)


def test_sensitive_route_rejects_unauthenticated_request():
    response = _client().get("/api/repos")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_sensitive_route_rejects_invalid_token():
    response = _client().get("/api/repos", headers={"Authorization": "Bearer " + "x" * 48})
    assert response.status_code == 401


def test_authentication_errors_remain_readable_from_allowed_frontend_origin():
    response = _client().get("/api/repos", headers={"Origin": "http://localhost:5174"})
    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == "http://localhost:5174"


def test_raw_access_key_is_not_accepted_as_a_session():
    secret = "k" * 48
    response = _client(secret).get("/api/repos", headers={"Authorization": f"Bearer {secret}"})
    assert response.status_code == 401


def test_login_issues_session_token_and_session_can_access_protected_route():
    token = "v" * 48
    client = _client(token)
    login = client.post("/api/auth/token", json={"access_token": token})
    assert login.status_code == 200
    assert login.json()["expires_in"] == settings.session_ttl_seconds
    session = login.json()["access_token"]
    response = client.get("/api/openapi.json", headers={"Authorization": f"Bearer {session}"})
    assert response.status_code == 200 and "/api/repos" in response.json()["paths"]


def test_login_rejects_invalid_credentials_and_malformed_body():
    client = _client("s" * 48)
    assert client.post("/api/auth/token", json={"access_token": "wrong"}).status_code == 401
    assert client.post("/api/auth/token", json={"access_token": ["not", "a", "string"]}).status_code == 401


def test_expired_session_is_rejected():
    secret = "e" * 48
    client = _client(secret)
    token = issue_session_token(secret, now=1000)
    response = client.get("/api/repos", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_malformed_authorization_headers_are_rejected():
    client = _client("m" * 48)
    for value in ("Basic abc", "Bearer", "Bearer not.a.valid.token", "Bearer a.b"):
        response = client.get("/api/repos", headers={"Authorization": value})
        assert response.status_code == 401, value


def test_every_sensitive_api_route_requires_a_session():
    client = _client()
    public = {"/api/health", "/api/ready", "/api/auth/token"}
    for path, operations in app.openapi()["paths"].items():
        if path in public:
            continue
        route = path.replace("{repo_id}", "00000000-0000-0000-0000-000000000001").replace("{pr_number}", "1")
        method = "GET" if "get" in operations else "POST"
        response = client.request(method, route)
        assert response.status_code == 401, f"{method} {path}: {response.status_code} {response.text}"


def test_authentication_is_unavailable_when_server_secret_is_missing():
    client = _client("")
    response = client.get("/api/repos")
    assert response.status_code == 503
    assert client.post("/api/auth/token", json={"access_token": "anything"}).status_code == 503


def test_disallowed_origin_gets_no_cors_permission():
    response = _client().options(
        "/api/repos",
        headers={
            "Origin": "https://untrusted.example",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_configured_origin_can_send_authorization_header():
    response = _client().options(
        "/api/repos",
        headers={
            "Origin": "http://localhost:5174",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5174"


def test_cors_configuration_rejects_wildcard_and_malformed_origins():
    previous = settings.sentra_cors_origins
    try:
        for value in ("*", "https://*.example.com", "https://example.com/path", "https://bad.example:port"):
            settings.sentra_cors_origins = value
            try:
                settings.cors_origins
            except ValueError:
                continue
            raise AssertionError(f"expected invalid CORS origin {value!r} to be rejected")
    finally:
        settings.sentra_cors_origins = previous


def test_health_is_public_and_readiness_does_not_leak_database_error(monkeypatch):
    class UnavailableConnection:
        async def __aenter__(self):
            raise RuntimeError("password=private localhost:5432")

        async def __aexit__(self, *args):
            pass

    class UnavailableEngine:
        def connect(self):
            return UnavailableConnection()

    monkeypatch.setattr("app.main.engine", UnavailableEngine())
    client = _client("")
    health = client.get("/api/health")
    assert health.status_code == 200
    ready = client.get("/api/ready")
    assert ready.status_code == 503
    assert "password" not in ready.text.lower()
    assert "localhost" not in ready.text.lower()


def test_login_limit_rejects_and_ignores_forwarded_address(monkeypatch):
    seen = []
    async def deny(host):
        seen.append(host)
        return False, 17
    monkeypatch.setattr("app.main.check_login_limit", deny)
    response = _client().post("/api/auth/token", content="bad json", headers={"X-Forwarded-For": "198.51.100.1"})
    assert response.status_code == 429
    assert response.headers["retry-after"] == "17"
    assert seen == ["testclient"]


def test_login_limit_database_error_fails_closed(monkeypatch):
    async def fail(_host):
        raise RuntimeError("private database details")
    monkeypatch.setattr("app.main.check_login_limit", fail)
    response = _client().post("/api/auth/token", json={"access_token": "x"})
    assert response.status_code == 503
    assert "private" not in response.text
