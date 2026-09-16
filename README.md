# Mini Kanban Board

A multi-user kanban board app. Each user gets personal boards to organize
tasks, and can share individual boards with others (view or edit access)
for collaborative work.

Built as a project for the [AI Dev Tools
Zoomcamp](https://aishippingblog.com/p/build-and-ship-a-full-stack-app-with),
working through a spec-first process: frontend prototype against a mocked
backend, an API contract extracted from that mock, a real backend
implementation and integration, then deployment — see
[_docs/process.md](_docs/process.md).

See [_docs/specs.md](_docs/specs.md) for the full design spec and
[openapi.yaml](openapi.yaml) for the API contract.

## Stack

**Frontend** — [frontend/](frontend/): TanStack Start (React) + TanStack
Router/Query, shadcn/ui on Radix primitives, Tailwind CSS, Vite.

**Backend** — [backend/](backend/): FastAPI + SQLAlchemy, implementing
`openapi.yaml`. Runs on SQLite by default; point `KANBAN_DATABASE_URL` at
another SQLAlchemy-supported database (e.g. Postgres, with the right
driver installed) to use that instead.

## Frontend

[frontend/](frontend/) is a git submodule tracking a Lovable-managed
project ([board-buddy](https://github.com/Lighfe/board-buddy)), originally
built against a fully mocked backend — see
[_docs/frontend_specs.md](_docs/frontend_specs.md). Changes to it go
through Lovable, not local edits. After cloning, run `git submodule
update --init` to fetch it; `git submodule update --remote frontend`
pulls the latest changes later.

## Backend

[backend/](backend/) is a FastAPI service backed by SQLAlchemy. See
[backend/README.md](backend/README.md) for setup, database configuration,
running locally, and tests. In short:

    cd backend
    make sync
    make run

## Deployment

Planned as a single container: FastAPI serves the static frontend build
and the API from one origin, with Postgres behind it. See
[_docs/deployment-plan.md](_docs/deployment-plan.md) for the steps and
current status.
