"""Shared FastAPI dependencies."""
from typing import Optional

from fastapi import Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import decode_token
from app.models import Role, User

CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


def _extract(authorization: Optional[str], token_q: Optional[str]) -> Optional[str]:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return token_q


async def get_current_user(
    authorization: Optional[str] = Header(default=None),
    token: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    raw = _extract(authorization, token)
    if not raw:
        raise CREDENTIALS_ERROR
    payload = decode_token(raw)
    if not payload or not payload.get("sub"):
        raise CREDENTIALS_ERROR
    user = await db.get(User, payload["sub"])
    if not user or not user.is_active:
        raise CREDENTIALS_ERROR
    return user


async def get_optional_user(
    authorization: Optional[str] = Header(default=None),
    token: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    raw = _extract(authorization, token)
    if not raw:
        return None
    payload = decode_token(raw)
    if not payload:
        return None
    return await db.get(User, payload["sub"])


async def require_teacher(user: User = Depends(get_current_user)) -> User:
    if user.role not in (Role.teacher.value, Role.admin.value):
        raise HTTPException(status_code=403, detail="Teacher or admin role required")
    return user


async def user_from_token(raw: Optional[str], db: AsyncSession) -> Optional[User]:
    """Used by the WebSocket handler, which cannot use Header dependencies."""
    if not raw:
        return None
    payload = decode_token(raw)
    if not payload or not payload.get("sub"):
        return None
    return await db.get(User, payload["sub"])
