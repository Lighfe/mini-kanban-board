import hashlib
import secrets
from datetime import datetime, timezone

from fastapi import Request

from kanban.errors import ApiError
from kanban.ordering import append_order
from kanban.store import store

SESSION_COOKIE = "session"
DEFAULT_COLUMN_NAMES = ["Backlog", "Today", "Doing", "Done"]


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 100_000)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    salt, _, digest_hex = hashed.partition("$")
    expected = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 100_000)
    return secrets.compare_digest(expected.hex(), digest_hex)


def create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    store.sessions[token] = user_id
    return token


def get_current_user(request: Request) -> dict:
    token = request.cookies.get(SESSION_COOKIE)
    user_id = store.sessions.get(token) if token else None
    if not user_id or user_id not in store.users:
        raise ApiError(401, "Not authenticated")
    return store.users[user_id]


def seed_default_board(user_id: str, name: str) -> str:
    import uuid

    board_id = str(uuid.uuid4())
    store.boards[board_id] = {
        "id": board_id,
        "name": name,
        "createdAt": datetime.now(timezone.utc),
    }
    member_id = str(uuid.uuid4())
    store.board_members[member_id] = {"id": member_id, "boardId": board_id, "userId": user_id, "role": "owner"}
    order = append_order([])
    for column_name in DEFAULT_COLUMN_NAMES:
        column_id = str(uuid.uuid4())
        store.columns[column_id] = {
            "id": column_id,
            "boardId": board_id,
            "name": column_name,
            "order": order,
        }
        order = append_order([c["order"] for c in store.columns_for_board(board_id)])
    return board_id
