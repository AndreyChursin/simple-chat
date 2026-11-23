"""The single place for permission checks (CLAUDE.md §4 "Безопасность"). Routers never check rights
themselves; services call these helpers.

Room rules (docs/API_AND_WS_SPEC.md §5–7):
- a private room is invisible (404) to everyone who is not a member — existence is not revealed;
- a public room is visible to everyone except users banned from it (403 room_banned);
- owner ⊂ admins ⊂ members.
"""

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.models import Room, RoomBan, RoomMember, RoomVisibility

RoomRole = Literal["owner", "admin", "member"]


@dataclass
class RoomAccess:
    room: Room
    member: RoomMember | None
    banned: bool
    user_id: int

    @property
    def role(self) -> RoomRole | None:
        if self.member is None:
            return None
        if self.room.owner_id == self.user_id:
            return "owner"
        return "admin" if self.member.is_admin else "member"

    @property
    def is_member(self) -> bool:
        return self.member is not None

    @property
    def is_admin(self) -> bool:
        return self.member is not None and self.member.is_admin

    @property
    def is_owner(self) -> bool:
        return self.member is not None and self.room.owner_id == self.user_id


def room_not_found() -> AppError:
    return AppError(404, "room_not_found", "Room not found")


def room_banned() -> AppError:
    return AppError(403, "room_banned", "You are banned from this room")


async def load_room_access(
    db: AsyncSession, room_id: int, user_id: int, *, for_update: bool = False
) -> RoomAccess | None:
    """Room + the user's membership and ban in one query. `for_update` locks the room row, which
    serialises membership changes of one room (member_count, owner invariant)."""
    query = (
        select(Room, RoomMember, RoomBan.user_id)
        .outerjoin(RoomMember, and_(RoomMember.room_id == Room.id, RoomMember.user_id == user_id))
        .outerjoin(RoomBan, and_(RoomBan.room_id == Room.id, RoomBan.user_id == user_id))
        .where(Room.id == room_id)
    )
    if for_update:
        query = query.with_for_update(of=Room)
    row = (await db.execute(query)).first()
    if row is None:
        return None
    room, member, banned_user_id = row
    return RoomAccess(room=room, member=member, banned=banned_user_id is not None, user_id=user_id)


async def require_room_visible(
    db: AsyncSession, room_id: int, user_id: int, *, for_update: bool = False
) -> RoomAccess:
    access = await load_room_access(db, room_id, user_id, for_update=for_update)
    if access is None:
        raise room_not_found()
    if access.is_member:
        return access
    if access.room.visibility == RoomVisibility.private:
        raise room_not_found()
    if access.banned:
        raise room_banned()
    return access


async def require_room_member(
    db: AsyncSession, room_id: int, user_id: int, *, for_update: bool = False
) -> RoomAccess:
    access = await require_room_visible(db, room_id, user_id, for_update=for_update)
    if not access.is_member:
        raise AppError(403, "forbidden", "You are not a member of this room")
    return access


async def require_room_admin(
    db: AsyncSession, room_id: int, user_id: int, *, for_update: bool = False
) -> RoomAccess:
    access = await require_room_visible(db, room_id, user_id, for_update=for_update)
    if not access.is_admin:
        raise AppError(403, "not_room_admin", "Only room admins can do this")
    return access


async def require_room_owner(
    db: AsyncSession, room_id: int, user_id: int, *, for_update: bool = False
) -> RoomAccess:
    access = await require_room_visible(db, room_id, user_id, for_update=for_update)
    if not access.is_owner:
        raise AppError(403, "not_room_owner", "Only the room owner can do this")
    return access


async def is_banned_from_room(db: AsyncSession, room_id: int, user_id: int) -> bool:
    found = await db.scalar(
        select(RoomBan.user_id).where(RoomBan.room_id == room_id, RoomBan.user_id == user_id)
    )
    return found is not None


async def is_room_member(db: AsyncSession, room_id: int, user_id: int) -> bool:
    found = await db.scalar(
        select(RoomMember.user_id).where(RoomMember.room_id == room_id, RoomMember.user_id == user_id)
    )
    return found is not None


# --- chats (room or DM) -----------------------------------------------------------------------------

ChatType = Literal["room", "dm"]


@dataclass(frozen=True)
class ChatRef:
    type: ChatType
    id: int

    @property
    def key(self) -> tuple[ChatType, int]:
        return (self.type, self.id)


@dataclass
class ChatAccess:
    chat: ChatRef
    user_id: int
    # Room admins/owner may delete any message of the room; nobody moderates a DM.
    can_moderate: bool


async def require_chat_member(db: AsyncSession, chat: ChatRef, user_id: int) -> ChatAccess:
    """Read/write access to a chat's messages: a current room member (404 for a private room or a
    missing one, 403 for a public room the user is not in). DMs arrive with stage 6."""
    if chat.type == "room":
        acc = await require_room_member(db, chat.id, user_id)
        return ChatAccess(chat=chat, user_id=user_id, can_moderate=acc.is_admin)
    raise AppError(404, "not_found", "Chat not found")
