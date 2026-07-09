import re

VIMEO_URL_PATTERN = re.compile(
    r"^https?://(www\.)?(player\.)?vimeo\.com/(video/)?\d+(/[A-Za-z0-9]+)?(\?.*)?$"
)


def is_vimeo_url(url: str) -> bool:
    if not url:
        return False
    return bool(VIMEO_URL_PATTERN.match(url.strip()))


def parse_url_list(text: str) -> tuple[list[str], list[str]]:
    valid: list[str] = []
    invalid: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if is_vimeo_url(line):
            valid.append(line)
        else:
            invalid.append(line)
    return valid, invalid
