import importlib

import pytest
from fastapi.testclient import TestClient

import kanban.main as main_module


def test_no_static_dir_leaves_non_api_paths_404(client):
    # KANBAN_STATIC_DIR is unset in tests (see conftest.py); local dev
    # without a built frontend must behave the same way.
    response = client.get("/")
    assert response.status_code == 404

    response = client.get("/some/deep-link")
    assert response.status_code == 404


@pytest.fixture
def static_client(tmp_path, monkeypatch):
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    (assets_dir / "app.js").write_text("console.log('hi');")
    (tmp_path / "index.html").write_text("<html><body>shell</body></html>")

    monkeypatch.setenv("KANBAN_STATIC_DIR", str(tmp_path))
    importlib.reload(main_module)
    try:
        yield TestClient(main_module.app)
    finally:
        monkeypatch.delenv("KANBAN_STATIC_DIR", raising=False)
        importlib.reload(main_module)


def test_assets_are_served_as_files(static_client):
    response = static_client.get("/assets/app.js")
    assert response.status_code == 200
    assert response.text == "console.log('hi');"


def test_unknown_non_api_path_falls_back_to_index(static_client):
    response = static_client.get("/boards/123")
    assert response.status_code == 200
    assert "shell" in response.text


def test_root_falls_back_to_index(static_client):
    response = static_client.get("/")
    assert response.status_code == 200
    assert "shell" in response.text


def test_unknown_api_path_still_returns_json_404(static_client):
    response = static_client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


def test_missing_asset_returns_404(static_client):
    response = static_client.get("/assets/does-not-exist.js")
    assert response.status_code == 404


def test_existing_api_route_unaffected_by_catch_all(static_client):
    response = static_client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_missing_index_html_fails_fast(tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()

    monkeypatch.setenv("KANBAN_STATIC_DIR", str(tmp_path))
    try:
        with pytest.raises(RuntimeError, match="index.html"):
            importlib.reload(main_module)
    finally:
        monkeypatch.delenv("KANBAN_STATIC_DIR", raising=False)
        importlib.reload(main_module)
