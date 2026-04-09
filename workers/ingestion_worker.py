import asyncio
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError

from backend.database import AsyncSessionLocal
from backend.models import Incident, Trace
from backend.schemas import OTelPayload
from services.diagnostic_agent import run_diagnostic_agent
from services.embedding_service import embed_text
from services.incident_processor import (
    build_trace_summary,
    publish_event,
    search_similar_incidents,
)
from services.report_generator import (
    ReportPayload,
    SimilarIncidentEntry,
    generate_incident_report,
)
from services.visualizations import generate_error_timeline_chart

load_dotenv()

logger = logging.getLogger(__name__)

_OUTPUTS_DIR = Path(os.getenv("PDF_OUTPUT_DIR", "outputs"))


def _is_error_span(status_code: int, error_message: str | None) -> bool:
    return status_code == 2 or bool(error_message)


async def _is_first_failure(service_name: str) -> bool:
    recent_window = datetime.now(UTC) - timedelta(minutes=5)
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(func.count())
            .select_from(Trace)
            .where(
                Trace.service_name == service_name,
                Trace.created_at >= recent_window,
                or_(Trace.status_code == 2, Trace.error_message.is_not(None)),
            )
        )
        return int(result.scalar_one() or 0) == 0


async def _generate_chart_for_service(service_name: str, trace_id: str) -> str | None:
    since = datetime.now(UTC) - timedelta(hours=1)
    bucket = func.date_trunc("minute", Trace.created_at)
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(
                bucket.label("bucket"),
                func.count().label("error_count"),
            )
            .where(
                Trace.service_name == service_name,
                Trace.created_at >= since,
                or_(Trace.status_code == 2, Trace.error_message.is_not(None)),
            )
            .group_by(bucket)
            .order_by(bucket)
        )
        rows = result.all()

    if not rows:
        return None

    timestamps = [row.bucket for row in rows if row.bucket is not None]
    error_counts = [int(row.error_count or 0) for row in rows if row.bucket is not None]
    if not timestamps:
        return None

    chart_path = _OUTPUTS_DIR / f"timeline_{trace_id}.png"
    return generate_error_timeline_chart(
        timestamps=timestamps,
        error_counts=error_counts,
        output_path=os.fspath(chart_path),
    )


def _build_executive_summary(trace: Trace, diagnosis: dict[str, object], similar_count: int) -> str:
    service_summary = (
        f"The {trace.service_name} service failed while executing {trace.operation_name}. "
        f"The request ended with status code {trace.status_code} after {trace.duration_ms:.2f} ms."
    )
    error_summary = (
        f"The captured error was: {trace.error_message or 'No explicit message was attached to the span.'} "
        f"The agent assessed the incident severity as {diagnosis['severity']}."
    )
    history_summary = (
        f"{similar_count} similar incidents were found in historical data, which improved confidence in the diagnosis. "
        "The attached report includes technical details, prior matches, and recommended actions."
    )
    return " ".join([service_summary, error_summary, history_summary])


def _pdf_filename(pdf_path: str | None) -> str | None:
    return Path(pdf_path).name if pdf_path else None


