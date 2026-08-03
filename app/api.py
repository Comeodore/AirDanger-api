import time
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

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
    await _ctx(request).db.upsert_device(body.token.lower())
    return {"ok": True}

@router.delete("/devices/{token}", dependencies=[Depends(check_api_key)])
async def unregister_device(token: str, request: Request) -> dict:
    await _ctx(request).db.delete_device(token.lower())
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
