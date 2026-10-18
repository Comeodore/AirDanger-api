import html as html_lib
import re
from datetime import datetime

_POST_RE = re.compile(r'data-post="[^"/]+/(\d+)"')
_TEXT_RE = re.compile(
    r'class="tgme_widget_message_text js-message_text[^"]*"[^>]*>(.*?)</div>', re.S
)
_TIME_RE = re.compile(r'<time datetime="([^"]+)"')
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(fragment: str) -> str:
    fragment = _BR_RE.sub("\n", fragment)
    return html_lib.unescape(_TAG_RE.sub("", fragment)).strip()

def parse_messages(page: str) -> list[tuple[int, str, datetime | None]]:
    messages: list[tuple[int, str, datetime | None]] = []
    posts = list(_POST_RE.finditer(page))
    for i, match in enumerate(posts):
        block = page[match.end(): posts[i + 1].start() if i + 1 < len(posts) else len(page)]
        text_match = _TEXT_RE.search(block)
        if text_match is None:
            continue
        text = strip_html(text_match.group(1))
        if not text:
            continue
        ts = None
        if time_match := _TIME_RE.search(block):
            try:
                ts = datetime.fromisoformat(time_match.group(1))
            except ValueError:
                ts = None
        messages.append((int(match.group(1)), text, ts))
    messages.sort(key=lambda m: m[0])
    return messages
