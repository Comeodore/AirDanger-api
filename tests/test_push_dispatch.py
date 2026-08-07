import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from aioapns import PushType

from app.config import Config
from app.danger_service import DetectedThreat
from app.push import BACKGROUND_PRIORITY, PROBE_CONCURRENCY, PushService, SendOutcome

TOKEN_A = "aa" * 32
TOKEN_B = "bb" * 32
TOKEN_C = "cc" * 32

ALERT_CONCURRENCY = 50


def make_service() -> tuple[PushService, list[str]]:
    removed: list[str] = []

    async def on_dead(token: str) -> None:
        removed.append(token)

    config = Config(
        channels=["kyiv_nebo"], database_url="postgresql://unused",
        apns_key_p8_b64="", apns_key_id="", apns_team_id="",
        apns_topic="comeodore.airdanger", apns_sandbox=False, api_key=None,
        critical_alerts=False, push_cooldown_sec=120, push_warnings=False,
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


async def test_alert_uses_the_alert_pool_and_leaves_the_probe_pool_untouched():
    service, _ = make_service()
    seen = watch_pools(service)
    threat = DetectedThreat(type="ballistic", text="Балістика", severity="inbound")
    await service.send_detection([TOKEN_A], threat, datetime.now(UTC), source="kyiv_nebo")
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
        service.send_detection([TOKEN_A, TOKEN_B], threat, datetime.now(UTC),
                               source="kyiv_nebo"),
        timeout=2,
    )
    assert reached == 2


async def test_send_detection_is_a_high_priority_time_sensitive_alert():
    service, _ = make_service()
    captured: list[tuple] = []

    async def fake_send_one(token, payload, priority, **kwargs):
        captured.append((token, payload, priority))
        return SendOutcome(token, True)

    service._send_one = fake_send_one
    service._apns = object()

    threat = DetectedThreat(type="ballistic", text="Балістика на Київ", severity="inbound")
    reached = await service.send_detection([TOKEN_A], threat, datetime.now(UTC),
                                           source="kyiv_nebo")

    assert reached == 1
    _token, payload, priority = captured[0]
    assert priority == 10
    assert payload["aps"]["interruption-level"] == "time-sensitive"
    assert payload["aps"]["alert"]["title"] == "Балістика на Київ"
    assert payload["source"] == "kyiv_nebo"
