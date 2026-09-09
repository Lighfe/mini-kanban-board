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
