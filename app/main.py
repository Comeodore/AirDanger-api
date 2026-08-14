import asyncio
import contextlib
import logging
import os
import sys
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI

from .api import router
from .config import Config
from .danger_service import DangerService, DetectedThreat
from .db import Database
from .ingest import Ingest
from .profiles import profile_for
from .push import PushService, first_sentence
from .state import Episode, PushLedger, SkyContext

CLEAR_DISMISS_SEC = 180

class _TrimAccessLog(logging.Filter):
    QUIET = {"/health"}

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) == 5:
            _, _, path, _, status = record.args
            if str(path).split("?")[0] in self.QUIET and str(status) == "200":
                return False
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
    ingest: Ingest | None
    sky: SkyContext
    episode: Episode | None = None

    async def handle_message(self, source: str, text: str, ts: datetime) -> None:
        short = brief(text)
        profile = profile_for(source)
        evaluation = self.danger.evaluate(text, profile)
        if evaluation.safety:
            if self.sky.ballistic_live(ts):
                logger.info("%s: safety cleared ballistic context — %s", source, short)
            else:
                logger.info("%s: safety, no live context — %s", source, short)
            self.sky.clear()
            if evaluation.all_clear:
                await self._announce_all_clear(source, text, ts)
            return
        if evaluation.other_weapon:
            logger.debug("%s: other weapon marks context — %s", source, short)
            self.sky.mark_other(ts)

        threat = evaluation.detection
        bare = False
        if threat is not None:
            if threat.type not in self.config.push_types:
                logger.debug("%s: %s not in PUSH_TYPES — %s", source, threat.type, short)
                return
            self.sky.mark_ballistic(ts)
            if threat.severity == "warning" and not self.config.push_warnings:
                logger.info("%s: %s warning silent, PUSH_WARNINGS off — %s",
                            source, threat.type, short)
                return
        elif evaluation.bare_target:
            if not self.sky.ballistic_leads(ts):
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

        if threat.severity == "warning" and profile.trim_warning_push:
            threat = replace(threat, text=first_sentence(threat.text))

        label = f"{threat.severity}/{threat.type}{' (context)' if bare else ''}"

        async def record(pushed: bool) -> None:
            await self.db.insert_push(
                source, threat.type, threat.severity, text, ts, pushed=pushed,
            )

        if not self.ledger.should_notify(threat, ts):
            left = int(self.ledger.wait_left(threat, ts).total_seconds())
            logger.info("%s: %s suppressed, cooldown %ds left — %s",
                        source, label, left, short)
            await record(pushed=False)
            await self._episode_note(threat, ts, pushed=False)
            return

        tokens = await self.db.tokens()
        if not tokens:
            logger.warning("%s: %s dropped, no devices registered — %s",
                           source, label, short)
            await record(pushed=False)
            return

        logger.info("%s: %s sending to %d device(s) — %s",
                    source, label, len(tokens), short)
        started = time.monotonic()
        delivered = await self.push.send_detection(tokens, threat, ts, source=source)
        took = int((time.monotonic() - started) * 1000)

        if not delivered:
            logger.error("%s: %s DELIVERED TO NONE of %d devices in %dms",
                         source, label, len(tokens), took)
            await record(pushed=False)
            await self._episode_note(threat, ts, pushed=False)
            return
        if delivered < len(tokens):
            logger.warning("%s: %s delivered to %d of %d devices in %dms",
                           source, label, delivered, len(tokens), took)
        else:
            logger.info("%s: %s delivered to %d/%d devices in %dms",
                        source, label, delivered, len(tokens), took)
        self.ledger.note(threat, ts)
        await record(pushed=True)
        await self._episode_note(threat, ts, pushed=True)

    async def _episode_note(self, threat: DetectedThreat, ts: datetime, pushed: bool) -> None:
        gap = timedelta(minutes=self.config.la_timeout_min)
        if self.episode is not None and ts - self.episode.last_signal_at > gap:
            self.episode = None
        if self.episode is None:
            if not pushed:
                return
            self.episode = Episode(
                started_at=ts, last_signal_at=ts,
                type=threat.type, severity=threat.severity, text=threat.text,
                escalated_at=ts if threat.severity != "warning" else None,
            )
            await self._la_send("start", self.episode.content_state(), ts)
            return
        self.episode.note(threat, ts)
        await self._la_send("update", self.episode.content_state(), ts)

    async def end_episode(self, ts: datetime, clear_text: str | None = None) -> None:
        episode = self.episode
        if episode is None:
            return
        self.episode = None
        if clear_text is not None:
            state = episode.content_state(state="clear", text=clear_text)
            dismissal = ts + timedelta(seconds=CLEAR_DISMISS_SEC)
        else:
            state = episode.content_state()
            dismissal = ts
        await self._la_send("end", state, ts, dismissal_at=dismissal)

    async def episode_watchdog(self) -> None:
        while True:
            await asyncio.sleep(30)
            episode = self.episode
            if episode is None:
                continue
            now = datetime.now(UTC)
            if now - episode.last_signal_at > timedelta(minutes=self.config.la_timeout_min):
                logger.info("episode ended after %d quiet minutes",
                            self.config.la_timeout_min)
                await self.end_episode(now)

    async def _la_send(
        self, event: str, content_state: dict, ts: datetime,
        dismissal_at: datetime | None = None,
    ) -> None:
        if event == "start":
            tokens = await self.db.la_start_tokens()
            if not tokens:
                return
            await self.db.clear_la_update_tokens()
            delivered = await self.push.send_live_activity(
                tokens, "start", content_state, ts,
                attributes={"episode": int(ts.timestamp())},
            )
        else:
            tokens = await self.db.la_update_tokens()
            if not tokens:
                return
            delivered = await self.push.send_live_activity(
                tokens, event, content_state, ts, dismissal_at=dismissal_at,
            )
        logger.info("live activity %s delivered to %d/%d device(s)",
                    event, delivered, len(tokens))

    async def _announce_all_clear(self, source: str, text: str, ts: datetime) -> None:
        short = brief(text)
        window = timedelta(minutes=self.config.all_clear_window_min)
        last_threat = await self.db.last_pushed_threat()
        if last_threat is None or ts - last_threat > window:
            logger.info("%s: all clear outside an episode — %s", source, short)
            return
        last_clear = await self.db.last_pushed_clear()
        if last_clear is not None and last_clear >= last_threat:
            logger.info("%s: all clear already announced — %s", source, short)
            return

        async def record(pushed: bool) -> None:
            await self.db.insert_push(
                source, "all_clear", "clear", text, ts, pushed=pushed,
            )

        tokens = await self.db.tokens()
        if not tokens:
            logger.warning("%s: all clear dropped, no devices registered — %s",
                           source, short)
            await record(pushed=False)
            return
        logger.info("%s: all clear sending to %d device(s) — %s",
                    source, len(tokens), short)
        started = time.monotonic()
        delivered = await self.push.send_all_clear(tokens, text, ts, source=source)
        took = int((time.monotonic() - started) * 1000)
        if not delivered:
            logger.error("%s: all clear DELIVERED TO NONE of %d devices in %dms",
                         source, len(tokens), took)
            await record(pushed=False)
            return
        if delivered < len(tokens):
            logger.warning("%s: all clear delivered to %d of %d devices in %dms",
                           source, delivered, len(tokens), took)
        else:
            logger.info("%s: all clear delivered to %d/%d devices in %dms",
                        source, delivered, len(tokens), took)
        await record(pushed=True)
        await self.end_episode(ts, clear_text=text)

