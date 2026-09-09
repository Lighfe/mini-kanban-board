from fastapi import FastAPI

app = FastAPI(title="Mini Kanban Board API")


@app.get("/api/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}
