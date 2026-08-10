import asyncio
import html as html_lib
import logging
import random
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from .config import Config

logger = logging.getLogger(__name__)


MessageHandler = Callable[[str, str, datetime], Awaitable[None]]

SEEN_LIMIT = 200
BLIND_WARN_SEC = 60.0
CONNECT_TIMEOUT = 3.0
READ_TIMEOUT = 5.0
OUTAGE_WARN_SEC = 30.0
OUTAGE_ERROR_SEC = 180.0
OUTAGE_REMIND_SEC = 600.0
BACKOFF_AFTER_FAILURES = 4
BACKOFF_CEILING_SEC = 15.0

HOSTS = ("t.me", "telegram.me")

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

@dataclass
class ChannelHealth:
    failures: int = 0
    failing_since: float = 0.0
    outage_level: int = 0
    reported_at: float = 0.0
    blind_since: float | None = None
    warned_blind: bool = False
    warned_no_preview: bool = False


class Ingest:
    def __init__(self, config: Config, on_message: MessageHandler) -> None:
        self._config = config
        self._on_message = on_message
        self._seen: dict[str, deque[int]] = {}
        self._lock = asyncio.Lock()
        self._last_ok = 0.0
        self._health: dict[str, ChannelHealth] = {}
        self.last_message_at: dict[str, float] = {}

    @property
    def connected(self) -> bool:
        return time.time() - self._last_ok < self._config.health_window_sec

    def channel_state(self, channel: str) -> str:
        health = self._health.get(channel)
        if health is None:
            return "ok"
        if health.warned_no_preview:
            return "no_preview"
        if health.warned_blind:
            return "blind"
        if health.outage_level:
            return "unreachable"
        return "ok"

    def seed(self, channel: str, ids: list[int]) -> bool:
        if channel in self._seen:
            return False
        self._seen[channel] = deque(ids, maxlen=SEEN_LIMIT)
        return True

    async def run(self) -> None:
        channels = self._config.channels
        interval = self._config.poll_sec
        logger.info("polling t.me/s every %.1fs: %s", interval, ", ".join(channels))
        timeout = httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT)
        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT}, timeout=timeout, follow_redirects=True,
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
            delay = max(interval * 0.5, interval - elapsed)
            failures = self._health_of(channel).failures
            if failures > BACKOFF_AFTER_FAILURES:
                backoff = interval * 2 ** min(failures - BACKOFF_AFTER_FAILURES, 6)
                delay = max(delay, min(BACKOFF_CEILING_SEC, backoff))
            await asyncio.sleep(delay * random.uniform(0.9, 1.1))

    async def _poll(self, client: httpx.AsyncClient, channel: str) -> None:
        host = HOSTS[self._health_of(channel).failures % len(HOSTS)]
        try:
            response = await client.get(f"https://{host}/s/{channel}")
            response.raise_for_status()
            if "/s/" not in str(response.url):
                self._note_no_preview(channel, str(response.url))
                return
            messages = parse_messages(response.text)
            if not messages:
                self._note_blind(channel, len(response.text))
                return
            self._see(channel)
            ids = [msg_id for msg_id, _, _ in messages]
            if self.seed(channel, ids):
                logger.info("watching %s from message %d", channel, max(ids))
                return
            for msg_id, text, ts in messages:
                await self.deliver(channel, msg_id, text, ts or datetime.now(UTC))
        except httpx.HTTPError as exc:
            self._note_failure(channel, repr(exc))
        except Exception as exc:
            self._note_failure(channel, repr(exc))

    def _health_of(self, channel: str) -> ChannelHealth:
        health = self._health.get(channel)
        if health is None:
            health = ChannelHealth()
            self._health[channel] = health
        return health

    def _end_outage(self, channel: str, health: ChannelHealth) -> None:
        if not health.failures:
            return
        if health.outage_level:
            logger.info("%s: reachable again after %.0fs (%d polls failed)",
                        channel, time.time() - health.failing_since, health.failures)
        health.failures = 0
        health.outage_level = 0

    def _see(self, channel: str) -> None:
        health = self._health_of(channel)
        self._end_outage(channel, health)
        if health.warned_blind:
            logger.info("%s: messages parse again, markup is fine", channel)
        if health.warned_no_preview:
            logger.info("%s: the channel preview is back", channel)
        health.blind_since = None
        health.warned_blind = False
        health.warned_no_preview = False
        self._last_ok = time.time()

    def _note_no_preview(self, channel: str, url: str) -> None:
        health = self._health_of(channel)
        self._end_outage(channel, health)
        if not health.warned_no_preview:
            health.warned_no_preview = True
            logger.error("%s: t.me redirected to %s — the channel preview is "
                         "gone or revoked", channel, url)

    def _note_failure(self, channel: str, detail: str) -> None:
        health = self._health_of(channel)
        now = time.time()
        health.failures += 1
        if health.failures == 1:
            health.failing_since = now
        down = now - health.failing_since
        if health.outage_level == 0:
            if down >= OUTAGE_WARN_SEC:
                health.outage_level = 1
                health.reported_at = now
                logger.warning("%s: unreachable for %.0fs (%d polls failed), last: %s",
                               channel, down, health.failures, detail)
        elif health.outage_level == 1:
            if down >= OUTAGE_ERROR_SEC:
                health.outage_level = 2
                health.reported_at = now
                logger.error("%s: STILL unreachable for %.1f min (%d polls failed), last: %s",
                             channel, down / 60, health.failures, detail)
        elif now - health.reported_at >= OUTAGE_REMIND_SEC:
            health.reported_at = now
            logger.error("%s: STILL unreachable for %.1f min (%d polls failed), last: %s",
                         channel, down / 60, health.failures, detail)

    def _note_blind(self, channel: str, page_bytes: int) -> None:
        health = self._health_of(channel)
        self._end_outage(channel, health)
        now = time.time()
        if health.blind_since is None:
            health.blind_since = now
            return
        if not health.warned_blind and now - health.blind_since >= BLIND_WARN_SEC:
            health.warned_blind = True
            logger.error(
                "%s: t.me/s answered %d bytes but no messages parsed for %.0fs — "
                "the page markup has probably changed",
                channel, page_bytes, now - health.blind_since,
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
