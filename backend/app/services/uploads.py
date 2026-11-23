"""Streaming multipart reception for attachments (docs/ARCHITECTURE.md §5.2).

The request body is parsed while it arrives: the file part goes straight to `tmp/<uuid>.part` and the
byte counter is checked after every chunk, so an oversized upload is aborted early and `Content-Length`
is never trusted. A body that starts with image magic bytes is held to the image limit (3 MB) right away.
"""

import contextlib
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import aiofiles
import anyio
from python_multipart.multipart import MultipartParser, parse_options_header
from starlette.requests import Request

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.models import FILE_MAX_BYTES, IMAGE_MAX_BYTES
from app.services.files import sniff_image

COMMENT_MAX_CHARS = 1000
_COMMENT_MAX_BYTES = COMMENT_MAX_CHARS * 4
_FIELD_MAX_BYTES = 8 * 1024
# multipart overhead allowance on top of the file limit, for an early refusal by Content-Length
_ENVELOPE_BYTES = 64 * 1024


def file_too_large() -> AppError:
    return AppError(413, "file_too_large", "File exceeds 20 MB", {"max_bytes": FILE_MAX_BYTES})


def image_too_large() -> AppError:
    return AppError(413, "image_too_large", "Image exceeds 3 MB", {"max_bytes": IMAGE_MAX_BYTES})


@dataclass
class ReceivedFile:
    tmp_path: Path
    filename: str
    declared_type: str
    size: int
    head: bytes
    comment: str | None


@dataclass
class _Part:
    headers: dict[str, bytes] = field(default_factory=dict)
    name: str = ""
    filename: str | None = None
    content_type: str = ""
    data: bytearray = field(default_factory=bytearray)


class _Collector:
    """Callbacks of the synchronous parser; file bytes are buffered per chunk and flushed by the caller."""

    def __init__(self) -> None:
        self.part: _Part | None = None
        self.header_field = b""
        self.header_value = b""
        self.file_part: _Part | None = None
        self.file_done = False
        self.fields: dict[str, bytes] = {}
        self.pending_file_bytes = bytearray()

    def on_part_begin(self) -> None:
        self.part = _Part()

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self.header_field += data[start:end]

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self.header_value += data[start:end]

    def on_header_end(self) -> None:
        assert self.part is not None
        self.part.headers[self.header_field.decode("latin-1").lower()] = self.header_value
        self.header_field = self.header_value = b""

    def on_headers_finished(self) -> None:
        part = self.part
        assert part is not None
        _, params = parse_options_header(part.headers.get("content-disposition", b""))
        part.name = params.get(b"name", b"").decode("utf-8", "replace")
        raw_filename = params.get(b"filename")
        part.filename = raw_filename.decode("utf-8", "replace") if raw_filename is not None else None
        part.content_type = part.headers.get("content-type", b"").decode("latin-1").strip()
        if part.name == "file" and part.filename is not None and self.file_part is None:
            self.file_part = part

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        part = self.part
        assert part is not None
        if part is self.file_part:
            self.pending_file_bytes += data[start:end]
        else:
            part.data += data[start:end]
            if len(part.data) > _FIELD_MAX_BYTES:
                raise AppError(400, "validation_error", f"Field '{part.name}' is too long")

    def on_part_end(self) -> None:
        part = self.part
        if part is None:
            return
        if part is self.file_part:
            self.file_done = True
        elif part.name:
            self.fields[part.name] = bytes(part.data)
        self.part = None


async def receive_file(request: Request) -> ReceivedFile:
    content_type, params = parse_options_header(request.headers.get("content-type", ""))
    boundary = params.get(b"boundary")
    if content_type != b"multipart/form-data" or not boundary:
        raise AppError(400, "validation_error", "Expected multipart/form-data with a 'file' part")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > FILE_MAX_BYTES + _ENVELOPE_BYTES:
        raise file_too_large()  # an honest client gets the answer before sending anything

    tmp_dir = get_settings().upload_dir / "tmp"
    tmp_path = tmp_dir / f"{uuid.uuid4().hex}.part"
    collector = _Collector()
    parser = MultipartParser(
        boundary,
        {
            "on_part_begin": collector.on_part_begin,
            "on_header_field": collector.on_header_field,
            "on_header_value": collector.on_header_value,
            "on_header_end": collector.on_header_end,
            "on_headers_finished": collector.on_headers_finished,
            "on_part_data": collector.on_part_data,
            "on_part_end": collector.on_part_end,
        },
    )
    size = 0
    head = b""
    limit = FILE_MAX_BYTES
    try:
        async with aiofiles.open(tmp_path, "wb") as out:
            async for chunk in request.stream():
                parser.write(chunk)
                if collector.pending_file_bytes:
                    data = bytes(collector.pending_file_bytes)
                    collector.pending_file_bytes.clear()
                    if len(head) < 16:
                        head = (head + data)[:16]
                        if sniff_image(head):
                            limit = IMAGE_MAX_BYTES
                    size += len(data)
                    if size > limit:
                        raise image_too_large() if limit == IMAGE_MAX_BYTES else file_too_large()
                    await out.write(data)
            parser.finalize()
    except BaseException:
        await _discard(tmp_path)
        raise

    part = collector.file_part
    if part is None or not collector.file_done:
        await _discard(tmp_path)
        raise AppError(400, "validation_error", "A 'file' part is required")
    filename = sanitize_filename(part.filename or "")
    if not filename:
        await _discard(tmp_path)
        raise AppError(400, "validation_error", "The file has no name")
    if size == 0:
        await _discard(tmp_path)
        raise AppError(400, "empty_file", "The file is empty")
    raw_comment = collector.fields.get("comment", b"")
    if len(raw_comment) > _COMMENT_MAX_BYTES:
        await _discard(tmp_path)
        raise AppError(400, "validation_error", f"Comment must be at most {COMMENT_MAX_CHARS} characters")
    comment = raw_comment.decode("utf-8", "replace").strip()
    if len(comment) > COMMENT_MAX_CHARS:
        await _discard(tmp_path)
        raise AppError(400, "validation_error", f"Comment must be at most {COMMENT_MAX_CHARS} characters")
    return ReceivedFile(
        tmp_path=tmp_path,
        filename=filename,
        declared_type=part.content_type,
        size=size,
        head=head,
        comment=comment or None,
    )


async def _discard(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        await anyio.Path(path).unlink()


def sanitize_filename(raw: str) -> str:
    """Keep the user's name for display and Content-Disposition only: no path parts, no control
    characters, at most 255 characters (it never reaches the filesystem)."""
    # Browsers escape these three in multipart filenames (WHATWG HTML, "multipart/form-data encoding").
    raw = raw.replace("%22", '"').replace("%0D", "").replace("%0A", "")
    name = raw.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch >= " " and ch != "\x7f").strip()
    if name in (".", ".."):
        return ""
    if len(name) > 255:
        stem, dot, ext = name.rpartition(".")
        ext = ext if dot and len(ext) <= 16 else ""
        name = (stem if dot else name)[: 255 - len(ext) - (1 if ext else 0)] + (f".{ext}" if ext else "")
    return name
