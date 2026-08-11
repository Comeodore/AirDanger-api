from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

STYLE = """
:root { color-scheme: light dark; }
body { font-family: -apple-system, system-ui, sans-serif; max-width: 680px;
       margin: 0 auto; padding: 24px 20px 48px; line-height: 1.55; }
h1 { font-size: 1.6em; margin-bottom: 0.2em; }
h2 { font-size: 1.15em; margin-top: 1.6em; }
p, li { color: light-dark(#333, #ccc); }
hr { border: none; border-top: 1px solid light-dark(#ddd, #333); margin: 2.2em 0; }
.muted { color: light-dark(#777, #888); font-size: 0.9em; }
"""

PRIVACY_HTML = f"""<!doctype html>
<html lang="uk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Air Danger — Політика конфіденційності</title>
<style>{STYLE}</style>
</head>
<body>
<h1>Політика конфіденційності</h1>
<p class="muted">Air Danger · чинна з 12 серпня 2026 р.</p>

<h2>Які дані ми обробляємо</h2>
<p>Застосунок не вимагає реєстрації та не збирає жодних персональних даних:
ані імені, ані електронної пошти, ані геолокації.</p>
<p>Єдине, що зберігає наш сервер, — анонімний токен пристрою Apple Push
Notification service (APNs). Він потрібен виключно для доставки сповіщень
і не повʼязаний з вашою особою.</p>

<h2>Як ми використовуємо токен</h2>
<ul>
<li>Надсилання push-сповіщень про загрози — єдина мета обробки.</li>
<li>Токен нікому не передається і не використовується для реклами чи аналітики.</li>
<li>Коли APNs повідомляє, що токен більше не дійсний, ми його видаляємо.</li>
</ul>

<h2>Чого в застосунку немає</h2>
<ul>
<li>Аналітики та трекінгу.</li>
<li>Реклами.</li>
<li>Сторонніх SDK.</li>
</ul>

<h2>Контакти</h2>
<p>З питань конфіденційності пишіть на
<a href="mailto:volodymyr.maksymchuk@gen.tech">volodymyr.maksymchuk@gen.tech</a>.</p>

<hr>

<h1>Privacy Policy</h1>
<p class="muted">Air Danger · effective August 12, 2026</p>
<p>The app requires no account and collects no personal data — no name, no
email, no location.</p>
<p>The only data our server stores is the anonymous Apple Push Notification
service (APNs) device token. It is used solely to deliver threat
notifications, is not linked to your identity, is never shared or used for
advertising or analytics, and is deleted once APNs reports it invalid.</p>
<p>The app contains no analytics, no tracking, no ads, and no third-party
SDKs.</p>
<p>Privacy questions:
<a href="mailto:volodymyr.maksymchuk@gen.tech">volodymyr.maksymchuk@gen.tech</a>.</p>
</body>
</html>"""

SUPPORT_HTML = f"""<!doctype html>
<html lang="uk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Air Danger — Підтримка</title>
<style>{STYLE}</style>
</head>
<body>
<h1>Підтримка</h1>
<p class="muted">Air Danger</p>

<h2>Що робить застосунок</h2>
<p>Air Danger надсилає push-сповіщення про балістичні загрози для Києва за
повідомленнями публічних моніторингових каналів. Це неофіційне джерело:
завжди реагуйте на офіційну повітряну тривогу та прямуйте в укриття.</p>

<h2>Не приходять сповіщення?</h2>
<ul>
<li>Відкрийте Налаштування → Сповіщення → Air Danger і увімкніть «Допуск сповіщень».</li>
<li>Переконайтеся, що на пристрої не увімкнено режим «Не турбувати» без дозволу для застосунку.</li>
<li>Перезапустіть застосунок — реєстрація пристрою оновиться автоматично.</li>
</ul>

<h2>Контакти</h2>
<p>Питання та пропозиції:
<a href="mailto:volodymyr.maksymchuk@gen.tech">volodymyr.maksymchuk@gen.tech</a>.</p>

<hr>

<h1>Support</h1>
<p>Air Danger sends push notifications about ballistic threats to Kyiv based
on public monitoring feeds. It is an unofficial source — always follow the
official air-raid alert and proceed to shelter.</p>
<p>If notifications do not arrive, allow them in Settings → Notifications →
Air Danger, then relaunch the app.</p>
<p>Contact:
<a href="mailto:volodymyr.maksymchuk@gen.tech">volodymyr.maksymchuk@gen.tech</a>.</p>
</body>
</html>"""


@router.get("/privacy", include_in_schema=False)
async def privacy() -> HTMLResponse:
    return HTMLResponse(PRIVACY_HTML)


@router.get("/support", include_in_schema=False)
async def support() -> HTMLResponse:
    return HTMLResponse(SUPPORT_HTML)
