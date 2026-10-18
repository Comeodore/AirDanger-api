import hmac
import logging
import time
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field, field_validator

from .push import SOUND_CHOICES

logger = logging.getLogger(__name__)

router = APIRouter()


def _ctx(request: Request):
    return request.app.state.ctx

async def check_api_key(request: Request, x_api_key: Annotated[str | None, Header()] = None):
    expected = _ctx(request).config.api_key
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail="invalid api key")

async def check_edge_key(request: Request, x_edge_key: Annotated[str | None, Header()] = None):
    expected = _ctx(request).config.edge_key
    if not expected or not hmac.compare_digest(
        (x_edge_key or "").encode(), expected.encode(),
    ):
        raise HTTPException(status_code=401, detail="invalid edge key")

LA_TOKEN = Field(default=None, min_length=32, max_length=400, pattern=r"^[0-9a-fA-F]+$")

CRITICAL_VOLUME = Field(default=None, ge=0.0, le=1.0)


class DeviceRegistration(BaseModel):
    token: str = Field(min_length=32, max_length=200, pattern=r"^[0-9a-fA-F]+$")
    warnings: bool | None = None
    sound: str | None = None
    critical: bool | None = None
    critical_volume: float | None = CRITICAL_VOLUME
    la_start_token: str | None = LA_TOKEN
    la_update_token: str | None = LA_TOKEN
    model: str | None = Field(default=None, max_length=64)
    ios_version: str | None = Field(default=None, max_length=32)

    @field_validator("model", "ios_version")
    @classmethod
    def _printable(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return "".join(ch for ch in value if ch.isprintable()).strip() or None

@router.post("/devices", dependencies=[Depends(check_api_key)])
async def register_device(body: DeviceRegistration, request: Request) -> dict:
    ctx = _ctx(request)
    if body.sound is not None and body.sound not in SOUND_CHOICES:
        raise HTTPException(status_code=422, detail="unknown sound")
    token = body.token.lower()
    if not await ctx.push.token_is_usable(token):
        logger.warning("device registration rejected %s…, APNs does not accept this token",
                       token[:8])
        raise HTTPException(status_code=400, detail="unknown device token")
    await ctx.db.upsert_device(
        token, body.warnings, body.sound,
        _lower(body.la_start_token), _lower(body.la_update_token),
        body.critical, body.critical_volume, body.model, body.ios_version,
    )
    logger.info("device registered %s… critical=%s model=%s ios=%s (%d total)",
                token[:8], body.critical, body.model, body.ios_version,
                len(await ctx.db.tokens()))
    return {"ok": True}


def _lower(token: str | None) -> str | None:
    return token.lower() if token else None

class DevicePrefs(BaseModel):
    warnings: bool | None = None
    sound: str | None = None
    critical: bool | None = None
    critical_volume: float | None = CRITICAL_VOLUME
    la_start_token: str | None = LA_TOKEN
    la_update_token: str | None = LA_TOKEN

@router.patch("/devices/{token}", dependencies=[Depends(check_api_key)])
async def update_device(
    token: Annotated[str, Path(min_length=32, max_length=200, pattern=r"^[0-9a-fA-F]+$")],
    body: DevicePrefs,
    request: Request,
) -> dict:
    ctx = _ctx(request)
    if all(value is None for value in (
        body.warnings, body.sound, body.critical, body.critical_volume,
        body.la_start_token, body.la_update_token,
    )):
        raise HTTPException(status_code=422, detail="nothing to update")
    if body.sound is not None and body.sound not in SOUND_CHOICES:
        raise HTTPException(status_code=422, detail="unknown sound")
    token = token.lower()
    updated = await ctx.db.update_device_prefs(
        token, body.warnings, body.sound,
        _lower(body.la_start_token), _lower(body.la_update_token),
        body.critical, body.critical_volume,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="unknown device")
    logger.info(
        "device %s… prefs: warnings=%s sound=%s critical=%s volume=%s "
        "la_start=%s la_update=%s",
        token[:8], body.warnings, body.sound, body.critical, body.critical_volume,
        bool(body.la_start_token), bool(body.la_update_token))
    return {"ok": True}

@router.get("/alerts", dependencies=[Depends(check_api_key)])
async def alerts(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before: Annotated[int | None, Query(ge=1)] = None,
) -> dict:
    return {"alerts": await _ctx(request).db.recent_pushes(limit, before)}

DEVICE_TOKEN = Field(min_length=32, max_length=200, pattern=r"^[0-9a-fA-F]+$")


class EdgeDecision(BaseModel):
    channel: str = Field(min_length=1, max_length=64)
    type: Literal["ballistic", "irbm", "all_clear"]
    severity: Literal["inbound", "warning", "clear"]
    text: str = Field(max_length=8192)
    push_text: str = Field(default="", max_length=8192)
    ts: datetime
    pushed: bool


class EdgeReport(BaseModel):
    decisions: list[EdgeDecision] = Field(default_factory=list, max_length=500)
    dead_tokens: list[Annotated[str, DEVICE_TOKEN]] = Field(default_factory=list, max_length=500)


@router.get("/edge/devices", dependencies=[Depends(check_edge_key)])
async def edge_devices(request: Request) -> dict:
    ctx = _ctx(request)
    ctx.edge_seen_at = time.time()
    return {"devices": await ctx.db.tokens()}


@router.post("/edge/report", dependencies=[Depends(check_edge_key)])
async def edge_report(body: EdgeReport, request: Request) -> dict:
    ctx = _ctx(request)
    ctx.edge_seen_at = time.time()
    stored = 0
    for d in body.decisions:
        if await ctx.record_edge_decision(
            d.channel, d.type, d.severity, d.text, d.push_text, d.ts, d.pushed,
        ):
            stored += 1
    for token in body.dead_tokens:
        await ctx.db.delete_device(token.lower())
        logger.info("device %s… removed by edge: Unregistered", token[:8])
    return {"stored": stored, "removed": len(body.dead_tokens)}


SOURCE_STATE = Field(max_length=16, pattern=r"^[a-z_]+$")


class EdgeChannelHealth(BaseModel):
    tme: str = SOURCE_STATE
    mtproto: str = SOURCE_STATE


class EdgeHealth(BaseModel):
    mtproto: str = SOURCE_STATE
    channels: dict[Annotated[str, Field(max_length=64)], EdgeChannelHealth] = Field(
        default_factory=dict, max_length=20,
    )


@router.post("/edge/health", dependencies=[Depends(check_edge_key)])
async def edge_health(body: EdgeHealth, request: Request) -> dict:
    ctx = _ctx(request)
    now = time.time()
    ctx.edge_seen_at = now
    ctx.edge_health = body.model_dump()
    ctx.edge_health_at = now
    return {"ok": True}


EDGE_SILENT_SEC = 300
GOOD_STATES = {"ok", "starting", "waiting"}


def edge_state(ctx, now: float) -> tuple[str, int | None]:
    seen = ctx.edge_seen_at
    if seen is not None:
        ago = now - seen
        return ("ok" if ago <= EDGE_SILENT_SEC else "silent"), round(ago)
    if now - ctx.started_at <= EDGE_SILENT_SEC:
        return "waiting", None
    return "silent", None


def edge_sources(ctx, now: float) -> tuple[str, str, dict]:
    report = ctx.edge_health
    if report is None or now - ctx.edge_health_at > EDGE_SILENT_SEC:
        waiting = report is None and now - ctx.started_at <= EDGE_SILENT_SEC
        state = "waiting" if waiting else "unknown"
        return state, state, {}
    channels = report["channels"]
    tme_states = [c["tme"] for c in channels.values()]
    if all(state == "ok" for state in tme_states):
        tme = "ok"
    elif all(state in ("ok", "starting") for state in tme_states):
        tme = "starting"
    else:
        tme = "down"
    mtproto = report["mtproto"]
    if mtproto == "ok" and any(c["mtproto"] != "ok" for c in channels.values()):
        mtproto = "failing"
    return mtproto, tme, channels


@router.get("/health")
async def health(request: Request) -> dict:
    ctx = _ctx(request)
    ingest = ctx.ingest
    now = time.time()
    ingest_on = ctx.config.ingest_enabled
    connected = bool(ingest and ingest.connected)
    degraded = ingest_on and not connected
    channels = {}
    for channel in ctx.config.channels if ingest_on else []:
        state = ingest.channel_state(channel) if ingest else "starting"
        if state != "ok":
            degraded = True
        last = ingest.last_message_at.get(channel) if ingest else None
        channels[channel] = {
            "state": state,
            "last_message_sec": round(now - last) if last is not None else None,
        }
    mtproto = "off"
    if ctx.mtproto is not None:
        mtproto = "ok" if ctx.mtproto.connected else "down"
        if mtproto != "ok":
            degraded = True
    tme_ok = connected and all(c["state"] == "ok" for c in channels.values())
    tme = "ok" if ingest_on and tme_ok else "down"
    edge, edge_ago = edge_state(ctx, now)
    if not ingest_on:
        mtproto, tme, channels = edge_sources(ctx, now)
        if edge == "silent" or mtproto not in GOOD_STATES or tme not in GOOD_STATES:
            degraded = True
    return {
        "status": "degraded" if degraded else "ok",
        "mtproto": mtproto,
        "tme": tme,
        "edge": edge,
        "edge_last_seen_sec": edge_ago,
        "ingest": "on" if ingest_on else "off",
        "connected": connected,
        "channels": channels,
    }
