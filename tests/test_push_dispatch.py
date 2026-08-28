import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from aioapns import PushType

from app.config import Config
from app.danger_service import DetectedThreat
from app.push import (
    BACKGROUND_PRIORITY,
    INBOUND_TTL_SEC,
    LA_END_TTL_SEC,
    LA_TTL_SEC,
    PROBE_CONCURRENCY,
    PushService,
    SendOutcome,
    WARNING_TTL_SEC,
)

TOKEN_A = "aa" * 32
TOKEN_B = "bb" * 32
TOKEN_C = "cc" * 32

ALERT_CONCURRENCY = 50


def device(
    token: str, warnings: bool = True, sound: str = "alert.caf",
    critical: bool = False, critical_volume: float | None = None,
) -> dict:
    return {
        "token": token, "warnings": warnings, "sound": sound,
        "critical": critical, "critical_volume": critical_volume,
    }


def make_service(critical_alerts: bool = True) -> tuple[PushService, list[str]]:
    removed: list[str] = []

    async def on_dead(token: str) -> None:
        removed.append(token)

    config = Config(
        channels=["kyiv_nebo"], database_url="postgresql://unused",
        apns_key_p8_b64="", apns_key_id="", apns_team_id="",
        apns_topic="comeodore.airdanger", apns_sandbox=False, api_key=None,
        critical_alerts=critical_alerts, push_cooldown_sec=120,
        push_escalation=True, push_warnings=False,
        push_types=frozenset({"ballistic", "irbm"}),
        poll_sec=5.0, max_age_sec=300.0, health_window_sec=60.0,
    )
    return PushService(config, on_dead_token=on_dead), removed


async def test_unregistered_token_is_removed():
    service, removed = make_service()
    await service._retire_dead([
        SendOutcome(TOKEN_A, True),
        SendOutcome(TOKEN_B, False, "Unregistered"),
    ])
    assert removed == [TOKEN_B]


async def test_every_unregistered_token_is_removed_even_when_all_fail():
    service, removed = make_service()
    await service._retire_dead([
        SendOutcome(TOKEN_A, False, "Unregistered"),
        SendOutcome(TOKEN_B, False, "Unregistered"),
    ])
    assert removed == [TOKEN_A, TOKEN_B]


async def test_bad_device_token_is_never_removed():
    service, removed = make_service()
    await service._retire_dead([
        SendOutcome(TOKEN_A, True),
        SendOutcome(TOKEN_B, False, "BadDeviceToken"),
    ])
    assert removed == []


async def test_universal_bad_device_token_keeps_every_device():
    service, removed = make_service()
    await service._retire_dead([
        SendOutcome(TOKEN_A, False, "BadDeviceToken"),
        SendOutcome(TOKEN_B, False, "BadDeviceToken"),
        SendOutcome(TOKEN_C, False, "BadDeviceToken"),
    ])
    assert removed == []


async def test_wrong_topic_is_never_removed():
    service, removed = make_service()
    await service._retire_dead([
        SendOutcome(TOKEN_A, True),
        SendOutcome(TOKEN_B, False, "DeviceTokenNotForTopic"),
    ])
    assert removed == []


async def test_transport_crash_never_removes_a_device():
    service, removed = make_service()
    await service._retire_dead([
        SendOutcome(TOKEN_A, False, None),
        SendOutcome(TOKEN_B, False, None),
    ])
    assert removed == []


async def test_throttling_never_removes_a_device():
    service, removed = make_service()
    await service._retire_dead([
        SendOutcome(TOKEN_A, False, "TooManyRequests"),
        SendOutcome(TOKEN_B, False, "ServiceUnavailable"),
    ])
    assert removed == []


async def test_mixed_unregistered_and_mismatch_removes_only_the_unregistered():
    service, removed = make_service()
    await service._retire_dead([
        SendOutcome(TOKEN_A, False, "Unregistered"),
        SendOutcome(TOKEN_B, False, "BadDeviceToken"),
    ])
    assert removed == [TOKEN_A]


def stub_send_one(service: PushService, reason: str | None, captured: list) -> None:
    async def fake(token, payload, priority, push_type=None, **kwargs):
        captured.append((token, payload, priority, push_type))
        return SendOutcome(token, reason is None, reason)

    service._send_one = fake
    service._apns = object()


async def test_unusable_token_reasons_are_rejected_at_registration():
    for reason in ("BadDeviceToken", "DeviceTokenNotForTopic", "Unregistered"):
        service, _ = make_service()
        stub_send_one(service, reason, [])
        assert await service.token_is_usable(TOKEN_A) is False, reason


