FROM python:3.12-slim

# Create non-root user
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid 1000 --no-create-home appuser

WORKDIR /app
ENV PYTHONPATH=/app

# Install deps first for layer caching
COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends gcc python3-dev git && \
    pip install --no-cache-dir -r requirements.txt && \
    apt-get purge -y gcc python3-dev && apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

# Copy source after deps
COPY backend/ ./backend/
COPY services/ ./services/
COPY workers/ ./workers/

# Create outputs dir and set ownership
RUN mkdir -p /app/outputs && chown -R appuser:appuser /app

USER appuser

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
