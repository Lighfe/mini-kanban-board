from fastapi import Response

from tests.conftest import signup

import kanban.routers.auth as auth_router


def test_signup_creates_a_session_cookie_and_returns_the_user(client):
    response = client.post(
        "/api/auth/signup",
        json={"email": "alice@example.com", "name": "Alice", "password": "hunter2"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "alice@example.com"
    assert body["name"] == "Alice"
    assert "id" in body
    assert "session" in response.cookies


def test_signup_seeds_personal_and_work_boards_with_default_columns(client):
    signup(client)
    boards_response = client.get("/api/boards")
    names = sorted(b["name"] for b in boards_response.json())
    assert names == ["Personal", "Work"]

    board_id = boards_response.json()[0]["id"]
    board = client.get(f"/api/boards/{board_id}").json()
    column_names = [c["name"] for c in board["columns"]]
    assert column_names == ["Backlog", "Today", "Doing", "Done"]
    assert board["role"] == "owner"


def test_signup_rejects_a_duplicate_email(client):
    signup(client)
    response = client.post(
        "/api/auth/signup",
        json={"email": "alice@example.com", "name": "Alice 2", "password": "x"},
    )
    assert response.status_code == 400


def test_signin_with_correct_credentials_returns_the_user(client):
    signup(client)
    client.cookies.clear()
    response = client.post("/api/auth/signin", json={"email": "alice@example.com", "password": "hunter2"})
    assert response.status_code == 200
    assert response.json()["email"] == "alice@example.com"


def test_signin_with_wrong_password_is_rejected(client):
    signup(client)
    client.cookies.clear()
    response = client.post("/api/auth/signin", json={"email": "alice@example.com", "password": "wrong"})
    assert response.status_code == 401


def test_get_me_requires_authentication(client):
    response = client.get("/api/me")
    assert response.status_code == 401


def test_get_me_returns_the_signed_in_user(client):
    user = signup(client)
    response = client.get("/api/me")
    assert response.status_code == 200
    assert response.json() == user


def test_session_cookie_is_not_secure_under_the_test_env_override(client):
    # conftest.py sets KANBAN_SECURE_COOKIES=false before the app is
    # imported, specifically so TestClient's plain-HTTP session works.
    # Confirm that override actually took effect.
    response = client.post(
        "/api/auth/signup",
        json={"email": "alice@example.com", "name": "Alice", "password": "hunter2"},
    )
    set_cookie_header = response.headers.get("set-cookie", "")
    assert "secure" not in set_cookie_header.lower()


def test_session_cookie_is_secure_by_default_when_the_env_var_is_unset_or_true():
    # Exercise the real production default directly against the cookie
    # helper: with KANBAN_SECURE_COOKIES=true (the default when unset),
    # the Set-Cookie header must carry the Secure attribute.
    response = Response()
    old_value = auth_router.SECURE_COOKIES
    try:
        auth_router.SECURE_COOKIES = True
        auth_router._set_session_cookie(response, "some-user-id")
    finally:
        auth_router.SECURE_COOKIES = old_value
    set_cookie_header = response.headers.get("set-cookie", "")
    assert "secure" in set_cookie_header.lower()
