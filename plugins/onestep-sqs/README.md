# onestep-sqs

Amazon SQS connector plugin for `onestep`.

```bash
pip install onestep-sqs
```

The package registers these YAML resource types through the `onestep.resources`
entry point:

- `sqs`
- `sqs_queue`

Python usage:

```python
from onestep_sqs import SQSConnector
```

## Delivery metadata

Fetched messages keep the existing OneStep body decoding behavior and expose
SQS system metadata under `delivery.envelope.meta["sqs"]`:

```python
{
    "message_id": "00000000-0000-0000-0000-000000000000",
    "attributes": {
        "ApproximateReceiveCount": "2",
        "SentTimestamp": "1720000000000",
    },
}
```

The current message's `MessageId` sets `message_id`. Its `Attributes` sets
`attributes` to an isolated snapshot of the complete system attributes
dictionary. When no existing SQS metadata or current system fields are
available, `meta["sqs"]` is an empty dictionary.

Existing envelope `meta` and `attempts` values are preserved. If the encoded
envelope already contains a `meta["sqs"]` dictionary, its other keys are kept.
The reserved `message_id` and `attributes` keys are populated only from the
current SQS response, so missing fields do not inherit stale transport values.

`ReceiptHandle` remains internal to acknowledgement, retry, and release
operations. Custom SQS `MessageAttributes` are not exposed in the envelope.

## Shutdown and pause behavior

SQS receives use a blocking long poll, so shutdown, drain, and pause wait for
the current poll to finish instead of cancelling it. Any deliveries returned
after fetching has stopped are released immediately with a visibility timeout
of zero when processing has not started, making them available to SQS consumers
again without waiting for the configured visibility timeout.
