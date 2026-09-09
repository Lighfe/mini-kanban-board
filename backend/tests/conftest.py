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
