import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI

from .api import router
from .config import Config
from .danger_service import DangerService
from .db import Database
from .dedup import TTLSet, digest
from .ingest import ChannelPoller
from .push import PushService
from .state import PushLedger

class _TrimAccessLog(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) == 5:
            record.args = record.args[1:]
            record.msg = '%s %s HTTP/%s -> %s'
        return True


class _ShortName(logging.Filter):
    ALIASES = {"uvicorn.error": "uvicorn", "uvicorn.access": "http"}

    def filter(self, record: logging.LogRecord) -> bool:
        record.name = self.ALIASES.get(record.name, record.name).removeprefix("app.")
        return True


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)

for _handler in logging.getLogger().handlers:
    _handler.addFilter(_ShortName())

for _name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
    _uv = logging.getLogger(_name)
    _uv.handlers.clear()
    _uv.propagate = True
logging.getLogger("uvicorn.access").addFilter(_TrimAccessLog())
logger = logging.getLogger(__name__)

DEVICE_PURGE_INTERVAL_SEC = 24 * 3600


@dataclass
class AppContext:
    config: Config
    db: Database
    danger: DangerService
    ledger: PushLedger
    push: PushService
    ingest: ChannelPoller | None
    dedup: TTLSet

    async def handle_message(self, source: str, text: str, ts: datetime) -> None:
        if not self.dedup.add(digest(text)):
            return
        threat = self.danger.evaluate(text).detection
        if threat is None or threat.type not in self.config.push_types:
            return
        if threat.severity == "warning" and not self.config.push_warnings:
            return
        if not self.ledger.should_notify(threat, ts):
            return
        logger.info("push %s/%s from %s: %s",
                    threat.severity, threat.type, source, " ".join(text.split())[:80])
        await self.db.insert_push(source, threat.type, threat.severity, text, ts)
        tokens = await self.db.tokens()
        await self.push.send_detection(tokens, threat, ts, source=source)

    async def purge_loop(self) -> None:
        while True:
            try:
                purged = await self.db.purge_stale_devices()
                if purged:
                    logger.info("purged %d stale devices", purged)
            except Exception:
                logger.exception("device purge failed")
            await asyncio.sleep(DEVICE_PURGE_INTERVAL_SEC)

@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    config = Config.from_env()
    db = await Database.connect(config.database_url)
    ledger = PushLedger(cooldown=timedelta(seconds=config.push_cooldown_sec))
    ledger.seed(await db.pushes_since(datetime.now(UTC) - ledger.cooldown))

    push = PushService(config, on_dead_token=db.delete_device)
    dedup_ttl_sec = min(config.dedup_ttl_min * 60, config.push_cooldown_sec)
    ctx = AppContext(
        config=config, db=db, danger=DangerService(), ledger=ledger,
        push=push, ingest=None, dedup=TTLSet(ttl_seconds=dedup_ttl_sec),
    )
    ctx.ingest = ChannelPoller(config, ctx.handle_message)
    tasks = [
        asyncio.create_task(ctx.purge_loop()),
        asyncio.create_task(ctx.ingest.run()),
    ]

    app.state.ctx = ctx
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await db.close()

app = FastAPI(title="Air Danger API", lifespan=lifespan)
app.include_router(router)
