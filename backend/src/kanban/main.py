from fastapi import FastAPI

from kanban.errors import register_exception_handlers
from kanban.locking import SerializeRequestsMiddleware
from kanban.routers import auth, boards, columns, members, share_links, tasks, users

app = FastAPI(title="Mini Kanban Board API")
register_exception_handlers(app)

# Mock stage only (see _docs/process.md): the in-memory Store is shared,
# mutable, and not thread-safe, yet FastAPI dispatches sync `def` route
# handlers to a thread pool, so concurrent requests can interleave mid
# read-modify-write (e.g. two concurrent signups with the same email, or
# two concurrent transfer-ownership calls both passing their authorization
# check before either mutates state). A single process-wide lock that
# serializes full request handling is sufficient here; it will be replaced
# by real per-record persistence/transactions in the Persistence stage.
#
# This is added as a raw ASGI middleware class (not `@app.middleware("http")`,
# which wraps into Starlette's BaseHTTPMiddleware) deliberately: BaseHTTPMiddleware
# runs the downstream app in a separate anyio task per request and shuttles the
# response back over a memory stream, and that machinery does not play well with
# holding a lock around the entire request when many requests are dispatched onto
# one event loop concurrently (observed hangs in manual testing). A plain ASGI
# middleware has no such indirection: it just awaits the wrapped app directly
# under the lock.
app.add_middleware(SerializeRequestsMiddleware)

app.include_router(auth.router)
app.include_router(boards.router)
app.include_router(columns.router)
app.include_router(members.router)
app.include_router(share_links.board_router)
app.include_router(share_links.redeem_router)
app.include_router(tasks.router)
app.include_router(users.router)


@app.get("/api/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}
