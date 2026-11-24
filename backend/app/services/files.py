"""Upload storage on disk (docs/ARCHITECTURE.md §5). Paths never contain user-supplied data:

    rooms/{room_id}/{yyyy}/{mm}/{uuid4}
    dms/{dm_id}/{yyyy}/{mm}/{uuid4}
    tmp/{uuid4}.part                       (while uploading)

Disk operations run in worker threads. Removal happens after the DB commit; a failure is logged and left
to the hourly GC, it never rolls back the DB change.
"""

import logging
import os
import shutil
import time
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import anyio
from PIL import Image
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import Attachment, DirectConversation, Room

logger = logging.getLogger(__name__)

ChatType = Literal["room", "dm"]
CHAT_DIRS: dict[ChatType, str] = {"room": "rooms", "dm": "dms"}
IMAGE_MIME = {"png": "image/png", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}
PENDING_TTL = timedelta(hours=24)
ORPHAN_MIN_AGE_SECONDS = 3600
# Pillow refuses absurd dimensions (decompression bombs); we only read the header anyway.
Image.MAX_IMAGE_PIXELS = 100_000_000


def root() -> Path:
    return get_settings().upload_dir


def room_dir(room_id: int) -> Path:
    return root() / "rooms" / str(int(room_id))


def chat_dir(chat_type: ChatType, chat_id: int) -> Path:
    return root() / CHAT_DIRS[chat_type] / str(int(chat_id))


def new_stored_path(chat_type: ChatType, chat_id: int) -> str:
    now = datetime.now(UTC)
    return f"{CHAT_DIRS[chat_type]}/{int(chat_id)}/{now:%Y}/{now:%m}/{uuid.uuid4().hex}"


def absolute(stored_path: str) -> Path:
    """UPLOAD_DIR / stored_path, refusing anything that would escape the upload root."""
    base = root().resolve()
    path = (base / stored_path).resolve()
    if not path.is_relative_to(base):
        raise ValueError(f"stored path escapes the upload root: {stored_path!r}")
    return path


# --- images -------------------------------------------------------------------------------------------


def sniff_image(head: bytes) -> str | None:
    """Image type by magic bytes (never by name or declared MIME)."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


def _inspect_image(path: Path, kind: str) -> tuple[int, int] | None:
    try:
        with Image.open(path) as img:
            if (img.format or "").lower() != kind:
                return None
            img.verify()
        with Image.open(path) as img:  # verify() leaves the image unusable; reopen for the size
            width, height = img.size
            return width, height
    except Exception:
        return None


async def inspect_image(path: Path, head: bytes) -> tuple[str, int, int] | None:
    """(mime, width, height) if the magic bytes say image and Pillow agrees, else None."""
    kind = sniff_image(head)
    if kind is None:
        return None
    size = await anyio.to_thread.run_sync(_inspect_image, path, kind)
    return (IMAGE_MIME[kind], size[0], size[1]) if size else None


# --- moving / removing --------------------------------------------------------------------------------


async def store(tmp_path: Path, stored_path: str) -> None:
    target = absolute(stored_path)

    def _move() -> None:
        for attempt in range(2):
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.replace(tmp_path, target)
                return
            except FileNotFoundError:
                # the GC removed the (empty, old) month directory between mkdir and replace
                if attempt or not tmp_path.exists():
                    raise

    await anyio.to_thread.run_sync(_move)


async def remove_files(stored_paths: Iterable[str]) -> None:
    paths = list(stored_paths)
    if not paths:
        return

    def _unlink() -> None:
        for stored in paths:
            try:
                absolute(stored).unlink(missing_ok=True)
            except OSError:
                logger.exception("Could not remove %s; GC will retry", stored)

    await anyio.to_thread.run_sync(_unlink)


async def discard(path: Path) -> None:
    await anyio.to_thread.run_sync(lambda: path.unlink(missing_ok=True))


async def _rmtree(path: Path) -> None:
    def _remove() -> None:
        if path.exists():
            shutil.rmtree(path)

    try:
        await anyio.to_thread.run_sync(_remove)
    except OSError:
        logger.exception("Could not remove %s; GC will retry", path)


async def remove_room_dir(room_id: int) -> None:
    """Every stored file of a deleted room. Called after the DB commit."""
    await _rmtree(room_dir(room_id))


async def remove_dm_dir(dm_id: int) -> None:
    await _rmtree(chat_dir("dm", dm_id))


# --- garbage collection -------------------------------------------------------------------------------


def _newest_mtime(path: Path) -> float:
    newest = path.stat().st_mtime
    for p in path.rglob("*"):
        newest = max(newest, p.stat().st_mtime)
    return newest


async def collect_garbage(db: AsyncSession, *, now: datetime | None = None) -> dict[str, int]:
    """Hourly + at start (docs/ARCHITECTURE.md §5.4):
    1) pending attachments older than 24 h (row + file);
    2) files on disk without a row, older than 1 h (a crash between commit and unlink);
    3) `tmp/*.part` older than 1 h;
    4) directories of rooms / DMs that no longer exist — only when nothing in them changed for 1 h, so
       a chat created (and uploaded to) after the id snapshot below is never touched;
    5) empty `yyyy/mm` directories older than 1 h."""
    now = now or datetime.now(UTC)
    stale = list(
        await db.scalars(
            delete(Attachment)
            .where(Attachment.message_id.is_(None), Attachment.created_at < now - PENDING_TTL)
            .returning(Attachment.stored_path)
        )
    )
    await db.commit()
    await remove_files(stale)

    known = set(await db.scalars(select(Attachment.stored_path)))
    rooms = {str(r) for r in await db.scalars(select(Room.id))}
    dms = {str(d) for d in await db.scalars(select(DirectConversation.id))}
    base = root()
    cutoff = time.time() - ORPHAN_MIN_AGE_SECONDS

    def _sweep() -> dict[str, int]:
        counts = {"orphans": 0, "parts": 0, "chat_dirs": 0, "empty_dirs": 0}
        for kind, alive in (("rooms", rooms), ("dms", dms)):
            top = base / kind
            if not top.is_dir():
                continue
            for chat in top.iterdir():
                if chat.name not in alive:
                    if _newest_mtime(chat) < cutoff:
                        shutil.rmtree(chat, ignore_errors=True)
                        counts["chat_dirs"] += 1
                    continue
                # mtimes before this sweep touches anything (removing a child bumps its parent's)
                dir_mtimes = {p: p.stat().st_mtime for p in chat.rglob("*") if p.is_dir()}
                for path in chat.rglob("*"):
                    orphan = path.is_file() and path.relative_to(base).as_posix() not in known
                    if orphan and path.stat().st_mtime < cutoff:
                        path.unlink(missing_ok=True)
                        counts["orphans"] += 1
                # deepest first, so an emptied month can empty its year
                for path in sorted(dir_mtimes, key=lambda p: len(p.parts), reverse=True):
                    if not any(path.iterdir()) and dir_mtimes[path] < cutoff:
                        path.rmdir()
                        counts["empty_dirs"] += 1
        tmp = base / "tmp"
        if tmp.is_dir():
            for part in tmp.glob("*.part"):
                if part.stat().st_mtime < cutoff:
                    part.unlink(missing_ok=True)
                    counts["parts"] += 1
        return counts

    counts = await anyio.to_thread.run_sync(_sweep)
    counts["pending"] = len(stale)
    return counts
