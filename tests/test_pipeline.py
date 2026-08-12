from datetime import UTC, datetime, timedelta

from app.config import Config
from app.danger_service import DangerService
from app.main import AppContext
from app.state import PushLedger, SkyContext

T0 = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def make_config(push_warnings: bool = False, push_escalation: bool = True) -> Config:
    return Config(
        channels=["kyiv_nebo"],
        database_url="postgresql://unused",
        apns_key_p8_b64="", apns_key_id="", apns_team_id="",
        apns_topic="t", apns_sandbox=True, api_key=None,
        critical_alerts=False,
        push_cooldown_sec=120,
        push_escalation=push_escalation,
        push_warnings=push_warnings,
        push_types=frozenset({"ballistic", "irbm"}),
        poll_sec=5.0, max_age_sec=300.0, health_window_sec=60.0,
        context_ttl_min=20,
    )


class FakeDB:
    def __init__(self, devices: int = 1) -> None:
        self.pushes: list[tuple] = []
        self.devices = devices

    async def insert_push(self, channel, type_, severity, text, ts, pushed=True):
        self.pushes.append((channel, type_, severity, text, pushed))

    async def tokens(self):
        return [f"{i + 10:02x}" * 32 for i in range(self.devices)]

class FakePush:
    def __init__(self, delivered: int = 1) -> None:
        self.sent: list[str] = []
        self.delivered = delivered

    async def send_detection(self, tokens, threat, ts, source=None):
        self.sent.append(threat.text)
        return self.delivered

def make_ctx(push_warnings: bool = False, delivered: int = 1,
             devices: int = 1, push_escalation: bool = True) -> AppContext:
    return AppContext(
        config=make_config(push_warnings, push_escalation),
        db=FakeDB(devices),
        danger=DangerService(),
        ledger=PushLedger(cooldown=timedelta(seconds=120),
                          escalate=push_escalation),
        push=FakePush(delivered),
        ingest=None,
        sky=SkyContext(ttl=timedelta(minutes=20)),
    )

async def test_ballistic_mention_pushes_and_is_recorded():
    ctx = make_ctx()
    await ctx.handle_message("kyiv_nebo", "Швидкісна ціль на Київ!", T0)
    assert ctx.push.sent == ["Швидкісна ціль на Київ!"]
    assert ctx.db.pushes == [
        ("kyiv_nebo", "ballistic", "inbound", "Швидкісна ціль на Київ!", True),
    ]

async def test_partial_delivery_is_recorded_and_starts_cooldown():
    ctx = make_ctx(delivered=1, devices=3)
    await ctx.handle_message("kyiv_nebo", "Балістика на Київ", T0)
    assert len(ctx.db.pushes) == 1
    await ctx.handle_message("kyiv_nebo", "Ще Циркон на Київ", T0 + timedelta(seconds=10))
    assert len(ctx.push.sent) == 1

async def test_no_registered_devices_means_no_send_but_feed_records():
    ctx = make_ctx(devices=0)
    await ctx.handle_message("kyiv_nebo", "Балістика на Київ", T0)
    assert ctx.push.sent == []
    assert ctx.db.pushes == [
        ("kyiv_nebo", "ballistic", "inbound", "Балістика на Київ", False),
    ]

async def test_undelivered_push_keeps_cooldown_open_and_records_unpushed():
    ctx = make_ctx(delivered=0)
    await ctx.handle_message("kyiv_nebo", "Балістика на Київ", T0)
    assert ctx.db.pushes == [
        ("kyiv_nebo", "ballistic", "inbound", "Балістика на Київ", False),
    ]
    await ctx.handle_message("kyiv_nebo", "Ще Циркон на Київ", T0 + timedelta(seconds=10))
    assert len(ctx.push.sent) == 2

async def test_mentions_inside_cooldown_are_silent_but_feed_records():
    ctx = make_ctx()
    await ctx.handle_message("kyiv_nebo", "Швидкісна ціль на Київ!", T0)
    await ctx.handle_message("kyiv_nebo", "Ще балістика", T0 + timedelta(seconds=20))
    await ctx.handle_message("kyiv_nebo", "Балістика + Циркони", T0 + timedelta(seconds=40))
    assert len(ctx.push.sent) == 1
    assert [p[4] for p in ctx.db.pushes] == [True, False, False]

