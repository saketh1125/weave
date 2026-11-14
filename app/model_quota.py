"""Durable, shared quota reservations for configured model connections."""
import asyncio
import json
import hashlib
import math
import time
from sqlalchemy import select, text
from contextlib import asynccontextmanager
from sqlalchemy.exc import SQLAlchemyError
from app import model_connections as model

RPM = 50
TPM = 20_000
INTERVAL = 1.25

@asynccontextmanager
async def _concurrency_slot(connection):
    from app.db import engine
    async with engine.connect() as db:
        locked = None
        acquiring = False
        try:
            for slot in range(connection.concurrency):
                raw = hashlib.sha256((connection.quota_group + "\0" + str(slot)).encode()).digest()[:8]
                key = int.from_bytes(raw, "big", signed=True)
                acquiring = True
                acquired = (await db.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})).scalar_one()
                if acquired:
                    locked = key
                acquiring = False
                if acquired:
                    break
            await db.commit()
            if locked is None:
                raise model.ProviderError('rate_limited', 1, request_sent=False)
            yield
        finally:
            if acquiring:
                await db.invalidate()
            if locked is not None:
                async def cleanup():
                    try:
                        await db.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": locked})
                        await db.commit()
                    except BaseException:
                        await db.invalidate()
                        raise
                task = asyncio.create_task(cleanup())
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    await task
                    raise

async def _request(messages, tools, *, model_name, max_tokens, credential_slot=1, preflight=None, prompt_tokens_upper_bound=None, force_assessment=False, connection=None, workflow="reviewer", temperature=None, response_format=None, timeout_seconds=None):
    from app.db import SessionLocal
    if connection is None:
        from app.model_connections import resolve
        connection = resolve(workflow, credential_slot)
    # Preserve existing seeded legacy rows; arbitrary group names never enter SQL identifiers.
    group = connection.quota_group
    row_id = group if group in ("mistral:1", "mistral:2") else "q:" + hashlib.sha256(group.encode()).hexdigest()[:62]
    rpm, tpm, interval = connection.rpm, connection.tpm, connection.interval
    if type(max_tokens) is not int or max_tokens <= 0:
        raise model.ProviderError('output_budget_exceeded', request_sent=False)
    raw_bound = len(json.dumps(messages, ensure_ascii=False).encode()) + len(json.dumps(tools, ensure_ascii=False).encode())
    if prompt_tokens_upper_bound is not None and (type(prompt_tokens_upper_bound) is not int or not 0 < prompt_tokens_upper_bound <= 1_000_000):
        raise model.ProviderError('context_budget_exceeded', request_sent=False)
    units = (raw_bound if prompt_tokens_upper_bound is None else prompt_tokens_upper_bound) + max_tokens
    if units > min(tpm, connection.context_tokens) or max_tokens > connection.max_output_tokens:
        raise model.ProviderError('context_budget_exceeded', request_sent=False)
    from app.models import DocumentationProviderQuota
    while True:
        now = time.time()
        async with SessionLocal() as session:
            if row_id not in ('mistral:1', 'mistral:2'):
                from sqlalchemy.dialects.postgresql import insert
                await session.execute(insert(DocumentationProviderQuota).values(id=row_id).on_conflict_do_nothing(index_elements=['id']))
            row = (await session.execute(select(DocumentationProviderQuota).where(DocumentationProviderQuota.id == row_id).with_for_update())).scalar_one()
            now = time.time()
            if now - row.window_start >= 60:
                row.window_start = now
                row.reserved_tokens = row.reserved_requests = 0
            wait = row.blocked_until - now
            if row.reserved_requests >= rpm or row.reserved_tokens + units > tpm:
                wait = max(wait, row.window_start + 60 - now)
            if wait > 0:
                await session.commit()
                raise model.ProviderError('rate_limited', max(1, math.ceil(wait)), request_sent=False)
            spacing = row.next_call_at - now
            if spacing <= 0:
                reserved_window = row.window_start
                row.reserved_requests += 1
                row.reserved_tokens += units
                row.next_call_at = now + interval
                await session.commit()
                break
            await session.commit()
        if spacing > connection.timeout_seconds:
            raise model.ProviderError('rate_limited', math.ceil(spacing), request_sent=False)
        await asyncio.sleep(spacing)
    # A failed lease check still consumes the conservative reservation.
    try:
        kwargs = {'model': model_name, 'max_tokens': max_tokens}
        if connection is not None:
            kwargs.update(connection=connection, workflow=workflow)
        if force_assessment:
            kwargs['force_assessment'] = True
        if prompt_tokens_upper_bound is not None: kwargs['prompt_tokens_upper_bound'] = prompt_tokens_upper_bound
        if timeout_seconds is not None: kwargs['timeout_seconds'] = timeout_seconds
        if temperature is not None: kwargs['temperature'] = temperature
        if response_format is not None: kwargs['response_format'] = response_format
        async with _concurrency_slot(connection):
            if preflight is not None:
                await preflight()
            result = await model.complete(messages, tools, **kwargs)
        usage = result[1] if isinstance(result, (tuple, list)) and len(result) == 2 else None
        actual = usage.get('total_tokens') if isinstance(usage, dict) else None
        if actual is None and isinstance(usage, dict):
            prompt, completion = usage.get('prompt_tokens'), usage.get('completion_tokens')
            if type(prompt) is int and type(completion) is int and prompt >= 0 and completion >= 0:
                actual = prompt + completion
        if type(actual) is int and 0 < actual <= units:
            async with SessionLocal() as session:
                row = (await session.execute(select(DocumentationProviderQuota).where(DocumentationProviderQuota.id == row_id).with_for_update())).scalar_one()
                if row.window_start == reserved_window:
                    row.reserved_tokens = max(0, row.reserved_tokens - (units - actual))
                await session.commit()
        return result
    except model.ProviderError as exc:
        if exc.reason == 'rate_limited' and exc.request_sent:
            async with SessionLocal() as session:
                row = (await session.execute(select(DocumentationProviderQuota).where(DocumentationProviderQuota.id == row_id).with_for_update())).scalar_one()
                row.blocked_until = max(row.blocked_until, time.time() + (exc.retry_after_seconds or 60))
                await session.commit()
        raise


async def request(messages, tools, *, model_name, max_tokens, credential_slot=1, preflight=None, prompt_tokens_upper_bound=None, force_assessment=False, connection=None, workflow="reviewer", temperature=None, response_format=None, timeout_seconds=None):
    try:
        connection = connection or model.resolve(workflow, credential_slot)
        if timeout_seconds is not None and (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0 or timeout_seconds > connection.timeout_seconds):
            raise model.ProviderError('invalid_configuration', request_sent=False)
        limit = min(connection.timeout_seconds, timeout_seconds) if timeout_seconds is not None else connection.timeout_seconds
        async with asyncio.timeout(limit):
            return await _request(messages, tools, model_name=model_name, max_tokens=max_tokens, credential_slot=credential_slot, preflight=preflight, prompt_tokens_upper_bound=prompt_tokens_upper_bound, force_assessment=force_assessment, connection=connection, workflow=workflow, temperature=temperature, response_format=response_format, timeout_seconds=timeout_seconds)
    except TimeoutError:
        raise model.ProviderError('timeout') from None
    except SQLAlchemyError:
        raise model.ProviderError('quota_unavailable') from None
