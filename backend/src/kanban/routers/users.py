from fastapi import APIRouter, Depends

from kanban.auth import get_current_user
from kanban.schemas import User

router = APIRouter(prefix="/api", tags=["Users"])


@router.get("/me", response_model=User)
def get_me(current_user: dict = Depends(get_current_user)) -> dict:
    return current_user
