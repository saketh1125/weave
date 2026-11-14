"""Expand the synthetic quality fixture; optionally run two bounded live summaries."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "documentation_quality.json"
LIVE_CASE_IDS = ("summary-fact-after-source-boundary", "summary-conditional-branch")
MAX_LIVE_CALLS = 8


class CallBudgetExceeded(RuntimeError):
    pass


def expand_source(source: dict) -> str:
    """Return literal source text, expanding the fixture's small repeat recipe."""
    if "content" in source:
        return source["content"]
    recipe = source["construction"]
    count = recipe["repeat_count"]
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise ValueError(f"invalid repeat_count for {source['path']}")
    if count > 10000:
        raise ValueError(f"repeat_count exceeds fixture bound for {source['path']}")
    return recipe["prefix"] + recipe["repeat_text"] * count + recipe["suffix"]


def load_fixture(path: Path = FIXTURE) -> tuple[dict, str]:
    raw = path.read_bytes()
    fixture = json.loads(raw)
    if fixture.get("version") != 1 or not isinstance(fixture.get("cases"), list):
        raise ValueError("unsupported or malformed quality fixture")
    if len(fixture["cases"]) != 8:
        raise ValueError("fixture version 1 must contain exactly eight cases")
    digest = hashlib.sha256(raw).hexdigest()
    return fixture, digest


def build_manifest(fixture: dict, fixture_hash: str) -> dict:
    cases = []
    for case in fixture["cases"]:
        sources = []
        seen_paths = set()
        for source in case["sources"]:
            path = source["path"]
            if path in seen_paths:
                raise ValueError(f"duplicate source path in {case['id']}: {path}")
            seen_paths.add(path)
            content = expand_source(source)
            sources.append({"path": path, "kind": source["kind"], "content": content})
        source_by_path = {source["path"]: source["content"] for source in sources}
        for citation in case["citations"]:
            text = source_by_path.get(citation["path"])
            lines = text.splitlines() if text is not None else []
            if not (1 <= citation["start_line"] <= citation["end_line"] <= len(lines)):
                raise ValueError(f"citation range outside source in {case['id']}")
        entry = {
            "id": case["id"], "task": case["task"], "question": case["question"],
            "sources": sources, "expected_facts": case["expected_facts"],
            "citations": case["citations"], "forbidden_claims": case["forbidden_claims"],
            "expected_outcome": case["expected_outcome"],
        }
        if "evaluation_context" in case:
            entry["evaluation_context"] = case["evaluation_context"]
        cases.append(entry)
    return {
        "kind": "synthetic_fixture_manifest",
        "fixture_version": fixture["version"],
        "fixture_sha256": fixture_hash,
        "quality_scores": None,
        "cases": cases,
    }


def _safe_error(exc: Exception) -> dict:
    detail = str(exc).lower()
    status = getattr(exc, "status", None)
    if isinstance(exc, CallBudgetExceeded) or status == "budget_exhausted":
        reason = "call_budget_exhausted"
    elif status == "rate_limited" or any(term in detail for term in ("429", "rate limit", "rate_limited", "quota", "billing")):
        reason = "provider_rate_or_quota_limit"
    else:
        reason = "provider_or_schema_error"
    # Provider error text may reflect credentials or request details.
    return {"type": type(exc).__name__, "reason": reason}


def run_live(fixture: dict, fixture_hash: str, max_calls: int) -> dict:
    if not 1 <= max_calls <= MAX_LIVE_CALLS:
        raise ValueError(f"--max-calls must be between 1 and {MAX_LIVE_CALLS}")

    sys.path.insert(0, str(ROOT))
    # Load the repository's standard environment file before the registry reads
    # credential references and workflow mappings.
    from app.config import settings as _settings
    from app.doc_generation import (
        DocProvider, MAX_SECTIONS, SECTION_OVERLAP, SOURCE_LIMIT, generate_summary, plan_sections,
    )
    from app import model_connections
    from app import model_text

    connection = model_connections.resolve("summary")
    if not model_text.configured("summary"):
        raise ValueError("Live summary evaluation requires a configured summary connection")
    model = connection.model
    connection_fingerprint = model_connections.fingerprint(connection)
    generator_version = json.dumps({
        "model": model, "connection": connection_fingerprint, "prompt": "2", "schema": "2",
        "source_limit": SOURCE_LIMIT, "section_overlap": SECTION_OVERLAP,
        "max_sections": MAX_SECTIONS, "max_calls": 12,
        "max_tokens": 600, "response_format": {"type": "json_object"},
        "window_seconds": 30.0, "request_interval_seconds": 1.1,
    }, sort_keys=True)

    calls = []
    results = []
    by_id = {case["id"]: case for case in fixture["cases"]}

    async def bounded_chat(**kwargs):
        # One shared quota request per DocProvider attempt; never retry inside it.
        if len(calls) >= max_calls:
            raise CallBudgetExceeded("live provider-call budget reached")
        calls.append(1)
        kwargs["max_attempts"] = 1
        return await model_text.chat(workflow="summary", **kwargs)

    provider = DocProvider(bounded_chat, max_attempts=max_calls)

    for case_id in LIVE_CASE_IDS:
        case = by_id[case_id]
        source = case["sources"][0]
        content = expand_source(source)
        plan = plan_sections(content)

        record = {
            "case_id": case_id,
            "task": case["task"],
            "model": model,
            "connection_fingerprint": connection_fingerprint,
            "generator_version": generator_version,
            "provider_call_count_before": len(calls),
        }
        try:
            record["output"] = __import__("asyncio").run(generate_summary(
                plan,
                language={".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "tsx"}.get(
                    Path(source["path"]).suffix.lower(), "code"
                ),
                qualified_name=source["path"],
                start_line=1,
                end_line=len(content.splitlines()),
                model=model,
                provider=provider,
            ))
            record["error"] = None
        except Exception as exc:
            record["output"] = None
            record["error"] = _safe_error(exc)
        record["provider_call_count_after"] = len(calls)
        results.append(record)

        reason = (record["error"] or {}).get("reason")
        if reason in {"provider_rate_or_quota_limit", "call_budget_exhausted"}:
            break

    return {
        "kind": "synthetic_live_summary_outputs",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "fixture_version": fixture["version"],
        "fixture_sha256": fixture_hash,
        "model": model,
        "connection_fingerprint": connection_fingerprint,
        "generator_version": generator_version,
        "max_provider_calls": max_calls,
        "provider_call_count": len(calls),
        "results": results,
        "quality_scores": None,
        "note": "Raw synthetic-input outputs only; no automatic factuality scores or judgments.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="call the configured summary model on two synthetic cases")
    parser.add_argument("--max-calls", type=int, default=MAX_LIVE_CALLS, help="maximum provider HTTP attempts for --live (1-8)")
    parser.add_argument("--output", type=Path, help="write JSON here instead of stdout")
    args = parser.parse_args()

    try:
        fixture, fixture_hash = load_fixture()
        if args.live:
            result = run_live(fixture, fixture_hash, args.max_calls)
        else:
            result = build_manifest(fixture, fixture_hash)
    except Exception as exc:
        print(f"evaluation runner error: {_safe_error(exc)}", file=sys.stderr)
        return 2

    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
