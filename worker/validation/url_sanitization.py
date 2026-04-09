import ipaddress
import re
import socket
from urllib.parse import urlparse

MAX_URL_LENGTH = 2048

SUSPICIOUS_PARAMS = {
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
        return f"suspicious: scheme '{parsed.scheme}' not allowed"

    # 2. Length
    if len(url) > MAX_URL_LENGTH:
        return f"suspicious: URL exceeds {MAX_URL_LENGTH} chars"

    # 3. Non-standard ports
    if parsed.port and parsed.port not in (80, 443):
        return f"suspicious: non-standard port {parsed.port}"

    # 4. Credentials in URL
    if parsed.username or parsed.password:
        return "suspicious: credentials in URL"

    # 4. Suspicious query params
    if parsed.query:
        params = parsed.query.lower().split("&")
        for param in params:
            key = param.split("=")[0]
            if key in SUSPICIOUS_PARAMS:
                return f"suspicious: sensitive param '{key}' in URL"

    # 5. Base64 in path or query
    if BASE64_RE.search(parsed.path) or BASE64_RE.search(parsed.query or ""):
        return "suspicious: base64-encoded content in URL"

    # 6. DNS resolution
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        return f"suspicious: hostname '{parsed.hostname}' does not resolve"

    # 7. Private IP block
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        for network in PRIVATE_NETWORKS:
            if ip in network:
                return f"suspicious: resolves to private IP {ip}"

    return None
