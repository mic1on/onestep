import asyncio
import os
import uuid

import pytest

from onestep import SQSConnector


if not os.getenv("ONESTEP_SQS_QUEUE_URL"):
    pytest.skip("set ONESTEP_SQS_QUEUE_URL to run SQS integration tests", allow_module_level=True)


async def _create_queue(connector: SQSConnector, *, fifo: bool = True) -> str:
    client = connector.get_client()
    queue_name = f"onestep-live-{uuid.uuid4().hex}"
    attributes = {}
    if fifo:
        queue_name = f"{queue_name}.fifo"
        attributes = {
            "FifoQueue": "true",
            "ContentBasedDeduplication": "true",
        }
    response = await asyncio.to_thread(client.create_queue, QueueName=queue_name, Attributes=attributes)
    return response["QueueUrl"]


async def _delete_queue(connector: SQSConnector, queue_url: str) -> None:
    await asyncio.to_thread(connector.get_client().delete_queue, QueueUrl=queue_url)


@pytest.mark.integration
def test_sqs_round_trip_live():
    async def scenario():
        region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
        connector = SQSConnector(
            region_name=region,
            options={
                "endpoint_url": os.getenv("AWS_ENDPOINT_URL"),
                "aws_access_key_id": os.getenv("AWS_ACCESS_KEY_ID"),
                "aws_secret_access_key": os.getenv("AWS_SECRET_ACCESS_KEY"),
            },
        )
        group_id = os.getenv("ONESTEP_SQS_GROUP_ID")
        queue_url = await _create_queue(connector, fifo=True)
        queue = connector.queue(
            queue_url,
            wait_time_s=1,
            message_group_id=group_id,
            delete_flush_interval_s=0,
        )
        try:
            await queue.publish({"value": 1})
            batch = await queue.fetch(1)
            assert len(batch) == 1
            assert batch[0].payload == {"value": 1}
            await batch[0].ack()
            await queue.close()
        finally:
            await _delete_queue(connector, queue_url)
            await connector.close()

    asyncio.run(scenario())


@pytest.mark.integration
def test_sqs_release_live():
    async def scenario():
        region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
        connector = SQSConnector(
            region_name=region,
            options={
                "endpoint_url": os.getenv("AWS_ENDPOINT_URL"),
                "aws_access_key_id": os.getenv("AWS_ACCESS_KEY_ID"),
                "aws_secret_access_key": os.getenv("AWS_SECRET_ACCESS_KEY"),
            },
        )
        queue_url = await _create_queue(connector, fifo=False)
        queue = connector.queue(
            queue_url,
            wait_time_s=1,
            visibility_timeout=2,
            on_fail="release",
            delete_flush_interval_s=0,
        )

        try:
            release_marker = f"release-{uuid.uuid4().hex}"
            await queue.publish({"marker": release_marker, "value": 1})

            first = await queue.fetch(1)
            assert len(first) == 1
            assert first[0].payload["marker"] == release_marker
            await first[0].fail(RuntimeError("release"))

            released = await queue.fetch(1)
            assert len(released) == 1
            assert released[0].payload["marker"] == release_marker
            await released[0].ack()
            empty = await queue.fetch(1)
            assert empty == []

            await queue.close()
        finally:
            await _delete_queue(connector, queue_url)
            await connector.close()

    asyncio.run(scenario())


@pytest.mark.integration
def test_sqs_batch_delete_live():
    async def scenario():
        region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
        connector = SQSConnector(
            region_name=region,
            options={
                "endpoint_url": os.getenv("AWS_ENDPOINT_URL"),
                "aws_access_key_id": os.getenv("AWS_ACCESS_KEY_ID"),
                "aws_secret_access_key": os.getenv("AWS_SECRET_ACCESS_KEY"),
            },
        )
        queue_url = await _create_queue(connector, fifo=False)
        queue = connector.queue(
            queue_url,
            wait_time_s=1,
            delete_batch_size=2,
            delete_flush_interval_s=60,
        )

        try:
            batch_marker = f"batch-{uuid.uuid4().hex}"
            await queue.publish({"marker": batch_marker, "value": 2})
            await queue.publish({"marker": batch_marker, "value": 3})

            batch = await queue.fetch(2)
            assert len(batch) == 2
            assert {item.payload["value"] for item in batch} == {2, 3}
            assert all(item.payload["marker"] == batch_marker for item in batch)
            await batch[0].ack()
            await batch[1].ack()

            await queue.flush_deletes()
            empty = await queue.fetch(1)
            assert empty == []

            await queue.close()
        finally:
            await _delete_queue(connector, queue_url)
            await connector.close()

    asyncio.run(scenario())
