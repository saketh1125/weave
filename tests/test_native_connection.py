import asyncio
import pytest

from app import model_connections as model, model_quota
from app.model_preferences import Preferences, use_preferences
from app.routers.ask import _fit_ask_context


def test_native_resolution_requires_verified_selection_and_preserves_api_fingerprint(monkeypatch):
    from app import antigravity_bridge
    monkeypatch.setattr(antigravity_bridge, "configuration", lambda: ("http://localhost:9218", "token"))
    connection = model.Connection('a', 'mistral', 'm', 'https://example.com', 'KEY')
    import hashlib, json
    from dataclasses import asdict
    old = asdict(connection)
    del old['runtime_identity']
    assert model.fingerprint(connection) == hashlib.sha256(json.dumps({'connection': old, 'protocol_version': 1}, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    with use_preferences(Preferences({'qa': 'antigravity_local'}, 'm', None, None)):
        with pytest.raises(model.ProviderError): model.resolve('qa')
    with use_preferences(Preferences({'qa': 'antigravity_local'}, 'm', 'm', 'a' * 64)):
        native = model.resolve('qa')
        assert model.configured('qa') and native.runtime_identity == 'a' * 64 and native.tpm == 0
        assert _fit_ask_context('question', [], native, max_tokens=800)[1] is not None


@pytest.mark.asyncio
async def test_native_dispatch_validates_result_and_skips_quota(monkeypatch):
    from app import antigravity_bridge
    monkeypatch.setattr(antigravity_bridge, 'configuration', lambda: ('http://localhost:9218', 'token'))
    native = model.native_connection(Preferences({}, 'm', 'm', 'a' * 64))
    received = []
    async def generate(messages, tools, model_name, identity, **kwargs):
        received.append((model_name, identity))
        return {'message': {'role': 'assistant', 'content': '{\"ok\": true}'}}
    monkeypatch.setattr(antigravity_bridge, 'generate', generate)
    message, usage = await model_quota.request([{'role': 'user', 'content': 'hi'}], [], model_name='m', max_tokens=100, connection=native, response_format={'type': 'json_object'})
    assert message['content'] == '{\"ok\": true}' and usage['input_tokens'] is None
    assert received == [('m', 'a' * 64)]
    async def invalid(*args, **kwargs): return {'message': {'role': 'assistant', 'content': '[]'}}
    monkeypatch.setattr(antigravity_bridge, 'generate', invalid)
    with pytest.raises(model.ProviderError, match='invalid_response'):
        await model.complete([{'role': 'user', 'content': 'hi'}], connection=native, response_format={'type': 'json_object'})


@pytest.mark.asyncio
async def test_native_tool_calls_history_and_timeout(monkeypatch):
    from app import antigravity_bridge
    monkeypatch.setattr(antigravity_bridge, 'configuration', lambda: ('http://localhost:9218', 'token'))
    native = model.native_connection(Preferences({}, 'm', 'm', 'a' * 64))
    tools = [{'type': 'function', 'function': {'name': 'lookup', 'parameters': {'type': 'object', 'required': ['key'], 'properties': {'key': {'type': 'string'}}}}}]
    async def generate(*args, **kwargs):
        return {'message': {'role': 'assistant', 'content': None, 'tool_calls': [{'id': '1', 'type': 'function', 'function': {'name': 'lookup', 'arguments': '{"key":"x"}'}}]}}
    monkeypatch.setattr(antigravity_bridge, 'generate', generate)
    messages = [{'role': 'user', 'content': 'hi'}]
    message, _ = await model.complete(messages, tools, connection=native)
    assert message['tool_calls'][0]['function']['name'] == 'lookup'
    async def invalid(*args, **kwargs):
        return {'message': {'role': 'assistant', 'content': None, 'tool_calls': [{'id': '1', 'type': 'function', 'function': {'name': 'lookup', 'arguments': '{}'}}]}}
    monkeypatch.setattr(antigravity_bridge, 'generate', invalid)
    with pytest.raises(model.ProviderError, match='invalid_response'):
        await model.complete(messages, tools, connection=native)
    with pytest.raises(model.ProviderError, match='invalid_request'):
        await model.complete([{'role': 'assistant', '_sentra_native': {'adapter': 'mistral'}}], connection=native)
    cancelled = asyncio.Event()
    async def blocked(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    monkeypatch.setattr(antigravity_bridge, 'generate', blocked)
    with pytest.raises(model.ProviderError, match='timeout'):
        await model.complete(messages, connection=native, timeout_seconds=0.01)
    assert cancelled.is_set()


def test_native_connection_rejects_invalid_identity_or_disabled_bridge(monkeypatch):
    from app import antigravity_bridge
    monkeypatch.setattr(antigravity_bridge, 'configuration', lambda: None)
    with pytest.raises(model.ProviderError, match='connection_unavailable'):
        model.native_connection(Preferences({}, 'm', 'm', 'a' * 64))
    monkeypatch.setattr(antigravity_bridge, 'configuration', lambda: ('http://localhost:9218', 'token'))
    with pytest.raises(model.ProviderError, match='connection_unavailable'):
        model.native_connection(Preferences({}, 'm', 'm', 'bad'))


@pytest.mark.asyncio
async def test_native_preserves_text_with_valid_tool_call(monkeypatch):
    from app import antigravity_bridge
    monkeypatch.setenv('SENTRA_ANTIGRAVITY_ENABLED', 'true')
    monkeypatch.setenv('SENTRA_AGENT_BRIDGE_TOKEN', 't' * 40)
    native = model.native_connection(Preferences({}, 'm', 'm', 'a' * 64))
    tools = [{'type': 'function', 'function': {'name': 'check', 'parameters': {'type': 'object'}}}]
    async def generate(*args, **kwargs):
        return {'message': {'role': 'assistant', 'content': 'Checking.', 'tool_calls': [
            {'id': 'call_1', 'type': 'function', 'function': {'name': 'check', 'arguments': '{}'}}]}}
    monkeypatch.setattr(antigravity_bridge, 'generate', generate)
    message, _ = await model.complete([{'role': 'user', 'content': 'Check.'}], tools, connection=native)
    assert message['content'] == 'Checking.' and len(message['tool_calls']) == 1
