# onestep-control-plane

Control-plane reporter and WebSocket command integration plugin for `onestep`.

```bash
pip install onestep-control-plane
```

Most applications should install it through the core extra:

```bash
pip install 'onestep[control-plane]'
```

YAML usage:

```yaml
reporter: true
```

Python usage:

```python
from onestep_control_plane import ControlPlaneReporter, ControlPlaneReporterConfig
```

Compatibility imports also work when this plugin is installed:

```python
from onestep import ControlPlaneReporter, ControlPlaneReporterConfig
from onestep.control_plane_ws import ControlPlaneWsSender
```

## Presence beacon

Since 0.2.0 the reporter keeps an instance shown as **online** while its event
loop is blocked inside a synchronous handler, instead of letting it flap
offline/online on every slow call.

The heartbeat loop and every task handler share one asyncio event loop, so a
handler making a blocking call starves the heartbeat. The reporter therefore
starts a daemon thread that watches a timestamp the heartbeat loop updates each
tick and POSTs `/api/v1/agents/presence` to the control plane **only** once that
timestamp is older than the grace window. While the loop is healthy the thread
sends nothing at all, so a normal deployment behaves exactly as before.

The control plane uses the frame to advance `last_seen_at` and nothing else: it
carries no sequence and no health, so it cannot reorder or overwrite real
telemetry. An agent whose WebSocket is broken while its loop is healthy keeps
touching the timestamp, stays silent, and is still reported `offline` -- the
beacon does not mask a real reporting outage.

The beacon starts only when the reporter built its own WebSocket sender. An
injected sender (tests, custom transports) gets no background HTTP client
unless you pass one explicitly:

```python
from onestep_control_plane import ControlPlaneReporter, PresenceBeacon

reporter = ControlPlaneReporter(config, presence_beacon=PresenceBeacon(...))
```

Settings:

| Env var | Default | Meaning |
| --- | --- | --- |
| `ONESTEP_CONTROL_PLANE_PRESENCE_ENABLED` | `true` | `false` disables the beacon. |
| `ONESTEP_CONTROL_PLANE_PRESENCE_INTERVAL_S` | `heartbeat_interval_s` | Poll interval while the loop is stalled. |
| `ONESTEP_CONTROL_PLANE_PRESENCE_GRACE_S` | `2 x presence_interval_s` (so `2 x heartbeat_interval_s` by default) | Loop silence that counts as stalled; must be `>= 2 x` the interval. |

If the control plane predates the route it answers `404`; the beacon logs once
and disables itself, leaving the WebSocket heartbeat as the only liveness
signal. The plugin can therefore be upgraded before the plane.

Blocking handlers are still worth converting (`asyncio.to_thread()`, a real
async client): the beacon keeps the instance shown as online, but a blocked loop
still delays task throughput and command responses such as `ping` or `drain`.
