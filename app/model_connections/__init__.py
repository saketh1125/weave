"""Validated, credential-reference-only model connection registry and transport."""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import math
import os
import re
import socket
import weakref
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx
import httpcore
import jsonschema

WORKFLOWS = frozenset({'planner', 'reviewer', 'draft', 'qa', 'summary', 'drift', 'pr_summary', 'pr_qa'})

SAFE_PROVIDER_CODES = frozenset({'tier_not_allowed', 'model_not_found', 'invalid_model', 'invalid_api_key', 'authentication_error', 'context_length_exceeded'})

class ProviderError(Exception):
    def __init__(self, reason, retry_after_seconds=None, *, request_sent=True, status_code=None, provider_code=None):
        super().__init__(reason)
        self.reason = reason
        self.retry_after_seconds = retry_after_seconds
        self.request_sent = request_sent
        self.status_code = status_code
        self.provider_code = provider_code if isinstance(provider_code, str) and provider_code in SAFE_PROVIDER_CODES else None

    @property
    def retryable(self):
        return self.reason in {'rate_limited', 'timeout', 'provider_unavailable', 'provider_connection_failed'} or (
            self.reason == 'provider_error' and (self.status_code is None or self.status_code >= 500))

    def diagnostic(self):
        result = {'reason': self.reason}
        if type(self.status_code) is int and 100 <= self.status_code <= 599:
            result['status_code'] = self.status_code
        if self.provider_code:
            result['provider_code'] = self.provider_code
        return result

@dataclass(frozen=True)
class Connection:
    name: str
    adapter: str
    model: str
    endpoint: str
    credential_env: str = field(repr=False)
    quota_group: str = ''
    rpm: int = 50
    tpm: int = 20000
    interval: float = 1.25
    concurrency: int = 1
    context_tokens: int = 20000
    max_output_tokens: int = 4096
    timeout_seconds: float = 60
    max_attempts: int = 2
    capabilities: tuple = ()
    allow_private: bool = False
    reasoning_effort: str = 'auto'
    runtime_identity: str = ''
    @property
    def identity(self):
        return fingerprint(self)

