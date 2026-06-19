# Changelog

## 0.1.2 - 2026-06-19

- Install every deployment into a runtime virtualenv with the default onestep connector set, even when the uploaded package has no dependency manifest.
- Support packages that include both `pyproject.toml` and `requirements.txt` by installing both sets of dependencies.
- Rebuild cached runtime virtualenvs when the agent's default runtime dependency set changes.
- Publish worker-agent package releases from GitHub Actions through PyPI Trusted Publishing.

## 0.1.1 - 2026-06-16

- Honor the control plane's `hello_ack.heartbeat_interval_s` instead of a
  hardcoded 30s heartbeat, so a server-configured interval is actually respected.
  Falls back to 30s when the field is missing or invalid.
- Surface control-plane `error` frames (e.g. `hello_required`,
  `unsupported_message_type`) in the agent log instead of silently dropping them,
  so a forced-close reconnect is distinguishable from a network blip.
- Honor the command-level `timeout_s` the plane sets on every command:
  `onestep check` is now bounded by it (the child is killed on timeout, reported
  as exit code 124), and `stop`/`restart` use it as the graceful-shutdown window
  instead of a hardcoded 10s.
- Capture the per-deployment `params` and `credential_refs` the plane dispatches
  and record them on the `preparing` deployment event. They are not injected
  into the subprocess yet (onestep core has no runtime params entrypoint and the
  plane does not deliver credential values), but they are now visible in the
  deployment timeline instead of silently dropped.
- Fix child process output deadlock: `onestep run` stdout/stderr were piped but
  never drained, so the pipe buffer (64KB) would fill and block the worker
  forever. Output is now appended to `<deployment-dir>/worker.log` and the file
  handle is closed when the deployment is released.
- Fix the agent exiting on any WebSocket disconnect. The control plane relies on
  reconnect to re-dispatch pending commands, so the control loop now retries
  sessions with capped exponential backoff instead of terminating the process.
- Restrict `identity.json` to owner-only (`0o600`); it stores the long-lived
  `connection_token`, matching the protection already applied to `config.json`.

## 0.1.0 - 2026-06-16

- Add the `onestep-agent` CLI with `setup` and `start` commands.
- Support local config-file setup with environment variable overrides.
- Register worker agents with the control plane and maintain a stable local identity.
- Connect to the worker-agent WebSocket protocol and handle start, stop, and restart commands.
- Download, verify, and safely extract workflow packages before running `onestep check` and `onestep run`.
- Persist running deployment state for restart recovery.
- Emit deployment lifecycle events and command results back to the control plane.
- Add an end-to-end smoke script covering control-plane startup, agent registration, deployment start, and deployment stop.
