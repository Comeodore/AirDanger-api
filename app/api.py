import logging
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field

from .push import SOUND_CHOICES

logger = logging.getLogger(__name__)

router = APIRouter()


def _ctx(request: Request):
    return request.app.state.ctx

async def check_api_key(request: Request, x_api_key: Annotated[str | None, Header()] = None):
    expected = _ctx(request).config.api_key
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail="invalid api key")

class DeviceRegistration(BaseModel):
    token: str = Field(min_length=32, max_length=200, pattern=r"^[0-9a-fA-F]+$")
    warnings: bool | None = None
    sound: str | None = None

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
    await ctx.db.upsert_device(token, body.warnings, body.sound)
    logger.info("device registered %s… (%d total)", token[:8], len(await ctx.db.tokens()))
    return {"ok": True}

class DevicePrefs(BaseModel):
    warnings: bool | None = None
    sound: str | None = None

@router.patch("/devices/{token}", dependencies=[Depends(check_api_key)])
async def update_device(
    token: Annotated[str, Path(min_length=32, max_length=200, pattern=r"^[0-9a-fA-F]+$")],
    body: DevicePrefs,
    request: Request,
) -> dict:
    ctx = _ctx(request)
    if body.warnings is None and body.sound is None:
        raise HTTPException(status_code=422, detail="nothing to update")
    if body.sound is not None and body.sound not in SOUND_CHOICES:
        raise HTTPException(status_code=422, detail="unknown sound")
    token = token.lower()
    if not await ctx.db.update_device_prefs(token, body.warnings, body.sound):
        raise HTTPException(status_code=404, detail="unknown device")
    logger.info("device %s… prefs: warnings=%s sound=%s",
                token[:8], body.warnings, body.sound)
    return {"ok": True}

@router.get("/alerts", dependencies=[Depends(check_api_key)])
async def alerts(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before: Annotated[int | None, Query(ge=1)] = None,
) -> dict:
    return {"alerts": await _ctx(request).db.recent_pushes(limit, before)}

@router.get("/health")
async def health(request: Request) -> dict:
    ctx = _ctx(request)
    ingest = ctx.ingest
    now = time.time()
    connected = bool(ingest and ingest.connected)
    degraded = not connected
    channels = {}
    for channel in ctx.config.channels:
        state = ingest.channel_state(channel) if ingest else "starting"
        if state != "ok":
            degraded = True
        last = ingest.last_message_at.get(channel) if ingest else None
        channels[channel] = {
            "state": state,
            "last_message_sec": round(now - last) if last is not None else None,
        }
    return {
        "status": "degraded" if degraded else "ok",
        "connected": connected,
        "channels": channels,
    }
