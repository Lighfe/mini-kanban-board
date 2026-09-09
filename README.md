# Mini Kanban Board

A multi-user kanban board app. Each user gets personal boards to organize
tasks, and can share individual boards with others (view or edit access)
for collaborative work.

See [_docs/specs.md](_docs/specs.md) for the full design spec.

## Frontend

[frontend/](frontend/) is a git submodule tracking a Lovable-managed
project ([board-buddy](https://github.com/Lighfe/board-buddy)) and is
built against a fully mocked backend — see
[_docs/frontend_specs.md](_docs/frontend_specs.md). Changes to it go
through Lovable, not local edits; run `git submodule update --remote
frontend` to pull the latest.

## Status

Frontend built against a mocked backend. Real backend not started yet.
