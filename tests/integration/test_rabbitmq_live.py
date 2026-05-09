import asyncio
import os
import uuid

import pytest

from onestep import RabbitMQConnector


if not os.getenv("ONESTEP_RABBITMQ_URL"):
    pytest.skip("set ONESTEP_RABBITMQ_URL to run RabbitMQ integration tests", allow_module_level=True)


@pytest.mark.integration
def test_rabbitmq_round_trip_live():
    async def scenario():
        connector = RabbitMQConnector(os.environ["ONESTEP_RABBITMQ_URL"])
        queue_name = f"{os.getenv('ONESTEP_RABBITMQ_QUEUE', 'onestep.integration')}.{uuid.uuid4().hex}"
        queue = connector.queue(queue_name, poll_interval_s=1.0, auto_delete=True, durable=False, exclusive=True)
        await queue.publish({"value": 1})
        batch = await queue.fetch(1)
        assert len(batch) == 1
        assert batch[0].payload == {"value": 1}
        await batch[0].ack()
        await queue.close()
        await connector.close()

    asyncio.run(scenario())


@pytest.mark.integration
def test_rabbitmq_requeue_with_exchange_live():
    async def scenario():
        connector = RabbitMQConnector(os.environ["ONESTEP_RABBITMQ_URL"])
        suffix = uuid.uuid4().hex
        queue = connector.queue(
            f"onestep.integration.exchange.{suffix}",
            routing_key=f"events.{suffix}",
            exchange=f"onestep.exchange.{suffix}",
            exchange_type="direct",
            poll_interval_s=1.0,
            auto_delete=True,
            durable=False,
            exclusive=True,
            exchange_auto_delete=True,
            exchange_durable=False,
        )
        await queue.publish({"value": 2})

        first = await queue.fetch(1)
        assert len(first) == 1
        assert first[0].payload == {"value": 2}
        await first[0].retry()

        second = await queue.fetch(1)
        assert len(second) == 1
        assert second[0].payload == {"value": 2}
        await second[0].ack()
        await queue.close()
        await connector.close()

    asyncio.run(scenario())