async def test_mention_after_cooldown_pushes_again():
    ctx = make_ctx()
    await ctx.handle_message("kyiv_nebo", "Швидкісна ціль на Київ!", T0)
    await ctx.handle_message("kyiv_nebo", "Ще балістика на Київ", T0 + timedelta(seconds=121))
    assert len(ctx.push.sent) == 2

async def test_warnings_are_silent_when_disabled():
    ctx = make_ctx(push_warnings=False)
    await ctx.handle_message("kyiv_nebo", "Загроза балістики з Курська", T0)
    assert ctx.push.sent == []
    assert ctx.db.pushes == []

async def test_warnings_push_when_enabled():
    ctx = make_ctx(push_warnings=True)
    await ctx.handle_message("kyiv_nebo", "Загроза балістики з Курська", T0)
    assert ctx.push.sent == ["Загроза балістики з Курська"]

async def test_non_ballistic_messages_are_dropped():
    ctx = make_ctx()
    await ctx.handle_message("kyiv_nebo", "Шахеди, курсом на Оболонь!", T0)
    await ctx.handle_message("kyiv_nebo", "Крилаті ракети на Київ", T0)
    assert ctx.push.sent == []

async def test_a_forecast_that_something_may_happen_is_not_an_alert():
    ctx = make_ctx()
    await ctx.handle_message("kyiv_nebo", "Балістика ще може бути", T0)
    await ctx.handle_message("kyiv_nebo", "Нагадую, ціль може не фіксуватися", T0)
    assert ctx.push.sent == []

async def test_absence_of_ballistics_is_not_an_alert():
    ctx = make_ctx()
    await ctx.handle_message("kyiv_nebo", "Балістики поки не видно", T0)
    await ctx.handle_message("kyiv_nebo", "Поки цілей більше не видно", T0)
    assert ctx.push.sent == []

async def test_safety_messages_are_dropped():
    ctx = make_ctx()
    await ctx.handle_message("kyiv_nebo", "Відбій. Цілі зникли.", T0)
    assert ctx.push.sent == []

async def test_repeat_inside_cooldown_is_silent():
    ctx = make_ctx()
    await ctx.handle_message("kyiv_nebo", "Швидкісна ціль на Київ!", T0)
    await ctx.handle_message("kyiv_nebo", "Швидкісна ціль на Київ!", T0 + timedelta(seconds=10))
    assert len(ctx.push.sent) == 1

async def test_identical_repeat_after_cooldown_pushes_again():
    ctx = make_ctx()
    await ctx.handle_message("kyiv_nebo", "Швидкісна ціль на Київ!", T0)
    await ctx.handle_message("kyiv_nebo", "Швидкісна ціль на Київ!", T0 + timedelta(seconds=121))
    assert len(ctx.push.sent) == 2

async def test_bare_target_pushes_inside_ballistic_context():
    ctx = make_ctx()
    await ctx.handle_message("kyiv_nebo", "Загроза балістики з Брянська", T0)
    await ctx.handle_message("kyiv_nebo", "Цілі", T0 + timedelta(seconds=12))
    assert ctx.push.sent == ["Цілі"]
    assert ctx.db.pushes == [("kyiv_nebo", "ballistic", "inbound", "Цілі", True)]

async def test_bare_target_without_context_is_silent():
    ctx = make_ctx()
    await ctx.handle_message("kyiv_nebo", "Цілі", T0)
    await ctx.handle_message("kyiv_nebo", "Підлітають", T0 + timedelta(seconds=30))
    assert ctx.push.sent == []

async def test_bare_target_is_silent_under_other_weapon_context():
    ctx = make_ctx()
    await ctx.handle_message("kyiv_nebo", "Загроза балістики з Брянська", T0)
    await ctx.handle_message("kyiv_nebo", "Це реактивні Шахеди", T0 + timedelta(minutes=1))
    await ctx.handle_message("kyiv_nebo", "Летять на Київ", T0 + timedelta(minutes=2))
    assert ctx.push.sent == []

async def test_safety_clears_ballistic_context():
    ctx = make_ctx()
    await ctx.handle_message("kyiv_nebo", "Загроза балістики з Брянська", T0)
    await ctx.handle_message("kyiv_nebo", "Відбій", T0 + timedelta(minutes=1))
    await ctx.handle_message("kyiv_nebo", "Ще цілі", T0 + timedelta(minutes=2))
    assert ctx.push.sent == []

