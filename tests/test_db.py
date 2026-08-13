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

async def test_recent_pushes_newest_first_with_limit(db):
    now = datetime.now(UTC)
    for i in range(3):
        await db.insert_push("kyiv_nebo", "ballistic", "inbound", f"Ціль {i}",
                             now - timedelta(minutes=i))
    await db.insert_push("kyiv_nebo", "ballistic", "inbound", "Тиха ціль",
                         now - timedelta(seconds=30), pushed=False)
    await db.insert_push("kyiv_nebo", "ballistic", "inbound", "Стара ціль",
                         now - timedelta(hours=25))

    rows = await db.recent_pushes(10)
    assert [r["text"] for r in rows] == [
        "Ціль 0", "Тиха ціль", "Ціль 1", "Ціль 2", "Стара ціль",
    ]
    assert set(rows[0]) == {"id", "channel", "type", "severity", "text", "ts"}

    rows = await db.recent_pushes(2)
    assert [r["text"] for r in rows] == ["Ціль 0", "Тиха ціль"]

async def test_recent_pushes_before_cursor_continues_without_gaps(db):
    now = datetime.now(UTC)
    for i in range(5):
        await db.insert_push("kyiv_nebo", "ballistic", "inbound", f"Ціль {i}",
                             now - timedelta(minutes=i))

    first = await db.recent_pushes(2)
    second = await db.recent_pushes(2, before=first[-1]["id"])
    third = await db.recent_pushes(2, before=second[-1]["id"])
    texts = [r["text"] for r in first + second + third]
    assert texts == ["Ціль 0", "Ціль 1", "Ціль 2", "Ціль 3", "Ціль 4"]

async def test_push_round_trip(db):
    now = datetime.now(UTC)
    await db.insert_push("kyiv_nebo", "ballistic", "inbound", "Ціль на Київ",
                         now - timedelta(minutes=5))
    await db.insert_push("kyiv_nebo", "irbm", "warning", "Загроза МБР",
                         now - timedelta(minutes=1))
    await db.insert_push("kyiv_nebo", "ballistic", "inbound", "Тиха ціль",
                         now - timedelta(seconds=30), pushed=False)

    rows = await db.pushes_since(now - timedelta(minutes=2))
    assert [r["type"] for r in rows] == ["irbm"]
    assert rows[0]["channel"] == "kyiv_nebo"

    all_rows = await db.pushes_since(now - timedelta(hours=1))
    assert [r["text"] for r in all_rows] == ["Ціль на Київ", "Загроза МБР"]

async def test_all_clear_rows_feed_but_do_not_seed_ledger(db):
    now = datetime.now(UTC)
    await db.insert_push("kyiv_nebo", "ballistic", "inbound", "Ціль на Київ",
                         now - timedelta(minutes=10))
    await db.insert_push("kyiv_nebo", "all_clear", "clear", "Відбій",
                         now - timedelta(minutes=5))

    assert [r["type"] for r in await db.recent_pushes(10)] == ["all_clear", "ballistic"]
    assert [r["type"] for r in await db.pushes_since(now - timedelta(hours=1))] == ["ballistic"]

async def test_last_pushed_threat_and_clear(db):
    now = datetime.now(UTC)
    assert await db.last_pushed_threat() is None
    assert await db.last_pushed_clear() is None

    await db.insert_push("kyiv_nebo", "ballistic", "inbound", "Ціль на Київ",
                         now - timedelta(minutes=10))
    await db.insert_push("kyiv_nebo", "ballistic", "inbound", "Тиха ціль",
                         now - timedelta(minutes=2), pushed=False)
    await db.insert_push("kyiv_nebo", "all_clear", "clear", "Відбій",
                         now - timedelta(minutes=5))

    threat_at = await db.last_pushed_threat()
    clear_at = await db.last_pushed_clear()
    assert abs((threat_at - (now - timedelta(minutes=10))).total_seconds()) < 1
    assert abs((clear_at - (now - timedelta(minutes=5))).total_seconds()) < 1
