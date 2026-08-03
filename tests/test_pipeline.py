from datetime import UTC, datetime, timedelta

from app.config import Config
from app.danger_service import DangerService
from app.dedup import TTLSet
from app.main import AppContext
from app.state import PushLedger

T0 = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def make_config(push_warnings: bool = False) -> Config:
    return Config(
        channels=["kyiv_nebo"],
        database_url="postgresql://unused",
        apns_key_p8_b64="", apns_key_id="", apns_team_id="",
        apns_topic="t", apns_sandbox=True, api_key=None,
        critical_alerts=False,
        push_cooldown_sec=60,
        push_warnings=push_warnings,
        push_types=frozenset({"ballistic", "irbm"}),
        poll_sec=5.0,
    )


class FakeDB:
    def __init__(self) -> None:
        self.pushes: list[tuple] = []

    async def insert_push(self, channel, type_, severity, text, ts):
        self.pushes.append((channel, type_, severity, text))

    async def tokens(self):
        return ["aa" * 32]

class FakePush:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_detection(self, tokens, threat, ts, source=None):
        self.sent.append(threat.text)

def make_ctx(push_warnings: bool = False) -> AppContext:
    return AppContext(
        config=make_config(push_warnings),
        db=FakeDB(),
        danger=DangerService(),
        ledger=PushLedger(cooldown=timedelta(seconds=60)),
        push=FakePush(),
        ingest=None,
        dedup=TTLSet(ttl_seconds=60),
    )

async def test_ballistic_mention_pushes_and_is_recorded():
    ctx = make_ctx()
    await ctx.handle_message("kyiv_nebo", "Швидкісна ціль на Київ!", T0)
    assert ctx.push.sent == ["Швидкісна ціль на Київ!"]
    assert ctx.db.pushes == [
        ("kyiv_nebo", "ballistic", "inbound", "Швидкісна ціль на Київ!"),
    ]

async def test_mentions_inside_cooldown_are_silent():
    ctx = make_ctx()
    await ctx.handle_message("kyiv_nebo", "Швидкісна ціль на Київ!", T0)
    await ctx.handle_message("kyiv_nebo", "Ще балістика", T0 + timedelta(seconds=20))
    await ctx.handle_message("kyiv_nebo", "Балістика + Циркони", T0 + timedelta(seconds=40))
    assert len(ctx.push.sent) == 1
    assert len(ctx.db.pushes) == 1

async def test_mention_after_cooldown_pushes_again():
    ctx = make_ctx()
    await ctx.handle_message("kyiv_nebo", "Швидкісна ціль на Київ!", T0)
    await ctx.handle_message("kyiv_nebo", "Ще балістика на Київ", T0 + timedelta(seconds=61))
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

async def test_safety_messages_are_dropped():
    ctx = make_ctx()
    await ctx.handle_message("kyiv_nebo", "Відбій. Цілі зникли.", T0)
    assert ctx.push.sent == []

async def test_exact_duplicate_is_deduped():
    ctx = make_ctx()
    await ctx.handle_message("kyiv_nebo", "Швидкісна ціль на Київ!", T0)
    await ctx.handle_message("kyiv_nebo", "Швидкісна ціль на Київ!", T0 + timedelta(seconds=10))
    assert len(ctx.push.sent) == 1
