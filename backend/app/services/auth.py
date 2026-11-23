"""Accounts, sessions and passwords (docs/API_AND_WS_SPEC.md §1–3)."""

import ipaddress
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.mail import send_mail
from app.core.passwords import hash_password, needs_rehash, verify_password
from app.core.tokens import hash_token, new_token
from app.db.models import PasswordResetToken, User, UserSession
from app.ws.manager import manager

# Touch last_seen_at / slide expiry at most this often per session (avoids a write per request).
SESSION_TOUCH_INTERVAL = timedelta(seconds=60)
USER_AGENT_MAX_LEN = 512


def utcnow() -> datetime:
    return datetime.now(UTC)


def session_ttl(persistent: bool) -> timedelta:
    settings = get_settings()
    if persistent:
        return timedelta(days=settings.session_ttl_days)
    return timedelta(hours=settings.session_short_ttl_hours)


def normalize_ip(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return None


# --- registration / login -------------------------------------------------------------------------


async def register(db: AsyncSession, *, email: str, username: str, password: str) -> User:
    if await db.scalar(select(User.id).where(User.email == email)) is not None:
        raise AppError(409, "email_taken", "This e-mail is already registered")
    if await db.scalar(select(User.id).where(User.username == username)) is not None:
        raise AppError(409, "username_taken", "This username is already taken")

    user = User(email=email, username=username, password_hash=await hash_password(password))
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:  # concurrent registration with the same e-mail/username
        await db.rollback()
        if "uq_users_email" in str(exc.orig):
            raise AppError(409, "email_taken", "This e-mail is already registered") from exc
        if "uq_users_username" in str(exc.orig):
            raise AppError(409, "username_taken", "This username is already taken") from exc
        raise
    return user


async def authenticate(db: AsyncSession, *, email: str, password: str) -> User:
    user = await db.scalar(select(User).where(User.email == email))
    ok = await verify_password(user.password_hash if user else None, password)
    if user is None or not ok:
        raise AppError(401, "invalid_credentials", "Invalid e-mail or password")
    if needs_rehash(user.password_hash):
        user.password_hash = await hash_password(password)
    return user


# --- sessions -------------------------------------------------------------------------------------


async def create_session(
    db: AsyncSession, *, user: User, persistent: bool, user_agent: str, ip: str | None
) -> tuple[UserSession, str]:
    token = new_token()
    now = utcnow()
    session = UserSession(
        user_id=user.id,
        token_hash=hash_token(token),
        persistent=persistent,
        user_agent=user_agent[:USER_AGENT_MAX_LEN],
        ip=normalize_ip(ip),
        created_at=now,
        last_seen_at=now,
        expires_at=now + session_ttl(persistent),
    )
    db.add(session)
    await db.flush()
    session.user = user
    return session, token


async def resolve_session(db: AsyncSession, *, token: str, ip: str | None) -> UserSession | None:
    now = utcnow()
    session = await db.scalar(
        select(UserSession).where(UserSession.token_hash == hash_token(token), UserSession.expires_at > now)
    )
    if session is None:
        return None
    if now - session.last_seen_at >= SESSION_TOUCH_INTERVAL:
        session.last_seen_at = now
        session.expires_at = now + session_ttl(session.persistent)
        session.ip = normalize_ip(ip) or session.ip
        await db.commit()
    return session


async def revoke_sessions(db: AsyncSession, *, user_id: int, session_ids: list[int]) -> None:
    """Delete sessions (must belong to user_id), commit the unit of work (always), then close the
    sessions' WebSocket connections."""
    if session_ids:
        await db.execute(
            delete(UserSession).where(UserSession.user_id == user_id, UserSession.id.in_(session_ids))
        )
    await db.commit()
    if session_ids:
        await manager.revoke_sessions(session_ids)


async def list_sessions(db: AsyncSession, *, user_id: int) -> list[UserSession]:
    rows = await db.scalars(
        select(UserSession)
        .where(UserSession.user_id == user_id, UserSession.expires_at > utcnow())
        .order_by(UserSession.last_seen_at.desc())
    )
    return list(rows)


async def other_session_ids(db: AsyncSession, *, user_id: int, keep_session_id: int | None) -> list[int]:
    query = select(UserSession.id).where(UserSession.user_id == user_id)
    if keep_session_id is not None:
        query = query.where(UserSession.id != keep_session_id)
    return list(await db.scalars(query))


# --- passwords ------------------------------------------------------------------------------------


async def change_password(
    db: AsyncSession, *, session: UserSession, current_password: str, new_password: str
) -> None:
    user = session.user
    if not await verify_password(user.password_hash, current_password):
        raise AppError(401, "invalid_current_password", "Current password is incorrect")
    user.password_hash = await hash_password(new_password)
    user.password_changed_at = utcnow()
    await db.flush()
    others = await other_session_ids(db, user_id=user.id, keep_session_id=session.id)
    await revoke_sessions(db, user_id=user.id, session_ids=others)  # commits


async def request_password_reset(db: AsyncSession, *, email: str) -> tuple[str, str] | None:
    """Create a reset token. Returns (email, link) to send, or None if the e-mail is unknown.
    The caller always answers 202 so that existence of accounts is not revealed."""
    user = await db.scalar(select(User).where(User.email == email))
    if user is None:
        return None
    await db.execute(
        delete(PasswordResetToken).where(
            PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None)
        )
    )
    token = new_token()
    now = utcnow()
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hash_token(token),
            created_at=now,
            expires_at=now + timedelta(minutes=get_settings().password_reset_ttl_minutes),
        )
    )
    await db.commit()
    link = f"{get_settings().public_origin}/reset-password?token={token}"
    return user.email, link


async def send_password_reset_mail(email: str, link: str) -> None:
    minutes = get_settings().password_reset_ttl_minutes
    body = (
        "Someone (hopefully you) asked to reset the password of your chat account.\n\n"
        f"Open this link to choose a new password (valid for {minutes} minutes, single use):\n"
        f"{link}\n\n"
        "If you did not ask for this, ignore this e-mail; your password stays the same.\n"
    )
    await send_mail(email, "Reset your chat password", body)


async def reset_password(db: AsyncSession, *, token: str, new_password: str) -> None:
    now = utcnow()
    row = await db.scalar(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(token)).with_for_update()
    )
    if row is None or row.used_at is not None or row.expires_at <= now:
        raise AppError(410, "reset_token_invalid", "This reset link has expired or was already used")
    row.used_at = now
    await db.execute(
        update(User)
        .where(User.id == row.user_id)
        .values(password_hash=await hash_password(new_password), password_changed_at=now)
    )
    sessions = await other_session_ids(db, user_id=row.user_id, keep_session_id=None)
    await revoke_sessions(db, user_id=row.user_id, session_ids=sessions)  # commits
