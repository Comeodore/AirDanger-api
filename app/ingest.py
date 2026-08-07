import asyncio
import functools
import html as html_lib
import logging
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import httpx
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest

from .config import Config

logger = logging.getLogger(__name__)


MessageHandler = Callable[[str, str, datetime], Awaitable[None]]

CATCHUP_LIMIT = 20
RESTART_DELAY = 10.0
SEEN_LIMIT = 200
HEALTH_CHECK_SEC = 5.0

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

def build_client(config: Config) -> TelegramClient:
    return TelegramClient(
        StringSession(config.tg_session),
        config.tg_api_id,
        config.tg_api_hash,
        sequential_updates=True,
        connection_retries=5,
        retry_delay=2,
        auto_reconnect=True,
        request_retries=5,
        flood_sleep_threshold=120,
    )


class TelegramListener:
    def __init__(
        self,
        config: Config,
        sink: "Ingest",
        client_factory: Callable[[], TelegramClient] | None = None,
    ) -> None:
        self._config = config
        self._sink = sink
        self._client_factory = client_factory or (lambda: build_client(config))
        self._client: TelegramClient | None = None
        self._entities: dict[str, object] = {}
        self._last_ok = 0.0

    @property
    def connected(self) -> bool:
        if self._client is None or not self._client.is_connected():
            return False
        return time.time() - self._last_ok < max(120.0, self._config.catchup_sec * 3)

    async def run(self) -> None:
        while True:
            try:
                await self._session()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("mtproto failed, retrying in %.0fs", RESTART_DELAY)
            self._client = None
            await asyncio.sleep(RESTART_DELAY)

    async def _session(self) -> None:
        client = self._client_factory()
        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise RuntimeError("session is not authorised — run scripts/login.py")
            me = await client.get_me()
            logger.info("mtproto signed in as @%s (id %s)", me.username or me.phone, me.id)
            self._client = client
            await self._subscribe(client)
            catchup = asyncio.create_task(self._catchup_loop(client))
            try:
                await client.run_until_disconnected()
            finally:
                await stop(catchup)
        finally:
            self._client = None
            await client.disconnect()

    async def _subscribe(self, client: TelegramClient) -> None:
        for channel in self._config.channels:
            entity = await client.get_entity(channel)
            await client(JoinChannelRequest(entity))
            self._entities[channel] = entity
            messages = await client.get_messages(entity, limit=CATCHUP_LIMIT)
            client.add_event_handler(
                functools.partial(self._on_event, channel),
                events.NewMessage(chats=entity),
            )
            await self._ingest(channel, messages, "reconnect")
        self._last_ok = time.time()

    async def _catchup_loop(self, client: TelegramClient) -> None:
        while True:
            await asyncio.sleep(self._config.catchup_sec)
            try:
                for channel, entity in self._entities.items():
                    messages = await client.get_messages(entity, limit=CATCHUP_LIMIT)
                    await self._ingest(channel, messages, "catch-up")
                self._last_ok = time.time()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("mtproto catch-up failed", exc_info=True)

    async def _ingest(self, channel: str, messages: list, via: str) -> None:
        ids = [message.id for message in messages]
        if self._sink.seed(channel, ids):
            logger.info("mtproto watching %s from message %d", channel, max(ids, default=0))
            return
        for message in sorted(messages, key=lambda m: m.id):
            await self._sink.deliver(
                channel, message.id, message.message or "",
                message.date or datetime.now(UTC), via,
            )

    async def _on_event(self, channel: str, event) -> None:
        message = event.message
        self._last_ok = time.time()
        await self._sink.deliver(
            channel, message.id, message.message or "",
            message.date or datetime.now(UTC), "mtproto",
        )


