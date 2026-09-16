import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from kanban.errors import register_exception_handlers
from kanban.locking import SerializeRequestsMiddleware
from kanban.routers import auth, boards, columns, members, share_links, tasks, users

app = FastAPI(title="Mini Kanban Board API")
register_exception_handlers(app)

# The Store (kanban/store.py) is backed by a single process-wide
# SQLAlchemy Session (kanban/db.py) shared across requests, and FastAPI
# dispatches sync `def` route handlers to a thread pool, so concurrent
# requests can interleave mid read-modify-write (e.g. two concurrent
# signups with the same email, or two concurrent transfer-ownership calls
# both passing their authorization check before either mutates state). A
# single process-wide lock that serializes full request handling avoids
# that; it also commits the shared Session after each request (see
# locking.py).
#
# This is added as a raw ASGI middleware class (not `@app.middleware("http")`,
# which wraps into Starlette's BaseHTTPMiddleware) deliberately: BaseHTTPMiddleware
# runs the downstream app in a separate anyio task per request and shuttles the
# response back over a memory stream, and that machinery does not play well with
# holding a lock around the entire request when many requests are dispatched onto
# one event loop concurrently (observed hangs in manual testing). A plain ASGI
# middleware has no such indirection: it just awaits the wrapped app directly
# under the lock.
#
# Registered before CORSMiddleware below so that it ends up as the *inner*
# layer (Starlette wraps middleware in reverse registration order, outermost
# last): CORS must sit outside this lock, or every cross-origin preflight
# `OPTIONS` request — which CORSMiddleware answers directly without touching
# the Store — would needlessly queue behind it.
app.add_middleware(SerializeRequestsMiddleware)

# The frontend (Lovable-built, TanStack Start) runs on a different origin/port
# than this API, and its client sends the session cookie via
# `credentials: "include"`, which requires the API to echo back a specific
# (not "*") Allow-Origin plus Allow-Credentials. Extra origins (e.g. a
# deployed frontend URL) can be added via KANBAN_CORS_ORIGINS, comma-separated.
_extra_origins = [o.strip() for o in os.environ.get("KANBAN_CORS_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_extra_origins,
    allow_origin_regex=r"https?://localhost(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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


# Serves the built frontend (see _docs/deployment-plan.md step 2). Left
# unset, no static routes are registered, so local dev (no built frontend
# on disk) and the test suite are unaffected. Registered last so the
# `/{full_path:path}` catch-all doesn't shadow the `/api/*` routers above.
_static_dir = os.environ.get("KANBAN_STATIC_DIR")
if _static_dir:
    static_dir = Path(_static_dir)
    index_file = static_dir / "index.html"
    if not index_file.is_file():
        raise RuntimeError(f"KANBAN_STATIC_DIR={static_dir} has no index.html")
    app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def serve_frontend(full_path: str) -> FileResponse:
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404)
        return FileResponse(index_file)
