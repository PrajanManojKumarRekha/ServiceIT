# ServiceIT
Microservice analyzer
ServiceIT - Autonomous Microservice Observability Platform
Creating a production-ready framework for an autonomous observability platform that ingests telemetry data from microservices, uses RAG to diagnose issues based on historical incidents, and generates automated post-mortem reports.

Architecture Overview
System Architecture
Review
System Architecture

The system consists of:

Telemetry Collection: OpenTelemetry Collector receiving OTLP traces from instrumented microservices
Backend: FastAPI-based async engine for ingestion and analysis
AI Diagnostics: RAG pipeline using pgvector for semantic search of historical incidents
Reporting: Automated PDF generation with Matplotlib visualizations
Real-time Updates: WebSocket-based dashboard for live monitoring
Proposed Changes
Infrastructure Layer
[MODIFY] 
otel-config.yaml
Configure OpenTelemetry Collector with:

OTLP receivers (gRPC on 4317, HTTP on 4318)
Processors for batching and memory limiting
Exporters to forward traces to ServiceIT backend via HTTP
[MODIFY] 
docker-compose.yml
Add volumes for persistent storage and network configuration for proper service communication

[MODIFY] 
Dockerfile
Fix typo in base image (python:3.13-slim-bookworm) and adjust CMD to point to correct module path

Backend Core
[MODIFY] 
main.py
Complete FastAPI application with:

OTLP ingestion endpoint (POST /v1/traces)
WebSocket endpoint for real-time updates
Health check and metrics endpoints
CORS middleware for frontend integration
OpenTelemetry instrumentation
[NEW] 
database.py
SQLAlchemy async engine setup with PostgreSQL + pgvector connection pooling

[NEW] 
models.py
Database models:

Trace: Stores raw OTLP trace data
Incident: Stores detected incidents with vector embeddings for RAG
[NEW] 
schemas.py
Pydantic models for request/response validation

AI/RAG Pipeline
[NEW] 
services/embedding_service.py
Generate embeddings for incident descriptions using sentence transformers (will use placeholder for now, can integrate with actual embedding models)

[NEW] 
services/incident_processor.py
Core RAG logic:

Detect anomalies from traces
Generate embeddings
Search similar past incidents using pgvector
Auto-diagnose root cause
[NEW] 
services/diagnostic_agent.py
LangChain-based agent for intelligent root cause analysis (placeholder structure for now)

Reporting Engine
[NEW] 
services/report_generator.py
PDF post-mortem generation:

Matplotlib charts (error timeline, service dependency graph)
FPDF2 document assembly
Executive summary generation
[NEW] 
templates/report_template.py
Reusable report structure and styling

Example Microservices
[NEW] 
examples/microservice_demo.py
Standalone Python microservice instrumented with OpenTelemetry SDK that:

Generates sample traces (successful requests, errors, slowdowns)
Sends OTLP to collector
Demonstrates the complete pipeline
Database Schema
[NEW] 
init_db.py
Database initialization script:

Create tables
Enable pgvector extension
Seed sample data
Documentation
[MODIFY] 
README.md
Complete project documentation with setup instructions, architecture overview, and usage examples

Verification Plan
Automated Tests
# Start infrastructure
docker-compose up -d
# Initialize database
python backend/init_db.py
# Run example microservice to generate traces
python examples/microservice_demo.py
# Verify traces in database
docker exec serviceit-db psql -U admin -d serviceit -c "SELECT COUNT(*) FROM traces;"
Manual Verification
Check OpenTelemetry Collector receives traces at :4317
Verify FastAPI backend stores traces in PostgreSQL
Test RAG pipeline finds similar incidents
Generate sample PDF report
Confirm WebSocket updates work for real-time monitoring
ServiceIT Framework Creation

# Start infrastructure
docker-compose up -d
# Initialize database
python backend/init_db.py
# Run example microservice to generate traces
python examples/microservice_demo.py
# Verify traces in database
docker exec serviceit-db psql -U admin -d serviceit -c "SELECT COUNT(*) FROM traces;"