"""Room invitations (docs/API_AND_WS_SPEC.md §6). Any member may invite; admins see and revoke pending
invitations; accepting is the only way into a private room."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import UserPublic
from app.core.errors import AppError
from app.db.models import InvitationStatus, Room, RoomInvitation, User
from app.services import access
from app.services.access import RoomRole
from app.services.rooms import add_member, publish_joined, role_of, utcnow
from app.ws.manager import manager


def invitation_not_found() -> AppError:
    return AppError(404, "not_found", "Invitation not found")


async def invite(db: AsyncSession, *, inviter: User, room_id: int, username: str) -> RoomInvitation:
    acc = await access.require_room_member(db, room_id, inviter.id)
    invitee = await db.scalar(select(User).where(User.username == username))
    if invitee is None:
        raise AppError(404, "user_not_found", "User not found")
    if await access.is_room_member(db, room_id, invitee.id):
        raise AppError(409, "already_member", "This user is already a member of the room")
    if await access.is_banned_from_room(db, room_id, invitee.id):
        raise AppError(409, "user_is_banned_in_room", "This user is banned from the room; unban them first")
    if await access.is_user_banned_between(db, inviter.id, invitee.id):
        raise access.user_banned()
    pending = await db.scalar(
        select(RoomInvitation.id).where(
            RoomInvitation.room_id == room_id,
            RoomInvitation.invitee_id == invitee.id,
            RoomInvitation.status == InvitationStatus.pending,
        )
    )
    if pending is not None:
        raise AppError(409, "invitation_exists", "This user already has a pending invitation")
    invitation = RoomInvitation(
        room_id=room_id, invitee_id=invitee.id, inviter_id=inviter.id, created_at=utcnow()
    )
    db.add(invitation)
    try:
        await db.commit()
    except IntegrityError as exc:  # concurrent invitation of the same user (ux_room_invitations_pending)
        await db.rollback()
        raise AppError(409, "invitation_exists", "This user already has a pending invitation") from exc
    invitation.room = acc.room
    invitation.invitee = invitee
    invitation.inviter = inviter
    await manager.send_to_users(
        [invitee.id],
        {
            "type": "invitation_received",
            "invitation": {
                "id": invitation.id,
                "room": {"id": acc.room.id, "name": acc.room.name},
                "inviter": UserPublic.of(inviter).model_dump(mode="json"),
            },
        },
    )
    return invitation


async def list_my_invitations(db: AsyncSession, *, user_id: int) -> list[RoomInvitation]:
    return list(
        await db.scalars(
            select(RoomInvitation)
            .where(RoomInvitation.invitee_id == user_id, RoomInvitation.status == InvitationStatus.pending)
            .order_by(RoomInvitation.created_at.desc(), RoomInvitation.id.desc())
        )
    )


async def _my_invitation(db: AsyncSession, invitation_id: int, user_id: int) -> RoomInvitation:
    invitation = await db.scalar(
        select(RoomInvitation).where(RoomInvitation.id == invitation_id, RoomInvitation.invitee_id == user_id)
    )
    if invitation is None:
        raise invitation_not_found()
    return invitation


async def accept(db: AsyncSession, *, user: User, invitation_id: int) -> tuple[Room, RoomRole]:
    invitation = await _my_invitation(db, invitation_id, user.id)
    # Lock the room first (same order as join/leave), then re-read the invitation status under it.
    acc = await access.load_room_access(db, invitation.room_id, user.id, for_update=True)
    assert acc is not None  # FK cascade: the invitation would be gone with the room
    await db.refresh(invitation, attribute_names=["status"])
    # A ban revokes the user's invitations to the room; tell them why rather than "not pending".
    if acc.banned:
        raise access.room_banned()
    if invitation.status != InvitationStatus.pending:
        raise AppError(409, "invitation_not_pending", "This invitation is no longer pending")
    room = acc.room
    if acc.is_member:  # joined the public room meanwhile; just settle the invitation
        invitation.status = InvitationStatus.accepted
        invitation.responded_at = utcnow()
        await db.commit()
        role = role_of(room, acc.member)
        assert role is not None
        return room, role
    await add_member(db, room, user.id)  # also marks this invitation accepted
    await db.commit()
    await publish_joined(db, room, user, "member")
    return room, "member"


async def decline(db: AsyncSession, *, user: User, invitation_id: int) -> None:
    invitation = await _my_invitation(db, invitation_id, user.id)
    if invitation.status != InvitationStatus.pending:
        raise invitation_not_found()
    invitation.status = InvitationStatus.declined
    invitation.responded_at = utcnow()
    await db.commit()


async def list_room_invitations(db: AsyncSession, *, user: User, room_id: int) -> list[RoomInvitation]:
    await access.require_room_admin(db, room_id, user.id)
    return list(
        await db.scalars(
            select(RoomInvitation)
            .where(RoomInvitation.room_id == room_id, RoomInvitation.status == InvitationStatus.pending)
            .order_by(RoomInvitation.created_at.desc(), RoomInvitation.id.desc())
        )
    )


async def revoke(db: AsyncSession, *, user: User, room_id: int, invitation_id: int) -> None:
    await access.require_room_admin(db, room_id, user.id)
    invitation = await db.scalar(
        select(RoomInvitation).where(
            RoomInvitation.id == invitation_id,
            RoomInvitation.room_id == room_id,
            RoomInvitation.status == InvitationStatus.pending,
        )
    )
    if invitation is None:
        raise invitation_not_found()
    invitation.status = InvitationStatus.revoked
    invitation.responded_at = utcnow()
    await db.commit()
