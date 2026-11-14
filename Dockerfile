# syntax=docker/dockerfile:1.7
ARG PYTHON_BASE=python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
ARG GO_BASE=golang:1.27-bookworm@sha256:4fa7c968ba93f1635670522dbb01a2c78150443e1385746924566079f36570ea

FROM ${PYTHON_BASE} AS base
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 curl ca-certificates git \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.lock ./requirements.lock
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --requirement requirements.lock

FROM base AS semgrep-stage
RUN python -m venv /opt/semgrep
COPY requirements-semgrep.lock /tmp/requirements-semgrep.lock
RUN --mount=type=cache,target=/root/.cache/pip \
    /opt/semgrep/bin/pip install --requirement /tmp/requirements-semgrep.lock

FROM ${GO_BASE} AS osv-stage
RUN --mount=type=cache,target=/root/.cache/go-build \
    --mount=type=cache,target=/go/pkg/mod \
    go install github.com/google/osv-scanner/v2/cmd/osv-scanner@v2.6.0

FROM base AS final
COPY --from=semgrep-stage /opt/semgrep /opt/semgrep
COPY --from=osv-stage /go/bin/osv-scanner /usr/local/bin/osv-scanner
RUN ln -s /opt/semgrep/bin/semgrep /usr/local/bin/semgrep
COPY app ./app
COPY spikes ./spikes
COPY alembic ./alembic
COPY alembic.ini ./
RUN useradd --create-home --uid 10001 sentra \
 && mkdir -p /app/data/repos /app/data/cache \
 && chown -R 10001:10001 /app/data
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers", "--limit-concurrency", "16"]
