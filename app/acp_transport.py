"""Bounded JSON-RPC transport for an already launched ACP-compatible child.

The caller is responsible for isolation, process creation and group termination.
"""
import asyncio
import json
import inspect
import re
from collections.abc import Awaitable, Callable
from typing import Any


class ACPError(Exception):
    """A sanitized transport or remote RPC failure, optionally with an RPC code."""

    def __init__(self, message: str, *, code: int | None = None, category: str | None = None):
        super().__init__(message)
        self.code = code
        self.category = category


def remote_error_category(message: str) -> str:
    """Classify actionable failures without retaining provider-controlled text."""
    low = message.lower()
    categories = (
        ("rate_limited", ("rate limit", "rate_limit", "resource_exhausted", "quota exceeded", "too many requests")),
        ("credential_store_unavailable", ("keyring", "secret service", "dbus", "d-bus")),
        ("browser_unavailable", ("xdg-open", "browser", "headless")),
        ("filesystem_permission", ("permission denied", "read-only", "read only")),
        ("unsupported_auth_method", ("unsupported auth", "unknown auth", "not implemented", "not supported")),
        ("provider_connection_failed", ("proxy", "connect", "network", "dns")),
    )
    return next((category for category, terms in categories if any(term in low for term in terms)), "remote_error")


def native_model_limit_hint(text: str) -> bool:
    """Identify explicit model capacity messages without retaining their contents."""
    return bool(re.search(r"(?:quota\s+(?:has\s+been\s+|is\s+)?exhausted|resource[_ ]exhausted|rate[ -]limit(?:ed)?\s+(?:has\s+been\s+|is\s+)?exceeded|exhausted\s+(?:your\s+)?capacity|429\s+too\s+many\s+requests)", text, re.IGNORECASE))


