"""PostgreSQL-backed per-client limit for token requests."""
from __future__ import annotations

import hashlib
import secrets
from sqlalchemy import text
from app.db import SessionLocal

LIMIT = 10
WINDOW_SECONDS = 60

# Read the clock after acquiring the conflicting row lock. A statement can
# begin before another transaction creates or resets the window and then wait
# for that transaction; its statement_timestamp() would precede window_start.
_UPSERT = text('''
INSERT INTO login_buckets (key, window_start, attempts)
VALUES (:key, clock_timestamp(), 1)
ON CONFLICT (key) DO UPDATE SET
  (window_start, attempts) = (
    SELECT CASE WHEN login_buckets.window_start <= observed_clock.value - interval '60 seconds'
                THEN observed_clock.value ELSE login_buckets.window_start END,
           CASE WHEN login_buckets.window_start <= observed_clock.value - interval '60 seconds'
                THEN 1 ELSE login_buckets.attempts + 1 END
    FROM (SELECT clock_timestamp() AS value) AS observed_clock
  )
RETURNING attempts, GREATEST(1, CEIL(EXTRACT(EPOCH FROM
  (window_start + interval '60 seconds' - clock_timestamp()))))::integer AS retry_after
''')
_CLEANUP = text('''
DELETE FROM login_buckets WHERE key IN (
  SELECT key FROM login_buckets WHERE window_start < statement_timestamp() - interval '1 day'
  LIMIT 100
) AND window_start < statement_timestamp() - interval '1 day'
''')


async def check_login_limit(client_host: str) -> tuple[bool, int]:
    """Consume one attempt and return (allowed, retry-after seconds).

    Database errors propagate so callers can fail closed. Only the hashed direct
    peer address is stored. Deployments behind a proxy must configure a trusted
    proxy at the server layer; request headers are never interpreted here.
    """
    key = hashlib.sha256(client_host.encode("utf-8")).hexdigest()
    async with SessionLocal() as session:
        result = await session.execute(_UPSERT, {"key": key})
        row = result.one()
        if secrets.randbelow(100) == 0:
            await session.execute(_CLEANUP)
        await session.commit()
    return row.attempts <= LIMIT, row.retry_after
