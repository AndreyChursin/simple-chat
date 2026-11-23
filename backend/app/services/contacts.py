"""Friends, friend requests, personal bans and DMs (docs/API_AND_WS_SPEC.md §4, §8).

A DM is writable only while its two sides are friends and neither banned the other; that is computed
(app/services/access.py `dm_frozen_reason`), never stored. Every friendship has its DM: accepting a
request gets or creates it, so the WebSocket subscription to the DM chat is what makes friends see each
other's presence and messages.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import delete, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import UserPublic
from app.core.errors import AppError
from app.db.models import DirectConversation, Friendship, FriendshipStatus, User, UserBan
from app.services import access
from app.services.access import FrozenReason
from app.ws.manager import manager


def utcnow() -> datetime:
    return datetime.now(UTC)


def _public(user: User | None) -> dict[str, Any]:
    return UserPublic.of(user).model_dump(mode="json")


async def _user_by_id(db: AsyncSession, user_id: int) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise AppError(404, "user_not_found", "User not found")
    return user


async def _pair(db: AsyncSession, a: int, b: int) -> Friendship | None:
    row: Friendship | None = await db.scalar(select(Friendship).where(access.pair_filter(a, b)))
    return row


# --- DMs ---------------------------------------------------------------------------------------------


async def dm_between(db: AsyncSession, a: int, b: int) -> DirectConversation | None:
    lo, hi = sorted((a, b))
    row: DirectConversation | None = await db.scalar(
        select(DirectConversation).where(
            DirectConversation.user_a_orig_id == lo, DirectConversation.user_b_orig_id == hi
        )
    )
    return row


async def _get_or_create_dm(db: AsyncSession, a: int, b: int) -> tuple[DirectConversation, bool]:
    """(dm, created). Caller commits."""
    existing = await dm_between(db, a, b)
    if existing is not None:
        return existing, False
    lo, hi = sorted((a, b))
    await db.execute(
        pg_insert(DirectConversation)
        .values(user_a_id=lo, user_b_id=hi, user_a_orig_id=lo, user_b_orig_id=hi, created_at=utcnow())
        .on_conflict_do_nothing()
    )
    dm = await dm_between(db, a, b)
    assert dm is not None
    return dm, True


async def open_dm(db: AsyncSession, *, user: User, peer_id: int) -> DirectConversation:
    """`POST /api/dms`: get-or-create the DM with a friend."""
    peer = await _user_by_id(db, peer_id)
    if peer.id == user.id:
        raise AppError(400, "validation_error", "You cannot message yourself")
    if await access.is_user_banned_between(db, user.id, peer.id):
        raise access.user_banned()
    if not await access.are_friends(db, user.id, peer.id):
        raise AppError(403, "not_friends", "You can only message your friends")
    dm, created = await _get_or_create_dm(db, user.id, peer.id)
    await db.commit()
    if created:
        for uid in (user.id, peer.id):
            await manager.on_membership_changed(uid, ("dm", dm.id), joined=True)
    return dm


async def get_dm(
    db: AsyncSession, *, user: User, dm_id: int
) -> tuple[DirectConversation, FrozenReason | None]:
    dm = await access.require_dm_side(db, dm_id, user.id)
    return dm, await access.dm_frozen_reason(db, dm)


async def _dm_became_writable(dm: DirectConversation, a: int, b: int) -> None:
    for uid in (a, b):
        await manager.on_membership_changed(uid, ("dm", dm.id), joined=True)
    await manager.send_to_users([a, b], {"type": "dm_unfrozen", "dm_id": dm.id})


async def _dm_became_frozen(db: AsyncSession, a: int, b: int, reason: FrozenReason) -> None:
    dm = await dm_between(db, a, b)
    if dm is None:
        return
    for uid in (a, b):
        await manager.on_membership_changed(uid, ("dm", dm.id), joined=False)
    await manager.send_to_users([a, b], {"type": "dm_frozen", "dm_id": dm.id, "reason": reason})


# --- friend requests ---------------------------------------------------------------------------------


@dataclass
class RequestResult:
    request: Friendship | None = None  # a new pending request
    friend: User | None = None  # set when a pending request back was accepted instead
    dm_id: int | None = None


async def send_request(db: AsyncSession, *, user: User, username: str, message: str | None) -> RequestResult:
    target = await db.scalar(select(User).where(User.username == username))
    if target is None:
        raise AppError(404, "user_not_found", "User not found")
    if target.id == user.id:
        raise AppError(400, "validation_error", "You cannot add yourself as a friend")
    if await access.is_user_banned_between(db, user.id, target.id):
        raise access.user_banned()
    existing = await _pair(db, user.id, target.id)
    if existing is not None:
        if existing.status == FriendshipStatus.accepted:
            raise AppError(409, "already_friends", "You are already friends")
        if existing.requester_id == user.id:
            raise AppError(409, "friend_request_exists", "You already sent a friend request")
        # They asked first: a request back means yes.
        friend, dm_id = await _accept(db, existing)
        return RequestResult(friend=friend, dm_id=dm_id)

    request = Friendship(
        requester_id=user.id,
        addressee_id=target.id,
        message=(message or "").strip() or None,
        created_at=utcnow(),
    )
    db.add(request)
    try:
        await db.commit()
    except IntegrityError as exc:  # concurrent requests between the same pair (ux_friendships_pair)
        await db.rollback()
        raise AppError(409, "friend_request_exists", "A friend request between you already exists") from exc
    request.requester = user
    request.addressee = target
    await manager.send_to_users(
        [target.id],
        {
            "type": "friend_request_received",
            "request": {"id": request.id, "from": _public(user), "message": request.message},
        },
    )
    return RequestResult(request=request)


async def _accept(db: AsyncSession, request: Friendship) -> tuple[User, int]:
    """Make the pair friends (commits). Returns (the requester, dm_id) from the addressee's view."""
    request.status = FriendshipStatus.accepted
    request.accepted_at = utcnow()
    dm, created = await _get_or_create_dm(db, request.requester_id, request.addressee_id)
    await db.commit()
    requester, addressee = request.requester, request.addressee
    await manager.send_to_users(
        [requester.id], {"type": "friend_request_resolved", "request_id": request.id, "result": "accepted"}
    )
    if created:
        for uid in (requester.id, addressee.id):
            await manager.on_membership_changed(uid, ("dm", dm.id), joined=True)
    else:  # friends again: the old history becomes writable
        await _dm_became_writable(dm, requester.id, addressee.id)
    for me, other in ((requester, addressee), (addressee, requester)):
        await manager.send_to_users([me.id], {"type": "friend_added", "user": _public(other), "dm_id": dm.id})
    return requester, dm.id


