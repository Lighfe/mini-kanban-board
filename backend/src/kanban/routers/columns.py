import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator

from kanban.auth import get_current_user
from kanban.errors import ApiError
from kanban.ordering import GAP, append_order, needs_respacing, order_between, respaced_values
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
        _respace_columns(boardId, columns)
        columns = store.columns_for_board(boardId)
        done = next(c for c in columns if c["name"] == "Done")
        before_done = [c for c in columns if c["id"] != done["id"]]
        order = order_between(before_done[-1]["order"] if before_done else None, done["order"])
    column_id = str(uuid.uuid4())
    store.columns[column_id] = {"id": column_id, "boardId": boardId, "name": body.name, "order": order}
    return store.columns[column_id]


def _respace_columns(board_id: str, columns_in_order: list[dict]) -> None:
    """Reassign evenly-spaced `order` values to every column on a board,
    given `columns_in_order` already in the desired final order (Done last,
    or anywhere — it is located by name, not position).

    The slots are shifted up by one GAP so the first non-"Done" column never
    lands on 0.0: `respaced_values(n)` starts at 0.0, and a bare 0.0 order
    collides exactly with `order_between(None, 0.0) == 0.0` on the next
    "move to front" reorder, silently re-creating the ordering bug fixed in
    `auth.py`'s board seeding. Shifting by GAP keeps every slot strictly
    positive, so `order_between(None, first_slot)` always yields a smaller,
    distinct value.
    """
    non_done = [c for c in columns_in_order if c["name"] != "Done"]
    done = next(c for c in columns_in_order if c["name"] == "Done")
    slots = [value + GAP for value in respaced_values(len(non_done) + 1)]
    for column, value in zip(non_done, slots):
        column["order"] = value
    done["order"] = slots[-1] + GAP


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
        _respace_columns(boardId, [*non_done, done])
    else:
        column["order"] = order_between(before, after)

    return store.columns_for_board(boardId)
