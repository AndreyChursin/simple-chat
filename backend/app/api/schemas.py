"""Request/response models shared by routers (docs/API_AND_WS_SPEC.md §0.3)."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints

from app.db.models import User
from app.services.presence import Presence, get_presence

USERNAME_PATTERN = r"^[A-Za-z0-9_.-]{3,32}$"

Username = Annotated[str, StringConstraints(pattern=USERNAME_PATTERN)]
Password = Annotated[str, StringConstraints(min_length=8, max_length=128)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UserPublic(BaseModel):
    id: int | None
    username: str
    display_name: str | None
    presence: Presence

    @classmethod
    def of(cls, user: User | None) -> "UserPublic":
        if user is None:
            return cls(id=None, username="Deleted user", display_name=None, presence="offline")
        return cls(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            presence=get_presence(user.id),
        )


class Me(BaseModel):
    id: int
    email: str
    username: str
    display_name: str | None
    created_at: datetime

    @classmethod
    def of(cls, user: User) -> "Me":
        return cls(
            id=user.id,
            email=user.email,
            username=user.username,
            display_name=user.display_name,
            created_at=user.created_at,
        )


class MeResponse(BaseModel):
    user: Me


class RegisterRequest(StrictModel):
    email: EmailStr = Field(max_length=254)
    username: Username
    password: Password


class LoginRequest(StrictModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)
    keep_signed_in: bool = False


class ForgotPasswordRequest(StrictModel):
    email: str = Field(max_length=254)


class ResetPasswordRequest(StrictModel):
    token: str = Field(min_length=1, max_length=128)
    new_password: Password


class ChangePasswordRequest(StrictModel):
    current_password: str = Field(max_length=128)
    new_password: Password
