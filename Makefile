# Repo-level shortcuts. Backend-only targets live in backend/Makefile.
.PHONY: build up down test test-pg

IMAGE ?= mini-kanban
# Matches the `db` service in docker-compose.yml, reached via its
# published port.
PG_URL ?= postgresql+psycopg://kanban:kanban@localhost:5432/kanban

build:
	docker build -t $(IMAGE) .

up:
	docker compose up --build

down:
	docker compose down

test:
	$(MAKE) -C backend test

# Backend tests against the compose Postgres (starts just the db service
# and waits for its health check). Catches SQLite-only assumptions.
test-pg:
	docker compose up -d --wait db
	KANBAN_DATABASE_URL=$(PG_URL) $(MAKE) -C backend test
