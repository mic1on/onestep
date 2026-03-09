import asyncio

from onestep import ConnectorOperation, ConnectorOperationError, SQSConnector


class FakeSQSClient:
    def __init__(self):
        self.available = []
        self.inflight = {}
        self.deleted = []
        self.deleted_batches = []
        self.visibility_changes = []
        self.sent = []
        self._counter = 0

    def send_message(self, **kwargs):
        self._counter += 1
        receipt_handle = f"rh-{self._counter}"
        message = {
            "MessageId": f"msg-{self._counter}",
            "ReceiptHandle": receipt_handle,
            "Body": kwargs["MessageBody"],
        }
        self.sent.append(kwargs)
        self.available.append(message)
        return {"MessageId": message["MessageId"]}

    def receive_message(self, **kwargs):
        take = kwargs["MaxNumberOfMessages"]
        messages = []
        while self.available and len(messages) < take:
            message = self.available.pop(0)
            self.inflight[message["ReceiptHandle"]] = message
            messages.append(message)
        if not messages:
            return {}
        return {"Messages": messages}

    def delete_message(self, QueueUrl, ReceiptHandle):
        self.deleted.append((QueueUrl, ReceiptHandle))
        self.inflight.pop(ReceiptHandle, None)

    def delete_message_batch(self, QueueUrl, Entries):
        self.deleted_batches.append((QueueUrl, Entries))
        for entry in Entries:
            receipt_handle = entry["ReceiptHandle"]
            self.deleted.append((QueueUrl, receipt_handle))
            self.inflight.pop(receipt_handle, None)
        return {"Successful": [{"Id": entry["Id"]} for entry in Entries], "Failed": []}

    def change_message_visibility(self, QueueUrl, ReceiptHandle, VisibilityTimeout):
        self.visibility_changes.append((QueueUrl, ReceiptHandle, VisibilityTimeout))
        message = self.inflight.get(ReceiptHandle)
        if message is not None and VisibilityTimeout == 0:
            self.available.append(message)
            self.inflight.pop(ReceiptHandle, None)


def test_sqs_queue_send_fetch_batch_delete_and_fail_delete():
    async def scenario():
        client = FakeSQSClient()
        connector = SQSConnector(region_name="ap-southeast-1", client=client)
        queue = connector.queue(
            "https://sqs.ap-southeast-1.amazonaws.com/123456789/jobs.fifo",
            wait_time_s=0,
            message_group_id="workers",
            on_fail="delete",
            delete_batch_size=2,
            delete_flush_interval_s=60,
        )

        await queue.publish({"value": 1}, meta={"source": "test"}, attempts=3)
        await queue.publish({"value": 2})
        assert client.sent[0]["MessageGroupId"] == "workers"

        batch = await queue.fetch(2)
        assert len(batch) == 2
        assert batch[0].payload == {"value": 1}
        assert batch[0].envelope.meta == {"source": "test"}
        assert batch[0].envelope.attempts == 3

        first_receipt = batch[0]._message["ReceiptHandle"]
        second_receipt = batch[1]._message["ReceiptHandle"]
        await batch[0].ack()
        assert client.deleted_batches == []

        await batch[1].ack()
        assert len(client.deleted_batches) == 1
        entries = client.deleted_batches[0][1]
        assert {entry["ReceiptHandle"] for entry in entries} == {first_receipt, second_receipt}

        await queue.publish({"value": 3})
        failed = await queue.fetch(1)
        assert len(failed) == 1
        fail_receipt = failed[0]._message["ReceiptHandle"]
        await failed[0].fail(RuntimeError("boom"))
        await queue.close()
        assert any(handle == fail_receipt for _, handle in client.deleted)
        await connector.close()

    asyncio.run(scenario())


def test_sqs_queue_retry_and_heartbeat_release():
    async def scenario():
        client = FakeSQSClient()
        connector = SQSConnector(region_name="ap-southeast-1", client=client)
        queue = connector.queue(
            "https://sqs.ap-southeast-1.amazonaws.com/123456789/jobs",
            wait_time_s=0,
            on_fail="release",
            heartbeat_interval_s=0.01,
            heartbeat_visibility_timeout=30,
            delete_flush_interval_s=0,
        )

        await queue.publish({"value": 1})
        batch = await queue.fetch(1)
        assert len(batch) == 1
        receipt = batch[0]._message["ReceiptHandle"]

        await batch[0].start_processing()
        await asyncio.sleep(0.03)
        assert any(item == (queue.url, receipt, 30) for item in client.visibility_changes)

        await batch[0].retry(delay_s=5)
        assert client.visibility_changes[-1] == (queue.url, receipt, 5)
        client.available.append(client.inflight.pop(receipt))

        redelivery = await queue.fetch(1)
        assert len(redelivery) == 1
        release_receipt = redelivery[0]._message["ReceiptHandle"]
        await redelivery[0].start_processing()
        await redelivery[0].fail(RuntimeError("release"))
        assert client.visibility_changes[-1] == (queue.url, release_receipt, 0)

        await queue.close()
        await connector.close()

    asyncio.run(scenario())


def test_sqs_queue_send_maps_retryable_errors():
    class BrokenClient(FakeSQSClient):
        def send_message(self, **kwargs):
            raise TimeoutError("timeout")

    async def scenario():
        client = BrokenClient()
        connector = SQSConnector(region_name="ap-southeast-1", client=client)
        queue = connector.queue(
            "https://sqs.ap-southeast-1.amazonaws.com/123456789/jobs",
            wait_time_s=0,
        )

        try:
            await queue.publish({"value": 1})
        except ConnectorOperationError as exc:
            assert exc.operation is ConnectorOperation.SEND
        else:
            raise AssertionError("expected ConnectorOperationError")

    asyncio.run(scenario())


def test_sqs_batch_delete_keeps_pending_entries_when_delete_raises() -> None:
    class BrokenDeleteClient(FakeSQSClient):
        def delete_message_batch(self, QueueUrl, Entries):
            raise TimeoutError("timeout")

    async def scenario():
        client = BrokenDeleteClient()
        connector = SQSConnector(region_name="ap-southeast-1", client=client)
        queue = connector.queue(
            "https://sqs.ap-southeast-1.amazonaws.com/123456789/jobs",
            wait_time_s=0,
            delete_batch_size=2,
            delete_flush_interval_s=60,
        )

        await queue.publish({"value": 1})
        await queue.publish({"value": 2})
        batch = await queue.fetch(2)
        first_receipt = batch[0]._message["ReceiptHandle"]
        second_receipt = batch[1]._message["ReceiptHandle"]

        await batch[0].ack()
        try:
            await batch[1].ack()
        except TimeoutError:
            pass
        else:
            raise AssertionError("expected batch delete failure")

        assert [entry["ReceiptHandle"] for entry in queue._pending_delete] == [
            first_receipt,
            second_receipt,
        ]
        assert first_receipt in client.inflight
        assert second_receipt in client.inflight

        await connector.close()

    asyncio.run(scenario())
