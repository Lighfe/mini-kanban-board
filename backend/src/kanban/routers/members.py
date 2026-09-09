from fastapi import APIRouter, Depends
from pydantic import BaseModel

from kanban.auth import get_current_user
from kanban.errors import ApiError
from kanban.permissions import require_role_or_403
from kanban.schemas import BoardMemberDetail, ShareRole
from kanban.store import store

router = APIRouter(prefix="/api/boards/{boardId}/members", tags=["Members"])


class UpdateRoleBody(BaseModel):
    role: ShareRole


def _detailed(members: list[dict]) -> list[dict]:
    role_rank = {"owner": 0, "editor": 1, "viewer": 2}
    ordered = sorted(members, key=lambda m: (role_rank[m["role"]], store.users[m["userId"]]["name"]))
    return [{**m, "user": store.users[m["userId"]]} for m in ordered]


@router.get("", response_model=list[BoardMemberDetail])
def list_members(boardId: str, current_user: dict = Depends(get_current_user)) -> list[dict]:
    require_role_or_403(boardId, current_user["id"], "viewer")
    return _detailed(store.members_for_board(boardId))


@router.patch("/{userId}", response_model=list[BoardMemberDetail])
def update_member_role(
    boardId: str, userId: str, body: UpdateRoleBody, current_user: dict = Depends(get_current_user)
) -> list[dict]:
    require_role_or_403(boardId, current_user["id"], "owner")
    if userId == current_user["id"]:
        raise ApiError(400, "Cannot change your own role")
    member = store.member_for(boardId, userId)
    if not member:
        raise ApiError(404, "Member not found")
    member["role"] = body.role
    return _detailed(store.members_for_board(boardId))


@router.delete("/{userId}", response_model=list[BoardMemberDetail])
def remove_member(boardId: str, userId: str, current_user: dict = Depends(get_current_user)) -> list[dict]:
    require_role_or_403(boardId, current_user["id"], "owner")
    if userId == current_user["id"]:
        raise ApiError(400, "Cannot remove the owner")
    member = store.member_for(boardId, userId)
    if not member:
        raise ApiError(404, "Member not found")
    del store.board_members[member["id"]]
    for link in store.share_links.values():
        if link["boardId"] == boardId and not link["revoked"]:
            link["revoked"] = True
    return _detailed(store.members_for_board(boardId))
