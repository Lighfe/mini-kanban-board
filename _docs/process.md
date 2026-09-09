# Process

How this project moves from spec to shipped code. Stage-based, not
role-based — there's no separate PM/SWE/QA agent split, just one agent
working through the stages below with a review checkpoint at the end of
each.

## Stages

1. **Frontend prototype** — a mocked-backend React build per
   [frontend_specs.md](frontend_specs.md), built and iterated via Lovable.
   This stage is already underway there; this doc doesn't restate it.
2. **API contract** — extract an `openapi.yaml` from the frontend's mock
   client (`src/api/mockClient.ts`), defining the real endpoints and
   shapes needed to swap the mock for a real backend.
3. **Backend implementation** — build `backend/` against the contract.
   Language/framework is not decided yet; pick it when this stage starts.
4. **Persistence** — replace any in-memory/mock store with a real
   database.
5. **Integration** — swap `mockClient.ts`'s internals for real API calls
   (per its design in frontend_specs.md, no UI changes needed), and
   verify the app end-to-end against the real backend.

## Tools

- **Claude Code** — default agent for backend, persistence, and
  integration work.
- **Lovable** (MCP plugin) — builds and iterates the frontend.
- **Codex** (plugin) — code review. Run a Codex review at the end of
  every stage, before moving to the next.

## Out of scope

No backlog file, task template, or per-role agent instructions. Work is
driven directly from [specs.md](specs.md) and
[frontend_specs.md](frontend_specs.md).
