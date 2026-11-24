"""Periodic housekeeping (runs inside the single backend process)."""

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import delete, or_

from app.core.ratelimit import rate_limiter
from app.db.models import PasswordResetToken, UserSession
from app.db.session import SessionLocal
from app.services import files
from app.services.auth import utcnow
from app.ws.manager import manager

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


async def prune_memory() -> None:
    rate_limiter.prune()


async def run_forever() -> None:
    """Hourly, and once at start (docs/ARCHITECTURE.md §5.4)."""
    while True:
        for job in (purge_expired, collect_file_garbage, prune_memory):
            try:
                await job()
            except Exception:
                logger.exception("Housekeeping job %s failed", job.__name__)
        await asyncio.sleep(INTERVAL_SECONDS)


METRICS_INTERVAL_SECONDS = 60
metrics_logger = logging.getLogger("app.metrics")


async def log_metrics_forever() -> None:
    """Once a minute: connections, online/AFK users, p95 queue→socket delay (docs/ARCHITECTURE.md §7)."""
    while True:
        await asyncio.sleep(METRICS_INTERVAL_SECONDS)
        try:
            metrics_logger.info("metrics", extra=manager.snapshot_metrics())
        except Exception:
            logger.exception("metrics snapshot failed")
