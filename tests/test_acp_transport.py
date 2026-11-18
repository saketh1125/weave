import asyncio
import sys

import pytest

from app.acp_transport import ACPClient, ACPError


CHILD = r'''
import json, sys
mode = sys.argv[1]
held = None
for line in sys.stdin:
    m = json.loads(line)
    method = m.get("method")
    if method == "session/cancel":
        break
    if "id" not in m:
        continue
    if mode == "reverse":
        if held is None:
            held = m; continue
        for item in (m, held):
            print(json.dumps({"jsonrpc":"2.0","id":item["id"],"result":item["method"]}), flush=True)
        continue
    if mode in ("rpc_error", "bad_error", "ambiguous", "bool_id"):
        reply = {"jsonrpc":"2.0", "id":m["id"], "error":{"code":-32000,"message":"secret", "data":"secret"}}
        if mode == "bad_error": reply["error"]["code"] = True
        if mode == "ambiguous": reply["method"] = "secret"
        if mode == "bool_id": reply["id"] = True
        print(json.dumps(reply), flush=True); continue
    if mode == "notify":
        print(json.dumps({"jsonrpc":"2.0","method":"update","params":{}}), flush=True)
        continue
    if mode == "malformed":
        print("{bad secret", flush=True); continue
    if mode == "oversized":
        print(json.dumps({"jsonrpc":"2.0","id":m["id"],"result":"x"*500}), flush=True); continue
    if mode == "wrong_id":
        print(json.dumps({"jsonrpc":"2.0","id":999,"result":{}}), flush=True); continue
    if method == "session/prompt" and mode == "cancel":
        continue
    if method == "session/prompt" and mode == "server":
        for i, name in enumerate(("session/request_permission", "fs/write_text_file", "terminal/create"), 40):
            print(json.dumps({"jsonrpc":"2.0","id":i,"method":name,"params":{}}), flush=True)
            reply = json.loads(sys.stdin.readline())
            assert reply["id"] == i
            if i == 40: assert reply["result"]["outcome"]["outcome"] == "cancelled"
            else: assert reply["error"]["code"] == -32601
    result = {"sessionId":"local-session"} if method == "session/new" else {"ok":True}
    print(json.dumps({"jsonrpc":"2.0","id":m["id"],"result":result}), flush=True)
'''


async def make_client(mode="normal", **limits):
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-u", "-c", CHILD, mode,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    async def terminate():
        if process.returncode is None:
            process.terminate()
        await process.wait()
    return ACPClient(process, terminate=terminate, **limits), process


def test_synthetic_session_and_denied_server_requests():
    async def run():
        client, process = await make_client("server")
        async with client:
            assert await client.request("initialize", {}) == {"ok": True}
            assert await client.request("session/new", {}) == {"sessionId": "local-session"}
            assert await client.request("session/prompt", {}) == {"ok": True}
        assert process.returncode is not None
    asyncio.run(run())


def test_cancel_sends_notification_and_cleanup():
    async def run():
        client, process = await make_client("cancel")
        await client.request("session/new", {})
        task = asyncio.create_task(client.request("session/prompt", {}))
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(process.wait(), 2)
        assert process.returncode == 0
        await client.close()
    asyncio.run(run())


@pytest.mark.parametrize("mode", ["malformed", "oversized", "wrong_id"])
def test_invalid_replies_are_sanitized(mode):
    async def run():
        client, process = await make_client(mode, max_line=256)
        async with client:
            with pytest.raises(ACPError) as error:
                await client.request("initialize", {})
            assert "secret" not in str(error.value)
        assert process.returncode is not None
    asyncio.run(run())


def test_roundtrip_limit():
    async def run():
        client, _ = await make_client(max_roundtrips=1)
        async with client:
            await client.request("initialize", {})
            with pytest.raises(ACPError, match="roundtrip limit"):
                await client.request("session/new", {})
    asyncio.run(run())


def test_out_of_order_replies_match_pending_ids():
    async def run():
        client, _ = await make_client("reverse")
        async with client:
            first = asyncio.create_task(client.request("first", {}))
            second = asyncio.create_task(client.request("second", {}))
            assert await asyncio.gather(first, second) == ["first", "second"]
    asyncio.run(run())


def test_rpc_error_exposes_only_numeric_code():
    async def run():
        client, _ = await make_client("rpc_error")
        async with client:
            with pytest.raises(ACPError) as error:
                await client.request("initialize", {})
            assert error.value.code == -32000
            assert "secret" not in str(error.value)
    asyncio.run(run())


