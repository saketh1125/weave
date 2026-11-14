"""Short-lived, signed bearer sessions for the single-user deployment."""

import base64
import binascii
import hashlib
import hmac
import json
import time

from app.config import settings

_ISSUER = "sentra"
_AUDIENCE = "sentra-api"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def issue_session_token(password: str, *, now: int | None = None) -> str | None:
    secret = settings.sentra_access_token
    if len(secret) < 32 or not hmac.compare_digest(password.encode(), secret.encode()):
        return None
    issued = int(time.time()) if now is None else now
    payload = _b64(json.dumps({
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "sub": "single-user",
        "iat": issued,
        "exp": issued + settings.session_ttl_seconds,
    }, separators=(",", ":")).encode())
    signature = _b64(hmac.new(secret.encode(), payload.encode("ascii"), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def valid_session_token(token: str, *, now: int | None = None) -> bool:
    secret = settings.sentra_access_token
    if len(secret) < 32 or not token or len(token) > 2048 or not token.isascii():
        return False
    parts = token.split(".")
    if len(parts) != 2 or not all(parts):
        return False
    payload, signature = parts
    expected = hmac.new(secret.encode(), payload.encode("ascii", errors="ignore"), hashlib.sha256).digest()
    try:
        if not hmac.compare_digest(_unb64(signature), expected):
            return False
        claims = json.loads(_unb64(payload))
        if not isinstance(claims, dict):
            return False
        current = int(time.time()) if now is None else now
        issued, expires = claims.get("iat"), claims.get("exp")
        return (
            claims.get("iss") == _ISSUER
            and claims.get("aud") == _AUDIENCE
            and claims.get("sub") == "single-user"
            and isinstance(issued, int) and not isinstance(issued, bool)
            and isinstance(expires, int) and not isinstance(expires, bool)
            and issued <= current
            and expires > current
            and expires - issued <= settings.session_ttl_seconds
        )
    except (ValueError, TypeError, UnicodeError, binascii.Error, json.JSONDecodeError):
        return False