class PreviewPoller:
    def __init__(self, config: Config, sink: "Ingest") -> None:
        self._config = config
        self._sink = sink
        self._last_ok = 0.0

    @property
    def connected(self) -> bool:
        return time.time() - self._last_ok < max(60.0, self._config.preview_poll_sec * 3)

    async def run(self) -> None:
        self._last_ok = 0.0
        channels = self._config.channels
        interval = self._config.preview_poll_sec
        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT}, timeout=8.0, follow_redirects=True,
        ) as client:
            async with asyncio.TaskGroup() as tg:
                for i, channel in enumerate(channels):
                    tg.create_task(
                        self._channel_loop(client, channel, interval * i / len(channels))
                    )

    async def _channel_loop(self, client: httpx.AsyncClient, channel: str, phase: float) -> None:
        await asyncio.sleep(phase)
        while True:
            started = time.monotonic()
            await self._poll(client, channel)
            elapsed = time.monotonic() - started
            await asyncio.sleep(max(0.5, self._config.preview_poll_sec - elapsed))

    async def _poll(self, client: httpx.AsyncClient, channel: str) -> None:
        try:
            response = await client.get(f"https://t.me/s/{channel}")
            response.raise_for_status()
            messages = parse_messages(response.text)
            if not messages:
                return
            self._last_ok = time.time()
            ids = [msg_id for msg_id, _, _ in messages]
            if self._sink.seed(channel, ids):
                logger.info("preview watching %s from message %d", channel, max(ids))
                return
            for msg_id, text, ts in messages:
                await self._sink.deliver(
                    channel, msg_id, text, ts or datetime.now(UTC), "preview",
                )
        except httpx.HTTPError as exc:
            logger.warning("preview poll failed for %s: %r", channel, exc)
        except Exception:
            logger.warning("preview poll failed for %s", channel, exc_info=True)


class Ingest:
    def __init__(
        self,
        config: Config,
        on_message: MessageHandler,
        client_factory: Callable[[], TelegramClient] | None = None,
    ) -> None:
        self._config = config
        self._on_message = on_message
        self._seen: dict[str, deque[int]] = {}
        self._lock = asyncio.Lock()
        self._poller_task: asyncio.Task | None = None
        self.last_message_at: dict[str, float] = {}
        self.listener = TelegramListener(config, self, client_factory)
        self.poller = PreviewPoller(config, self)

    @property
    def source(self) -> str | None:
        if self.listener.connected:
            return "mtproto"
        if self._poller_task is not None and self.poller.connected:
            return "preview"
        return None

    @property
    def connected(self) -> bool:
        return self.source is not None

    def seed(self, channel: str, ids: list[int]) -> bool:
        if channel in self._seen:
            return False
        self._seen[channel] = deque(ids, maxlen=SEEN_LIMIT)
        return True

    async def deliver(
        self, channel: str, msg_id: int, text: str, ts: datetime, via: str,
    ) -> None:
        async with self._lock:
            seen = self._seen.setdefault(channel, deque(maxlen=SEEN_LIMIT))
            if msg_id in seen:
                return
            seen.append(msg_id)
            if via != "mtproto":
                logger.info("%s: message %d recovered via %s", channel, msg_id, via)
            text = text.strip()
            if not text:
                return
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            age = (datetime.now(UTC) - ts).total_seconds()
            if age > self._config.catchup_max_age_sec:
                logger.info("%s: message %d skipped, %.0fs old", channel, msg_id, age)
                return
            self.last_message_at[channel] = time.time()
            await self._on_message(channel, text, ts)

    async def run(self) -> None:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(self.listener.run())
            tg.create_task(self._fallback_loop())

    async def _fallback_loop(self) -> None:
        down_since: float | None = None
        while True:
            await asyncio.sleep(HEALTH_CHECK_SEC)
            if self.listener.connected:
                down_since = None
                if self._poller_task is not None:
                    logger.warning("mtproto is back, stopping the t.me/s fallback")
                    await self._stop_poller()
                continue
            down_since = down_since or time.monotonic()
            if self._poller_task is not None:
                continue
            if time.monotonic() - down_since >= self._config.fallback_after_sec:
                logger.error("mtproto down for %.0fs, falling back to t.me/s polling",
                             self._config.fallback_after_sec)
                self._poller_task = asyncio.create_task(self.poller.run())

    async def _stop_poller(self) -> None:
        task, self._poller_task = self._poller_task, None
        await stop(task)
