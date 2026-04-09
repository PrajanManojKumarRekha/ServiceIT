import logging
import time
from dataclasses import dataclass
from pathlib import Path

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Status, StatusCode

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "outputs"
_PDF_GLOB = "incident_*.pdf"


@dataclass(frozen=True, slots=True)
class DemoScenario:
    service_name: str
    operation_name: str
    duration_seconds: float
    error_message: str | None
    status_code: StatusCode
    attributes: dict[str, str | int | float]


def _existing_pdf_names() -> set[str]:
    return {path.name for path in _OUTPUT_DIR.glob(_PDF_GLOB)}


def _new_pdf_names(previous_names: set[str]) -> list[str]:
    return sorted({path.name for path in _OUTPUT_DIR.glob(_PDF_GLOB)} - previous_names)


def _build_provider(service_name: str) -> TracerProvider:
    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": service_name,
                "service.version": "1.0.0-demo",
                "deployment.environment": "development",
            }
        )
    )
    exporter = OTLPSpanExporter(endpoint="localhost:4317", insecure=True)
    processor = BatchSpanProcessor(
        exporter,
        max_export_batch_size=1,
        max_queue_size=1,
        schedule_delay_millis=400,
    )
    provider.add_span_processor(processor)
    return provider


def _emit_scenario(scenario: DemoScenario) -> None:
    provider = _build_provider(scenario.service_name)
    tracer = provider.get_tracer("serviceit.microservice_demo")

    logger.info("Emitting %s trace for %s", scenario.operation_name, scenario.service_name)
    try:
        with tracer.start_as_current_span(scenario.operation_name) as span:
            for key, value in scenario.attributes.items():
                span.set_attribute(key, value)

            if scenario.error_message:
                span.set_attribute("serviceit.error_message", scenario.error_message)
                span.set_attribute("exception.message", scenario.error_message)
                span.record_exception(TimeoutError(scenario.error_message))
                span.set_status(
                    Status(
                        status_code=scenario.status_code,
                        description=scenario.error_message,
                    )
                )
            else:
                span.set_status(Status(status_code=scenario.status_code))

            time.sleep(scenario.duration_seconds)
    finally:
        provider.force_flush()
        provider.shutdown()


def _wait_for_incident_pdfs(previous_names: set[str], expected_count: int, timeout_seconds: int = 60) -> list[str]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        new_names = _new_pdf_names(previous_names)
        if len(new_names) >= expected_count:
            return new_names
        time.sleep(1.0)
    raise RuntimeError(
        "The demo traces were emitted, but the expected incident PDFs were not generated in time."
    )


def main() -> None:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    previous_pdf_names = _existing_pdf_names()

    scenarios = [
        DemoScenario(
            service_name="orders-service",
            operation_name="POST /api/orders",
            duration_seconds=1.2,
            error_message="database connection timeout while fetching order lock",
            status_code=StatusCode.ERROR,
            attributes={
                "db.system": "postgresql",
                "db.operation": "SELECT",
                "db.timeout_ms": 1200,
            },
        ),
        DemoScenario(
            service_name="inventory-service",
            operation_name="PATCH /api/inventory/reserve",
            duration_seconds=1.5,
            error_message="redis cache timeout during stock reservation",
            status_code=StatusCode.ERROR,
            attributes={
                "cache.system": "redis",
                "cache.operation": "GET",
                "cache.timeout_ms": 1500,
            },
        ),
        DemoScenario(
            service_name="billing-service",
            operation_name="GET /api/billing/health",
            duration_seconds=0.2,
            error_message=None,
            status_code=StatusCode.OK,
            attributes={
                "http.method": "GET",
                "http.route": "/api/billing/health",
            },
        ),
        DemoScenario(
            service_name="checkout-gateway",
            operation_name="POST /api/checkout/submit",
            duration_seconds=2.4,
            error_message=None,
            status_code=StatusCode.OK,
            attributes={
                "http.method": "POST",
                "http.route": "/api/checkout/submit",
                "serviceit.stalled_reason": "waiting on downstream payment retries",
            },
        ),
    ]

    logger.info("Sending 4 demo microservice traces to localhost:4317")
    for scenario in scenarios:
        _emit_scenario(scenario)

    logger.info("Waiting for 2 incident PDFs in %s", _OUTPUT_DIR)
    pdf_names = _wait_for_incident_pdfs(previous_pdf_names, expected_count=2)

    logger.info("Demo complete. Generated PDFs: %s", ", ".join(pdf_names))
    print("\n".join(pdf_names))


if __name__ == "__main__":
    main()