async def test_accepted_token_is_usable():
    service, _ = make_service()
    stub_send_one(service, None, [])
    assert await service.token_is_usable(TOKEN_A) is True


async def test_inconclusive_probe_keeps_the_token():
    for reason in ("TooManyRequests", "ServiceUnavailable", "InternalServerError", None):
        service, _ = make_service()
        captured: list = []

        async def fake(token, payload, priority, push_type=None, _r=reason, **kwargs):
            captured.append(token)
            return SendOutcome(token, False, _r)

        service._send_one = fake
        service._apns = object()
        assert await service.token_is_usable(TOKEN_A) is True, reason


async def test_validation_probe_is_a_silent_background_push():
    service, _ = make_service()
    captured: list = []
    stub_send_one(service, None, captured)
    await service.token_is_usable(TOKEN_A)
    _token, payload, priority, push_type = captured[0]
    assert payload == {"aps": {"content-available": 1}}
    assert priority == BACKGROUND_PRIORITY
    assert push_type is PushType.BACKGROUND


async def test_token_is_usable_when_apns_is_not_configured():
    service, _ = make_service()
    assert service._apns is None
    assert await service.token_is_usable(TOKEN_A) is True


def watch_pools(service: PushService) -> list[tuple[int, int]]:
    seen: list[tuple[int, int]] = []

    class FakeAPNs:
        async def send_notification(self, request):
            seen.append((service._semaphore._value, service._probe_semaphore._value))
            return SimpleNamespace(is_successful=True, description=None)

    service._apns = FakeAPNs()
    return seen


async def test_probe_uses_its_own_pool_and_leaves_the_alert_pool_untouched():
    service, _ = make_service()
    seen = watch_pools(service)
    await service.token_is_usable(TOKEN_A)
    alert_free, probe_free = seen[0]
    assert alert_free == ALERT_CONCURRENCY
    assert probe_free == PROBE_CONCURRENCY - 1


def watch_requests(service: PushService) -> list:
    requests: list = []

    class FakeAPNs:
        async def send_notification(self, request):
            requests.append(request)
            return SimpleNamespace(is_successful=True, description=None)

    service._apns = FakeAPNs()
    return requests


async def test_alerts_expire_instead_of_arriving_stale():
    service, _ = make_service()
    requests = watch_requests(service)
    ts = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    await service.send_detection(
        [device(TOKEN_A)], DetectedThreat(type="ballistic", text="Балістика на Київ"), ts,
    )
    await service.send_detection(
        [device(TOKEN_A)],
        DetectedThreat(type="ballistic", text="Загроза", severity="warning"), ts,
    )
    assert requests[0].time_to_live == INBOUND_TTL_SEC
    assert requests[1].time_to_live == WARNING_TTL_SEC


async def test_a_warning_vibrates_via_the_silent_sound_without_audio():
    service, _ = make_service()
    requests = watch_requests(service)
    ts = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    await service.send_detection(
        [device(TOKEN_A)],
        DetectedThreat(type="ballistic", text="Загроза балістики", severity="warning"),
        ts,
    )
    aps = requests[0].message["aps"]
    assert aps["sound"] == "silent.caf"
    assert aps["interruption-level"] == "time-sensitive"
    assert aps["alert"]["title"] == "Загроза балістики"


async def test_an_inbound_alert_still_sounds():
    service, _ = make_service()
    requests = watch_requests(service)
    ts = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    await service.send_detection(
        [device(TOKEN_A)], DetectedThreat(type="ballistic", text="Балістика на Київ"), ts,
    )
    aps = requests[0].message["aps"]
    assert aps["sound"] == "alert.caf"
    assert aps["interruption-level"] == "time-sensitive"


async def test_the_probe_push_is_never_stored_for_later():
    service, _ = make_service()
    requests = watch_requests(service)
    await service.token_is_usable(TOKEN_A)
    assert requests[0].time_to_live == 0


async def test_alert_uses_the_alert_pool_and_leaves_the_probe_pool_untouched():
    service, _ = make_service()
    seen = watch_pools(service)
    threat = DetectedThreat(type="ballistic", text="Балістика", severity="inbound")
    await service.send_detection([device(TOKEN_A)], threat, datetime.now(UTC), source="kyiv_nebo")
    alert_free, probe_free = seen[0]
    assert alert_free == ALERT_CONCURRENCY - 1
    assert probe_free == PROBE_CONCURRENCY


