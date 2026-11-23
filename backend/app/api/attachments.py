"""Attachment endpoints (docs/API_AND_WS_SPEC.md §10, docs/ARCHITECTURE.md §5.2–5.3)."""

from urllib.parse import quote

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import FileResponse

from app.api.messages import ChatPath, chat_ref
from app.api.schemas import AttachmentCommentRequest, AttachmentOut
from app.core.deps import CurrentUser, DbSession
from app.services import attachments as attachment_service

router = APIRouter(tags=["attachments"])


@router.post("/{chat_type}/{chat_id}/attachments", status_code=status.HTTP_201_CREATED)
async def upload(
    chat_type: ChatPath, chat_id: int, request: Request, user: CurrentUser, db: DbSession
) -> AttachmentOut:
    """multipart/form-data: `file` (required), `comment` (optional, ≤ 1000). The body is parsed as a
    stream — see app/services/uploads.py — so it is deliberately not declared as FastAPI form fields."""
    attachment = await attachment_service.upload(
        db, user=user, chat=chat_ref(chat_type, chat_id), request=request
    )
    return AttachmentOut.of(attachment)


@router.patch("/attachments/{attachment_id}")
async def set_comment(
    attachment_id: int, body: AttachmentCommentRequest, user: CurrentUser, db: DbSession
) -> AttachmentOut:
    attachment = await attachment_service.set_comment(
        db, user=user, attachment_id=attachment_id, comment=body.comment
    )
    return AttachmentOut.of(attachment)


@router.delete("/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel(attachment_id: int, user: CurrentUser, db: DbSession) -> Response:
    await attachment_service.cancel(db, user=user, attachment_id=attachment_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def content_disposition(kind: str, filename: str) -> str:
    """RFC 6266: an ASCII fallback plus the exact UTF-8 name (Cyrillic, emoji…)."""
    fallback = "".join(ch if 32 <= ord(ch) < 127 and ch not in '"\\%' else "_" for ch in filename) or "file"
    return f"{kind}; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


@router.get("/attachments/{attachment_id}/download")
async def download(
    attachment_id: int, user: CurrentUser, db: DbSession, inline: int = Query(0, ge=0, le=1)
) -> FileResponse:
    """Access is checked on every request against current membership (no static serving of uploads).
    Range requests are answered by FileResponse."""
    attachment, path = await attachment_service.for_download(db, user=user, attachment_id=attachment_id)
    as_inline = bool(inline) and attachment.is_image
    return FileResponse(
        path,
        media_type=attachment.mime_type if attachment.is_image else "application/octet-stream",
        headers={
            "Content-Disposition": content_disposition(
                "inline" if as_inline else "attachment", attachment.original_filename
            ),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
            "Cache-Control": "private, max-age=0, must-revalidate",
        },
    )