def fingerprint(connection):
    # The reference name identifies an account without exposing the credential.
    details = asdict(connection)
    if not details['runtime_identity']:
        del details['runtime_identity']
    value = json.dumps({'connection': details, 'protocol_version': 1}, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(value.encode()).hexdigest()

def _local_schema_references(value):
    if isinstance(value, dict):
        return all((key not in ('$ref', '$dynamicRef', '$id') or (isinstance(item, str) and item.startswith('#'))) and _local_schema_references(item) for key, item in value.items())
    if isinstance(value, list):
        return all(_local_schema_references(item) for item in value)
    return True

def _invalid():
    raise ProviderError('invalid_configuration', request_sent=False)

def _registry():
    model = os.environ.get('SENTRA_DOCUMENTATION_MODEL') or 'mistral-large-4'
    defaults = {
        'mistral_primary': {'adapter': 'mistral', 'model': model, 'endpoint': 'https://api.mistral.ai/v1', 'credential_env': 'MISTRAL_API_KEY', 'quota_group': 'mistral:1', 'capabilities': ['tools', 'json_object']},
        'mistral_secondary': {'adapter': 'mistral', 'model': model, 'endpoint': 'https://api.mistral.ai/v1', 'credential_env': 'MISTRAL_API_KEY2', 'quota_group': 'mistral:2', 'capabilities': ['tools', 'json_object']},
    }
    try:
        raw = os.environ.get('SENTRA_MODEL_CONNECTIONS')
        if raw:
            supplied = json.loads(raw)
            if not isinstance(supplied, dict): _invalid()
            defaults.update(supplied)
        result = {}
        allowed = set(Connection.__dataclass_fields__) - {'name', 'runtime_identity'}
        for name, cfg in defaults.items():
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', name) or not isinstance(cfg, dict) or set(cfg) - allowed: _invalid()
            if any(k not in cfg for k in ('adapter', 'model', 'endpoint', 'credential_env')): _invalid()
            if cfg['adapter'] not in ('mistral', 'openai_compatible'): _invalid()
            if not all(isinstance(cfg[k], str) and cfg[k] for k in ('model', 'endpoint', 'credential_env')): _invalid()
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', cfg['credential_env']): _invalid()
            endpoint = cfg['endpoint']
            if any(ord(c) < 33 or ord(c) == 127 or c == '\\' for c in endpoint) or '%' in endpoint: _invalid()
            parsed = urlsplit(endpoint)
            _ = parsed.port  # Reject malformed and out-of-range ports.
            if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment: _invalid()
            if parsed.scheme == 'http' and cfg.get('allow_private') is not True: _invalid()
            if type(cfg.get('allow_private', False)) is not bool: _invalid()
            for k in ('rpm','tpm','concurrency','context_tokens','max_output_tokens','max_attempts'):
                if k in cfg and (type(cfg[k]) is not int or not 0 < cfg[k] <= 10000000): _invalid()
            if cfg.get('concurrency', 1) > 16 or cfg.get('max_attempts', 2) > 5: _invalid()
            effort = cfg.get('reasoning_effort', 'auto')
            if effort not in ('auto', 'low', 'medium', 'high') or (effort != 'auto' and (cfg['adapter'] != 'mistral' or cfg['model'] not in ('mistral-large-4', 'mistral-large-4-0'))): _invalid()
            for k in ('interval','timeout_seconds'):
                if k in cfg and (type(cfg[k]) not in (int,float) or not math.isfinite(cfg[k]) or cfg[k] < 0 or (k == 'timeout_seconds' and not 0 < cfg[k] <= 300)): _invalid()
            if 'quota_group' in cfg and (not isinstance(cfg['quota_group'], str) or not cfg['quota_group'] or len(cfg['quota_group']) > 128): _invalid()
            caps = cfg.get('capabilities', [])
            if not isinstance(caps, list) or any(c not in ('tools','json_object','json_schema') for c in caps) or len(caps) != len(set(caps)): _invalid()
            args = dict(cfg); args['capabilities'] = tuple(caps); args.setdefault('quota_group', name)
            result[name] = Connection(name=name, **args)
        limits = {}
        credentials = {}
        for conn in result.values():
            settings = (conn.rpm, conn.tpm, conn.interval, conn.concurrency)
            if conn.quota_group in limits and limits[conn.quota_group] != settings: _invalid()
            limits[conn.quota_group] = settings
            if conn.credential_env in credentials and credentials[conn.credential_env] != conn.quota_group: _invalid()
            credentials[conn.credential_env] = conn.quota_group
        workflows = {w: 'mistral_primary' for w in WORKFLOWS}
        workflows['reviewer'] = ['mistral_primary', 'mistral_secondary'] if os.environ.get('MISTRAL_API_KEY2') else ['mistral_primary']
        raw = os.environ.get('SENTRA_MODEL_WORKFLOWS')
        if raw:
            supplied = json.loads(raw)
            if not isinstance(supplied, dict) or set(supplied) - WORKFLOWS: _invalid()
            workflows.update(supplied)
        from app.model_preferences import get_overrides
        for workflow, name in get_overrides().items():
            if name == 'antigravity_local':
                continue  # Agent review dispatch is separate from direct APIs.
            if workflow not in WORKFLOWS or name not in result:
                _invalid()
            workflows[workflow] = name
        for workflow, value in workflows.items():
            if isinstance(value, str): names = [value]
            elif workflow == 'reviewer' and isinstance(value, list) and 1 <= len(value) <= 2: names = value
            else: _invalid()
            if any(not isinstance(n, str) or n not in result for n in names): _invalid()
        return result, workflows
    except (ValueError, TypeError, KeyError, OverflowError):
        _invalid()

def validate_configuration():
    """Validate registry and workflow mappings without resolving secrets or DNS."""
    _registry()

def native_connection(preferences=None):
    from app.model_preferences import get_preferences
    prefs = preferences if preferences is not None else get_preferences()
    if (not prefs.antigravity_model or prefs.antigravity_model != prefs.antigravity_verified_model
            or not isinstance(prefs.antigravity_identity, str) or not re.fullmatch(r'[0-9a-f]{64}', prefs.antigravity_identity)):
        raise ProviderError('connection_unavailable', request_sent=False)
    from app import antigravity_bridge
    if antigravity_bridge.configuration() is None:
        raise ProviderError('connection_unavailable', request_sent=False)
    return Connection(name='antigravity_local', adapter='antigravity_native', model=prefs.antigravity_model,
        endpoint='', credential_env='', quota_group='antigravity_local', rpm=0, tpm=0, interval=0,
        concurrency=1, context_tokens=100000, max_output_tokens=8192, timeout_seconds=120,
        max_attempts=1, capabilities=('tools', 'json_object', 'json_schema'), runtime_identity=prefs.antigravity_identity)


def resolve(workflow, slot=1):
    from app.model_preferences import get_overrides
    if workflow not in WORKFLOWS or type(slot) is not int or slot not in (1,2): _invalid()
    if get_overrides().get(workflow) == 'antigravity_local':
        if slot != 1: raise ProviderError('connection_unavailable', request_sent=False)
        return native_connection()
    registry, workflows = _registry()
    names = workflows[workflow]
    names = [names] if isinstance(names, str) else names
    if slot > len(names):
        raise ProviderError('connection_unavailable', request_sent=False)
    return registry[names[slot-1]]

def _credential(connection):
    fallback = (connection.name == 'mistral_primary' and connection.adapter == 'mistral'
                and connection.endpoint == 'https://api.mistral.ai/v1'
                and connection.credential_env == 'MISTRAL_API_KEY')
    return os.environ.get(connection.credential_env) or (os.environ.get('SENTRA_MISTRAL_API_KEY') if fallback else None)

def configured(workflow):
    connection = resolve(workflow)
    return connection.adapter == 'antigravity_native' or bool(_credential(connection))

def _check_credential_groups(connection, key):
    registry, _ = _registry()
    endpoint = urlsplit(connection.endpoint)
    origin = (endpoint.scheme, endpoint.hostname, endpoint.port or (443 if endpoint.scheme == 'https' else 80))
    for other in registry.values():
        parsed = urlsplit(other.endpoint)
        other_origin = (parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80))
        if origin == other_origin and other.quota_group != connection.quota_group and _credential(other) == key:
            _invalid()

