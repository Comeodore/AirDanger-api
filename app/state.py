from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .danger_service import DetectedThreat


@dataclass
class ChannelContext:
    ttl: timedelta
    _ballistic_at: datetime | None = None
    _other_at: datetime | None = None

    def mark_ballistic(self, ts: datetime) -> None:
        self._ballistic_at = ts

    def mark_other(self, ts: datetime) -> None:
        self._other_at = ts

    def clear(self) -> None:
        self._ballistic_at = None
        self._other_at = None

    def _live(self, at: datetime | None, ts: datetime) -> bool:
        return at is not None and ts - at <= self.ttl

    def ballistic_live(self, ts: datetime) -> bool:
        return self._live(self._ballistic_at, ts)

    def other_live(self, ts: datetime) -> bool:
        return self._live(self._other_at, ts)

    def ballistic_leads(self, ts: datetime) -> bool:
        if not self.ballistic_live(ts):
            return False
        if not self.other_live(ts):
            return True
        return self._ballistic_at > self._other_at


@dataclass
class PushLedger:
    cooldown: timedelta
    _last_push: dict[tuple[str, str], datetime] = field(default_factory=dict)

    def should_notify(self, threat: DetectedThreat, ts: datetime) -> bool:
        last = self._last_push.get((threat.severity, threat.type))
        return last is None or ts - last >= self.cooldown

    def note(self, threat: DetectedThreat, ts: datetime) -> None:
        self._last_push[(threat.severity, threat.type)] = ts

    def wait_left(self, threat: DetectedThreat, ts: datetime) -> timedelta:
        last = self._last_push.get((threat.severity, threat.type))
        if last is None:
            return timedelta()
        return max(timedelta(), self.cooldown - (ts - last))

    def seed(self, rows: list[dict]) -> None:
        for row in rows:
            key = (row.get("severity") or "inbound", row["type"])
            self._last_push[key] = row["ts"]
