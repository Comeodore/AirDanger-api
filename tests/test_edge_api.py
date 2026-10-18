import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from app.api import EDGE_SILENT_SEC, router
from test_pipeline import FakeDB, make_ctx

KEY = "edge-secret"


class EdgeDB(FakeDB):
    def __init__(self, devices: int = 1) -> None:
        super().__init__(devices=devices, la_devices=1)
        self.deleted: list[str] = []
        self.keys: set[tuple] = set()

    async def has_push(self, channel, type_, ts, text):
        return (channel, type_, ts, text) in self.keys

    async def insert_push(self, channel, type_, severity, text, ts, pushed=True):
        await super().insert_push(channel, type_, severity, text, ts, pushed=pushed)
        self.keys.add((channel, type_, ts, text))

    async def delete_device(self, token):
        self.deleted.append(token)


def make_app(ctx) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.ctx = ctx
    return app


def client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def edge_ctx(edge_key: str = KEY):
    ctx = make_ctx(push_warnings=True, la_devices=1)
    ctx.db = EdgeDB()
    ctx.config.edge_key = edge_key
    return ctx


async def test_edge_routes_reject_a_missing_or_wrong_key():
    app = make_app(edge_ctx())
    async with client(app) as http:
        assert (await http.get("/edge/devices")).status_code == 401
        assert (await http.get("/edge/devices", headers={"x-edge-key": "nope"})).status_code == 401


async def test_edge_routes_are_closed_when_no_key_is_configured():
    app = make_app(edge_ctx(edge_key=""))
    async with client(app) as http:
        assert (await http.get("/edge/devices", headers={"x-edge-key": ""})).status_code == 401


async def test_edge_gets_the_device_list():
    app = make_app(edge_ctx())
    async with client(app) as http:
        response = await http.get("/edge/devices", headers={"x-edge-key": KEY})
    assert response.status_code == 200
    assert len(response.json()["devices"]) == 1


async def test_a_reported_alert_lands_in_the_feed_once_and_starts_a_live_activity():
    ctx = edge_ctx()
    app = make_app(ctx)
    now = datetime.now(UTC)
    decision = {"channel": "kpszsu", "type": "ballistic", "severity": "inbound",
                "text": "Балістика на Київ", "push_text": "Балістика на Київ",
                "ts": now.isoformat(), "pushed": True}
    async with client(app) as http:
        first = await http.post("/edge/report", headers={"x-edge-key": KEY},
                                json={"decisions": [decision], "dead_tokens": ["AA" * 32]})
        again = await http.post("/edge/report", headers={"x-edge-key": KEY},
                                json={"decisions": [decision]})
    assert first.json() == {"stored": 1, "removed": 1}
    assert again.json() == {"stored": 0, "removed": 0}
    assert ctx.db.pushes == [("kpszsu", "ballistic", "inbound", "Балістика на Київ", True)]
    assert ctx.db.deleted == ["aa" * 32]
    assert [event for event, _, _ in ctx.push.activity] == ["start"]


async def test_a_wrong_key_of_the_same_length_is_rejected():
    app = make_app(edge_ctx())
    async with client(app) as http:
        wrong = "x" * len(KEY)
        assert (await http.get("/edge/devices", headers={"x-edge-key": wrong})).status_code == 401


async def test_two_posts_in_the_same_second_both_reach_the_feed():
    ctx = edge_ctx()
    now = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=10)
    first = await ctx.record_edge_decision(
        "war_monitor", "ballistic", "inbound", "☄ Вихід балістики на Київ", "", now, True)
    second = await ctx.record_edge_decision(
        "war_monitor", "ballistic", "inbound", "‼️ Балістика на Київ", "", now, True)
    again = await ctx.record_edge_decision(
        "war_monitor", "ballistic", "inbound", "‼️ Балістика на Київ", "", now, True)
    assert (first, second, again) == (True, True, False)
    assert len(ctx.db.pushes) == 2


