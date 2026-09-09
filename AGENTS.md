# Agent Instructions

This repo implements the mini kanban board described in
[_docs/specs.md](_docs/specs.md) (data model, permissions, behavior) and
[_docs/frontend_specs.md](_docs/frontend_specs.md) (frontend build against
a mocked backend). These are living docs, not frozen — update them when a
decision changes, especially specs.md when a change affects both frontend
and backend.

See [_docs/process.md](_docs/process.md) for how work moves from spec to
shipped code.

[frontend/](frontend/) is a git submodule tracking a Lovable-managed
project — don't edit it locally; changes go through Lovable.

## Tools

- **Claude Code** — default agent for backend, persistence, and
  integration work.
- **Lovable** (MCP plugin) — builds and iterates the frontend.
- **Codex** (plugin) — code review, run at the end of every stage.
