"""Regression tests for Finding 1 (shared-store operations are not atomic).

FastAPI dispatches sync `def` route handlers to a worker thread pool, so
concurrent requests against the shared in-memory Store can interleave
mid read-modify-write. `kanban/locking.py` adds an ASGI middleware that
holds a single process-wide `asyncio.Lock` for the full duration of every
request, so no two requests' handler bodies ever run concurrently.

A note on how these tests actually exercise concurrency: `TestClient`, when
used as a plain object (`TestClient(app)`), opens a *new* anyio portal
(its own event loop, in its own dedicated thread) for every single
request — see `starlette.testclient._TestClientTransport.handle_request`.
That defeats an `asyncio.Lock`-based fix, because the lock ends up shared
across futures that belong to *different* event loops, which is a
documented asyncio hazard (a release on one loop cannot correctly wake a
waiter parked on another loop) — in manual testing this produced an
outright hang, not just a missed race. Using `TestClient` as a context
manager (`with TestClient(app) as client:`) instead makes it open exactly
one portal for the client's lifetime, so all requests run on the *same*
event loop, exactly like a real deployment behind a single uvicorn worker.
Under that single event loop, a sync route handler is still off-loaded to
a real OS worker thread (via anyio's `to_thread`), so multiple in-flight
requests' handler bodies genuinely execute concurrently in separate
threads — that's the real race window this suite defends against, and it
was confirmed to reproduce both violations below before kanban/locking.py
was added, and to stop reproducing after.
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi.testclient import TestClient

import kanban.routers.boards as boards_router
from kanban.main import app
from kanban.store import store

N = 20


def test_concurrent_signups_with_the_same_email_only_one_succeeds():
    """Reproduces Codex's first finding: N concurrent signups with an
    identical email must not all pass signup's duplicate-email check
    before any of them creates the user."""
    email = "race@example.com"

    def do_signup(client: TestClient, i: int) -> int:
        response = client.post(
            "/api/auth/signup",
            json={"email": email, "name": f"User {i}", "password": "pw"},
        )
        return response.status_code

    with TestClient(app) as client:
        with ThreadPoolExecutor(max_workers=N) as executor:
            futures = [executor.submit(do_signup, client, i) for i in range(N)]
            statuses = [f.result(timeout=30) for f in as_completed(futures, timeout=30)]

    successes = [s for s in statuses if s == 201]
    failures = [s for s in statuses if s == 400]
    assert len(successes) == 1, f"expected exactly one signup to succeed, got statuses={statuses}"
    assert len(failures) == N - 1

    matching_users = [u for u in store.users.values() if u["email"] == email]
    assert len(matching_users) == 1, f"expected exactly one user with {email!r}, found {len(matching_users)}"


def test_concurrent_transfer_ownership_leaves_exactly_one_owner(monkeypatch):
    """Reproduces Codex's second finding: N concurrent transfer-ownership
    calls to N different target members of the same board must not all
    pass the owner-role authorization check before any of them mutates
    membership state, which would leave more than one owner.

    `require_role_or_403` (used by the transfer-ownership handler to both
    authorize the caller and mutate membership right after) is wrapped
    with a small sleep between its authorization check and its return, to
    reliably widen the check-then-mutate window that Codex's live-server
    reproduction hit under real network latency — plain in-memory dict
    operations are fast enough that the race is present but not reliably
    hit within a short, deterministic unit test otherwise.
    """
    original_require_role_or_403 = boards_router.require_role_or_403

    def slow_require_role_or_403(*args, **kwargs):
        result = original_require_role_or_403(*args, **kwargs)
        time.sleep(0.05)
        return result

    monkeypatch.setattr(boards_router, "require_role_or_403", slow_require_role_or_403)

    with TestClient(app) as client:
        client.post("/api/auth/signup", json={"email": "owner@example.com", "name": "Owner", "password": "pw"})
        board_id = client.get("/api/boards").json()[0]["id"]

        targets = []
        for i in range(N):
            link = client.post(f"/api/boards/{board_id}/share-links", json={"role": "editor"}).json()
            target_client = TestClient(app)
            target = target_client.post(
                "/api/auth/signup",
                json={"email": f"target{i}@example.com", "name": f"Target {i}", "password": "pw"},
            ).json()
            redeem_response = target_client.post("/api/share-links/redeem", json={"token": link["token"]})
            assert redeem_response.status_code == 200
            targets.append(target)

        def do_transfer(target: dict) -> int:
            response = client.post(
                f"/api/boards/{board_id}/transfer-ownership",
                json={"toUserId": target["id"]},
            )
            return response.status_code

        with ThreadPoolExecutor(max_workers=N) as executor:
            futures = [executor.submit(do_transfer, target) for target in targets]
            statuses = [f.result(timeout=30) for f in as_completed(futures, timeout=30)]

    successes = [s for s in statuses if s == 200]
    assert len(successes) == 1, f"expected exactly one transfer to succeed, got statuses={statuses}"

    owners = [
        m for m in store.board_members.values() if m["boardId"] == board_id and m["role"] == "owner"
    ]
    assert len(owners) == 1, f"expected exactly one owner on the board, found {len(owners)}"
