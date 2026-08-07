import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.config import Config
from app.ingest import Ingest, parse_messages, stop, strip_html

CHANNEL = "kyiv_nebo"

PREVIEW_PAGE = """
<div class="tgme_widget_message_wrap">
 <div class="tgme_widget_message" data-post="kyiv_nebo/101">
  <div class="tgme_widget_message_text js-message_text" dir="auto">
   🔴 Балістика на <a href="/x">Київ</a>!<br/><br/>Прямуйте в укриття &amp; чекайте
  </div>
  <time datetime="2026-08-01T19:01:45+00:00">19:01</time>
 </div>
</div>
<div class="tgme_widget_message_wrap">
 <div class="tgme_widget_message" data-post="kyiv_nebo/102">
  <a class="tgme_widget_message_photo_wrap" href="x"></a>
  <time datetime="2026-08-01T19:02:00+00:00">19:02</time>
 </div>
</div>
<div class="tgme_widget_message_wrap">
 <div class="tgme_widget_message" data-post="kyiv_nebo/103">
  <div class="tgme_widget_message_text js-message_text" dir="auto">Відбій!</div>
  <time datetime="2026-08-01T19:30:00+00:00">19:30</time>
 </div>
</div>
"""


def make_config(**overrides) -> Config:
    values = dict(
        channels=[CHANNEL],
        database_url="postgresql://unused",
        apns_key_p8_b64="", apns_key_id="", apns_team_id="",
        apns_topic="t", apns_sandbox=True, api_key=None,
        critical_alerts=False, push_cooldown_sec=120, push_warnings=False,
        push_types=frozenset({"ballistic", "irbm"}),
        tg_api_id=1, tg_api_hash="hash", tg_session="session",
        catchup_sec=0.02, catchup_max_age_sec=300.0,
        fallback_after_sec=0.05, preview_poll_sec=0.02,
        context_ttl_min=20,
    )
    values.update(overrides)
    return Config(**values)


@dataclass
class FakeMessage:
    id: int
    message: str
    date: datetime = field(default_factory=lambda: datetime.now(UTC))


class FakeClient:
    def __init__(self, history: list[FakeMessage]) -> None:
        self.history = history
        self.handlers: list = []
        self.requests: list = []
        self.authorized = True
        self.disconnected = asyncio.Event()
        self._connected = False

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False
        self.disconnected.set()

    def is_connected(self) -> bool:
        return self._connected

    async def is_user_authorized(self) -> bool:
        return self.authorized

    async def get_me(self):
        return SimpleNamespace(username="tester", phone="+380", id=42)

    async def get_entity(self, name: str) -> str:
        return name

    async def __call__(self, request):
        self.requests.append(request)

    async def get_messages(self, entity, limit: int) -> list[FakeMessage]:
        return sorted(self.history, key=lambda m: m.id, reverse=True)[:limit]

    def add_event_handler(self, callback, event) -> None:
        self.handlers.append(callback)

    async def run_until_disconnected(self) -> None:
        await self.disconnected.wait()

    async def fire(self, message: FakeMessage) -> None:
        self.history.append(message)
        for callback in self.handlers:
            await callback(SimpleNamespace(message=message))


async def until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition never became true")


class Harness:
    def __init__(self, history: list[FakeMessage], config: Config) -> None:
        self.client = FakeClient(history)
        self.received: list[tuple[str, str, datetime]] = []
        self.ingest = Ingest(config, self._on_message, client_factory=lambda: self.client)
        self._task: asyncio.Task | None = None

    async def _on_message(self, source: str, text: str, ts: datetime) -> None:
        self.received.append((source, text, ts))

    async def __aenter__(self) -> "Harness":
        self._task = asyncio.create_task(self.ingest.run())
        await until(lambda: bool(self.client.handlers))
        return self

    async def __aexit__(self, *exc) -> None:
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass


@pytest.fixture
def history() -> list[FakeMessage]:
    return [FakeMessage(id=i, message=f"старе {i}") for i in (98, 99, 100)]


def test_preview_parsing_extracts_ids_text_and_time():
    messages = parse_messages(PREVIEW_PAGE)
    assert [m[0] for m in messages] == [101, 103]
    assert "Балістика на Київ!" in messages[0][1]
    assert "\n\nПрямуйте в укриття & чекайте" in messages[0][1]
    assert messages[0][2].isoformat() == "2026-08-01T19:01:45+00:00"
    assert messages[1][1] == "Відбій!"


