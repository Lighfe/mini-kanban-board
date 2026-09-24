"""SerializeRequestsMiddleware at the ASGI level, with a stub downstream app.

Covers two review findings (2026-09-24):
- a client that never finishes sending its request body must not hold
  the process-wide lock and block every other request;
- the response must not reach the client before the commit succeeds.
"""

import asyncio

import pytest

import kanban.locking as locking
from kanban.locking import SerializeRequestsMiddleware


async def echo_app(scope, receive, send):
    """Reads the full body, then responds 200 with it."""
    body = b""
    while True:
        message = await receive()
        body += message.get("body", b"")
        if not message.get("more_body", False):
            break
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": body})


def http_scope():
    return {"type": "http", "method": "POST", "path": "/", "headers": []}


def body_receive(*chunks):
    messages = [{"type": "http.request", "body": c, "more_body": i < len(chunks) - 1}
                for i, c in enumerate(chunks)]

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}
    return receive


class Sink:
    def __init__(self):
        self.messages = []

    async def __call__(self, message):
        self.messages.append(message)

    @property
    def status(self):
        return next(m["status"] for m in self.messages if m["type"] == "http.response.start")

    @property
    def body(self):
        return b"".join(m.get("body", b"") for m in self.messages if m["type"] == "http.response.body")


@pytest.fixture
def commits(monkeypatch):
    calls = []
    monkeypatch.setattr(locking.db_session, "commit", lambda: calls.append("commit"))
    monkeypatch.setattr(locking.db_session, "rollback", lambda: calls.append("rollback"))
    return calls


def test_body_chunks_reach_the_app(commits):
    mw = SerializeRequestsMiddleware(echo_app)
    sink = Sink()
    asyncio.run(mw(http_scope(), body_receive(b"ab", b"cd"), sink))
    assert (sink.status, sink.body) == (200, b"abcd")
    assert commits == ["commit"]


def test_stalled_request_body_does_not_block_other_requests(commits):
    mw = SerializeRequestsMiddleware(echo_app)

    async def scenario():
        never = asyncio.Event()

        async def stalled_receive():
            await never.wait()

        stalled = asyncio.create_task(mw(http_scope(), stalled_receive, Sink()))
        await asyncio.sleep(0.01)  # let the stalled request start
        sink = Sink()
        await asyncio.wait_for(mw(http_scope(), body_receive(b"ok"), sink), timeout=1)
        stalled.cancel()
        return sink

    sink = asyncio.run(scenario())
    assert (sink.status, sink.body) == (200, b"ok")


def test_response_is_sent_only_after_commit(monkeypatch):
    events = []
    monkeypatch.setattr(locking.db_session, "commit", lambda: events.append("commit"))
    monkeypatch.setattr(locking.db_session, "rollback", lambda: events.append("rollback"))

    async def send(message):
        events.append(message["type"])

    mw = SerializeRequestsMiddleware(echo_app)
    asyncio.run(mw(http_scope(), body_receive(b"x"), send))
    assert events == ["commit", "http.response.start", "http.response.body"]


def test_commit_failure_returns_500_and_rolls_back(monkeypatch):
    events = []

    def failing_commit():
        events.append("commit")
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(locking.db_session, "commit", failing_commit)
    monkeypatch.setattr(locking.db_session, "rollback", lambda: events.append("rollback"))

    mw = SerializeRequestsMiddleware(echo_app)
    sink = Sink()
    asyncio.run(mw(http_scope(), body_receive(b"x"), sink))
    assert sink.status == 500
    assert events == ["commit", "rollback"]


def test_app_exception_rolls_back_and_propagates(commits):
    async def boom(scope, receive, send):
        raise ValueError("boom")

    mw = SerializeRequestsMiddleware(boom)
    with pytest.raises(ValueError):
        asyncio.run(mw(http_scope(), body_receive(b""), Sink()))
    assert commits == ["rollback"]


def test_oversized_body_is_rejected_before_the_app_runs(commits, monkeypatch):
    monkeypatch.setattr(locking, "MAX_BODY_BYTES", 8)
    called = []

    async def app(scope, receive, send):
        called.append(1)

    mw = SerializeRequestsMiddleware(app)
    sink = Sink()
    asyncio.run(mw(http_scope(), body_receive(b"12345", b"67890"), sink))
    assert sink.status == 413
    assert called == [] and commits == []


def test_body_at_the_limit_is_accepted(commits, monkeypatch):
    monkeypatch.setattr(locking, "MAX_BODY_BYTES", 8)
    mw = SerializeRequestsMiddleware(echo_app)
    sink = Sink()
    asyncio.run(mw(http_scope(), body_receive(b"1234", b"5678"), sink))
    assert (sink.status, sink.body) == (200, b"12345678")
