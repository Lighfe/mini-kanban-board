from kanban.store import store
from tests.conftest import signup


def add_second_user_as_editor(client, board_id):
    """Create a share link, redeem it as a second user (Bob), and switch the
    client's session to Bob so subsequent requests act as a non-owner editor
    member of the board. Returns Bob's user dict."""
    link = client.post(f"/api/boards/{board_id}/share-links", json={"role": "editor"}).json()
    client.cookies.clear()
    bob = signup(client, email="bob@example.com", name="Bob", password="pw")
    client.post("/api/share-links/redeem", json={"token": link["token"]})
    return bob


def create_second_user(client):
    client.cookies.clear()
    return signup(client, email="bob@example.com", name="Bob", password="pw")


def test_list_boards_requires_auth(client):
    assert client.get("/api/boards").status_code == 401


def test_create_board_seeds_default_columns_and_becomes_owner(client):
    signup(client)
    response = client.post("/api/boards", json={"name": "Side Project"})
    assert response.status_code == 201
    board = response.json()
    assert board["name"] == "Side Project"

    contents = client.get(f"/api/boards/{board['id']}").json()
    assert contents["role"] == "owner"
    assert [c["name"] for c in contents["columns"]] == ["Backlog", "Today", "Doing", "Done"]


def test_create_board_rejects_blank_name(client):
    signup(client)
    response = client.post("/api/boards", json={"name": ""})
    assert response.status_code == 400


def test_get_board_404_for_nonexistent_board(client):
    signup(client)
    assert client.get("/api/boards/does-not-exist").status_code == 404


def test_get_board_403_for_non_member(client):
    signup(client)
    board_id = client.get("/api/boards").json()[0]["id"]
    create_second_user(client)
    assert client.get(f"/api/boards/{board_id}").status_code == 403


def test_rename_board_requires_owner_role(client):
    signup(client)
    board_id = client.get("/api/boards").json()[0]["id"]
    response = client.patch(f"/api/boards/{board_id}", json={"name": "Renamed"})
    assert response.status_code == 200
    assert response.json()["name"] == "Renamed"


def test_rename_board_non_owner_gets_403(client):
    signup(client)
    board_id = client.get("/api/boards").json()[0]["id"]
    add_second_user_as_editor(client, board_id)

    response = client.patch(f"/api/boards/{board_id}", json={"name": "Renamed"})
    assert response.status_code == 403


def test_delete_board_non_owner_gets_403(client):
    signup(client)
    board_id = client.get("/api/boards").json()[0]["id"]
    add_second_user_as_editor(client, board_id)

    response = client.delete(f"/api/boards/{board_id}")
    assert response.status_code == 403


def test_delete_board_cascades_columns_and_tasks(client):
    signup(client)
    board_id = client.get("/api/boards").json()[0]["id"]
    contents = client.get(f"/api/boards/{board_id}").json()
    column_id = contents["columns"][0]["id"]
    client.post(f"/api/boards/{board_id}/columns/{column_id}/tasks", json={"title": "A task"})

    response = client.delete(f"/api/boards/{board_id}")
    assert response.status_code == 204
    assert client.get(f"/api/boards/{board_id}").status_code == 404


def test_delete_board_also_removes_tasks_orphaned_by_prior_column_deletion(client):
    signup(client)
    board_id = client.get("/api/boards").json()[0]["id"]
    contents = client.get(f"/api/boards/{board_id}").json()
    column_id = contents["columns"][0]["id"]
    task = client.post(
        f"/api/boards/{board_id}/columns/{column_id}/tasks", json={"title": "Orphan"}
    ).json()

    # Deleting the column archives the task but leaves it alive with a
    # dangling columnId (by design), no longer reachable via any column.
    delete_column_response = client.delete(f"/api/boards/{board_id}/columns/{column_id}")
    assert delete_column_response.status_code == 200
    assert task["id"] in store.tasks

    response = client.delete(f"/api/boards/{board_id}")
    assert response.status_code == 204
    assert task["id"] not in store.tasks


def test_transfer_ownership_rejects_unknown_target_user(client):
    signup(client)
    board_id = client.get("/api/boards").json()[0]["id"]
    response = client.post(f"/api/boards/{board_id}/transfer-ownership", json={"toUserId": "nope"})
    assert response.status_code == 400


def test_transfer_ownership_requires_owner_role(client):
    signup(client)
    board_id = client.get("/api/boards").json()[0]["id"]
    bob_response = create_second_user(client)
    client.post("/api/boards/does-not-matter")  # noop, keeps client on bob's session
    response = client.post(f"/api/boards/{board_id}/transfer-ownership", json={"toUserId": bob_response["id"]})
    assert response.status_code in (403, 404)


def test_transfer_ownership_promotes_target_and_demotes_current_owner(client):
    signup(client)
    owner_id = client.get("/api/me").json()["id"]
    board_id = client.get("/api/boards").json()[0]["id"]
    link = client.post(f"/api/boards/{board_id}/share-links", json={"role": "editor"}).json()

    client.cookies.clear()
    bob = signup(client, email="bob@example.com", name="Bob", password="pw")
    client.post("/api/share-links/redeem", json={"token": link["token"]})

    client.cookies.clear()
    client.post("/api/auth/signin", json={"email": "alice@example.com", "password": "hunter2"})
    response = client.post(f"/api/boards/{board_id}/transfer-ownership", json={"toUserId": bob["id"]})
    assert response.status_code == 200
    roles = {m["userId"]: m["role"] for m in response.json()}
    assert roles[bob["id"]] == "owner"
    assert roles[owner_id] == "editor"
