def test_rabbitmq_broker():
    from onestep.broker.rabbitmq import RabbitMQBroker
    from onestep.message import Message
    from loguru import logger
    
    rmq_broker = RabbitMQBroker(
        "test_one",
        {
            "username": "admin",
            "password": "admin",
        }
    )
    rmq_broker.send(message={"test": "test_one"})
    consumer = rmq_broker.consume()
    msg: Message = next(consumer)
    logger.debug(f"msg: {msg}")
    msg.broker = msg.broker or rmq_broker
    logger.debug("del rmq_broker.client.connection")
    del rmq_broker.client.connection
    assert msg.requeue(is_source=True) is None