async def accept_request(db: AsyncSession, *, user: User, request_id: int) -> tuple[User, int]:
    request = await db.scalar(
        select(Friendship).where(Friendship.id == request_id).with_for_update(of=Friendship)
    )
    if request is None or request.addressee_id != user.id:
        raise AppError(404, "not_found", "Friend request not found")
    if request.status != FriendshipStatus.pending:
        raise AppError(409, "request_not_pending", "This friend request was already handled")
    return await _accept(db, request)


async def decline_request(db: AsyncSession, *, user: User, request_id: int) -> None:
    request = await db.get(Friendship, request_id)
    if request is None or request.addressee_id != user.id or request.status != FriendshipStatus.pending:
        raise AppError(404, "not_found", "Friend request not found")
    requester_id = request.requester_id
    await db.delete(request)
    await db.commit()
    await manager.send_to_users(
        [requester_id], {"type": "friend_request_resolved", "request_id": request_id, "result": "declined"}
    )


async def cancel_request(db: AsyncSession, *, user: User, request_id: int) -> None:
    request = await db.get(Friendship, request_id)
    if request is None or request.requester_id != user.id or request.status != FriendshipStatus.pending:
        raise AppError(404, "not_found", "Friend request not found")
    addressee_id = request.addressee_id
    await db.delete(request)
    await db.commit()
    await manager.send_to_users(
        [addressee_id], {"type": "friend_request_resolved", "request_id": request_id, "result": "cancelled"}
    )


# --- friends -----------------------------------------------------------------------------------------


async def remove_friend(db: AsyncSession, *, user: User, friend_id: int) -> None:
    """Either side, any time. The DM and its history stay, frozen until they are friends again."""
    friendship = await _pair(db, user.id, friend_id)
    if friendship is None or friendship.status != FriendshipStatus.accepted:
        raise AppError(404, "not_found", "Not your friend")
    await db.delete(friendship)
    await db.commit()
    await _friendship_ended(db, user.id, friend_id, "not_friends")


async def _friendship_ended(db: AsyncSession, a: int, b: int, reason: FrozenReason) -> None:
    await manager.send_to_users([a], {"type": "friend_removed", "user_id": b})
    await manager.send_to_users([b], {"type": "friend_removed", "user_id": a})
    await _dm_became_frozen(db, a, b, reason)


# --- personal bans -----------------------------------------------------------------------------------


