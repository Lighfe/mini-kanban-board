# Backend

FastAPI service implementing `../openapi.yaml`, backed by an in-memory
mock store (see `_docs/process.md` for the persistence stage that will
replace it).

## Setup

    cd backend
    uv sync

## Run

    uv run uvicorn kanban.main:app --reload --port 8000

## Test

    uv run pytest
