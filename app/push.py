import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from aioapns import APNs, NotificationRequest, PushType
from aioapns.common import DynamicBoundedSemaphore
from aioapns.connection import APNsBaseClientProtocol, ChannelPool, H2Protocol

from .config import Config
from .danger_service import DetectedThreat
from .payloads import (
    ALL_CLEAR_TTL_SEC,
    BODY_LIMIT,
    INBOUND_TTL_SEC,
    SILENT_SOUND,
    SOUND_CHOICES,
    TYPE_NAMES_UK,
    WARNING_TTL_SEC,
    all_clear_batch,
    detection_batches,
    first_sentence,
)

logger = logging.getLogger(__name__)


class _DropPingAckNoise(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "PingAckReceived" not in record.getMessage()


class HonestChannelPool(ChannelPool):
    @property
    def bound(self) -> int:
        return self._bound_value

    @bound.setter
    def bound(self, value: int) -> None:
        in_flight = self._bound_value - self._value
        self._bound_value = value
        self._value = max(0, value - in_flight)


def install_honest_channel_pool() -> None:
    if getattr(H2Protocol, "_airdanger_honest_pool", False):
        return
    original_init = H2Protocol.__init__

    def __init__(self) -> None:
        original_init(self)
        self.free_channels = HonestChannelPool(1)

    H2Protocol.__init__ = __init__
    H2Protocol._airdanger_honest_pool = True

CONFIRMED_DEAD_REASON = "Unregistered"

MISMATCH_REASONS = {"BadDeviceToken", "DeviceTokenNotForTopic"}

UNUSABLE_TOKEN_REASONS = MISMATCH_REASONS | {CONFIRMED_DEAD_REASON}

BACKGROUND_PRIORITY = 5

SILENT_PAYLOAD = {"aps": {"content-available": 1}}

PROBE_CONCURRENCY = 4

PROBE_TTL_SEC = 0

LA_ATTRIBUTES_TYPE = "ThreatActivityAttributes"
LA_TOPIC_SUFFIX = ".push-type.liveactivity"
LA_TTL_SEC = 300
LA_END_TTL_SEC = 14400
LA_STALE_SEC = 1800

IDLE_CLOSE_SEC = 600.0
KEEPALIVE_SEC = 45.0
PING_PAYLOAD = b"airdangr"


@dataclass
class SendOutcome:
    token: str
    ok: bool
    reason: str | None = None


class PushService:
    def __init__(
        self,
        config: Config,
        on_dead_token: Callable[[str], Awaitable[None]],
        on_dead_la_token: Callable[[str], Awaitable[None]] | None = None,
        concurrency: int = 50,
        probe_concurrency: int = PROBE_CONCURRENCY,
    ) -> None:
        self._config = config
        self._on_dead_token = on_dead_token
        self._on_dead_la_token = on_dead_la_token
        self._semaphore = asyncio.Semaphore(concurrency)
        self._probe_semaphore = asyncio.Semaphore(probe_concurrency)
        self._apns: APNs | None = None
        install_honest_channel_pool()
        APNsBaseClientProtocol.INACTIVITY_TIME = IDLE_CLOSE_SEC
        logging.getLogger("aioapns").addFilter(_DropPingAckNoise())
        if config.apns_configured:
            self._apns = APNs(
                key=config.apns_key_path(),
                key_id=config.apns_key_id,
                team_id=config.apns_team_id,
                topic=config.apns_topic,
                use_sandbox=config.apns_sandbox,
            )
        else:
            logger.warning("APNs credentials missing — push disabled")

    async def send_detection(
        self, devices: list[dict], threat: DetectedThreat, ts: datetime,
        source: str | None = None,
    ) -> int:
        batches = detection_batches(
            devices, threat, ts, source, self._config.critical_alerts,
        )
        results = await asyncio.gather(
            *(self._fan_out(b.tokens, b.payload, priority=b.priority, ttl=b.ttl)
              for b in batches)
        )
        return sum(results)

    async def send_all_clear(
        self, devices: list[dict], text: str, ts: datetime,
        source: str | None = None,
    ) -> int:
        batch = all_clear_batch(devices, text, ts, source)
        return await self._fan_out(
            batch.tokens, batch.payload, priority=batch.priority, ttl=batch.ttl,
        )

    async def send_live_activity(
        self, tokens: list[str], event: str, content_state: dict, ts: datetime,
        attributes: dict | None = None, dismissal_at: datetime | None = None,
        priority: int = 10,
    ) -> int:
        aps: dict = {
            "timestamp": int(ts.timestamp()),
            "event": event,
            "content-state": content_state,
            "stale-date": int(ts.timestamp()) + LA_STALE_SEC,
        }
        started_at = content_state.get("startedAt")
        if started_at:
            aps["relevance-score"] = started_at
        if event == "start":
            aps["attributes-type"] = LA_ATTRIBUTES_TYPE
            aps["attributes"] = attributes or {}
            type_name = TYPE_NAMES_UK.get(content_state.get("type"), "Небезпека")
            title = f"{type_name} — Київ"
            body = " ".join(content_state.get("text", "").split())[:BODY_LIMIT]
            aps["alert"] = {
                "title": title,
                "body": body or title,
                "sound": SILENT_SOUND,
            }
        if dismissal_at is not None:
            aps["dismissal-date"] = int(dismissal_at.timestamp())
        return await self._fan_out(
            tokens, {"aps": aps}, priority=priority,
            ttl=LA_END_TTL_SEC if event == "end" else LA_TTL_SEC,
            push_type=PushType.LIVEACTIVITY,
            apns_topic=self._config.apns_topic + LA_TOPIC_SUFFIX,
        )

    async def token_is_usable(self, token: str) -> bool:
        if self._apns is None:
            return True
        outcome = await self._send_one(
            token, SILENT_PAYLOAD, BACKGROUND_PRIORITY, PushType.BACKGROUND,
            gate=self._probe_semaphore, ttl=PROBE_TTL_SEC,
        )
        return outcome.reason not in UNUSABLE_TOKEN_REASONS

    async def keep_warm(self) -> None:
        if self._apns is None:
            return
        warm: bool | None = None
        while True:
            ok = True
            try:
                await self._apns.pool.acquire()
            except Exception:
                ok = False
            if ok:
                for connection in list(self._apns.pool.connections):
                    try:
                        connection.conn.ping(PING_PAYLOAD)
                        connection.flush()
                    except Exception:
                        pass
            if ok != warm:
                if not ok:
                    logger.warning(
                        "no warm APNs connection — the next push pays a reconnect")
                elif warm is False:
                    logger.info("APNs connection is warm again")
                warm = ok
            await asyncio.sleep(KEEPALIVE_SEC)

    async def _fan_out(
        self, tokens: list[str], payload: dict, priority: int,
        ttl: int | None = None,
        push_type: PushType = PushType.ALERT,
        apns_topic: str | None = None,
    ) -> int:
        if self._apns is None or not tokens:
            return 0
        outcomes = await asyncio.gather(
            *(self._send_one(t, payload, priority, push_type,
                             ttl=ttl, apns_topic=apns_topic) for t in tokens)
        )
        if push_type is not PushType.LIVEACTIVITY:
            await self._retire_dead(outcomes)
        elif self._on_dead_la_token is not None:
            for outcome in outcomes:
                if outcome.reason in UNUSABLE_TOKEN_REASONS:
                    logger.info("la token %s… retired: %s",
                                outcome.token[:8], outcome.reason)
                    await self._on_dead_la_token(outcome.token)
        return sum(1 for outcome in outcomes if outcome.ok)

    async def _retire_dead(self, outcomes: list[SendOutcome]) -> None:
        mismatched = [o for o in outcomes if o.reason in MISMATCH_REASONS]
        if mismatched and len(mismatched) == len(outcomes):
            logger.error(
                "every push failed with %s — check APNS_SANDBOX and APNS_TOPIC; "
                "keeping all %d device(s)", mismatched[0].reason, len(outcomes),
            )
        elif mismatched:
            logger.warning(
                "keeping %d device(s) rejected as %s: %s",
                len(mismatched), mismatched[0].reason,
                ", ".join(f"{o.token[:8]}…" for o in mismatched),
            )
        for outcome in outcomes:
            if outcome.reason == CONFIRMED_DEAD_REASON:
                logger.info("device %s… removed: %s", outcome.token[:8], outcome.reason)
                await self._on_dead_token(outcome.token)

    async def _send_one(
        self, token: str, payload: dict, priority: int,
        push_type: PushType = PushType.ALERT,
        gate: asyncio.Semaphore | None = None,
        ttl: int | None = None,
        apns_topic: str | None = None,
    ) -> SendOutcome:
        try:
            async with gate or self._semaphore:
                request = NotificationRequest(
                    device_token=token,
                    message=payload,
                    push_type=push_type,
                    priority=priority,
                    time_to_live=ttl,
                    apns_topic=apns_topic,
                )
                response = await self._apns.send_notification(request)
            if not response.is_successful:
                logger.warning("push to %s… failed: %s", token[:8], response.description)
                return SendOutcome(token, False, response.description)
            return SendOutcome(token, True)
        except Exception:
            logger.exception("push to %s… crashed", token[:8])
            return SendOutcome(token, False)
