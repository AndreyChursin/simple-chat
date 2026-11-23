"""Upload storage on disk (docs/ARCHITECTURE.md §5). Paths never contain user-supplied data."""

import logging
import shutil
from pathlib import Path

import anyio

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def room_dir(room_id: int) -> Path:
    return get_settings().upload_dir / "rooms" / str(int(room_id))


async def remove_room_dir(room_id: int) -> None:
    """Remove every stored file of a deleted room. Called after the DB commit; failures are logged and
    left to the hourly GC instead of failing the request."""
    path = room_dir(room_id)

    def _rmtree() -> None:
        if path.exists():
            shutil.rmtree(path)

    try:
        await anyio.to_thread.run_sync(_rmtree)
    except OSError:
        logger.exception("Could not remove %s; GC will retry", path)