class ACPClient:
    def __init__(self, process: asyncio.subprocess.Process, *, terminate: Callable[[], Awaitable[None]],
                 timeout: float = 30, max_line: int = 65536, max_stdout: int = 1048576,
                 max_roundtrips: int = 32, notification: Callable[[str, Any], None] | None = None,
                 server_request: Callable[[str, Any], Any] | None = None,
                 permission_outcome: Callable[[str, bool], None] | None = None,
                 allowed_tool_names: frozenset[str] = frozenset()):
        if process.stdin is None or process.stdout is None:
            raise ValueError("process requires piped stdin and stdout")
        if timeout <= 0 or min(max_line, max_stdout, max_roundtrips) <= 0:
            raise ValueError("limits must be positive")
        self.process, self.terminate = process, terminate
        self.deadline = asyncio.get_running_loop().time() + timeout
        self.max_line, self.max_stdout, self.max_roundtrips = max_line, max_stdout, max_roundtrips
        self.notification, self.server_request = notification, server_request
        self.permission_outcome = permission_outcome
        self.allowed_tool_names = frozenset(allowed_tool_names)
        if any(not name.startswith('mcp_sentra_') for name in self.allowed_tool_names):
            raise ValueError('only isolated Sentra MCP tools can be permitted')
        self.pending: dict[int, asyncio.Future] = {}
        self.next_id = 0
        self.stdout_bytes = 0
        self.server_requests = 0
        self._close_task = None
        self.session_id: str | None = None
        self.session_ids: set[str] = set()
        self.closed = False
        self.reader = asyncio.create_task(self._read())

    def _remaining(self) -> float:
        remaining = self.deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise ACPError("transport deadline exceeded")
        return remaining

    async def _send(self, message: dict) -> None:
        try:
            data = json.dumps(message, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8") + b"\n"
            if len(data) > self.max_line:
                raise ACPError("message exceeds limit")
            self.process.stdin.write(data)
            await asyncio.wait_for(self.process.stdin.drain(), self._remaining())
        except asyncio.TimeoutError:
            raise ACPError("transport deadline exceeded") from None
        except (OSError, ConnectionError, TypeError, ValueError):
            raise ACPError("transport write failed") from None

    async def request(self, method: str, params: dict) -> Any:
        if self.closed or self.reader.done():
            raise ACPError("transport closed")
        if self.next_id >= self.max_roundtrips:
            raise ACPError("roundtrip limit exceeded")
        self.next_id += 1
        request_id = self.next_id
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            result = await asyncio.wait_for(asyncio.shield(future), self._remaining())
            if method == "session/new" and isinstance(result, dict) and isinstance(result.get("sessionId"), str):
                self.session_id = result["sessionId"]
                self.session_ids.add(self.session_id)
            return result
        except asyncio.CancelledError:
            session_id = params.get("sessionId") if method == "session/prompt" else None
            if method == "session/prompt" and session_id is None and len(self.session_ids) == 1:
                session_id = self.session_id
            if session_id in self.session_ids and not self.closed:
                try:
                    await self._send({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": session_id}})
                except ACPError:
                    pass
            raise
        except asyncio.TimeoutError:
            raise ACPError("transport deadline exceeded") from None
        finally:
            self.pending.pop(request_id, None)
            if not future.done():
                future.cancel()

    async def _read(self) -> None:
        try:
            while True:
                try:
                    line = await asyncio.wait_for(self.process.stdout.readuntil(b"\n"), self._remaining())
                except asyncio.TimeoutError:
                    raise ACPError("transport deadline exceeded") from None
                except (asyncio.LimitOverrunError, asyncio.IncompleteReadError):
                    raise ACPError("invalid or incomplete response") from None
                self.stdout_bytes += len(line)
                if len(line) > self.max_line or self.stdout_bytes > self.max_stdout:
                    raise ACPError("response limit exceeded")
                try:
                    message = json.loads(line.decode("utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                except (UnicodeDecodeError, ValueError):
                    raise ACPError("invalid response") from None
                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                    raise ACPError("invalid response")
                if "method" in message:
                    if not set(message) <= {"jsonrpc", "id", "method", "params"}:
                        raise ACPError("invalid response")
                    method = message["method"]
                    if not isinstance(method, str) or ("params" in message and not isinstance(message["params"], (dict, list))):
                        raise ACPError("invalid response")
                    if "id" in message:
                        await self._handle_request(message)
                    elif self.notification:
                        self._callback(self.notification, method, message.get("params"))
                    continue
                if not set(message) <= {"jsonrpc", "id", "result", "error"}:
                    raise ACPError("invalid response")
                request_id = message.get("id")
                if type(request_id) is not int or request_id not in self.pending:
                    raise ACPError("unexpected response id")
                future = self.pending[request_id]
                if future.done() or ("result" in message) == ("error" in message):
                    raise ACPError("invalid response")
                if "error" in message:
                    error = message["error"]
                    if not isinstance(error, dict) or type(error.get("code")) is not int or not isinstance(error.get("message"), str):
                        raise ACPError("invalid response")
                    future.set_exception(ACPError("remote request failed", code=error["code"], category=remote_error_category(error["message"])))
                else:
                    future.set_result(message["result"])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            message = "transport deadline exceeded" if isinstance(exc, ACPError) and str(exc) == "transport deadline exceeded" else "transport response failed"
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(ACPError(message))

    @staticmethod
    def _callback(callback, method, params):
        result = callback(method, params)
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise ACPError("async callbacks are unsupported")
        return result

    async def _handle_request(self, message: dict) -> None:
        self.server_requests += 1
        if self.server_requests > self.max_roundtrips:
            raise ACPError("server request limit exceeded")
        request_id, method = message["id"], message["method"]
        if type(request_id) not in (int, str):
            raise ACPError("invalid request id")
        reply = {"jsonrpc": "2.0", "id": request_id}
        if method == "session/request_permission":
            reply["result"] = {"outcome": {"outcome": "cancelled"}}
            params = message.get('params')
            if isinstance(params, dict) and (params.get('sessionId') in self.session_ids or (not self.session_ids and params.get('sessionId') == self.session_id)):
                call, options = params.get('toolCall'), params.get('options')
                # Native ACP's machine tool name, never its model-authored title,
                # identifies our fixed, read-only MCP service's tools.
                if (isinstance(call, dict) and isinstance(call.get('name'), str) and call['name'] in self.allowed_tool_names
                        and call.get('kind') == 'other' and isinstance(options, list)):
                    for option in options[:10]:
                        if isinstance(option, dict) and option.get('kind') == 'allow_once' and option.get('optionId') == 'allow':
                            reply['result'] = {'outcome': {'outcome': 'selected', 'optionId': 'allow'}}
                            break
            if self.permission_outcome and isinstance(params, dict) and isinstance(params.get('sessionId'), str):
                self.permission_outcome(params['sessionId'], reply['result']['outcome']['outcome'] == 'selected')
        elif method.startswith("fs/") or method.startswith("terminal/") or self.server_request is None:
            reply["error"] = {"code": -32601, "message": "Method not found"}
        else:
            reply["result"] = self._callback(self.server_request, method, message.get("params"))
        await self._send(reply)

    async def _close(self) -> None:
        self.closed = True
        self.reader.cancel()
        try:
            await self.reader
        except asyncio.CancelledError:
            pass
        for future in self.pending.values():
            if not future.done():
                future.set_exception(ACPError("transport closed"))
        self.process.stdin.close()
        try:
            await asyncio.wait_for(self.terminate(), 5)
        except (Exception, asyncio.CancelledError):
            raise ACPError("process termination failed") from None

    async def close(self) -> None:
        """Run the trusted process-group terminator even if close is cancelled."""
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        try:
            await asyncio.shield(self._close_task)
        except asyncio.CancelledError:
            await asyncio.shield(self._close_task)
            raise

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.close()
