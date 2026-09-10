.PHONY: sync run test

sync:
	uv sync

run:
	KANBAN_SECURE_COOKIES=false uv run uvicorn kanban.main:app --reload --port 8000

test:
	uv run pytest
