from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .danger_service import DetectedThreat


@dataclass
class PushLedger:
    cooldown: timedelta
    _last_push: dict[tuple[str, str], datetime] = field(default_factory=dict)

    def should_notify(self, threat: DetectedThreat, ts: datetime) -> bool:
        key = (threat.severity, threat.type)
        last = self._last_push.get(key)
        if last is not None and ts - last < self.cooldown:
            return False
        self._last_push[key] = ts
        return True

    def seed(self, rows: list[dict]) -> None:
        for row in rows:
            key = (row.get("severity") or "inbound", row["type"])
            self._last_push[key] = row["ts"]
