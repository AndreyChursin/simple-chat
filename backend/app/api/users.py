from typing import Annotated, Any

from fastapi import APIRouter, Body, Query
from pydantic import BaseModel, ValidationError
from sqlalchemy import select

from app.api.schemas import Me, MeResponse, UserProfile, UserPublic
from app.core.deps import CurrentUser, DbSession
from app.core.errors import AppError
from app.db.models import User
from app.services import contacts as contact_service

router = APIRouter(tags=["users"])

DISPLAY_NAME_MAX = 64


class UserList(BaseModel):
    items: list[UserPublic]


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("/users")
async def search_users(
    _: CurrentUser,
    db: DbSession,
    search: str = Query(min_length=2, max_length=32),
    limit: int = Query(20, ge=1, le=50),
) -> UserList:
    rows = await db.scalars(
        select(User)
        # username is citext, so LIKE is case-insensitive
        .where(User.username.like(_escape_like(search.strip()) + "%", escape="\\"))
        .order_by(User.username)
        .limit(limit)
    )
    return UserList(items=[UserPublic.of(u) for u in rows])


@router.get("/users/{username}")
async def get_user(username: str, me: CurrentUser, db: DbSession) -> UserProfile:
    user = await db.scalar(select(User).where(User.username == username))
    if user is None:
        raise AppError(404, "user_not_found", "User not found")
    rel = await contact_service.relation(db, user_id=me.id, other_id=user.id)
    return UserProfile(
        **UserPublic.of(user).model_dump(),
        friendship=rel.friendship,
        banned_by_me=rel.banned_by_me,
        has_banned_me=rel.has_banned_me,
    )


class _DisplayNameModel(BaseModel):
    display_name: str | None = None


@router.patch("/me")
async def update_me(user: CurrentUser, db: DbSession, body: Annotated[dict[str, Any], Body()]) -> MeResponse:
    if "username" in body:
        raise AppError(400, "username_immutable", "Username cannot be changed")
    unknown = set(body) - {"display_name"}
    if unknown:
        raise AppError(
            400,
            "validation_error",
            "Only display_name can be changed",
            {
                "errors": [
                    {"loc": ["body", k], "msg": "Field cannot be changed", "type": "extra_forbidden"}
                    for k in sorted(unknown)
                ]
            },
        )
    if "display_name" in body:
        try:
            value = _DisplayNameModel.model_validate(body).display_name
        except ValidationError as exc:
            raise AppError(400, "validation_error", "display_name must be a string or null") from exc
        value = (value or "").strip() or None
        if value is not None and len(value) > DISPLAY_NAME_MAX:
            raise AppError(
                400, "validation_error", f"display_name must be at most {DISPLAY_NAME_MAX} characters"
            )
        user.display_name = value
        await db.commit()
    return MeResponse(user=Me.of(user))
