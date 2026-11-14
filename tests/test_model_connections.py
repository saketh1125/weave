import asyncio
import json
import httpx
import pytest
from app import model_connections as mc


def test_defaults_and_fail_closed(monkeypatch):
    monkeypatch.delenv('SENTRA_MODEL_CONNECTIONS', raising=False)
    monkeypatch.delenv('SENTRA_MODEL_WORKFLOWS', raising=False)
    monkeypatch.delenv('MISTRAL_API_KEY2', raising=False)
    assert mc.resolve('reviewer').quota_group == 'mistral:1'
    with pytest.raises(mc.ProviderError) as exc:
        mc.resolve('reviewer', 2)
    assert exc.value.reason == 'connection_unavailable'
    monkeypatch.setenv('SENTRA_MODEL_CONNECTIONS', '{secret')
    with pytest.raises(mc.ProviderError) as exc:
        mc.resolve('reviewer')
    assert str(exc.value) == 'invalid_configuration'

@pytest.mark.asyncio
async def test_native_metadata_and_sanitized_errors(monkeypatch):
    monkeypatch.setenv('MISTRAL_API_KEY', 'secret')
    async def handle(request):
        assert request.headers['authorization'] == 'Bearer secret'
        return httpx.Response(200, json={'choices':[{'message':{'role':'assistant','content':'ok','thinking':[{'text':'native'}]}}], 'usage':{'total_tokens':3}})
    monkeypatch.setattr(mc, '_transport', lambda connection: httpx.MockTransport(handle))
    message, usage = await mc.complete([{'role':'user','content':'hi'}])
    assert message['_sentra_native']['message']['thinking'] and usage['total_tokens'] == 3
    async def bad(request): return httpx.Response(429, headers={'Retry-After':'4'}, text='secret')
    monkeypatch.setattr(mc, '_transport', lambda connection: httpx.MockTransport(bad))
    with pytest.raises(mc.ProviderError) as exc:
        await mc.complete([])
    assert exc.value.reason == 'rate_limited' and exc.value.retry_after_seconds == 4
    assert 'secret' not in str(exc.value)

@pytest.mark.asyncio
async def test_cancel_propagates_and_closes(monkeypatch):
    monkeypatch.setenv('MISTRAL_API_KEY', 'secret')
    entered = asyncio.Event()
    closed = []
    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            entered.set()
            await asyncio.Event().wait()
        async def aclose(self): closed.append(True)
    monkeypatch.setattr(mc, '_transport', lambda connection: Transport())
    task = asyncio.create_task(mc.complete([]))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert closed

@pytest.mark.asyncio
async def test_private_resolution_rejected(monkeypatch):
    loop = asyncio.get_running_loop()
    async def lookup(*args, **kwargs): return [(2, 1, 6, '', ('127.0.0.1', 443))]
    monkeypatch.setattr(loop, 'getaddrinfo', lookup)
    with pytest.raises(mc.ProviderError) as exc:
        await mc._ValidatedBackend(False).connect_tcp('example.com', 443)
    assert exc.value.reason == 'invalid_endpoint' and not exc.value.request_sent


def test_blank_settings_and_fingerprint(monkeypatch):
    from dataclasses import replace
    monkeypatch.setenv('SENTRA_MODEL_CONNECTIONS', '')
    monkeypatch.setenv('SENTRA_MODEL_WORKFLOWS', '')
    c = mc.resolve('planner')
    assert c.identity != replace(c, model='different').identity
    assert c.identity != replace(c, capabilities=('tools',)).identity
    monkeypatch.setenv('MISTRAL_API_KEY', 'one')
    first = c.identity
    monkeypatch.setenv('MISTRAL_API_KEY', 'two')
    assert c.identity == first


def _compatible(monkeypatch, capabilities):
    monkeypatch.setenv('SENTRA_MODEL_CONNECTIONS', json.dumps({'custom': {'adapter':'openai_compatible', 'model':'test', 'endpoint':'https://example.com/v1', 'credential_env':'TEST_CONNECTION_KEY', 'capabilities':capabilities}}))
    monkeypatch.setenv('SENTRA_MODEL_WORKFLOWS', '{"planner":"custom"}')
    monkeypatch.setenv('TEST_CONNECTION_KEY', 'test-secret')
    return mc.resolve('planner')

