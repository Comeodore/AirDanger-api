import asyncio
import time
from datetime import UTC, datetime, timedelta

import pytest

from app.config import Config
from app.ingest import Ingest, parse_messages, strip_html

CHANNEL = "kyiv_nebo"

SAMPLE = """
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
        critical_alerts=False, push_cooldown_sec=120, push_escalation=True, push_warnings=False,
        push_types=frozenset({"ballistic", "irbm"}),
        poll_sec=0.02, max_age_sec=300.0, health_window_sec=0.2,
        context_ttl_min=20,
    )
    values.update(overrides)
    return Config(**values)


def page(rows: list[tuple[int, str]], fresh: bool = True) -> str:
    when = datetime.now(UTC) if fresh else datetime.now(UTC) - timedelta(hours=1)
    return "".join(
        f'<div class="tgme_widget_message" data-post="kyiv_nebo/{msg_id}">'
        f'<div class="tgme_widget_message_text js-message_text">{text}</div>'
        f'<time datetime="{when.isoformat()}"></time></div>'
        for msg_id, text in rows
    )


class FakeResponse:
    def __init__(self, body: str, status: int = 200, url: str = "") -> None:
        self.text = body
        self.status = status
        self.url = url

    def raise_for_status(self) -> None:
        if self.status != 200:
            raise RuntimeError(f"HTTP {self.status}")


class FakeHTTP:
    body = ""
    status = 200
    hits = 0
    fail_substring: str | None = None
    redirect_to: str | None = None
    urls: list[str] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self) -> "FakeHTTP":
        return self

    async def __aexit__(self, *exc) -> None:
        pass

    async def get(self, url: str) -> FakeResponse:
        FakeHTTP.hits += 1
        FakeHTTP.urls.append(url)
        if FakeHTTP.fail_substring and FakeHTTP.fail_substring in url:
            return FakeResponse(FakeHTTP.body, 503, url)
        return FakeResponse(FakeHTTP.body, FakeHTTP.status,
                            FakeHTTP.redirect_to or url)


async def until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition never became true")


class Harness:
    def __init__(self, config: Config) -> None:
        self.received: list[tuple[str, str, datetime]] = []
        self.ingest = Ingest(config, self._on_message)
        self._task: asyncio.Task | None = None

    async def _on_message(self, source: str, text: str, ts: datetime) -> None:
        self.received.append((source, text, ts))

    async def __aenter__(self) -> "Harness":
        self._task = asyncio.create_task(self.ingest.run())
        await until(lambda: CHANNEL in self.ingest._seen)
        return self

    async def __aexit__(self, *exc) -> None:
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)


@pytest.fixture
def http(monkeypatch):
    FakeHTTP.body = page([(98, "старе 98"), (99, "старе 99"), (100, "старе 100")])
    FakeHTTP.status = 200
    FakeHTTP.hits = 0
    FakeHTTP.fail_substring = None
    FakeHTTP.redirect_to = None
    FakeHTTP.urls = []
    monkeypatch.setattr("app.ingest.httpx.AsyncClient", FakeHTTP)
    return FakeHTTP


def test_parsing_extracts_ids_text_and_time():
    messages = parse_messages(SAMPLE)
    assert [m[0] for m in messages] == [101, 103]
    assert "Балістика на Київ!" in messages[0][1]
    assert "\n\nПрямуйте в укриття & чекайте" in messages[0][1]
    assert messages[0][2].isoformat() == "2026-08-01T19:01:45+00:00"
    assert messages[1][1] == "Відбій!"


def test_strip_html_keeps_emoji_and_plain_text():
    assert strip_html('🛵 <i class="emoji"><b>⚡</b></i> шахед') == "🛵 ⚡ шахед"


def test_markup_without_message_blocks_parses_to_nothing():
    assert parse_messages("<html><body>nothing here</body></html>") == []


async def test_cold_start_does_not_replay_what_is_already_on_the_page(http):
    async with Harness(make_config()) as h:
        hits = http.hits
        await until(lambda: http.hits >= hits + 3)
        assert h.received == []


async def test_a_new_message_is_delivered(http):
    async with Harness(make_config()) as h:
        http.body = page([(100, "старе 100"), (101, "Балістика на Київ")])
        await until(lambda: len(h.received) == 1)
        assert h.received[0][1] == "Балістика на Київ"


async def test_a_message_is_delivered_only_once(http):
    async with Harness(make_config()) as h:
        http.body = page([(101, "Балістика на Київ")])
        await until(lambda: len(h.received) == 1)
        hits = http.hits
        await until(lambda: http.hits >= hits + 3)
        assert len(h.received) == 1


async def test_messages_are_delivered_in_order(http):
    async with Harness(make_config()) as h:
        http.body = page([(102, "друге"), (101, "перше")])
        await until(lambda: len(h.received) == 2)
        assert [m[1] for m in h.received] == ["перше", "друге"]


async def test_a_stale_message_is_skipped(http):
    async with Harness(make_config()) as h:
        http.body = page([(101, "Балістика")], fresh=False)
        hits = http.hits
        await until(lambda: http.hits >= hits + 3)
        assert h.received == []


async def test_a_message_the_pipeline_choked_on_is_retried(http):
    async with Harness(make_config()) as h:
        failures = []
        original = h.ingest._on_message

        async def flaky(source, text, ts):
            if not failures:
                failures.append(text)
                raise ConnectionError("database is down")
            await original(source, text, ts)

        h.ingest._on_message = flaky
        http.body = page([(101, "Балістика на Київ")])
        await until(lambda: len(h.received) == 1)


async def test_health_is_green_while_the_page_yields_messages(http):
    async with Harness(make_config()) as h:
        assert h.ingest.connected


async def test_health_goes_red_when_the_page_stops_yielding_messages(http):
    async with Harness(make_config()) as h:
        http.body = "<html><body>markup changed</body></html>"
        await until(lambda: not h.ingest.connected)


async def test_a_short_burst_of_failures_is_not_logged(http, caplog):
    async with Harness(make_config()) as h:
        with caplog.at_level("INFO"):
            http.status = 503
            hits = http.hits
            await until(lambda: http.hits >= hits + 5)
            http.status = 200
            hits = http.hits
            await until(lambda: http.hits >= hits + 3)
            assert not any("unreachable" in r.message for r in caplog.records)
            assert not any("reachable again" in r.message for r in caplog.records)


async def test_a_lasting_outage_is_logged_once_and_recovery_reported(
    http, monkeypatch, caplog,
):
    monkeypatch.setattr("app.ingest.OUTAGE_WARN_SEC", 0.05)
    async with Harness(make_config()) as h:
        with caplog.at_level("INFO"):
            http.status = 503
            await until(lambda: any("unreachable" in r.message
                                    for r in caplog.records))
            hits = http.hits
            await until(lambda: http.hits >= hits + 5)
            outages = [r for r in caplog.records if "unreachable" in r.message]
            assert len(outages) == 1
            assert outages[0].levelname == "WARNING"
            assert outages[0].message.startswith(CHANNEL)

            http.status = 200
            await until(lambda: any("reachable again" in r.message
                                    for r in caplog.records))
            recovery = next(r for r in caplog.records if "reachable again" in r.message)
            assert "polls failed" in recovery.message


async def test_a_long_outage_escalates_to_error(http, monkeypatch, caplog):
    monkeypatch.setattr("app.ingest.OUTAGE_WARN_SEC", 0.02)
    monkeypatch.setattr("app.ingest.OUTAGE_ERROR_SEC", 0.1)
    async with Harness(make_config()) as h:
        with caplog.at_level("INFO"):
            http.status = 503
            await until(lambda: any(r.levelname == "ERROR"
                                    and "STILL unreachable" in r.message
                                    for r in caplog.records))


async def test_failures_are_attributed_to_the_failing_channel(monkeypatch, caplog):
    FakeHTTP.body = page([(100, "старе 100")])
    FakeHTTP.status = 200
    FakeHTTP.hits = 0
    FakeHTTP.fail_substring = None
    monkeypatch.setattr("app.ingest.httpx.AsyncClient", FakeHTTP)
    monkeypatch.setattr("app.ingest.OUTAGE_WARN_SEC", 0.05)
    async with Harness(make_config(channels=[CHANNEL, "war_monitor"])) as h:
        await until(lambda: "war_monitor" in h.ingest._seen)
        with caplog.at_level("INFO"):
            FakeHTTP.fail_substring = "war_monitor"
            await until(lambda: any("unreachable" in r.message
                                    for r in caplog.records))
            FakeHTTP.fail_substring = None
            await until(lambda: any("reachable again" in r.message
                                    for r in caplog.records))
            touched = [r.message for r in caplog.records
                       if "unreachable" in r.message or "reachable again" in r.message]
            assert touched and all(m.startswith("war_monitor") for m in touched)


async def test_health_goes_red_when_the_page_stops_answering(http):
    async with Harness(make_config()) as h:
        http.status = 503
        await until(lambda: not h.ingest.connected)


async def test_a_broken_page_is_reported_once_it_has_lasted(http, monkeypatch, caplog):
    monkeypatch.setattr("app.ingest.BLIND_WARN_SEC", 0.05)
    async with Harness(make_config()) as h:
        http.body = "<html><body>markup changed</body></html>"
        with caplog.at_level("ERROR"):
            await until(lambda: any("markup has probably changed" in r.message
                                    for r in caplog.records))


async def test_recovery_after_a_broken_page_turns_health_green_again(http, monkeypatch):
    monkeypatch.setattr("app.ingest.BLIND_WARN_SEC", 0.05)
    async with Harness(make_config()) as h:
        http.body = "<html><body>markup changed</body></html>"
        await until(lambda: not h.ingest.connected)
        http.body = page([(100, "старе 100")])
        await until(lambda: h.ingest.connected)


async def test_the_page_breaking_a_second_time_is_reported_again(http, monkeypatch, caplog):
    monkeypatch.setattr("app.ingest.BLIND_WARN_SEC", 0.05)
    broken = "<html><body>markup changed</body></html>"
    healthy = page([(100, "старе 100")])
    async with Harness(make_config()) as h:
        with caplog.at_level("ERROR"):
            http.body = broken
            await until(lambda: sum("markup has probably changed" in r.message
                                    for r in caplog.records) == 1)
            http.body = healthy
            hits = http.hits
            await until(lambda: http.hits >= hits + 3 and h.ingest.connected)
            http.body = broken
            await until(lambda: sum("markup has probably changed" in r.message
                                    for r in caplog.records) == 2)


async def test_polling_keeps_to_its_interval(http):
    async with Harness(make_config(poll_sec=0.3)) as h:
        http.hits = 0
        await asyncio.sleep(1.2)
        assert 3 <= http.hits <= 6


async def test_a_revoked_preview_is_reported_once_and_recovery_noted(http, caplog):
    async with Harness(make_config()) as h:
        with caplog.at_level("INFO"):
            http.redirect_to = f"https://t.me/{CHANNEL}"
            await until(lambda: any("preview is gone or revoked" in r.message
                                    for r in caplog.records))
            hits = http.hits
            await until(lambda: http.hits >= hits + 3)
            gone = [r for r in caplog.records if "gone or revoked" in r.message]
            assert len(gone) == 1
            assert gone[0].levelname == "ERROR"
            await until(lambda: not h.ingest.connected)

            http.redirect_to = None
            await until(lambda: any("preview is back" in r.message
                                    for r in caplog.records))
            assert h.ingest.connected


async def test_channel_state_reports_an_outage_and_recovers(http, monkeypatch):
    monkeypatch.setattr("app.ingest.OUTAGE_WARN_SEC", 0.05)
    async with Harness(make_config()) as h:
        assert h.ingest.channel_state(CHANNEL) == "ok"
        http.status = 503
        await until(lambda: h.ingest.channel_state(CHANNEL) == "unreachable")
        http.status = 200
        await until(lambda: h.ingest.channel_state(CHANNEL) == "ok")


async def test_channel_state_reports_a_blind_page(http, monkeypatch):
    monkeypatch.setattr("app.ingest.BLIND_WARN_SEC", 0.05)
    async with Harness(make_config()) as h:
        http.body = "<html><body>markup changed</body></html>"
        await until(lambda: h.ingest.channel_state(CHANNEL) == "blind")


async def test_channel_state_reports_a_revoked_preview(http):
    async with Harness(make_config()) as h:
        http.redirect_to = f"https://t.me/{CHANNEL}"
        await until(lambda: h.ingest.channel_state(CHANNEL) == "no_preview")


async def test_failing_polls_fall_back_to_the_mirror_host(http):
    async with Harness(make_config()) as h:
        http.urls.clear()
        http.status = 503
        await until(lambda: any("telegram.me" in u for u in http.urls))
        http.status = 200
        hits = http.hits
        await until(lambda: http.hits >= hits + 2)
        assert any("//t.me/" in u for u in http.urls)
