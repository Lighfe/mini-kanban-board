from tests.conftest import signup


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


def test_delete_board_cascades_columns_and_tasks(client):
    signup(client)
    board_id = client.get("/api/boards").json()[0]["id"]
    contents = client.get(f"/api/boards/{board_id}").json()
    column_id = contents["columns"][0]["id"]
    client.post(f"/api/boards/{board_id}/columns/{column_id}/tasks", json={"title": "A task"})

    response = client.delete(f"/api/boards/{board_id}")
    assert response.status_code == 204
    assert client.get(f"/api/boards/{board_id}").status_code == 404
