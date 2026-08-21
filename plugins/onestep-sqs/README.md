# onestep-sqs

Amazon SQS connector plugin for `onestep`. The package also ships an Amazon SNS
topic sink for fan-out publishing.

```bash
pip install onestep-sqs
```

The package registers these YAML resource types through the `onestep.resources`
entry point:

- `sqs`
- `sqs_queue`
- `sns`
- `sns_topic`

Python usage:

```python
from onestep_sqs import SQSConnector, SNSConnector
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

## SNS topic sink

SNS is publish/subscribe only, so `SNSTopic` implements `Sink` (not `Source`).
To consume SNS messages, subscribe an SQS queue to the topic and use
`sqs_queue` as the source.

```python
from onestep import MemoryQueue, OneStepApp
from onestep_sqs import SNSConnector

app = OneStepApp("sns-demo")
sns = SNSConnector(region_name="us-east-1")
notify = sns.topic(
    "arn:aws:sns:us-east-1:123456789012:events",
    subject="onestep-event",
)


@app.task(source=MemoryQueue("jobs"), emit=notify)
async def publish_event(ctx, job):
    return {"id": job["id"], "status": "done"}
```

The task return value is encoded with the standard OneStep envelope codec and
sent as the SNS `Message`. Configuration options:

- `subject`: optional SNS `Subject`.
- `message_attributes`: raw SNS `MessageAttributes` mapping for subscription
  filter policies.
- `message_group_id` / `deduplication_id_factory`: required (group) and
  optional (dedup) for FIFO topics whose ARN ends in `.fifo`. The factory
  receives the `Envelope` and returns the deduplication id string.
- `retry_delay_s`: retry backoff hint applied to normalized connector errors.

YAML:

```yaml
resources:
  sns:
    type: sns
    region_name: us-east-1
  notify:
    type: sns_topic
    connector: sns
    arn: arn:aws:sns:us-east-1:123456789012:events
    subject: onestep-event
```
