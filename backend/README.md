# Backend

FastAPI service implementing `../openapi.yaml`, backed by a SQLAlchemy
database (see `_docs/process.md` for the stage that added it).

## Setup

    cd backend
    make sync

## Database

Persistence is via SQLAlchemy, so any SQLAlchemy-supported database
works. Which one to connect to is controlled by `KANBAN_DATABASE_URL` (any
SQLAlchemy database URL), read once at process start. Left unset, it
defaults to a local SQLite file (`backend/kanban.db`), created
automatically on first run — no setup needed for local dev.

To use Postgres (or another real database) instead:

    uv add "psycopg[binary]"   # or your driver of choice
    KANBAN_DATABASE_URL="postgresql+psycopg://user:pass@host/db" make run

By default the test suite runs against an isolated in-memory SQLite
database (`tests/conftest.py` sets `KANBAN_DATABASE_URL` before the app is
imported); an explicitly-set `KANBAN_DATABASE_URL` in the environment
overrides that.

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

## Serving the frontend

In production the backend also serves the built frontend (see
`_docs/deployment-plan.md`), so the app is same-origin and only one
container is needed. Set `KANBAN_STATIC_DIR` to the built frontend's
`dist/client` directory: `/assets/*` is served as static files, and any
other non-`/api` path returns `index.html` so client-side routes and deep
links work on refresh. `/api/*` keeps its normal JSON behavior, including
a JSON 404 for unmatched API routes. Left unset (the default), no static
routes are registered — local dev and the test suite are unaffected.

To try it locally, build the frontend with the production API base URL
and point the backend at the output:

    cd frontend && VITE_API_BASE_URL=/api npm run build
    cd ../backend
    KANBAN_STATIC_DIR=../frontend/dist/client make run

Then open the backend's port in a browser and refresh a deep link (e.g.
`/boards/<id>`) to confirm it doesn't 404.

## Test

    make test
