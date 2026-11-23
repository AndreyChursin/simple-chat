from fastapi import APIRouter, Response, status

from app.api.schemas import MyInvitation, MyInvitations, RoomBrief, RoomSummary, UserPublic
from app.core.deps import CurrentUser, DbSession
from app.services import invitations as invitation_service

router = APIRouter(tags=["invitations"])


@router.get("/invitations")
async def my_invitations(user: CurrentUser, db: DbSession) -> MyInvitations:
    rows = await invitation_service.list_my_invitations(db, user_id=user.id)
    return MyInvitations(
        items=[
            MyInvitation(
                id=inv.id,
                room=RoomBrief.of(inv.room),
                inviter=UserPublic.of(inv.inviter),
                created_at=inv.created_at,
            )
            for inv in rows
        ]
    )


@router.post("/invitations/{invitation_id}/accept")
async def accept(invitation_id: int, user: CurrentUser, db: DbSession) -> RoomSummary:
    room, role = await invitation_service.accept(db, user=user, invitation_id=invitation_id)
    return RoomSummary.of(room, role)


@router.post("/invitations/{invitation_id}/decline", status_code=status.HTTP_204_NO_CONTENT)
async def decline(invitation_id: int, user: CurrentUser, db: DbSession) -> Response:
    await invitation_service.decline(db, user=user, invitation_id=invitation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
