import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from aioapns import APNs, NotificationRequest, PushType
from aioapns.common import DynamicBoundedSemaphore
from aioapns.connection import APNsBaseClientProtocol, ChannelPool, H2Protocol

from .config import Config
from .danger_service import DetectedThreat
from .timefmt import iso_kyiv

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

TYPE_NAMES_UK = {
    "irbm": "МБР",
    "ballistic": "Балістика",
}


CONFIRMED_DEAD_REASON = "Unregistered"

MISMATCH_REASONS = {"BadDeviceToken", "DeviceTokenNotForTopic"}

UNUSABLE_TOKEN_REASONS = MISMATCH_REASONS | {CONFIRMED_DEAD_REASON}

ALERT_SOUND = "alert.caf"
SILENT_SOUND = "silent.caf"

SOUND_CHOICES = ("alert.caf", "siren.caf", "pulse.caf", "klaxon.caf")

TITLE_LIMIT = 110
BODY_LIMIT = 178

BACKGROUND_PRIORITY = 5

SILENT_PAYLOAD = {"aps": {"content-available": 1}}

PROBE_CONCURRENCY = 4

INBOUND_TTL_SEC = 60
WARNING_TTL_SEC = 300
ALL_CLEAR_TTL_SEC = 300
PROBE_TTL_SEC = 0

LA_ATTRIBUTES_TYPE = "ThreatActivityAttributes"
LA_TOPIC_SUFFIX = ".push-type.liveactivity"
LA_TTL_SEC = 300
LA_STALE_SEC = 1800

IDLE_CLOSE_SEC = 600.0
KEEPALIVE_SEC = 45.0
PING_PAYLOAD = b"airdangr"


@dataclass
class SendOutcome:
    token: str
    ok: bool
    reason: str | None = None


SENTENCE_END = re.compile(r"(.+?[.!?…])(?:\s|$)")


def first_sentence(text: str) -> str:
    flat = " ".join(text.split())
    match = SENTENCE_END.match(flat)
    if match:
        flat = match.group(1)
    return flat.rstrip(".")


def push_alert(text: str, fallback: str) -> dict:
    lines = [" ".join(line.split()) for line in text.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return {"title": fallback}
    title = lines[0]
    if len(title) > TITLE_LIMIT:
        title = title[:TITLE_LIMIT - 1] + "…"
    alert = {"title": title}
    if len(lines) > 1:
        alert["body"] = " ".join(lines[1:])[:BODY_LIMIT]
    return alert

class PushService:
    def __init__(
        self,
        config: Config,
        on_dead_token: Callable[[str], Awaitable[None]],
        concurrency: int = 50,
        probe_concurrency: int = PROBE_CONCURRENCY,
    ) -> None:
        self._config = config
        self._on_dead_token = on_dead_token
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
        type_name = TYPE_NAMES_UK.get(threat.type, "Небезпека")
        alert = push_alert(threat.text, fallback=f"{type_name} — Київ")
        payload = {
            "kind": "detection",
            "type": threat.type,
            "severity": threat.severity,
            "source": source,
            "text": threat.text,
            "ts": iso_kyiv(ts),
        }
        if threat.severity == "warning":
            tokens = [d["token"] for d in devices if d.get("warnings", True)]
            aps = {
                "alert": alert,
                "sound": SILENT_SOUND,
                "interruption-level": "time-sensitive",
            }
            return await self._fan_out(
                tokens, {"aps": aps, **payload}, priority=10, ttl=WARNING_TTL_SEC,
            )

        groups: dict[str, list[str]] = {}
        for device in devices:
            groups.setdefault(device.get("sound") or ALERT_SOUND, []).append(device["token"])

        async def send_group(sound_name: str, tokens: list[str]) -> int:
            if self._config.critical_alerts:
                sound: dict | str = {"critical": 1, "name": sound_name, "volume": 1.0}
            else:
                sound = sound_name
            aps = {
                "alert": alert,
                "sound": sound,
                "interruption-level": "time-sensitive",
            }
            return await self._fan_out(
                tokens, {"aps": aps, **payload}, priority=10, ttl=INBOUND_TTL_SEC,
            )

        results = await asyncio.gather(
            *(send_group(sound, tokens) for sound, tokens in groups.items())
        )
        return sum(results)

    async def send_all_clear(
        self, devices: list[dict], text: str, ts: datetime,
        source: str | None = None,
    ) -> int:
        payload = {
            "aps": {"alert": push_alert(text, fallback="Відбій — Київ")},
            "kind": "all_clear",
            "type": "all_clear",
            "severity": "clear",
            "source": source,
            "text": text,
            "ts": iso_kyiv(ts),
        }
        tokens = [d["token"] for d in devices]
        return await self._fan_out(tokens, payload, priority=10, ttl=ALL_CLEAR_TTL_SEC)

    async def send_live_activity(
        self, tokens: list[str], event: str, content_state: dict, ts: datetime,
        attributes: dict | None = None, dismissal_at: datetime | None = None,
    ) -> int:
        aps: dict = {
            "timestamp": int(ts.timestamp()),
            "event": event,
            "content-state": content_state,
            "stale-date": int(ts.timestamp()) + LA_STALE_SEC,
        }
        if event == "start":
            aps["attributes-type"] = LA_ATTRIBUTES_TYPE
            aps["attributes"] = attributes or {}
        if dismissal_at is not None:
            aps["dismissal-date"] = int(dismissal_at.timestamp())
        return await self._fan_out(
            tokens, {"aps": aps}, priority=10, ttl=LA_TTL_SEC,
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
