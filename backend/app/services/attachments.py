"""Attachments: upload (pending), comment, cancel, protected download (docs/API_AND_WS_SPEC.md §10).
Binding to a message happens in app/services/messages.py when the message is sent."""

import re
from datetime import UTC, datetime
from pathlib import Path

import anyio
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.core.errors import AppError
from app.db.models import Attachment, User
from app.services import access, files
from app.services.access import ChatRef
from app.services.uploads import COMMENT_MAX_CHARS, receive_file

_MIME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,62}/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,62}$")
GENERIC_MIME = "application/octet-stream"


def not_found() -> AppError:
    return AppError(404, "attachment_not_found", "Attachment not found")


async def upload(db: AsyncSession, *, user: User, chat: ChatRef, request: Request) -> Attachment:
    """Stream the file to disk, classify it by content, store it as a pending attachment."""
    await access.require_chat_writable(db, chat, user.id)
    await db.commit()  # do not hold a transaction open while the body streams in
    received = await receive_file(request)
    try:
        # receive_file already held anything with image magic bytes to the 3 MB limit
        image = await files.inspect_image(received.tmp_path, received.head)
        declared = received.declared_type.lower()
        mime = image[0] if image else (declared if _MIME_RE.match(declared) else GENERIC_MIME)
        stored_path = files.new_stored_path(chat.type, chat.id)
        await files.store(received.tmp_path, stored_path)
    except BaseException:
        await files.discard(received.tmp_path)
        raise
    attachment = Attachment(
        room_id=chat.id if chat.type == "room" else None,
        dm_id=chat.id if chat.type == "dm" else None,
        uploader_id=user.id,
        stored_path=stored_path,
        original_filename=received.filename,
        mime_type=mime[:127],
        size_bytes=received.size,
        is_image=image is not None,
        width=image[1] if image else None,
        height=image[2] if image else None,
        comment=received.comment,
        created_at=datetime.now(UTC),
    )
    try:
        # Access may have been lost while uploading (left, banned, DM frozen).
        await access.require_chat_writable(db, chat, user.id)
        db.add(attachment)
        await db.commit()
    except (AppError, SQLAlchemyError):
        await db.rollback()
        await files.remove_files([stored_path])
        raise
    return attachment


async def _own_pending(db: AsyncSession, attachment_id: int, user: User) -> Attachment:
    attachment = await db.get(Attachment, attachment_id)
    if attachment is None:
        raise not_found()
    await access.require_attachment_readable(db, attachment, user.id)
    access.require_attachment_uploader(attachment, user.id)
    if attachment.message_id is not None:
        raise AppError(409, "attachment_already_sent", "This attachment is already part of a message")
    return attachment


async def set_comment(db: AsyncSession, *, user: User, attachment_id: int, comment: str | None) -> Attachment:
    attachment = await _own_pending(db, attachment_id, user)
    value = (comment or "").strip()
    if len(value) > COMMENT_MAX_CHARS:
        raise AppError(400, "validation_error", f"Comment must be at most {COMMENT_MAX_CHARS} characters")
    attachment.comment = value or None
    await db.commit()
    return attachment


async def cancel(db: AsyncSession, *, user: User, attachment_id: int) -> None:
    attachment = await _own_pending(db, attachment_id, user)
    stored = attachment.stored_path
    await db.delete(attachment)
    await db.commit()
    await files.remove_files([stored])


async def for_download(db: AsyncSession, *, user: User, attachment_id: int) -> tuple[Attachment, Path]:
    attachment = await db.get(Attachment, attachment_id)
    if attachment is None:
        raise not_found()
    await access.require_attachment_readable(db, attachment, user.id)
    path = files.absolute(attachment.stored_path)
    if not await anyio.Path(path).is_file():
        raise not_found()
    return attachment, path
