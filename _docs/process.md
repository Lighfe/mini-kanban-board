# Process

How this project moves from spec to shipped code. Stage-based, not
role-based — there's no separate PM/SWE/QA agent split, just one agent
working through the stages below with a review checkpoint at the end of
each.

## Stages

Stages 1–5 are done; stage 6 is in progress.

1. **Frontend prototype** — a mocked-backend React build per
   [frontend_specs.md](frontend_specs.md), built and iterated via Lovable.
2. **API contract** — extract an `openapi.yaml` from the frontend's mock
   client (`src/api/mockClient.ts`), defining the real endpoints and
   shapes needed to swap the mock for a real backend.
3. **Backend implementation** — build `backend/` against the contract.
   Built with FastAPI (Python).
4. **Persistence** — replace any in-memory/mock store with a real
   database.
5. **Integration** — swap `mockClient.ts`'s internals for real API calls
   (per its design in frontend_specs.md, no UI changes needed), and
   verify the app end-to-end against the real backend.
6. **Deployment** — one container (FastAPI serving the static frontend
   build), Postgres, Docker Compose locally, GitHub Actions CI/CD. Broken
   into steps in [deployment-plan.md](deployment-plan.md).

## Tools

- **Claude Code** — default agent for backend, persistence,
  integration, and deployment work.
- **Lovable** (MCP plugin) — builds and iterates the frontend, including
  its build configuration.
- **Codex** (plugin) — code review. Run a Codex review at the end of
  every stage, before moving to the next.

## Out of scope

No backlog file, task template, or per-role agent instructions. Work is
driven directly from [specs.md](specs.md),
[frontend_specs.md](frontend_specs.md), and
[deployment-plan.md](deployment-plan.md). Finished plans and resolved
issue logs move to [archive/](archive/) and are not kept current.
