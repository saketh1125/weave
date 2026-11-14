import hashlib
import pytest
from types import SimpleNamespace
from app import login_limits


@pytest.mark.asyncio
async def test_limiter_uses_hashed_peer_and_commits(monkeypatch):
    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def execute(self, statement, params=None):
            self.calls.append((str(statement), params))
            return SimpleNamespace(one=lambda: SimpleNamespace(attempts=11, retry_after=23))
        async def commit(self): self.committed = True
    session = Session()
    session.calls, session.committed = [], False
    monkeypatch.setattr(login_limits, "SessionLocal", lambda: session)
    monkeypatch.setattr(login_limits.secrets, "randbelow", lambda _: 0)
    assert await login_limits.check_login_limit("192.0.2.1") == (False, 23)
    assert session.calls[0][1] == {"key": hashlib.sha256(b"192.0.2.1").hexdigest()}
    assert "192.0.2.1" not in str(session.calls)
    assert "ON CONFLICT" in session.calls[0][0]
    assert "DELETE FROM login_buckets" in session.calls[1][0]
    assert session.committed


@pytest.mark.asyncio
async def test_limiter_propagates_database_failure(monkeypatch):
    class Session:
        async def __aenter__(self): raise RuntimeError("database unavailable")
        async def __aexit__(self, *args): pass
    monkeypatch.setattr(login_limits, "SessionLocal", lambda: Session())
    with pytest.raises(RuntimeError):
        await login_limits.check_login_limit("test")
