from fastapi import APIRouter, BackgroundTasks, Request, Response

from app.api.schemas import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    Me,
    MeResponse,
    RegisterRequest,
    ResetPasswordRequest,
)
from app.core.deps import (
    SESSION_COOKIE,
    CurrentSession,
    DbSession,
    clear_session_cookie,
    client_ip,
    set_session_cookie,
    user_agent,
)
from app.core.ratelimit import rate_limiter
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

LOGIN_LIMIT = (10, 60.0)  # per IP + e-mail per minute
FORGOT_PER_EMAIL_LIMIT = (3, 3600.0)
FORGOT_PER_IP_LIMIT = (20, 3600.0)


@router.post("/register", status_code=201)
async def register(body: RegisterRequest, request: Request, response: Response, db: DbSession) -> MeResponse:
    user = await auth_service.register(db, email=body.email, username=body.username, password=body.password)
    _, token = await auth_service.create_session(
        db, user=user, persistent=False, user_agent=user_agent(request), ip=client_ip(request)
    )
    await db.commit()
    await db.refresh(user)
    set_session_cookie(response, token, persistent=False)
    return MeResponse(user=Me.of(user))


@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response, db: DbSession) -> MeResponse:
    rate_limiter.hit(f"login:{client_ip(request)}:{body.email.strip().lower()}", *LOGIN_LIMIT)
    user = await auth_service.authenticate(db, email=body.email.strip(), password=body.password)
    _, token = await auth_service.create_session(
        db,
        user=user,
        persistent=body.keep_signed_in,
        user_agent=user_agent(request),
        ip=client_ip(request),
    )
    await db.commit()
    set_session_cookie(response, token, persistent=body.keep_signed_in)
    return MeResponse(user=Me.of(user))


@router.post("/logout", status_code=204)
async def logout(session: CurrentSession, db: DbSession) -> Response:
    """Ends only the current browser session; other sessions of the user stay signed in."""
    await auth_service.revoke_sessions(db, user_id=session.user_id, session_ids=[session.id])
    response = Response(status_code=204)
    clear_session_cookie(response)
    return response


@router.get("/me")
async def me(session: CurrentSession, request: Request, response: Response) -> MeResponse:
    if session.persistent:
        # Slide the persistent cookie's Max-Age along with the server-side expiry.
        token = request.cookies[SESSION_COOKIE]
        set_session_cookie(response, token, persistent=True)
    return MeResponse(user=Me.of(session.user))


@router.post("/password/forgot", status_code=202)
async def forgot_password(
    body: ForgotPasswordRequest, request: Request, background: BackgroundTasks, db: DbSession
) -> Response:
    email = body.email.strip()
    rate_limiter.hit(f"forgot-ip:{client_ip(request)}", *FORGOT_PER_IP_LIMIT)
    rate_limiter.hit(f"forgot-email:{email.lower()}", *FORGOT_PER_EMAIL_LIMIT)
    result = await auth_service.request_password_reset(db, email=email)
    if result is not None:
        # Sent after the response so the timing does not reveal whether the account exists.
        background.add_task(auth_service.send_password_reset_mail, *result)
    return Response(status_code=202)


@router.post("/password/reset", status_code=204)
async def reset_password(body: ResetPasswordRequest, db: DbSession) -> Response:
    await auth_service.reset_password(db, token=body.token, new_password=body.new_password)
    return Response(status_code=204)


@router.post("/password/change", status_code=204)
async def change_password(body: ChangePasswordRequest, session: CurrentSession, db: DbSession) -> Response:
    """Keeps the current session; every other session of the user is signed out."""
    await auth_service.change_password(
        db, session=session, current_password=body.current_password, new_password=body.new_password
    )
    return Response(status_code=204)