def test_strip_html_keeps_emoji_and_plain_text():
    assert strip_html('🛵 <i class="emoji"><b>⚡</b></i> шахед') == "🛵 ⚡ шахед"


async def test_cold_start_does_not_replay_history(history):
    async with Harness(history, make_config()) as h:
        await asyncio.sleep(0.1)
        assert h.received == []


async def test_channel_is_joined_on_start(history):
    async with Harness(history, make_config()) as h:
        assert [type(r).__name__ for r in h.client.requests] == ["JoinChannelRequest"]


async def test_live_message_is_delivered(history):
    async with Harness(history, make_config()) as h:
        ts = datetime.now(UTC)
        await h.client.fire(FakeMessage(id=101, message="Балістика на Київ", date=ts))
        assert h.received == [(CHANNEL, "Балістика на Київ", ts)]


async def test_live_message_is_not_redelivered_by_catchup(history):
    async with Harness(history, make_config()) as h:
        await h.client.fire(FakeMessage(id=101, message="Балістика на Київ"))
        await asyncio.sleep(0.1)
        assert len(h.received) == 1


async def test_repeated_update_for_one_message_is_delivered_once(history):
    async with Harness(history, make_config()) as h:
        message = FakeMessage(id=101, message="Балістика на Київ")
        await h.client.fire(message)
        await h.client.fire(message)
        assert len(h.received) == 1


async def test_catchup_recovers_a_message_updates_missed(history):
    async with Harness(history, make_config()) as h:
        history.append(FakeMessage(id=101, message="Циркони"))
        await until(lambda: len(h.received) == 1)
        assert h.received[0][1] == "Циркони"


async def test_catchup_delivers_missed_messages_in_order(history):
    async with Harness(history, make_config()) as h:
        history.append(FakeMessage(id=102, message="друге"))
        history.append(FakeMessage(id=101, message="перше"))
        await until(lambda: len(h.received) == 2)
        assert [m[1] for m in h.received] == ["перше", "друге"]


async def test_stale_message_is_skipped(history):
    async with Harness(history, make_config()) as h:
        old = datetime.now(UTC) - timedelta(minutes=30)
        await h.client.fire(FakeMessage(id=101, message="Балістика", date=old))
        await asyncio.sleep(0.1)
        assert h.received == []


async def test_message_within_max_age_is_delivered(history):
    async with Harness(history, make_config()) as h:
        recent = datetime.now(UTC) - timedelta(minutes=2)
        await h.client.fire(FakeMessage(id=101, message="Балістика", date=recent))
        assert len(h.received) == 1


async def test_media_without_caption_is_skipped(history):
    async with Harness(history, make_config()) as h:
        await h.client.fire(FakeMessage(id=101, message=""))
        await asyncio.sleep(0.1)
        assert h.received == []


async def test_naive_timestamp_is_treated_as_utc(history):
    async with Harness(history, make_config()) as h:
        naive = datetime.now(UTC).replace(tzinfo=None)
        await h.client.fire(FakeMessage(id=101, message="Балістика", date=naive))
        assert h.received[0][2].tzinfo is UTC


async def test_health_reports_mtproto_while_running(history):
    async with Harness(history, make_config()) as h:
        assert h.ingest.source == "mtproto"
        assert h.ingest.last_message_at == {}
        await h.client.fire(FakeMessage(id=101, message="Балістика"))
        assert CHANNEL in h.ingest.last_message_at


async def test_health_reports_nothing_after_the_client_drops(history):
    async with Harness(history, make_config(fallback_after_sec=1000.0)) as h:
        await h.client.disconnect()
        await until(lambda: h.ingest.source is None)
        assert not h.ingest.connected


async def test_reconnect_keeps_the_cursor_and_recovers_the_gap(history, monkeypatch):
    monkeypatch.setattr("app.ingest.RESTART_DELAY", 0.02)
    async with Harness(history, make_config(catchup_sec=100.0)) as h:
        history.append(FakeMessage(id=101, message="під час розриву"))
        await asyncio.sleep(0.05)
        assert h.received == []
        h.ingest.listener._client_factory = lambda: FakeClient(history)
        await h.client.disconnect()
        await until(lambda: len(h.received) == 1)
        assert h.received[0][1] == "під час розриву"


