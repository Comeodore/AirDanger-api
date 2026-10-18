from collections.abc import Collection
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from .danger_service import DangerService, DetectedThreat
from .payloads import first_sentence
from .profiles import profile_for
from .state import SkyContext

SAFETY = "safety"
ALERT = "alert"
TYPE_OFF = "type_off"
CRUISE_LEADS = "cruise_leads"
WARNING_OFF = "warning_off"
BARE_NO_CONTEXT = "bare_no_context"
BARE_WARNING_OFF = "bare_warning_off"
NO_MATCH = "no_match"

CLEAR_DUE = "due"
CLEAR_OUTSIDE = "outside"
CLEAR_REPEAT = "repeat"


@dataclass(frozen=True)
class Decision:
    outcome: str
    threat: DetectedThreat | None = None
    label: str = ""
    all_clear: bool = False
    was_live: bool = False
    marked_other: bool = False
    marked_cruise: bool = False


def decide(
    danger: DangerService, sky: SkyContext, push_types: Collection[str],
    push_warnings: bool, source: str, text: str, ts: datetime,
) -> Decision:
    profile = profile_for(source)
    evaluation = danger.evaluate(text, profile)
    if evaluation.safety:
        was_live = sky.ballistic_live(ts)
        sky.clear()
        return Decision(SAFETY, all_clear=evaluation.all_clear, was_live=was_live)
    marked_other = evaluation.other_weapon
    if marked_other:
        sky.mark_other(ts)
    marked_cruise = danger.is_cruise(text)
    if marked_cruise:
        sky.mark_cruise(ts)
    marks = {"marked_other": marked_other, "marked_cruise": marked_cruise}

    threat = evaluation.detection
    bare = False
    if threat is not None:
        if threat.type not in push_types:
            return Decision(TYPE_OFF, threat=threat, **marks)
        if evaluation.guessed and sky.cruise_leads(ts):
            return Decision(CRUISE_LEADS, **marks)
        if not evaluation.forecast:
            sky.mark_ballistic(ts)
        threat = replace(threat, severity=danger.siren_severity(
            text, threat.severity, profile))
        if threat.severity == "warning" and not push_warnings:
            return Decision(WARNING_OFF, threat=threat, **marks)
    elif evaluation.bare_target:
        if not sky.ballistic_leads(ts):
            return Decision(BARE_NO_CONTEXT, **marks)
        severity = danger.siren_severity(
            text, danger.bare_severity(text), profile)
        if severity == "warning" and not push_warnings:
            return Decision(BARE_WARNING_OFF, **marks)
        threat = DetectedThreat(type="ballistic", text=text, severity=severity)
        bare = True
    else:
        return Decision(NO_MATCH, **marks)

    if threat.severity == "warning" and profile.trim_warning_push:
        threat = replace(threat, text=first_sentence(threat.text))

    label = f"{threat.severity}/{threat.type}"
    if bare:
        label += " (context)"
    elif evaluation.forecast:
        label += " (forecast)"
    return Decision(ALERT, threat=threat, label=label, **marks)


def all_clear_due(
    ts: datetime, last_threat: datetime | None, last_clear: datetime | None,
    window: timedelta,
) -> str:
    if last_threat is None or ts - last_threat > window:
        return CLEAR_OUTSIDE
    if last_clear is not None and last_clear >= last_threat:
        return CLEAR_REPEAT
    return CLEAR_DUE
