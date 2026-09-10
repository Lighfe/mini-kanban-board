"""Process-wide request serialization for the shared database Session.

See kanban/main.py for why this exists and why it's a raw ASGI middleware
class rather than `@app.middleware("http")` (Starlette's BaseHTTPMiddleware).
"""

import asyncio

from kanban.db import session as db_session


class SerializeRequestsMiddleware:
    """Holds a single asyncio.Lock for the full duration of every HTTP
    request, so no two requests' handler code — sync (dispatched to a
    thread pool by Starlette) or async — ever executes concurrently
    against the shared, process-wide SQLAlchemy Session (kanban/db.py).

    Also commits that Session after every request that completes
    normally, and rolls it back if the request raised, so each request
    gets its own short-lived transaction — matching the usual
    one-session-per-request pattern, just without a per-request Session
    object (kanban/db.py explains why one process-wide Session is used
    instead).

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
        async with self._get_lock():
            try:
                await self.app(scope, receive, send)
            except Exception:
                db_session.rollback()
                raise
            else:
                db_session.commit()
