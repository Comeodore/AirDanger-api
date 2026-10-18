import json
from datetime import UTC, datetime, timedelta

from app.brain import NO_TEXT, STALE, Brain, commit_request, process_request
from app.danger_service import DangerService
from test_pipeline import make_ctx

T0 = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
DEVICE = {"token": "aa" * 32, "warnings": True, "sound": "pulse.caf",
          "critical": True, "critical_volume": 0.5}

SCRIPT = [
    ("kyiv_nebo", "Балістика на Київ", 0),
    ("kyiv_nebo", "Ще Циркон на Київ", 10),
    ("kyiv_nebo", "Відбій", 300),
    ("kyiv_nebo", "Відбій", 360),
    ("kyiv_nebo", "Загроза балістики з Курська", 600),
    ("kyiv_nebo", "Балістика на Київ", 660),
    ("kyiv_nebo", "Шахеди, курсом на Оболонь!", 700),
    ("kyiv_nebo", "Відбій", 3 * 3600),
    ("kyiv_nebo", "Швидкісна ціль на Київ!", 3 * 3600 + 60),
]

DANGER = DangerService()


def brain_records() -> list[tuple]:
    state: dict = {}
    records = []
    for channel, text, offset in SCRIPT:
        ts = T0 + timedelta(seconds=offset)
        brain = Brain(DANGER, state)
        result = brain.handle({"channel": channel, "text": text, "ts": ts.isoformat()},
                              now=ts, devices=[DEVICE])
        if "commit" in result:
            brain.commit(result["commit"])
            commit = result["commit"]
            if commit["kind"] == "clear":
                records.append((channel, "all_clear", "clear", text, True))
            else:
                records.append((channel, commit["type"], commit["severity"], text, True))
        elif result.get("suppressed"):
            records.append((channel, result["type"], result["severity"], text, False))
        state = brain.state()
    return records


async def test_brain_makes_the_same_calls_as_the_server():
    ctx = make_ctx(push_warnings=True)
    for channel, text, offset in SCRIPT:
        await ctx.handle_message(channel, text, T0 + timedelta(seconds=offset))
    assert brain_records() == ctx.db.pushes
    assert [p[4] for p in ctx.db.pushes].count(True) >= 4


def test_critical_device_gets_a_critical_payload():
    brain = Brain(DANGER)
    result = brain.handle({"channel": "kyiv_nebo", "text": "Балістика на Київ",
                           "ts": T0.isoformat()}, now=T0, devices=[DEVICE])
    [batch] = result["send"]
    assert batch["tokens"] == [DEVICE["token"]]
    assert batch["payload"]["aps"]["interruption-level"] == "critical"
    assert batch["payload"]["aps"]["sound"] == {"critical": 1, "name": "pulse.caf",
                                                "volume": 0.5}


def test_a_post_is_read_from_its_page_block():
    block = ('<div class="tgme_widget_message" data-post="kyiv_nebo/123">'
             '<div class="tgme_widget_message_text js-message_text" dir="auto">'
             'Балістика на Київ</div>'
             '<time datetime="2026-08-01T12:00:00+00:00"></time></div>')
    result = Brain(DANGER).handle({"channel": "kyiv_nebo", "html": block},
                                  now=T0 + timedelta(seconds=5), devices=[])
    assert result["outcome"] == "alert"
    assert result["text"] == "Балістика на Київ"
    assert result["age"] == 5.0


def test_a_block_without_text_is_skipped():
    block = '<div class="tgme_widget_message" data-post="kyiv_nebo/124"></div>'
    result = Brain(DANGER).handle({"channel": "kyiv_nebo", "html": block},
                                  now=T0, devices=[])
    assert result == {"outcome": NO_TEXT}


def test_an_old_post_is_not_decided():
    brain = Brain(DANGER)
    result = brain.handle({"channel": "kyiv_nebo", "text": "Балістика на Київ",
                           "ts": T0.isoformat()}, now=T0 + timedelta(minutes=6), devices=[])
    assert result["outcome"] == STALE
    assert brain.state() == Brain(DANGER).state()


def test_a_reply_carries_the_state_to_keep_once_the_push_is_delivered():
    request = {"state": {}, "post": {"channel": "kyiv_nebo", "text": "Балістика на Київ",
                                     "ts": T0.isoformat()},
               "now": T0.isoformat(), "devices": [DEVICE]}
    reply = json.loads(json.dumps(process_request(DANGER, request)))
    assert reply["result"]["commit"]["kind"] == "threat"
    assert reply["state"]["ledger_at"] is None
    assert reply["committed_state"]["ledger_at"] == T0.isoformat()
    later = {**request, "state": reply["committed_state"],
             "post": {**request["post"], "ts": (T0 + timedelta(seconds=30)).isoformat()},
             "now": (T0 + timedelta(seconds=30)).isoformat()}
    assert process_request(DANGER, later)["result"]["suppressed"] is True
    committed = commit_request(DANGER, {"state": reply["state"],
                                        "commit": reply["result"]["commit"]})
    assert committed["state"] == reply["committed_state"]


def test_an_undelivered_push_leaves_the_cooldown_untouched():
    request = {"state": {}, "post": {"channel": "kyiv_nebo", "text": "Балістика на Київ",
                                     "ts": T0.isoformat()},
               "now": T0.isoformat(), "devices": [DEVICE]}
    reply = process_request(DANGER, request)
    retry = {**request, "state": reply["state"],
             "post": {**request["post"], "ts": (T0 + timedelta(seconds=30)).isoformat()},
             "now": (T0 + timedelta(seconds=30)).isoformat()}
    assert "send" in process_request(DANGER, retry)["result"]


def test_a_dry_run_commits_as_if_delivered():
    request = {"state": {}, "post": {"channel": "kyiv_nebo", "text": "Балістика на Київ",
                                     "ts": T0.isoformat()},
               "now": T0.isoformat(), "devices": [], "assume_delivered": True}
    reply = process_request(DANGER, request)
    assert reply["state"] == reply["committed_state"]
    assert reply["state"]["ledger_at"] == T0.isoformat()


def test_no_committed_state_without_a_push():
    request = {"state": {}, "post": {"channel": "kyiv_nebo", "text": "Шахеди на Київ",
                                     "ts": T0.isoformat()},
               "now": T0.isoformat(), "devices": [DEVICE]}
    assert "committed_state" not in process_request(DANGER, request)
