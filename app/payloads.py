import re
from dataclasses import dataclass
from datetime import datetime

from .danger_service import DetectedThreat
from .timefmt import iso_kyiv

TYPE_NAMES_UK = {
    "irbm": "МБР",
    "ballistic": "Балістика",
}

ALERT_SOUND = "alert.caf"
SILENT_SOUND = "silent.caf"

DEFAULT_CRITICAL_VOLUME = 1.0

SOUND_CHOICES = ("alert.caf", "pulse.caf", "opovishchennia.caf")

TITLE_LIMIT = 110
BODY_LIMIT = 178

ALERT_PRIORITY = 10

INBOUND_TTL_SEC = 60
WARNING_TTL_SEC = 300
ALL_CLEAR_TTL_SEC = 300

SENTENCE_END = re.compile(r"(.+?[.!?…])(?:\s|$)")


@dataclass(frozen=True)
class Batch:
    tokens: list[str]
    payload: dict
    priority: int
    ttl: int


def first_sentence(text: str) -> str:
    flat = " ".join(text.split())
    match = SENTENCE_END.match(flat)
    if match:
        flat = match.group(1)
    return flat.rstrip(".")


def critical_volume(device: dict) -> float:
    raw = device.get("critical_volume")
    if raw is None:
        return DEFAULT_CRITICAL_VOLUME
    return round(min(1.0, max(0.0, float(raw))), 2)


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


def detection_batches(
    devices: list[dict], threat: DetectedThreat, ts: datetime,
    source: str | None, critical_alerts: bool,
) -> list[Batch]:
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
        return [Batch(tokens, {"aps": aps, **payload}, ALERT_PRIORITY, WARNING_TTL_SEC)]

    groups: dict[tuple[str, bool, float], list[str]] = {}
    for device in devices:
        sound_name = device.get("sound") or ALERT_SOUND
        if sound_name not in SOUND_CHOICES:
            sound_name = ALERT_SOUND
        critical = critical_alerts and bool(device.get("critical"))
        volume = critical_volume(device) if critical else DEFAULT_CRITICAL_VOLUME
        key = (sound_name, critical, volume)
        groups.setdefault(key, []).append(device["token"])

    batches = []
    for (sound_name, critical, volume), tokens in groups.items():
        sound: dict | str = sound_name
        level = "time-sensitive"
        if critical:
            sound = {"critical": 1, "name": sound_name, "volume": volume}
            level = "critical"
        aps = {
            "alert": alert,
            "sound": sound,
            "interruption-level": level,
        }
        batches.append(
            Batch(tokens, {"aps": aps, **payload}, ALERT_PRIORITY, INBOUND_TTL_SEC)
        )
    return batches


def all_clear_batch(
    devices: list[dict], text: str, ts: datetime, source: str | None,
) -> Batch:
    payload = {
        "aps": {
            "alert": push_alert(text, fallback="Відбій — Київ"),
            "sound": SILENT_SOUND,
            "interruption-level": "time-sensitive",
        },
        "kind": "all_clear",
        "type": "all_clear",
        "severity": "clear",
        "source": source,
        "text": text,
        "ts": iso_kyiv(ts),
    }
    return Batch([d["token"] for d in devices], payload, ALERT_PRIORITY, ALL_CLEAR_TTL_SEC)
