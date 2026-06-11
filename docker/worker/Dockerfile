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
COPY plugins/onestep-rabbitmq/pyproject.toml plugins/onestep-rabbitmq/README.md /tmp/onestep/plugins/onestep-rabbitmq/
COPY plugins/onestep-rabbitmq/src/onestep_rabbitmq /tmp/onestep/plugins/onestep-rabbitmq/src/onestep_rabbitmq
COPY plugins/onestep-redis/pyproject.toml plugins/onestep-redis/README.md /tmp/onestep/plugins/onestep-redis/
COPY plugins/onestep-redis/src/onestep_redis /tmp/onestep/plugins/onestep-redis/src/onestep_redis
COPY plugins/onestep-mysql/pyproject.toml plugins/onestep-mysql/README.md /tmp/onestep/plugins/onestep-mysql/
COPY plugins/onestep-mysql/src/onestep_mysql /tmp/onestep/plugins/onestep-mysql/src/onestep_mysql
COPY plugins/onestep-sqs/pyproject.toml plugins/onestep-sqs/README.md /tmp/onestep/plugins/onestep-sqs/
COPY plugins/onestep-sqs/src/onestep_sqs /tmp/onestep/plugins/onestep-sqs/src/onestep_sqs

RUN python -m pip install --upgrade pip \
    && python -m pip install \
        "/tmp/onestep[all]" \
        /tmp/onestep/plugins/onestep-rabbitmq \
        /tmp/onestep/plugins/onestep-redis \
        /tmp/onestep/plugins/onestep-mysql \
        /tmp/onestep/plugins/onestep-sqs \
    && rm -rf /tmp/onestep

COPY docker/worker/entrypoint.sh /usr/local/bin/onestep-worker-entrypoint
RUN chmod +x /usr/local/bin/onestep-worker-entrypoint

ENTRYPOINT ["onestep-worker-entrypoint"]
