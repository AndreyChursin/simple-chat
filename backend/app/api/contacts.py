"""Contacts, friend requests, personal bans and DMs (docs/API_AND_WS_SPEC.md §4, §8)."""

from fastapi import APIRouter, Response, status
from fastapi.responses import JSONResponse

from app.api.schemas import (
    BanList,
    BanOut,
    ContactsOut,
    DmOut,
    FriendAccepted,
    FriendOut,
    FriendRequestBody,
    FriendRequestCreated,
    IncomingRequest,
    OutgoingRequest,
    PastDm,
    UserIdBody,
    UserPublic,
)
from app.core.deps import CurrentUser, DbSession
from app.services import contacts as contact_service

router = APIRouter(tags=["contacts"])


@router.get("/contacts")
async def contacts(user: CurrentUser, db: DbSession) -> ContactsOut:
    c = await contact_service.list_contacts(db, user=user)
    return ContactsOut(
        friends=[FriendOut(**UserPublic.of(f).model_dump(), dm_id=dm_id) for f, dm_id in c.friends],
        incoming=[
            IncomingRequest(
                id=r.id, from_=UserPublic.of(r.requester), message=r.message, created_at=r.created_at
            )
            for r in c.incoming
        ],
        outgoing=[
            OutgoingRequest(
                id=r.id, to=UserPublic.of(r.addressee), message=r.message, created_at=r.created_at
            )
            for r in c.outgoing
        ],
        past_dms=[PastDm(dm_id=dm.id, peer=UserPublic.of(peer)) for dm, peer in c.past_dms],
    )


@router.post("/friend-requests", status_code=status.HTTP_201_CREATED, response_model=None)
async def send_friend_request(
    body: FriendRequestBody, user: CurrentUser, db: DbSession
) -> FriendRequestCreated | JSONResponse:
    result = await contact_service.send_request(
        db, user=user, username=body.username.strip(), message=body.message
    )
    if result.request is None:  # their pending request was accepted instead
        assert result.friend is not None and result.dm_id is not None
        accepted = FriendAccepted(friend=UserPublic.of(result.friend), dm_id=result.dm_id)
        return JSONResponse(accepted.model_dump(mode="json"), status_code=status.HTTP_200_OK)
    r = result.request
    return FriendRequestCreated(
        request=OutgoingRequest(
            id=r.id, to=UserPublic.of(r.addressee), message=r.message, created_at=r.created_at
        )
    )


@router.post("/friend-requests/{request_id}/accept")
async def accept_friend_request(request_id: int, user: CurrentUser, db: DbSession) -> FriendAccepted:
    friend, dm_id = await contact_service.accept_request(db, user=user, request_id=request_id)
    return FriendAccepted(friend=UserPublic.of(friend), dm_id=dm_id)


@router.post("/friend-requests/{request_id}/decline", status_code=status.HTTP_204_NO_CONTENT)
async def decline_friend_request(request_id: int, user: CurrentUser, db: DbSession) -> Response:
    await contact_service.decline_request(db, user=user, request_id=request_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/friend-requests/{request_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_friend_request(request_id: int, user: CurrentUser, db: DbSession) -> Response:
    await contact_service.cancel_request(db, user=user, request_id=request_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/friends/{friend_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_friend(friend_id: int, user: CurrentUser, db: DbSession) -> Response:
    await contact_service.remove_friend(db, user=user, friend_id=friend_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/user-bans")
async def list_bans(user: CurrentUser, db: DbSession) -> BanList:
    bans = await contact_service.list_bans(db, user=user)
    return BanList(items=[BanOut(user=UserPublic.of(b.banned), created_at=b.created_at) for b in bans])


@router.post("/user-bans", status_code=status.HTTP_201_CREATED)
async def ban_user(body: UserIdBody, user: CurrentUser, db: DbSession) -> BanOut:
    ban = await contact_service.ban_user(db, user=user, target_id=body.user_id)
    return BanOut(user=UserPublic.of(ban.banned), created_at=ban.created_at)


@router.delete("/user-bans/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unban_user(target_id: int, user: CurrentUser, db: DbSession) -> Response:
    await contact_service.unban_user(db, user=user, target_id=target_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/dms")
async def open_dm(body: UserIdBody, user: CurrentUser, db: DbSession) -> DmOut:
    dm = await contact_service.open_dm(db, user=user, peer_id=body.user_id)
    return DmOut(dm_id=dm.id, peer=UserPublic.of(dm.peer(user.id)), frozen=False, frozen_reason=None)


@router.get("/dms/{dm_id}")
async def get_dm(dm_id: int, user: CurrentUser, db: DbSession) -> DmOut:
    dm, reason = await contact_service.get_dm(db, user=user, dm_id=dm_id)
    return DmOut(
        dm_id=dm.id, peer=UserPublic.of(dm.peer(user.id)), frozen=reason is not None, frozen_reason=reason
    )
