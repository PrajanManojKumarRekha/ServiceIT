import asyncio
import gzip
import json
import logging
import os
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
import redis.asyncio as aioredis
from redis import Redis
from rq import Queue
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_session
from backend.init_db import init_db
from backend.models import Incident, Trace
from backend.schemas import HealthResponse, IncidentFeedback, OTelPayload, OTelResource, OTelResourceSpan, OTelScopeSpans, OTelSpan, TraceResponse

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address)

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_UI_FILE = _PROJECT_ROOT / "ui" / "dashboard.html"
_OUTPUTS_DIR = Path(os.getenv("PDF_OUTPUT_DIR", os.fspath(_PROJECT_ROOT / "outputs")))
_REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
_EVENTS_CHANNEL = "serviceit:events"
_redis_connection = Redis.from_url(_REDIS_URL)
queue = Queue("serviceit-ingestion", connection=_redis_connection)
_ws_clients: list[WebSocket] = []


async def _redis_subscriber() -> None:
    """Subscribe to Redis pub/sub and relay events to WebSocket clients.

    Runs as a background asyncio task for the lifetime of the server.
    The RQ worker publishes to the same channel so live updates cross
    the process boundary without polling.
    """
    while True:
        try:
            client = aioredis.from_url(_REDIS_URL)
            pubsub = client.pubsub()
            await pubsub.subscribe(_EVENTS_CHANNEL)
            logger.info("Redis subscriber connected on channel: %s", _EVENTS_CHANNEL)
            async for message in pubsub.listen():
                if message["type"] == "message":
                    try:
                        event = json.loads(message["data"])
                        await _broadcast_event(event)
                    except Exception:
                        logger.warning("Failed to parse Redis event")
        except asyncio.CancelledError:
            return
        except Exception:
            logger.warning("Redis subscriber disconnected, retrying in 3s")
            await asyncio.sleep(3)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    subscriber_task = asyncio.create_task(_redis_subscriber())
    yield
    subscriber_task.cancel()
    with suppress(asyncio.CancelledError):
        await subscriber_task


app = FastAPI(title="ServiceIT", lifespan=lifespan)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:8000", "http://127.0.0.1:8000"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
app.mount("/outputs", StaticFiles(directory=os.fspath(_OUTPUTS_DIR)), name="outputs")


def _attribute_value_to_python(attribute: Any) -> str | int | float | bool:
    value = attribute.value
    kind = value.WhichOneof("value")
    if kind == "string_value":
        return value.string_value
    if kind == "int_value":
        return int(value.int_value)
    if kind == "double_value":
        return float(value.double_value)
    if kind == "bool_value":
        return bool(value.bool_value)
    return ""


def _attributes_to_dict(attributes: Any) -> dict[str, str | int | float | bool]:
    return {attribute.key: _attribute_value_to_python(attribute) for attribute in attributes}


def _extract_error_message(span: Any) -> str | None:
    if span.status.message:
        return span.status.message

    span_attributes = _attributes_to_dict(span.attributes)
    for key in ("serviceit.error_message", "exception.message", "error.message"):
        value = span_attributes.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    for event in span.events:
        event_attributes = _attributes_to_dict(event.attributes)
        for key in ("exception.message", "error.message"):
            value = event_attributes.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    return None


def _decode_request_body(raw_body: bytes, content_encoding: str | None) -> bytes:
    if content_encoding == "gzip" or raw_body.startswith(b"\x1f\x8b"):
        return gzip.decompress(raw_body)
    return raw_body


def _otlp_request_to_payload(raw_body: bytes) -> OTelPayload:
    export_request = ExportTraceServiceRequest()
    export_request.ParseFromString(raw_body)

    resource_spans_payload: list[OTelResourceSpan] = []
    for resource_span in export_request.resource_spans:
        resource_attributes = _attributes_to_dict(resource_span.resource.attributes)
        service_name = str(resource_attributes.get("service.name", "unknown")) or "unknown"
        scope_payloads: list[OTelScopeSpans] = []

        for scope_span in resource_span.scope_spans:
            spans_payload: list[OTelSpan] = []
            for span in scope_span.spans:
                spans_payload.append(
                    OTelSpan(
                        trace_id=span.trace_id.hex(),
                        span_id=span.span_id.hex(),
                        name=span.name[:256],
                        status_code=int(span.status.code),
                        error_message=_extract_error_message(span),
                        start_time_unix_nano=int(span.start_time_unix_nano),
                        end_time_unix_nano=int(span.end_time_unix_nano),
                    )
                )
            scope_payloads.append(OTelScopeSpans(spans=spans_payload))

        resource_spans_payload.append(
            OTelResourceSpan(
                resource=OTelResource(service_name=service_name[:256]),
                scope_spans=scope_payloads,
            )
        )

    return OTelPayload(resource_spans=resource_spans_payload)


async def _parse_trace_payload(request: Request) -> OTelPayload:
    raw_body = await request.body()
    decoded_body = _decode_request_body(raw_body, request.headers.get("content-encoding"))
    content_type = request.headers.get("content-type", "").lower()

    if "application/json" in content_type or decoded_body.lstrip().startswith(b"{"):
        return OTelPayload.model_validate_json(decoded_body)

    if "application/x-protobuf" in content_type or "application/octet-stream" in content_type:
        return _otlp_request_to_payload(decoded_body)

    raise HTTPException(status_code=415, detail="Unsupported trace payload format")


