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

    async def upsert_device(
        self, token: str, warnings: bool | None = None, sound: str | None = None,
    ) -> None:
        await self._pool.execute(
            """INSERT INTO devices (token, updated_at, warnings, sound)
               VALUES ($1, now(), coalesce($2, true), coalesce($3, 'alert.caf'))
               ON CONFLICT (token) DO UPDATE SET
                   updated_at = now(),
                   warnings = coalesce($2, devices.warnings),
                   sound = coalesce($3, devices.sound)""",
            token, warnings, sound,
        )

    async def update_device_prefs(
        self, token: str, warnings: bool | None = None, sound: str | None = None,
    ) -> bool:
        result = await self._pool.execute(
            """UPDATE devices SET
                   warnings = coalesce($2, warnings),
                   sound = coalesce($3, sound),
                   updated_at = now()
               WHERE token = $1""",
            token, warnings, sound,
        )
        return result == "UPDATE 1"

    async def delete_device(self, token: str) -> None:
        await self._pool.execute("DELETE FROM devices WHERE token = $1", token)

    async def tokens(self) -> list[dict]:
        rows = await self._pool.fetch("SELECT token, warnings, sound FROM devices")
        return [dict(row) for row in rows]

    async def insert_push(
        self, channel: str, type_: str, severity: str, text: str, ts: datetime,
        pushed: bool = True,
    ) -> None:
        await self._pool.execute(
            """INSERT INTO pushes (channel, type, severity, text, ts, pushed)
               VALUES ($1, $2, $3, $4, $5, $6)""",
            channel, type_, severity, text, ts, pushed,
        )

    async def recent_pushes(self, limit: int, before: int | None = None) -> list[dict]:
        rows = await self._pool.fetch(
            """SELECT id, channel, type, severity, text, ts FROM pushes
               WHERE $2::bigint IS NULL
                  OR (ts, id) < (SELECT ts, id FROM pushes WHERE id = $2)
               ORDER BY ts DESC, id DESC LIMIT $1""",
            limit, before,
        )
        return [dict(row) for row in rows]

    async def pushes_since(self, since: datetime) -> list[dict]:
        rows = await self._pool.fetch(
            """SELECT channel, type, severity, text, ts FROM pushes
               WHERE ts >= $1 AND pushed AND type <> 'all_clear'
               ORDER BY ts ASC""",
            since,
        )
        return [dict(row) for row in rows]

    async def last_pushed_threat(self) -> datetime | None:
        return await self._pool.fetchval(
            "SELECT max(ts) FROM pushes WHERE pushed AND type <> 'all_clear'"
        )

    async def last_pushed_clear(self) -> datetime | None:
        return await self._pool.fetchval(
            "SELECT max(ts) FROM pushes WHERE pushed AND type = 'all_clear'"
        )