async def _persist_incident(trace: Trace) -> None:
    async with AsyncSessionLocal() as session:
        db_trace = await session.get(Trace, trace.id)
        if db_trace is None:
            return

        logger.info("Agent started investigation")
        diagnosis = await run_diagnostic_agent(session, db_trace)
        similar_matches = await search_similar_incidents(
            session,
            db_trace.error_message or build_trace_summary(db_trace),
            limit=3,
        )
        chart_path = await _generate_chart_for_service(db_trace.service_name, db_trace.trace_id)

        severity_str = str(diagnosis["severity"]).upper()
        severity_rank = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}.get(severity_str, 1)
        incident = Incident(
            trace_id=db_trace.trace_id,
            root_cause=str(diagnosis["root_cause"]),
            severity=severity_str,
            recommendations=json.dumps(diagnosis["recommendations"]),
            pdf_path=None,
            resolved=False,
            is_pinned=False,
            severity_rank=severity_rank,
        )
        session.add(incident)
        await session.flush()

        report_payload = ReportPayload(
            incident_id=incident.id,
            trace_id=db_trace.trace_id,
            service_name=db_trace.service_name,
            operation_name=db_trace.operation_name,
            error_message=db_trace.error_message or "No explicit error message.",
            duration_ms=db_trace.duration_ms,
            severity=str(diagnosis["severity"]),
            timestamp=db_trace.created_at or datetime.now(UTC),
            executive_summary=_build_executive_summary(db_trace, diagnosis, len(similar_matches)),
            root_cause=str(diagnosis["root_cause"]),
            recommendations=[str(item) for item in diagnosis["recommendations"]],
            similar_incidents=[
                SimilarIncidentEntry(
                    trace_id=match.trace_id,
                    root_cause=match.root_cause,
                    similarity_score=match.similarity_score,
                    severity=match.severity,
                )
                for match in similar_matches[:3]
            ],
            chart_path=chart_path,
        )
        incident.pdf_path = generate_incident_report(report_payload)
        await session.commit()
        await session.refresh(incident)

        logger.info("Root cause identified + severity: %s", incident.severity)
        logger.info("PDF written: %s", incident.pdf_path)
        await publish_event(
            {
                "event": "incident",
                "incident_id": incident.id,
                "service_name": db_trace.service_name,
                "trace_id": incident.trace_id,
                "severity": incident.severity,
                "root_cause": incident.root_cause,
                "pdf_filename": _pdf_filename(incident.pdf_path),
                "created_at": incident.created_at.isoformat() if incident.created_at else None,
            }
        )


async def _persist_trace(
    service_name: str,
    span_name: str,
    trace_id: str,
    span_id: str,
    status_code: int,
    error_message: str | None,
    duration_ms: float,
    embedding: list[float] | None,
    is_first_failure: bool,
) -> Trace:
    async with AsyncSessionLocal() as session:
        trace = Trace(
            trace_id=trace_id,
            span_id=span_id,
            service_name=service_name,
            operation_name=span_name,
            error_message=error_message,
            status_code=status_code,
            duration_ms=duration_ms,
            embedding=embedding,
        )
        session.add(trace)
        await session.commit()
        await session.refresh(trace)

    await publish_event(
        {
            "event": "trace",
            "service_name": service_name,
            "trace_id": trace.trace_id,
            "operation_name": trace.operation_name,
            "status_code": trace.status_code,
            "error_message": trace.error_message,
            "duration_ms": round(trace.duration_ms, 2),
            "is_first_failure": is_first_failure,
        }
    )
    logger.info("process_trace stored trace: %s", trace.trace_id)
    return trace


def process_trace(payload_dict: dict) -> None:
    try:
        payload = OTelPayload(**payload_dict)
        asyncio.run(_process_trace_async(payload))

    except ValidationError as exc:
        logger.warning("Invalid trace payload in worker: %s", exc)
        return
    except SQLAlchemyError as exc:
        logger.error("DB error processing trace in worker: %s", exc)
        return
    except Exception:
        logger.exception("Unexpected error in ingestion worker process_trace")
        return


async def _process_trace_async(payload: OTelPayload) -> None:
    for resource_span in payload.resource_spans:
        service_name = resource_span.resource.service_name if resource_span.resource else "unknown"
        for scope in resource_span.scope_spans:
            for span in scope.spans:
                duration_ms = (
                    span.end_time_unix_nano - span.start_time_unix_nano
                ) / 1_000_000
                is_error = _is_error_span(span.status_code, span.error_message)
                is_first_failure = await _is_first_failure(service_name) if is_error else False
                embedding = None
                if is_error:
                    try:
                        embedding = await embed_text(
                            "\n".join(
                                [
                                    f"Service: {service_name}",
                                    f"Operation: {span.name}",
                                    f"Status code: {span.status_code}",
                                    f"Error: {span.error_message or 'No explicit error message.'}",
                                ]
                            )
                        )
                    except Exception:
                        logger.exception("Embedding generation failed in worker for trace %s", span.trace_id)
                        embedding = None

                stored_trace = await _persist_trace(
                    service_name=service_name,
                    span_name=span.name,
                    trace_id=span.trace_id,
                    span_id=span.span_id,
                    status_code=span.status_code,
                    error_message=span.error_message,
                    duration_ms=duration_ms,
                    embedding=embedding,
                    is_first_failure=is_first_failure,
                )

                if is_error:
                    await _persist_incident(stored_trace)
