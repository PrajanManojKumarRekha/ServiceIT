import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from fpdf import FPDF

from services.visualizations import generate_error_timeline_chart

load_dotenv()

logger = logging.getLogger(__name__)

_OUTPUT_DIR = Path(os.getenv("PDF_OUTPUT_DIR", "outputs"))


@dataclass(slots=True)
class SimilarIncidentEntry:
    trace_id: str
    root_cause: str
    similarity_score: float
    severity: str


@dataclass(slots=True)
class ReportPayload:
    incident_id: int
    trace_id: str
    service_name: str
    operation_name: str
    error_message: str
    duration_ms: float
    severity: str
    timestamp: datetime
    executive_summary: str
    root_cause: str
    recommendations: list[str]
    similar_incidents: list[SimilarIncidentEntry] = field(default_factory=list)
    chart_path: str | None = None


class IncidentReportPDF(FPDF):
    def header(self) -> None:
        self.set_x(self.l_margin)
        self.set_font("Helvetica", size=10)
        self.set_text_color(80, 80, 80)
        self.multi_cell(0, 5, "ServiceIT Incident Report")
        self.ln(2)

    def footer(self) -> None:
        self.set_y(-15)
        self.set_x(self.l_margin)
        self.set_font("Helvetica", size=9)
        self.set_text_color(110, 110, 110)
        self.multi_cell(0, 5, f"Page {self.page_no()}")


def _ensure_output_dir() -> Path:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return _OUTPUT_DIR


def _filename_for_incident(incident_id: int, timestamp: datetime) -> Path:
    safe_timestamp = timestamp.strftime("%Y%m%d_%H%M%S")
    return _ensure_output_dir() / f"incident_{incident_id}_{safe_timestamp}.pdf"


def _add_section_title(pdf: FPDF, title: str) -> None:
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", "B", 15)
    pdf.set_text_color(20, 20, 20)
    pdf.multi_cell(0, 8, title)
    pdf.ln(2)


def _add_body_text(pdf: FPDF, text: str, *, bold: bool = False) -> None:
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", "B" if bold else "", 11)
    pdf.set_text_color(30, 30, 30)
    pdf.multi_cell(0, 6, text)
    pdf.ln(1)


def _build_cover_page(pdf: FPDF, payload: ReportPayload) -> None:
    pdf.add_page()
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", "B", 24)
    pdf.set_text_color(18, 52, 86)
    pdf.multi_cell(0, 12, "ServiceIT")
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", size=14)
    pdf.set_text_color(45, 45, 45)
    pdf.multi_cell(0, 8, "Autonomous Local Incident Report")
    pdf.ln(8)
    _add_body_text(pdf, f"Incident ID: {payload.incident_id}", bold=True)
    _add_body_text(pdf, f"Timestamp: {payload.timestamp.isoformat()}")
    _add_body_text(pdf, f"Severity: {payload.severity}", bold=True)


def _build_executive_summary(pdf: FPDF, payload: ReportPayload) -> None:
    pdf.add_page()
    _add_section_title(pdf, "Executive Summary")
    _add_body_text(pdf, payload.executive_summary)


def _build_technical_details(pdf: FPDF, payload: ReportPayload) -> None:
    _add_section_title(pdf, "Technical Details")
    _add_body_text(pdf, f"Service Name: {payload.service_name}")
    _add_body_text(pdf, f"Operation Name: {payload.operation_name}")
    _add_body_text(pdf, f"Trace ID: {payload.trace_id}")
    _add_body_text(pdf, f"Error Message: {payload.error_message}")
    _add_body_text(pdf, f"Duration (ms): {payload.duration_ms:.2f}")


def _build_root_cause(pdf: FPDF, payload: ReportPayload) -> None:
    _add_section_title(pdf, "Root Cause Analysis")
    _add_body_text(pdf, payload.root_cause)


def _build_similar_incidents(pdf: FPDF, payload: ReportPayload) -> None:
    _add_section_title(pdf, "Similar Incidents")
    if not payload.similar_incidents:
        _add_body_text(pdf, "No similar incidents were available for comparison.")
        return

    for index, incident in enumerate(payload.similar_incidents[:3], start=1):
        _add_body_text(
            pdf,
            (
                f"{index}. Trace ID: {incident.trace_id}\n"
                f"Severity: {incident.severity}\n"
                f"Similarity Score: {incident.similarity_score:.4f}\n"
                f"Root Cause: {incident.root_cause}"
            ),
        )


