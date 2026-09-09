from fastapi import FastAPI

from kanban.errors import register_exception_handlers
from kanban.routers import auth, boards, columns, members, share_links, tasks, users

app = FastAPI(title="Mini Kanban Board API")
register_exception_handlers(app)

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
