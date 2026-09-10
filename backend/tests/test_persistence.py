"""Regression tests for the Persistence stage (_docs/process.md): the
Store must be backed by a real, durable database — reachable through any
SQLAlchemy URL configured via KANBAN_DATABASE_URL — not an in-process
dict. These write through one engine/session and read back through a
completely independent one pointed at the same SQLite file, which an
in-memory-only stand-in could not pass.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session as SqlAlchemySession

from kanban.db import Base, TaskRow, UserRow
from kanban.store import Store


def test_data_written_and_committed_is_durable_on_disk_for_an_independent_connection(tmp_path):
    db_path = tmp_path / "persistence.db"
    url = f"sqlite:///{db_path}"

    writer_engine = create_engine(url)
    Base.metadata.create_all(writer_engine)
    with SqlAlchemySession(writer_engine) as writer_session:
        writer_session.add(
            UserRow(id="u1", email="alice@example.com", name="Alice", passwordHash="hash")
        )
        writer_session.commit()
    writer_engine.dispose()

    # A brand-new engine/session against the same file, sharing no Python
    # object (and no cached identity map) with the writer above.
    reader_engine = create_engine(url)
    with SqlAlchemySession(reader_engine) as reader_session:
        row = reader_session.get(UserRow, "u1")
        assert row is not None
        assert row.email == "alice@example.com"
        assert row.name == "Alice"
    reader_engine.dispose()


def test_reset_drops_data_from_every_table_not_just_the_one_touched(tmp_path):
    """Store.reset() (used by the autouse per-test fixture) recreates the
    schema via drop_all/create_all rather than clearing dicts one by one —
    verify it actually empties every table, board_members and share_links
    included, not just the table a given test happened to touch."""
    store = Store()
    store.users["u1"] = {"id": "u1", "email": "a@example.com", "name": "Alice"}
    store.boards["b1"] = {"id": "b1", "name": "Board"}
    store.board_members["m1"] = {"id": "m1", "boardId": "b1", "userId": "u1", "role": "owner"}
    store.columns["c1"] = {"id": "c1", "boardId": "b1", "name": "Backlog", "order": 1000.0}
    store.tasks["t1"] = {"id": "t1", "boardId": "b1", "columnId": "c1", "order": 1000.0, "archived": False}
    store.share_links["l1"] = {"id": "l1", "boardId": "b1", "role": "viewer", "token": "tok", "revoked": False}
    store.sessions["sess-token"] = "u1"

    store.reset()

    assert store.users == {}
    assert store.boards == {}
    assert store.board_members == {}
    assert store.columns == {}
    assert store.tasks == {}
    assert store.share_links == {}
    assert store.sessions.get("sess-token") is None


def test_two_store_facades_see_each_others_committed_and_pending_writes():
    """Store() is a stateless facade over the one process-wide Session
    (kanban/db.py), not a per-instance cache — mutating through one
    instance must be visible through another, matching how a router
    (via the module-level `store` singleton) and a test (via a fresh
    Store()) are expected to observe the same data."""
    first = Store()
    second = Store()

    first.tasks["t1"] = {"id": "t1", "boardId": "b1", "columnId": "c1", "order": 1000.0, "archived": False}

    assert second.tasks["t1"]["order"] == 1000.0

    second.tasks["t1"]["order"] = 2000.0

    assert first.tasks["t1"]["order"] == 2000.0


def test_task_row_created_from_a_partial_dict_defaults_missing_columns_to_null():
    """The routers always create full rows, but the DB schema itself
    can't require that — tests/test_ordering.py's Store unit tests
    construct tasks/columns/members with only a few fields set, so every
    non-PK column must be nullable rather than NOT NULL."""
    store = Store()
    store.tasks["t1"] = {"id": "t1", "columnId": "c1", "order": 1000.0, "archived": False}

    task = store.tasks["t1"]
    assert task["boardId"] is None
    assert task["title"] is None
    assert isinstance(task, TaskRow)
