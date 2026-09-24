import os

# Must be set before kanban.main / kanban.routers.auth are imported: the
# session cookie is Secure by default, but TestClient talks to plain-HTTP
# http://testserver, and some HTTP client cookie-jars refuse to send a
# Secure cookie back to a non-HTTPS origin — which would silently break
# every session-dependent test.
os.environ.setdefault("KANBAN_SECURE_COOKIES", "false")

# Likewise must be set before kanban.db is imported: default to a fast,
# isolated in-memory SQLite DB so the test suite never touches (or
# pollutes) the dev-mode `backend/kanban.db` file, and each test run
# starts from a clean database.
os.environ.setdefault("KANBAN_DATABASE_URL", "sqlite:///:memory:")


def is_disposable_database(url: str) -> bool:
    """The suite drops every table, so only run it against in-memory SQLite
    or a database whose name says it's for tests."""
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    database = parsed.database or ""
    if parsed.get_backend_name() == "sqlite" and database in ("", ":memory:"):
        return True
    return "test" in database.lower()


if not is_disposable_database(os.environ["KANBAN_DATABASE_URL"]):
    import pytest

    pytest.exit(
        "Refusing to run: the test suite drops every table, and KANBAN_DATABASE_URL "
        "points at a database whose name doesn't contain 'test'.",
        returncode=2,
    )

import pytest
from fastapi.testclient import TestClient

from kanban.main import app
from kanban.store import store


@pytest.fixture(autouse=True)
def reset_store():
    store.reset()
    yield
    store.reset()


@pytest.fixture
def client():
    return TestClient(app)


def signup(client: TestClient, email: str = "alice@example.com", name: str = "Alice", password: str = "hunter2"):
    response = client.post("/api/auth/signup", json={"email": email, "name": name, "password": password})
    assert response.status_code == 201, response.text
    return response.json()
