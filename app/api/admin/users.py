"""Admin endpoints — user management."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, func, select

from app.api.admin.deps import require_admin
from app.core.logging import logger
from app.models.session import Session as ChatSession
from app.models.user import User
from app.services.database import database_service

router = APIRouter(prefix="/users", tags=["admin-users"])


class UserUpdate(BaseModel):
    """Partial update payload for a user."""

    is_admin: Optional[bool] = None
    username: Optional[str] = None


def _user_dict(u: User, session_count: int = 0) -> dict:
    return {
        "id": u.id,
        "email": u.email,
        "username": u.username,
        "is_admin": u.is_admin,
        "session_count": session_count,
        "created_at": u.created_at.isoformat(),
    }


@router.get("", dependencies=[Depends(require_admin)])
async def list_users():
    """List all registered users with their session counts."""
    with Session(database_service.engine) as db:
        users = db.exec(select(User).order_by(User.id)).all()

        counts_rows = db.exec(
            select(ChatSession.user_id, func.count(ChatSession.id).label("cnt"))
            .group_by(ChatSession.user_id)
        ).all()
        counts = {r.user_id: r.cnt for r in counts_rows}

    return [_user_dict(u, counts.get(u.id, 0)) for u in users]


@router.patch("/{user_id}", dependencies=[Depends(require_admin)])
async def update_user(user_id: int, body: UserUpdate):
    """Update user properties (is_admin, username)."""
    with Session(database_service.engine) as db:
        user = db.exec(select(User).where(User.id == user_id)).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found.")

        if body.is_admin is not None:
            user.is_admin = body.is_admin
        if body.username is not None:
            user.username = body.username or None

        db.add(user)
        db.commit()
        db.refresh(user)
        logger.info("admin_user_updated", user_id=user_id)
        return _user_dict(user)


@router.delete("/{user_id}", dependencies=[Depends(require_admin)])
async def delete_user(user_id: int):
    """Permanently delete a user account and all their sessions."""
    with Session(database_service.engine) as db:
        user = db.exec(select(User).where(User.id == user_id)).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found.")

        db.delete(user)
        db.commit()

    logger.info("admin_user_deleted", user_id=user_id)
    return {"status": "deleted", "user_id": user_id}
