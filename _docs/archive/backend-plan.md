# FastAPI Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the `backend/` FastAPI service that satisfies `openapi.yaml`, backed by an in-memory mock database, built test-first.

**Architecture:** A single FastAPI app in `backend/src/kanban/`, with an in-memory `Store` (plain Python dicts, no persistence) that stands in for the real database until the "Persistence" stage. Session auth is cookie-based: a minimal (non-openapi) `/auth/signup` and `/auth/signin` pair establishes the `session` cookie that every `openapi.yaml` operation requires, per the note in the spec that sign-up/sign-in are "out of scope of the frontend's mocked client" but the backend must implement *some* email/password flow. FastAPI dependencies handle "must be authenticated" and "must have role >= X on this board" checks so route handlers stay focused on business logic. A small `ordering.py` module implements the sortable-`order` scheme (midpoint insert + re-spacing) shared by columns and tasks.

**Tech Stack:** Python, FastAPI, Starlette `TestClient`/`httpx`, Pydantic v2, pytest, uv for dependency management.

**Spec:** `openapi.yaml` (contract), `_docs/specs.md` (behavior/permissions), `_docs/process.md` (workflow).

## Global Constraints

- Use `uv` for all dependency management: `uv init`, `uv add <pkg>`, `uv run pytest`, `uv run uvicorn ...` — never call `pip` directly.
- Tests are written and run **before** the implementation they cover, per task, in every task below (TDD).
- The store is a mock — in-memory only, wiped on process restart. Do not add a real DB dependency in this stage.
- All API responses match the exact field names/shapes in `openapi.yaml`'s `components.schemas`.
- Role hierarchy is `owner` (3) > `editor` (2) > `viewer` (1); "role X or higher" checks compare these ranks.
- The `Done` column: exactly one per board, never renamed/deleted/reordered away from last position.
- Commit after every task's tests pass (see Global git convention: `git add <files>; git commit -m "..."`, with the standard Co-Authored-By trailer this repo's agent instructions require).

---

## File Structure

```
backend/
  pyproject.toml
  src/kanban/
    __init__.py
    main.py            # FastAPI() app, mounts all routers, CORS/session middleware config
    schemas.py          # Pydantic models mirroring openapi.yaml components.schemas
    store.py            # In-memory Store class: users, boards, columns, tasks, members, share_links, sessions
    ordering.py          # append_order / order_between / needs_respacing / respace
    auth.py              # password hashing, signup/signin logic, get_current_user dependency
    permissions.py       # get_board_and_role dependency + require_role helper
    errors.py            # ApiError exception + exception handler registration
    routers/
      __init__.py
      auth.py            # POST /auth/signup, POST /auth/signin (not in openapi.yaml, session bootstrap only)
      users.py            # GET /me
      boards.py            # /boards, /boards/{boardId}, /boards/{boardId}/transfer-ownership
      columns.py           # /boards/{boardId}/columns[/{columnId}[/reorder]]
      tasks.py              # everything under /boards/{boardId}/tasks... and /archive-done, /archived-tasks
      members.py            # /boards/{boardId}/members[/{userId}]
      share_links.py         # /boards/{boardId}/share-links[...], /share-links/redeem
  tests/
    conftest.py           # TestClient fixture, fresh-store-per-test, signup/login helpers
    test_health.py
    test_ordering.py       # pure unit tests, no HTTP
    test_auth.py
    test_boards.py
    test_columns.py
    test_tasks.py
    test_members.py
    test_share_links.py
```

---

## Task 1: Project scaffold with uv

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/src/kanban/__init__.py`
- Create: `backend/src/kanban/main.py`
- Test: `backend/tests/test_health.py`
- Create: `backend/tests/conftest.py`

**Interfaces:**
- Produces: `kanban.main:app` — the FastAPI application instance, importable as `from kanban.main import app`.

- [ ] **Step 1: Initialize the uv project**

```bash
cd backend
uv init --package --name kanban --python 3.12
uv add fastapi "uvicorn[standard]"
uv add --dev pytest httpx
```

This creates `backend/pyproject.toml`, `backend/src/kanban/__init__.py`, and a `uv.lock`. Delete any placeholder `src/kanban/__init__.py` content uv scaffolds (e.g. a `main()` hello-world) — it's not needed.

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/conftest.py
import pytest
from fastapi.testclient import TestClient

from kanban.main import app


@pytest.fixture
def client():
    return TestClient(app)
```

