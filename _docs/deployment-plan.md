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

### 5. CI (GitHub Actions) — done

On every push and PR: frontend and backend tests in parallel, then build
the Docker image and start it with Compose.

Then an end-to-end Playwright test in `e2e/` at the repo root, run
against that running Compose stack, covering the sharing flow:

1. Sign up as user A (session 1); create a board and a card explicitly
   (don't rely on seeded sample data).
2. From board settings, create an editor share link and copy its
   token/URL.
3. In a separate browser context (session 2), open the share link
   unsigned-in first (covers sign-up-then-redeem), sign up as user B,
   and confirm it lands on user A's board as editor.
4. As user B, move the card to a different column.
5. Reload user A's session and confirm the move is visible.

Per [specs.md](specs.md#concurrency) this app has no real-time sync, so
step 5 checks persistence after a reload, not a live push.

Cover the viewer case separately (a viewer link opens the card read-only,
no save action, no drag) rather than branching it into the same test.

Implemented in [.github/workflows/ci.yml](../.github/workflows/ci.yml):
`backend-test` (`uv run pytest`) and `frontend-test` (`bun run build`) run
in parallel; `e2e` waits on both, then runs `docker compose up --build
-d`, polls `/api/health`, and runs the Playwright suite in
[e2e/](../e2e/) — a standalone Node/npm project, independent of the
frontend submodule's Bun toolchain — against the running container,
uploading the HTML report as an artifact on failure.

`frontend-test` builds rather than lints: `bun run lint` (the only
test-like script the frontend submodule exposes — it has no unit-test
runner) currently fails on 38 pre-existing `prettier/prettier` errors
(formatting only, no logic) across 9 already-committed files, and this
repo doesn't edit `frontend/` locally (it's Lovable-managed). Once
Lovable reformats those files and the submodule pointer is bumped on a
branch, add `bun run lint` back as a blocking step in `frontend-test`.

Verified locally by running the same sequence outside of CI: `docker
compose up --build -d`, poll `/api/health`, then `npx playwright test`
from `e2e/` against `http://localhost:8000` — both the editor
sharing-flow test and the viewer read-only test pass against the built
image.

### 6. Deploy

**Hosting: AWS, single CloudFormation stack.** A personal AdminAccess
IAM user via AWS SSO handles manual account setup/debugging; it is
separate from the scoped deploy role CI uses (below). CloudFormation
(not Terraform) is the IaC tool — it matches the course guide and keeps
learning effort on the CI/CD piece rather than splitting it across a
new IaC syntax too.

One stack holds EC2 (t3.micro/t4g.micro, free-tier eligible, running
the existing Docker image) and RDS Postgres (db.t4g.micro, single-AZ,
20GB gp3), created and destroyed together. Postgres data is lost on
each teardown — acceptable for now, since this runs ephemeral (most
sessions under 2 hours, never longer than 3 days), not continuously.
Splitting into a persistent-RDS stack plus an ephemeral-EC2 stack is a
possible future evolution if data ever needs to survive between runs,
but isn't built now.

No load balancer. Caddy runs as a second container alongside the app
container on the EC2 instance, terminating TLS via Let's Encrypt and
reverse-proxying to the app container's internal port — this matches
the plan's single-container/single-instance shape more closely than
adding an ALB. TLS needs a domain: a personally-owned domain (e.g.
`lighfe.dev`, ~$12-15/yr), reused across future projects via
subdomains, with this project on one subdomain of it. The DNS A record
is updated to the new EC2 instance's public IP on each deploy (no
Elastic IP reservation, so the IP changes per create/destroy cycle).

**Deploy trigger: manual, not push-to-main.** Given the short,
infrequent usage pattern, an always-on auto-deploy on every push
doesn't fit. Instead, a GitHub Actions workflow triggered manually via
`workflow_dispatch`, with two entry points: `deploy` (build+push image,
`aws cloudformation deploy`, update the DNS record) and `destroy`
(`aws cloudformation delete-stack`). This still exercises the CI/CD +
OIDC mechanics, which is the actual learning goal here — GitHub OIDC
federates to a scoped AWS IAM role with only the deploy/destroy
permissions needed, no long-lived AWS access keys stored in GitHub.

Secrets (`KANBAN_DATABASE_URL` etc.) come from CloudFormation outputs
or SSM Parameter Store, not hardcoded into the template or GitHub
secrets; GitHub Actions only holds the OIDC role ARN.
`KANBAN_SECURE_COOKIES` must stay at its default (unset) in production
— Compose only disables it for local plain-HTTP use. After a `deploy`
run, poll `GET /api/health` until it returns 200 before declaring the
deploy done.

Production configuration:

| Variable | Production value |
| --- | --- |
| `KANBAN_DATABASE_URL` | Postgres URL (secret, from CloudFormation output / SSM) |
| `KANBAN_STATIC_DIR` | path to the built frontend inside the image |
| `KANBAN_SECURE_COOKIES` | unset (defaults to `true`) |
| `KANBAN_CORS_ORIGINS` | unset unless a Lovable preview should hit prod |

## Out of scope

Multiple workers or horizontal scaling (blocked by the request lock),
database migrations tooling (tables are created at startup; revisit when
the schema first changes after launch), an Elastic IP or other stable
address across redeploys, and a persistent database across
deploy/destroy cycles (see step 6).
