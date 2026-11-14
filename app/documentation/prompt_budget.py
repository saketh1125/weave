"""Conservative prompt reservations based on a provider-reported prefix."""
import hashlib
import json

FRAMING_PER_MESSAGE = 128
MAX_REPORTED_TOKENS = 1_000_000
MAX_MESSAGES = 1000


def _bytes(value):
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8'))


def _hash(messages, tools, model):
    payload = json.dumps([model, tools, messages], ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def record(messages, tools, model, usage, assistant_message=None):
    count = len(messages)
    tokens = usage.get('prompt_tokens') if isinstance(usage, dict) else None
    if not 0 < count <= MAX_MESSAGES or type(tokens) is not int or not 0 < tokens <= MAX_REPORTED_TOKENS:
        return None
    completion = usage.get('completion_tokens') if isinstance(usage, dict) else None
    if assistant_message is not None and isinstance(assistant_message, dict) and type(completion) is int and 0 < completion <= MAX_REPORTED_TOKENS and tokens + completion + 256 <= MAX_REPORTED_TOKENS and count < MAX_MESSAGES:
        messages = [*messages, assistant_message]
        count += 1
        tokens += completion + 256
    return {'message_count': count, 'prefix_hash': _hash(messages, tools, model), 'prompt_tokens': tokens}


def upper_bound(messages, tools, model, accounting):
    if not isinstance(accounting, dict) or set(accounting) != {'message_count', 'prefix_hash', 'prompt_tokens'}:
        return None
    count, tokens, digest = accounting['message_count'], accounting['prompt_tokens'], accounting['prefix_hash']
    if type(count) is not int or not 0 < count <= min(len(messages), MAX_MESSAGES) or type(tokens) is not int or not 0 < tokens <= MAX_REPORTED_TOKENS or not isinstance(digest, str) or len(digest) != 64:
        return None
    if digest != _hash(messages[:count], tools, model):
        return None
    appended = messages[count:]
    return tokens + _bytes(appended) + FRAMING_PER_MESSAGE * len(appended)
