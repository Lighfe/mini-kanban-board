from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator

from kanban.auth import get_current_user, seed_default_board
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
