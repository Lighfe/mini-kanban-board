# Backend

FastAPI service implementing `../openapi.yaml`, backed by an in-memory
mock store (see `_docs/process.md` for the persistence stage that will
replace it).

## Setup

    cd backend
    make sync

## Run

    make run

This sets `KANBAN_SECURE_COOKIES=false` for local HTTP development. The
session cookie is `Secure` by default, so a browser or `curl` accessing the
API over plain HTTP (as on `localhost`) would otherwise receive it on
signup/signin but **not send it back** on later requests — every subsequent
request would look unauthenticated (401) with no obvious reason why. Leave
it unset (or `true`) for any real deployment, where the API is served over
HTTPS:

    uv run uvicorn kanban.main:app --reload --port 8000

## CORS

The frontend runs on a different origin/port and sends the session cookie
via `credentials: "include"`, so any `http://localhost:<port>` or
`https://localhost:<port>` origin is allowed by default (with credentials).
For a non-localhost frontend origin (e.g. a deployed Lovable preview URL),
set `KANBAN_CORS_ORIGINS` to a comma-separated list of allowed origins.

## Test

    make test
