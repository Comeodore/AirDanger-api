import asyncio
import html as html_lib
import logging
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import httpx

from .config import Config

logger = logging.getLogger(__name__)


MessageHandler = Callable[[str, str, datetime], Awaitable[None]]

SEEN_LIMIT = 200
BLIND_WARN_SEC = 60.0

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"

_POST_RE = re.compile(r'data-post="[^"/]+/(\d+)"')
_TEXT_RE = re.compile(
    r'class="tgme_widget_message_text js-message_text[^"]*"[^>]*>(.*?)</div>', re.S
)
_TIME_RE = re.compile(r'<time datetime="([^"]+)"')
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")


async def stop(task: asyncio.Task) -> None:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

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

class Ingest:
    def __init__(self, config: Config, on_message: MessageHandler) -> None:
        self._config = config
        self._on_message = on_message
        self._seen: dict[str, deque[int]] = {}
        self._lock = asyncio.Lock()
        self._last_ok = 0.0
        self._blind_since: float | None = None
        self._warned_blind = False
        self.last_message_at: dict[str, float] = {}

    @property
    def source(self) -> str | None:
        return "preview" if self.connected else None

    @property
    def connected(self) -> bool:
        return time.time() - self._last_ok < self._config.health_window_sec

    def seed(self, channel: str, ids: list[int]) -> bool:
        if channel in self._seen:
            return False
        self._seen[channel] = deque(ids, maxlen=SEEN_LIMIT)
        return True

    async def run(self) -> None:
        channels = self._config.channels
        interval = self._config.poll_sec
        logger.info("polling t.me/s every %.1fs: %s", interval, ", ".join(channels))
        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT}, timeout=8.0, follow_redirects=True,
        ) as client:
            async with asyncio.TaskGroup() as tg:
                for i, channel in enumerate(channels):
                    tg.create_task(
                        self._channel_loop(client, channel, interval * i / len(channels))
                    )

    async def _channel_loop(
        self, client: httpx.AsyncClient, channel: str, phase: float,
    ) -> None:
        await asyncio.sleep(phase)
        while True:
            started = time.monotonic()
            await self._poll(client, channel)
            elapsed = time.monotonic() - started
            interval = self._config.poll_sec
            await asyncio.sleep(max(interval * 0.5, interval - elapsed))

    async def _poll(self, client: httpx.AsyncClient, channel: str) -> None:
        try:
            response = await client.get(f"https://t.me/s/{channel}")
            response.raise_for_status()
            messages = parse_messages(response.text)
            if not messages:
                self._note_blind(channel, len(response.text))
                return
            self._see()
            ids = [msg_id for msg_id, _, _ in messages]
            if self.seed(channel, ids):
                logger.info("watching %s from message %d", channel, max(ids))
                return
            for msg_id, text, ts in messages:
                await self.deliver(channel, msg_id, text, ts or datetime.now(UTC))
        except httpx.HTTPError as exc:
            logger.warning("poll failed for %s: %r", channel, exc)
        except Exception:
            logger.warning("poll failed for %s", channel, exc_info=True)

    def _see(self) -> None:
        self._last_ok = time.time()
        self._blind_since = None
        self._warned_blind = False

    def _note_blind(self, channel: str, page_bytes: int) -> None:
        now = time.time()
        if self._blind_since is None:
            self._blind_since = now
            return
        if not self._warned_blind and now - self._blind_since >= BLIND_WARN_SEC:
            self._warned_blind = True
            logger.error(
                "%s: t.me/s answered %d bytes but no messages parsed for %.0fs — "
                "the page markup has probably changed",
                channel, page_bytes, now - self._blind_since,
            )

    async def deliver(self, channel: str, msg_id: int, text: str, ts: datetime) -> None:
        async with self._lock:
            seen = self._seen.setdefault(channel, deque(maxlen=SEEN_LIMIT))
            if msg_id in seen:
                return
            text = text.strip()
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            age = (datetime.now(UTC) - ts).total_seconds()
            if text:
                self._measure(channel, msg_id, ts, age)
            if not text or age > self._config.max_age_sec:
                if text:
                    logger.info("%s: message %d skipped, %.0fs old", channel, msg_id, age)
                seen.append(msg_id)
                return
            self.last_message_at[channel] = time.time()
            try:
                await self._on_message(channel, text, ts)
            except Exception:
                logger.exception("%s: message %d failed, leaving it to be retried",
                                 channel, msg_id)
                return
            seen.append(msg_id)

    def _measure(self, channel: str, msg_id: int, ts: datetime, lag: float) -> None:
        if not self._config.latency_log:
            return
        try:
            with open(self._config.latency_log, "a") as log:
                log.write(f"{datetime.now(UTC).isoformat()}\t{channel}\t{msg_id}\t"
                          f"{ts.isoformat()}\t{lag:.2f}\tpreview\n")
        except OSError:
            logger.warning("could not write %s", self._config.latency_log, exc_info=True)
