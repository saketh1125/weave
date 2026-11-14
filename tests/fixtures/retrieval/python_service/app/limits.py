from time import monotonic


def client_key(headers: dict, peer_address: str) -> str:
    """Use the direct peer address unless a trusted proxy set a client header."""
    return headers.get("x-client-address", peer_address)


def allow_request(key: str, windows: dict, now: float | None = None) -> bool:
    """Allow at most five requests per client during a sixty second window."""
    current = monotonic() if now is None else now
    start, count = windows.get(key, (current, 0))
    if current - start >= 60:
        start, count = current, 0
    if count >= 5:
        return False
    windows[key] = (start, count + 1)
    return True
