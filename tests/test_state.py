from datetime import UTC, datetime, timedelta

from app.danger_service import DetectedThreat
from app.state import PushLedger

T0 = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def threat(type_="ballistic", text="msg", severity="inbound"):
    return DetectedThreat(type=type_, text=text, severity=severity)

def ledger(cooldown_sec=60):
    return PushLedger(cooldown=timedelta(seconds=cooldown_sec))

def test_first_mention_notifies_immediately():
    assert ledger().should_notify(threat(), T0) is True

def test_mention_inside_cooldown_is_silent():
    led = ledger()
    assert led.should_notify(threat(), T0) is True
    assert led.should_notify(threat(), T0 + timedelta(seconds=30)) is False
    assert led.should_notify(threat(), T0 + timedelta(seconds=59)) is False

def test_mention_after_cooldown_pushes_again():
    led = ledger(cooldown_sec=60)
    led.should_notify(threat(), T0)
    assert led.should_notify(threat(), T0 + timedelta(seconds=60)) is True

def test_cooldown_runs_from_the_last_push_not_the_last_mention():
    led = ledger(cooldown_sec=60)
    led.should_notify(threat(), T0)
    led.should_notify(threat(), T0 + timedelta(seconds=50))
    assert led.should_notify(threat(), T0 + timedelta(seconds=61)) is True

def test_warning_does_not_silence_inbound_escalation():
    led = ledger()
    assert led.should_notify(threat(severity="warning", text="Загроза балістики"), T0) is True

    assert led.should_notify(threat(text="Ціль на Київ"), T0 + timedelta(seconds=30)) is True

def test_seed_primes_cooldown_from_feed_rows_after_restart():
    led = ledger(cooldown_sec=60)
    led.seed([{"channel": "ch", "type": "ballistic", "severity": "inbound",
               "text": "т", "ts": T0}])
    assert led.should_notify(threat(), T0 + timedelta(seconds=30)) is False
    assert led.should_notify(threat(), T0 + timedelta(seconds=90)) is True
