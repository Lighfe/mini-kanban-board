from tests.conftest import signup


def test_create_and_list_share_links_requires_owner(client):
    signup(client)
    board_id = client.get("/api/boards").json()[0]["id"]
    response = client.post(f"/api/boards/{board_id}/share-links", json={"role": "editor"})
    assert response.status_code == 201
    link = response.json()
    assert link["revoked"] is False

    listed = client.get(f"/api/boards/{board_id}/share-links").json()
    assert len(listed) == 1


def test_redeem_share_link_adds_caller_as_member_at_links_role(client):
    signup(client)
    board_id = client.get("/api/boards").json()[0]["id"]
    link = client.post(f"/api/boards/{board_id}/share-links", json={"role": "viewer"}).json()

    client.cookies.clear()
    signup(client, email="bob@example.com", name="Bob", password="pw")
    response = client.post("/api/share-links/redeem", json={"token": link["token"]})
    assert response.status_code == 200
    body = response.json()
    assert body == {"boardId": board_id, "boardName": "Personal", "role": "viewer", "changed": True}

    members = client.get(f"/api/boards/{board_id}/members")
    assert response.status_code == 200


def test_redeem_share_link_never_downgrades_existing_membership(client):
    signup(client)
    board_id = client.get("/api/boards").json()[0]["id"]
    editor_link = client.post(f"/api/boards/{board_id}/share-links", json={"role": "editor"}).json()
    viewer_link = client.post(f"/api/boards/{board_id}/share-links", json={"role": "viewer"}).json()

    client.cookies.clear()
    signup(client, email="bob@example.com", name="Bob", password="pw")
    client.post("/api/share-links/redeem", json={"token": editor_link["token"]})

    response = client.post("/api/share-links/redeem", json={"token": viewer_link["token"]})
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "editor"
    assert body["changed"] is False


def test_redeem_revoked_link_returns_410(client):
    signup(client)
    board_id = client.get("/api/boards").json()[0]["id"]
    link = client.post(f"/api/boards/{board_id}/share-links", json={"role": "viewer"}).json()
    client.post(f"/api/boards/{board_id}/share-links/{link['id']}/revoke")

    client.cookies.clear()
    signup(client, email="bob@example.com", name="Bob", password="pw")
    response = client.post("/api/share-links/redeem", json={"token": link["token"]})
    assert response.status_code == 410


def test_redeem_unknown_token_returns_404(client):
    signup(client)
    response = client.post("/api/share-links/redeem", json={"token": "does-not-exist"})
    assert response.status_code == 404


def test_redeem_requires_authentication(client):
    response = client.post("/api/share-links/redeem", json={"token": "anything"})
    assert response.status_code == 401


def test_revoke_share_link(client):
    signup(client)
    board_id = client.get("/api/boards").json()[0]["id"]
    link = client.post(f"/api/boards/{board_id}/share-links", json={"role": "viewer"}).json()
    response = client.post(f"/api/boards/{board_id}/share-links/{link['id']}/revoke")
    assert response.status_code == 200
    assert response.json()["revoked"] is True
