import pytest
from fastapi.testclient import TestClient

from kanban.main import app


@pytest.fixture
def client():
    return TestClient(app)
