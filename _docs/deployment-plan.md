# Deployment Plan

How the app goes from "runs on localhost" to a public URL. Follows the
shape of the course's deployment guide
([Deploy a full-stack app with AI coding](https://aishippingblog.com/p/deploy-a-full-stack-app-with-ai-coding)):
one container, FastAPI serving the built frontend, Postgres in
production, Docker Compose locally, GitHub Actions for CI/CD.

Each step is a stage in the sense of [process.md](process.md): finish it,
run a Codex review, commit, then move on.

## Target shape

- **One container.** A two-stage Docker build: Bun builds the frontend
  into static files, then a Python image runs the backend with those
  files copied in. FastAPI serves `/api/*` as today and the static
  frontend for everything else, with an `index.html` fallback so
  TanStack Router's client-side routes and deep links work.
- **Same origin.** Frontend and API share one origin in production, so
  the session cookie is first-party and CORS is only needed for local
  dev and Lovable previews (already handled via `KANBAN_CORS_ORIGINS`).
- **Postgres in production**, SQLite stays the local default. The
  backend is already SQLAlchemy-backed and switches via
  `KANBAN_DATABASE_URL`; only the driver needs adding.
- **HTTPS at the edge.** The session cookie is `Secure` by default; the
  deployment must terminate TLS in front of the container.
- **Single worker.** The backend serializes requests with a process-wide
  lock (see `backend/src/kanban/main.py`), so run exactly one uvicorn
  worker per container. Scale by resizing, not by adding workers.

## Steps

### 1. Static frontend build (Lovable) — done

The frontend's build target is switched from Cloudflare Workers SSR to
TanStack Start SPA mode with Nitro disabled: `vite build` emits a plain
static folder at `frontend/dist/client` with a prerendered `index.html`
shell and no server runtime. The `index.html` fallback for unknown paths
is the backend's job (step 2). Details in
[frontend_specs.md](frontend_specs.md#build-target).

### 2. FastAPI serves the frontend — done

- Mount the built frontend directory (`dist/client`; path from an env
  var, e.g. `KANBAN_STATIC_DIR`; when unset, serve nothing, as in local
  dev). Serve `/assets/*` as files and return `index.html` for any other
  non-`/api` path.
- Build the frontend with `VITE_API_BASE_URL=/api` so it calls the API
  relative to its own origin instead of `http://localhost:8000/api`.
- Verify locally: build the frontend, point the backend at the output,
  open the app on the backend's port, refresh a deep link.

Details in [backend/README.md](../backend/README.md#serving-the-frontend).

### 3. Dockerfile — done

Two-stage build in [Dockerfile](../Dockerfile) at the repo root (it needs
both `frontend/` and `backend/`):

- Frontend stage (`oven/bun`): `bun install --frozen-lockfile && bun run
  build` with `VITE_API_BASE_URL=/api`. Bun rather than `npm ci` because
  the Lovable-managed frontend only ships a `bun.lock`, no
  `package-lock.json`.
- Backend stage (`astral-sh/uv` Python 3.12 image): `uv sync --frozen
  --no-dev`, copies `dist/client` from the first stage to `/app/static`,
  sets `KANBAN_STATIC_DIR` to it, and runs `uvicorn` with `--workers 1`
  on port 8000.

The `frontend/` submodule must be checked out for the build to work
(`git submodule update --init`). Verified locally with
`docker build -t mini-kanban . && docker run -p 8000:8000 -e
KANBAN_SECURE_COOKIES=false mini-kanban`: `/api/health` returns JSON,
`/` and a refreshed deep link (`/boards/<id>`) both return the
`index.html` shell, `/assets/*` is served as files, and unknown `/api/*`
paths still get a JSON 404. Without a database URL the container uses a
SQLite file inside its own filesystem, so data is lost when the
container is removed; step 4 wires up Postgres.

### 4. Postgres + Docker Compose — done

- `psycopg[binary]` added to the backend; no code changes were needed.
  `KANBAN_DATABASE_URL=postgresql+psycopg://...` works end to end,
  including `create_all` at startup, and the full backend test suite
  passes against Postgres (`make test-pg`) — no SQLite-only assumptions
  turned up.
- [docker-compose.yml](../docker-compose.yml) at the root: `db`
  (`postgres:17`, `pg_isready` health check, named volume `pgdata`,
  port 5432 published on loopback only so tests can reach it from the
  host) and `app`
  (built from the Dockerfile, `depends_on` the healthy db,
  `KANBAN_DATABASE_URL` pointing at `db`, `KANBAN_SECURE_COOKIES=false`,
  port 8000 published).
- Root [Makefile](../Makefile): `build`, `up`, `down`, `test`, `test-pg`.
- Verified locally: `make up`, `/api/health` returns JSON, `/` returns
  the frontend shell, signup + create board via the API, `docker compose
  down` then `up` again — the board (and the session cookie, since
  sessions are in the database too) survived the restart.

### 5. CI (GitHub Actions)

On every push and PR: check out with submodules, run the backend tests,
build the Docker image, and start it with Compose.

Then an end-to-end Playwright test in `e2e/` at the repo root, run
against that running Compose stack, covering the sharing flow — the one
piece of behavior that only exists across two real user sessions and
can't be exercised by either side's own test suite:

1. Sign up as user A (session 1); create a board (seeded by default, but
   create one explicitly to also cover that path).
2. From board settings, create a view or edit share link and copy its
   token/URL.
3. In a separate browser context (session 2), sign up as user B and open
   the share link; confirm it lands on user A's board with the granted
   role.
4. As user B, move a card to a different column (or edit its title, if
   the link was view-only — then assert the edit control is absent
   instead).
5. Reload user A's session and confirm the change is visible.

Step 5 is a reload, not a live push: per
[specs.md](specs.md#concurrency), this app has no real-time sync, so
proving the change persisted and is visible on refresh is the correct
assertion here, not that session 1 updates without reloading.

Treat this as a required CI step, not a stretch goal — it's the only
test that exercises share-link redemption and cross-user permissions end
to end against the real backend and database, which the spec's unit and
interaction-layer tests in [specs.md](specs.md#testing-approach) don't
cover.

### 6. Deploy

Push the image to a registry and run it with a managed Postgres and TLS
in front. The course guide uses AWS (CloudFormation, single EC2
instance, GitHub Actions deploying via OIDC). That's the default; a
platform like Render, Railway, or Fly.io is a simpler alternative with
the same container. Deploy on push to `main` after CI passes, then
verify `GET /api/health` on the public URL.

Production configuration:

| Variable | Production value |
| --- | --- |
| `KANBAN_DATABASE_URL` | Postgres URL (secret) |
| `KANBAN_STATIC_DIR` | path to the built frontend inside the image |
| `KANBAN_SECURE_COOKIES` | unset (defaults to `true`) |
| `KANBAN_CORS_ORIGINS` | unset unless a Lovable preview should hit prod |

## Open decisions

- **Hosting provider.** AWS per the course, or a simpler PaaS. Decide
  before step 6; steps 1–5 are provider-independent.

## Out of scope

Multiple workers or horizontal scaling (blocked by the request lock),
database migrations tooling (tables are created at startup; revisit when
the schema first changes after launch), and custom domains.
