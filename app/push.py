import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

from aioapns import APNs, NotificationRequest, PushType

from .config import Config
from .danger_service import DetectedThreat
from .timefmt import iso_kyiv

logger = logging.getLogger(__name__)

TYPE_NAMES_UK = {
    "irbm": "МБР",
    "ballistic": "Балістика",
}


DEAD_TOKEN_REASONS = {"BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"}

ALERT_SOUND = "alert.caf"

TITLE_LIMIT = 110
BODY_LIMIT = 178


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
    ) -> None:
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
        await self._fan_out(tokens, payload, priority=10)

    async def _fan_out(self, tokens: list[str], payload: dict, priority: int) -> None:
        if self._apns is None or not tokens:
            return
        await asyncio.gather(*(self._send_one(t, payload, priority) for t in tokens))

    async def _send_one(self, token: str, payload: dict, priority: int) -> None:
        try:
            async with self._semaphore:
                request = NotificationRequest(
                    device_token=token,
                    message=payload,
                    push_type=PushType.ALERT,
                    priority=priority,
                )
                response = await self._apns.send_notification(request)
            if not response.is_successful:
                logger.warning("push to %s… failed: %s", token[:8], response.description)
                if response.description in DEAD_TOKEN_REASONS:
                    await self._on_dead_token(token)
        except Exception:
            logger.exception("push to %s… crashed", token[:8])