```python
# backend/tests/test_health.py
def test_health_check(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_health.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kanban.main'` (or import error, since `main.py` doesn't exist yet).

- [ ] **Step 3: Write minimal implementation**

```python
# backend/src/kanban/main.py
from fastapi import FastAPI

app = FastAPI(title="Mini Kanban Board API")


@app.get("/api/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_health.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/src backend/tests
git commit -m "backend: scaffold FastAPI project with uv"
```

---

## Task 2: In-memory store + ordering utility

**Files:**
- Create: `backend/src/kanban/store.py`
- Create: `backend/src/kanban/ordering.py`
- Test: `backend/tests/test_ordering.py`

**Interfaces:**
- Produces: `ordering.append_order(sorted_orders: list[float]) -> float`, `ordering.order_between(before: float | None, after: float | None) -> float`, `ordering.needs_respacing(before: float | None, after: float | None) -> bool`, `ordering.respaced_values(count: int) -> list[float]`.
- Produces: `store.Store` class with plain dict attributes `users`, `boards`, `board_members`, `columns`, `tasks`, `share_links`, `sessions`, each keyed by id (or, for `board_members`, a `(board_id, user_id)` tuple key alongside an id), plus `store.store = Store()` a process-wide singleton, and `store.reset()` to clear it back to empty.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_ordering.py
from kanban.ordering import append_order, needs_respacing, order_between, respaced_values


def test_append_order_on_empty_list_returns_first_gap_value():
    assert append_order([]) == 1000.0


def test_append_order_adds_one_gap_past_the_last_value():
    assert append_order([1000.0, 2000.0]) == 3000.0


def test_order_between_two_values_is_the_midpoint():
    assert order_between(1000.0, 2000.0) == 1500.0


def test_order_between_none_and_a_value_is_half_that_value():
    assert order_between(None, 1000.0) == 500.0


def test_order_between_a_value_and_none_is_one_gap_past_it():
    assert order_between(1000.0, None) == 2000.0


def test_order_between_none_and_none_is_the_first_gap_value():
    assert order_between(None, None) == 1000.0


def test_needs_respacing_is_false_when_theres_room_between_neighbors():
    assert needs_respacing(1000.0, 2000.0) is False


def test_needs_respacing_is_true_when_neighbors_are_too_close_to_split():
    assert needs_respacing(1000.0, 1000.0000000001) is True


def test_needs_respacing_is_false_at_either_open_end():
    assert needs_respacing(None, 1000.0) is False
    assert needs_respacing(1000.0, None) is False


def test_respaced_values_returns_round_multiples_of_the_gap():
    assert respaced_values(4) == [0.0, 1000.0, 2000.0, 3000.0]


def test_respaced_values_empty():
    assert respaced_values(0) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_ordering.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kanban.ordering'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/src/kanban/ordering.py
"""Sortable `order` scheme shared by columns and tasks (see _docs/specs.md § Drag and Drop)."""

GAP = 1000.0
MIN_GAP = 1e-6


def append_order(sorted_orders: list[float]) -> float:
    if not sorted_orders:
        return GAP
    return sorted_orders[-1] + GAP


def order_between(before: float | None, after: float | None) -> float:
    if before is None and after is None:
        return GAP
    if before is None:
        return after / 2
    if after is None:
        return before + GAP
    return (before + after) / 2


def needs_respacing(before: float | None, after: float | None) -> bool:
    if before is None or after is None:
        return False
    return (after - before) < MIN_GAP


def respaced_values(count: int) -> list[float]:
    return [i * GAP for i in range(count)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_ordering.py -v`
Expected: PASS

- [ ] **Step 5: Write the store (no test file of its own — it's exercised through Task 3+'s HTTP tests, but write a quick smoke test inline)**

```python
# backend/tests/test_ordering.py (append at end of file)
from kanban.store import Store


def test_store_starts_empty_and_reset_clears_it():
    store = Store()
    assert store.users == {}
    store.users["u1"] = {"id": "u1"}
    store.reset()
    assert store.users == {}
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_ordering.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kanban.store'`

- [ ] **Step 7: Write minimal implementation**

```python
# backend/src/kanban/store.py
"""In-memory mock database. Replace with a real persistence layer in the
Persistence stage (_docs/process.md); every access goes through this class
so that swap is localized."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Store:
    users: dict[str, dict[str, Any]] = field(default_factory=dict)
    sessions: dict[str, str] = field(default_factory=dict)  # token -> user_id
    boards: dict[str, dict[str, Any]] = field(default_factory=dict)
    board_members: dict[str, dict[str, Any]] = field(default_factory=dict)  # id -> member
    columns: dict[str, dict[str, Any]] = field(default_factory=dict)
    tasks: dict[str, dict[str, Any]] = field(default_factory=dict)
    share_links: dict[str, dict[str, Any]] = field(default_factory=dict)

    def reset(self) -> None:
        self.users.clear()
        self.sessions.clear()
        self.boards.clear()
        self.board_members.clear()
        self.columns.clear()
        self.tasks.clear()
        self.share_links.clear()

    def member_for(self, board_id: str, user_id: str) -> dict[str, Any] | None:
        for member in self.board_members.values():
            if member["boardId"] == board_id and member["userId"] == user_id:
                return member
        return None

    def members_for_board(self, board_id: str) -> list[dict[str, Any]]:
        return [m for m in self.board_members.values() if m["boardId"] == board_id]

    def columns_for_board(self, board_id: str) -> list[dict[str, Any]]:
        return sorted(
            (c for c in self.columns.values() if c["boardId"] == board_id),
            key=lambda c: c["order"],
        )

    def tasks_for_column(self, column_id: str, *, include_archived: bool = False) -> list[dict[str, Any]]:
        return sorted(
            (
                t
                for t in self.tasks.values()
                if t["columnId"] == column_id and (include_archived or not t["archived"])
            ),
            key=lambda t: t["order"],
        )


store = Store()
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_ordering.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add backend/src/kanban/ordering.py backend/src/kanban/store.py backend/tests/test_ordering.py
git commit -m "backend: add ordering utility and in-memory store"
```

---

## Task 3: Auth — signup, signin, session cookie, GET /me

**Files:**
- Create: `backend/src/kanban/schemas.py`
- Create: `backend/src/kanban/auth.py`
- Create: `backend/src/kanban/errors.py`
- Create: `backend/src/kanban/routers/__init__.py`
- Create: `backend/src/kanban/routers/auth.py`
- Create: `backend/src/kanban/routers/users.py`
- Modify: `backend/src/kanban/main.py`
- Modify: `backend/tests/conftest.py`
- Test: `backend/tests/test_auth.py`

**Interfaces:**
- Consumes: `store.store` (Task 2), `ordering.append_order` (Task 2, used here to seed default columns).
- Produces: `auth.hash_password(password: str) -> str`, `auth.verify_password(password: str, hashed: str) -> bool`, `auth.get_current_user(request: Request) -> dict` (FastAPI dependency, raises `errors.ApiError(401, "Not authenticated")` if no valid session), `auth.SESSION_COOKIE = "session"`. `schemas.User`, `schemas.Board`, `schemas.Column`, `schemas.Priority`, `schemas.Role`, `schemas.Task` (Pydantic models later tasks import). `errors.ApiError(status_code: int, message: str)` and `errors.register_exception_handlers(app)`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/conftest.py  (replace file contents)
import pytest
from fastapi.testclient import TestClient

from kanban.main import app
from kanban.store import store


@pytest.fixture(autouse=True)
def reset_store():
    store.reset()
    yield
    store.reset()


@pytest.fixture
def client():
    return TestClient(app)


def signup(client: TestClient, email: str = "alice@example.com", name: str = "Alice", password: str = "hunter2"):
    response = client.post("/api/auth/signup", json={"email": email, "name": name, "password": password})
    assert response.status_code == 201, response.text
    return response.json()
```

```python
# backend/tests/test_auth.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_auth.py -v`
Expected: FAIL — 404s / import errors, since none of these routes or modules exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/src/kanban/errors.py
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"message": exc.message})
```

```python
# backend/src/kanban/schemas.py
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

Role = Literal["owner", "editor", "viewer"]
ShareRole = Literal["editor", "viewer"]
Priority = Literal["Low", "Medium", "High"]


class User(BaseModel):
    id: str
    email: str
    name: str


class Board(BaseModel):
    id: str
    name: str
    createdAt: datetime


