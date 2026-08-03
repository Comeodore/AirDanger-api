import os
from datetime import UTC, datetime, timedelta

import pytest

from app.db import Database

TEST_DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DSN, reason="TEST_DATABASE_URL is unset")


@pytest.fixture
async def db():
    database = await Database.connect(TEST_DSN)
    await database._pool.execute("TRUNCATE devices, pushes")
    yield database
    await database.close()

async def test_device_upsert_and_tokens(db):
    await db.upsert_device("aa" * 32)
    await db.upsert_device("aa" * 32)
    await db.upsert_device("bb" * 32)
    assert sorted(await db.tokens()) == ["aa" * 32, "bb" * 32]

async def test_delete_device(db):
    await db.upsert_device("aa" * 32)
    await db.delete_device("aa" * 32)
    assert await db.tokens() == []

async def test_purge_stale_devices(db):
    await db.upsert_device("aa" * 32)
    await db._pool.execute(
        "UPDATE devices SET updated_at = now() - interval '91 days' WHERE token = $1",
        "aa" * 32,
    )
    await db.upsert_device("bb" * 32)
    assert await db.purge_stale_devices() == 1
    assert await db.tokens() == ["bb" * 32]

async def test_push_round_trip(db):
    now = datetime.now(UTC)
    await db.insert_push("kyiv_nebo", "ballistic", "inbound", "Ціль на Київ",
                         now - timedelta(minutes=5))
    await db.insert_push("kyiv_nebo", "irbm", "warning", "Загроза МБР",
                         now - timedelta(minutes=1))

    rows = await db.pushes_since(now - timedelta(minutes=2))
    assert [r["type"] for r in rows] == ["irbm"]
    assert rows[0]["channel"] == "kyiv_nebo"

    all_rows = await db.pushes_since(now - timedelta(hours=1))
    assert [r["text"] for r in all_rows] == ["Ціль на Київ", "Загроза МБР"]
