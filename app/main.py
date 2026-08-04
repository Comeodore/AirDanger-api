import asyncio
import contextlib
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI

from .api import router
from .config import Config
from .danger_service import DangerService, DetectedThreat
from .db import Database
from .ingest import ChannelPoller
from .push import PushService
from .state import ChannelContext, PushLedger

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
    level=os.environ.get("LOG_LEVEL", "INFO").strip().upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
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

BRIEF_LIMIT = 80


def brief(text: str) -> str:
    return " ".join(text.split())[:BRIEF_LIMIT]


@dataclass
class AppContext:
    config: Config
    db: Database
    danger: DangerService
    ledger: PushLedger
    push: PushService
    ingest: ChannelPoller | None
    context: ChannelContext

    async def handle_message(self, source: str, text: str, ts: datetime) -> None:
        short = brief(text)
        evaluation = self.danger.evaluate(text)
        if evaluation.safety:
            if self.context.ballistic_live(ts):
                logger.info("%s: safety cleared ballistic context — %s", source, short)
            else:
                logger.debug("%s: safety, no live context — %s", source, short)
            self.context.clear()
            return
        if evaluation.other_weapon:
            logger.debug("%s: other weapon marks context — %s", source, short)
            self.context.mark_other(ts)

        threat = evaluation.detection
        bare = False
        if threat is not None:
            if threat.type not in self.config.push_types:
                logger.debug("%s: %s not in PUSH_TYPES — %s", source, threat.type, short)
                return
            self.context.mark_ballistic(ts)
            if threat.severity == "warning" and not self.config.push_warnings:
                logger.info("%s: %s warning silent, PUSH_WARNINGS off — %s",
                            source, threat.type, short)
                return
        elif evaluation.bare_target:
            if not self.context.ballistic_leads(ts):
                logger.info("%s: bare target dropped, no ballistic context — %s",
                            source, short)
                return
            severity = self.danger.bare_severity(text)
            if severity == "warning" and not self.config.push_warnings:
                logger.info("%s: bare warning silent, PUSH_WARNINGS off — %s",
                            source, short)
                return
            threat = DetectedThreat(type="ballistic", text=text, severity=severity)
            bare = True
        else:
            logger.debug("%s: no match — %s", source, short)
            return

        label = f"{threat.severity}/{threat.type}{' (context)' if bare else ''}"
        if not self.ledger.should_notify(threat, ts):
            left = int(self.ledger.wait_left(threat, ts).total_seconds())
            logger.info("%s: %s suppressed, cooldown %ds left — %s",
                        source, label, left, short)
            return

        tokens = await self.db.tokens()
        if not tokens:
            logger.warning("%s: %s dropped, no devices registered — %s",
                           source, label, short)
            return

        logger.info("%s: %s sending to %d device(s) — %s",
                    source, label, len(tokens), short)
        started = time.monotonic()
        delivered = await self.push.send_detection(tokens, threat, ts, source=source)
        took = int((time.monotonic() - started) * 1000)

        if not delivered:
            logger.error("%s: %s DELIVERED TO NONE of %d devices in %dms",
                         source, label, len(tokens), took)
            return
        if delivered < len(tokens):
            logger.warning("%s: %s delivered to %d of %d devices in %dms",
                           source, label, delivered, len(tokens), took)
        else:
            logger.info("%s: %s delivered to %d/%d devices in %dms",
                        source, label, delivered, len(tokens), took)
        self.ledger.note(threat, ts)
        await self.db.insert_push(source, threat.type, threat.severity, text, ts)

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
    seeded = await db.pushes_since(datetime.now(UTC) - ledger.cooldown)
    ledger.seed(seeded)
    logger.info(
        "config: channels=%s poll=%.1fs cooldown=%ds types=%s warnings=%s critical=%s "
        "apns=%s topic=%s ttl=%dmin devices=%d ledger_seeded=%d",
        ",".join(config.channels), config.poll_sec, config.push_cooldown_sec,
        ",".join(sorted(config.push_types)), config.push_warnings, config.critical_alerts,
        "SANDBOX" if config.apns_sandbox else "production", config.apns_topic,
        config.context_ttl_min, len(await db.tokens()), len(seeded),
    )
    if not config.apns_configured:
        logger.error("APNs not configured — pushes are disabled")

    push = PushService(config, on_dead_token=db.delete_device)
    ctx = AppContext(
        config=config, db=db, danger=DangerService(), ledger=ledger,
        push=push, ingest=None,
        context=ChannelContext(ttl=timedelta(minutes=config.context_ttl_min)),
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