async def test_saturated_probe_pool_cannot_delay_an_alert():
    service, _ = make_service()
    service._apns = SimpleNamespace(
        send_notification=lambda request: asyncio.sleep(
            0, SimpleNamespace(is_successful=True, description=None)
        )
    )
    for _ in range(PROBE_CONCURRENCY):
        await service._probe_semaphore.acquire()
    assert service._probe_semaphore.locked()

    threat = DetectedThreat(type="ballistic", text="Балістика", severity="inbound")
    reached = await asyncio.wait_for(
        service.send_detection([device(TOKEN_A), device(TOKEN_B)], threat, datetime.now(UTC),
                               source="kyiv_nebo"),
        timeout=2,
    )
    assert reached == 2


async def test_send_detection_is_a_high_priority_time_sensitive_alert():
    service, _ = make_service()
    captured: list[tuple] = []

    async def fake_send_one(token, payload, priority, push_type=None, **kwargs):
        captured.append((token, payload, priority))
        return SendOutcome(token, True)

    service._send_one = fake_send_one
    service._apns = object()

    threat = DetectedThreat(type="ballistic", text="Балістика на Київ", severity="inbound")
    reached = await service.send_detection([device(TOKEN_A)], threat, datetime.now(UTC),
                                           source="kyiv_nebo")

    assert reached == 1
    _token, payload, priority = captured[0]
    assert priority == 10
    assert payload["aps"]["interruption-level"] == "time-sensitive"
    assert payload["aps"]["alert"]["title"] == "Балістика на Київ"
    assert payload["source"] == "kyiv_nebo"


async def test_warning_skips_devices_that_muted_warnings():
    service, _ = make_service()
    requests = watch_requests(service)
    ts = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    delivered = await service.send_detection(
        [device(TOKEN_A, warnings=False), device(TOKEN_B)],
        DetectedThreat(type="ballistic", text="Загроза балістики", severity="warning"),
        ts,
    )
    assert delivered == 1
    assert [r.device_token for r in requests] == [TOKEN_B]


async def test_inbound_reaches_devices_that_muted_warnings():
    service, _ = make_service()
    requests = watch_requests(service)
    ts = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    delivered = await service.send_detection(
        [device(TOKEN_A, warnings=False), device(TOKEN_B)],
        DetectedThreat(type="ballistic", text="Балістика на Київ"),
        ts,
    )
    assert delivered == 2
    assert sorted(r.device_token for r in requests) == [TOKEN_A, TOKEN_B]


async def test_inbound_sound_follows_device_pref():
    service, _ = make_service()
    requests = watch_requests(service)
    ts = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    delivered = await service.send_detection(
        [
            device(TOKEN_A, sound="opovishchennia.caf"),
            device(TOKEN_B, sound="alert.caf"),
            {"token": TOKEN_C},
        ],
        DetectedThreat(type="ballistic", text="Балістика на Київ"),
        ts,
    )
    assert delivered == 3
    sounds = {r.device_token: r.message["aps"]["sound"] for r in requests}
    assert sounds == {TOKEN_A: "opovishchennia.caf", TOKEN_B: "alert.caf", TOKEN_C: "alert.caf"}


async def test_inbound_is_critical_only_for_devices_that_granted_it():
    service, _ = make_service()
    requests = watch_requests(service)
    ts = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    delivered = await service.send_detection(
        [device(TOKEN_A, critical=True), device(TOKEN_B)],
        DetectedThreat(type="ballistic", text="Балістика на Київ"),
        ts,
    )
    assert delivered == 2
    by_token = {r.device_token: r.message["aps"] for r in requests}
    assert by_token[TOKEN_A]["sound"] == {
        "critical": 1, "name": "alert.caf", "volume": 1.0,
    }
    assert by_token[TOKEN_A]["interruption-level"] == "critical"
    assert by_token[TOKEN_B]["sound"] == "alert.caf"
    assert by_token[TOKEN_B]["interruption-level"] == "time-sensitive"


async def test_critical_volume_follows_device_pref():
    service, _ = make_service()
    requests = watch_requests(service)
    ts = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    delivered = await service.send_detection(
        [
            device(TOKEN_A, critical=True, critical_volume=0.35),
            device(TOKEN_B, critical=True, sound="pulse.caf"),
            device(TOKEN_C, critical=True, critical_volume=7.0),
        ],
        DetectedThreat(type="ballistic", text="Балістика на Київ"),
        ts,
    )
    assert delivered == 3
    sounds = {r.device_token: r.message["aps"]["sound"] for r in requests}
    assert sounds == {
        TOKEN_A: {"critical": 1, "name": "alert.caf", "volume": 0.35},
        TOKEN_B: {"critical": 1, "name": "pulse.caf", "volume": 1.0},
        TOKEN_C: {"critical": 1, "name": "alert.caf", "volume": 1.0},
    }


