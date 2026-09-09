import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Response
from pydantic import BaseModel

from kanban.auth import create_session, hash_password, seed_default_board, verify_password, SESSION_COOKIE
from kanban.errors import ApiError
from kanban.schemas import User
from kanban.store import store

router = APIRouter(prefix="/api/auth", tags=["Auth"])

# Secure by default (session cookie only sent back over HTTPS). Local dev
# and the test suite talk to a plain-HTTP origin (uvicorn on localhost, or
# TestClient's http://testserver), where a Secure cookie either wouldn't be
# sent over the wire in real deployment terms, or — worse for tests — some
# HTTP client cookie-jars refuse to persist/send a Secure cookie against a
# non-HTTPS test origin, silently breaking session-dependent tests. Read
# once at import time; tests/conftest.py sets this env var to "false"
# before the app is imported.
SECURE_COOKIES = os.environ.get("KANBAN_SECURE_COOKIES", "true").lower() != "false"


class SignupRequest(BaseModel):
    email: str
    name: str
    password: str


class SigninRequest(BaseModel):
    email: str
    password: str


def _set_session_cookie(response: Response, user_id: str) -> None:
    token = create_session(user_id)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", secure=SECURE_COOKIES)


@router.post("/signup", status_code=201, response_model=User)
def signup(body: SignupRequest, response: Response) -> dict:
    if any(u["email"] == body.email for u in store.users.values()):
        raise ApiError(400, "Email already registered")
    user_id = str(uuid.uuid4())
    store.users[user_id] = {
        "id": user_id,
        "email": body.email,
        "name": body.name,
        "passwordHash": hash_password(body.password),
        "createdAt": datetime.now(timezone.utc),
    }
    seed_default_board(user_id, "Personal")
    seed_default_board(user_id, "Work")
    _set_session_cookie(response, user_id)
    return store.users[user_id]


@router.post("/signin", response_model=User)
def signin(body: SigninRequest, response: Response) -> dict:
    user = next((u for u in store.users.values() if u["email"] == body.email), None)
    if not user or not verify_password(body.password, user["passwordHash"]):
        raise ApiError(401, "Invalid email or password")
    _set_session_cookie(response, user["id"])
    return user