async def _broadcast_event(event: dict[str, Any]) -> None:
    message = json.dumps(event, default=str)
    dead_clients: list[WebSocket] = []
    for websocket in _ws_clients:
        try:
            await websocket.send_text(message)
        except Exception:
            dead_clients.append(websocket)

    for websocket in dead_clients:
        if websocket in _ws_clients:
            _ws_clients.remove(websocket)


def _is_error_span(span: OTelSpan) -> bool:
    return span.status_code == 2 or bool(span.error_message)


def _pdf_filename(pdf_path: str | None) -> str | None:
    return Path(pdf_path).name if pdf_path else None


@app.post("/v1/traces", response_model=TraceResponse, status_code=202)
@limiter.limit("100/minute")
async def ingest_traces(
    request: Request,
) -> TraceResponse:
    payload = await _parse_trace_payload(request)
    queue.enqueue("workers.ingestion_worker.process_trace", payload.model_dump())
    return TraceResponse(status="accepted")


@app.get("/health", response_model=HealthResponse)
async def health(session: AsyncSession = Depends(get_session)) -> HealthResponse:
    try:
        await session.execute(select(func.count()).select_from(Trace))
        db_status = "ok"
    except Exception as exc:
        logger.error("Health check DB error: %s", exc)
        db_status = "error"
    return HealthResponse(status="ok", db=db_status)


@app.get("/metrics")
async def metrics(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    now = datetime.now(UTC)
    hour_ago = now - timedelta(hours=1)
    five_minutes_ago = now - timedelta(minutes=5)
    error_condition = or_(Trace.status_code == 2, Trace.error_message.is_not(None))

    result = await session.execute(
        select(
            Trace.service_name,
            func.count().filter(Trace.created_at >= hour_ago).label("total_traces_hour"),
            func.count().filter(and_(Trace.created_at >= hour_ago, error_condition)).label("error_traces_hour"),
            func.count().filter(and_(Trace.created_at >= five_minutes_ago, error_condition)).label("recent_error_traces"),
        )
        .group_by(Trace.service_name)
        .order_by(Trace.service_name)
    )

    services: list[dict[str, Any]] = []
    for service_name, total_traces_hour, error_traces_hour, recent_error_traces in result.all():
        total = int(total_traces_hour or 0)
        errors = int(error_traces_hour or 0)
        recent_errors = int(recent_error_traces or 0)
        services.append(
            {
                "service_name": service_name,
                "total_traces_hour": total,
                "error_traces_hour": errors,
                "recent_error_traces": recent_errors,
                "error_rate": round((errors / total) if total else 0.0, 4),
            }
        )

    return {"services": services}


@app.get("/traces")
async def list_traces(
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    result = await session.execute(
        select(Trace)
        .order_by(Trace.created_at.desc())
        .limit(limit)
    )

    traces: list[dict[str, Any]] = []
    for trace in result.scalars():
        traces.append(
            {
                "service_name": trace.service_name,
                "trace_id": trace.trace_id,
                "operation_name": trace.operation_name,
                "status_code": trace.status_code,
                "error_message": trace.error_message,
                "duration_ms": round(trace.duration_ms, 2),
                "created_at": trace.created_at.isoformat() if trace.created_at else None,
                "is_first_failure": False,
            }
        )
    return traces


@app.get("/incidents")
async def list_incidents(
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    result = await session.execute(
        select(Incident, Trace.service_name, Trace.operation_name)
        .join(Trace, Trace.trace_id == Incident.trace_id)
        .order_by(Incident.created_at.desc())
        .limit(limit)
    )

    incidents: list[dict[str, Any]] = []
    for incident, service_name, operation_name in result.all():
        incidents.append(
            {
                "id": incident.id,
                "trace_id": incident.trace_id,
                "service_name": service_name,
                "operation_name": operation_name,
                "severity": incident.severity,
                "root_cause": incident.root_cause,
                "recommendations": json.loads(incident.recommendations),
                "pdf_path": incident.pdf_path,
                "pdf_filename": _pdf_filename(incident.pdf_path),
                "resolved": incident.resolved,
                "created_at": incident.created_at.isoformat() if incident.created_at else None,
            }
        )

    return incidents


@app.post("/v1/incidents/{incident_id}/feedback", status_code=200)
@limiter.limit("100/minute")
async def incident_feedback(
    incident_id: int,
    feedback: IncidentFeedback,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if incident_id < 1:
        raise HTTPException(status_code=422, detail="incident_id must be a positive integer")

    result = await session.execute(
        select(Incident).where(Incident.id == incident_id)
    )
    incident = result.scalar_one_or_none()
    if incident is None:
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")

    from services.embedding_service import reembed_incident
    await reembed_incident(incident_id, feedback.corrected_root_cause, session)

    incident.root_cause = feedback.corrected_root_cause
    incident.is_pinned = feedback.is_pinned
    incident.resolved = feedback.resolved

    await session.commit()
    logger.info("Feedback accepted for incident %d (pinned=%s, resolved=%s)", incident_id, feedback.is_pinned, feedback.resolved)
    return {"status": "feedback accepted", "incident_id": incident_id}


@app.get("/ui")
async def ui_dashboard() -> FileResponse:
    return FileResponse(_UI_FILE)


@app.websocket("/ws/traces")
async def websocket_traces(websocket: WebSocket) -> None:
    await websocket.accept()
    _ws_clients.append(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in _ws_clients:
            _ws_clients.remove(websocket)