@pytest.mark.asyncio
async def test_tool_roundtrip_and_native_thinking(monkeypatch):
    monkeypatch.setenv('MISTRAL_API_KEY', 'secret')
    requests = []
    async def handler(request):
        requests.append(json.loads(request.content))
        message = {'role':'assistant', 'content':None, 'thinking':[{'type':'thinking','value':'state'}], 'tool_calls':[{'id':'call1','type':'function','function':{'name':'record_assessment','arguments':'{}'}}]}
        return httpx.Response(200, json={'choices':[{'message':message}]})
    monkeypatch.setattr(mc, '_transport', lambda c: httpx.MockTransport(handler))
    tool = {'type':'function','function':{'name':'record_assessment','parameters':{'type':'object'}}}
    result, _ = await mc.complete([{'role':'user','content':'x'}], [tool], force_assessment=True)
    await mc.complete([{'role':'user','content':'x'}, result, {'role':'tool','tool_call_id':'call1','content':'ok'}], [tool])
    assert requests[1]['messages'][1] == result['_sentra_native']['message']
    assert requests[0]['tool_choice']['function']['name'] == 'record_assessment'

@pytest.mark.asyncio
async def test_json_schema_validation_and_invalid_schema(monkeypatch):
    c = _compatible(monkeypatch, ['json_schema','json_object'])
    value = {'score': 2}
    async def handler(request): return httpx.Response(200, json={'choices':[{'message':{'role':'assistant','content':json.dumps(value)}}]})
    monkeypatch.setattr(mc, '_transport', lambda c: httpx.MockTransport(handler))
    fmt = {'type':'json_schema','json_schema':{'name':'score','schema':{'type':'object','properties':{'score':{'type':'integer'}},'required':['score']}}}
    result, _ = await mc.complete([], connection=c, response_format=fmt)
    assert json.loads(result['content']) == value
    value['score'] = 'bad'
    with pytest.raises(mc.ProviderError, match='invalid_response_schema'): await mc.complete([], connection=c, response_format=fmt)
    with pytest.raises(mc.ProviderError, match='invalid_schema'): await mc.complete([], connection=c, response_format={'type':'json_schema'})
    value.clear()
    await mc.complete([], connection=c, response_format={'type':'json_object'})

@pytest.mark.asyncio
async def test_capabilities_and_unknown_response_fields_rejected(monkeypatch):
    c = _compatible(monkeypatch, [])
    with pytest.raises(mc.ProviderError, match='unsupported_capability'):
        await mc.complete([], [{'type':'function'}], connection=c)
    with pytest.raises(mc.ProviderError, match='unsupported_capability'):
        await mc.complete([{'role':'assistant','reasoning_content':'hidden'}], connection=c)
    async def handler(request): return httpx.Response(200, json={'choices':[{'message':{'role':'assistant','content':'x','vendor_state':'opaque'}}]})
    monkeypatch.setattr(mc, '_transport', lambda c: httpx.MockTransport(handler))
    with pytest.raises(mc.ProviderError, match='unsupported_capability'): await mc.complete([], connection=c)

@pytest.mark.asyncio
async def test_timeout_redirect_and_exception_redaction(monkeypatch):
    monkeypatch.setenv('MISTRAL_API_KEY', 'secret')
    async def slow(request): await asyncio.Event().wait()
    monkeypatch.setattr(mc, '_transport', lambda c: httpx.MockTransport(slow))
    with pytest.raises(mc.ProviderError, match='timeout'): await mc.complete([], timeout_seconds=0.01)
    async def redirect(request): return httpx.Response(302, headers={'Location':'http://127.0.0.1/'})
    monkeypatch.setattr(mc, '_transport', lambda c: httpx.MockTransport(redirect))
    with pytest.raises(mc.ProviderError) as exc: await mc.complete([])
    assert exc.value.status_code == 302
    async def fail(request): raise httpx.ConnectError('secret')
    monkeypatch.setattr(mc, '_transport', lambda c: httpx.MockTransport(fail))
    with pytest.raises(mc.ProviderError) as exc: await mc.complete([])
    assert 'secret' not in str(exc.value)

