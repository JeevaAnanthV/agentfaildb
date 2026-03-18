# ── Stage 1: builder ────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# System build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy only the packaging metadata first so pip layer is cached unless deps change
COPY pyproject.toml ./
# Create a minimal stub so pip can resolve the package without full source
RUN mkdir -p agentfaildb && touch agentfaildb/__init__.py

# Upgrade pip + setuptools first, then install core deps only (no PyTorch/ML)
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip setuptools && \
    pip install --prefix=/install .


# ── Stage 2: runtime ────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app

# Runtime system dependencies (libpq for psycopg2)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY agentfaildb/ ./agentfaildb/
COPY db/ ./db/

# Create a non-root user and switch to it
RUN useradd --create-home --shell /bin/bash appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "agentfaildb.harness.api:app", "--host", "0.0.0.0", "--port", "8000"]
