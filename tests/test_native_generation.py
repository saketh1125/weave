import pytest
import jsonschema

from scripts import native_generation as generation
from deployment.antigravity.generation_tools import Result


def job():
    return {'model': 'm', 'messages': [{'role': 'user', 'content': 'Hello'}],
        'tools': [{'type': 'function', 'function': {'name': 'lookup', 'parameters': {
            'type': 'object', 'properties': {'key': {'type': 'string'}}, 'required': ['key']}}}],
        'response_format': None, 'force_assessment': False, 'max_tokens': 100, 'timeout_seconds': 60}


def test_result_mailbox_only_accepts_one_bounded_shape():
    mailbox = Result()
    assert mailbox.call_tool('other', {})[1]
    assert mailbox.call_tool('submit_result', {'content': 1, 'tool_calls': []})[1]
    assert mailbox.call_tool('submit_result', {'content': 'x' * 33000, 'tool_calls': []})[1]
    assert not mailbox.call_tool('submit_result', {'content': 'ok', 'tool_calls': []})[1]
    assert mailbox.call_tool('submit_result', {'content': 'again', 'tool_calls': []})[1]


def test_generation_result_validation():
    request = job()
    generation.validate_request(request)
    text_request = {**request, 'tools': []}
    assert generation.validate_result({'content': 'hello', 'tool_calls': []}, text_request)['content'] == 'hello'
    result = generation.validate_result({'content': None, 'tool_calls': [{'name': 'lookup', 'arguments': {'key': 'x'}}]}, request)
    assert result['tool_calls'][0]['function']['name'] == 'lookup'
    mixed = generation.validate_result({'content': 'context', 'tool_calls': [{'name': 'lookup', 'arguments': {'key': 'x'}}]}, request)
    assert mixed['content'] == 'context' and mixed['tool_calls']
    with pytest.raises(ValueError):
        generation.validate_result({'content': None, 'tool_calls': [{'name': 'unknown', 'arguments': {}}]}, request)
    with pytest.raises(jsonschema.ValidationError):
        generation.validate_result({'content': None, 'tool_calls': [{'name': 'lookup', 'arguments': {}}]}, request)
    text_request['response_format'] = {'type': 'json_object'}
    with pytest.raises(ValueError): generation.validate_result({'content': '[]', 'tool_calls': []}, text_request)
    assert generation.validate_result({'content': '{}', 'tool_calls': []}, text_request)['content'] == '{}'
    request['tools'][0]['function']['parameters'] = {'$ref': 'https://example.com/schema'}
    with pytest.raises(ValueError): generation.validate_request(request)
    for key in ('$dynamicRef', '$id'):
        request['tools'][0]['function']['parameters'] = {key: 'https://example.com/schema'}
        with pytest.raises(ValueError): generation.validate_request(request)


@pytest.mark.asyncio
async def test_exercise_uses_one_native_session_and_fixed_tool():
    class Client:
        calls = []
        async def request(self, method, params):
            self.calls.append((method, params))
            if method == 'initialize': return {'protocolVersion': 1}
            if method == 'session/new': return {'sessionId': 's', 'configOptions': [
                {'id': 'model', 'currentValue': 'm', 'options': [{'value': 'm'}]}]}
            return {}
    client = Client()
    async def read(): return {'result': {'content': 'ok', 'tool_calls': []}}
    assert (await generation.exercise(client, 'm', '{}', read))['content'] == 'ok'
    assert [c[0] for c in client.calls] == ['initialize', 'session/new', 'session/prompt']
    assert client.calls[1][1]['mcpServers'][0]['url'] == 'http://generation-tools:8080/mcp'
    assert client.calls[0][1]['clientCapabilities']['terminal'] is False


def test_safe_failure_categories():
    import asyncio
    from app.acp_transport import ACPError
    assert generation.failure_reason(asyncio.TimeoutError()) == 'agent_timeout'
    assert generation.failure_reason(ACPError('transport deadline exceeded')) == 'agent_timeout'
    assert generation.failure_reason(ValueError('private output')) == 'invalid_agent_result'
    assert generation.failure_reason(ACPError('invalid generation result')) == 'invalid_agent_result'
    assert generation.failure_reason(RuntimeError('private output')) == 'agent_execution_failed'


def test_busy_http_contract():
    import threading
    import httpx
    from http.server import ThreadingHTTPServer
    from scripts.antigravity_bridge import Handler, AgentBusy
    class Busy:
        token = 't' * 40
        def start(self, payload): raise AgentBusy('private')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.bridge = Busy()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        response = httpx.post(f'http://127.0.0.1:{server.server_port}/jobs', json={'kind': 'test'},
            headers={'Authorization': 'Bearer ' + server.bridge.token}, trust_env=False)
        assert response.status_code == 409 and response.json() == {'status': 'agent_busy'}
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_diagnostic_contains_shape_only():
    mailbox = Result()
    mailbox.call_tool('submit_result', {'content': 'private', 'tool_calls': [{'name': 'private', 'arguments': 'private'}], 'private': 'private'})
    assert mailbox.diagnostic == {'type': 'object', 'content_present': True, 'calls_present': True,
        'extra_keys': True, 'content_type': 'string', 'calls_type': 'array', 'calls_count': 1,
        'call_type': 'object', 'name_present': True, 'arguments_present': True, 'call_extra_keys': False,
        'name_type': 'string', 'arguments_type': 'string'}
    assert 'private' not in str(mailbox.diagnostic)
