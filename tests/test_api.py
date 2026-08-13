from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from app.api import router

T0 = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


class FakeDB:
    def __init__(self, rows: list[dict], known_tokens: tuple[str, ...] = ()) -> None:
        self.rows = rows
        self.calls: list[tuple] = []
        self.prefs: list[tuple] = []
        self.known = set(known_tokens)

    async def recent_pushes(self, limit: int, before: int | None = None) -> list[dict]:
        self.calls.append((limit, before))
        rows = self.rows
        if before is not None:
            rows = [r for r in rows if r["id"] < before]
        return rows[:limit]

    async def update_device_prefs(self, token, warnings=None, sound=None):
        self.prefs.append((token, warnings, sound))
        return token in self.known


def make_app(
    rows: list[dict], api_key: str | None = None,
    known_tokens: tuple[str, ...] = (),
) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.ctx = SimpleNamespace(
        config=SimpleNamespace(api_key=api_key),
        db=FakeDB(rows, known_tokens),
    )
    return app


def make_client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    )


def make_rows(count: int = 2) -> list[dict]:
    return [
        {
            "id": count - i,
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
        "id": 2,
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
    assert app.state.ctx.db.calls == [(50, None)]

async def test_alerts_limit_param_is_passed_through():
    app = make_app(make_rows(5))
    async with make_client(app) as client:
        response = await client.get("/alerts", params={"limit": 3})
    assert app.state.ctx.db.calls == [(3, None)]
    assert len(response.json()["alerts"]) == 3

async def test_alerts_before_cursor_pages_older_rows():
    app = make_app(make_rows(5))
    async with make_client(app) as client:
        response = await client.get("/alerts", params={"limit": 2, "before": 4})
    assert app.state.ctx.db.calls == [(2, 4)]
    assert [a["id"] for a in response.json()["alerts"]] == [3, 2]

async def test_alerts_params_are_validated():
    app = make_app([])
    async with make_client(app) as client:
        assert (await client.get("/alerts", params={"limit": 0})).status_code == 422
        assert (await client.get("/alerts", params={"limit": 201})).status_code == 422
        assert (await client.get("/alerts", params={"limit": "abc"})).status_code == 422
        assert (await client.get("/alerts", params={"before": 0})).status_code == 422
        assert (await client.get("/alerts", params={"before": "abc"})).status_code == 422
    assert app.state.ctx.db.calls == []

async def test_device_prefs_are_updated():
    token = "Aa" * 32
    app = make_app([], known_tokens=(token.lower(),))
    async with make_client(app) as client:
        response = await client.patch(
            f"/devices/{token}", json={"warnings": False, "sound": "siren.caf"},
        )
    assert response.status_code == 200
    assert app.state.ctx.db.prefs == [(token.lower(), False, "siren.caf")]

async def test_device_prefs_reject_unknown_sound_and_empty_body():
    token = "aa" * 32
    app = make_app([], known_tokens=(token,))
    async with make_client(app) as client:
        assert (await client.patch(
            f"/devices/{token}", json={"sound": "airhorn.caf"},
        )).status_code == 422
        assert (await client.patch(f"/devices/{token}", json={})).status_code == 422
        assert (await client.patch(
            "/devices/zz", json={"warnings": True},
        )).status_code == 422
    assert app.state.ctx.db.prefs == []

async def test_device_prefs_for_unknown_token_is_404():
    app = make_app([])
    async with make_client(app) as client:
        response = await client.patch(
            f"/devices/{'bb' * 32}", json={"warnings": True},
        )
    assert response.status_code == 404

async def test_device_prefs_require_api_key_when_configured():
    token = "aa" * 32
    app = make_app([], api_key="secret", known_tokens=(token,))
    async with make_client(app) as client:
        assert (await client.patch(
            f"/devices/{token}", json={"warnings": False},
        )).status_code == 401
        good = await client.patch(
            f"/devices/{token}", json={"warnings": False},
            headers={"X-API-Key": "secret"},
        )
    assert good.status_code == 200

async def test_alerts_requires_api_key_when_configured():
    app = make_app([], api_key="secret")
    async with make_client(app) as client:
        assert (await client.get("/alerts")).status_code == 401
        good = await client.get("/alerts", headers={"X-API-Key": "secret"})
    assert good.status_code == 200
