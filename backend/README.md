# Backend

FastAPI service implementing `../openapi.yaml`, backed by an in-memory
mock store (see `_docs/process.md` for the persistence stage that will
replace it).

## Setup

    cd backend
    uv sync

## Run

    uv run uvicorn kanban.main:app --reload --port 8000

The session cookie is `Secure` by default, so a browser or `curl` accessing
the API over plain HTTP (as above, on `localhost`) will receive it on
signup/signin but **won't send it back** on later requests — every
subsequent request looks unauthenticated (401) with no obvious reason why.
For local HTTP development, disable it:

    KANBAN_SECURE_COOKIES=false uv run uvicorn kanban.main:app --reload --port 8000

Leave it unset (or `true`) for any real deployment, where the API is served
over HTTPS.

## Test

    uv run pytest
