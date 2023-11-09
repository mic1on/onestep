def test_rabbitmq_broker():
    from onestep.broker.sqs import SQSBroker

    sqs_broker = SQSBroker(
        "job.fifo",
        {
            "region_name": "cn-northwest-1",
        }
    )
