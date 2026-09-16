# Two-stage build: the frontend is built into static files, then a Python
# image runs the backend with those files copied in. See
# _docs/deployment-plan.md (step 3). Build from the repo root; the
# frontend/ submodule must be checked out first (git submodule update --init).

# --- Stage 1: build the frontend -------------------------------------------
# The frontend is a Lovable-managed project locked with bun.lock (there is no
# package-lock.json), so install with Bun against that lockfile rather than
# npm ci.
FROM oven/bun:1 AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/bun.lock frontend/bunfig.toml ./
RUN bun install --frozen-lockfile
COPY frontend/ ./
# Same origin in production: the app calls the API relative to itself.
ENV VITE_API_BASE_URL=/api
RUN bun run build

# --- Stage 2: run the backend, serving the built frontend --------------------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS backend
WORKDIR /app/backend
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
# Install dependencies first so this layer is cached across source changes.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/README.md ./
COPY backend/src ./src
RUN uv sync --frozen --no-dev
COPY --from=frontend /app/frontend/dist/client /app/static

ENV PATH="/app/backend/.venv/bin:$PATH" \
    KANBAN_STATIC_DIR=/app/static
EXPOSE 8000
# Exactly one worker: the backend serializes requests with a process-wide
# lock (backend/src/kanban/main.py).
CMD ["uvicorn", "kanban.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
