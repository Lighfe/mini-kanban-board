import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator

from kanban.auth import get_current_user
from kanban.errors import ApiError
from kanban.ordering import GAP, append_order, needs_respacing, order_between, respaced_values
from kanban.permissions import require_role_or_403
from kanban.schemas import ArchivedTask, Priority, Task
from kanban.store import store

router = APIRouter(prefix="/api/boards/{boardId}", tags=["Tasks"])


class CreateTaskBody(BaseModel):
    title: str
    description: str = ""
    dueDate: date | None = None
    priority: Priority = "Medium"

    @field_validator("title")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be blank")
        return value


class UpdateTaskBody(BaseModel):
    title: str | None = None
    description: str | None = None
    dueDate: date | None = None
    priority: Priority | None = None


class MoveTaskBody(BaseModel):
    toColumnId: str
    toIndex: int


def _get_task_or_404(board_id: str, task_id: str) -> dict:
    task = store.tasks.get(task_id)
    if not task or task["boardId"] != board_id:
        raise ApiError(404, "Task not found")
    return task


def _get_column_or_404(board_id: str, column_id: str) -> dict:
    column = store.columns.get(column_id)
    if not column or column["boardId"] != board_id:
        raise ApiError(404, "Column not found")
    return column


def _archived_view(task: dict) -> dict:
    column = store.columns.get(task["columnId"])
    column_name = column["name"] if column else "Deleted column"
    return {**task, "columnName": column_name}


@router.post("/columns/{columnId}/tasks", status_code=201, response_model=Task)
def create_task(
    boardId: str, columnId: str, body: CreateTaskBody, current_user: dict = Depends(get_current_user)
) -> dict:
    require_role_or_403(boardId, current_user["id"], "editor")
    _get_column_or_404(boardId, columnId)
    existing = store.tasks_for_column(columnId)
    order = append_order([t["order"] for t in existing])
    task_id = str(uuid.uuid4())
    store.tasks[task_id] = {
        "id": task_id,
        "boardId": boardId,
        "columnId": columnId,
        "title": body.title,
        "description": body.description,
        "dueDate": body.dueDate,
        "priority": body.priority,
        "order": order,
        "archived": False,
        "createdAt": datetime.now(timezone.utc),
        "createdBy": current_user["id"],
    }
    return store.tasks[task_id]


@router.patch("/tasks/{taskId}", response_model=Task)
def update_task(
    boardId: str, taskId: str, body: UpdateTaskBody, current_user: dict = Depends(get_current_user)
) -> dict:
    require_role_or_403(boardId, current_user["id"], "editor")
    task = _get_task_or_404(boardId, taskId)
    updates = body.model_dump(exclude_unset=True)
    task.update(updates)
    return task


@router.delete("/tasks/{taskId}", status_code=204)
def delete_task_permanently(boardId: str, taskId: str, current_user: dict = Depends(get_current_user)) -> None:
    require_role_or_403(boardId, current_user["id"], "owner")
    task = _get_task_or_404(boardId, taskId)
    if not task["archived"]:
        raise ApiError(400, "Task must be archived before it can be permanently deleted")
    del store.tasks[taskId]


@router.post("/tasks/{taskId}/move", response_model=Task)
def move_task(
    boardId: str, taskId: str, body: MoveTaskBody, current_user: dict = Depends(get_current_user)
) -> dict:
    require_role_or_403(boardId, current_user["id"], "editor")
    task = _get_task_or_404(boardId, taskId)
    _get_column_or_404(boardId, body.toColumnId)

    siblings = [t for t in store.tasks_for_column(body.toColumnId) if t["id"] != taskId]
    target_index = max(0, min(body.toIndex, len(siblings)))
    before = siblings[target_index - 1]["order"] if target_index > 0 else None
    after = siblings[target_index]["order"] if target_index < len(siblings) else None

    task["columnId"] = body.toColumnId
    if needs_respacing(before, after):
        siblings.insert(target_index, task)
        # Shift the respaced slots up by one GAP so the first task never
        # lands on 0.0 (respaced_values(n) starts at 0.0). A bare 0.0 order
        # would collide exactly with order_between(None, 0.0) == 0.0 on a
        # later "move to front" reorder, and needs_respacing(None, 0.0)
        # returns False (it only fires when both neighbors are non-None),
        # so the collision would silently never be caught. See columns.py's
        # _respace_columns for the same fix applied to column ordering.
        slots = [value + GAP for value in respaced_values(len(siblings))]
        for t, value in zip(siblings, slots):
            t["order"] = value
    else:
        task["order"] = order_between(before, after)
    return task


@router.post("/tasks/{taskId}/archive", response_model=Task)
def archive_task(boardId: str, taskId: str, current_user: dict = Depends(get_current_user)) -> dict:
    require_role_or_403(boardId, current_user["id"], "editor")
    task = _get_task_or_404(boardId, taskId)
    task["archived"] = True
    return task


def _restore_target_column_id(board_id: str, task: dict) -> str | None:
    if task["columnId"] in store.columns:
        return task["columnId"]
    backlog = next(
        (c for c in store.columns_for_board(board_id) if c["name"] == "Backlog"), None
    )
    return backlog["id"] if backlog else None


@router.post("/tasks/{taskId}/unarchive", response_model=Task)
def unarchive_task(boardId: str, taskId: str, current_user: dict = Depends(get_current_user)) -> dict:
    require_role_or_403(boardId, current_user["id"], "editor")
    task = _get_task_or_404(boardId, taskId)
    target_column_id = _restore_target_column_id(boardId, task)
    if target_column_id is None:
        raise ApiError(409, "No column available to restore this task into")
    task["columnId"] = target_column_id
    task["order"] = append_order([t["order"] for t in store.tasks_for_column(target_column_id)])
    task["archived"] = False
    return task


@router.post("/tasks/{taskId}/restore", response_model=list[ArchivedTask])
def restore_task(boardId: str, taskId: str, current_user: dict = Depends(get_current_user)) -> list[dict]:
    require_role_or_403(boardId, current_user["id"], "editor")
    task = _get_task_or_404(boardId, taskId)
    if not task["archived"]:
        raise ApiError(409, "Task is not archived")
    target_column_id = _restore_target_column_id(boardId, task)
    if target_column_id is None:
        raise ApiError(409, "No column available to restore this task into")
    task["columnId"] = target_column_id
    task["order"] = append_order([t["order"] for t in store.tasks_for_column(target_column_id)])
    task["archived"] = False

    remaining = [
        t for t in store.tasks.values() if t["boardId"] == boardId and t["archived"]
    ]
    remaining.sort(key=lambda t: t["createdAt"], reverse=True)
    return [_archived_view(t) for t in remaining]


@router.post("/archive-done")
def archive_all_in_done(boardId: str, current_user: dict = Depends(get_current_user)) -> dict:
    require_role_or_403(boardId, current_user["id"], "editor")
    done = next(c for c in store.columns_for_board(boardId) if c["name"] == "Done")
    tasks = store.tasks_for_column(done["id"])
    for task in tasks:
        task["archived"] = True
    return {"archivedCount": len(tasks)}


@router.get("/archived-tasks", response_model=list[ArchivedTask])
def list_archived_tasks(boardId: str, current_user: dict = Depends(get_current_user)) -> list[dict]:
    require_role_or_403(boardId, current_user["id"], "viewer")
    archived = [t for t in store.tasks.values() if t["boardId"] == boardId and t["archived"]]
    archived.sort(key=lambda t: t["createdAt"], reverse=True)
    return [_archived_view(t) for t in archived]
