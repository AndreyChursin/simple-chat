"""Shared FastAPI dependencies: DB session, current session/user, session cookie helpers."""

from typing import Annotated

from fastapi import Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.models import User, UserSession
from app.db.session import get_db
from app.services import auth as auth_service

SESSION_COOKIE = "chat_session"

DbSession = Annotated[AsyncSession, Depends(get_db)]


def client_ip(request: Request) -> str | None:
    # uvicorn --proxy-headers fills request.client from X-Forwarded-For, which nginx overwrites
    # with the real peer address (frontend/nginx.conf).
    return request.client.host if request.client else None


def user_agent(request: Request) -> str:
    return request.headers.get("user-agent", "")


async def get_current_session(request: Request, db: DbSession) -> UserSession:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        session = await auth_service.resolve_session(db, token=token, ip=client_ip(request))
        if session is not None:
            return session
    raise AppError(401, "not_authenticated", "Authentication required")


CurrentSession = Annotated[UserSession, Depends(get_current_session)]


async def get_current_user(session: CurrentSession) -> User:
    return session.user


CurrentUser = Annotated[User, Depends(get_current_user)]


def set_session_cookie(response: Response, token: str, *, persistent: bool) -> None:
    settings = get_settings()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_ttl_days * 86400 if persistent else None,
        httponly=True,
        samesite="lax",
        secure=settings.public_origin.startswith("https://"),
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        SESSION_COOKIE,
        httponly=True,
        samesite="lax",
        secure=settings.public_origin.startswith("https://"),
        path="/",
    )
