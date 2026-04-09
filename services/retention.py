"""TTL-based purge of raw traces (>7 days) and unpinned incidents (>90 days)."""
import asyncio
import logging

from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError

from backend.database import AsyncSessionLocal
from backend.models import Incident, Trace

logger = logging.getLogger(__name__)

TRACE_TTL_DAYS = 7
INCIDENT_TTL_DAYS = 90


async def purge_old_traces(session) -> int:
    """Delete traces older than 7 days that have no linked incident."""
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import not_, exists

    cutoff = datetime.now(timezone.utc) - timedelta(days=TRACE_TTL_DAYS)

    linked_subquery = select(Incident.trace_id).where(
        Incident.trace_id == Trace.trace_id
    )

    stmt = delete(Trace).where(
        Trace.created_at < cutoff,
        ~exists(linked_subquery),
    )

    result = await session.execute(stmt)
    return result.rowcount


async def purge_old_incidents(session) -> int:
    """Delete unpinned incidents older than 90 days."""
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(days=INCIDENT_TTL_DAYS)

    stmt = delete(Incident).where(
        Incident.created_at < cutoff,
        Incident.is_pinned == False,  # noqa: E712 — SQLAlchemy requires == not 'is'
    )

    result = await session.execute(stmt)
    return result.rowcount


async def main() -> None:
    """Run both purge passes sequentially."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    async with AsyncSessionLocal() as session:
        try:
            trace_count = await purge_old_traces(session)
            await session.commit()
            logger.info("Purged %d raw traces older than %d days", trace_count, TRACE_TTL_DAYS)
        except SQLAlchemyError as e:
            logger.error("DB error purging old traces: %s", e)
            await session.rollback()
        except Exception:
            logger.exception("Unexpected error purging old traces")
            await session.rollback()

        try:
            incident_count = await purge_old_incidents(session)
            await session.commit()
            logger.info(
                "Purged %d unpinned incidents older than %d days",
                incident_count,
                INCIDENT_TTL_DAYS,
            )
        except SQLAlchemyError as e:
            logger.error("DB error purging old incidents: %s", e)
            await session.rollback()
        except Exception:
            logger.exception("Unexpected error purging old incidents")
            await session.rollback()


if __name__ == "__main__":
    asyncio.run(main())
