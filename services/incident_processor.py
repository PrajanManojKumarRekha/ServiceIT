import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Incident, Trace
from services.embedding_service import embed_text

logger = logging.getLogger(__name__)

_REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
_EVENTS_CHANNEL = "serviceit:events"

@dataclass(slots=True)
class SimilarIncidentMatch:
    trace_id: str
    service_name: str
    root_cause: str
    severity: str
    similarity_score: float
    recommendations: list[str]
    created_at: datetime | None


def build_trace_summary(trace: Trace) -> str:
    error_message = trace.error_message or "No explicit error message."
    return (
        f"Service: {trace.service_name}\n"
        f"Operation: {trace.operation_name}\n"
        f"Status code: {trace.status_code}\n"
        f"Duration ms: {trace.duration_ms:.2f}\n"
        f"Error: {error_message}"
    )


def _parse_recommendations(raw_recommendations: str) -> list[str]:
    try:
        parsed = json.loads(raw_recommendations)
    except json.JSONDecodeError:
        return [raw_recommendations]

    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    if isinstance(parsed, dict):
        return [f"{key}: {value}" for key, value in parsed.items()]
    return [str(parsed)]


async def search_similar_incidents(
    session: AsyncSession,
    error_summary: str,
    limit: int = 3,
) -> list[SimilarIncidentMatch]:
    cleaned_summary = error_summary.strip()
    if not cleaned_summary:
        return []

    query_embedding = await embed_text(cleaned_summary)
    similarity_expression = 1 - Trace.embedding.cosine_distance(query_embedding)

    result = await session.execute(
        select(Trace, Incident, similarity_expression.label("similarity_score"))
        .join(Incident, Incident.trace_id == Trace.trace_id)
        .where(Trace.embedding.is_not(None))
        .order_by(Trace.embedding.cosine_distance(query_embedding))
        .limit(limit)
    )

    matches: list[SimilarIncidentMatch] = []
    for trace, incident, similarity_score in result.all():
        matches.append(
            SimilarIncidentMatch(
                trace_id=trace.trace_id,
                service_name=trace.service_name,
                root_cause=incident.root_cause,
                severity=incident.severity,
                similarity_score=float(similarity_score or 0.0),
                recommendations=_parse_recommendations(incident.recommendations),
                created_at=incident.created_at,
            )
        )

    return matches


def format_similar_incidents(matches: list[SimilarIncidentMatch]) -> str:
    if not matches:
        return "No similar incidents found."

    lines: list[str] = []
    for index, match in enumerate(matches, start=1):
        timestamp = match.created_at.isoformat() if match.created_at else "unknown"
        recommendations = "; ".join(match.recommendations[:3]) or "None"
        lines.append(
            "\n".join(
                [
                    f"Incident {index}",
                    f"trace_id: {match.trace_id}",
                    f"service_name: {match.service_name}",
                    f"severity: {match.severity}",
                    f"similarity_score: {match.similarity_score:.4f}",
                    f"created_at: {timestamp}",
                    f"root_cause: {match.root_cause}",
                    f"recommendations: {recommendations}",
                ]
            )
        )

    return "\n\n".join(lines)


async def get_service_metrics(
    session: AsyncSession,
    service_name: str,
    lookback_hours: int = 1,
) -> dict[str, float | int | str]:
    cleaned_name = service_name.strip()
    if not cleaned_name:
        return {
            "service_name": "",
            "window_hours": lookback_hours,
            "error_count": 0,
            "total_traces": 0,
            "error_rate": 0.0,
        }

    since = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    total_stmt = select(func.count()).select_from(Trace).where(
        Trace.service_name == cleaned_name,
        Trace.created_at >= since,
    )
    error_stmt = select(func.count()).select_from(Trace).where(
        Trace.service_name == cleaned_name,
        Trace.created_at >= since,
        Trace.status_code > 0,
    )

    total_result = await session.execute(total_stmt)
    error_result = await session.execute(error_stmt)
    total_traces = int(total_result.scalar_one() or 0)
    error_count = int(error_result.scalar_one() or 0)
    error_rate = (error_count / total_traces) if total_traces else 0.0

    return {
        "service_name": cleaned_name,
        "window_hours": lookback_hours,
        "error_count": error_count,
        "total_traces": total_traces,
        "error_rate": round(error_rate, 4),
    }


async def publish_event(event: dict[str, Any]) -> None:
    """Publish a trace or incident event to the Redis pub/sub channel.

    The FastAPI process subscribes to this channel and relays messages
    to connected WebSocket clients. Using Redis decouples the RQ worker
    process from the API process so live dashboard updates work correctly.
    """
    try:
        client = aioredis.from_url(_REDIS_URL)
        await client.publish(_EVENTS_CHANNEL, json.dumps(event, default=str))
        await client.aclose()
    except Exception:
        logger.warning("Redis publish failed, event dropped: %s", event.get("event"))
