# ServiceIT

Local-first incident diagnostics for microservices.

ServiceIT receives OpenTelemetry traces, detects failures, runs an AI diagnostic pipeline, and generates incident PDFs. The platform runs on your machine; only summarized error text is sent to your configured LLM provider.

## Why use it

- Fast ingest path: trace endpoint returns `202` and queues work asynchronously.
- Practical output: each incident gets root cause, severity, recommendations, and a PDF report.
- Live visibility: dashboard updates in real time via WebSocket events.
- Privacy-first architecture: traces stay in local PostgreSQL.

## System overview

```text
Your services (OTLP) -> OTel Collector -> FastAPI (/v1/traces)
                                         -> Redis queue (RQ)
                                         -> Worker (AI + embeddings + PDF)
                                         -> PostgreSQL (incidents/traces + pgvector)
                                         -> Redis pub/sub -> UI websocket updates
```

## Quick start

### 1. Prerequisites

- Docker Desktop 4.x+ (with Compose)
- Python 3.11+ (only for running demo script from host)
- An LLM API key

Notes:
- Current implementation is wired to Gemini env vars.
- To use another provider, replace logic in `services/diagnostic_agent.py` and `services/embedding_service.py`.

### 2. Bootstrap

```bash
git clone <repo-url>
cd ServiceIT
cp .env.example .env
# Set at minimum: GEMINI_API_KEY and POSTGRES_PASSWORD
docker compose up -d --build
```

### 3. Send sample failures

```bash
python examples/microservice_demo.py
```

### 4. Open UI and outputs

- Dashboard: <http://localhost:8000/ui>
- PDFs: `./outputs/`

Typical first incident appears in ~20-60 seconds depending on model/API latency.

## Configuration

All runtime configuration is read from `.env`.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `GEMINI_API_KEY` | Yes | - | LLM API key for diagnostics |
| `GEMINI_MODEL` | No | `gemini-1.5-flash` | Diagnostic model |
| `GEMINI_EMBEDDING_MODEL` | No | `models/text-embedding-004` | Embedding model for similarity search |
| `POSTGRES_USER` | No | `serviceit` | DB username |
| `POSTGRES_PASSWORD` | Yes | - | DB password |
| `POSTGRES_DB` | No | `serviceit_db` | DB name |
| `PDF_OUTPUT_DIR` | No | `/app/outputs` | PDF output path in container |
| `LOG_LEVEL` | No | `INFO` | Application log level |
| `REDIS_URL` | No | `redis://redis:6379/0` | Queue and pub/sub backend |

## What most engineers need (API)

- `POST /v1/traces`: ingest OTLP traces (protobuf or JSON), returns `202`.
- `GET /incidents`: list analyzed incidents.
- `GET /health`: service and DB health.
- `GET /ui`: browser dashboard.

For advanced integration, other endpoints remain available in code.

## Retention

- Raw traces older than 7 days (with no linked incident) are deleted.
- Unpinned incidents older than 90 days are deleted.
- Pinned incidents are retained.

Run retention manually:

```bash
docker compose run --rm serviceit-retention
```

## Security notes

- API listens on `127.0.0.1:8000` by default.
- Trace data stays local in Docker-hosted PostgreSQL.
- Only summarized error text is sent to the external LLM API.
- Input is validated with strict Pydantic schemas before persistence.

## Upgrade note

If upgrading from an older DB schema, apply:

```sql
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS is_pinned BOOLEAN DEFAULT FALSE;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS severity_rank INTEGER DEFAULT 1;
```

## Contributing

- Open PRs against `main`.
- Keep dependencies pinned in `requirements.txt`.
- Validate external input before DB/agent usage.
- Smoke test:

```bash
docker compose down -v
docker compose up -d --build
python examples/microservice_demo.py
```
