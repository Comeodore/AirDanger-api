import asyncio
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from telethon.errors import FloodWaitError

from app.ingest import Ingest
from app.mtproto import MtprotoSource
from test_ingest import CHANNEL, make_config


def post(msg_id: int, text: str, age_sec: float = 1.0) -> SimpleNamespace:
    return SimpleNamespace(id=msg_id, message=text,
                           date=datetime.now(UTC) - timedelta(seconds=age_sec))


class FakeClient:
    def __init__(self, *batches) -> None:
        self.batches = list(batches)

    async def get_messages(self, entity, limit):
        batch = self.batches.pop(0) if len(self.batches) > 1 else self.batches[0]
        if isinstance(batch, Exception):
            raise batch
        return batch


class Sink:
    def __init__(self, **config) -> None:
        self.config = make_config(**config)
        self.received: list[str] = []
        self.ingest = Ingest(self.config, self.on_message)
        self.source = MtprotoSource(self.config, self.ingest, client_factory=lambda: None)

    async def on_message(self, channel: str, text: str, ts: datetime) -> None:
        self.received.append(text)


async def test_first_pull_is_a_baseline_and_later_pulls_deliver_new_posts():
    sink = Sink()
    client = FakeClient([post(100, "старе")],
                        [post(100, "старе"), post(101, "Балістика на Київ")])
    await sink.source.pull(client, CHANNEL, None, baseline=True)
    await sink.source.pull(client, CHANNEL, None)
    assert sink.received == ["Балістика на Київ"]


async def test_the_first_path_to_see_a_post_wins():
    sink = Sink()
    client = FakeClient([post(100, "старе")], [post(101, "Балістика на Київ")])
    await sink.source.pull(client, CHANNEL, None, baseline=True)
    await sink.source.pull(client, CHANNEL, None)
    await sink.ingest.deliver(CHANNEL, 101, "Балістика на Київ", datetime.now(UTC),
                              source="update")
    await sink.ingest.deliver(CHANNEL, 101, "Балістика на Київ", datetime.now(UTC))
    assert sink.received == ["Балістика на Київ"]


async def test_a_late_tme_baseline_does_not_replay_its_page_but_new_posts_flow():
    sink = Sink()
    client = FakeClient([post(100, "старе")], [post(100, "старе"), post(103, "нове")])
    await sink.source.pull(client, CHANNEL, None, baseline=True)
    assert sink.ingest.baseline("tme", CHANNEL, [97, 98, 99, 100, 101, 102])
    await sink.source.pull(client, CHANNEL, None)
    assert sink.received == ["нове"]


async def test_a_pull_marks_the_source_alive():
    sink = Sink()
    assert not sink.source.connected
    await sink.source.pull(FakeClient([post(100, "старе")]), CHANNEL, None, baseline=True)
    assert sink.source.connected


async def test_a_flood_wait_pauses_pulls_and_then_resumes():
    sink = Sink(mtproto_pull_sec=0.01)
    await sink.source.pull(FakeClient([post(100, "старе")]), CHANNEL, None, baseline=True)
    client = FakeClient(FloodWaitError(request=None, capture=0), [post(101, "Балістика на Київ")])
    task = asyncio.create_task(sink.source._pull_loop(client, CHANNEL, None, 0))
    try:
        for _ in range(300):
            if sink.received:
                break
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert sink.received == ["Балістика на Київ"]


async def test_pull_failures_are_reported_once_a_minute_and_recovery_is_logged(caplog):
    sink = Sink()
    with caplog.at_level(logging.INFO):
        sink.source._failed(CHANNEL, RuntimeError("internal issues"))
        sink.source._failed(CHANNEL, RuntimeError("internal issues"))
        sink.source._recovered(CHANNEL)
    failing = [r for r in caplog.records if "pull failing" in r.message]
    back = [r for r in caplog.records if "pull back after" in r.message]
    assert len(failing) == 1
    assert len(back) == 1


async def test_an_unauthorized_session_stops_mtproto_without_retrying(caplog):
    class Unauthorized:
        disconnected = False

        async def connect(self):
            return None

        async def is_user_authorized(self):
            return False

        async def disconnect(self):
            Unauthorized.disconnected = True

    sink = Sink()
    source = MtprotoSource(sink.config, sink.ingest, client_factory=Unauthorized)
    with caplog.at_level(logging.ERROR):
        await asyncio.wait_for(source.run(), timeout=1)
    assert Unauthorized.disconnected
    assert any("not authorized" in r.message for r in caplog.records)
