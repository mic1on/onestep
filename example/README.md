# Examples

All examples assume you run them from the repo root with:

```bash
PYTHONPATH=src python3 example/<file>.py
```

Files:

- `cli_app.py`: minimal module shape for `onestep run package.module:app`
- `memory_pipeline.py`: in-memory source and sink with a simple transform
- `interval_source.py`: fixed-interval scheduling
- `cron_source.py`: wall-clock scheduling with cron expressions
- `webhook_source.py`: standalone webhook ingestion endpoint
- `runtime_showcase.py`: webhook -> queue -> worker -> dead-letter showcase
- `mysql_table_queue.py`: table-backed queue using `status` transitions
- `mysql_incremental.py`: incremental sync using a durable cursor store
- `rabbitmq_queue.py`: RabbitMQ source/sink usage
- `sqs_queue.py`: SQS source/sink usage
