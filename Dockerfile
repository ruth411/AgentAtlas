# AgentAtlas — single-stage image that ships the API server, the MCP
# stdio bridge, and the seed/migrate CLI behind one `agentatlas` binary.
#
# Build:  docker build -t agentatlas .
# Run:    docker run --rm -p 8000:8000 agentatlas serve --host 0.0.0.0
# MCP:    docker run --rm -i agentatlas mcp
# Seed:   docker run --rm -v $(pwd)/data:/app/data agentatlas seed --reset

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Backend package + Alembic + seed data. README/contracts are pulled in
# because the package metadata's `readme = "../README.md"` reference and
# the Stage 0 tool gate (`contracts/agentatlas_stage_0.v1.json`) both
# resolve relative to the source tree at runtime.
COPY backend/ /app/backend/
COPY contracts/ /app/contracts/
COPY data/ /app/data/
COPY scripts/ /app/scripts/
COPY README.md /app/README.md

# `pip install -e backend` would leave the package as a path import; a
# regular install lands the `agentatlas` script on PATH the same way it
# does for end users via PyPI.
RUN pip install /app/backend

# Default to the API server; override with `docker run ... agentatlas <cmd>`
# (e.g. `mcp`, `seed`, `migrate`, `query`).
EXPOSE 8000
ENTRYPOINT ["agentatlas"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
