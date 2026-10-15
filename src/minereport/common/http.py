"""HTTP helpers with SSRF guard, size limits and bounded redirects."""

from __future__ import annotations

import ipaddress
import socket
from fnmatch import fnmatch
from urllib.parse import urlparse

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)

MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_REDIRECTS = 3


class UrlNotAllowedError(ValueError):
    """Raised when a URL fails the SSRF policy."""


class ResponseTooLargeError(ValueError):
    """Raised when a download exceeds the configured byte budget."""


def _ip_is_blocked(ip: ipaddress._BaseAddress) -> bool:
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def assert_url_allowed(
    url: str,
    *,
    allow_schemes: tuple[str, ...] = ("http", "https"),
    allow_hosts: tuple[str, ...] | None = None,
) -> str:
    """Reject non-http schemes, private/loopback targets and off-allowlist hosts."""

    parsed = urlparse(url)
    if parsed.scheme.lower() not in allow_schemes:
        raise UrlNotAllowedError(
            f"scheme {parsed.scheme!r} not allowed; expected one of {allow_schemes}"
        )
    host = parsed.hostname
    if not host:
        raise UrlNotAllowedError(f"no host in url {url!r}")

    if allow_hosts and not any(fnmatch(host, pattern) for pattern in allow_hosts):
        raise UrlNotAllowedError(f"host {host!r} not in allow-list {allow_hosts}")

    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:  # pragma: no cover - network dependent
        raise UrlNotAllowedError(f"cannot resolve host {host!r}: {exc}") from exc

    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:  # pragma: no cover - defensive
            raise UrlNotAllowedError(f"unparsable resolved address {address!r}") from None
        if _ip_is_blocked(ip):
            raise UrlNotAllowedError(
                f"host {host!r} resolves to non-public address {address!r}; blocked by SSRF policy"
            )
    return host


def build_client(
    *,
    timeout: float = DEFAULT_TIMEOUT,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    headers: dict[str, str] | None = None,
) -> httpx.Client:
    merged = {"User-Agent": DEFAULT_UA, "Accept-Language": "en-US,en;q=0.9"}
    if headers:
        merged.update(headers)
    return httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        max_redirects=max_redirects,
        headers=merged,
    )


@retry(
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, max=6),
    reraise=True,
)
def fetch_bytes(
    url: str,
    *,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
    timeout: float = DEFAULT_TIMEOUT,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    allow_hosts: tuple[str, ...] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[bytes, str]:
    """Download at most ``max_bytes`` bytes and return ``(content, final_url)``."""

    assert_url_allowed(url, allow_hosts=allow_hosts)
    with build_client(timeout=timeout, max_redirects=max_redirects, headers=headers) as client:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            assert_url_allowed(str(response.url))
            declared = response.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise ResponseTooLargeError(
                    f"content-length {declared} exceeds limit {max_bytes} bytes"
                )
            buffer = bytearray()
            for chunk in response.iter_bytes():
                buffer += chunk
                if len(buffer) > max_bytes:
                    raise ResponseTooLargeError(f"download exceeded {max_bytes} bytes")
            return bytes(buffer), str(response.url)


@retry(
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, max=4),
    reraise=True,
)
def get_text(
    url: str,
    *,
    params: dict[str, str] | None = None,
    encoding: str | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    allow_hosts: tuple[str, ...] | None = None,
) -> str:
    assert_url_allowed(url, allow_hosts=allow_hosts)
    with build_client(timeout=timeout, max_redirects=max_redirects, headers=headers) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
        if encoding:
            response.encoding = encoding
        return response.text


def get_json(
    url: str,
    *,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    allow_hosts: tuple[str, ...] | None = None,
) -> object:
    text = get_text(url, params=params, headers=headers, timeout=timeout, allow_hosts=allow_hosts)
    import json

    return json.loads(text)

