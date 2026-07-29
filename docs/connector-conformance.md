# Connector Conformance

`onestep.testing` provides a small, pytest-independent toolkit for proving connector delivery semantics against the real runtime.

Capability profiles belong in connector tests. They are not resource catalog fields, reporter payloads, or runtime feature negotiation.

## Capability Profiles

| Capability | Contract |
| --- | --- |
| `basic_source` | Fetch returns valid deliveries and respects the source lifecycle. |
| `checkpoint_source` | Progress advances from delivery completion, never merely from fetch. |
| `claimed_source` | Intake stop controls release work fetched but not started. |
| `acknowledged_sink` | `send()` returns only after the backend acknowledges the write. |
| `chunked_sink` | A logical batch is split into bounded writes and every chunk is awaited. |
| `replay_safe_sink` | The documented stable-key or upsert mode tolerates replay without creating a second logical record. |
| `public_errors` | Backend failures use public connector errors without exposing configured secrets. |

Profiles map each declared capability to the test node IDs that prove it:

```python
from onestep.testing import ConnectorCapability, ConnectorConformanceProfile


PROFILE = ConnectorConformanceProfile(
    name="example",
    contracts={
        ConnectorCapability.BASIC_SOURCE: ("test_fetches_delivery",),
        ConnectorCapability.CLAIMED_SOURCE: ("test_stop_controls_release_claim",),
    },
)
```

The profile validates capability dependencies. For example, `claimed_source` requires `basic_source`, while `chunked_sink` and `replay_safe_sink` require `acknowledged_sink`.

## Data Flow

```text
source backend -> fetch -> Delivery -> handler -> sink.send -> backend ack -> Delivery.ack
                         |                         |
                         |                         +-- send error: retry/fail policy
                         +-- intake stops before handler: release_unstarted
```

The reusable runners exercise the two cross-runtime ordering boundaries:

```text
claimed source                          acknowledged sink

fetch starts and blocks                 sink.send starts and blocks
        |                                       |
drain / pause / shutdown                Delivery.ack must still be false
        |                                       |
fetch returns a claimed delivery        backend acknowledgement returns
        |                                       |
runtime calls release_unstarted         runtime calls Delivery.ack
```

Connector-specific tests still own backend setup and backend assertions. The toolkit owns only the shared `OneStepApp` orchestration, so connector behavior remains explicit rather than hidden behind a generic fake backend.

## Scope

The toolkit does not add runtime capability discovery, catalog fields, reporter fields, YAML syntax, or control-plane protocol changes. It also does not claim exactly-once delivery: onestep remains at-least-once, and replay safety is an explicit connector mode backed by a stable key or upsert contract.
