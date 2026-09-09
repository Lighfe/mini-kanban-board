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
