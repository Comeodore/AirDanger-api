import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest

from app.config import _parse_channels

USAGE = """usage:
  python scripts/login.py send <phone>
  python scripts/login.py code <code> [2fa-password]
  python scripts/login.py status
"""

STATE = Path(__file__).resolve().parent / ".login.json"


def credentials() -> tuple[int, str]:
    api_id = os.environ.get("TG_API_ID")
    api_hash = os.environ.get("TG_API_HASH")
    if not api_id or not api_hash:
        sys.exit("set TG_API_ID and TG_API_HASH (from https://my.telegram.org)")
    return int(api_id), api_hash


def client_for(session: str) -> TelegramClient:
    api_id, api_hash = credentials()
    return TelegramClient(StringSession(session), api_id, api_hash)


async def join_channels(client: TelegramClient) -> None:
    for channel in _parse_channels(os.environ.get("CHANNELS")):
        entity = await client.get_entity(channel)
        await client(JoinChannelRequest(entity))
        latest = await client.get_messages(entity, limit=1)
        print(f"joined {channel} — {entity.title!r}, newest message "
              f"{latest[0].id if latest else 0}")


async def send(phone: str) -> None:
    client = client_for("")
    await client.connect()
    sent = await client.send_code_request(phone)
    STATE.write_text(json.dumps({
        "session": client.session.save(), "phone": phone, "hash": sent.phone_code_hash,
    }))
    await client.disconnect()
    print(f"code sent to {phone} via {type(sent.type).__name__}")
    print("now run: python scripts/login.py code <code> [2fa-password]")


async def code(value: str, password: str | None) -> None:
    if not STATE.exists():
        sys.exit("no pending login — run 'send <phone>' first")
    state = json.loads(STATE.read_text())
    client = client_for(state["session"])
    await client.connect()
    try:
        await client.sign_in(
            phone=state["phone"], code=value, phone_code_hash=state["hash"],
        )
    except Exception as exc:
        if "password" not in type(exc).__name__.lower():
            raise
        if not password:
            sys.exit("account has 2FA — pass the cloud password as the second argument")
        await client.sign_in(password=password)

    me = await client.get_me()
    print(f"signed in as @{me.username or me.phone} (id {me.id})")
    await join_channels(client)
    print("\nput this in the server .env — it grants full access to the account:\n")
    print(f"TG_SESSION={client.session.save()}")
    await client.disconnect()
    STATE.unlink()


async def status() -> None:
    session = os.environ.get("TG_SESSION")
    if not session:
        sys.exit("set TG_SESSION to check it")
    client = client_for(session)
    await client.connect()
    if not await client.is_user_authorized():
        sys.exit("session is NOT authorised")
    me = await client.get_me()
    print(f"authorised as @{me.username or me.phone} (id {me.id})")
    await join_channels(client)
    await client.disconnect()


async def main() -> None:
    match sys.argv[1:]:
        case ["send", phone]:
            await send(phone)
        case ["code", value]:
            await code(value, None)
        case ["code", value, password]:
            await code(value, password)
        case ["status"]:
            await status()
        case _:
            sys.exit(USAGE)


asyncio.run(main())
