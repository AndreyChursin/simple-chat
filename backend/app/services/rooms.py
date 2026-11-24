"""Rooms: creation, settings, catalog, membership, deletion (docs/API_AND_WS_SPEC.md §5).

Every mutating function commits its own unit of work and only then publishes WebSocket events
(docs/ARCHITECTURE.md §3, "events after commit").
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import RoomSummary, UserPublic
from app.core.errors import AppError
from app.db.models import (
    ChatReadState,
    InvitationStatus,
    Message,
    Room,
    RoomBan,
    RoomInvitation,
    RoomMember,
    RoomVisibility,
    User,
)
from app.services import access, files, unread
from app.services.access import ChatRef, RoomAccess, RoomRole
from app.services.presence import get_presence
from app.ws.manager import manager

ROLE_RANK: dict[RoomRole, int] = {"owner": 0, "admin": 1, "member": 2}
PRESENCE_RANK = {"online": 0, "AFK": 1, "offline": 2}


def utcnow() -> datetime:
    return datetime.now(UTC)


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def role_of(room: Room, member: RoomMember | None) -> RoomRole | None:
    if member is None:
        return None
    if member.user_id == room.owner_id:
        return "owner"
    return "admin" if member.is_admin else "member"


def name_taken() -> AppError:
    return AppError(409, "room_name_taken", "Room name is already taken")


async def member_ids(db: AsyncSession, room_id: int) -> list[int]:
    return list(await db.scalars(select(RoomMember.user_id).where(RoomMember.room_id == room_id)))


# --- membership primitives (used by rooms and invitations; caller holds the room row lock) ----------


async def add_member(db: AsyncSession, room: Room, user_id: int, *, is_admin: bool = False) -> bool:
    """Insert membership + read state, bump member_count, settle pending invitations.
    Returns False if the user already is a member."""
    inserted = await db.scalar(
        pg_insert(RoomMember)
        .values(room_id=room.id, user_id=user_id, is_admin=is_admin, joined_at=utcnow())
        .on_conflict_do_nothing()
        .returning(RoomMember.user_id)
    )
    if inserted is None:
        return False
    # History that existed before joining is not unread.
    last_id = (
        select(func.coalesce(func.max(Message.id), 0)).where(Message.room_id == room.id).scalar_subquery()
    )
    await db.execute(
        pg_insert(ChatReadState)
        .values(user_id=user_id, room_id=room.id, last_read_message_id=last_id, updated_at=utcnow())
        .on_conflict_do_nothing(
            index_elements=["user_id", "room_id"], index_where=text("room_id IS NOT NULL")
        )
    )
    room.member_count = await _bump_member_count(db, room.id, +1)
    await db.execute(
        update(RoomInvitation)
        .where(
            RoomInvitation.room_id == room.id,
            RoomInvitation.invitee_id == user_id,
            RoomInvitation.status == InvitationStatus.pending,
        )
        .values(status=InvitationStatus.accepted, responded_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    return True


async def remove_member(db: AsyncSession, room: Room, user_id: int) -> bool:
    """Delete membership + read state, decrement member_count. Messages and files stay in the room."""
    removed = await db.scalar(
        delete(RoomMember)
        .where(RoomMember.room_id == room.id, RoomMember.user_id == user_id)
        .returning(RoomMember.user_id)
        .execution_options(synchronize_session=False)
    )
    if removed is None:
        return False
    await db.execute(
        delete(ChatReadState)
        .where(ChatReadState.room_id == room.id, ChatReadState.user_id == user_id)
        .execution_options(synchronize_session=False)
    )
    room.member_count = await _bump_member_count(db, room.id, -1)
    return True


async def _bump_member_count(db: AsyncSession, room_id: int, delta: int) -> int:
    count = await db.scalar(
        update(Room)
        .where(Room.id == room_id)
        .values(member_count=Room.member_count + delta)
        .returning(Room.member_count)
        .execution_options(synchronize_session=False)
    )
    assert count is not None
    return count


async def publish_joined(db: AsyncSession, room: Room, user: User, role: RoomRole) -> None:
    """After commit: the joiner's tabs get `room_joined`, other members get `member_joined`."""
    await manager.on_membership_changed(user.id, ("room", room.id), joined=True)
    await manager.send_to_users(
        [user.id], {"type": "room_joined", "room": RoomSummary.of(room, role).model_dump(mode="json")}
    )
    # The new read cursor (history before joining counts as read) for every tab's counters.
    for item in await unread.list_unread(db, user_id=user.id, chat=ChatRef("room", room.id)):
        await manager.send_to_users(
            [user.id],
            {
                "type": "unread_update",
                "chat": {"type": "room", "id": room.id},
                "unread": item.unread,
                "last_read_message_id": item.last_read_message_id,
            },
        )
    others = [uid for uid in await member_ids(db, room.id) if uid != user.id]
    if not others:
        return
    await manager.send_to_users(
        others,
        {
            "type": "member_joined",
            "room_id": room.id,
            "user": UserPublic.of(user).model_dump(mode="json"),
            "member_count": room.member_count,
        },
    )