@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    config = Config.from_env()
    db = await Database.connect(config.database_url)
    ledger = PushLedger(
        cooldown=timedelta(seconds=config.push_cooldown_sec),
        escalate=config.push_escalation,
    )
    seeded = await db.pushes_since(datetime.now(UTC) - ledger.cooldown)
    ledger.seed(seeded)
    logger.info(
        "config: channels=%s poll=%.1fs max_age=%.0fs cooldown=%ds escalation=%s "
        "types=%s warnings=%s clear_window=%dmin "
        "critical=%s apns=%s topic=%s ttl=%dmin devices=%d ledger_seeded=%d",
        ",".join(config.channels), config.poll_sec, config.max_age_sec,
        config.push_cooldown_sec, config.push_escalation,
        ",".join(sorted(config.push_types)), config.push_warnings,
        config.all_clear_window_min, config.critical_alerts,
        "SANDBOX" if config.apns_sandbox else "production", config.apns_topic,
        config.context_ttl_min, len(await db.tokens()), len(seeded),
    )
    if not config.apns_configured:
        logger.error("APNs not configured — pushes are disabled")

    push = PushService(config, on_dead_token=db.delete_device)
    ctx = AppContext(
        config=config, db=db, danger=DangerService(), ledger=ledger,
        push=push, ingest=None,
        sky=SkyContext(ttl=timedelta(minutes=config.context_ttl_min)),
    )
    ctx.ingest = Ingest(config, ctx.handle_message)
    tasks = [
        asyncio.create_task(ctx.ingest.run()),
        asyncio.create_task(push.keep_warm()),
        asyncio.create_task(ctx.episode_watchdog()),
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
