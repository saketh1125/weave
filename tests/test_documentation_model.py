import json
import httpx
import pytest

from app import model_connections
from app.documentation import model


@pytest.mark.asyncio
async def test_single_tool_forced_and_thinking_preserved(monkeypatch):
    sent = []
    message = {'role': 'assistant', 'content': [{'type': 'thinking', 'thinking': [{'type': 'text', 'text': 'reasoning'}]}], 'tool_calls': []}
    async def respond(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'finish_reason': 'tool_calls', 'message': message}], 'usage': {'total_tokens': 42}})
    monkeypatch.setenv('MISTRAL_API_KEY', 'test')
    monkeypatch.setattr(model_connections, '_transport', lambda connection: httpx.MockTransport(respond))
    tools = [{'type': 'function', 'function': {'name': 'assign_sections'}}]
    result, usage = await model.complete([], tools, max_tokens=4096)
    assert result['content'] == '' and result['_sentra_native']['message'] == message
    assert usage['total_tokens'] == 42
    assert sent[0]['reasoning_effort'] == 'low'
    assert sent[0]['tool_choice'] == {'type': 'function', 'function': {'name': 'assign_sections'}}
    assert sent[0]['max_tokens'] == 4096


@pytest.mark.asyncio
async def test_review_reasoning_and_truncation(monkeypatch):
    sent = []
    async def respond(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'finish_reason': 'length', 'message': {'role': 'assistant', 'content': []}}]})
    monkeypatch.setenv('MISTRAL_API_KEY', 'test')
    monkeypatch.setattr(model_connections, '_transport', lambda connection: httpx.MockTransport(respond))
    tools = [{'type': 'function', 'function': {'name': name}} for name in ('read', 'finish')]
    with pytest.raises(model.ProviderError) as error:
        await model.complete([], tools, max_tokens=4096)
    assert error.value.reason == 'output_budget_exhausted'
    assert sent[0]['reasoning_effort'] == 'medium' and sent[0]['tool_choice'] == 'auto'
