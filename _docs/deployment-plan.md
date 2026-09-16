# Deployment Plan

How the app goes from "runs on localhost" to a public URL. Follows the
shape of the course's deployment guide
([Deploy a full-stack app with AI coding](https://aishippingblog.com/p/deploy-a-full-stack-app-with-ai-coding)):
one container, FastAPI serving the built frontend, Postgres in
production, Docker Compose locally, GitHub Actions for CI/CD.

Each step is a stage in the sense of [process.md](process.md): finish it,
run a Codex review, commit, then move on.

## Target shape

- **One container.** A two-stage Docker build: Node builds the frontend
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

### 3. Dockerfile

Two-stage build at the repo root (it needs both `frontend/` and
`backend/`): Node stage runs `npm ci && npm run build` with
`VITE_API_BASE_URL=/api`; Python stage installs the backend with `uv`,
copies the static output in, and runs uvicorn on one worker. The
`frontend/` submodule must be checked out for the build to work
(`git submodule update --init`).

### 4. Postgres + Docker Compose

- Add the Postgres driver to the backend (`uv add "psycopg[binary]"`).
- `docker-compose.yml` at the root with two services: `db` (Postgres,
  with a health check) and `app` (built from the Dockerfile, with
  `KANBAN_DATABASE_URL` pointing at `db`, `KANBAN_SECURE_COOKIES=false`
  for plain-HTTP local use).
- `docker compose up --build` runs the full production-shaped stack
  locally. Run the backend test suite once against Postgres to catch any
  SQLite-only assumptions.

### 5. CI (GitHub Actions)

On every push and PR: check out with submodules, run the backend tests,
build the Docker image, start it with Compose, and smoke-test
`GET /api/health` plus the frontend root. End-to-end browser tests
(Playwright, as in the course guide) are a stretch goal, not a blocker;
the spec's interaction-layer tests in [specs.md](specs.md#testing-approach)
are the candidates.

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