def _http_error(status, body):
    # Keep only recognized machine codes; provider text can echo credentials or source.
    try:
        data = json.loads(body)
        if isinstance(data, dict) and isinstance(data.get('error'), dict):
            data = data['error']
        code = next((data[k] for k in ('code', 'type') if data.get(k) in SAFE_PROVIDER_CODES), None) if isinstance(data, dict) else None
    except (ValueError, TypeError):
        code = None
    code = code if isinstance(code, str) and code in SAFE_PROVIDER_CODES else None
    if status == 401:
        reason = 'invalid_credentials'
    elif status == 403:
        reason = 'model_access_denied' if code == 'tier_not_allowed' else 'provider_access_denied'
    elif status == 404 and code in {'model_not_found', 'invalid_model'}:
        reason = 'invalid_model'
    elif status in (408, 504):
        reason = 'timeout'
    elif status >= 500:
        reason = 'provider_unavailable'
    else:
        reason = 'provider_invalid_request'
    return ProviderError(reason, status_code=status, provider_code=code)


def _retry_after(value):
    if not value: return None
    try:
        try: seconds = int(value)
        except ValueError:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None: date = date.replace(tzinfo=timezone.utc)
            seconds = math.ceil((date - datetime.now(timezone.utc)).total_seconds())
        return max(0, min(seconds, 3600))
    except (ValueError, TypeError, OverflowError): return None

