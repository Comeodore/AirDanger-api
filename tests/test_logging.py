import logging

from app.main import _TrimAccessLog
from app.push import _DropPingAckNoise


def record(path: str, status: int, method: str = "GET") -> logging.LogRecord:
    return logging.LogRecord(
        name="uvicorn.access", level=logging.INFO, pathname="", lineno=0,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:1", method, path, "1.1", status), exc_info=None,
    )


def test_a_successful_health_probe_is_not_logged():
    assert _TrimAccessLog().filter(record("/health", 200)) is False


def test_a_failing_health_probe_is_logged():
    assert _TrimAccessLog().filter(record("/health", 500)) is True


def test_every_other_request_is_still_logged():
    assert _TrimAccessLog().filter(record("/devices", 200, "POST")) is True


def test_the_client_address_is_trimmed_off():
    entry = record("/devices", 400, "POST")
    _TrimAccessLog().filter(entry)
    assert entry.msg % entry.args == "POST /devices HTTP/1.1 -> 400"


def aioapns_record(msg: str, arg: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="aioapns", level=logging.WARNING, pathname="", lineno=0,
        msg=msg, args=(arg,), exc_info=None,
    )


def test_keepalive_ping_acks_are_not_logged():
    entry = aioapns_record("Unknown event: %s", "<PingAckReceived ping_data:6169>")
    assert _DropPingAckNoise().filter(entry) is False


def test_other_unknown_apns_events_still_log():
    entry = aioapns_record("Unknown event: %s", "<SomethingElse>")
    assert _DropPingAckNoise().filter(entry) is True
