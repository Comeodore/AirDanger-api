import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from aioapns import APNs, NotificationRequest, PushType
from aioapns.common import DynamicBoundedSemaphore
from aioapns.connection import ChannelPool, H2Protocol

from .config import Config
from .danger_service import DetectedThreat
from .timefmt import iso_kyiv

logger = logging.getLogger(__name__)


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

TITLE_LIMIT = 110
BODY_LIMIT = 178

BACKGROUND_PRIORITY = 5

SILENT_PAYLOAD = {"aps": {"content-available": 1}}


@dataclass
class SendOutcome:
    token: str
    ok: bool
    reason: str | None = None


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
    ) -> None:
        self._config = config
        self._on_dead_token = on_dead_token
        self._semaphore = asyncio.Semaphore(concurrency)
        self._apns: APNs | None = None
        install_honest_channel_pool()
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
        self, tokens: list[str], threat: DetectedThreat, ts: datetime,
        source: str | None = None,
    ) -> int:
        type_name = TYPE_NAMES_UK.get(threat.type, "Небезпека")
        alert = push_alert(threat.text, fallback=f"{type_name} — Київ")
        if threat.severity == "warning":
            aps = {
                "alert": alert,
                "sound": ALERT_SOUND,
            }
        else:
            if self._config.critical_alerts:
                sound: dict | str = {"critical": 1, "name": ALERT_SOUND, "volume": 1.0}
            else:
                sound = ALERT_SOUND
            aps = {
                "alert": alert,
                "sound": sound,
                "interruption-level": "time-sensitive",
            }
        payload = {
            "aps": aps,
            "kind": "detection",
            "type": threat.type,
            "severity": threat.severity,
            "source": source,
            "text": threat.text,
            "ts": iso_kyiv(ts),
        }
        return await self._fan_out(tokens, payload, priority=10)

    async def token_is_usable(self, token: str) -> bool:
        if self._apns is None:
            return True
        outcome = await self._send_one(
            token, SILENT_PAYLOAD, BACKGROUND_PRIORITY, PushType.BACKGROUND,
        )
        return outcome.reason not in UNUSABLE_TOKEN_REASONS

    async def _fan_out(self, tokens: list[str], payload: dict, priority: int) -> int:
        if self._apns is None or not tokens:
            return 0
        outcomes = await asyncio.gather(
            *(self._send_one(t, payload, priority) for t in tokens)
        )
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
    ) -> SendOutcome:
        try:
            async with self._semaphore:
                request = NotificationRequest(
                    device_token=token,
                    message=payload,
                    push_type=push_type,
                    priority=priority,
                )
                response = await self._apns.send_notification(request)
            if not response.is_successful:
                logger.warning("push to %s… failed: %s", token[:8], response.description)
                return SendOutcome(token, False, response.description)
            return SendOutcome(token, True)
        except Exception:
            logger.exception("push to %s… crashed", token[:8])
            return SendOutcome(token, False)
