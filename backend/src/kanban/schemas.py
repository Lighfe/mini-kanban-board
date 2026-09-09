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
