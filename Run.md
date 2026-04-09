# ServiceIT Run Guide

This guide covers:

1. Starting the ServiceIT stack
2. Opening the frontend dashboard
3. Running the built-in demo scenario
4. Connecting your own microservices to ServiceIT
5. Where to inspect incidents and PDF reports

## 1. Prerequisites

- Docker Desktop running
- A valid Gemini API key in `.env`
- Python 3.11 available on the host if you want to run the demo outside Docker

The `.env` file must at least contain:

```env
POSTGRES_USER=serviceit
POSTGRES_PASSWORD=your_password
POSTGRES_DB=serviceit_db
POSTGRES_HOST=serviceit-db
POSTGRES_PORT=5432

GEMINI_API_KEY=your_valid_key
GEMINI_MODEL=gemini-1.5-flash
GEMINI_EMBEDDING_MODEL=models/text-embedding-004

APP_ENV=development
PDF_OUTPUT_DIR=/app/outputs
LOG_LEVEL=INFO
```

## 2. Start ServiceIT

From the repo root:

```powershell
docker compose up -d --build
```

This starts:

- PostgreSQL with pgvector
- the FastAPI backend
- the OpenTelemetry collector

## 3. Open The Frontend

Open this in your browser:

```text
http://localhost:8000/ui
```

The dashboard shows:

- service health pills
- live trace stream
- incident queue
- PDF history table

Useful API endpoints:

- `http://localhost:8000/ui`
- `http://localhost:8000/metrics`
- `http://localhost:8000/traces?limit=50`
- `http://localhost:8000/incidents?limit=10`

## 4. Run The Built-In Demo

The demo emits four sample services:

- `orders-service` — failed
- `inventory-service` — failed
- `billing-service` — passed
- `checkout-gateway` — slow/stalled

Run it from the host:

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" examples\microservice_demo.py
```

Expected result in the UI within a few seconds:

- two services show failed activity
- one service shows healthy traffic
- one service shows a slow trace
- two incidents appear
- PDF links appear in the incident queue and history table

The generated PDFs are written to:

```text
./outputs/
```

## 5. Connect Your Own Microservices

Your services should export OpenTelemetry traces to the local collector:

- gRPC endpoint: `localhost:4317`
- HTTP endpoint: `localhost:4318`

If your microservice runs on the host machine, point its OTLP exporter to:

```text
localhost:4317
```

If your microservice runs in Docker on the same Compose network, point it to:

```text
otel-collector:4317
```

### What to include in your spans

ServiceIT diagnoses failures best when spans include:

- `service.name`
- operation/span name
- proper error status
- error message in span status or exception attributes
- realistic timing

The backend extracts failures from:

- span status code
- `serviceit.error_message`
- `exception.message`
- span status description/message

### Example Python microservice setup

```python
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Status, StatusCode

provider = TracerProvider(
    resource=Resource.create({"service.name": "my-service"})
)
provider.add_span_processor(
    BatchSpanProcessor(
        OTLPSpanExporter(endpoint="localhost:4317", insecure=True)
    )
)
trace.set_tracer_provider(provider)

tracer = trace.get_tracer("my-service")

with tracer.start_as_current_span("GET /api/example") as span:
    try:
        raise TimeoutError("database connection timeout")
    except TimeoutError as exc:
        span.set_attribute("serviceit.error_message", str(exc))
        span.record_exception(exc)
        span.set_status(Status(StatusCode.ERROR, str(exc)))
```

## 6. How Diagnosis Works

When an error trace arrives:

1. ServiceIT stores the trace in PostgreSQL
2. It creates an embedding for the failure text
3. It runs the diagnostic agent
4. It searches for similar incidents
5. It generates a PDF incident report
6. It pushes updates to the dashboard

## 7. Where To Find Reports

PDF reports are available:

- in the UI incident queue
- in the UI PDF history table
- directly on disk in `./outputs`
- directly over HTTP at `/outputs/<filename>`

Example:

```text
http://localhost:8000/outputs/incident_1_20260407_155633.pdf
```

## 8. If The UI Looks Empty

Check these in order:

1. Make sure the stack is up:

```powershell
docker compose ps
```

2. Open:

```text
http://localhost:8000/ui
```

3. Run the demo:

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" examples\microservice_demo.py
```

4. Check backend logs:

```powershell
docker compose logs --tail 100 serviceit-api
docker compose logs --tail 100 otel-collector
```

5. Check the incident API:

```text
http://localhost:8000/incidents?limit=10
```

If incidents appear there, the UI should also render them.
