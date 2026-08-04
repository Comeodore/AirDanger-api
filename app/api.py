import logging
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

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

@router.post("/devices", dependencies=[Depends(check_api_key)])
async def register_device(body: DeviceRegistration, request: Request) -> dict:
    db = _ctx(request).db
    token = body.token.lower()
    await db.upsert_device(token)
    logger.info("device registered %s… (%d total)", token[:8], len(await db.tokens()))
    return {"ok": True}

@router.delete("/devices/{token}", dependencies=[Depends(check_api_key)])
async def unregister_device(token: str, request: Request) -> dict:
    db = _ctx(request).db
    await db.delete_device(token.lower())
    logger.info("device removed %s… (%d left)", token[:8].lower(), len(await db.tokens()))
    return {"ok": True}

@router.get("/health")
async def health(request: Request) -> dict:
    ctx = _ctx(request)
    now = time.time()
    return {
        "status": "ok",
        "connected": ctx.ingest.connected if ctx.ingest else False,
        "channels": {
            channel: (
                round(now - ctx.ingest.last_message_at[channel])
                if ctx.ingest and channel in ctx.ingest.last_message_at
                else None
            )
            for channel in ctx.config.channels
        },
    }
