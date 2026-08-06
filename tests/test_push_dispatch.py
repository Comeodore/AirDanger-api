from datetime import UTC, datetime

from app.config import Config
from app.danger_service import DetectedThreat
from app.push import PushService, SendOutcome

TOKEN_A = "aa" * 32
TOKEN_B = "bb" * 32
TOKEN_C = "cc" * 32


def make_service() -> tuple[PushService, list[str]]:
    removed: list[str] = []

    async def on_dead(token: str) -> None:
        removed.append(token)

    config = Config(
        channels=["kyiv_nebo"], database_url="postgresql://unused",
        apns_key_p8_b64="", apns_key_id="", apns_team_id="",
        apns_topic="comeodore.airdanger", apns_sandbox=False, api_key=None,
        critical_alerts=False, push_cooldown_sec=120, push_warnings=False,
        push_types=frozenset({"ballistic", "irbm"}), poll_sec=5.0,
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


async def test_send_detection_is_a_high_priority_time_sensitive_alert():
    service, _ = make_service()
    captured: list[tuple] = []

    async def fake_send_one(token, payload, priority):
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
