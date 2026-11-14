import asyncio
import json
import pytest
from app.documentation import reviewer

SECTION = {"id": "s1", "path": "README.md", "start_line": 1, "end_line": 1, "content": "The service is ready."}
class Tools:
    async def read_source(self, path, start_line, end_line):
        return {"path": path, "start_line": start_line, "end_line": end_line, "content": "ready = True"}
    async def list_files(self, query=None): return ["app.py"]
    async def search_source(self, query, limit=20): return []

def call(name, args, ident="call12345"):
    return {"role": "assistant", "content": None, "tool_calls": [{"id": ident, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}

def assessment():
    return {"claim": "The service is ready.", "documentation_citation": {"path": "README.md", "start_line": 1, "end_line": 1}, "verdict": "supported", "code_citations": [{"path": "app.py", "start_line": 1, "end_line": 1}], "suggested_update": "No change", "rationale": "The flag is true."}

@pytest.mark.asyncio
async def test_navigation_citations_and_resumption(monkeypatch):
    snapshots = []
    async def save(s): snapshots.append(s)
    responses = [call("list_files", {}), call("read_source", {"path": "app.py", "start_line": 1, "end_line": 1}), call("record_assessment", assessment()), call("finish_review", {"reason": "all_claims_reviewed"})]
    async def complete(messages, tools, **kwargs):
        assert "The service is ready." in messages[1]["content"]
        return responses.pop(0), {}
    monkeypatch.setattr(reviewer.provider, "complete", complete)
    monkeypatch.setattr(reviewer, "MAX_CALLS_PER_INVOCATION", 2)
    first = await reviewer.review_section(SECTION, Tools(), save_state=save)
    assert first["status"] == "paused" and first["state"]["calls"] == 2
    second = await reviewer.review_section(SECTION, Tools(), state=first["state"], save_state=save)
    assert second["status"] == "completed" and len(second["assessments"]) == 1
    assert second["state"]["calls"] == 4 and second["state"]["read_ranges"][0]["path"] == "app.py"
    assert snapshots and any(s["calls"] == 1 and len(s["messages"]) == 2 for s in snapshots)

@pytest.mark.asyncio
async def test_unread_citation_rejected_and_rate_limit_pauses(monkeypatch):
    snapshots = []
    async def save(s): snapshots.append(s)
    count = 0
    async def complete(messages, tools, **kwargs):
        nonlocal count
        count += 1
        if count == 1: return call("record_assessment", assessment()), {}
        assert "invalid_tool_request_or_result" in messages[-1]["content"]
        raise reviewer.provider.ProviderError("rate_limited", 37)
    monkeypatch.setattr(reviewer.provider, "complete", complete)
    result = await reviewer.review_section(SECTION, Tools(), save_state=save)
    assert result["status"] == "paused" and result["retry_after_seconds"] == 37
    assert result["assessments"] == [] and result["state"]["calls"] == 2

@pytest.mark.asyncio
async def test_section_mismatch_fails_without_provider(monkeypatch):
    async def save(s): pass
    result = await reviewer.review_section(SECTION, Tools(), state={"section_digest": "wrong"}, save_state=save)
    assert result["status"] == "failed"

@pytest.mark.asyncio
async def test_mistral_transport_429(monkeypatch):
    import httpx
    from app.documentation import model
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    real_client = httpx.AsyncClient
    async def respond(request):
        assert request.url.host == "api.mistral.ai"
        return httpx.Response(429, headers={"Retry-After": "17"})
    from app import model_connections
    monkeypatch.setattr(model_connections, "_transport", lambda connection: httpx.MockTransport(respond))
    with pytest.raises(model.ProviderError) as exc:
        await model.complete([], [], model=model.DEFAULT_MODEL)
    assert exc.value.reason == "rate_limited" and exc.value.retry_after_seconds == 17

@pytest.mark.asyncio
async def test_no_factual_claims_finishes(monkeypatch):
    async def save(s): pass
    async def complete(messages, tools, **kwargs):
        return call("finish_review", {"reason": "no_factual_claims"}), {}
    monkeypatch.setattr(reviewer.provider, "complete", complete)
    result = await reviewer.review_section(SECTION, Tools(), save_state=save)
    assert result["status"] == "completed" and result["assessments"] == []
    assert result["state"]["finish_reason"] == "no_factual_claims"

@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["unquoted", "markdown"])
async def test_fabricated_claim_and_document_as_code_rejected(monkeypatch, change):
    async def save(s): pass
    item = assessment()
    if change == "unquoted": item["claim"] = "A fabricated claim"
    else: item["code_citations"][0]["path"] = "README.md"
    responses = [call("read_source", {"path": item["code_citations"][0]["path"], "start_line": 1, "end_line": 1}), call("record_assessment", item)]
    async def complete(messages, tools, **kwargs):
        if responses: return responses.pop(0), {}
        assert "invalid_tool_request_or_result" in messages[-1]["content"]
        return {"role": "assistant", "content": "Done."}, {}
    monkeypatch.setattr(reviewer.provider, "complete", complete)
    result = await reviewer.review_section(SECTION, Tools(), save_state=save)
    assert result["status"] == "failed" and result["assessments"] == []

@pytest.mark.asyncio
async def test_large_section_completes_with_bounded_budget(monkeypatch):
    async def save(s): pass
    async def no_delay(_): pass
    monkeypatch.setattr(reviewer.asyncio, "sleep", no_delay)
    section = {**SECTION, "content": "The service is ready. " + "x" * 16000}
    responses = [call("read_source", {"path": "app.py", "start_line": 1, "end_line": 1}), call("record_assessment", assessment()), call("finish_review", {"reason": "all_claims_reviewed"})]
    async def complete(messages, tools, **kwargs): return responses.pop(0), {}
    monkeypatch.setattr(reviewer.provider, "complete", complete)
    result = await reviewer.review_section(section, Tools(), save_state=save)
    assert result["status"] == "completed" and result["state"]["calls"] == 3
    assert result["state"]["tokens"] < reviewer.MAX_TOTAL_TOKENS

@pytest.mark.asyncio
async def test_no_claims_finish_rejected_after_invalid_request(monkeypatch):
    async def save(s): pass
    async def no_delay(_): pass
    monkeypatch.setattr(reviewer.asyncio, "sleep", no_delay)
    responses = [call("record_assessment", assessment()), call("finish_review", {"reason": "no_factual_claims"}), {"role": "assistant", "content": "Done."}]
    async def complete(messages, tools, **kwargs): return responses.pop(0), {}
    monkeypatch.setattr(reviewer.provider, "complete", complete)
    result = await reviewer.review_section(SECTION, Tools(), save_state=save)
    assert result["status"] == "failed" and result["state"]["invalid_tool_requests"] == 2

@pytest.mark.asyncio
async def test_semantic_search_discovers_but_cannot_supply_citations(monkeypatch):
    async def save(s): pass
    async def no_delay(_): pass
    monkeypatch.setattr(reviewer.asyncio, "sleep", no_delay)
    class SemanticTools(Tools):
        async def semantic_search(self, query, limit=5):
            assert query == "readiness" and limit == 5
            return [{"path": "app.py", "start_line": 1, "end_line": 1}]
    responses = [call("semantic_search", {"query": "readiness", "limit": 5}), call("record_assessment", assessment()), {"role": "assistant", "content": "Done."}]
    async def complete(messages, tools, **kwargs): return responses.pop(0), {}
    monkeypatch.setattr(reviewer.provider, "complete", complete)
    result = await reviewer.review_section(SECTION, SemanticTools(), save_state=save)
    assert result["status"] == "failed" and result["assessments"] == []
    assert result["state"]["reviewer_version"] == 1
    old = dict(result["state"], reviewer_version=0)
    assert (await reviewer.review_section(SECTION, SemanticTools(), state=old, save_state=save))["reason"] == "invalid_state_or_section"

def test_context_compaction_preserves_section_and_read_evidence():
    import copy
    from app.documentation import reviewer
    messages = [
        {'role': 'system', 'content': 'instructions'},
        {'role': 'user', 'content': 'original section'},
        {'role': 'assistant', 'tool_calls': [{'id': 'd', 'function': {'name': 'search_source', 'arguments': '{}'}}]},
        {'role': 'tool', 'tool_call_id': 'd', 'content': 'x' * 17000},
        {'role': 'assistant', 'tool_calls': [{'id': 'r', 'function': {'name': 'read_source', 'arguments': '{}'}}]},
        {'role': 'tool', 'tool_call_id': 'r', 'content': 'source evidence'},
        {'role': 'assistant', 'tool_calls': [{'id': 'a', 'function': {'name': 'record_assessment', 'arguments': '{}'}}]},
        {'role': 'tool', 'tool_call_id': 'a', 'content': '{"recorded":true}'},
    ]
    original = copy.deepcopy(messages)
    compacted = reviewer.compact_discovery_messages(messages)
    assert messages == original
    assert compacted[1] == original[1]
    assert compacted[3]['tool_call_id'] == 'd' and len(compacted[3]['content']) < 100
    assert compacted[5:] == original[5:]
    assert reviewer.valid_citation({'path': 'app.py', 'start_line': 1, 'end_line': 1}, [{'path': 'app.py', 'start_line': 1, 'end_line': 2}])

@pytest.mark.asyncio
async def test_legacy_resume_adds_tool_guidance_once_without_replacing_history(monkeypatch):
    monkeypatch.setattr(reviewer, 'MAX_CALLS_PER_INVOCATION', 1)
    async def save(state): pass
    async def first(messages, tools, **kwargs):
        return call('list_files', {}), {}
    paused = await reviewer.review_section(SECTION, Tools(), save_state=save, complete=first)
    state = paused['state']
    state.pop('tool_guidance_version', None)
    original = list(state['messages'])
    async def resumed(messages, tools, **kwargs):
        assert messages[:len(original)] == original
        guidance = [m for m in messages if m.get('role') == 'user' and 'Workflow tool clarification' in str(m.get('content'))]
        assert len(guidance) == 1
        return call('finish_review', {'reason': 'no_factual_claims'}), {}
    final = await reviewer.review_section(SECTION, Tools(), state=state, save_state=save, complete=resumed)
    assert final['status'] == 'completed'
    assert final['state']['tool_guidance_version'] == 1
