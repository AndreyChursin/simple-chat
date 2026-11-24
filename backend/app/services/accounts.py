"""Account deletion (docs/DATA_MODEL.md §3.2): one transaction in the DB, files and WS events after commit.

Deleted: the user, their sessions, owned rooms (with messages, attachments, files), memberships, bans,
invitations to them, friendships, personal bans, read cursors, pending (unsent) uploads.
Kept: their messages and files in other rooms and in DMs (author → "Deleted user"); DMs stay with the
peer, frozen. A DM whose both sides are gone is removed with its files.
"""

from dataclasses import dataclass, field

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.passwords import verify_password
from app.db.models import (
    Attachment,
    DirectConversation,
    Friendship,
    FriendshipStatus,
    Room,
    RoomMember,
    User,
    UserSession,
)
from app.services import files
from app.services.rooms import member_ids, remove_member
from app.ws.manager import manager


@dataclass
class _Plan:
    session_ids: list[int] = field(default_factory=list)
    owned: dict[int, list[int]] = field(default_factory=dict)  # room id → other members
    left: dict[int, tuple[list[int], int]] = field(default_factory=dict)  # room id → (members, count)
    friends: list[int] = field(default_factory=list)
    requests: list[tuple[int, int]] = field(default_factory=list)  # (request id, other user)
    dms: list[tuple[int, int]] = field(default_factory=list)  # (dm id, peer id) with a living peer
    dead_dms: list[int] = field(default_factory=list)  # nobody left on either side
    pending_files: list[str] = field(default_factory=list)


async def delete_account(db: AsyncSession, *, user: User, password: str) -> None:
    if not await verify_password(user.password_hash, password):
        raise AppError(401, "invalid_current_password", "Password is incorrect")
    uid = user.id
    plan = _Plan(session_ids=list(await db.scalars(select(UserSession.id).where(UserSession.user_id == uid))))

    # Rooms the user is in, locked in id order (the same lock join/leave/ban take).
    rooms = list(
        await db.scalars(
            select(Room)
            .join(RoomMember, RoomMember.room_id == Room.id)
            .where(RoomMember.user_id == uid)
            .order_by(Room.id)
            .with_for_update(of=Room)
        )
    )
    for room in rooms:
        if room.owner_id == uid:
            plan.owned[room.id] = [m for m in await member_ids(db, room.id) if m != uid]
        else:
            await remove_member(db, room, uid)  # keeps member_count right; CASCADE would not
            plan.left[room.id] = (await member_ids(db, room.id), room.member_count)

    for f in await db.scalars(
        select(Friendship).where(or_(Friendship.requester_id == uid, Friendship.addressee_id == uid))
    ):
        other = f.addressee_id if f.requester_id == uid else f.requester_id
        if f.status == FriendshipStatus.accepted:
            plan.friends.append(other)
        else:
            plan.requests.append((f.id, other))

    for dm in await db.scalars(
        select(DirectConversation).where(
            or_(DirectConversation.user_a_id == uid, DirectConversation.user_b_id == uid)
        )
    ):
        peer = dm.user_b_id if dm.user_a_id == uid else dm.user_a_id
        if peer is None:
            plan.dead_dms.append(dm.id)
        else:
            plan.dms.append((dm.id, peer))

    plan.pending_files = list(
        await db.scalars(
            delete(Attachment)
            .where(Attachment.uploader_id == uid, Attachment.message_id.is_(None))
            .returning(Attachment.stored_path)
        )
    )
    # Owned rooms go with the user (rooms.owner_id ON DELETE CASCADE), the rest follows the FKs.
    await db.execute(delete(User).where(User.id == uid).execution_options(synchronize_session=False))
    if plan.dead_dms:
        await db.execute(
            delete(DirectConversation)
            .where(DirectConversation.id.in_(plan.dead_dms))
            .execution_options(synchronize_session=False)
        )
    await db.commit()
    db.expunge_all()
    await _after_commit(plan, uid)


async def _after_commit(plan: _Plan, uid: int) -> None:
    # Every tab of the user: offline for their audience (still computed from their chats), then
    # `session_revoked` + close 4001.
    await manager.forget_user(uid, plan.session_ids)
    await files.remove_files(plan.pending_files)
    for room_id in plan.owned:
        await files.remove_room_dir(room_id)
    for dm_id in plan.dead_dms:
        await files.remove_dm_dir(dm_id)

    for room_id, others in plan.owned.items():
        for other in others:
            await manager.on_membership_changed(other, ("room", room_id), joined=False)
        await manager.send_to_users(
            others, {"type": "room_access_revoked", "room_id": room_id, "reason": "room_deleted"}
        )
    for room_id, (members, count) in plan.left.items():
        await manager.send_to_users(
            members,
            {
                "type": "member_removed",
                "room_id": room_id,
                "user_id": uid,
                "reason": "account_deleted",
                "member_count": count,
            },
        )
    for request_id, other in plan.requests:
        await manager.send_to_users(
            [other], {"type": "friend_request_resolved", "request_id": request_id, "result": "cancelled"}
        )
    for friend in plan.friends:
        await manager.send_to_users([friend], {"type": "friend_removed", "user_id": uid})
    for dm_id, peer in plan.dms:
        await manager.on_membership_changed(peer, ("dm", dm_id), joined=False)
        await manager.send_to_users([peer], {"type": "dm_frozen", "dm_id": dm_id, "reason": "user_deleted"})