async def test_unauthorised_session_does_not_crash_the_task(history):
    client = FakeClient(history)
    client.authorized = False
    ingest = Ingest(
        make_config(fallback_after_sec=1000.0),
        lambda *a: asyncio.sleep(0),
        client_factory=lambda: client,
    )
    task = asyncio.create_task(ingest.run())
    await asyncio.sleep(0.1)
    assert not task.done()
    assert not ingest.connected
    await stop(task)


class FakePreviewResponse:
    def __init__(self, page: str) -> None:
        self.text = page

    def raise_for_status(self) -> None:
        pass


class FakeHTTP:
    pages: list[str] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self) -> "FakeHTTP":
        return self

    async def __aexit__(self, *exc) -> None:
        pass

    async def get(self, url: str) -> FakePreviewResponse:
        return FakePreviewResponse(FakeHTTP.pages[-1])


def preview_page(rows: list[tuple[int, str]]) -> str:
    now = datetime.now(UTC).isoformat()
    return "".join(
        f'<div class="tgme_widget_message" data-post="kyiv_nebo/{msg_id}">'
        f'<div class="tgme_widget_message_text js-message_text">{text}</div>'
        f'<time datetime="{now}"></time></div>'
        for msg_id, text in rows
    )


@pytest.fixture
def preview(monkeypatch):
    FakeHTTP.pages = [preview_page([(98, "старе 98"), (99, "старе 99"), (100, "старе 100")])]
    monkeypatch.setattr("app.ingest.httpx.AsyncClient", FakeHTTP)
    return FakeHTTP


async def test_fallback_starts_when_mtproto_stays_down(history, preview, monkeypatch):
    monkeypatch.setattr("app.ingest.RESTART_DELAY", 1000.0)
    monkeypatch.setattr("app.ingest.HEALTH_CHECK_SEC", 0.01)
    async with Harness(history, make_config()) as h:
        await h.client.disconnect()
        await until(lambda: h.ingest.source == "preview")
        preview.pages.append(preview_page([(101, "Балістика на Київ")]))
        await until(lambda: len(h.received) == 1)
        assert h.received[0][1] == "Балістика на Київ"


async def test_fallback_does_not_start_while_mtproto_is_healthy(history, preview, monkeypatch):
    monkeypatch.setattr("app.ingest.HEALTH_CHECK_SEC", 0.01)
    async with Harness(history, make_config()) as h:
        await asyncio.sleep(0.2)
        assert h.ingest.source == "mtproto"
        preview.pages.append(preview_page([(101, "Балістика на Київ")]))
        await asyncio.sleep(0.1)
        assert h.received == []


async def test_fallback_does_not_redeliver_what_mtproto_already_sent(
    history, preview, monkeypatch,
):
    monkeypatch.setattr("app.ingest.RESTART_DELAY", 1000.0)
    monkeypatch.setattr("app.ingest.HEALTH_CHECK_SEC", 0.01)
    async with Harness(history, make_config()) as h:
        await h.client.fire(FakeMessage(id=101, message="Балістика на Київ"))
        assert len(h.received) == 1
        await h.client.disconnect()
        await until(lambda: h.ingest.source == "preview")
        preview.pages.append(preview_page([(101, "Балістика на Київ")]))
        await asyncio.sleep(0.15)
        assert len(h.received) == 1


async def test_fallback_stops_once_mtproto_recovers(history, preview, monkeypatch):
    monkeypatch.setattr("app.ingest.RESTART_DELAY", 0.02)
    monkeypatch.setattr("app.ingest.HEALTH_CHECK_SEC", 0.01)
    async with Harness(history, make_config()) as h:
        broken = FakeClient(history)
        broken.authorized = False
        h.ingest.listener._client_factory = lambda: broken
        await h.client.disconnect()
        await until(lambda: h.ingest.source == "preview")

        h.ingest.listener._client_factory = lambda: FakeClient(history)
        await until(lambda: h.ingest.source == "mtproto")
        await until(lambda: h.ingest._poller_task is None)
