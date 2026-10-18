import asyncio
import logging
import random
import time
from collections.abc import Callable

from telethon import TelegramClient, events, utils
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession

from .config import Config
from .ingest import Ingest

logger = logging.getLogger(__name__)

PULL = "pull"
UPDATE = "update"
PULL_LIMIT = 3
FAIL_REPORT_SEC = 60.0
RESTART_DELAY_SEC = 10.0


class NotAuthorized(Exception):
    pass


class MtprotoSource:
    def __init__(
        self, config: Config, ingest: Ingest,
        client_factory: Callable[[], TelegramClient] | None = None,
    ) -> None:
        self._config = config
        self._ingest = ingest
        self._client_factory = client_factory or self._make_client
        self._failing: dict[str, tuple[float, float]] = {}
        self.last_ok = 0.0

    def _make_client(self) -> TelegramClient:
        return TelegramClient(
            StringSession(self._config.tg_session),
            self._config.tg_api_id, self._config.tg_api_hash,
            auto_reconnect=True, connection_retries=None, retry_delay=2,
            flood_sleep_threshold=0,
        )

    @property
    def connected(self) -> bool:
        return time.time() - self.last_ok < self._config.health_window_sec

    async def run(self) -> None:
        while True:
            try:
                await self._session()
            except NotAuthorized:
                logger.error("mtproto session is not authorized — log in again; "
                             "polling t.me/s only")
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("mtproto stopped, restarting in %.0fs", RESTART_DELAY_SEC)
            await asyncio.sleep(RESTART_DELAY_SEC)

    async def _session(self) -> None:
        client = self._client_factory()
        await client.connect()
        try:
            if not await client.is_user_authorized():
                raise NotAuthorized
            me = await client.get_me()
            logger.info("mtproto signed in as @%s, pull every %.1fs plus updates",
                        me.username, self._config.mtproto_pull_sec)
            entities = {}
            for channel in self._config.channels:
                entities[channel] = await client.get_entity(channel)
                await self.pull(client, channel, entities[channel], baseline=True)
            by_peer = {utils.get_peer_id(entity): channel for channel, entity in entities.items()}

            async def on_update(event) -> None:
                channel = by_peer.get(event.chat_id)
                if channel is None:
                    return
                self.last_ok = time.time()
                message = event.message
                await self._ingest.deliver(
                    channel, message.id, message.message or "", message.date, source=UPDATE,
                )

            client.add_event_handler(on_update, events.NewMessage(chats=list(entities.values())))
            channels = self._config.channels
            async with asyncio.TaskGroup() as group:
                for i, channel in enumerate(channels):
                    phase = self._config.mtproto_pull_sec * i / len(channels)
                    group.create_task(self._pull_loop(client, channel, entities[channel], phase))
                group.create_task(client.run_until_disconnected())
        finally:
            await client.disconnect()

    async def pull(self, client, channel: str, entity, baseline: bool = False) -> None:
        messages = await client.get_messages(entity, limit=PULL_LIMIT)
        self.last_ok = time.time()
        if baseline:
            ids = [message.id for message in messages]
            if self._ingest.baseline(PULL, channel, ids):
                logger.info("watching %s via %s from message %d",
                            channel, PULL, max(ids, default=0))
            return
        for message in sorted(messages, key=lambda m: m.id):
            await self._ingest.deliver(
                channel, message.id, getattr(message, "message", None) or "",
                message.date, source=PULL,
            )

    async def _pull_loop(self, client, channel: str, entity, phase: float) -> None:
        await asyncio.sleep(phase)
        while True:
            started = time.monotonic()
            interval = self._config.mtproto_pull_sec
            try:
                await self.pull(client, channel, entity)
                self._recovered(channel)
            except FloodWaitError as exc:
                logger.warning("%s: mtproto FLOOD_WAIT %ds, pulls paused", channel, exc.seconds)
                await asyncio.sleep(exc.seconds + 1)
                continue
            except Exception as exc:
                self._failed(channel, exc)
            delay = max(interval * 0.5, interval - (time.monotonic() - started))
            await asyncio.sleep(delay * random.uniform(0.9, 1.1))

    def _failed(self, channel: str, exc: Exception) -> None:
        now = time.time()
        since, reported = self._failing.get(channel, (now, 0.0))
        if not reported or now - reported >= FAIL_REPORT_SEC:
            logger.warning("%s: mtproto pull failing for %.0fs: %r", channel, now - since, exc)
            reported = now
        self._failing[channel] = (since, reported)

    def _recovered(self, channel: str) -> None:
        failing = self._failing.pop(channel, None)
        if failing is not None:
            logger.info("%s: mtproto pull back after %.0fs", channel, time.time() - failing[0])
