# Control Plane

Use this reference only when the user asks for telemetry, service/instance reporting, runtime control, WebSocket protocol behavior, or the onestep control plane.

## Runtime Reporter

Install the extra:

```bash
pip install 'onestep[control-plane]'
```

The smallest YAML reporter config is:

```yaml
reporter: true
```

This enables the built-in `ControlPlaneReporter` and resolves connection details from environment variables.

Use explicit config when the deployment should pin service metadata:

```yaml
reporter:
  base_url: "${ONESTEP_CONTROL_PLANE_URL}"
  token: "${ONESTEP_CONTROL_PLANE_TOKEN}"
  service_name: billing-sync
```

Do not add reporter config to local examples unless the user asks for control-plane integration.

## Topology Descriptors

Reporter sync payloads include task source/sink descriptors. Built-in connector kinds include `redis_stream` and `http_sink`.

Redis Streams report stream, group, consumer, batch, blocking, start ID, group creation, and trimming options. `HttpSink` reports redacted URL, method, header names with redacted values, query parameter names with redacted values, timeout, and success status codes.

Do not include secrets in custom descriptors. Redact DSNs, tokens, passwords, headers, query strings, and fragments before sending topology payloads.

## Runtime Identity

onestep supports stable runtime/replica identity for control-plane coordination. Preserve existing identity-store or instance-id behavior when editing deployed workers. Do not invent a new identity scheme unless the task is specifically about identity behavior.

## Useful Validation

For runtime app changes:

```bash
onestep check --strict worker.yaml
```

For control-plane protocol changes in the onestep repo, use focused tests around reporter, WebSocket, and runtime identity before broader suites.

Relevant areas in the onestep source tree:

- `src/onestep/reporter.py`
- `src/onestep/control_plane_ws.py`
- `src/onestep/identity_store.py`
- `tests/test_control_plane_reporter.py`
- `tests/test_control_plane_ws.py`
- `tests/test_runtime_identity.py`
