from dataclasses import dataclass
from datetime import datetime, timedelta

from .danger_service import DetectedThreat


SEVERITY_RANK = {"warning": 0, "inbound": 1}
TYPE_RANK = {"irbm": 2}


def rank_of(threat: DetectedThreat) -> int:
    return SEVERITY_RANK.get(threat.severity, 1) + TYPE_RANK.get(threat.type, 0)


@dataclass
class SkyContext:
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
    escalate: bool = True
    _last_at: datetime | None = None
    _last_rank: int = -1

    def _cooling(self, ts: datetime) -> bool:
        return self._last_at is not None and ts - self._last_at < self.cooldown

    def should_notify(self, threat: DetectedThreat, ts: datetime) -> bool:
        if not self._cooling(ts):
            return True
        return self.escalate and rank_of(threat) > self._last_rank

    def note(self, threat: DetectedThreat, ts: datetime) -> None:
        rank = rank_of(threat)
        self._last_rank = max(self._last_rank, rank) if self._cooling(ts) else rank
        self._last_at = ts

    def wait_left(self, threat: DetectedThreat, ts: datetime) -> timedelta:
        if self._last_at is None:
            return timedelta()
        return max(timedelta(), self.cooldown - (ts - self._last_at))

    def seed(self, rows: list[dict]) -> None:
        for row in rows:
            ts = row["ts"]
            if self._last_at is None or ts >= self._last_at:
                self.note(
                    DetectedThreat(
                        type=row["type"],
                        text="",
                        severity=row.get("severity") or "inbound",
                    ),
                    ts,
                )