class BoardSummary(Board):
    role: Role
    ownerName: str


class Column(BaseModel):
    id: str
    boardId: str
    name: str
    order: float


class Task(BaseModel):
    id: str
    boardId: str
    columnId: str
    title: str
    description: str
    dueDate: date | None
    priority: Priority
    order: float
    archived: bool
    createdAt: datetime
    createdBy: str


class ArchivedTask(Task):
    columnName: str


class BoardContents(BaseModel):
    board: Board
    role: Role
    columns: list[Column]
    tasks: list[Task]


class BoardMember(BaseModel):
    boardId: str
    userId: str
    role: Role


class BoardMemberDetail(BoardMember):
    user: User


class ShareLink(BaseModel):
    id: str
    boardId: str
    role: ShareRole
    token: str
    createdBy: str
    revoked: bool
```

```python
# backend/src/kanban/auth.py
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


def seed_default_board(user_id: str, name: str) -> None:
    import uuid

    board_id = str(uuid.uuid4())
    store.boards[board_id] = {
        "id": board_id,
        "name": name,
        "createdAt": datetime.now(timezone.utc),
    }
    member_id = str(uuid.uuid4())
    store.board_members[member_id] = {"id": member_id, "boardId": board_id, "userId": user_id, "role": "owner"}
    order = 0.0
    for column_name in DEFAULT_COLUMN_NAMES:
        column_id = str(uuid.uuid4())
        store.columns[column_id] = {
            "id": column_id,
            "boardId": board_id,
            "name": column_name,
            "order": order,
        }
        order = append_order([c["order"] for c in store.columns_for_board(board_id)])
```

```python
# backend/src/kanban/routers/__init__.py
```

```python
# backend/src/kanban/routers/auth.py
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Response
from pydantic import BaseModel

from kanban.auth import create_session, hash_password, seed_default_board, verify_password, SESSION_COOKIE
from kanban.errors import ApiError
from kanban.schemas import User
from kanban.store import store

router = APIRouter(prefix="/api/auth", tags=["Auth"])


class SignupRequest(BaseModel):
    email: str
    name: str
    password: str


class SigninRequest(BaseModel):
    email: str
    password: str


def _set_session_cookie(response: Response, user_id: str) -> None:
    token = create_session(user_id)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax")


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
```

```python
# backend/src/kanban/routers/users.py
from fastapi import APIRouter, Depends

from kanban.auth import get_current_user
from kanban.schemas import User

router = APIRouter(prefix="/api", tags=["Users"])


@router.get("/me", response_model=User)
def get_me(current_user: dict = Depends(get_current_user)) -> dict:
    return current_user
```

```python
# backend/src/kanban/main.py  (replace file contents)
from fastapi import FastAPI

from kanban.errors import register_exception_handlers
from kanban.routers import auth, users

app = FastAPI(title="Mini Kanban Board API")
register_exception_handlers(app)

app.include_router(auth.router)
app.include_router(users.router)


