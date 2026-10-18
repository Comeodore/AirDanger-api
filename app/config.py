import base64
import os
import tempfile
from dataclasses import dataclass, field


DEFAULT_CHANNELS = ["kyiv_nebo", "war_monitor"]


def _bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes")

def _parse_channels(raw: str | None) -> list[str]:
    if not raw:
        return list(DEFAULT_CHANNELS)
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
    push_escalation: bool
    push_warnings: bool
    push_types: frozenset[str]
    poll_sec: float
    max_age_sec: float
    health_window_sec: float
    context_ttl_min: int = 20
    all_clear_window_min: int = 60
    la_timeout_min: int = 15
    tg_api_id: int = 0
    tg_api_hash: str = field(default="", repr=False)
    tg_session: str = field(default="", repr=False)
    mtproto_pull_sec: float = 3.0
    edge_key: str = field(default="", repr=False)
    ingest_enabled: bool = True
    _apns_key_path: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls) -> "Config":
        poll_sec = float(os.environ.get("POLL_SEC") or 2)
        return cls(
            channels=_parse_channels(os.environ.get("CHANNELS")),
            database_url=os.environ["DATABASE_URL"],
            apns_key_p8_b64=os.environ.get("APNS_KEY_P8", ""),
            apns_key_id=os.environ.get("APNS_KEY_ID", ""),
            apns_team_id=os.environ.get("APNS_TEAM_ID", ""),
            apns_topic=os.environ.get("APNS_TOPIC", "comeodore.airdanger"),
            apns_sandbox=_bool("APNS_SANDBOX", default=False),
            api_key=os.environ.get("API_KEY") or None,
            critical_alerts=_bool("CRITICAL_ALERTS", default=True),
            push_cooldown_sec=int(os.environ.get("PUSH_COOLDOWN_SEC") or 120),
            push_escalation=_bool("PUSH_ESCALATION", default=True),
            push_warnings=_bool("PUSH_WARNINGS", default=True),
            push_types=frozenset(
                t.strip()
                for t in (os.environ.get("PUSH_TYPES") or "ballistic,irbm").split(",")
                if t.strip()
            ),
            poll_sec=poll_sec,
            max_age_sec=float(os.environ.get("MAX_AGE_SEC") or 300),
            health_window_sec=float(
                os.environ.get("HEALTH_WINDOW_SEC") or max(30.0, poll_sec * 10)
            ),
            context_ttl_min=int(os.environ.get("CONTEXT_TTL_MIN") or 20),
            all_clear_window_min=int(os.environ.get("ALL_CLEAR_WINDOW_MIN") or 60),
            la_timeout_min=int(os.environ.get("LA_TIMEOUT_MIN") or 15),
            tg_api_id=int(os.environ.get("TG_API_ID") or 0),
            tg_api_hash=os.environ.get("TG_API_HASH", "").strip(),
            tg_session=os.environ.get("TG_SESSION", "").strip(),
            mtproto_pull_sec=float(os.environ.get("MTPROTO_PULL_SEC") or 3),
            edge_key=os.environ.get("EDGE_KEY", "").strip(),
            ingest_enabled=_bool("INGEST", default=True),
        )

    @property
    def mtproto_configured(self) -> bool:
        return bool(self.tg_api_id and self.tg_api_hash and self.tg_session)

    @property
    def apns_configured(self) -> bool:
        return bool(self.apns_key_p8_b64 and self.apns_key_id and self.apns_team_id)

    def apns_key_path(self) -> str:
        if self._apns_key_path is None:
            fd, path = tempfile.mkstemp(suffix=".p8")
            with os.fdopen(fd, "wb") as f:
                f.write(base64.b64decode(self.apns_key_p8_b64))
            self._apns_key_path = path
        return self._apns_key_path