async def test_ballistic_context_expires_after_ttl():
    ctx = make_ctx()
    await ctx.handle_message("kyiv_nebo", "Загроза балістики з Брянська", T0)
    await ctx.handle_message("kyiv_nebo", "Ще цілі", T0 + timedelta(minutes=21))
    assert ctx.push.sent == []



async def test_target_elsewhere_is_silent_in_context():
    ctx = make_ctx()
    await ctx.handle_message("kyiv_nebo", "Балістика", T0)
    ctx.push.sent.clear()
    ctx.db.pushes.clear()
    await ctx.handle_message("kyiv_nebo", "Ціль на Сумщині", T0 + timedelta(minutes=5))
    await ctx.handle_message("kyiv_nebo", "Ціль на Кременчук, не до нас", T0 + timedelta(minutes=6))
    await ctx.handle_message("kyiv_nebo", "Без фіксації цілей", T0 + timedelta(minutes=7))
    assert ctx.push.sent == []

async def test_cruise_launches_do_not_push_as_ballistic():
    ctx = make_ctx()
    await ctx.handle_message("kyiv_nebo", "Загроза балістики з Брянська", T0)
    await ctx.handle_message("kyiv_nebo", "Попередньо, пуски ракет із ТУшок", T0 + timedelta(minutes=1))
    await ctx.handle_message("kyiv_nebo", "Ще пуски Калібрів", T0 + timedelta(minutes=2))
    assert ctx.push.sent == []

async def test_cruise_context_suppresses_later_bare_targets():
    ctx = make_ctx()
    await ctx.handle_message("kyiv_nebo", "Балістика", T0)
    ctx.push.sent.clear()
    await ctx.handle_message("kyiv_nebo", "Пуски Калібрів", T0 + timedelta(minutes=1))
    await ctx.handle_message("kyiv_nebo", "Ще цілі", T0 + timedelta(minutes=5))
    assert ctx.push.sent == []

async def test_hypothetical_bare_wording_is_treated_as_warning():
    ctx = make_ctx()
    await ctx.handle_message("kyiv_nebo", "Балістика", T0)
    ctx.push.sent.clear()
    await ctx.handle_message("kyiv_nebo", "Ще можуть бути пуски", T0 + timedelta(minutes=5))
    assert ctx.push.sent == []

async def test_ballistic_after_drones_reopens_bare_pushes():
    ctx = make_ctx()
    await ctx.handle_message("kyiv_nebo", "Шахеди на Київ", T0)
    await ctx.handle_message("kyiv_nebo", "Загроза балістики з Брянська", T0 + timedelta(minutes=5))
    await ctx.handle_message("kyiv_nebo", "3 на Київ", T0 + timedelta(minutes=6))
    assert ctx.push.sent == ["3 на Київ"]

async def test_drones_after_ballistic_close_bare_pushes():
    ctx = make_ctx()
    await ctx.handle_message("kyiv_nebo", "Загроза балістики з Брянська", T0)
    await ctx.handle_message("kyiv_nebo", "Це реактивні Шахеди", T0 + timedelta(minutes=5))
    await ctx.handle_message("kyiv_nebo", "Підлітають з півдня", T0 + timedelta(minutes=6))
    assert ctx.push.sent == []

async def test_misspelled_zircon_is_recognised():
    ctx = make_ctx()
    await ctx.handle_message("kyiv_nebo", "Цикрони + С-400", T0)
    assert ctx.push.sent == ["Цикрони + С-400"]

async def test_one_cooldown_applies_to_weapon_and_context_alike():
    ctx = make_ctx()
    await ctx.handle_message("kyiv_nebo", "Балістика на Київ", T0)
    await ctx.handle_message("kyiv_nebo", "Ще цілі", T0 + timedelta(seconds=90))
    await ctx.handle_message("kyiv_nebo", "Ще Циркон на Київ", T0 + timedelta(seconds=110))
    assert len(ctx.push.sent) == 1
    await ctx.handle_message("kyiv_nebo", "Ще цілі", T0 + timedelta(seconds=121))
    assert len(ctx.push.sent) == 2