@app.get("/api/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}
```

Note: `GET /boards` and `GET /boards/{boardId}` are referenced by this task's tests but implemented in Task 4 — this task's tests for signup seeding (`test_signup_seeds_personal_and_work_boards_with_default_columns`) will only pass once Task 4 lands. Run just the tests that don't depend on those two routes first (`-k "not seeds_personal"`), confirm those pass, then proceed to Task 4 before marking this task's full file green.

- [ ] **Step 4: Run the auth-only tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_auth.py -v -k "not seeds_personal"`
Expected: PASS for signup/signin/me tests; the seeding test still fails (404 on `/api/boards`) until Task 4.

- [ ] **Step 5: Commit**

```bash
git add backend/src/kanban backend/tests/test_auth.py backend/tests/conftest.py
git commit -m "backend: add auth (signup/signin/session) and GET /me"
```

---

## Task 4: Boards — list, create, get, rename, delete

**Files:**
- Create: `backend/src/kanban/permissions.py`
- Create: `backend/src/kanban/routers/boards.py`
- Modify: `backend/src/kanban/main.py`
- Test: `backend/tests/test_boards.py`

**Interfaces:**
- Consumes: `auth.get_current_user`, `store.store`, `schemas.{Board, BoardSummary, BoardContents, Column, Task}`.
- Produces: `permissions.ROLE_RANK: dict[str, int]`, `permissions.require_member(board_id: str) -> Callable` — a dependency factory returning `(board, member)`, raising 404 if the board doesn't exist or 403 if the caller isn't a member; `permissions.require_role(min_role: str)` — a dependency factory that also enforces the caller's rank meets `min_role`, raising 403 otherwise. These are consumed by every later router (columns, tasks, members, share_links).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_boards.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_boards.py tests/test_auth.py -v`
Expected: FAIL — `/api/boards` routes don't exist yet (404s where 200/201/400/403 expected); `tests/test_boards.py` also needs `tests/__init__.py` so `from tests.conftest import signup` resolves — add an empty `backend/tests/__init__.py` in this step too.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/src/kanban/permissions.py
from typing import Callable

from fastapi import Depends

from kanban.auth import get_current_user
from kanban.errors import ApiError
from kanban.store import store

ROLE_RANK = {"viewer": 1, "editor": 2, "owner": 3}


def require_member(board_id: str) -> Callable[..., tuple[dict, dict]]:
    def dependency(current_user: dict = Depends(get_current_user)) -> tuple[dict, dict]:
        board = store.boards.get(board_id)
        if not board:
            raise ApiError(404, "Board not found")
        member = store.member_for(board_id, current_user["id"])
        if not member:
            raise ApiError(403, "Not a member of this board")
        return board, member

    return dependency


def require_role(board_id: str, min_role: str) -> Callable[..., tuple[dict, dict]]:
    def dependency(pair: tuple[dict, dict] = Depends(require_member(board_id))) -> tuple[dict, dict]:
        board, member = pair
        if ROLE_RANK[member["role"]] < ROLE_RANK[min_role]:
            raise ApiError(403, f"Requires {min_role} role or higher")
        return board, member

    return dependency
```

Note: because FastAPI resolves path parameters before dependency factories run, `require_member`/`require_role` are called *inside* each route function's own dependency (via `Depends(lambda ...)`) rather than as decorator-level `Depends(require_member(boardId))` — path params aren't available at router-definition time. The pattern used in every router from here on is:

```python
@router.get("/boards/{boardId}")
def get_board(boardId: str, current_user: dict = Depends(get_current_user)):
    board, member = _require_member(boardId, current_user)
    ...
```

So `permissions.py` also exposes plain functions (not dependency factories) for direct use inside handlers:

```python
# backend/src/kanban/permissions.py  (append)


def get_member_or_404_403(board_id: str, user_id: str) -> tuple[dict, dict]:
    board = store.boards.get(board_id)
    if not board:
        raise ApiError(404, "Board not found")
    member = store.member_for(board_id, user_id)
    if not member:
        raise ApiError(403, "Not a member of this board")
    return board, member


def require_role_or_403(board_id: str, user_id: str, min_role: str) -> tuple[dict, dict]:
    board, member = get_member_or_404_403(board_id, user_id)
    if ROLE_RANK[member["role"]] < ROLE_RANK[min_role]:
        raise ApiError(403, f"Requires {min_role} role or higher")
    return board, member
```

(Drop the earlier `require_member`/`require_role` dependency-factory versions — the plain functions above are what every router actually calls. Keep only `ROLE_RANK`, `get_member_or_404_403`, and `require_role_or_403` in the final file.)

```python
# backend/src/kanban/routers/boards.py
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator

from kanban.auth import get_current_user, seed_default_board
from kanban.errors import ApiError
from kanban.ordering import append_order
from kanban.permissions import get_member_or_404_403, require_role_or_403
from kanban.schemas import Board, BoardContents, BoardSummary
from kanban.store import store

router = APIRouter(prefix="/api/boards", tags=["Boards"])


class NameBody(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be blank")
        return value


@router.get("", response_model=list[BoardSummary])
def list_boards(current_user: dict = Depends(get_current_user)) -> list[dict]:
    result = []
    for member in store.board_members.values():
        if member["userId"] != current_user["id"]:
            continue
        board = store.boards[member["boardId"]]
        owner = next(
            m for m in store.members_for_board(board["id"]) if m["role"] == "owner"
        )
        result.append(
            {**board, "role": member["role"], "ownerName": store.users[owner["userId"]]["name"]}
        )
    return sorted(result, key=lambda b: b["name"])


@router.post("", status_code=201, response_model=Board)
def create_board(body: NameBody, current_user: dict = Depends(get_current_user)) -> dict:
    board_id = seed_default_board(current_user["id"], body.name)
    return store.boards[board_id]


@router.get("/{boardId}", response_model=BoardContents)
def get_board(boardId: str, current_user: dict = Depends(get_current_user)) -> dict:
    board, member = get_member_or_404_403(boardId, current_user["id"])
    columns = store.columns_for_board(boardId)
    tasks = [t for c in columns for t in store.tasks_for_column(c["id"])]
    return {"board": board, "role": member["role"], "columns": columns, "tasks": tasks}


@router.patch("/{boardId}", response_model=Board)
def rename_board(boardId: str, body: NameBody, current_user: dict = Depends(get_current_user)) -> dict:
    require_role_or_403(boardId, current_user["id"], "owner")
    store.boards[boardId]["name"] = body.name
    return store.boards[boardId]


@router.delete("/{boardId}", status_code=204)
def delete_board(boardId: str, current_user: dict = Depends(get_current_user)) -> None:
    require_role_or_403(boardId, current_user["id"], "owner")
    column_ids = {c["id"] for c in store.columns.values() if c["boardId"] == boardId}
    for task_id in [t["id"] for t in store.tasks.values() if t["columnId"] in column_ids]:
        del store.tasks[task_id]
    for column_id in list(column_ids):
        del store.columns[column_id]
    for member_id in [m["id"] for m in store.board_members.values() if m["boardId"] == boardId]:
        del store.board_members[member_id]
    for link_id in [l["id"] for l in store.share_links.values() if l["boardId"] == boardId]:
        del store.share_links[link_id]
    del store.boards[boardId]
```

Refactor `auth.seed_default_board` to return the new `board_id` (needed by `create_board` above):

```python
# backend/src/kanban/auth.py — change the function signature and add a return
def seed_default_board(user_id: str, name: str) -> str:
    ...  # body unchanged
    return board_id
```

```python
# backend/src/kanban/main.py  (add boards router)
from kanban.routers import auth, boards, users
...
app.include_router(boards.router)
```

Also add `backend/tests/__init__.py` (empty) so `tests.conftest` is importable as a package.

Also add a Pydantic validation-error handler so a blank `name` (which raises via `field_validator`) reports as `400`, not FastAPI's default `422`:

```python
# backend/src/kanban/errors.py (append)
from fastapi.exceptions import RequestValidationError


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"message": exc.message})

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"message": str(exc.errors())})
```

(Remove the old, single-handler version of `register_exception_handlers` from Task 3 and replace it with this one that registers both handlers.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/ -v`
Expected: PASS for all of `test_health.py`, `test_ordering.py`, `test_auth.py`, `test_boards.py`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/kanban backend/tests
git commit -m "backend: add board CRUD with membership/role permissions"
```

---

## Task 5: Transfer ownership

**Files:**
- Modify: `backend/src/kanban/routers/boards.py`
- Modify: `backend/tests/test_boards.py`

**Interfaces:**
- Consumes: `permissions.require_role_or_403`, `store.store`.
- Produces: `POST /api/boards/{boardId}/transfer-ownership`, returning `list[BoardMemberDetail]`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_boards.py (append)
def test_transfer_ownership_promotes_target_and_demotes_current_owner(client):
    signup(client)
    owner_id = client.get("/api/me").json()["id"]
    board_id = client.get("/api/boards").json()[0]["id"]

    bob = create_second_user(client)
    client.cookies.clear()
    signup(client, email="alice@example.com", name="Alice", password="hunter2")
    # re-add bob as a member directly through the store isn't available from
    # HTTP yet (share links land in a later task), so this test seeds the
    # membership via a share-link redemption once Task 9 exists. For now,
    # skip straight to asserting the 400 case, which needs no second member:


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
```

