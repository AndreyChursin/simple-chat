from fastapi import APIRouter, Query, Response, status

from app.api.schemas import (
    CatalogPage,
    CatalogRoom,
    CreateRoomRequest,
    InvitationOut,
    InvitationResponse,
    InviteRequest,
    MemberPage,
    MyRooms,
    RoomDetail,
    RoomInvitationItem,
    RoomInvitations,
    RoomMemberOut,
    RoomSummary,
    UpdateRoomRequest,
    UserPublic,
)
from app.core.deps import CurrentUser, DbSession
from app.db.models import RoomVisibility
from app.services import invitations as invitation_service
from app.services import rooms as room_service
from app.services import unread as unread_service
from app.services.access import ChatRef

router = APIRouter(tags=["rooms"])


@router.get("/rooms/catalog")
async def catalog(
    user: CurrentUser,
    db: DbSession,
    search: str | None = Query(None, max_length=100),
    cursor: int | None = Query(None, ge=1),
    limit: int = Query(30, ge=1, le=100),
) -> CatalogPage:
    entries, has_more = await room_service.list_catalog(
        db, user_id=user.id, search=(search or "").strip() or None, cursor=cursor, limit=limit
    )
    items = [
        CatalogRoom(**RoomSummary.of(e.room, e.role).model_dump(), is_banned=e.is_banned) for e in entries
    ]
    return CatalogPage(items=items, has_more=has_more, next_cursor=items[-1].id if has_more else None)


@router.get("/rooms/mine")
async def my_rooms(user: CurrentUser, db: DbSession) -> MyRooms:
    result = MyRooms(public=[], private=[])
    unread = await unread_service.unread_map(db, user_id=user.id)
    for room, role in await room_service.list_my_rooms(db, user_id=user.id):
        target = result.public if room.visibility == RoomVisibility.public else result.private
        target.append(RoomSummary.of(room, role, unread.get(("room", room.id), 0)))
    return result


@router.post("/rooms", status_code=status.HTTP_201_CREATED)
async def create_room(body: CreateRoomRequest, user: CurrentUser, db: DbSession) -> RoomSummary:
    room = await room_service.create_room(
        db, owner=user, name=body.name, description=body.description, visibility=body.visibility
    )
    return RoomSummary.of(room, "owner")


@router.get("/rooms/{room_id}")
async def get_room(room_id: int, user: CurrentUser, db: DbSession) -> RoomDetail:
    acc, admins = await room_service.get_room(db, user_id=user.id, room_id=room_id)
    unread = 0
    if acc.is_member:
        items = await unread_service.list_unread(db, user_id=user.id, chat=ChatRef("room", room_id))
        unread = items[0].unread if items else 0
    return RoomDetail(
        **RoomSummary.of(acc.room, acc.role, unread).model_dump(), admins=[UserPublic.of(a) for a in admins]
    )


@router.patch("/rooms/{room_id}")
async def update_room(room_id: int, body: UpdateRoomRequest, user: CurrentUser, db: DbSession) -> RoomSummary:
    acc = await room_service.update_room(
        db, user=user, room_id=room_id, changes=body.model_dump(exclude_unset=True)
    )
    return RoomSummary.of(acc.room, acc.role)


@router.delete("/rooms/{room_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_room(room_id: int, user: CurrentUser, db: DbSession) -> Response:
    await room_service.delete_room(db, user=user, room_id=room_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/rooms/{room_id}/join")
async def join_room(room_id: int, user: CurrentUser, db: DbSession) -> RoomSummary:
    room = await room_service.join_room(db, user=user, room_id=room_id)
    return RoomSummary.of(room, "member")


@router.post("/rooms/{room_id}/leave", status_code=status.HTTP_204_NO_CONTENT)
async def leave_room(room_id: int, user: CurrentUser, db: DbSession) -> Response:
    await room_service.leave_room(db, user=user, room_id=room_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/rooms/{room_id}/members")
async def members(
    room_id: int,
    user: CurrentUser,
    db: DbSession,
    search: str | None = Query(None, max_length=32),
    cursor: int | None = Query(None, ge=1),
    limit: int = Query(200, ge=1, le=1000),
) -> MemberPage:
    entries, has_more = await room_service.list_members(
        db,
        user_id=user.id,
        room_id=room_id,
        search=(search or "").strip() or None,
        cursor=cursor,
        limit=limit,
    )
    items = [
        RoomMemberOut(**UserPublic.of(e.member.user).model_dump(), role=e.role, joined_at=e.member.joined_at)
        for e in entries
    ]
    return MemberPage(
        items=items, has_more=has_more, next_cursor=entries[-1].member.user_id if has_more else None
    )


# --- invitations of a room ---------------------------------------------------------------------------


@router.post("/rooms/{room_id}/invitations", status_code=status.HTTP_201_CREATED)
async def invite(room_id: int, body: InviteRequest, user: CurrentUser, db: DbSession) -> InvitationResponse:
    invitation = await invitation_service.invite(
        db, inviter=user, room_id=room_id, username=body.username.strip()
    )
    return InvitationResponse(invitation=InvitationOut.of(invitation))


@router.get("/rooms/{room_id}/invitations")
async def room_invitations(room_id: int, user: CurrentUser, db: DbSession) -> RoomInvitations:
    rows = await invitation_service.list_room_invitations(db, user=user, room_id=room_id)
    return RoomInvitations(
        items=[
            RoomInvitationItem(
                id=inv.id,
                invitee=UserPublic.of(inv.invitee),
                inviter=UserPublic.of(inv.inviter),
                created_at=inv.created_at,
            )
            for inv in rows
        ]
    )


@router.delete("/rooms/{room_id}/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invitation(room_id: int, invitation_id: int, user: CurrentUser, db: DbSession) -> Response:
    await invitation_service.revoke(db, user=user, room_id=room_id, invitation_id=invitation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
