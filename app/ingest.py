import asyncio
import logging
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from .config import Config
from .tme import parse_messages, strip_html

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

TME = "tme"
BRIEF_LIMIT = 80

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"


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
        self._baselined: set[tuple[str, str]] = set()
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

    def baseline(self, source: str, channel: str, ids: list[int]) -> bool:
        if (source, channel) in self._baselined:
            return False
        self._baselined.add((source, channel))
        seen = self._seen.setdefault(channel, deque(maxlen=SEEN_LIMIT))
        for msg_id in sorted(ids):
            if msg_id not in seen:
                seen.append(msg_id)
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
            if self.baseline(TME, channel, ids):
                logger.info("watching %s via %s from message %d", channel, TME, max(ids))
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

    async def deliver(
        self, channel: str, msg_id: int, text: str, ts: datetime, source: str = TME,
    ) -> None:
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
            logger.info("%s: message %d via %s, %.1fs after post — %s",
                        channel, msg_id, source, age, " ".join(text.split())[:BRIEF_LIMIT])
            self.last_message_at[channel] = time.time()
            try:
                await self._on_message(channel, text, ts)
            except Exception:
                logger.exception("%s: message %d failed, leaving it to be retried",
                                 channel, msg_id)
                return
            seen.append(msg_id)
