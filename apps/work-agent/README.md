# onestep-worker-agent

Execution host agent for OneStep Control Plane deployments.

## Local Start

```bash
export ONESTEP_PLANE_URL=http://localhost:8000
export ONESTEP_AGENT_REGISTRATION_TOKEN=dev-token
export ONESTEP_WORKER_AGENT_DIR=.onestep-worker-agent
export ONESTEP_WORKER_AGENT_MAX_CONCURRENCY=2
onestep-worker-agent start
```

The agent registers once, stores its identity under `ONESTEP_WORKER_AGENT_DIR`,
connects to the control plane, and runs assigned workflow packages with
`onestep check worker.yaml` followed by `onestep run worker.yaml`.