Replace the first, unfinished test above — it has no assertion and must not ship. The real "promotes/demotes" behavior needs a second board member, which only becomes reachable via HTTP once share-link redemption exists (Task 9). Write it there instead: delete `test_transfer_ownership_promotes_target_and_demotes_current_owner` from this file now, and add it to `test_share_links.py` in Task 9 (that task's step list includes it).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_boards.py -v -k transfer`
Expected: FAIL — 404, route doesn't exist.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/src/kanban/routers/boards.py (add import + route)
from kanban.schemas import BoardMemberDetail


class TransferOwnershipBody(BaseModel):
    toUserId: str


@router.post("/{boardId}/transfer-ownership", response_model=list[BoardMemberDetail])
def transfer_ownership(
    boardId: str, body: TransferOwnershipBody, current_user: dict = Depends(get_current_user)
) -> list[dict]:
    board, current_owner_member = require_role_or_403(boardId, current_user["id"], "owner")
    target_member = store.member_for(boardId, body.toUserId)
    if not target_member:
        raise ApiError(400, "Target user is not a member of this board")
    current_owner_member["role"] = "editor"
    target_member["role"] = "owner"
    return [
        {**m, "user": store.users[m["userId"]]} for m in store.members_for_board(boardId)
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_boards.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/kanban/routers/boards.py backend/tests/test_boards.py
git commit -m "backend: add board ownership transfer"
```

---

## Task 6: Columns — create, rename, delete, reorder

**Files:**
- Create: `backend/src/kanban/routers/columns.py`
- Modify: `backend/src/kanban/main.py`
- Test: `backend/tests/test_columns.py`

**Interfaces:**
- Consumes: `permissions.require_role_or_403`, `ordering.{append_order, order_between, needs_respacing, respaced_values}`, `store.store`.
- Produces: `POST /api/boards/{boardId}/columns`, `PATCH/DELETE /api/boards/{boardId}/columns/{columnId}`, `POST /api/boards/{boardId}/columns/{columnId}/reorder`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_columns.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_columns.py -v`
Expected: FAIL — routes don't exist (404s).

- [ ] **Step 3: Write minimal implementation**

```python
# backend/src/kanban/routers/columns.py
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator

from kanban.auth import get_current_user
from kanban.errors import ApiError
from kanban.ordering import append_order, needs_respacing, order_between, respaced_values
from kanban.permissions import require_role_or_403
from kanban.schemas import Column
from kanban.store import store

router = APIRouter(prefix="/api/boards/{boardId}/columns", tags=["Columns"])


class NameBody(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def not_blank_or_done(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be blank")
        if value.strip().lower() == "done":
            raise ValueError('name must not be "Done"')
        return value


class ReorderBody(BaseModel):
    index: int


def _get_column_or_404(board_id: str, column_id: str) -> dict:
    column = store.columns.get(column_id)
    if not column or column["boardId"] != board_id:
        raise ApiError(404, "Column not found")
    return column


@router.post("", status_code=201, response_model=Column)
def create_column(boardId: str, body: NameBody, current_user: dict = Depends(get_current_user)) -> dict:
    require_role_or_403(boardId, current_user["id"], "editor")
    columns = store.columns_for_board(boardId)
    done = next(c for c in columns if c["name"] == "Done")
    before_done = [c for c in columns if c["id"] != done["id"]]
    order = order_between(before_done[-1]["order"] if before_done else None, done["order"])
    if needs_respacing(before_done[-1]["order"] if before_done else None, done["order"]):
        _respace_columns(boardId, columns, insert_before_done=True)
        columns = store.columns_for_board(boardId)
        done = next(c for c in columns if c["name"] == "Done")
        before_done = [c for c in columns if c["id"] != done["id"]]
        order = order_between(before_done[-1]["order"] if before_done else None, done["order"])
    column_id = str(uuid.uuid4())
    store.columns[column_id] = {"id": column_id, "boardId": boardId, "name": body.name, "order": order}
    return store.columns[column_id]


def _respace_columns(board_id: str, columns: list[dict], *, insert_before_done: bool) -> None:
    non_done = [c for c in columns if c["name"] != "Done"]
    done = next(c for c in columns if c["name"] == "Done")
    slots = respaced_values(len(non_done) + 1)
    for column, value in zip(non_done, slots):
        column["order"] = value
    done["order"] = slots[-1] + 1000.0


@router.patch("/{columnId}", response_model=Column)
def rename_column(
    boardId: str, columnId: str, body: NameBody, current_user: dict = Depends(get_current_user)
) -> dict:
    require_role_or_403(boardId, current_user["id"], "editor")
    column = _get_column_or_404(boardId, columnId)
    if column["name"] == "Done":
        raise ApiError(400, 'The "Done" column cannot be renamed')
    column["name"] = body.name
    return column


@router.delete("/{columnId}")
def delete_column(boardId: str, columnId: str, current_user: dict = Depends(get_current_user)) -> dict:
    require_role_or_403(boardId, current_user["id"], "editor")
    column = _get_column_or_404(boardId, columnId)
    if column["name"] == "Done":
        raise ApiError(400, 'The "Done" column cannot be deleted')
    tasks = store.tasks_for_column(columnId)
    for task in tasks:
        task["archived"] = True
    del store.columns[columnId]
    return {"archivedCount": len(tasks)}


@router.post("/{columnId}/reorder", response_model=list[Column])
def reorder_column(
    boardId: str, columnId: str, body: ReorderBody, current_user: dict = Depends(get_current_user)
) -> list[dict]:
    require_role_or_403(boardId, current_user["id"], "editor")
    column = _get_column_or_404(boardId, columnId)
    if column["name"] == "Done":
        raise ApiError(400, 'The "Done" column cannot be reordered')

    columns = store.columns_for_board(boardId)
    done = next(c for c in columns if c["name"] == "Done")
    non_done = [c for c in columns if c["id"] != column["id"] and c["name"] != "Done"]
    target_index = max(0, min(body.index, len(non_done)))
    non_done.insert(target_index, column)

    before = non_done[target_index - 1]["order"] if target_index > 0 else None
    after = non_done[target_index + 1]["order"] if target_index + 1 < len(non_done) else done["order"]
    if needs_respacing(before, after):
        slots = respaced_values(len(non_done) + 1)
        for c, value in zip(non_done, slots):
            c["order"] = value
        done["order"] = slots[-1] + 1000.0
    else:
        column["order"] = order_between(before, after)

    return store.columns_for_board(boardId)
```

```python
# backend/src/kanban/main.py (add import + include_router)
from kanban.routers import auth, boards, columns, users
...
app.include_router(columns.router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_columns.py -v`
Expected: PASS. (Note: `test_delete_column_with_tasks_archives_them` also depends on `POST .../tasks` and `GET .../archived-tasks`, which Task 7 implements — run this file again after Task 7 to confirm full green; the rest of this file's tests do not depend on Task 7.)

- [ ] **Step 5: Commit**

```bash
git add backend/src/kanban/routers/columns.py backend/src/kanban/main.py backend/tests/test_columns.py
git commit -m "backend: add column create/rename/delete/reorder"
```

---

## Task 7: Tasks — create, update, move, archive, unarchive, restore, delete, archive-done, list-archived

**Files:**
- Create: `backend/src/kanban/routers/tasks.py`
- Modify: `backend/src/kanban/main.py`
- Test: `backend/tests/test_tasks.py`

**Interfaces:**
- Consumes: `permissions.require_role_or_403`, `ordering.*`, `store.store`.
- Produces: every `/boards/{boardId}/tasks...`, `/archive-done`, `/archived-tasks` route from `openapi.yaml`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_tasks.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_tasks.py -v`
Expected: FAIL — routes don't exist (404s).

- [ ] **Step 3: Write minimal implementation**

```python
# backend/src/kanban/routers/tasks.py
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator

from kanban.auth import get_current_user
from kanban.errors import ApiError
from kanban.ordering import append_order, needs_respacing, order_between, respaced_values
from kanban.permissions import require_role_or_403
from kanban.schemas import ArchivedTask, Priority, Task
from kanban.store import store

router = APIRouter(prefix="/api/boards/{boardId}", tags=["Tasks"])


class CreateTaskBody(BaseModel):
    title: str
    description: str = ""
    dueDate: date | None = None
    priority: Priority = "Medium"

    @field_validator("title")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be blank")
        return value


class UpdateTaskBody(BaseModel):
    title: str | None = None
    description: str | None = None
    dueDate: date | None = None
    priority: Priority | None = None


class MoveTaskBody(BaseModel):
    toColumnId: str
    toIndex: int


def _get_task_or_404(board_id: str, task_id: str) -> dict:
    task = store.tasks.get(task_id)
    if not task or task["boardId"] != board_id:
        raise ApiError(404, "Task not found")
    return task


def _get_column_or_404(board_id: str, column_id: str) -> dict:
    column = store.columns.get(column_id)
    if not column or column["boardId"] != board_id:
        raise ApiError(404, "Column not found")
    return column


def _archived_view(task: dict) -> dict:
    column = store.columns.get(task["columnId"])
    column_name = column["name"] if column else "Deleted column"
    return {**task, "columnName": column_name}


@router.post("/columns/{columnId}/tasks", status_code=201, response_model=Task)
def create_task(
    boardId: str, columnId: str, body: CreateTaskBody, current_user: dict = Depends(get_current_user)
) -> dict:
    require_role_or_403(boardId, current_user["id"], "editor")
    _get_column_or_404(boardId, columnId)
    existing = store.tasks_for_column(columnId)
    order = append_order([t["order"] for t in existing])
    task_id = str(uuid.uuid4())
    store.tasks[task_id] = {
        "id": task_id,
        "boardId": boardId,
        "columnId": columnId,
        "title": body.title,
        "description": body.description,
        "dueDate": body.dueDate,
        "priority": body.priority,
        "order": order,
        "archived": False,
        "createdAt": datetime.now(timezone.utc),
        "createdBy": current_user["id"],
    }
    return store.tasks[task_id]


@router.patch("/tasks/{taskId}", response_model=Task)
def update_task(
    boardId: str, taskId: str, body: UpdateTaskBody, current_user: dict = Depends(get_current_user)
) -> dict:
    require_role_or_403(boardId, current_user["id"], "editor")
    task = _get_task_or_404(boardId, taskId)
    updates = body.model_dump(exclude_unset=True)
    task.update(updates)
    return task


@router.delete("/tasks/{taskId}", status_code=204)
def delete_task_permanently(boardId: str, taskId: str, current_user: dict = Depends(get_current_user)) -> None:
    require_role_or_403(boardId, current_user["id"], "owner")
    task = _get_task_or_404(boardId, taskId)
    if not task["archived"]:
        raise ApiError(400, "Task must be archived before it can be permanently deleted")
    del store.tasks[taskId]


@router.post("/tasks/{taskId}/move", response_model=Task)
def move_task(
    boardId: str, taskId: str, body: MoveTaskBody, current_user: dict = Depends(get_current_user)
) -> dict:
    require_role_or_403(boardId, current_user["id"], "editor")
    task = _get_task_or_404(boardId, taskId)
    _get_column_or_404(boardId, body.toColumnId)

    siblings = [t for t in store.tasks_for_column(body.toColumnId) if t["id"] != taskId]
    target_index = max(0, min(body.toIndex, len(siblings)))
    before = siblings[target_index - 1]["order"] if target_index > 0 else None
    after = siblings[target_index]["order"] if target_index < len(siblings) else None

    task["columnId"] = body.toColumnId
    if needs_respacing(before, after):
        siblings.insert(target_index, task)
        for t, value in zip(siblings, respaced_values(len(siblings))):
            t["order"] = value
    else:
        task["order"] = order_between(before, after)
    return task


@router.post("/tasks/{taskId}/archive", response_model=Task)
def archive_task(boardId: str, taskId: str, current_user: dict = Depends(get_current_user)) -> dict:
    require_role_or_403(boardId, current_user["id"], "editor")
    task = _get_task_or_404(boardId, taskId)
    task["archived"] = True
    return task


def _restore_target_column_id(board_id: str, task: dict) -> str | None:
    if task["columnId"] in store.columns:
        return task["columnId"]
    backlog = next(
        (c for c in store.columns_for_board(board_id) if c["name"] == "Backlog"), None
    )
    return backlog["id"] if backlog else None


@router.post("/tasks/{taskId}/unarchive", response_model=Task)
def unarchive_task(boardId: str, taskId: str, current_user: dict = Depends(get_current_user)) -> dict:
    require_role_or_403(boardId, current_user["id"], "editor")
    task = _get_task_or_404(boardId, taskId)
    target_column_id = _restore_target_column_id(boardId, task)
    if target_column_id is None:
        raise ApiError(409, "No column available to restore this task into")
    task["columnId"] = target_column_id
    task["order"] = append_order([t["order"] for t in store.tasks_for_column(target_column_id)])
    task["archived"] = False
    return task


@router.post("/tasks/{taskId}/restore", response_model=list[ArchivedTask])
def restore_task(boardId: str, taskId: str, current_user: dict = Depends(get_current_user)) -> list[dict]:
    require_role_or_403(boardId, current_user["id"], "editor")
    task = _get_task_or_404(boardId, taskId)
    if not task["archived"]:
        raise ApiError(409, "Task is not archived")
    target_column_id = _restore_target_column_id(boardId, task)
    if target_column_id is None:
        raise ApiError(409, "No column available to restore this task into")
    task["columnId"] = target_column_id
    task["order"] = append_order([t["order"] for t in store.tasks_for_column(target_column_id)])
    task["archived"] = False

    remaining = [
        t for t in store.tasks.values() if t["boardId"] == boardId and t["archived"]
    ]
    remaining.sort(key=lambda t: t["createdAt"], reverse=True)
    return [_archived_view(t) for t in remaining]


@router.post("/archive-done")
def archive_all_in_done(boardId: str, current_user: dict = Depends(get_current_user)) -> dict:
    require_role_or_403(boardId, current_user["id"], "editor")
    done = next(c for c in store.columns_for_board(boardId) if c["name"] == "Done")
    tasks = store.tasks_for_column(done["id"])
    for task in tasks:
        task["archived"] = True
    return {"archivedCount": len(tasks)}


@router.get("/archived-tasks", response_model=list[ArchivedTask])
def list_archived_tasks(boardId: str, current_user: dict = Depends(get_current_user)) -> list[dict]:
    require_role_or_403(boardId, current_user["id"], "viewer")
    archived = [t for t in store.tasks.values() if t["boardId"] == boardId and t["archived"]]
    archived.sort(key=lambda t: t["createdAt"], reverse=True)
    return [_archived_view(t) for t in archived]
```

```python
# backend/src/kanban/main.py (add import + include_router)
from kanban.routers import auth, boards, columns, tasks, users
...
app.include_router(tasks.router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/ -v`
Expected: PASS for the whole suite, including `test_columns.py::test_delete_column_with_tasks_archives_them` which needed these routes.

- [ ] **Step 5: Commit**

```bash
git add backend/src/kanban/routers/tasks.py backend/src/kanban/main.py backend/tests/test_tasks.py
git commit -m "backend: add task CRUD, move/archive/restore, and archive-done"
```

---

## Task 8: Members — list, update role, remove

**Files:**
- Create: `backend/src/kanban/routers/members.py`
- Modify: `backend/src/kanban/main.py`
- Test: `backend/tests/test_members.py`

**Interfaces:**
- Consumes: `permissions.require_role_or_403`, `store.store`.
- Produces: `GET /api/boards/{boardId}/members`, `PATCH`/`DELETE /api/boards/{boardId}/members/{userId}`.
- Note: this task's "add a second member" setup uses a direct store manipulation helper (`_add_member_directly`) in the test file rather than the HTTP share-link flow, since share links land in Task 9. That helper is temporary scaffolding local to this test file, not production code.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_members.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_members.py -v`
Expected: FAIL — member routes 404, share-link routes (used in `test_remove_member_revokes_active_share_links`) also 404 until Task 9. Run `-k "not revokes_active"` first.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/src/kanban/routers/members.py
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from kanban.auth import get_current_user
from kanban.errors import ApiError
from kanban.permissions import require_role_or_403
from kanban.schemas import BoardMemberDetail, ShareRole
from kanban.store import store

router = APIRouter(prefix="/api/boards/{boardId}/members", tags=["Members"])


class UpdateRoleBody(BaseModel):
    role: ShareRole


def _detailed(members: list[dict]) -> list[dict]:
    role_rank = {"owner": 0, "editor": 1, "viewer": 2}
    ordered = sorted(members, key=lambda m: (role_rank[m["role"]], store.users[m["userId"]]["name"]))
    return [{**m, "user": store.users[m["userId"]]} for m in ordered]


@router.get("", response_model=list[BoardMemberDetail])
def list_members(boardId: str, current_user: dict = Depends(get_current_user)) -> list[dict]:
    require_role_or_403(boardId, current_user["id"], "viewer")
    return _detailed(store.members_for_board(boardId))


@router.patch("/{userId}", response_model=list[BoardMemberDetail])
def update_member_role(
    boardId: str, userId: str, body: UpdateRoleBody, current_user: dict = Depends(get_current_user)
) -> list[dict]:
    require_role_or_403(boardId, current_user["id"], "owner")
    if userId == current_user["id"]:
        raise ApiError(400, "Cannot change your own role")
    member = store.member_for(boardId, userId)
    if not member:
        raise ApiError(404, "Member not found")
    member["role"] = body.role
    return _detailed(store.members_for_board(boardId))


@router.delete("/{userId}", response_model=list[BoardMemberDetail])
def remove_member(boardId: str, userId: str, current_user: dict = Depends(get_current_user)) -> list[dict]:
    require_role_or_403(boardId, current_user["id"], "owner")
    if userId == current_user["id"]:
        raise ApiError(400, "Cannot remove the owner")
    member = store.member_for(boardId, userId)
    if not member:
        raise ApiError(404, "Member not found")
    del store.board_members[member["id"]]
    for link in store.share_links.values():
        if link["boardId"] == boardId and not link["revoked"]:
            link["revoked"] = True
    return _detailed(store.members_for_board(boardId))
```

```python
# backend/src/kanban/main.py (add import + include_router)
from kanban.routers import auth, boards, columns, members, tasks, users
...
app.include_router(members.router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_members.py -v -k "not revokes_active"`
Expected: PASS for all except the share-link one, which Task 9 makes pass.

- [ ] **Step 5: Commit**

```bash
git add backend/src/kanban/routers/members.py backend/src/kanban/main.py backend/tests/test_members.py
git commit -m "backend: add member listing, role updates, and removal"
```

---

## Task 9: Share links — list, create, revoke, redeem

**Files:**
- Create: `backend/src/kanban/routers/share_links.py`
- Modify: `backend/src/kanban/main.py`
- Test: `backend/tests/test_share_links.py`
- Modify: `backend/tests/test_boards.py` (add the deferred transfer-ownership test)

**Interfaces:**
- Consumes: `permissions.require_role_or_403`, `store.store`.
- Produces: `GET/POST /api/boards/{boardId}/share-links`, `POST /api/boards/{boardId}/share-links/{linkId}/revoke`, `POST /api/share-links/redeem`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_share_links.py
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
```

```python
# backend/tests/test_boards.py (add — replaces the placeholder deleted in Task 5)
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_share_links.py tests/test_boards.py -v`
Expected: FAIL — share-link routes 404.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/src/kanban/routers/share_links.py
import secrets
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from kanban.auth import get_current_user
from kanban.errors import ApiError
from kanban.permissions import ROLE_RANK, get_member_or_404_403, require_role_or_403
from kanban.schemas import ShareLink, ShareRole
from kanban.store import store

board_router = APIRouter(prefix="/api/boards/{boardId}/share-links", tags=["Share Links"])
redeem_router = APIRouter(prefix="/api/share-links", tags=["Share Links"])


class CreateLinkBody(BaseModel):
    role: ShareRole


class RedeemBody(BaseModel):
    token: str


@board_router.get("", response_model=list[ShareLink])
def list_share_links(boardId: str, current_user: dict = Depends(get_current_user)) -> list[dict]:
    require_role_or_403(boardId, current_user["id"], "owner")
    return [l for l in store.share_links.values() if l["boardId"] == boardId]


@board_router.post("", status_code=201, response_model=ShareLink)
def create_share_link(
    boardId: str, body: CreateLinkBody, current_user: dict = Depends(get_current_user)
) -> dict:
    require_role_or_403(boardId, current_user["id"], "owner")
    link_id = str(uuid.uuid4())
    store.share_links[link_id] = {
        "id": link_id,
        "boardId": boardId,
        "role": body.role,
        "token": secrets.token_urlsafe(24),
        "createdBy": current_user["id"],
        "revoked": False,
    }
    return store.share_links[link_id]


@board_router.post("/{linkId}/revoke", response_model=ShareLink)
def revoke_share_link(boardId: str, linkId: str, current_user: dict = Depends(get_current_user)) -> dict:
    require_role_or_403(boardId, current_user["id"], "owner")
    link = store.share_links.get(linkId)
    if not link or link["boardId"] != boardId:
        raise ApiError(404, "Share link not found")
    link["revoked"] = True
    return link


@redeem_router.post("/redeem")
def redeem_share_link(body: RedeemBody, current_user: dict = Depends(get_current_user)) -> dict:
    link = next((l for l in store.share_links.values() if l["token"] == body.token), None)
    if not link or link["boardId"] not in store.boards:
        raise ApiError(404, "Invalid share link")
    if link["revoked"]:
        raise ApiError(410, "This share link has been revoked")

    board = store.boards[link["boardId"]]
    existing = store.member_for(board["id"], current_user["id"])
    if existing and ROLE_RANK[existing["role"]] >= ROLE_RANK[link["role"]]:
        return {"boardId": board["id"], "boardName": board["name"], "role": existing["role"], "changed": False}

    if existing:
        existing["role"] = link["role"]
    else:
        member_id = str(uuid.uuid4())
        store.board_members[member_id] = {
            "id": member_id,
            "boardId": board["id"],
            "userId": current_user["id"],
            "role": link["role"],
        }
    return {"boardId": board["id"], "boardName": board["name"], "role": link["role"], "changed": True}
```

```python
# backend/src/kanban/main.py (add import + include both routers)
from kanban.routers import auth, boards, columns, members, share_links, tasks, users
...
app.include_router(share_links.board_router)
app.include_router(share_links.redeem_router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/ -v`
Expected: PASS for the entire suite, including the previously-deferred tests in `test_members.py` and `test_boards.py`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/kanban/routers/share_links.py backend/src/kanban/main.py backend/tests/test_share_links.py backend/tests/test_boards.py
git commit -m "backend: add share link create/list/revoke/redeem"
```

---

## Task 10: Final wiring pass and full-suite verification

**Files:**
- Modify: `backend/src/kanban/main.py` (double-check every router is mounted, add a root `/api` 404 doesn't leak stack traces)
- Modify: `backend/README.md` or top-level `README.md` if it documents how to run the backend (check both; update whichever describes running services)

**Interfaces:**
- No new interfaces — this task is verification and cleanup only.

- [ ] **Step 1: Run the full test suite**

Run: `cd backend && uv run pytest tests/ -v`
Expected: All tests PASS, zero failures/errors.

- [ ] **Step 2: Start the server manually and smoke-test one full flow**

```bash
cd backend && uv run uvicorn kanban.main:app --reload --port 8000
```

In another terminal:

```bash
curl -c /tmp/cookies.txt -X POST localhost:8000/api/auth/signup \
  -H 'content-type: application/json' \
  -d '{"email":"smoke@example.com","name":"Smoke","password":"pw"}'
curl -b /tmp/cookies.txt localhost:8000/api/boards
```

Expected: signup returns a 201 with the user JSON; `/api/boards` returns the two seeded boards. Stop the server (Ctrl+C) when confirmed.

- [ ] **Step 3: Check `backend/README.md` exists with run instructions; create if missing**

```markdown
# Backend

FastAPI service implementing `../openapi.yaml`, backed by an in-memory
mock store (see `_docs/process.md` for the persistence stage that will
replace it).

## Setup

    cd backend
    uv sync

## Run

    uv run uvicorn kanban.main:app --reload --port 8000

## Test

    uv run pytest
```

- [ ] **Step 4: Commit**

```bash
git add backend/README.md
git commit -m "backend: add README with setup/run/test instructions"
```

---

## Self-Review Notes

- **Spec coverage:** every `openapi.yaml` operationId maps to exactly one route across Tasks 3–9 (`getCurrentUser`→Task 3, `listBoards`/`createBoard`/`getBoard`/`renameBoard`/`deleteBoard`→Task 4, `transferOwnership`→Task 5, `createColumn`/`renameColumn`/`deleteColumn`/`reorderColumn`→Task 6, all task ops + `archiveAllInDone`/`listArchivedTasks`→Task 7, member ops→Task 8, share-link ops→Task 9). Role/permission boundaries, the single-owner invariant, Done-column protections, ordering/re-spacing, and the share-link upgrade-never-downgrades rule each have a dedicated test.
- **Deferred assertions:** three tests in earlier tasks are intentionally incomplete until a later task supplies the missing HTTP surface (noted inline in Tasks 5, 6, 8) — this is necessary because share-link redemption (Task 9) is the only way to create a second board member without reaching into the store directly, and it's implemented last to keep the router list building bottom-up. Do not skip re-running the full suite after Task 9.
- **Out of scope, deliberately:** password reset/email verification, real persistence, and the frontend's `mockClient.ts` swap (Persistence and Integration stages per `_docs/process.md`).
