import ipaddress
from urllib.parse import urlparse


def _is_internal_literal_ip(hostname: str) -> bool:
    """True if hostname is a literal loopback/private/link-local/otherwise
    non-public IP address -- yt-dlp will happily fetch whatever URL it's
    handed, so without this a queued "download" could be pointed straight at
    internal-network services or the cloud metadata endpoint
    (169.254.169.254) instead of an actual video. Deliberately checks only
    literal IPs, not a DNS-resolved hostname: this runs synchronously in an
    async route handler for up to MAX_URLS_PER_BATCH URLs at once (see
    queue_routes.py), and a real DNS lookup here would be a blocking call
    that could stall the whole event loop -- a slow-to-resolve or
    deliberately blackholed hostname would freeze every other request/WS
    update for however long that lookup hangs. This closes the low-effort,
    high-likelihood version of the attack (a literal internal IP in the
    URL); a hostname that only resolves to an internal address via DNS
    rebinding is a materially harder attack to pull off and isn't covered."""
    try:
        return not ipaddress.ip_address(hostname).is_global
    except ValueError:
        return False  # not a literal IP at all -- an ordinary hostname


def is_supported_url(url: str) -> bool:
    if not url:
        return False
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False
    hostname = parsed.hostname
    if not hostname or _is_internal_literal_ip(hostname):
        return False
    return True


def parse_url_list(text: str) -> tuple[list[str], list[str]]:
    valid: list[str] = []
    invalid: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if is_supported_url(line):
            valid.append(line)
        else:
            invalid.append(line)
    return valid, invalid