async def ban_user(db: AsyncSession, *, user: User, target_id: int) -> UserBan:
    """No contact from `target` any more: the friendship or pending requests of the pair are removed,
    the DM history stays visible to both but read-only (docs/API_AND_WS_SPEC.md §4)."""
    if target_id == user.id:
        raise AppError(400, "validation_error", "You cannot ban yourself")
    target = await _user_by_id(db, target_id)
    existing = await db.get(UserBan, (user.id, target_id))
    if existing is not None:
        raise AppError(409, "already_banned", "You already banned this user")
    friendship = await _pair(db, user.id, target_id)
    was = (friendship.status, friendship.id) if friendship is not None else None
    ban = UserBan(banner_id=user.id, banned_id=target_id, created_at=utcnow())
    db.add(ban)
    if friendship is not None:
        await db.delete(friendship)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise AppError(409, "already_banned", "You already banned this user") from exc

    ban.banned = target
    if was is not None and was[0] == FriendshipStatus.accepted:
        await _friendship_ended(db, user.id, target_id, "user_banned")
        return ban
    if was is not None:  # a pending request in either direction is gone
        await manager.send_to_users(
            [user.id, target_id],
            {"type": "friend_request_resolved", "request_id": was[1], "result": "cancelled"},
        )
    await _dm_became_frozen(db, user.id, target_id, "user_banned")
    return ban


async def unban_user(db: AsyncSession, *, user: User, target_id: int) -> None:
    """The friendship is not restored; the DM stays frozen until they are friends again."""
    result = await db.execute(
        delete(UserBan)
        .where(UserBan.banner_id == user.id, UserBan.banned_id == target_id)
        .returning(UserBan.banned_id)
    )
    if result.first() is None:
        raise AppError(404, "not_found", "This user is not banned")
    await db.commit()
    dm = await dm_between(db, user.id, target_id)
    if dm is not None:
        reason = await access.dm_frozen_reason(db, dm)
        if reason is not None:  # e.g. user_banned → not_friends, or still banned by the other side
            await manager.send_to_users(
                [user.id, target_id], {"type": "dm_frozen", "dm_id": dm.id, "reason": reason}
            )


async def list_bans(db: AsyncSession, *, user: User) -> list[UserBan]:
    return list(
        await db.scalars(
            select(UserBan).where(UserBan.banner_id == user.id).order_by(UserBan.created_at.desc())
        )
    )


# --- contacts overview -------------------------------------------------------------------------------


@dataclass
class Contacts:
    friends: list[tuple[User, int | None]]  # (friend, dm_id)
    incoming: list[Friendship]
    outgoing: list[Friendship]
    past_dms: list[tuple[DirectConversation, User | None]]  # frozen DMs and their peer


async def list_contacts(db: AsyncSession, *, user: User) -> Contacts:
    rows = list(
        await db.scalars(
            select(Friendship).where(
                or_(Friendship.requester_id == user.id, Friendship.addressee_id == user.id)
            )
        )
    )
    dms = list(
        await db.scalars(
            select(DirectConversation).where(
                or_(DirectConversation.user_a_id == user.id, DirectConversation.user_b_id == user.id)
            )
        )
    )
    dm_by_peer = {dm.peer_orig_id(user.id): dm for dm in dms}
    friends: list[tuple[User, int | None]] = []
    incoming: list[Friendship] = []
    outgoing: list[Friendship] = []
    for f in rows:
        if f.status == FriendshipStatus.accepted:
            other = f.other(user.id)
            dm = dm_by_peer.get(other.id)
            friends.append((other, dm.id if dm else None))
        elif f.addressee_id == user.id:
            incoming.append(f)
        else:
            outgoing.append(f)
    friend_ids = {u.id for u, _ in friends}
    # Friends are never banned (a ban ends the friendship), so a DM is frozen iff its peer is gone or
    # no longer a friend.
    past = [(dm, dm.peer(user.id)) for dm in dms if dm.peer_orig_id(user.id) not in friend_ids]
    friends.sort(key=lambda p: p[0].username.lower())
    incoming.sort(key=lambda f: f.created_at, reverse=True)
    outgoing.sort(key=lambda f: f.created_at, reverse=True)
    past.sort(key=lambda p: p[0].created_at, reverse=True)
    return Contacts(friends=friends, incoming=incoming, outgoing=outgoing, past_dms=past)


@dataclass
class Relation:
    friendship: Literal["none", "pending_out", "pending_in", "friends"]
    banned_by_me: bool
    has_banned_me: bool


async def relation(db: AsyncSession, *, user_id: int, other_id: int) -> Relation:
    f = await _pair(db, user_id, other_id)
    state: Literal["none", "pending_out", "pending_in", "friends"]
    if f is None:
        state = "none"
    elif f.status == FriendshipStatus.accepted:
        state = "friends"
    else:
        state = "pending_out" if f.requester_id == user_id else "pending_in"
    by_me = await db.get(UserBan, (user_id, other_id)) is not None
    by_them = await db.get(UserBan, (other_id, user_id)) is not None
    return Relation(friendship=state, banned_by_me=by_me, has_banned_me=by_them)
