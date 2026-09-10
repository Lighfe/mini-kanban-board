from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

Role = Literal["owner", "editor", "viewer"]
ShareRole = Literal["editor", "viewer"]
Priority = Literal["Low", "Medium", "High"]


class ORMBase(BaseModel):
    # Lets these models validate directly from the ORM rows kanban/store.py
    # hands back (attribute access), not just from plain dicts.
    model_config = ConfigDict(from_attributes=True)


class User(ORMBase):
    id: str
    email: str
    name: str


class Board(ORMBase):
    id: str
    name: str
    createdAt: datetime


class BoardSummary(Board):
    role: Role
    ownerName: str


class Column(ORMBase):
    id: str
    boardId: str
    name: str
    order: float


class Task(ORMBase):
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


class BoardContents(ORMBase):
    board: Board
    role: Role
    columns: list[Column]
    tasks: list[Task]


class BoardMember(ORMBase):
    boardId: str
    userId: str
    role: Role


class BoardMemberDetail(BoardMember):
    user: User


class ShareLink(ORMBase):
    id: str
    boardId: str
    role: ShareRole
    token: str
    createdBy: str
    revoked: bool
