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
COPY plugins/onestep-control-plane/pyproject.toml plugins/onestep-control-plane/README.md /tmp/onestep/plugins/onestep-control-plane/
COPY plugins/onestep-control-plane/src/onestep_control_plane /tmp/onestep/plugins/onestep-control-plane/src/onestep_control_plane
COPY plugins/onestep-redis/pyproject.toml plugins/onestep-redis/README.md /tmp/onestep/plugins/onestep-redis/
COPY plugins/onestep-redis/src/onestep_redis /tmp/onestep/plugins/onestep-redis/src/onestep_redis
COPY plugins/onestep-sql/pyproject.toml plugins/onestep-sql/README.md /tmp/onestep/plugins/onestep-sql/
COPY plugins/onestep-sql/src/onestep_sql /tmp/onestep/plugins/onestep-sql/src/onestep_sql
COPY plugins/onestep-mysql/pyproject.toml plugins/onestep-mysql/README.md /tmp/onestep/plugins/onestep-mysql/
COPY plugins/onestep-mysql/src/onestep_mysql /tmp/onestep/plugins/onestep-mysql/src/onestep_mysql
COPY plugins/onestep-postgres/pyproject.toml plugins/onestep-postgres/README.md /tmp/onestep/plugins/onestep-postgres/
COPY plugins/onestep-postgres/src/onestep_postgres /tmp/onestep/plugins/onestep-postgres/src/onestep_postgres
COPY plugins/onestep-sqs/pyproject.toml plugins/onestep-sqs/README.md /tmp/onestep/plugins/onestep-sqs/
COPY plugins/onestep-sqs/src/onestep_sqs /tmp/onestep/plugins/onestep-sqs/src/onestep_sqs
COPY plugins/onestep-cf-queues/pyproject.toml plugins/onestep-cf-queues/README.md /tmp/onestep/plugins/onestep-cf-queues/
COPY plugins/onestep-cf-queues/src/onestep_cf_queues /tmp/onestep/plugins/onestep-cf-queues/src/onestep_cf_queues
COPY plugins/onestep-kafka/pyproject.toml plugins/onestep-kafka/README.md /tmp/onestep/plugins/onestep-kafka/
COPY plugins/onestep-kafka/src/onestep_kafka /tmp/onestep/plugins/onestep-kafka/src/onestep_kafka
COPY plugins/onestep-elasticsearch/pyproject.toml plugins/onestep-elasticsearch/README.md /tmp/onestep/plugins/onestep-elasticsearch/
COPY plugins/onestep-elasticsearch/src/onestep_elasticsearch /tmp/onestep/plugins/onestep-elasticsearch/src/onestep_elasticsearch
COPY plugins/onestep-clickhouse/pyproject.toml plugins/onestep-clickhouse/README.md /tmp/onestep/plugins/onestep-clickhouse/
COPY plugins/onestep-clickhouse/src/onestep_clickhouse /tmp/onestep/plugins/onestep-clickhouse/src/onestep_clickhouse
COPY plugins/onestep-mongodb/pyproject.toml plugins/onestep-mongodb/README.md /tmp/onestep/plugins/onestep-mongodb/
COPY plugins/onestep-mongodb/src/onestep_mongodb /tmp/onestep/plugins/onestep-mongodb/src/onestep_mongodb

RUN python -m pip install --upgrade pip \
    && python -m pip install \
        "/tmp/onestep[all]" \
        /tmp/onestep/plugins/onestep-rabbitmq \
        /tmp/onestep/plugins/onestep-control-plane \
        /tmp/onestep/plugins/onestep-redis \
        /tmp/onestep/plugins/onestep-sql \
        /tmp/onestep/plugins/onestep-mysql \
        /tmp/onestep/plugins/onestep-postgres \
        /tmp/onestep/plugins/onestep-sqs \
        /tmp/onestep/plugins/onestep-cf-queues \
        /tmp/onestep/plugins/onestep-kafka \
        /tmp/onestep/plugins/onestep-elasticsearch \
        /tmp/onestep/plugins/onestep-clickhouse \
        /tmp/onestep/plugins/onestep-mongodb \
    && rm -rf /tmp/onestep

COPY docker/worker/entrypoint.sh /usr/local/bin/onestep-worker-entrypoint
RUN chmod +x /usr/local/bin/onestep-worker-entrypoint

ENTRYPOINT ["onestep-worker-entrypoint"]
