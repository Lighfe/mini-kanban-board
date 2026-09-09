from kanban.errors import ApiError
from kanban.store import store

ROLE_RANK = {"viewer": 1, "editor": 2, "owner": 3}


def get_member_or_404_403(board_id: str, user_id: str) -> tuple[dict, dict]:
    board = store.boards.get(board_id)
    if not board:
        raise ApiError(404, "Board not found")
    member = store.member_for(board_id, user_id)
    if not member:
        raise ApiError(403, "Not a member of this board")
    return board, member


def require_role_or_403(board_id: str, user_id: str, min_role: str) -> tuple[dict, dict]:
    board, member = get_member_or_404_403(board_id, user_id)
    if ROLE_RANK[member["role"]] < ROLE_RANK[min_role]:
        raise ApiError(403, f"Requires {min_role} role or higher")
    return board, member
