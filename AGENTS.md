# Agent Instructions

This repo implements the mini kanban board described in
[_docs/specs.md](_docs/specs.md) (data model, permissions, behavior) and
[_docs/frontend_specs.md](_docs/frontend_specs.md) (frontend build and
build target), and deploys per
[_docs/deployment-plan.md](_docs/deployment-plan.md). These are living
docs, not frozen — update them when a decision changes, especially
specs.md when a change affects both frontend and backend.

See [_docs/process.md](_docs/process.md) for how work moves from spec to
shipped code.

[frontend/](frontend/) is a git submodule tracking a Lovable-managed
project — don't edit it locally; changes go through Lovable.

## Tools

- **Claude Code** — default agent for backend, persistence,
  integration, and deployment work.
- **Lovable** (MCP plugin) — builds and iterates the frontend, including
  its build configuration.
- **Codex** (plugin) — code review, run at the end of every stage.
  Uses the Codex default model (`gpt-6-astra`); don't pass an older
  model.
- **Jev** (TypeSafe) — narrow decisions with confidence; design in
  [_docs/decision-delegation.md](_docs/decision-delegation.md), not yet
  built.

## Secrets

Local secrets live outside the repo in
`~/.config/mini-kanban-board/secrets.env` (file mode 600); variables are
listed in [secrets.env.example](secrets.env.example). Don't read, print,
or copy that file. Run commands that need secrets through
`scripts/with-secrets <command>`. Never commit secrets or add them to
`.env` files in the repo.

## Backend conventions

- Use `uv` for dependency management in `backend/`: `uv sync`,
  `uv add <package-name>`, `uv run python <file>`.
- Commit regularly.
- The backend serializes requests with a process-wide lock; never run it
  with more than one uvicorn worker.
