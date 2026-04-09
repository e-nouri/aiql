import ipaddress
import re
import socket
from urllib.parse import urlparse

MAX_URL_LENGTH = 2048

SENSITIVE_PARAMS = {
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "auth", "credential", "access_token", "private_key", "session",
}

BASE64_RE = re.compile(r"[A-Za-z0-9+/=]{40,}")

PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fd00::/8"),
]


def sanitize_url(url: str) -> str | None:
    """Validate URL for security. Returns rejection reason or None if clean."""

    parsed = urlparse(url)

    # 1. Scheme
    if parsed.scheme not in ("http", "https"):
        return f"scheme '{parsed.scheme}' not allowed"

    # 2. Length
    if len(url) > MAX_URL_LENGTH:
        return f"URL exceeds {MAX_URL_LENGTH} chars"

    # 3. Non-standard ports
    if parsed.port and parsed.port not in (80, 443):
        return f"non-standard port {parsed.port}"

    # 4. Credentials in URL
    if parsed.username or parsed.password:
        return "credentials in URL"

    # 5. Sensitive query params
    if parsed.query:
        params = parsed.query.lower().split("&")
        for param in params:
            key = param.split("=")[0]
            if key in SENSITIVE_PARAMS:
                return f"sensitive param '{key}' in URL"

    # 6. Base64 in path or query
    if BASE64_RE.search(parsed.path) or BASE64_RE.search(parsed.query or ""):
        return "base64-encoded content in URL"

    # 7. DNS resolution
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        return f"hostname '{parsed.hostname}' does not resolve"

    # 8. Private IP block
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        for network in PRIVATE_NETWORKS:
            if ip in network:
                return f"resolves to private IP {ip}"

    return None
