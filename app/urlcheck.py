"""URL validation with SSRF protection.

Only http and https are accepted. The host is resolved and every address is
checked against loopback, private, link-local, multicast, reserved and cloud
metadata ranges. The crawler calls this again for every redirect target.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

Resolver = Callable[[str], Awaitable[list[str]]]


class UnsafeURL(ValueError):
    """Raised when a URL must not be fetched."""


_BLOCKED_HOSTS = {"localhost", "metadata.google.internal", "metadata", "instance-data"}


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return bool(
        ip.is_private  # 10/8, 172.16/12, 192.168/16, fc00::/7 and more
        or ip.is_loopback
        or ip.is_link_local  # includes 169.254.169.254
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or (isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.ip_network("100.64.0.0/10"))
    )


async def default_resolver(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


def normalize_url(url: str) -> str:
    url = url.strip()
    if not url:
        raise UnsafeURL("Please enter a website address.")
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UnsafeURL("Only http and https addresses are allowed.")
    if not parts.hostname:
        raise UnsafeURL("That address has no host name.")
    if parts.username or parts.password:
        raise UnsafeURL("Addresses with credentials are not allowed.")
    path = parts.path or "/"
    return urlunsplit((parts.scheme, parts.netloc.lower(), path, parts.query, ""))


async def validate_url(url: str, resolver: Resolver | None = None) -> str:
    """Normalise and check a URL. Returns the normalised URL or raises UnsafeURL."""
    url = normalize_url(url)
    host = urlsplit(url).hostname or ""
    if host in _BLOCKED_HOSTS or host.endswith(".localhost") or host.endswith(".internal"):
        raise UnsafeURL("That address points to a private or local network.")

    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        if _is_blocked_ip(literal):
            raise UnsafeURL("That address points to a private or local network.")
        return url

    resolve = resolver or default_resolver
    try:
        addresses = await resolve(host)
    except Exception as exc:  # DNS failure
        raise UnsafeURL("That host name could not be resolved.") from exc
    if not addresses:
        raise UnsafeURL("That host name could not be resolved.")
    for addr in addresses:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            raise UnsafeURL("That host name resolved to an invalid address.")
        if _is_blocked_ip(ip):
            raise UnsafeURL("That address points to a private or local network.")
    return url
