# Changelog

## 0.1.0 - 2026-06-16

- Add the `onestep-agent` CLI with `setup` and `start` commands.
- Support local config-file setup with environment variable overrides.
- Register worker agents with the control plane and maintain a stable local identity.
- Connect to the worker-agent WebSocket protocol and handle start, stop, and restart commands.
- Download, verify, and safely extract workflow packages before running `onestep check` and `onestep run`.
- Persist running deployment state for restart recovery.
- Emit deployment lifecycle events and command results back to the control plane.
- Add an end-to-end smoke script covering control-plane startup, agent registration, deployment start, and deployment stop.
