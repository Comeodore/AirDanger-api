import asyncio
import getpass
import sys
from pathlib import Path

from telethon import TelegramClient, functions
from telethon.errors import PhoneCodeExpiredError, PhoneCodeInvalidError, SessionPasswordNeededError
from telethon.sessions import StringSession

DELIVERY = {
    "SentCodeTypeApp": "a message in the Telegram app on a device already logged in to this account",
    "SentCodeTypeSms": "an SMS",
    "SentCodeTypeCall": "a phone call that reads the code",
    "SentCodeTypeFlashCall": "a missed call (the code is the last digits of the number)",
    "SentCodeTypeMissedCall": "a missed call (the code is the last digits of the number)",
    "SentCodeTypeFragmentSms": "an SMS through Fragment",
    "SentCodeTypeEmailCode": "an email",
}


def read_env(path: Path) -> dict[str, str]:
    return dict(
        line.split("=", 1) for line in path.read_text().splitlines() if "=" in line
    )


def write_session(path: Path, session: str) -> None:
    lines = [line for line in path.read_text().splitlines() if not line.startswith("TG_SESSION=")]
    lines.append(f"TG_SESSION={session}")
    path.write_text("\n".join(lines) + "\n")


def describe(sent) -> str:
    now = DELIVERY.get(type(sent.type).__name__, type(sent.type).__name__)
    later = DELIVERY.get(type(sent.next_type).__name__, None) if sent.next_type else None
    text = f"code sent as {now}"
    if later:
        text += f"; type r to resend it as {later}"
        if sent.timeout:
            text += f" (allowed after {sent.timeout}s)"
    return text


async def qr_login(client: TelegramClient) -> None:
    import qrcode

    qr = await client.qr_login()
    while True:
        print("\033[2J\033[H", end="")
        code = qrcode.QRCode(border=1)
        code.add_data(qr.url)
        code.print_ascii(invert=True)
        print("Telegram on the phone: Settings > Devices > Link Desktop Device, scan this.")
        print("A new code appears every 25 seconds.")
        try:
            await qr.wait(timeout=25)
            return
        except asyncio.TimeoutError:
            await qr.recreate()
        except SessionPasswordNeededError:
            await client.sign_in(password=getpass.getpass("two-step password: "))
            return


async def code_login(client: TelegramClient) -> None:
    phone = input("phone: ").strip()
    sent = await client.send_code_request(phone)
    print(describe(sent))
    while True:
        answer = input("code (or r to resend): ").strip()
        if answer.lower() == "r":
            sent = await client(functions.auth.ResendCodeRequest(phone, sent.phone_code_hash))
            print(describe(sent))
            continue
        try:
            await client.sign_in(phone, answer, phone_code_hash=sent.phone_code_hash)
        except SessionPasswordNeededError:
            await client.sign_in(password=getpass.getpass("two-step password: "))
        except (PhoneCodeInvalidError, PhoneCodeExpiredError) as error:
            print(f"rejected: {error.__class__.__name__}, try again or r to resend")
            continue
        break


async def main() -> None:
    env = Path(sys.argv[1])
    values = read_env(env)
    client = TelegramClient(
        StringSession(), int(values["TG_API_ID"]), values["TG_API_HASH"].strip(),
    )
    await client.connect()
    if len(sys.argv) > 2 and sys.argv[2] == "qr":
        await qr_login(client)
    else:
        await code_login(client)
    me = await client.get_me()
    write_session(env, client.session.save())
    await client.disconnect()
    print(f"signed in as @{me.username}, TG_SESSION written to {env}")


if __name__ == "__main__":
    asyncio.run(main())
