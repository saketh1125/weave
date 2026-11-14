"""Shared persistence lifecycle for normalized scanner findings."""
from __future__ import annotations

import json

from sqlalchemy import select


def _stable_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def deduplicate_normalized(rows: list[dict]) -> list[dict]:
    """Keep one deterministic row per stable key before touching unique DB keys."""
    selected = {}
    for row in rows:
        key = row.get("stable_key")
        if not key:
            raise ValueError("normalized finding requires a stable_key")
        candidate = dict(row)
        current = selected.get(key)
        if current is None or _stable_json(candidate) < _stable_json(current):
            selected[key] = candidate
    return [selected[key] for key in sorted(selected)]


def apply_scan_lifecycle(current: list, normalized: list[dict], make_row, legacy_keys: dict[str, set[str]] | None = None) -> list[dict]:
    """Apply deduplicated scan state to rows; `make_row` constructs new rows."""
    by_key = {row.stable_key: row for row in current if row.stable_key}
    seen = set()
    deduplicated = deduplicate_normalized(normalized)
    # Only move an old identity if this scan maps it to exactly one new key
    # and there is no row already using that key.
    normalized_keys = {values["stable_key"] for values in deduplicated}
    for old_key, new_keys in (legacy_keys or {}).items():
        if len(new_keys) != 1 or old_key not in by_key:
            continue
        new_key = next(iter(new_keys))
        if new_key not in normalized_keys or new_key in by_key:
            continue
        row = by_key.pop(old_key)
        row.stable_key = new_key
        by_key[new_key] = row

    for values in deduplicated:
        key = values["stable_key"]
        seen.add(key)
        row = by_key.get(key)
        if row is None:
            row = make_row(values)
            by_key[key] = row
            continue

        old_status = (row.status or "open").strip().lower()
        row.status = old_status if old_status in {"ignored", "dismissed"} else "open"
        for field, value in values.items():
            if field != "stable_key":
                setattr(row, field, value)

    for row in current:
        if row.stable_key not in seen and (row.status or "open").strip().lower() == "open":
            row.status = "resolved"
    return deduplicated


async def reconcile_scanner_findings(repo_id, module: str, normalized: list[dict], session, *, legacy_keys: dict[str, set[str]] | None = None) -> list[dict]:
    """Upsert scan results, resolve missing open rows, and retain user states."""
    from app.models import Finding

    current = (await session.execute(
        select(Finding).where(Finding.repo_id == repo_id, Finding.module == module)
    )).scalars().all()
    def make_row(values):
        row = Finding(repo_id=repo_id, module=module, status="open", **values)
        session.add(row)
        return row

    deduplicated = apply_scan_lifecycle(current, normalized, make_row, legacy_keys=legacy_keys)
    await session.commit()
    return deduplicated
