"""Process-wide request serialization for the shared database Session.

See kanban/main.py for why this exists and why it's a raw ASGI middleware
class rather than `@app.middleware("http")` (Starlette's BaseHTTPMiddleware).
"""

import asyncio
import json
import logging

from kanban.db import session as db_session

logger = logging.getLogger(__name__)

_COMMIT_FAILED_BODY = json.dumps({"message": "Could not save changes"}).encode()
_COMMIT_FAILED_RESPONSE = [
    {"type": "http.response.start", "status": 500,
     "headers": [(b"content-type", b"application/json"),
                 (b"content-length", str(len(_COMMIT_FAILED_BODY)).encode())]},
    {"type": "http.response.body", "body": _COMMIT_FAILED_BODY},
]


class SerializeRequestsMiddleware:
    """Holds a single asyncio.Lock while each HTTP request's handler runs
    and its transaction commits, so no two requests' handler code — sync (dispatched to a
    thread pool by Starlette) or async — ever executes concurrently
    against the shared, process-wide SQLAlchemy Session (kanban/db.py).

    Also commits that Session after every request that completes
    normally, and rolls it back if the request raised, so each request
    gets its own short-lived transaction — matching the usual
    one-session-per-request pattern, just without a per-request Session
    object (kanban/db.py explains why one process-wide Session is used
    instead).

    Network I/O happens outside the lock: the request body is read in
    full before acquiring it, so a client that never finishes sending
    its body only stalls its own request; the response is captured
    while the lock is held and sent after the commit, so the client
    never sees success for a write that failed to commit (a failed
    commit is rolled back and answered with a 500 instead).

    The lock is (re)created lazily, bound to whichever event loop is
    currently running, rather than once at import time. A real deployment
    has exactly one event loop for the app's entire lifetime, so this is
    equivalent to a single persistent lock there. It matters for tests:
    `asyncio.Lock` permanently binds itself to the first event loop that
    ever actually contends for it, and this middleware instance is a
    singleton attached to the (session-wide) `app` object, so without
    rebinding, a lock that got contended once under one test's event loop
    (e.g. one `with TestClient(app) as client:` block) would raise
    "bound to a different event loop" the next time a *different* test's
    event loop contends for it. Recreating the lock whenever the running
    loop has changed avoids that without weakening the guarantee: within
    any single event loop (the only case that matters — one per real
    deployment, one per properly-scoped test), all requests still share
    one lock and are still fully serialized.
    """

    def __init__(self, app):
        self.app = app
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None

    def _get_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_messages = []
        while True:
            message = await receive()
            request_messages.append(message)
            if message["type"] != "http.request" or not message.get("more_body", False):
                break

        async def replay_receive():
            if request_messages:
                return request_messages.pop(0)
            return await receive()

        response_messages = []

        async def capture_send(message):
            response_messages.append(message)

        async with self._get_lock():
            try:
                await self.app(scope, replay_receive, capture_send)
            except Exception:
                db_session.rollback()
                raise
            try:
                db_session.commit()
            except Exception:
                logger.exception("commit failed; rolled back and answered 500")
                db_session.rollback()
                response_messages = _COMMIT_FAILED_RESPONSE

        for message in response_messages:
            await send(message)
