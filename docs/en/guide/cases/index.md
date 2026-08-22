---
title: User Cases | Guide
outline: deep
---

# User Cases

This section collects production-ready OneStep user cases. Cases use anonymous business names and environment variables,
focusing on reusable connector combinations, reliability boundaries, pre-deployment checks, and disaster recovery.
Business field transformations should still be implemented by the application's own Python handlers.

## Cases

- [MySQL Order Stream Incremental Sync to Feishu Bitable](/en/guide/cases/mysql-feishu-order-sync):
  Uses MySQL composite cursor, persistent progress, and Feishu Insert key index to reliably write
  immutable order records into a Bitable.
- [SQS Messages Reliably Persisted to MySQL](/en/guide/cases/sqs-to-mysql):
  Handles SQS at-least-once delivery, using visibility heartbeat and an `upsert` idempotency key to
  reliably write messages into MySQL, with failures going to a dead-letter queue.
- [Multi-Connector Event Fan-out Pipeline](/en/guide/cases/multi-connector-fanout):
  One task reads from Redis Streams and fans out via conditional routing and per-sink transforms to
  MySQL, an HTTP callback, and an audit stream, with terminal failures going to dead-letter.
- [FastAPI Submits Long Tasks and Schedules Workers](/en/guide/cases/fastapi-execution-scheduling):
  Uses PostgreSQL tracked execution so FastAPI submits tasks and returns an ID while a separate worker
  asynchronously claims and executes, supporting idempotent submission, lease heartbeat, and cancellation.

## How to Read

First read the prerequisites and full YAML in each case, then replace resource names, environment variables, view names, and field mappings with your own business values. The `handler` in each case only defines the input/output contract; do not put business transformation, query, or branching logic into YAML.
