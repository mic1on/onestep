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
    message: Message = next(consumer)
    logger.debug(f"message: {message}")
    message.broker = message.broker or rmq_broker
    message.confirm()
    logger.debug("message.confirm()")
    # logger.debug("del rmq_broker.client.connection")
    # del rmq_broker.client.connection
    # assert message.requeue(is_source=True) is None