@pytest.mark.asyncio
async def test_group_serialization(monkeypatch):
    monkeypatch.setenv('MISTRAL_API_KEY', 'secret')
    active = 0
    highest = 0
    async def handler(request):
        nonlocal active, highest
        active += 1
        highest = max(highest, active)
        await asyncio.sleep(.02)
        active -= 1
        return httpx.Response(200, json={'choices':[{'message':{'role':'assistant','content':'ok'}}]})
    monkeypatch.setattr(mc, '_transport', lambda c: httpx.MockTransport(handler))
    await asyncio.gather(mc.complete([]), mc.complete([]))
    assert highest == 1

@pytest.mark.asyncio
async def test_dns_pins_validated_address(monkeypatch):
    loop = asyncio.get_running_loop()
    lookups = []
    async def lookup(*args, **kwargs):
        lookups.append(args[0])
        return [(2, 1, 6, '', ('8.8.8.8', 443))]
    monkeypatch.setattr(loop, 'getaddrinfo', lookup)
    backend = mc._ValidatedBackend(False)
    addresses = []
    async def connect(host, port, **kwargs): addresses.append(host); return object()
    monkeypatch.setattr(backend.backend, 'connect_tcp', connect)
    await backend.connect_tcp('example.com', 443)
    assert lookups == ['example.com'] and addresses == ['8.8.8.8']

@pytest.mark.asyncio
async def test_stream_rejected(monkeypatch):
    with pytest.raises(mc.ProviderError, match='unsupported_capability'):
        await mc.complete([], stream=True)


def test_shared_quota_must_agree(monkeypatch):
    base = {'adapter':'mistral', 'model':'test', 'endpoint':'https://example.com/v1', 'credential_env':'CUSTOM_KEY', 'quota_group':'mistral:1', 'rpm':5}
    monkeypatch.setenv('SENTRA_MODEL_CONNECTIONS', json.dumps({'custom':base}))
    with pytest.raises(mc.ProviderError, match='invalid_configuration'): mc.resolve('planner')
    base.update(quota_group='other', credential_env='MISTRAL_API_KEY', rpm=50)
    monkeypatch.setenv('SENTRA_MODEL_CONNECTIONS', json.dumps({'custom':base}))
    with pytest.raises(mc.ProviderError, match='invalid_configuration'): mc.resolve('planner')

@pytest.mark.asyncio
async def test_context_budget_and_usage_validation(monkeypatch):
    from dataclasses import replace
    monkeypatch.setenv('MISTRAL_API_KEY', 'secret')
    c = replace(mc.resolve('planner'), context_tokens=1100)
    async def handler(request): return httpx.Response(200, json={'choices':[{'message':{'role':'assistant','content':'ok'}}], 'usage':{'total_tokens':'fake', 'prompt_tokens':10, 'completion_tokens':-1}})
    monkeypatch.setattr(mc, '_transport', lambda c: httpx.MockTransport(handler))
    with pytest.raises(mc.ProviderError, match='context_budget_exceeded'):
        await mc.complete([{'role':'user','content':'x'*100}], connection=c)
    _, usage = await mc.complete([{'role':'user','content':'x'*100}], connection=c, prompt_tokens_upper_bound=20)
    assert usage == {'prompt_tokens':10}

@pytest.mark.asyncio
async def test_real_loopback_http_transport(monkeypatch):
    from dataclasses import replace
    monkeypatch.setenv('MISTRAL_API_KEY', 'secret')
    async def serve(reader, writer):
        await reader.readuntil(b'\r\n\r\n')
        body = b'{"choices":[{"message":{"role":"assistant","content":"ok"}}]}'
        writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: ' + str(len(body)).encode() + b'\r\nConnection: close\r\n\r\n' + body)
        await writer.drain()
        writer.close()
        await writer.wait_closed()
    server = await asyncio.start_server(serve, '127.0.0.1', 0)
    try:
        port = server.sockets[0].getsockname()[1]
        c = replace(mc.resolve('planner'), endpoint=f'http://localhost:{port}/v1', allow_private=True)
        result, _ = await mc.complete([], connection=c)
        assert result['content'] == 'ok'
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.parametrize('endpoint', ['https://example.com:99999/v1', 'https://example.com:abc/v1', 'https://example.com\\@127.0.0.1/v1', 'https://%65xample.com/v1', 'https://example.com/\npath'])
def test_invalid_endpoint_configuration(monkeypatch, endpoint):
    cfg = {'adapter':'mistral', 'model':'test', 'endpoint':endpoint, 'credential_env':'CUSTOM_KEY'}
    monkeypatch.setenv('SENTRA_MODEL_CONNECTIONS', json.dumps({'custom':cfg}))
    with pytest.raises(mc.ProviderError, match='invalid_configuration'): mc.validate_configuration()

