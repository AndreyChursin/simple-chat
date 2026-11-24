"""The single place for permission checks (CLAUDE.md §4 "Безопасность"). Routers never check rights
themselves; services call these helpers.

Room rules (docs/API_AND_WS_SPEC.md §5–7):
- a private room is invisible (404) to everyone who is not a member — existence is not revealed;
- a public room is visible to everyone except users banned from it (403 room_banned);
- owner ⊂ admins ⊂ members.
"""

from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.models import (
    Attachment,
    DirectConversation,
    Friendship,
    FriendshipStatus,
    Message,
    Room,
    RoomBan,
    RoomMember,
    RoomVisibility,
    UserBan,
    UserSession,
)

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


# --- contacts ---------------------------------------------------------------------------------------


async def is_user_banned_between(db: AsyncSession, a: int, b: int) -> bool:
    """A personal ban in either direction blocks the pair (docs/API_AND_WS_SPEC.md §4)."""
    found = await db.scalar(
        select(UserBan.banner_id).where(
            or_(
                and_(UserBan.banner_id == a, UserBan.banned_id == b),
                and_(UserBan.banner_id == b, UserBan.banned_id == a),
            )
        )
    )
    return found is not None


def pair_filter(a: int, b: int) -> Any:
    return or_(
        and_(Friendship.requester_id == a, Friendship.addressee_id == b),
        and_(Friendship.requester_id == b, Friendship.addressee_id == a),
    )


async def are_friends(db: AsyncSession, a: int, b: int) -> bool:
    found = await db.scalar(
        select(Friendship.id).where(pair_filter(a, b), Friendship.status == FriendshipStatus.accepted)
    )
    return found is not None


def user_banned() -> AppError:
    return AppError(403, "user_banned", "One of you has blocked the other")


# --- chats (room or DM) -----------------------------------------------------------------------------

ChatType = Literal["room", "dm"]
FrozenReason = Literal["not_friends", "user_banned", "user_deleted"]


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
    # DMs: why nobody can write (None = writable). Rooms are always writable for members.
    frozen_reason: FrozenReason | None = None
    dm: DirectConversation | None = None


async def dm_frozen_reason(db: AsyncSession, dm: DirectConversation) -> FrozenReason | None:
    """Writable = both sides exist, are friends and neither banned the other (computed, not stored)."""
    if dm.user_a_id is None or dm.user_b_id is None:
        return "user_deleted"
    if await is_user_banned_between(db, dm.user_a_id, dm.user_b_id):
        return "user_banned"
    if not await are_friends(db, dm.user_a_id, dm.user_b_id):
        return "not_friends"
    return None


async def require_dm_side(db: AsyncSession, dm_id: int, user_id: int) -> DirectConversation:
    dm = await db.get(DirectConversation, dm_id)
    if dm is None or not dm.has_side(user_id):
        raise AppError(404, "not_found", "Conversation not found")
    return dm


async def require_chat_member(db: AsyncSession, chat: ChatRef, user_id: int) -> ChatAccess:
    """Read access to a chat's messages: a current room member (404 for a private room or a missing
    one, 403 for a public room the user is not in) or a side of the DM (404 otherwise). A frozen DM
    stays readable for both sides."""
    if chat.type == "room":
        acc = await require_room_member(db, chat.id, user_id)
        return ChatAccess(chat=chat, user_id=user_id, can_moderate=acc.is_admin)
    dm = await require_dm_side(db, chat.id, user_id)
    return ChatAccess(
        chat=chat, user_id=user_id, can_moderate=False, frozen_reason=await dm_frozen_reason(db, dm), dm=dm
    )


async def require_chat_writable(db: AsyncSession, chat: ChatRef, user_id: int) -> ChatAccess:
    """Sending, editing or deleting: a frozen DM is read-only for both sides (403 dm_frozen)."""
    acc = await require_chat_member(db, chat, user_id)
    if acc.frozen_reason is not None:
        raise AppError(403, "dm_frozen", "This conversation is read-only", {"reason": acc.frozen_reason})
    return acc


async def require_attachment_readable(db: AsyncSession, attachment: Attachment, user_id: int) -> None:
    """Checked on every download, against the state *now*: a pending file only for its uploader; a sent
    one for current room members / either side of the DM (frozen DMs stay readable). 404 otherwise,
    so that existence is not revealed."""
    not_found = AppError(404, "attachment_not_found", "Attachment not found")
    if attachment.message_id is None and attachment.uploader_id != user_id:
        raise not_found
    if attachment.room_id is not None:
        if not await is_room_member(db, attachment.room_id, user_id):
            raise not_found
        return
    dm = await db.get(DirectConversation, attachment.dm_id)
    if dm is None or not dm.has_side(user_id):
        raise not_found


# --- messages, attachments, sessions ----------------------------------------------------------------


def require_message_author(message: Message, user_id: int) -> None:
    """Editing: the author only (in rooms and DMs alike)."""
    if message.author_id != user_id:
        raise AppError(403, "not_message_author", "Only the author can edit a message")


def require_message_deletable(message: Message, chat: ChatAccess) -> None:
    """Deleting: the author, or an admin/owner of the room (nobody moderates a DM)."""
    if message.author_id != chat.user_id and not chat.can_moderate:
        raise AppError(403, "forbidden", "You can only delete your own messages")


def require_attachment_uploader(attachment: Attachment, user_id: int) -> None:
    if attachment.uploader_id != user_id:
        raise AppError(403, "forbidden", "Only the uploader can change this attachment")


async def require_own_session(db: AsyncSession, session_id: int, user_id: int) -> None:
    """Sessions of other users are invisible (404)."""
    found = await db.scalar(
        select(UserSession.id).where(UserSession.id == session_id, UserSession.user_id == user_id)
    )
    if found is None:
        raise AppError(404, "not_found", "Session not found")