# --- create / read ------------------------------------------------------------------------------------


async def create_room(
    db: AsyncSession, *, owner: User, name: str, description: str, visibility: RoomVisibility
) -> Room:
    if await db.scalar(select(Room.id).where(Room.name == name)) is not None:
        raise name_taken()
    room = Room(
        name=name,
        description=description,
        visibility=visibility,
        owner_id=owner.id,
        member_count=0,
        created_at=utcnow(),
    )
    db.add(room)
    try:
        await db.flush()
    except IntegrityError as exc:  # concurrent creation with the same name
        await db.rollback()
        if "uq_rooms_name" in str(exc.orig):
            raise name_taken() from exc
        raise
    room.owner = owner
    await add_member(db, room, owner.id, is_admin=True)
    await db.commit()
    await publish_joined(db, room, owner, "owner")
    return room


@dataclass
class CatalogEntry:
    room: Room
    role: RoomRole | None
    is_banned: bool


async def list_catalog(
    db: AsyncSession, *, user_id: int, search: str | None, cursor: int | None, limit: int
) -> tuple[list[CatalogEntry], bool]:
    """Public rooms, `member_count DESC, id`. The cursor is the id of the last room of the previous
    page; its current member_count positions the keyset."""
    query = (
        select(Room, RoomMember, RoomBan.user_id)
        .outerjoin(RoomMember, and_(RoomMember.room_id == Room.id, RoomMember.user_id == user_id))
        .outerjoin(RoomBan, and_(RoomBan.room_id == Room.id, RoomBan.user_id == user_id))
        .where(Room.visibility == RoomVisibility.public)
    )
    if search:
        # Same expression as ix_rooms_search_trgm so the GIN index can be used.
        query = query.where(
            text("(rooms.name::text || ' ' || rooms.description) ILIKE :pattern").bindparams(
                pattern=f"%{escape_like(search)}%"
            )
        )
    if cursor is not None:
        cursor_count = await db.scalar(select(Room.member_count).where(Room.id == cursor))
        if cursor_count is None:  # the cursor room was deleted meanwhile
            return [], False
        query = query.where(
            or_(
                Room.member_count < cursor_count,
                and_(Room.member_count == cursor_count, Room.id > cursor),
            )
        )
    rows = (await db.execute(query.order_by(Room.member_count.desc(), Room.id).limit(limit + 1))).all()
    entries = [
        CatalogEntry(room=room, role=role_of(room, member), is_banned=banned is not None)
        for room, member, banned in rows[:limit]
    ]
    return entries, len(rows) > limit


async def list_my_rooms(db: AsyncSession, *, user_id: int) -> list[tuple[Room, RoomRole]]:
    # Most recently active first (FRONTEND_SPEC §4.2), then by name; the newest message id per room
    # comes from ix_messages_room_id (one index probe per room).
    last_message = (
        select(Message.id)
        .where(Message.room_id == Room.id)
        .order_by(Message.id.desc())
        .limit(1)
        .correlate(Room)
        .scalar_subquery()
    )
    rows = (
        await db.execute(
            select(Room, RoomMember)
            .join(RoomMember, and_(RoomMember.room_id == Room.id, RoomMember.user_id == user_id))
            .order_by(last_message.desc().nulls_last(), Room.name)
        )
    ).all()
    result: list[tuple[Room, RoomRole]] = []
    for room, member in rows:
        role = role_of(room, member)
        assert role is not None
        result.append((room, role))
    return result


async def get_room(db: AsyncSession, *, user_id: int, room_id: int) -> tuple[RoomAccess, list[User]]:
    acc = await access.require_room_visible(db, room_id, user_id)
    admins = list(
        await db.scalars(
            select(User)
            .join(RoomMember, RoomMember.user_id == User.id)
            .where(RoomMember.room_id == room_id, RoomMember.is_admin)
            .order_by(User.id != acc.room.owner_id, User.username)
        )
    )
    return acc, admins


@dataclass
class MemberEntry:
    member: RoomMember
    role: RoomRole


