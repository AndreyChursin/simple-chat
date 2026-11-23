"""Periodic housekeeping (runs inside the single backend process)."""

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import delete, or_

from app.db.models import PasswordResetToken, UserSession
from app.db.session import SessionLocal
from app.services import files
from app.services.auth import utcnow

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 3600


async def purge_expired() -> None:
    now = utcnow()
    async with SessionLocal() as db:
        await db.execute(delete(UserSession).where(UserSession.expires_at <= now))
        await db.execute(
            delete(PasswordResetToken).where(
                or_(
                    PasswordResetToken.expires_at <= now,
                    PasswordResetToken.used_at <= now - timedelta(days=1),
                )
            )
        )
        await db.commit()


async def collect_file_garbage() -> None:
    async with SessionLocal() as db:
        counts = await files.collect_garbage(db)
    if any(counts.values()):
        logger.info("File GC: %s", counts)


async def run_forever() -> None:
    """Hourly, and once at start (docs/ARCHITECTURE.md §5.4)."""
    while True:
        for job in (purge_expired, collect_file_garbage):
            try:
                await job()
            except Exception:
                logger.exception("Housekeeping job %s failed", job.__name__)
        await asyncio.sleep(INTERVAL_SECONDS)
