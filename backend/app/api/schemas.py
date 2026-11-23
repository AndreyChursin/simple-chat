"""Request/response models shared by routers (docs/API_AND_WS_SPEC.md §0.3)."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints

from app.db.models import Room, RoomInvitation, RoomVisibility, User
from app.services.access import RoomRole
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


# --- rooms (docs/API_AND_WS_SPEC.md §5–6) ---------------------------------------------------------

RoomName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=3, max_length=64, pattern=r"^[^\x00-\x1f\x7f]+$"),
]
RoomDescription = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]


class RoomSummary(BaseModel):
    id: int
    name: str
    description: str
    visibility: RoomVisibility
    member_count: int
    owner: UserPublic
    my_role: RoomRole | None
    unread: int = 0
    created_at: datetime

    @classmethod
    def of(cls, room: Room, my_role: RoomRole | None, unread: int = 0) -> "RoomSummary":
        return cls(
            id=room.id,
            name=room.name,
            description=room.description,
            visibility=room.visibility,
            member_count=room.member_count,
            owner=UserPublic.of(room.owner),
            my_role=my_role,
            unread=unread,
            created_at=room.created_at,
        )


class CatalogRoom(RoomSummary):
    is_banned: bool


class RoomDetail(RoomSummary):
    admins: list[UserPublic]


class CatalogPage(BaseModel):
    items: list[CatalogRoom]
    has_more: bool
    next_cursor: int | None


class MyRooms(BaseModel):
    public: list[RoomSummary]
    private: list[RoomSummary]


class RoomMemberOut(UserPublic):
    role: RoomRole
    joined_at: datetime


class MemberPage(BaseModel):
    items: list[RoomMemberOut]
    has_more: bool
    next_cursor: int | None


class CreateRoomRequest(StrictModel):
    name: RoomName
    description: RoomDescription = ""
    visibility: RoomVisibility


class UpdateRoomRequest(StrictModel):
    name: RoomName | None = None
    description: RoomDescription | None = None
    visibility: RoomVisibility | None = None


class InviteRequest(StrictModel):
    username: str = Field(min_length=1, max_length=32)


class RoomBrief(BaseModel):
    id: int
    name: str
    description: str
    visibility: RoomVisibility
    member_count: int

    @classmethod
    def of(cls, room: Room) -> "RoomBrief":
        return cls(
            id=room.id,
            name=room.name,
            description=room.description,
            visibility=room.visibility,
            member_count=room.member_count,
        )


class InvitationOut(BaseModel):
    id: int
    room: RoomBrief
    invitee: UserPublic
    inviter: UserPublic
    created_at: datetime

    @classmethod
    def of(cls, inv: RoomInvitation) -> "InvitationOut":
        return cls(
            id=inv.id,
            room=RoomBrief.of(inv.room),
            invitee=UserPublic.of(inv.invitee),
            inviter=UserPublic.of(inv.inviter),
            created_at=inv.created_at,
        )


class InvitationResponse(BaseModel):
    invitation: InvitationOut


class MyInvitation(BaseModel):
    id: int
    room: RoomBrief
    inviter: UserPublic
    created_at: datetime


class MyInvitations(BaseModel):
    items: list[MyInvitation]


class RoomInvitationItem(BaseModel):
    id: int
    invitee: UserPublic
    inviter: UserPublic
    created_at: datetime


class RoomInvitations(BaseModel):
    items: list[RoomInvitationItem]
