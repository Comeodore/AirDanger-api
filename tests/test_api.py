from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from app.api import router

T0 = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


class FakeDB:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.limits: list[int] = []

    async def recent_pushes(self, limit: int) -> list[dict]:
        self.limits.append(limit)
        return self.rows[:limit]


def make_app(rows: list[dict], api_key: str | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.ctx = SimpleNamespace(
        config=SimpleNamespace(api_key=api_key),
        db=FakeDB(rows),
    )
    return app


def make_client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    )


def make_rows(count: int = 2) -> list[dict]:
    return [
        {
            "channel": "kyiv_nebo" if i % 2 == 0 else "war_monitor",
            "type": "ballistic",
            "severity": "inbound" if i % 2 == 0 else "warning",
            "text": f"Балістика на Київ {i}",
            "ts": T0 - timedelta(minutes=i),
        }
        for i in range(count)
    ]


async def test_alerts_returns_recent_pushes():
    app = make_app(make_rows(2))
    async with make_client(app) as client:
        response = await client.get("/alerts")
    assert response.status_code == 200
    alerts = response.json()["alerts"]
    assert len(alerts) == 2
    assert alerts[0] == {
        "channel": "kyiv_nebo",
        "type": "ballistic",
        "severity": "inbound",
        "text": "Балістика на Київ 0",
        "ts": "2026-08-01T12:00:00Z",
    }
    assert alerts[1]["severity"] == "warning"
    assert alerts[1]["channel"] == "war_monitor"

async def test_alerts_empty():
    app = make_app([])
    async with make_client(app) as client:
        response = await client.get("/alerts")
    assert response.status_code == 200
    assert response.json() == {"alerts": []}

async def test_alerts_default_limit_is_50():
    app = make_app([])
    async with make_client(app) as client:
        await client.get("/alerts")
    assert app.state.ctx.db.limits == [50]

async def test_alerts_limit_param_is_passed_through():
    app = make_app(make_rows(5))
    async with make_client(app) as client:
        response = await client.get("/alerts", params={"limit": 3})
    assert app.state.ctx.db.limits == [3]
    assert len(response.json()["alerts"]) == 3

async def test_alerts_limit_is_validated():
    app = make_app([])
    async with make_client(app) as client:
        assert (await client.get("/alerts", params={"limit": 0})).status_code == 422
        assert (await client.get("/alerts", params={"limit": 201})).status_code == 422
        assert (await client.get("/alerts", params={"limit": "abc"})).status_code == 422
    assert app.state.ctx.db.limits == []

async def test_alerts_requires_api_key_when_configured():
    app = make_app([], api_key="secret")
    async with make_client(app) as client:
        assert (await client.get("/alerts")).status_code == 401
        good = await client.get("/alerts", headers={"X-API-Key": "secret"})
    assert good.status_code == 200
