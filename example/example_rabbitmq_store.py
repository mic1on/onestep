from onestep.store.rabbitmq import RabbitMQStore

config = {
    "username": "admin",
    "password": "admin",
}

rmq_store = RabbitMQStore(**config)

rmq_store.channel.queue.declare(queue="test_one", durable=True)
rmq_store.send(queue_name="test_one", message="test_one")
counts = rmq_store.get_message_counts(queue_name="test_one")
print(counts)
msg = rmq_store.channel.basic.get(queue="test_one", auto_decode=True)
print(msg)
del rmq_store.connection
print(msg.nack(requeue=True))
