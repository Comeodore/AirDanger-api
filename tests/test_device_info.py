from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from app.api import router

TOKEN = "ab" * 32


class RecordingDB:
    def __init__(self) -> None:
        self.upserts: list[tuple] = []

    async def upsert_device(self, token, warnings=None, sound=None, la_start_token=None,
                            la_update_token=None, critical=None, critical_volume=None,
                            model=None, ios_version=None):
        self.upserts.append((token, model, ios_version))

    async def tokens(self):
        return [{"token": TOKEN}]


class AcceptingPush:
    async def token_is_usable(self, token):
        return True


def make_app() -> tuple[FastAPI, RecordingDB]:
    db = RecordingDB()
    app = FastAPI()
    app.include_router(router)
    app.state.ctx = SimpleNamespace(config=SimpleNamespace(api_key=None), db=db, push=AcceptingPush())
    return app, db


async def post(app: FastAPI, body: dict) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/devices", json=body)


async def test_registration_stores_the_model_and_ios_version():
    app, db = make_app()
    response = await post(app, {"token": TOKEN, "model": "iPhone17,1", "ios_version": "26.0.1"})
    assert response.status_code == 200
    assert db.upserts == [(TOKEN, "iPhone17,1", "26.0.1")]


async def test_older_builds_without_device_info_still_register():
    app, db = make_app()
    response = await post(app, {"token": TOKEN})
    assert response.status_code == 200
    assert db.upserts == [(TOKEN, None, None)]


async def test_an_oversized_model_is_rejected():
    app, _ = make_app()
    response = await post(app, {"token": TOKEN, "model": "x" * 65})
    assert response.status_code == 422


async def test_control_characters_never_reach_the_log_or_the_table():
    app, db = make_app()
    response = await post(app, {"token": TOKEN, "model": "iPhone17,1\nfake line",
                                "ios_version": "\x1b[31m"})
    assert response.status_code == 200
    assert db.upserts == [(TOKEN, "iPhone17,1fake line", "[31m")]