@pytest.mark.parametrize("mode", ["bad_error", "ambiguous", "bool_id"])
def test_bad_rpc_shapes_are_rejected(mode):
    async def run():
        client, _ = await make_client(mode)
        async with client:
            with pytest.raises(ACPError) as error:
                await client.request("initialize", {})
            assert error.value.code is None
            assert "secret" not in str(error.value)
    asyncio.run(run())


def test_server_requests_are_bounded():
    async def run():
        client, _ = await make_client("server", max_roundtrips=2)
        async with client:
            await client.request("initialize", {})
            with pytest.raises(ACPError):
                await client.request("session/prompt", {})
    asyncio.run(run())


def test_cancelled_context_terminates_child():
    async def run():
        client, process = await make_client("cancel")
        entered = asyncio.Event()
        async def operation():
            async with client:
                await client.request("session/new", {})
                entered.set()
                await client.request("session/prompt", {})
        task = asyncio.create_task(operation())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert process.returncode is not None
    asyncio.run(run())


def test_async_notification_callback_fails_without_coroutine_leak():
    async def callback(method, params):
        pass
    async def run():
        client, _ = await make_client("notify", notification=callback)
        async with client:
            with pytest.raises(ACPError, match="transport response failed"):
                await client.request("initialize", {})
    asyncio.run(run())


def test_native_permission_uses_machine_name_and_session_never_title():
    async def run():
        client, _ = await make_client(allowed_tool_names=frozenset({'mcp_sentra_read_source'}))
        replies = []
        async def capture(message): replies.append(message)
        client.session_id = 's'
        client._send = capture
        try:
            for name, session in [('mcp_sentra_read_source', 's'), ('run_command', 's'), ('mcp_sentra_read_source', 'other')]:
                await client._handle_request({'id': 1, 'method': 'session/request_permission', 'params': {
                    'sessionId': session, 'toolCall': {'name': name, 'title': 'Run mcp_sentra_read_source', 'kind': 'other'},
                    'options': [{'optionId': 'allow', 'kind': 'allow_once'}]}})
            assert replies[0]['result']['outcome'] == {'outcome': 'selected', 'optionId': 'allow'}
            assert all(r['result']['outcome']['outcome'] == 'cancelled' for r in replies[1:])
        finally:
            await client.close()
    asyncio.run(run())


def test_multiplex_permissions_and_cancellation_are_session_specific():
    async def run():
        client, _ = await make_client('cancel', allowed_tool_names=frozenset({'mcp_sentra_read_source'}))
        client.session_ids.update({'one', 'two'})
        client.session_id = 'two'
        sent = []
        async def capture(message): sent.append(message)
        client._send = capture
        try:
            for sid in ('one', 'two', 'unknown'):
                await client._handle_request({'id': 1, 'method': 'session/request_permission', 'params': {
                    'sessionId': sid, 'toolCall': {'name': 'mcp_sentra_read_source', 'kind': 'other'},
                    'options': [{'optionId': 'allow', 'kind': 'allow_once'}]}})
            assert [x['result']['outcome']['outcome'] for x in sent] == ['selected', 'selected', 'cancelled']
            task = asyncio.create_task(client.request('session/prompt', {'sessionId': 'one'}))
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
            assert sent[-1]['method'] == 'session/cancel' and sent[-1]['params']['sessionId'] == 'one'
        finally:
            await client.close()
    asyncio.run(run())


def test_transport_deadline_remains_hard_during_notifications():
    async def run():
        client, _ = await make_client('notify', timeout=0.15, notification=lambda *_: None)
        async with client:
            with pytest.raises(ACPError, match='transport deadline exceeded'):
                await client.request('initialize', {})
    asyncio.run(run())


def test_permission_callback_receives_only_session_and_decision():
    async def run():
        seen = []
        client, _ = await make_client(permission_outcome=lambda sid, allowed: seen.append((sid, allowed)))
        async def capture(message): pass
        client._send = capture
        try:
            await client._handle_request({'id': 1, 'method': 'session/request_permission', 'params': {
                'sessionId': 's', 'toolCall': {'title': 'secret'}, 'options': []}})
            assert seen == [('s', False)]
        finally:
            await client.close()
    asyncio.run(run())


def test_model_limit_hints_are_specific():
    from app.acp_transport import native_model_limit_hint
    for text in ('quota exhausted', 'RESOURCE_EXHAUSTED', 'rate limit exceeded', 'exhausted your capacity', '429 too many requests'):
        assert native_model_limit_hint(text)
    for text in ('the quota is 100', 'this documents a rate limit', 'capacity planning'):
        assert not native_model_limit_hint(text)
