# Repo-level shortcuts. Backend-only targets live in backend/Makefile.
.PHONY: build up down test test-pg

IMAGE ?= mini-kanban
# The `db` service in docker-compose.yml, reached via its published port.
# Tests use their own database there, since the suite drops every table.
PG_URL ?= postgresql+psycopg://kanban:kanban@localhost:5432/kanban_test

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
	docker compose exec -T db psql -U kanban -d kanban -tAc "SELECT 1 FROM pg_database WHERE datname = 'kanban_test'" | grep -q 1 \
		|| docker compose exec -T db createdb -U kanban kanban_test
	KANBAN_DATABASE_URL=$(PG_URL) $(MAKE) -C backend test
