"""One bounded MCP result mailbox for an isolated generation session."""
import json
import threading
from . import source_tools as base
from .workflow_tools import WorkflowHandler, schema


def shape(value):
    def kind(item):
        if item is None: return 'null'
        if isinstance(item, dict): return 'object'
        if isinstance(item, list): return 'array'
        if isinstance(item, str): return 'string'
        return 'other'
    result = {'type': kind(value)}
    if isinstance(value, dict):
        result.update(content_present='content' in value, calls_present='tool_calls' in value,
                      extra_keys=bool(set(value) - {'content', 'tool_calls'}))
        if 'content' in value: result['content_type'] = kind(value['content'])
        if 'tool_calls' in value:
            calls = value['tool_calls']
            result['calls_type'] = kind(calls)
            if isinstance(calls, list):
                result['calls_count'] = min(len(calls), 2)
                if calls:
                    call = calls[0]
                    result['call_type'] = kind(call)
                    if isinstance(call, dict):
                        result.update(name_present='name' in call, arguments_present='arguments' in call,
                                      call_extra_keys=bool(set(call) - {'name', 'arguments'}))
                        if 'name' in call: result['name_type'] = kind(call['name'])
                        if 'arguments' in call: result['arguments_type'] = kind(call['arguments'])
    return result


class Result:
    tools_schema = [schema('submit_result', 'Submit the final answer or proposed function calls.', {
        'content': {'type': ['string', 'null']},
        'tool_calls': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'name': {'type': 'string'}, 'arguments': {'type': 'object'}},
            'required': ['name', 'arguments'], 'additionalProperties': False}}}, ('content', 'tool_calls'))]

    def __init__(self):
        self.initialized = False
        self.value = None
        self.diagnostic = None
        self.lock = threading.Lock()

    def call_tool(self, name, args):
        if name != 'submit_result':
            return {'error': 'unknown_tool'}, True
        with self.lock:
            if self.value is not None:
                return {'error': 'already_submitted'}, True
            self.diagnostic = shape(args)
            try:
                if len(json.dumps(args, ensure_ascii=False, allow_nan=False).encode('utf-8')) > 32 * 1024:
                    return {'error': 'invalid_result'}, True
            except (TypeError, ValueError, UnicodeError):
                return {'error': 'invalid_result'}, True
            if (not isinstance(args, dict) or set(args) != {'content', 'tool_calls'}
                    or (args['content'] is not None and not isinstance(args['content'], str))
                    or not isinstance(args['tool_calls'], list) or len(args['tool_calls']) > 1
                    or any(not isinstance(c, dict) or set(c) != {'name', 'arguments'}
                           or not isinstance(c['name'], str) or not isinstance(c['arguments'], dict)
                           for c in args['tool_calls'])):
                return {'error': 'invalid_result'}, True
            self.value = args
            return {'accepted': True}, False


class Handler(WorkflowHandler):
    def _request_allowed(self):
        hosts = self.headers.get_all('Host') or []
        if len(hosts) != 1 or hosts[0].strip().lower() not in {'generation-tools:8080', 'localhost:8080', '127.0.0.1:8080'} or self.headers.get_all('Origin'):
            self._send(403, {'error': 'forbidden'})
            return False
        return True

    def do_GET(self):
        if not self._request_allowed():
            return
        if self.path != '/result':
            self._send(404, {'error': 'not_found'})
            return
        with self.server.result.lock:
            self._send(200, {'result': self.server.result.value, 'diagnostic': self.server.result.diagnostic})


class Server(base.SourceMCPServer):
    def __init__(self, address):
        self.result = Result()
        self.routes = {'/mcp': self.result}
        self._connection_slots = threading.BoundedSemaphore(32)
        base.ThreadingHTTPServer.__init__(self, address, Handler)


if __name__ == '__main__':
    server = Server(('0.0.0.0', 8080))
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
