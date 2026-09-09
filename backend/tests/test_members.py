import uuid

from kanban.store import store
from tests.conftest import signup


def setup_board_with_second_member(client, role="editor"):
    signup(client)
    owner_id = client.get("/api/me").json()["id"]
    board_id = client.get("/api/boards").json()[0]["id"]

    client.cookies.clear()
    bob = signup(client, email="bob@example.com", name="Bob", password="pw")
    client.cookies.clear()
    signup(client, email="alice-relogin@example.com", name="Alice2", password="pw")
    client.post("/api/auth/signin", json={"email": "alice@example.com", "password": "hunter2"})

    member_id = str(uuid.uuid4())
    store.board_members[member_id] = {"id": member_id, "boardId": board_id, "userId": bob["id"], "role": role}
    return board_id, owner_id, bob


def test_list_members_sorted_owner_first_then_name(client):
    board_id, owner_id, bob = setup_board_with_second_member(client)
    response = client.get(f"/api/boards/{board_id}/members")
    assert response.status_code == 200
    roles = [m["role"] for m in response.json()]
    assert roles[0] == "owner"


def test_update_member_role_requires_owner(client):
    board_id, owner_id, bob = setup_board_with_second_member(client)
    response = client.patch(f"/api/boards/{board_id}/members/{bob['id']}", json={"role": "viewer"})
    assert response.status_code == 200
    updated = next(m for m in response.json() if m["userId"] == bob["id"])
    assert updated["role"] == "viewer"


def test_update_and_remove_member_require_owner_role(client):
    board_id, owner_id, bob = setup_board_with_second_member(client, role="editor")

    client.cookies.clear()
    client.post("/api/auth/signin", json={"email": "bob@example.com", "password": "pw"})

    patch_response = client.patch(f"/api/boards/{board_id}/members/{owner_id}", json={"role": "viewer"})
    assert patch_response.status_code == 403

    delete_response = client.delete(f"/api/boards/{board_id}/members/{owner_id}")
    assert delete_response.status_code == 403


def test_update_member_role_cannot_change_owner_own_role(client):
    board_id, owner_id, bob = setup_board_with_second_member(client)
    response = client.patch(f"/api/boards/{board_id}/members/{owner_id}", json={"role": "viewer"})
    assert response.status_code == 400


def test_remove_member_revokes_active_share_links(client):
    board_id, owner_id, bob = setup_board_with_second_member(client)
    link = client.post(f"/api/boards/{board_id}/share-links", json={"role": "editor"}).json()

    response = client.delete(f"/api/boards/{board_id}/members/{bob['id']}")
    assert response.status_code == 200
    assert bob["id"] not in [m["userId"] for m in response.json()]

    links = client.get(f"/api/boards/{board_id}/share-links").json()
    assert all(l["revoked"] for l in links if l["id"] == link["id"])


def test_remove_member_cannot_remove_owner(client):
    board_id, owner_id, bob = setup_board_with_second_member(client)
    response = client.delete(f"/api/boards/{board_id}/members/{owner_id}")
    assert response.status_code == 400
