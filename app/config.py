import base64
import json
import os
import tempfile
from dataclasses import dataclass, field


DEFAULT_CHANNELS = ["kyiv_nebo"]


def _bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes")

def _parse_channels(raw: str | None) -> list[str]:
    if not raw:
        return list(DEFAULT_CHANNELS)
    raw = raw.strip()
    if raw.startswith("{"):
        return list(json.loads(raw).keys())
    if raw.startswith("["):
        return list(json.loads(raw))
    return [c.strip() for c in raw.split(",") if c.strip()]

@dataclass
class Config:
    channels: list[str]
    database_url: str
    apns_key_p8_b64: str
    apns_key_id: str
    apns_team_id: str
    apns_topic: str
    apns_sandbox: bool
    api_key: str | None
    critical_alerts: bool
    push_cooldown_sec: int
    push_warnings: bool
    push_types: frozenset[str]
    tg_api_id: int
    tg_api_hash: str
    tg_session: str
    catchup_sec: float
    catchup_max_age_sec: float
    fallback_after_sec: float
    preview_poll_sec: float
    context_ttl_min: int = 20
    _apns_key_path: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            channels=_parse_channels(os.environ.get("CHANNELS")),
            database_url=os.environ["DATABASE_URL"],
            apns_key_p8_b64=os.environ.get("APNS_KEY_P8", ""),
            apns_key_id=os.environ.get("APNS_KEY_ID", ""),
            apns_team_id=os.environ.get("APNS_TEAM_ID", ""),
            apns_topic=os.environ.get("APNS_TOPIC", "comeodore.airdanger"),
            apns_sandbox=_bool("APNS_SANDBOX", default=False),
            api_key=os.environ.get("API_KEY") or None,
            critical_alerts=_bool("CRITICAL_ALERTS", default=False),
            push_cooldown_sec=int(os.environ.get("PUSH_COOLDOWN_SEC") or 120),
            push_warnings=_bool("PUSH_WARNINGS", default=True),
            push_types=frozenset(
                t.strip()
                for t in (os.environ.get("PUSH_TYPES") or "ballistic,irbm").split(",")
                if t.strip()
            ),
            tg_api_id=int(os.environ.get("TG_API_ID") or 0),
            tg_api_hash=os.environ.get("TG_API_HASH", ""),
            tg_session=os.environ.get("TG_SESSION", ""),
            catchup_sec=float(os.environ.get("TG_CATCHUP_SEC") or 30),
            catchup_max_age_sec=float(os.environ.get("TG_MAX_AGE_SEC") or 300),
            fallback_after_sec=float(os.environ.get("FALLBACK_AFTER_SEC") or 20),
            preview_poll_sec=float(os.environ.get("POLL_SEC") or 5),
            context_ttl_min=int(os.environ.get("CONTEXT_TTL_MIN") or 20),
        )

    @property
    def apns_configured(self) -> bool:
        return bool(self.apns_key_p8_b64 and self.apns_key_id and self.apns_team_id)

    @property
    def tg_configured(self) -> bool:
        return bool(self.tg_api_id and self.tg_api_hash and self.tg_session)

    def apns_key_path(self) -> str:
        if self._apns_key_path is None:
            fd, path = tempfile.mkstemp(suffix=".p8")
            with os.fdopen(fd, "wb") as f:
                f.write(base64.b64decode(self.apns_key_p8_b64))
            self._apns_key_path = path
        return self._apns_key_path