async def test_a_report_with_an_unknown_type_or_a_bad_token_is_refused():
    app = make_app(edge_ctx())
    now = datetime.now(UTC).isoformat()
    bad_type = {"channel": "kpszsu", "type": "drone", "severity": "inbound",
                "text": "x", "ts": now, "pushed": True}
    async with client(app) as http:
        typed = await http.post("/edge/report", headers={"x-edge-key": KEY},
                                json={"decisions": [bad_type]})
        tokened = await http.post("/edge/report", headers={"x-edge-key": KEY},
                                  json={"dead_tokens": ["not-a-token"]})
    assert typed.status_code == 422
    assert tokened.status_code == 422


async def test_an_old_reported_alert_fills_the_feed_without_a_live_activity():
    ctx = edge_ctx()
    old = datetime.now(UTC) - timedelta(minutes=10)
    stored = await ctx.record_edge_decision(
        "kpszsu", "ballistic", "inbound", "Балістика на Київ", "", old, True)
    assert stored
    assert ctx.db.pushes == [("kpszsu", "ballistic", "inbound", "Балістика на Київ", True)]
    assert ctx.push.activity == []


async def test_a_reported_all_clear_ends_the_live_activity():
    ctx = edge_ctx()
    now = datetime.now(UTC)
    await ctx.record_edge_decision("kpszsu", "ballistic", "inbound", "Балістика на Київ",
                                   "Балістика на Київ", now - timedelta(minutes=1), True)
    await ctx.record_edge_decision("kpszsu", "all_clear", "clear", "Відбій", "", now, True)
    assert [event for event, _, _ in ctx.push.activity] == ["start", "end"]


async def health_of(ctx) -> dict:
    async with client(make_app(ctx)) as http:
        return (await http.get("/health")).json()


def edge_only_ctx(started_ago: float = 0.0):
    ctx = edge_ctx()
    ctx.config.ingest_enabled = False
    ctx.started_at = time.time() - started_ago
    return ctx


async def test_health_does_not_degrade_when_ingest_is_switched_off():
    body = await health_of(edge_only_ctx())
    assert body["status"] == "ok"
    assert body["ingest"] == "off"


async def test_health_gives_the_edge_five_minutes_after_a_restart():
    body = await health_of(edge_only_ctx(started_ago=EDGE_SILENT_SEC - 10))
    assert (body["status"], body["edge"], body["edge_last_seen_sec"]) == ("ok", "waiting", None)


async def test_health_degrades_when_the_edge_never_comes():
    body = await health_of(edge_only_ctx(started_ago=EDGE_SILENT_SEC + 10))
    assert (body["status"], body["edge"]) == ("degraded", "silent")


async def test_an_edge_visit_keeps_health_green():
    ctx = edge_only_ctx(started_ago=3600)
    async with client(make_app(ctx)) as http:
        await http.get("/edge/devices", headers={"x-edge-key": KEY})
        body = (await http.get("/health")).json()
    assert (body["status"], body["edge"], body["edge_last_seen_sec"]) == ("ok", "ok", 0)


async def test_a_report_counts_as_a_visit():
    ctx = edge_only_ctx(started_ago=3600)
    async with client(make_app(ctx)) as http:
        await http.post("/edge/report", headers={"x-edge-key": KEY}, json={})
    assert (await health_of(ctx))["edge"] == "ok"


async def test_health_degrades_after_five_silent_minutes():
    ctx = edge_only_ctx(started_ago=3600)
    ctx.edge_seen_at = time.time() - EDGE_SILENT_SEC - 1
    body = await health_of(ctx)
    assert (body["status"], body["edge"]) == ("degraded", "silent")
    assert body["edge_last_seen_sec"] >= EDGE_SILENT_SEC


async def test_a_wrong_key_is_not_a_visit():
    ctx = edge_only_ctx(started_ago=3600)
    async with client(make_app(ctx)) as http:
        await http.get("/edge/devices", headers={"x-edge-key": "nope"})
    assert (await health_of(ctx))["status"] == "degraded"


async def test_a_silent_edge_does_not_degrade_home_polling():
    ctx = edge_ctx()
    ctx.started_at = time.time() - 3600
    ctx.ingest = SimpleNamespace(connected=True, last_message_at={},
                                 channel_state=lambda channel: "ok")
    body = await health_of(ctx)
    assert (body["status"], body["edge"]) == ("ok", "silent")
