"""Authentication, profile, theme preference, device registry."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.security import create_access_token, hash_password, verify_password
from app.deps import get_current_user
from app.models import Device, Role, User, utcnow
from app.schemas import DeviceIn, LoginIn, RegisterIn, ThemeIn, TokenOut, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _token_for(user: User) -> TokenOut:
    token = create_access_token(
        user.id, {"role": user.role, "email": user.email, "roll": user.roll_number}
    )
    return TokenOut(
        token=token,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=UserOut.model_validate(user),
    )


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterIn, db: AsyncSession = Depends(get_db)):
    email = payload.email.lower().strip()
    if await db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status_code=409, detail="An account with that email already exists")

    roll = (payload.roll_number or "").strip().upper() or None
    if payload.role == Role.student.value and not roll:
        raise HTTPException(
            status_code=422,
            detail="Students must register with a roll number — it is the identity "
                   "used to admit them into a class.",
        )
    if roll and await db.scalar(select(User).where(User.roll_number == roll)):
        raise HTTPException(status_code=409, detail=f"Roll number {roll} is already registered")

    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name.strip(),
        role=payload.role,
        roll_number=roll,
        department=payload.department,
        section=payload.section,
        year=payload.year,
        last_login_at=utcnow(),
    )
    db.add(user)
    await db.flush()
    return _token_for(user)


@router.post("/login", response_model=TokenOut)
async def login(payload: LoginIn, request: Request, db: AsyncSession = Depends(get_db)):
    user = await db.scalar(select(User).where(User.email == payload.email.lower().strip()))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="This account has been deactivated")
    user.last_login_at = utcnow()
    return _token_for(user)


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return UserOut.model_validate(user)


@router.patch("/theme", response_model=UserOut)
async def set_theme(payload: ThemeIn, user: User = Depends(get_current_user),
                    db: AsyncSession = Depends(get_db)):
    """Theme follows the account, so dark/light stays consistent across phone,
    tablet and laptop."""
    user.theme = payload.theme
    db.add(user)
    return UserOut.model_validate(user)


@router.post("/devices")
async def register_device(payload: DeviceIn, user: User = Depends(get_current_user),
                          db: AsyncSession = Depends(get_db)):
    existing = await db.scalar(
        select(Device).where(Device.user_id == user.id, Device.user_agent == payload.user_agent)
    )
    if existing:
        existing.last_seen_at = utcnow()
        existing.network = payload.network or existing.network
        device = existing
    else:
        device = Device(user_id=user.id, **payload.model_dump())
        db.add(device)
    await db.flush()
    return {"success": True, "device_id": device.id, "kind": device.kind}


@router.get("/devices")
async def list_devices(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(
        select(Device).where(Device.user_id == user.id).order_by(Device.last_seen_at.desc())
    )).all()
    return {
        "success": True,
        "devices": [
            {"id": d.id, "kind": d.kind, "label": d.label, "network": d.network,
             "screen": d.screen, "last_seen_at": d.last_seen_at}
            for d in rows
        ],
    }
