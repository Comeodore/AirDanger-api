import asyncio
import html as html_lib
import logging
import re
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import httpx

from .config import Config

logger = logging.getLogger(__name__)


MessageHandler = Callable[[str, str, datetime], Awaitable[None]]

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"

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

class ChannelPoller:
    def __init__(self, config: Config, on_message: MessageHandler) -> None:
        self._config = config
        self._on_message = on_message
        self._interval = config.poll_sec
        self._cursors: dict[str, int] = {}
        self._last_ok = 0.0
        self._client: httpx.AsyncClient | None = None
        self.last_message_at: dict[str, float] = {}

    @property
    def connected(self) -> bool:
        return time.time() - self._last_ok < max(60.0, self._interval * 3)

    async def run(self) -> None:
        channels = self._config.channels
        logger.info(
            "polling t.me/s every %ss, channels: %s",
            self._interval, ", ".join(channels),
        )
        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT}, timeout=8.0, follow_redirects=True,
        ) as client:
            self._client = client

            async with asyncio.TaskGroup() as tg:
                for i, channel in enumerate(channels):
                    tg.create_task(
                        self._channel_loop(channel, self._interval * i / len(channels))
                    )

    async def _channel_loop(self, channel: str, phase: float) -> None:
        await asyncio.sleep(phase)
        while True:
            started = time.monotonic()
            await self._poll_channel(channel)
            elapsed = time.monotonic() - started
            await asyncio.sleep(max(0.5, self._interval - elapsed))

    async def _poll_channel(self, channel: str) -> None:
        try:
            response = await self._client.get(f"https://t.me/s/{channel}")
            response.raise_for_status()
            messages = parse_messages(response.text)
            if not messages:
                return
            self._last_ok = time.time()
            cursor = self._cursors.get(channel)
            newest = messages[-1][0]
            if cursor is None:
                self._cursors[channel] = newest
                return
            for msg_id, text, ts in messages:
                if msg_id <= cursor:
                    continue
                self.last_message_at[channel] = time.time()
                await self._on_message(channel, text, ts or datetime.now(UTC))
            self._cursors[channel] = newest
        except httpx.HTTPError as exc:
            logger.warning("poll failed for %s: %r", channel, exc)
        except Exception:
            logger.warning("poll failed for %s", channel, exc_info=True)