async def test_zero_volume_stays_a_silent_critical_alert():
    service, _ = make_service()
    requests = watch_requests(service)
    ts = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    await service.send_detection(
        [device(TOKEN_A, critical=True, critical_volume=0.0)],
        DetectedThreat(type="ballistic", text="Балістика на Київ"),
        ts,
    )
    aps = requests[0].message["aps"]
    assert aps["sound"] == {"critical": 1, "name": "alert.caf", "volume": 0.0}
    assert aps["interruption-level"] == "critical"


async def test_critical_alerts_can_be_killed_server_side():
    service, _ = make_service(critical_alerts=False)
    requests = watch_requests(service)
    ts = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    await service.send_detection(
        [device(TOKEN_A, critical=True, critical_volume=0.5)],
        DetectedThreat(type="ballistic", text="Балістика на Київ"),
        ts,
    )
    aps = requests[0].message["aps"]
    assert aps["sound"] == "alert.caf"
    assert aps["interruption-level"] == "time-sensitive"


async def test_a_warning_stays_time_sensitive_for_critical_devices():
    service, _ = make_service()
    requests = watch_requests(service)
    ts = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    await service.send_detection(
        [device(TOKEN_A, critical=True)],
        DetectedThreat(type="ballistic", text="Загроза балістики", severity="warning"),
        ts,
    )
    aps = requests[0].message["aps"]
    assert aps["sound"] == "silent.caf"
    assert aps["interruption-level"] == "time-sensitive"


async def test_all_clear_stays_time_sensitive_for_critical_devices():
    service, _ = make_service()
    requests = watch_requests(service)
    ts = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    await service.send_all_clear([device(TOKEN_A, critical=True)], "Відбій", ts)
    aps = requests[0].message["aps"]
    assert aps["sound"] == "silent.caf"
    assert aps["interruption-level"] == "time-sensitive"


async def test_live_activity_start_payload_targets_the_la_topic():
    service, _ = make_service()
    requests = watch_requests(service)
    ts = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    state = {"state": "active", "severity": "inbound", "count": 1}
    delivered = await service.send_live_activity(
        [TOKEN_A], "start", state, ts, attributes={"episode": 1},
    )
    assert delivered == 1
    request = requests[0]
    assert request.push_type is PushType.LIVEACTIVITY
    assert request.apns_topic == "comeodore.airdanger.push-type.liveactivity"
    aps = request.message["aps"]
    assert aps["event"] == "start"
    assert aps["attributes-type"] == "ThreatActivityAttributes"
    assert aps["attributes"] == {"episode": 1}
    assert aps["content-state"] == state
    assert aps["timestamp"] == int(ts.timestamp())
    assert aps["stale-date"] == int(ts.timestamp()) + 1800

async def test_live_activity_end_carries_a_dismissal_date():
    service, _ = make_service()
    requests = watch_requests(service)
    ts = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    await service.send_live_activity(
        [TOKEN_A], "end", {"state": "clear"}, ts,
        dismissal_at=ts + timedelta(seconds=180),
    )
    aps = requests[0].message["aps"]
    assert aps["event"] == "end"
    assert "attributes-type" not in aps
    assert aps["dismissal-date"] == int(ts.timestamp()) + 180


async def test_live_activity_end_waits_hours_for_offline_devices():
    service, _ = make_service()
    requests = watch_requests(service)
    ts = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    await service.send_live_activity(
        [TOKEN_A], "end", {"state": "clear", "startedAt": ts.timestamp()}, ts,
        dismissal_at=ts + timedelta(seconds=600),
    )
    request = requests[0]
    assert request.time_to_live == LA_END_TTL_SEC
    assert request.message["aps"]["relevance-score"] == ts.timestamp()


async def test_live_activity_update_priority_passes_through():
    service, _ = make_service()
    requests = watch_requests(service)
    ts = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    await service.send_live_activity(
        [TOKEN_A], "update", {"count": 2}, ts, priority=5,
    )
    assert requests[0].priority == 5
    assert requests[0].time_to_live == LA_TTL_SEC


async def test_all_clear_vibrates_without_sound():
    service, _ = make_service()
    requests = watch_requests(service)
    ts = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    delivered = await service.send_all_clear(
        [device(TOKEN_A)], "Відбій тривоги", ts,
    )
    assert delivered == 1
    aps = requests[0].message["aps"]
    assert aps["sound"] == "silent.caf"
    assert aps["interruption-level"] == "time-sensitive"
