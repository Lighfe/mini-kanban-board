from kanban.ordering import MIN_GAP
from kanban.store import store
from tests.conftest import signup


def get_board_and_columns(client):
    signup(client)
    board_id = client.get("/api/boards").json()[0]["id"]
    columns = client.get(f"/api/boards/{board_id}").json()["columns"]
    return board_id, columns


def test_create_column_appends_before_done(client):
    board_id, columns = get_board_and_columns(client)
    response = client.post(f"/api/boards/{board_id}/columns", json={"name": "Review"})
    assert response.status_code == 201
    new_column = response.json()
    done_order = next(c["order"] for c in columns if c["name"] == "Done")
    assert new_column["order"] < done_order


def test_create_column_rejects_name_done_case_insensitive(client):
    board_id, _ = get_board_and_columns(client)
    response = client.post(f"/api/boards/{board_id}/columns", json={"name": "done"})
    assert response.status_code == 400


def test_rename_done_column_is_rejected(client):
    board_id, columns = get_board_and_columns(client)
    done_id = next(c["id"] for c in columns if c["name"] == "Done")
    response = client.patch(f"/api/boards/{board_id}/columns/{done_id}", json={"name": "Finished"})
    assert response.status_code == 400


def test_rename_column_succeeds_for_non_done(client):
    board_id, columns = get_board_and_columns(client)
    backlog_id = next(c["id"] for c in columns if c["name"] == "Backlog")
    response = client.patch(f"/api/boards/{board_id}/columns/{backlog_id}", json={"name": "Inbox"})
    assert response.status_code == 200
    assert response.json()["name"] == "Inbox"


def test_delete_done_column_is_rejected(client):
    board_id, columns = get_board_and_columns(client)
    done_id = next(c["id"] for c in columns if c["name"] == "Done")
    response = client.delete(f"/api/boards/{board_id}/columns/{done_id}")
    assert response.status_code == 400


def test_delete_column_with_tasks_archives_them(client):
    board_id, columns = get_board_and_columns(client)
    backlog_id = next(c["id"] for c in columns if c["name"] == "Backlog")
    client.post(f"/api/boards/{board_id}/columns/{backlog_id}/tasks", json={"title": "T1"})
    client.post(f"/api/boards/{board_id}/columns/{backlog_id}/tasks", json={"title": "T2"})

    response = client.delete(f"/api/boards/{board_id}/columns/{backlog_id}")
    assert response.status_code == 200
    assert response.json() == {"archivedCount": 2}

    archived = client.get(f"/api/boards/{board_id}/archived-tasks").json()
    assert len(archived) == 2


def test_reorder_column_moves_it_among_siblings(client):
    board_id, columns = get_board_and_columns(client)
    doing_id = next(c["id"] for c in columns if c["name"] == "Doing")
    response = client.post(f"/api/boards/{board_id}/columns/{doing_id}/reorder", json={"index": 0})
    assert response.status_code == 200
    ordered_names = [c["name"] for c in response.json()]
    assert ordered_names == ["Doing", "Backlog", "Today", "Done"]


def test_reorder_column_cannot_move_done_or_move_past_it(client):
    board_id, columns = get_board_and_columns(client)
    done_id = next(c["id"] for c in columns if c["name"] == "Done")
    backlog_id = next(c["id"] for c in columns if c["name"] == "Backlog")

    assert client.post(f"/api/boards/{board_id}/columns/{done_id}/reorder", json={"index": 0}).status_code == 400

    response = client.post(f"/api/boards/{board_id}/columns/{backlog_id}/reorder", json={"index": 3})
    ordered_names = [c["name"] for c in response.json()]
    assert ordered_names[-1] == "Done"


def test_respace_then_front_reorder_produces_no_collisions(client):
    board_id, columns = get_board_and_columns(client)
    backlog_id = next(c["id"] for c in columns if c["name"] == "Backlog")
    today_id = next(c["id"] for c in columns if c["name"] == "Today")
    doing_id = next(c["id"] for c in columns if c["name"] == "Doing")

    # Force Backlog and Today so close together that inserting Doing between
    # them requires respacing (needs_respacing must return True here).
    store.columns[backlog_id]["order"] = 1000.0
    store.columns[today_id]["order"] = 1000.0 + (MIN_GAP / 2)

    response = client.post(f"/api/boards/{board_id}/columns/{doing_id}/reorder", json={"index": 1})
    assert response.status_code == 200
    respaced = response.json()
    orders = [c["order"] for c in respaced]
    assert orders == sorted(orders)
    assert len(set(orders)) == len(orders)  # no collisions after respacing
    assert orders[0] > 0  # first column must not collapse to 0.0
    assert respaced[-1]["name"] == "Done"

    # Move the second column to the front. This exercises
    # order_between(None, first_column_order) against the freshly respaced
    # first column and must still not collide with it.
    second_column_id = respaced[1]["id"]
    response2 = client.post(f"/api/boards/{board_id}/columns/{second_column_id}/reorder", json={"index": 0})
    assert response2.status_code == 200
    final_orders = [c["order"] for c in response2.json()]
    assert final_orders == sorted(final_orders)
    assert len(set(final_orders)) == len(final_orders)  # still no collisions
    assert final_orders[0] > 0


def test_column_routes_require_editor_role_or_higher(client):
    board_id, columns = get_board_and_columns(client)
    # A bare-owner-check stand-in: with only one member (the owner) on the
    # board, exercising the viewer-rejected path needs a second, lower-role
    # member, which arrives via share links in Task 9. This test is
    # extended there; for now confirm the happy path requires *some* auth:
    client.cookies.clear()
    backlog_id = next(c["id"] for c in columns if c["name"] == "Backlog")
    response = client.post(f"/api/boards/{board_id}/columns/{backlog_id}/reorder", json={"index": 0})
    assert response.status_code == 401
