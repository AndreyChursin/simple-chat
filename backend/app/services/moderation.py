"""Room roles and bans (docs/API_AND_WS_SPEC.md §7).

- the owner grants admin rights; the owner or any admin takes them away (never from the owner);
- removing a member is a ban: membership, read state and pending invitations go, a `room_bans` row stays
  until an admin unbans; the user's messages and files remain in the room;
- every operation locks the room row (same order as join/leave/invite accept), commits, then publishes.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.errors import AppError
from app.db.models import InvitationStatus, RoomBan, RoomInvitation, RoomMember, User
from app.services import access
from app.services.access import RoomAccess
from app.services.rooms import member_ids, remove_member, utcnow
from app.ws.manager import manager


def cannot_modify_owner() -> AppError:
    return AppError(403, "cannot_modify_owner", "The room owner cannot be demoted, removed or banned")


def not_a_member() -> AppError:
    return AppError(404, "not_found", "This user is not a member of the room")


async def _member(db: AsyncSession, room_id: int, user_id: int) -> RoomMember | None:
    member: RoomMember | None = await db.scalar(
        select(RoomMember).where(RoomMember.room_id == room_id, RoomMember.user_id == user_id)
    )
    return member


async def _publish_role(db: AsyncSession, room_id: int, user_id: int, role: str) -> None:
    await manager.send_to_users(
        await member_ids(db, room_id),
        {"type": "member_role_changed", "room_id": room_id, "user_id": user_id, "role": role},
    )


# --- admins -------------------------------------------------------------------------------------------


async def grant_admin(db: AsyncSession, *, actor: User, room_id: int, user_id: int) -> RoomMember:
    acc = await access.require_room_owner(db, room_id, actor.id, for_update=True)
    member = await _member(db, room_id, user_id)
    if member is None:
        raise not_a_member()
    if member.is_admin:
        raise AppError(409, "already_admin", "This user is already an admin")
    member.is_admin = True
    await db.commit()
    await _publish_role(db, acc.room.id, user_id, "admin")
    return member


async def revoke_admin(db: AsyncSession, *, actor: User, room_id: int, user_id: int) -> RoomMember:
    """Owner or any admin; an admin may also step down themselves."""
    acc = await access.require_room_admin(db, room_id, actor.id, for_update=True)
    if user_id == acc.room.owner_id:
        raise cannot_modify_owner()
    member = await _member(db, room_id, user_id)
    if member is None:
        raise not_a_member()
    if not member.is_admin:
        raise AppError(409, "not_admin", "This user is not an admin")
    member.is_admin = False
    await db.commit()
    await _publish_role(db, acc.room.id, user_id, "member")
    return member


# --- bans ---------------------------------------------------------------------------------------------


@dataclass
class BanEntry:
    user: User
    banned_by: User | None
    banned_at: datetime
    reason: str | None


async def ban(
    db: AsyncSession,
    *,
    actor: User,
    room_id: int,
    user_id: int,
    reason: str | None,
    member_only: bool = False,
) -> BanEntry:
    """`POST /bans` (members and non-members alike) and `DELETE /members/{id}` (`member_only`)."""
    acc = await access.require_room_admin(db, room_id, actor.id, for_update=True)
    if user_id == actor.id:
        raise AppError(400, "cannot_ban_self", "You cannot ban yourself")
    if user_id == acc.room.owner_id:
        raise cannot_modify_owner()
    target = await db.get(User, user_id)
    if target is None:
        raise AppError(404, "user_not_found", "User not found")
    if await access.is_banned_from_room(db, room_id, user_id):
        raise AppError(409, "already_banned", "This user is already banned")
    if member_only and not await access.is_room_member(db, room_id, user_id):
        raise not_a_member()
    was_member = await remove_member(db, acc.room, user_id)
    entry = RoomBan(
        room_id=room_id, user_id=user_id, banned_by_id=actor.id, banned_at=utcnow(), reason=reason or None
    )
    db.add(entry)
    await db.execute(
        update(RoomInvitation)
        .where(
            RoomInvitation.room_id == room_id,
            RoomInvitation.invitee_id == user_id,
            RoomInvitation.status == InvitationStatus.pending,
        )
        .values(status=InvitationStatus.revoked, responded_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    try:
        await db.commit()
    except IntegrityError as exc:  # pk_room_bans: banned concurrently (the room lock makes this rare)
        await db.rollback()
        raise AppError(409, "already_banned", "This user is already banned") from exc
    if was_member:
        await _publish_removed(db, acc, user_id)
    return BanEntry(user=target, banned_by=actor, banned_at=entry.banned_at, reason=entry.reason)


async def _publish_removed(db: AsyncSession, acc: RoomAccess, user_id: int) -> None:
    room_id = acc.room.id
    await manager.on_membership_changed(user_id, ("room", room_id), joined=False)
    await manager.send_to_users(
        [user_id], {"type": "room_access_revoked", "room_id": room_id, "reason": "banned"}
    )
    await manager.send_to_users(
        await member_ids(db, room_id),
        {
            "type": "member_removed",
            "room_id": room_id,
            "user_id": user_id,
            "reason": "banned",
            "member_count": acc.room.member_count,
        },
    )


async def list_bans(db: AsyncSession, *, actor: User, room_id: int) -> list[BanEntry]:
    await access.require_room_admin(db, room_id, actor.id)
    banned, by = aliased(User), aliased(User)
    rows = (
        await db.execute(
            select(RoomBan, banned, by)
            .join(banned, banned.id == RoomBan.user_id)
            .outerjoin(by, by.id == RoomBan.banned_by_id)
            .where(RoomBan.room_id == room_id)
            .order_by(RoomBan.banned_at.desc(), RoomBan.user_id)
        )
    ).all()
    return [BanEntry(user=u, banned_by=b, banned_at=row.banned_at, reason=row.reason) for row, u, b in rows]


async def unban(db: AsyncSession, *, actor: User, room_id: int, user_id: int) -> None:
    """The user is not re-added: they may join a public room again or be invited to a private one."""
    await access.require_room_admin(db, room_id, actor.id, for_update=True)
    removed = await db.scalar(
        delete(RoomBan)
        .where(RoomBan.room_id == room_id, RoomBan.user_id == user_id)
        .returning(RoomBan.user_id)
        .execution_options(synchronize_session=False)
    )
    if removed is None:
        raise AppError(404, "not_found", "This user is not banned")
    await db.commit()
