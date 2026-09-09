import secrets
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from kanban.auth import get_current_user
from kanban.errors import ApiError
from kanban.permissions import ROLE_RANK, require_role_or_403
from kanban.schemas import ShareLink, ShareRole
from kanban.store import store

board_router = APIRouter(prefix="/api/boards/{boardId}/share-links", tags=["Share Links"])
redeem_router = APIRouter(prefix="/api/share-links", tags=["Share Links"])


class CreateLinkBody(BaseModel):
    role: ShareRole


class RedeemBody(BaseModel):
    token: str


@board_router.get("", response_model=list[ShareLink])
def list_share_links(boardId: str, current_user: dict = Depends(get_current_user)) -> list[dict]:
    require_role_or_403(boardId, current_user["id"], "owner")
    return [l for l in store.share_links.values() if l["boardId"] == boardId]


@board_router.post("", status_code=201, response_model=ShareLink)
def create_share_link(
    boardId: str, body: CreateLinkBody, current_user: dict = Depends(get_current_user)
) -> dict:
    require_role_or_403(boardId, current_user["id"], "owner")
    link_id = str(uuid.uuid4())
    store.share_links[link_id] = {
        "id": link_id,
        "boardId": boardId,
        "role": body.role,
        "token": secrets.token_urlsafe(24),
        "createdBy": current_user["id"],
        "revoked": False,
    }
    return store.share_links[link_id]


@board_router.post("/{linkId}/revoke", response_model=ShareLink)
def revoke_share_link(boardId: str, linkId: str, current_user: dict = Depends(get_current_user)) -> dict:
    require_role_or_403(boardId, current_user["id"], "owner")
    link = store.share_links.get(linkId)
    if not link or link["boardId"] != boardId:
        raise ApiError(404, "Share link not found")
    link["revoked"] = True
    return link


@redeem_router.post("/redeem")
def redeem_share_link(body: RedeemBody, current_user: dict = Depends(get_current_user)) -> dict:
    link = next((l for l in store.share_links.values() if l["token"] == body.token), None)
    if not link or link["boardId"] not in store.boards:
        raise ApiError(404, "Invalid share link")
    if link["revoked"]:
        raise ApiError(410, "This share link has been revoked")

    board = store.boards[link["boardId"]]
    existing = store.member_for(board["id"], current_user["id"])
    if existing and ROLE_RANK[existing["role"]] >= ROLE_RANK[link["role"]]:
        return {"boardId": board["id"], "boardName": board["name"], "role": existing["role"], "changed": False}

    if existing:
        existing["role"] = link["role"]
    else:
        member_id = str(uuid.uuid4())
        store.board_members[member_id] = {
            "id": member_id,
            "boardId": board["id"],
            "userId": current_user["id"],
            "role": link["role"],
        }
    return {"boardId": board["id"], "boardName": board["name"], "role": link["role"], "changed": True}
