"""Request/response models shared by routers (docs/API_AND_WS_SPEC.md §0.3)."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints

from app.db.models import Message, Room, RoomInvitation, RoomVisibility, User
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


# --- messages (docs/API_AND_WS_SPEC.md §0.3, §9) --------------------------------------------------

REPLY_EXCERPT_CHARS = 200
ChatType = Literal["room", "dm"]


class ChatRefOut(BaseModel):
    type: ChatType
    id: int


class ReplyRef(BaseModel):
    id: int
    author: UserPublic
    excerpt: str
    deleted: bool


class MessageOut(BaseModel):
    id: int
    chat: ChatRefOut
    author: UserPublic
    content: str
    created_at: datetime
    edited_at: datetime | None
    deleted: bool
    reply_to: ReplyRef | None
    attachments: list[dict[str, Any]]  # Attachment objects arrive with stage 7
    client_msg_id: str | None

    @classmethod
    def of(cls, m: Message) -> "MessageOut":
        reply = None
        if m.reply_to is not None:
            r = m.reply_to
            reply = ReplyRef(
                id=r.id,
                author=UserPublic.of(r.author),
                excerpt="" if r.deleted_at else r.content[:REPLY_EXCERPT_CHARS],
                deleted=r.deleted_at is not None,
            )
        assert m.room_id is not None
        return cls(
            id=m.id,
            chat=ChatRefOut(type="room", id=m.room_id),
            author=UserPublic.of(m.author),
            content="" if m.deleted_at else m.content,
            created_at=m.created_at,
            edited_at=m.edited_at,
            deleted=m.deleted_at is not None,
            reply_to=reply,
            attachments=[],
            client_msg_id=str(m.client_msg_id) if m.client_msg_id else None,
        )


class MessagePage(BaseModel):
    items: list[MessageOut]
    has_more: bool
    next_cursor: int | None


class MessagesAround(BaseModel):
    items: list[MessageOut]
    has_more_before: bool
    has_more_after: bool


class SendMessageRequest(StrictModel):
    # No max_length here: the limit is in UTF-8 bytes and answers 413, not 400 (services/messages.py).
    content: str = ""
    reply_to_message_id: int | None = None
    client_msg_id: uuid.UUID | None = None


class EditMessageRequest(StrictModel):
    content: str