@pytest.mark.asyncio
async def test_schema_external_reference_and_reasoning_profile(monkeypatch):
    from dataclasses import replace
    c = _compatible(monkeypatch, ['json_schema'])
    fmt = {'type':'json_schema','json_schema':{'schema':{'$id':'https://evil.example/schema', '$ref':'#/definitions/a', 'definitions':{'a':{'type':'object'}}}}}
    with pytest.raises(mc.ProviderError, match='invalid_schema'): await mc.complete([], connection=c, response_format=fmt)
    monkeypatch.setenv('MISTRAL_API_KEY', 'secret')
    c = replace(mc.resolve('reviewer'), reasoning_effort='high')
    captured = []
    async def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={'choices':[{'message':{'role':'assistant','content':'ok'}}]})
    monkeypatch.setattr(mc, '_transport', lambda c: httpx.MockTransport(handler))
    await mc.complete([], connection=c)
    assert captured[0]['reasoning_effort'] == 'high'

@pytest.mark.asyncio
async def test_mistral_json_text_block_and_cross_adapter_replay(monkeypatch):
    monkeypatch.setenv('MISTRAL_API_KEY', 'secret')
    native = {'role':'assistant', 'content':[{'type':'thinking','thinking':'private'}, {'type':'text','text':'{"score": 1}'}]}
    async def handler(request): return httpx.Response(200, json={'choices':[{'message':native}]})
    monkeypatch.setattr(mc, '_transport', lambda c: httpx.MockTransport(handler))
    result, _ = await mc.complete([], response_format={'type':'json_object'})
    assert result['content'] == '{"score": 1}' and result['_sentra_native']['message'] == native
    c = _compatible(monkeypatch, [])
    with pytest.raises(mc.ProviderError, match='unsupported_capability'):
        await mc.complete([result], connection=c)

@pytest.mark.asyncio
async def test_legacy_key_never_falls_back_for_custom_primary(monkeypatch):
    monkeypatch.delenv('MISTRAL_API_KEY', raising=False)
    monkeypatch.setenv('SENTRA_MISTRAL_API_KEY', 'legacy-secret')
    cfg = {'adapter':'mistral', 'model':'custom', 'endpoint':'https://other.example/v1', 'credential_env':'CUSTOM_KEY'}
    monkeypatch.setenv('SENTRA_MODEL_CONNECTIONS', json.dumps({'mistral_primary':cfg}))
    monkeypatch.delenv('CUSTOM_KEY', raising=False)
    assert not mc.configured('planner')
    with pytest.raises(mc.ProviderError, match='missing_credentials'): await mc.complete([], workflow='planner')

@pytest.mark.asyncio
@pytest.mark.parametrize('address,require_private', [('8.8.8.8', True), ('169.254.169.254', False), ('169.254.169.254', True), ('ff02::1', False)])
async def test_local_endpoint_rejects_public_http_and_metadata(monkeypatch, address, require_private):
    loop = asyncio.get_running_loop()
    async def lookup(*args, **kwargs): return [(2, 1, 6, '', (address, 80))]
    monkeypatch.setattr(loop, 'getaddrinfo', lookup)
    with pytest.raises(mc.ProviderError, match='invalid_endpoint'):
        await mc._ValidatedBackend(True, require_private=require_private).connect_tcp('example.com', 80)

@pytest.mark.asyncio
async def test_identical_credentials_cannot_split_same_origin_quota(monkeypatch):
    monkeypatch.setenv('MISTRAL_API_KEY', 'same-secret')
    monkeypatch.setenv('OTHER_KEY', 'same-secret')
    cfg = {'adapter':'mistral', 'model':'test', 'endpoint':'https://api.mistral.ai/v1', 'credential_env':'OTHER_KEY', 'quota_group':'different'}
    monkeypatch.setenv('SENTRA_MODEL_CONNECTIONS', json.dumps({'other':cfg}))
    with pytest.raises(mc.ProviderError) as exc: await mc.complete([])
    assert exc.value.reason == 'invalid_configuration' and 'same-secret' not in str(exc.value)
