"""Database-backed Store. Every access to the DB from the rest of the app
goes through this module (or through the ORM rows it hands back), so the
actual database — engine, session, table schemas — is defined once in
kanban/db.py and swapping it (a different KANBAN_DATABASE_URL) needs no
changes here.

TableProxy/SessionsProxy give each table a dict-like facade
(`store.boards[id]`, `store.boards[id] = {...}`, `.get`, `in`, `.values()`)
so routers written against the original in-memory `dict[str, dict]` Store
keep working unchanged against real ORM-backed rows.
"""

from typing import Any

from sqlalchemy import select

from kanban.db import (
    Base,
    BoardMemberRow,
    BoardRow,
    ColumnRow,
    ShareLinkRow,
    SessionRow,
    TaskRow,
    UserRow,
    engine,
)
from kanban.db import session as db_session


class TableProxy:
    def __init__(self, model: type) -> None:
        self._model = model

    def __getitem__(self, key: str) -> Any:
        row = db_session.get(self._model, key)
        if row is None:
            raise KeyError(key)
        return row

    def __setitem__(self, key: str, value: dict) -> None:
        row = db_session.get(self._model, key)
        if row is None:
            row = self._model()
            db_session.add(row)
        for field, field_value in value.items():
            setattr(row, field, field_value)

    def __delitem__(self, key: str) -> None:
        row = db_session.get(self._model, key)
        if row is None:
            raise KeyError(key)
        db_session.delete(row)

    def __contains__(self, key: str) -> bool:
        return db_session.get(self._model, key) is not None

    def __len__(self) -> int:
        return len(self.values())

    def get(self, key: str, default=None):
        row = db_session.get(self._model, key)
        return row if row is not None else default

    def values(self) -> list[Any]:
        return list(db_session.scalars(select(self._model)))

    def __eq__(self, other):
        if isinstance(other, dict):
            return {row["id"]: row for row in self.values()} == other
        return NotImplemented


class SessionsProxy:
    """Dict-like facade over the `sessions` table: token -> user id (a
    plain string, unlike the other tables' row facades)."""

    def __setitem__(self, token: str, user_id: str) -> None:
        row = db_session.get(SessionRow, token)
        if row is None:
            row = SessionRow(token=token)
            db_session.add(row)
        row.userId = user_id

    def get(self, token: str, default=None):
        row = db_session.get(SessionRow, token)
        return row.userId if row is not None else default


class Store:
    def __init__(self) -> None:
        self.users = TableProxy(UserRow)
        self.sessions = SessionsProxy()
        self.boards = TableProxy(BoardRow)
        self.board_members = TableProxy(BoardMemberRow)
        self.columns = TableProxy(ColumnRow)
        self.tasks = TableProxy(TaskRow)
        self.share_links = TableProxy(ShareLinkRow)

    def reset(self) -> None:
        # close() (not just rollback()) also clears the Session's identity
        # map. Without that, a row committed by an earlier test would stay
        # cached in-session and get handed back by db_session.get() even
        # after drop_all/create_all recreate the (now-empty) tables —
        # tests reuse fixed ids like "u1"/"b1" across test functions, so
        # this does happen in practice, not just in theory.
        db_session.close()
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)

    def member_for(self, board_id: str, user_id: str) -> dict[str, Any] | None:
        for member in self.board_members.values():
            if member["boardId"] == board_id and member["userId"] == user_id:
                return member
        return None

    def members_for_board(self, board_id: str) -> list[dict[str, Any]]:
        return [m for m in self.board_members.values() if m["boardId"] == board_id]

    def members_detailed_for_board(self, board_id: str) -> list[dict[str, Any]]:
        """Members for a board, joined with their user record and sorted
        owner-first (highest role rank first) then alphabetically by name.

        Uses `permissions.ROLE_RANK` as the single source of truth for role
        ordering; imported locally to avoid a circular import (permissions
        imports store)."""
        from kanban.permissions import ROLE_RANK

        ordered = sorted(
            self.members_for_board(board_id),
            key=lambda m: (-ROLE_RANK[m["role"]], self.users[m["userId"]]["name"]),
        )
        return [{**m, "user": self.users[m["userId"]]} for m in ordered]

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
