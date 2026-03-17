# Examples

All examples assume you run them from the repo root with:

```bash
PYTHONPATH=src python3 example/<file>.py
```

Files:

- `cli_app.py`: minimal module shape for `onestep run package.module:app`
- `cli_app.yaml`: YAML app definition that reuses `example.cli_app:sync_users`
- `memory_pipeline.py`: in-memory source and sink with a simple transform
- `interval_source.py`: fixed-interval scheduling
- `cron_source.py`: wall-clock scheduling with cron expressions
- `webhook_source.py`: standalone webhook ingestion endpoint
- `runtime_showcase.py`: webhook -> queue -> worker -> dead-letter showcase
- `mysql_table_queue.py`: table-backed queue using `status` transitions
- `mysql_incremental.py`: incremental sync using a durable cursor store
- `rabbitmq_queue.py`: RabbitMQ source/sink usage
- `sqs_queue.py`: SQS source/sink usage
- `control_plane_reporter_demo.py`: long-running reporter demo for local control plane smoke testing;
  cycles `ok -> retry_once -> fail -> slow` so you can observe success, retry, timeout, and
  dead-letter behavior from the control plane

Control plane reporting is documented in the top-level `README.md`. For a quick local demo, start
`onestep-control-plane` first and then run `control_plane_reporter_demo.py`.
