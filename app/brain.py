from datetime import UTC, datetime, timedelta

from .danger_service import DangerService, DetectedThreat
from .decide import ALERT, CLEAR_DUE, SAFETY, all_clear_due, decide
from .payloads import Batch, all_clear_batch, detection_batches
from .state import PushLedger, SkyContext
from .tme import parse_messages

PUSH_TYPES = frozenset({"ballistic", "irbm"})
CONTEXT_TTL = timedelta(minutes=20)
COOLDOWN = timedelta(seconds=120)
CLEAR_WINDOW = timedelta(minutes=60)
MAX_AGE_SEC = 300.0

NO_TEXT = "no_text"
STALE = "stale"


def _at(raw: str | None) -> datetime | None:
    return datetime.fromisoformat(raw) if raw else None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _batch(batch: Batch) -> dict:
    return {
        "tokens": batch.tokens,
        "payload": batch.payload,
        "priority": batch.priority,
        "ttl": batch.ttl,
    }


class Brain:
    def __init__(self, danger: DangerService, state: dict | None = None) -> None:
        state = state or {}
        self.danger = danger
        self.sky = SkyContext(
            ttl=CONTEXT_TTL,
            _ballistic_at=_at(state.get("ballistic_at")),
            _other_at=_at(state.get("other_at")),
            _cruise_at=_at(state.get("cruise_at")),
        )
        self.ledger = PushLedger(
            cooldown=COOLDOWN,
            _last_at=_at(state.get("ledger_at")),
            _last_rank=state.get("ledger_rank", -1),
        )
        self.last_threat = _at(state.get("last_threat"))
        self.last_clear = _at(state.get("last_clear"))

    def state(self) -> dict:
        return {
            "ballistic_at": _iso(self.sky._ballistic_at),
            "other_at": _iso(self.sky._other_at),
            "cruise_at": _iso(self.sky._cruise_at),
            "ledger_at": _iso(self.ledger._last_at),
            "ledger_rank": self.ledger._last_rank,
            "last_threat": _iso(self.last_threat),
            "last_clear": _iso(self.last_clear),
        }

    def handle(
        self, post: dict, now: datetime, devices: list[dict],
        push_warnings: bool = True, critical_alerts: bool = True,
    ) -> dict:
        channel = post["channel"]
        text = post.get("text")
        ts = _at(post.get("ts"))
        if "html" in post:
            parsed = parse_messages(post["html"])
            if not parsed:
                return {"outcome": NO_TEXT}
            _, text, ts = parsed[0]
        text = (text or "").strip()
        if not text:
            return {"outcome": NO_TEXT}
        ts = ts or now
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        age = (now - ts).total_seconds()
        result = {"ts": ts.isoformat(), "age": round(age, 1), "text": text}
        if age > MAX_AGE_SEC:
            return {**result, "outcome": STALE}

        decision = decide(self.danger, self.sky, PUSH_TYPES, push_warnings, channel, text, ts)
        result["outcome"] = decision.outcome
        result["label"] = decision.label
        if decision.threat is not None:
            result["type"] = decision.threat.type
            result["severity"] = decision.threat.severity
        if decision.outcome == SAFETY:
            result["was_live"] = decision.was_live
        if decision.outcome == SAFETY and decision.all_clear:
            verdict = all_clear_due(ts, self.last_threat, self.last_clear, CLEAR_WINDOW)
            result["all_clear"] = verdict
            if verdict == CLEAR_DUE:
                result["send"] = [_batch(all_clear_batch(devices, text, ts, channel))]
                result["commit"] = {"kind": "clear", "ts": ts.isoformat()}
        elif decision.outcome == ALERT:
            threat = decision.threat
            result["push_text"] = threat.text
            if self.ledger.should_notify(threat, ts):
                result["send"] = [
                    _batch(b) for b in
                    detection_batches(devices, threat, ts, channel, critical_alerts)
                ]
                result["commit"] = {
                    "kind": "threat", "type": threat.type,
                    "severity": threat.severity, "ts": ts.isoformat(),
                }
            else:
                result["suppressed"] = True
                result["cooldown_left"] = int(
                    self.ledger.wait_left(threat, ts).total_seconds())
        return result

    def commit(self, commit: dict) -> None:
        ts = _at(commit["ts"])
        if commit["kind"] == "clear":
            self.last_clear = max(ts, self.last_clear) if self.last_clear else ts
            return
        self.ledger.note(
            DetectedThreat(type=commit["type"], text="", severity=commit["severity"]), ts,
        )
        self.last_threat = max(ts, self.last_threat) if self.last_threat else ts


def process_request(danger: DangerService, req: dict) -> dict:
    brain = Brain(danger, req.get("state"))
    result = brain.handle(
        req["post"],
        now=datetime.fromisoformat(req["now"]),
        devices=req.get("devices") or [],
        push_warnings=req.get("push_warnings", True),
        critical_alerts=req.get("critical_alerts", True),
    )
    reply = {"state": brain.state(), "result": result}
    if "commit" in result:
        brain.commit(result["commit"])
        reply["committed_state"] = brain.state()
        if req.get("assume_delivered"):
            reply["state"] = reply["committed_state"]
    return reply


def commit_request(danger: DangerService, req: dict) -> dict:
    brain = Brain(danger, req.get("state"))
    brain.commit(req["commit"])
    return {"state": brain.state()}
