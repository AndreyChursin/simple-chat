"""Message REST endpoints (docs/API_AND_WS_SPEC.md §9). `{chat}` = `rooms/{id}` or `dms/{id}`."""

from typing import Literal

from fastapi import APIRouter, Query, Response, status

from app.api.schemas import EditMessageRequest, MessageOut, MessagePage, MessagesAround, SendMessageRequest
from app.core.deps import CurrentUser, DbSession
from app.services import messages as message_service
from app.services.access import ChatRef

router = APIRouter(tags=["messages"])

ChatPath = Literal["rooms", "dms"]


def chat_ref(chat_type: ChatPath, chat_id: int) -> ChatRef:
    return ChatRef("room" if chat_type == "rooms" else "dm", chat_id)


@router.get("/{chat_type}/{chat_id}/messages")
async def list_messages(
    chat_type: ChatPath,
    chat_id: int,
    user: CurrentUser,
    db: DbSession,
    before_id: int | None = Query(None, ge=1),
    after_id: int | None = Query(None, ge=0),
    limit: int = Query(50, ge=1, le=100),
) -> MessagePage:
    items, has_more, cursor = await message_service.list_messages(
        db,
        user_id=user.id,
        chat=chat_ref(chat_type, chat_id),
        before_id=before_id,
        after_id=after_id,
        limit=limit,
    )
    return MessagePage(items=[MessageOut.of(m) for m in items], has_more=has_more, next_cursor=cursor)


@router.get("/{chat_type}/{chat_id}/messages/around/{message_id}")
async def messages_around(
    chat_type: ChatPath,
    chat_id: int,
    message_id: int,
    user: CurrentUser,
    db: DbSession,
    limit: int = Query(50, ge=2, le=100),
) -> MessagesAround:
    items, more_before, more_after = await message_service.messages_around(
        db, user_id=user.id, chat=chat_ref(chat_type, chat_id), message_id=message_id, limit=limit
    )
    return MessagesAround(
        items=[MessageOut.of(m) for m in items], has_more_before=more_before, has_more_after=more_after
    )


@router.get("/{chat_type}/{chat_id}/messages/search")
async def search_messages(
    chat_type: ChatPath,
    chat_id: int,
    user: CurrentUser,
    db: DbSession,
    q: str = Query(max_length=200),
    before_id: int | None = Query(None, ge=1),
    limit: int = Query(30, ge=1, le=100),
) -> MessagePage:
    items, has_more, cursor = await message_service.search_messages(
        db, user_id=user.id, chat=chat_ref(chat_type, chat_id), q=q, before_id=before_id, limit=limit
    )
    return MessagePage(items=[MessageOut.of(m) for m in items], has_more=has_more, next_cursor=cursor)


@router.post("/{chat_type}/{chat_id}/messages", status_code=status.HTTP_201_CREATED)
async def send_message(
    chat_type: ChatPath, chat_id: int, body: SendMessageRequest, user: CurrentUser, db: DbSession
) -> MessageOut:
    message, _ = await message_service.send_message(
        db,
        author=user,
        chat=chat_ref(chat_type, chat_id),
        content=body.content,
        reply_to_message_id=body.reply_to_message_id,
        client_msg_id=body.client_msg_id,
    )
    return MessageOut.of(message)


@router.patch("/messages/{message_id}")
async def edit_message(
    message_id: int, body: EditMessageRequest, user: CurrentUser, db: DbSession
) -> MessageOut:
    message = await message_service.edit_message(db, user=user, message_id=message_id, content=body.content)
    return MessageOut.of(message)


@router.delete("/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_message(message_id: int, user: CurrentUser, db: DbSession) -> Response:
    await message_service.delete_message(db, user=user, message_id=message_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
