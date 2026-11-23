from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel
from user_agents import parse as parse_ua

from app.core.deps import CurrentSession, DbSession, clear_session_cookie
from app.core.errors import AppError
from app.db.models import UserSession
from app.services import auth as auth_service

router = APIRouter(prefix="/sessions", tags=["sessions"])


class SessionOut(BaseModel):
    id: int
    browser: str
    os: str
    device: Literal["Desktop", "Mobile", "Tablet", "Bot", "Other"]
    user_agent: str
    ip: str | None
    created_at: datetime
    last_seen_at: datetime
    persistent: bool
    is_current: bool


class SessionList(BaseModel):
    items: list[SessionOut]


def _version(family: str, version: str) -> str:
    if family == "Other":
        return "Unknown"
    major = version.split(".")[0] if version else ""
    return f"{family} {major}".strip()


def describe(session: UserSession, current_id: int) -> SessionOut:
    ua = parse_ua(session.user_agent or "")
    device: Literal["Desktop", "Mobile", "Tablet", "Bot", "Other"]
    if ua.is_bot:
        device = "Bot"
    elif ua.is_tablet:
        device = "Tablet"
    elif ua.is_mobile:
        device = "Mobile"
    elif ua.is_pc:
        device = "Desktop"
    else:
        device = "Other"
    return SessionOut(
        id=session.id,
        browser=_version(ua.browser.family, ua.browser.version_string),
        os=_version(ua.os.family, ua.os.version_string),
        device=device,
        user_agent=session.user_agent,
        ip=str(session.ip) if session.ip is not None else None,
        created_at=session.created_at,
        last_seen_at=session.last_seen_at,
        persistent=session.persistent,
        is_current=session.id == current_id,
    )


@router.get("")
async def list_sessions(session: CurrentSession, db: DbSession) -> SessionList:
    rows = await auth_service.list_sessions(db, user_id=session.user_id)
    items = [describe(s, session.id) for s in rows]
    items.sort(key=lambda s: not s.is_current)  # current first, then by last activity (stable sort)
    return SessionList(items=items)


@router.delete("/{session_id}", status_code=204)
async def revoke_session(session_id: int, session: CurrentSession, db: DbSession) -> Response:
    ids = await auth_service.other_session_ids(db, user_id=session.user_id, keep_session_id=None)
    if session_id not in ids:
        raise AppError(404, "not_found", "Session not found")
    await auth_service.revoke_sessions(db, user_id=session.user_id, session_ids=[session_id])
    response = Response(status_code=204)
    if session_id == session.id:
        clear_session_cookie(response)
    return response


@router.delete("", status_code=204)
async def revoke_other_sessions(
    session: CurrentSession, db: DbSession, scope: Literal["others"] = Query()
) -> Response:
    ids = await auth_service.other_session_ids(db, user_id=session.user_id, keep_session_id=session.id)
    await auth_service.revoke_sessions(db, user_id=session.user_id, session_ids=ids)
    return Response(status_code=204)
