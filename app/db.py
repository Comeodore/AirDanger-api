from datetime import datetime
from pathlib import Path

import asyncpg

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema.sql"


class Database:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @classmethod
    async def connect(cls, dsn: str) -> "Database":
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
        async with pool.acquire() as conn:
            await conn.execute(SCHEMA_PATH.read_text())
        return cls(pool)

    async def close(self) -> None:
        await self._pool.close()

    async def upsert_device(self, token: str) -> None:
        await self._pool.execute(
            """INSERT INTO devices (token, updated_at) VALUES ($1, now())
               ON CONFLICT (token) DO UPDATE SET updated_at = now()""",
            token,
        )

    async def delete_device(self, token: str) -> None:
        await self._pool.execute("DELETE FROM devices WHERE token = $1", token)

    async def tokens(self) -> list[str]:
        rows = await self._pool.fetch("SELECT token FROM devices")
        return [row["token"] for row in rows]

    async def insert_push(
        self, channel: str, type_: str, severity: str, text: str, ts: datetime,
    ) -> None:
        await self._pool.execute(
            """INSERT INTO pushes (channel, type, severity, text, ts)
               VALUES ($1, $2, $3, $4, $5)""",
            channel, type_, severity, text, ts,
        )

    async def recent_pushes(self, limit: int) -> list[dict]:
        rows = await self._pool.fetch(
            """SELECT channel, type, severity, text, ts FROM pushes
               ORDER BY ts DESC LIMIT $1""",
            limit,
        )
        return [dict(row) for row in rows]

    async def pushes_since(self, since: datetime) -> list[dict]:
        rows = await self._pool.fetch(
            """SELECT channel, type, severity, text, ts FROM pushes
               WHERE ts >= $1 ORDER BY ts ASC""",
            since,
        )
        return [dict(row) for row in rows]
