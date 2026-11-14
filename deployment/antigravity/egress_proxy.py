"""Small HTTPS CONNECT allowlist proxy for the isolated ACP runner.

The proxy never terminates TLS or logs request bodies, headers, or credentials.
"""

import asyncio
import ipaddress
import re
import socket
import sys
from pathlib import Path
from urllib.parse import urlsplit

HOSTS_FILE = Path("/etc/agy-egress/hosts.txt")
LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 3128
HEADER_LIMIT = 8192
CONNECT_TIMEOUT_SECONDS = 10
MAX_TUNNEL_SECONDS = 900
MAX_CONNECTIONS = 32
CONNECTIONS = asyncio.Semaphore(MAX_CONNECTIONS)


def allowed_hosts() -> frozenset[str]:
    values = set()
    for line in HOSTS_FILE.read_text(encoding="ascii").splitlines():
        host = line.strip().lower()
        if host and not host.startswith("#"):
            values.add(host.rstrip("."))
    return frozenset(values)


def parse_connect_target(authority: str, allowlist: frozenset[str]) -> tuple[str, int]:
    """Accept only an exact allowlisted DNS name on TLS port 443."""
    if not authority or any(c in authority for c in "\r\n\t/@\\"):
        raise ValueError("invalid CONNECT authority")
    parsed = urlsplit("//" + authority)
    host = parsed.hostname
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid CONNECT port") from exc
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("userinfo is not accepted")
    if parsed.path or parsed.query or parsed.fragment or not host or port != 443:
        raise ValueError("only CONNECT host:443 is accepted")
    host = host.rstrip(".").lower()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("IP literal destinations are not accepted")
    try:
        ascii_host = host.encode("ascii").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("non-ASCII hostnames are not accepted") from exc
    if ascii_host not in allowlist:
        raise ValueError("host is not allowlisted")
    return ascii_host, port


def safe_denied_host(authority: str) -> str:
    """Return only a plain DNS hostname for diagnostics, never URL contents."""
    if not authority or any(c in authority for c in "/?#@\\\r\n\t"):
        return "invalid"
    try:
        parsed = urlsplit("//" + authority)
        if parsed.path or parsed.query or parsed.fragment:
            return "invalid"
        host = parsed.hostname or ""
        host = host.rstrip(".").lower()
    except ValueError:
        return "invalid"
    if len(host) <= 253 and re.fullmatch(r"[a-z0-9.-]+", host) and ".." not in host:
        return host
    return "invalid"


def public_addresses(records: list[tuple], host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Validate DNS answers and return IPs safe to connect to directly."""
    addresses = []
    for record in records:
        address = ipaddress.ip_address(record[4][0].split("%", 1)[0])
        if not address.is_global:
            raise ValueError("destination DNS answer is not globally routable")
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            raise ValueError("IPv4-mapped IPv6 destinations are not accepted")
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise ValueError("destination has no DNS answers")
    return addresses


async def resolve_target(host: str, port: int):
    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return public_addresses(records, host)


async def relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    while data := await reader.read(65536):
        writer.write(data)
        await writer.drain()
    try:
        writer.write_eof()
    except (AttributeError, OSError, RuntimeError):
        pass


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    upstream_writer = None
    try:
        async with CONNECTIONS:
            try:
                request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
            except (asyncio.TimeoutError, asyncio.LimitOverrunError, asyncio.IncompleteReadError):
                writer.write(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n")
                await writer.drain()
                return
            if len(request) > HEADER_LIMIT:
                writer.write(b"HTTP/1.1 431 Request Header Fields Too Large\r\nConnection: close\r\n\r\n")
                await writer.drain()
                return
            try:
                lines = request.decode("ascii").split("\r\n")
                method, authority, version = lines[0].split(" ")
                if method != "CONNECT" or version not in {"HTTP/1.0", "HTTP/1.1"}:
                    raise ValueError("CONNECT required")
                host, port = parse_connect_target(authority, allowed_hosts())
                addresses = await resolve_target(host, port)
            except (UnicodeError, ValueError, OSError, asyncio.TimeoutError):
                denied_host = safe_denied_host(authority if "authority" in locals() else "")
                print(f"blocked CONNECT host={denied_host}", file=sys.stderr, flush=True)
                writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
                await writer.drain()
                return

            last_error = None
            for address in addresses:
                try:
                    family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
                    upstream_reader, upstream_writer = await asyncio.wait_for(
                        asyncio.open_connection(str(address), port, family=family),
                        timeout=CONNECT_TIMEOUT_SECONDS,
                    )
                    break
                except (OSError, asyncio.TimeoutError) as exc:
                    last_error = exc
            if upstream_writer is None:
                writer.write(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
                await writer.drain()
                return

            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            try:
                async with asyncio.timeout(MAX_TUNNEL_SECONDS):
                    await asyncio.gather(
                        relay(reader, upstream_writer),
                        relay(upstream_reader, writer),
                    )
            except (TimeoutError, OSError, asyncio.CancelledError):
                pass
    except (ConnectionError, OSError):
        pass
    finally:
        if upstream_writer is not None:
            upstream_writer.close()
            try:
                await upstream_writer.wait_closed()
            except OSError:
                pass
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


async def main() -> None:
    server = await asyncio.start_server(
        handle, LISTEN_HOST, LISTEN_PORT, limit=HEADER_LIMIT + 1
    )
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
