from fastapi import FastAPI

from kanban.telemetry import setup_telemetry


def test_setup_is_a_noop_without_an_endpoint(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    app = FastAPI()
    assert setup_telemetry(app) is False
    assert app.user_middleware == []


def test_main_app_has_no_otel_middleware_in_tests():
    from kanban.main import app

    # instrument_app wraps build_middleware_stack instead of adding to
    # user_middleware; this flag is what it sets.
    assert not getattr(app, "_is_instrumented_by_opentelemetry", False)


from tests.conftest import counter_value, signup
from tests.test_tasks import add_second_user_as_editor, setup_board


def test_creating_a_board_counts_once_without_labels(client, metric_reader):
    signup(client)
    before = counter_value(metric_reader, "kanban.boards.created", {})
    assert client.post("/api/boards", json={"name": "Work"}).status_code == 201
    assert counter_value(metric_reader, "kanban.boards.created", {}) == before + 1


def test_signup_seed_board_is_not_counted(client, metric_reader):
    before = counter_value(metric_reader, "kanban.boards.created")
    signup(client)
    assert counter_value(metric_reader, "kanban.boards.created") == before


def test_moving_a_task_counts_and_a_rejected_move_does_not(client, metric_reader):
    board_id, columns = setup_board(client)
    task = client.post(f"/api/boards/{board_id}/columns/{columns['Backlog']}/tasks",
                       json={"title": "t"}).json()
    before = counter_value(metric_reader, "kanban.cards.moved", {})
    ok = client.post(f"/api/boards/{board_id}/tasks/{task['id']}/move",
                     json={"toColumnId": columns["Doing"], "toIndex": 0})
    assert ok.status_code == 200
    missing = client.post(f"/api/boards/{board_id}/tasks/{task['id']}/move",
                          json={"toColumnId": "no-such-column", "toIndex": 0})
    assert missing.status_code == 404
    assert counter_value(metric_reader, "kanban.cards.moved", {}) == before + 1


def test_redeem_that_adds_a_member_counts_with_its_role(client, metric_reader):
    board_id, _ = setup_board(client)
    before = counter_value(metric_reader, "kanban.share_links.redeemed", {"role": "editor"})
    add_second_user_as_editor(client, board_id)  # Bob redeems an editor link
    assert counter_value(metric_reader, "kanban.share_links.redeemed", {"role": "editor"}) == before + 1


def test_redeem_without_change_is_not_counted(client, metric_reader):
    signup(client)
    board_id = client.get("/api/boards").json()[0]["id"]
    link = client.post(f"/api/boards/{board_id}/share-links", json={"role": "viewer"}).json()
    client.cookies.clear()
    signup(client, email="bob@example.com", name="Bob", password="pw")
    assert client.post("/api/share-links/redeem", json={"token": link["token"]}).json()["changed"] is True
    before = counter_value(metric_reader, "kanban.share_links.redeemed")
    again = client.post("/api/share-links/redeem", json={"token": link["token"]}).json()
    assert again["changed"] is False
    assert counter_value(metric_reader, "kanban.share_links.redeemed") == before