def _build_recommendations(pdf: FPDF, payload: ReportPayload) -> None:
    _add_section_title(pdf, "Recommendations")
    for index, recommendation in enumerate(payload.recommendations, start=1):
        _add_body_text(pdf, f"{index}. {recommendation}")


def _build_charts_page(pdf: FPDF, payload: ReportPayload) -> None:
    pdf.add_page()
    _add_section_title(pdf, "Charts")
    if not payload.chart_path or not Path(payload.chart_path).exists():
        _add_body_text(pdf, "No chart data was available for this incident.")
        return

    _add_body_text(pdf, "Error timeline generated from recent trace activity.")
    pdf.image(payload.chart_path, x=15, y=55, w=180)


def generate_incident_report(payload: ReportPayload) -> str:
    output_path = _filename_for_incident(payload.incident_id, payload.timestamp)
    pdf = IncidentReportPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_title(f"Incident {payload.incident_id}")
    pdf.set_author("ServiceIT")

    _build_cover_page(pdf, payload)
    _build_executive_summary(pdf, payload)
    _build_technical_details(pdf, payload)
    _build_root_cause(pdf, payload)
    _build_similar_incidents(pdf, payload)
    _build_recommendations(pdf, payload)
    _build_charts_page(pdf, payload)

    pdf.output(os.fspath(output_path))
    logger.info("PDF written: %s", output_path)
    return os.fspath(output_path)


def generate_test_report() -> str:
    output_dir = _ensure_output_dir()
    chart_path = output_dir / "test_error_timeline.png"
    now = datetime.now(UTC)
    timestamps = [now - timedelta(minutes=15), now - timedelta(minutes=10), now - timedelta(minutes=5), now]
    error_counts = [1, 3, 2, 5]

    generated_chart = generate_error_timeline_chart(
        timestamps=timestamps,
        error_counts=error_counts,
        output_path=os.fspath(chart_path),
    )

    payload = ReportPayload(
        incident_id=999,
        trace_id="deadbeefcafebabe1234567890abcdef",
        service_name="orders-service",
        operation_name="POST /api/orders",
        error_message="database connection timeout while fetching order lock",
        duration_ms=1250.42,
        severity="HIGH",
        timestamp=now,
        executive_summary=(
            "The orders service failed while processing a write request because its database "
            "dependency did not respond in time. The error affected a latency-sensitive path and "
            "caused request failures visible to callers. Similar historical incidents indicate "
            "the issue is most likely tied to database saturation or connection pool exhaustion."
        ),
        root_cause=(
            "The most likely root cause is a database connection timeout triggered by saturation "
            "in the orders-service dependency path."
        ),
        recommendations=[
            "Inspect database saturation metrics and active connections during the failure window.",
            "Review orders-service connection pool limits and retry behavior.",
            "Add alerting for repeated timeout spikes on database-bound operations.",
        ],
        similar_incidents=[
            SimilarIncidentEntry(
                trace_id="11112222333344445555666677778888",
                root_cause="Connection pool exhaustion during peak checkout traffic.",
                similarity_score=0.9421,
                severity="HIGH",
            ),
            SimilarIncidentEntry(
                trace_id="9999aaaabbbbccccddddeeeeffff0000",
                root_cause="Primary database lock contention increased query latency.",
                similarity_score=0.9034,
                severity="MEDIUM",
            ),
            SimilarIncidentEntry(
                trace_id="1234567890abcdef1234567890abcdef",
                root_cause="Replica lag forced reads onto the saturated primary.",
                similarity_score=0.8812,
                severity="MEDIUM",
            ),
        ],
        chart_path=generated_chart,
    )

    generated_report = generate_incident_report(payload)
    test_report_path = output_dir / "test_report.pdf"
    Path(generated_report).replace(test_report_path)
    logger.info("PDF written: %s", test_report_path)
    return os.fspath(test_report_path)
