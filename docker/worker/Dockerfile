FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /workspace

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md /tmp/onestep/
COPY src/onestep /tmp/onestep/src/onestep

RUN python -m pip install --upgrade pip \
    && python -m pip install "/tmp/onestep[all]" \
    && rm -rf /tmp/onestep

COPY docker/worker/entrypoint.sh /usr/local/bin/onestep-worker-entrypoint
RUN chmod +x /usr/local/bin/onestep-worker-entrypoint

ENTRYPOINT ["onestep-worker-entrypoint"]
