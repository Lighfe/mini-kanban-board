"""SQLAlchemy engine/session/model setup — the real database backing
`kanban.store.Store` (see that module for the dict-like facade routers use).

Which database to connect to is controlled entirely by the
`KANBAN_DATABASE_URL` environment variable (any SQLAlchemy database URL,
e.g. `postgresql+psycopg://user:pass@host/db`), read once at import time.
Defaults to a local SQLite file so the app and test suite run with zero
configuration. See backend/README.md.
"""

import os
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

DATABASE_URL = os.environ.get("KANBAN_DATABASE_URL", "sqlite:///./kanban.db")

_is_sqlite = DATABASE_URL.startswith("sqlite")
_engine_kwargs: dict = {"connect_args": {"check_same_thread": False}} if _is_sqlite else {}
if _is_sqlite:
    # Share a single connection for the engine's whole lifetime instead of
    # SQLAlchemy's default pool (which hands out a *new* connection per
    # checkout). This matters most for `sqlite:///:memory:` (used by the
    # test suite): every SQLite connection is its own private database, so
    # without a shared connection each checkout would see an empty one.
    _engine_kwargs["poolclass"] = StaticPool

engine = create_engine(DATABASE_URL, **_engine_kwargs)


class Base(DeclarativeBase):
    pass


class DictLikeRow:
    """Mixin making a mapped row behave like the plain dict rows the
    previous in-memory Store used, so kanban/store.py's TableProxy and the
    routers built against it (`row["field"]`, `row["field"] = x`, `"field"
    in row`, `row.get(...)`, `row.update({...})`, `{**row}`) need no
    changes.
    """

    def keys(self) -> list[str]:
        return [c.name for c in self.__table__.columns]

    def __getitem__(self, key: str):
        return getattr(self, key)

    def __setitem__(self, key: str, value) -> None:
        setattr(self, key, value)

    def __contains__(self, key: str) -> bool:
        return key in self.keys()

    def get(self, key: str, default=None):
        return getattr(self, key, default)

    def update(self, values: dict) -> None:
        for key, value in values.items():
            setattr(self, key, value)

    def __eq__(self, other):
        if isinstance(other, dict):
            return {k: self[k] for k in self.keys()} == other
        return NotImplemented

    __hash__ = object.__hash__


# Every non-primary-key column is nullable: the DB layer mirrors the old
# in-memory dict store, which never enforced a schema either (validation
# lives in the routers). tests/test_ordering.py's white-box Store tests
# rely on this — they insert partial rows directly to unit-test filtering
# and sorting logic in isolation from the full create-task/create-user
# flows.


class UserRow(DictLikeRow, Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    passwordHash: Mapped[str | None] = mapped_column(String, nullable=True)
    createdAt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SessionRow(DictLikeRow, Base):
    __tablename__ = "sessions"

    token: Mapped[str] = mapped_column(String, primary_key=True)
    userId: Mapped[str | None] = mapped_column(String, nullable=True)


class BoardRow(DictLikeRow, Base):
    __tablename__ = "boards"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    createdAt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BoardMemberRow(DictLikeRow, Base):
    __tablename__ = "board_members"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    boardId: Mapped[str | None] = mapped_column(String, nullable=True)
    userId: Mapped[str | None] = mapped_column(String, nullable=True)
    role: Mapped[str | None] = mapped_column(String, nullable=True)


class ColumnRow(DictLikeRow, Base):
    __tablename__ = "columns"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    boardId: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    order: Mapped[float | None] = mapped_column(Float, nullable=True)


class TaskRow(DictLikeRow, Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    boardId: Mapped[str | None] = mapped_column(String, nullable=True)
    columnId: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    dueDate: Mapped[date | None] = mapped_column(Date, nullable=True)
    priority: Mapped[str | None] = mapped_column(String, nullable=True)
    order: Mapped[float | None] = mapped_column(Float, nullable=True)
    archived: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    createdAt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    createdBy: Mapped[str | None] = mapped_column(String, nullable=True)


class ShareLinkRow(DictLikeRow, Base):
    __tablename__ = "share_links"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    boardId: Mapped[str | None] = mapped_column(String, nullable=True)
    role: Mapped[str | None] = mapped_column(String, nullable=True)
    token: Mapped[str | None] = mapped_column(String, nullable=True)
    createdBy: Mapped[str | None] = mapped_column(String, nullable=True)
    revoked: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


Base.metadata.create_all(engine)

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

# A single process-wide Session, matching the process-wide `Store`
# singleton it backs (see kanban/store.py). Safe because
# kanban/locking.py's SerializeRequestsMiddleware already serializes all
# request handling, so this Session is never used by two threads
# concurrently; that middleware also commits it after every request and
# rolls it back if the request raised.
session: Session = SessionLocal()
