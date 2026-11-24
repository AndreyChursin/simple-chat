"""Unread counters (docs/API_AND_WS_SPEC.md §9)."""

from fastapi import APIRouter, Response, status

from app.api.messages import ChatPath, chat_ref
from app.api.schemas import MarkReadRequest, UnreadItemOut, UnreadList
from app.core.deps import CurrentUser, DbSession
from app.services import unread as unread_service

router = APIRouter(tags=["unread"])


@router.get("/me/unread")
async def my_unread(user: CurrentUser, db: DbSession) -> UnreadList:
    items = await unread_service.list_unread(db, user_id=user.id)
    return UnreadList(items=[UnreadItemOut.model_validate(i.as_dict()) for i in items])


@router.post("/{chat_type}/{chat_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_read(
    chat_type: ChatPath, chat_id: int, body: MarkReadRequest, user: CurrentUser, db: DbSession
) -> Response:
    await unread_service.mark_read(
        db, user_id=user.id, chat=chat_ref(chat_type, chat_id), last_read_message_id=body.last_read_message_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