async def list_members(
    db: AsyncSession, *, user_id: int, room_id: int, search: str | None, cursor: int | None, limit: int
) -> tuple[list[MemberEntry], bool]:
    """Owner, then admins, then members; inside a group online → AFK → offline, then username.
    Presence lives in memory, so ordering happens here; the cursor is the user_id of the last item."""
    acc = await access.require_room_member(db, room_id, user_id)
    query = select(RoomMember).join(User, User.id == RoomMember.user_id).where(RoomMember.room_id == room_id)
    if search:
        query = query.where(User.username.ilike(f"%{escape_like(search)}%"))
    entries: list[MemberEntry] = []
    for member in await db.scalars(query):
        role = role_of(acc.room, member)
        assert role is not None
        entries.append(MemberEntry(member=member, role=role))
    entries.sort(
        key=lambda e: (
            ROLE_RANK[e.role],
            PRESENCE_RANK[get_presence(e.member.user_id)],
            e.member.user.username.lower(),
            e.member.user_id,
        )
    )
    start = 0
    if cursor is not None:
        start = next((i + 1 for i, e in enumerate(entries) if e.member.user_id == cursor), len(entries))
    page = entries[start : start + limit]
    return page, start + limit < len(entries)


# --- update / delete ----------------------------------------------------------------------------------


async def update_room(db: AsyncSession, *, user: User, room_id: int, changes: dict[str, Any]) -> RoomAccess:
    acc = await access.require_room_owner(db, room_id, user.id, for_update=True)
    room = acc.room
    new_name = changes.get("name")
    if new_name is not None and new_name != room.name:
        clash = await db.scalar(select(Room.id).where(Room.name == new_name, Room.id != room.id))
        if clash is not None:
            raise name_taken()
        room.name = new_name
    if changes.get("description") is not None:
        room.description = changes["description"]
    if changes.get("visibility") is not None:
        room.visibility = changes["visibility"]
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if "uq_rooms_name" in str(exc.orig):
            raise name_taken() from exc
        raise
    await _publish_room_updated(db, room)
    return acc


async def _publish_room_updated(db: AsyncSession, room: Room) -> None:
    rows = (await db.execute(select(RoomMember).where(RoomMember.room_id == room.id))).scalars().all()
    by_role: dict[RoomRole, list[int]] = {}
    for member in rows:
        role = role_of(room, member)
        assert role is not None
        by_role.setdefault(role, []).append(member.user_id)
    for role, user_ids in by_role.items():
        payload = RoomSummary.of(room, role).model_dump(mode="json")
        await manager.send_to_users(user_ids, {"type": "room_updated", "room": payload})


async def delete_room(db: AsyncSession, *, user: User, room_id: int) -> None:
    """DB cascade (docs/DATA_MODEL.md §3.1) in one transaction, then the room's upload directory."""
    await access.require_room_owner(db, room_id, user.id, for_update=True)
    former_members = await member_ids(db, room_id)
    await db.execute(delete(Room).where(Room.id == room_id).execution_options(synchronize_session=False))
    await db.commit()
    db.expunge_all()
    await files.remove_room_dir(room_id)
    for uid in former_members:
        await manager.on_membership_changed(uid, ("room", room_id), joined=False)
    await manager.send_to_users(
        former_members, {"type": "room_access_revoked", "room_id": room_id, "reason": "room_deleted"}
    )


# --- join / leave -------------------------------------------------------------------------------------


async def join_room(db: AsyncSession, *, user: User, room_id: int) -> Room:
    """Public rooms only: a private room is invisible (404) to non-members, they need an invitation."""
    acc = await access.require_room_visible(db, room_id, user.id, for_update=True)
    if acc.is_member:
        raise AppError(409, "already_member", "You are already a member of this room")
    room = acc.room
    await add_member(db, room, user.id)
    await db.commit()
    await publish_joined(db, room, user, "member")
    return room


async def leave_room(db: AsyncSession, *, user: User, room_id: int) -> None:
    acc = await access.load_room_access(db, room_id, user.id, for_update=True)
    if acc is None or not acc.is_member:
        raise AppError(404, "room_not_found", "You are not a member of this room")
    if acc.is_owner:
        raise AppError(400, "owner_cannot_leave", "The owner cannot leave the room; delete it instead")
    room = acc.room
    await remove_member(db, room, user.id)
    await db.commit()
    await manager.on_membership_changed(user.id, ("room", room_id), joined=False)
    await manager.send_to_users(
        [user.id], {"type": "room_access_revoked", "room_id": room_id, "reason": "left"}
    )
    await manager.send_to_users(
        await member_ids(db, room_id),
        {
            "type": "member_removed",
            "room_id": room_id,
            "user_id": user.id,
            "reason": "left",
            "member_count": room.member_count,
        },
    )