_LOCAL_NETWORKS = tuple(ipaddress.ip_network(value) for value in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', 'fc00::/7'))

class _ValidatedBackend(httpcore.AsyncNetworkBackend):
    """Resolve and connect to the checked address; httpcore retains TLS SNI."""
    def __init__(self, allow_private, require_private=False):
        self.allow_private = allow_private
        self.require_private = require_private
        self.backend = httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
            addresses = list(dict.fromkeys(info[4][0] for info in infos))
            def permitted(address):
                ip = ipaddress.ip_address(address.split('%')[0])
                if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
                    return False
                local = ip.is_loopback or any(ip in network for network in _LOCAL_NETWORKS if network.version == ip.version)
                if ip.is_reserved and not local: return False
                if self.require_private: return local
                return ip.is_global or (self.allow_private and local)
            if not addresses or not all(permitted(address) for address in addresses):
                raise ProviderError('invalid_endpoint', request_sent=False)
        except (OSError, ValueError):
            raise ProviderError('invalid_endpoint', request_sent=False) from None
        for index, address in enumerate(addresses):
            try:
                return await self.backend.connect_tcp(address, port, timeout=timeout, local_address=local_address, socket_options=socket_options)
            except (httpcore.ConnectError, OSError):
                if index == len(addresses) - 1: raise

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise ProviderError('invalid_endpoint', request_sent=False)

    async def sleep(self, seconds):
        await self.backend.sleep(seconds)

def _transport(connection):
    transport = httpx.AsyncHTTPTransport(retries=0, trust_env=False)
    # Pin the resolved address while preserving the original hostname for TLS.
    transport._pool._network_backend = _ValidatedBackend(connection.allow_private, require_private=urlsplit(connection.endpoint).scheme == 'http')
    return transport

_semaphores = weakref.WeakKeyDictionary()

def _semaphore(connection):
    loop = asyncio.get_running_loop()
    groups = _semaphores.setdefault(loop, {})
    key = connection.quota_group
    if key not in groups: groups[key] = asyncio.Semaphore(connection.concurrency)
    return groups[key]

async def complete(messages, tools=(), *, connection=None, workflow='reviewer', slot=1, model=None, max_tokens=1024, temperature=None, response_format=None, force_assessment=False, timeout_seconds=None, stream=False, prompt_tokens_upper_bound=None):
    if stream is not False: raise ProviderError('unsupported_capability', request_sent=False)
    connection = connection or resolve(workflow, slot)
    if not isinstance(connection, Connection): _invalid()
    if type(max_tokens) is not int or not 0 < max_tokens <= connection.max_output_tokens: raise ProviderError('output_budget_exceeded', request_sent=False)
    timeout = connection.timeout_seconds if timeout_seconds is None else timeout_seconds
    if type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0 < timeout <= connection.timeout_seconds: _invalid()
    if not isinstance(messages, (list, tuple)) or not isinstance(tools, (list, tuple)): raise ProviderError('invalid_request', request_sent=False)
    try:
        raw_bound = len(json.dumps(messages, ensure_ascii=False).encode()) + len(json.dumps(tools, ensure_ascii=False).encode())
    except (TypeError, ValueError, OverflowError):
        raise ProviderError('invalid_request', request_sent=False) from None
    if prompt_tokens_upper_bound is not None and (type(prompt_tokens_upper_bound) is not int or not 0 < prompt_tokens_upper_bound <= 1_000_000):
        raise ProviderError('context_budget_exceeded', request_sent=False)
    if (raw_bound if prompt_tokens_upper_bound is None else prompt_tokens_upper_bound) + max_tokens > connection.context_tokens:
        raise ProviderError('context_budget_exceeded', request_sent=False)
    if connection.adapter == 'openai_compatible':
        if tools and 'tools' not in connection.capabilities: raise ProviderError('unsupported_capability', request_sent=False)
        if any(isinstance(m, dict) and any(k in m for k in ('reasoning', 'reasoning_content', 'thinking')) for m in messages): raise ProviderError('unsupported_capability', request_sent=False)
    if response_format is not None:
        if not isinstance(response_format, dict) or response_format.get('type') not in ('json_object','json_schema') or response_format['type'] not in connection.capabilities: raise ProviderError('unsupported_capability', request_sent=False)
    schema = None
    if response_format and response_format['type'] == 'json_schema':
        try:
            schema = response_format['json_schema']['schema']
            if not isinstance(schema, dict) or not _local_schema_references(schema): raise ValueError()
            jsonschema.Draft202012Validator.check_schema(schema)
        except (KeyError, TypeError, ValueError, jsonschema.exceptions.SchemaError):
            raise ProviderError('invalid_schema', request_sent=False) from None
    if connection.adapter == 'antigravity_native':
        if model is not None and model != connection.model:
            raise ProviderError('invalid_configuration', request_sent=False)
        if any(not isinstance(m, dict) or m.get('role') not in ('system', 'user', 'assistant', 'tool') or '_sentra_native' in m for m in messages):
            raise ProviderError('invalid_request', request_sent=False)
        functions = {}
        try:
            for tool in tools:
                function = tool['function']
                name, parameters = function['name'], function['parameters']
                if tool['type'] != 'function' or not isinstance(name, str) or not name or name in functions or not isinstance(parameters, dict) or not _local_schema_references(parameters): raise ValueError()
                jsonschema.Draft202012Validator.check_schema(parameters)
                functions[name] = parameters
        except (KeyError, TypeError, ValueError, jsonschema.exceptions.SchemaError):
            raise ProviderError('invalid_request', request_sent=False) from None
        from app import antigravity_bridge
        try:
            async with asyncio.timeout(timeout):
                async with _semaphore(connection):
                    report = await antigravity_bridge.generate(list(messages), list(tools), connection.model, connection.runtime_identity,
                        response_format=response_format, force_assessment=force_assessment, max_tokens=max_tokens,
                        timeout_seconds=max(60, math.ceil(timeout)))
        except TimeoutError:
            raise ProviderError('timeout') from None
        try:
            message = report['message']
            if not isinstance(message, dict) or message.get('role') != 'assistant' or set(message) - {'role', 'content', 'tool_calls'}: raise ValueError()
            content, calls = message.get('content'), message.get('tool_calls', [])
            if not isinstance(calls, list) or len(calls) > 1 or (content is not None and not isinstance(content, str)) or (not calls and not isinstance(content, str)): raise ValueError()
            if calls:
                call = calls[0]
                function = call['function']
                name = function['name']
                if call['type'] != 'function' or not isinstance(call['id'], str) or name not in functions: raise ValueError()
                args = json.loads(function['arguments'])
                if not isinstance(args, dict): raise ValueError()
                jsonschema.Draft202012Validator(functions[name]).validate(args)
                if force_assessment and 'record_assessment' in functions and name != 'record_assessment': raise ValueError()
            elif len(functions) == 1 or (force_assessment and 'record_assessment' in functions):
                raise ValueError()
            if response_format and not calls:
                parsed = json.loads(content)
                if response_format['type'] == 'json_object' and not isinstance(parsed, dict): raise ValueError()
                if schema is not None: jsonschema.Draft202012Validator(schema).validate(parsed)
        except (KeyError, TypeError, ValueError, jsonschema.exceptions.ValidationError):
            raise ProviderError('invalid_response') from None
        return message, {'native_prompt_calls': 1, 'input_tokens': None, 'output_tokens': None}
    key = _credential(connection)
    if not key: raise ProviderError('missing_credentials', request_sent=False)
    _check_credential_groups(connection, key)
    outgoing = []
    for message in messages:
        if isinstance(message, dict) and '_sentra_native' in message:
            envelope = message['_sentra_native']
            if (not isinstance(envelope, dict) or set(envelope) != {'adapter', 'version', 'message'}
                    or envelope['adapter'] != connection.adapter or envelope['version'] != 1
                    or connection.adapter != 'mistral' or not isinstance(envelope['message'], dict)):
                raise ProviderError('unsupported_capability', request_sent=False)
            outgoing.append(envelope['message'])
        else:
            outgoing.append(message)
    payload = {'model': model or connection.model, 'messages': outgoing, 'max_tokens': max_tokens}
    if tools:
        payload['tools'] = tools
        choice = 'auto'
        if force_assessment and any(t.get('type') == 'function' and (t.get('function') or {}).get('name') == 'record_assessment' for t in tools): choice = {'type':'function','function':{'name':'record_assessment'}}
        elif len(tools) == 1 and tools[0].get('type') == 'function': choice = {'type':'function','function':{'name':tools[0]['function']['name']}}
        payload['tool_choice'] = choice
        if connection.adapter == 'mistral': payload['parallel_tool_calls'] = False
    if connection.adapter == 'mistral':
        if connection.reasoning_effort != 'auto' and payload['model'] not in ('mistral-large-4','mistral-large-4-0'):
            raise ProviderError('unsupported_capability', request_sent=False)
        if payload['model'] in ('mistral-large-4','mistral-large-4-0'):
            payload['reasoning_effort'] = connection.reasoning_effort if connection.reasoning_effort != 'auto' else ('low' if len(tools) == 1 else 'medium')
    if temperature is not None: payload['temperature'] = temperature
    if response_format is not None: payload['response_format'] = response_format
    try:
        async with asyncio.timeout(timeout):
            async with _semaphore(connection):
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False, transport=_transport(connection)) as client:
                    async with client.stream('POST', connection.endpoint.rstrip('/') + '/chat/completions', headers={'Authorization': f'Bearer {key}'}, json=payload) as response:
                        if response.status_code == 429: raise ProviderError('rate_limited', _retry_after(response.headers.get('Retry-After')), status_code=429)
                        body = bytearray()
                        limit = 16000 if response.status_code >= 300 else 2_000_000
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > limit:
                                if response.status_code >= 300:
                                    body = body[:limit]
                                    break
                                raise ProviderError('invalid_response')

        if response.status_code == 429: raise ProviderError('rate_limited', _retry_after(response.headers.get('Retry-After')), status_code=429)
        if response.status_code >= 300: raise _http_error(response.status_code, body)
        data = json.loads(body)
        if not isinstance(data, dict) or data.get('error'): raise ProviderError('provider_error')
        choice = data['choices'][0]; message = choice['message']; usage = data.get('usage') or {}
        if choice.get('finish_reason') == 'length': raise ProviderError('output_budget_exhausted')
        if not isinstance(message, dict) or not isinstance(usage, dict): raise ValueError()
        if message.get('tool_calls') is not None:
            calls = message['tool_calls']
            if not isinstance(calls, list) or any(not isinstance(call, dict) or not isinstance(call.get('id'), str) or call.get('type') != 'function' or not isinstance(call.get('function'), dict) or not isinstance(call['function'].get('name'), str) or not isinstance(call['function'].get('arguments'), str) for call in calls):
                raise ProviderError('invalid_response')
        usage = {k: v for k, v in usage.items() if k in ('prompt_tokens', 'completion_tokens', 'total_tokens', 'reasoning_tokens', 'cached_tokens') and type(v) is int and v >= 0}
        if connection.adapter == 'openai_compatible':
            if set(message) - {'role', 'content', 'tool_calls', 'function_call', 'refusal'} or isinstance(message.get('content'), list):
                raise ProviderError('unsupported_capability')
        if connection.adapter == 'mistral':
            native = message
            content = native.get('content')
            if isinstance(content, list):
                if any(not isinstance(part, dict) or not isinstance(part.get('type'), str) for part in content):
                    raise ProviderError('invalid_response')
                pieces = [part.get('text') for part in content if part['type'] in ('text', 'output_text')]
                if any(not isinstance(piece, str) for piece in pieces): raise ProviderError('invalid_response')
                content = ''.join(pieces)
            if content is not None and not isinstance(content, str): raise ProviderError('invalid_response')
            message = {k: native[k] for k in ('role', 'tool_calls', 'function_call', 'refusal') if k in native}
            message['content'] = content
            message['_sentra_native'] = {'adapter': 'mistral', 'version': 1, 'message': native}
        if response_format:
            try:
                parsed = json.loads(message['content'])
                if response_format['type'] == 'json_object' and not isinstance(parsed, dict): raise ValueError()
                if schema is not None: jsonschema.Draft202012Validator(schema).validate(parsed)
            except (KeyError, ValueError, TypeError, jsonschema.exceptions.ValidationError):
                raise ProviderError('invalid_response_schema') from None
        return message, usage
    except ProviderError: raise
    except (TimeoutError, httpx.TimeoutException): raise ProviderError('timeout') from None
    except httpx.HTTPError: raise ProviderError('provider_connection_failed') from None
    except (ValueError, KeyError, IndexError, TypeError): raise ProviderError('invalid_response') from None
