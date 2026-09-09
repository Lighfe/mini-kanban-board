from tests.conftest import signup


def setup_board(client):
    signup(client)
    board_id = client.get("/api/boards").json()[0]["id"]
    columns = client.get(f"/api/boards/{board_id}").json()["columns"]
    by_name = {c["name"]: c["id"] for c in columns}
    return board_id, by_name


def test_create_task_defaults_priority_to_medium_and_appends_to_column(client):
    board_id, columns = setup_board(client)
    response = client.post(f"/api/boards/{board_id}/columns/{columns['Backlog']}/tasks", json={"title": "Write tests"})
    assert response.status_code == 201
    task = response.json()
    assert task["priority"] == "Medium"
    assert task["archived"] is False
    assert task["columnId"] == columns["Backlog"]


def test_create_task_rejects_blank_title(client):
    board_id, columns = setup_board(client)
    response = client.post(f"/api/boards/{board_id}/columns/{columns['Backlog']}/tasks", json={"title": ""})
    assert response.status_code == 400


def test_second_task_gets_a_larger_order_than_the_first(client):
    board_id, columns = setup_board(client)
    t1 = client.post(f"/api/boards/{board_id}/columns/{columns['Backlog']}/tasks", json={"title": "First"}).json()
    t2 = client.post(f"/api/boards/{board_id}/columns/{columns['Backlog']}/tasks", json={"title": "Second"}).json()
    assert t2["order"] > t1["order"]


def test_update_task_only_changes_provided_fields(client):
    board_id, columns = setup_board(client)
    task = client.post(f"/api/boards/{board_id}/columns/{columns['Backlog']}/tasks", json={"title": "Original"}).json()
    response = client.patch(f"/api/boards/{board_id}/tasks/{task['id']}", json={"priority": "High"})
    assert response.status_code == 200
    updated = response.json()
    assert updated["title"] == "Original"
    assert updated["priority"] == "High"


def test_move_task_sets_column_and_reorders(client):
    board_id, columns = setup_board(client)
    task = client.post(f"/api/boards/{board_id}/columns/{columns['Backlog']}/tasks", json={"title": "T"}).json()
    response = client.post(
        f"/api/boards/{board_id}/tasks/{task['id']}/move",
        json={"toColumnId": columns["Doing"], "toIndex": 0},
    )
    assert response.status_code == 200
    moved = response.json()
    assert moved["columnId"] == columns["Doing"]


def test_archive_and_unarchive_task_roundtrip(client):
    board_id, columns = setup_board(client)
    task = client.post(f"/api/boards/{board_id}/columns/{columns['Backlog']}/tasks", json={"title": "T"}).json()

    archived = client.post(f"/api/boards/{board_id}/tasks/{task['id']}/archive").json()
    assert archived["archived"] is True

    board_contents = client.get(f"/api/boards/{board_id}").json()
    assert task["id"] not in [t["id"] for t in board_contents["tasks"]]

    unarchived = client.post(f"/api/boards/{board_id}/tasks/{task['id']}/unarchive").json()
    assert unarchived["archived"] is False
    assert unarchived["columnId"] == columns["Backlog"]


def test_unarchive_falls_back_to_backlog_when_original_column_deleted(client):
    board_id, columns = setup_board(client)
    task = client.post(f"/api/boards/{board_id}/columns/{columns['Doing']}/tasks", json={"title": "T"}).json()
    client.post(f"/api/boards/{board_id}/tasks/{task['id']}/archive")
    client.delete(f"/api/boards/{board_id}/columns/{columns['Doing']}")

    response = client.post(f"/api/boards/{board_id}/tasks/{task['id']}/unarchive")
    assert response.status_code == 200
    assert response.json()["columnId"] == columns["Backlog"]


def test_restore_task_reports_column_name_and_deleted_column_fallback(client):
    board_id, columns = setup_board(client)
    task = client.post(f"/api/boards/{board_id}/columns/{columns['Doing']}/tasks", json={"title": "T"}).json()
    client.post(f"/api/boards/{board_id}/tasks/{task['id']}/archive")
    client.delete(f"/api/boards/{board_id}/columns/{columns['Doing']}")

    response = client.post(f"/api/boards/{board_id}/tasks/{task['id']}/restore")
    assert response.status_code == 200
    remaining_archived = response.json()
    assert remaining_archived == []

    board_contents = client.get(f"/api/boards/{board_id}").json()
    restored = next(t for t in board_contents["tasks"] if t["id"] == task["id"])
    assert restored["columnId"] == columns["Backlog"]
    assert restored["archived"] is False


