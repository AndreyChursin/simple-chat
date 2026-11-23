"""Room roles and bans (docs/API_AND_WS_SPEC.md §7). Rights are checked in app/services/access.py."""

from fastapi import APIRouter, Response, status

from app.api.schemas import BanRequest, RoomBanOut, RoomBans, RoomMemberOut, UserPublic
from app.core.deps import CurrentUser, DbSession
from app.db.models import RoomMember
from app.services import moderation
from app.services.moderation import BanEntry

router = APIRouter(tags=["moderation"])


def _member_out(member: RoomMember) -> RoomMemberOut:
    role = "admin" if member.is_admin else "member"
    return RoomMemberOut(**UserPublic.of(member.user).model_dump(), role=role, joined_at=member.joined_at)


def _ban_out(entry: BanEntry) -> RoomBanOut:
    return RoomBanOut(
        user=UserPublic.of(entry.user),
        banned_by=UserPublic.of(entry.banned_by),
        banned_at=entry.banned_at,
        reason=entry.reason,
    )


@router.post("/rooms/{room_id}/admins/{user_id}")
async def grant_admin(room_id: int, user_id: int, user: CurrentUser, db: DbSession) -> RoomMemberOut:
    member = await moderation.grant_admin(db, actor=user, room_id=room_id, user_id=user_id)
    return _member_out(member)


@router.delete("/rooms/{room_id}/admins/{user_id}")
async def revoke_admin(room_id: int, user_id: int, user: CurrentUser, db: DbSession) -> RoomMemberOut:
    member = await moderation.revoke_admin(db, actor=user, room_id=room_id, user_id=user_id)
    return _member_out(member)


@router.delete("/rooms/{room_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(room_id: int, user_id: int, user: CurrentUser, db: DbSession) -> Response:
    await moderation.ban(db, actor=user, room_id=room_id, user_id=user_id, reason=None, member_only=True)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/rooms/{room_id}/bans", status_code=status.HTTP_201_CREATED)
async def ban(room_id: int, body: BanRequest, user: CurrentUser, db: DbSession) -> RoomBanOut:
    entry = await moderation.ban(db, actor=user, room_id=room_id, user_id=body.user_id, reason=body.reason)
    return _ban_out(entry)


@router.get("/rooms/{room_id}/bans")
async def bans(room_id: int, user: CurrentUser, db: DbSession) -> RoomBans:
    return RoomBans(items=[_ban_out(e) for e in await moderation.list_bans(db, actor=user, room_id=room_id)])


@router.delete("/rooms/{room_id}/bans/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unban(room_id: int, user_id: int, user: CurrentUser, db: DbSession) -> Response:
    await moderation.unban(db, actor=user, room_id=room_id, user_id=user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