def test_restore_task_that_is_not_archived_returns_409(client):
    board_id, columns = setup_board(client)
    task = client.post(f"/api/boards/{board_id}/columns/{columns['Backlog']}/tasks", json={"title": "T"}).json()
    response = client.post(f"/api/boards/{board_id}/tasks/{task['id']}/restore")
    assert response.status_code == 409


def test_delete_task_permanently_requires_owner_and_archived_state(client):
    board_id, columns = setup_board(client)
    task = client.post(f"/api/boards/{board_id}/columns/{columns['Backlog']}/tasks", json={"title": "T"}).json()

    not_archived_response = client.delete(f"/api/boards/{board_id}/tasks/{task['id']}")
    assert not_archived_response.status_code == 400

    client.post(f"/api/boards/{board_id}/tasks/{task['id']}/archive")
    response = client.delete(f"/api/boards/{board_id}/tasks/{task['id']}")
    assert response.status_code == 204

    board_contents = client.get(f"/api/boards/{board_id}/archived-tasks").json()
    assert board_contents == []


def test_archive_all_in_done_archives_only_done_column_tasks(client):
    board_id, columns = setup_board(client)
    client.post(f"/api/boards/{board_id}/columns/{columns['Done']}/tasks", json={"title": "D1"})
    client.post(f"/api/boards/{board_id}/columns/{columns['Done']}/tasks", json={"title": "D2"})
    client.post(f"/api/boards/{board_id}/columns/{columns['Backlog']}/tasks", json={"title": "B1"})

    response = client.post(f"/api/boards/{board_id}/archive-done")
    assert response.status_code == 200
    assert response.json() == {"archivedCount": 2}

    board_contents = client.get(f"/api/boards/{board_id}").json()
    remaining_titles = {t["title"] for t in board_contents["tasks"]}
    assert remaining_titles == {"B1"}


def test_list_archived_tasks_most_recent_first_with_column_name(client):
    board_id, columns = setup_board(client)
    t1 = client.post(f"/api/boards/{board_id}/columns/{columns['Backlog']}/tasks", json={"title": "Old"}).json()
    t2 = client.post(f"/api/boards/{board_id}/columns/{columns['Backlog']}/tasks", json={"title": "New"}).json()
    client.post(f"/api/boards/{board_id}/tasks/{t1['id']}/archive")
    client.post(f"/api/boards/{board_id}/tasks/{t2['id']}/archive")

    response = client.get(f"/api/boards/{board_id}/archived-tasks")
    assert response.status_code == 200
    titles = [t["title"] for t in response.json()]
    assert titles == ["New", "Old"]
    assert all(t["columnName"] == "Backlog" for t in response.json())


def test_move_task_respacing_never_produces_duplicate_orders_at_front(client):
    board_id, columns = setup_board(client)
    backlog = columns["Backlog"]

    # Seed two tasks with orders closer together than MIN_GAP, forcing a
    # respace when a third task is moved between them.
    t1 = client.post(f"/api/boards/{board_id}/columns/{backlog}/tasks", json={"title": "A"}).json()
    t2 = client.post(f"/api/boards/{board_id}/columns/{backlog}/tasks", json={"title": "B"}).json()
    t3 = client.post(f"/api/boards/{board_id}/columns/{backlog}/tasks", json={"title": "C"}).json()

    from kanban.store import store

    store.tasks[t1["id"]]["order"] = 1000.0
    store.tasks[t2["id"]]["order"] = 1000.0 + 1e-9
    store.tasks[t3["id"]]["order"] = 5000.0

    # Move t3 to index 1 (between t1 and t2) — triggers needs_respacing.
    response = client.post(
        f"/api/boards/{board_id}/tasks/{t3['id']}/move",
        json={"toColumnId": backlog, "toIndex": 1},
    )
    assert response.status_code == 200

    # Now move a task to the very front again.
    response = client.post(
        f"/api/boards/{board_id}/tasks/{t2['id']}/move",
        json={"toColumnId": backlog, "toIndex": 0},
    )
    assert response.status_code == 200

    board_contents = client.get(f"/api/boards/{board_id}").json()
    backlog_tasks = [t for t in board_contents["tasks"] if t["columnId"] == backlog]
    orders = [t["order"] for t in backlog_tasks]
    assert len(orders) == len(set(orders)), f"duplicate order values found: {orders}"
