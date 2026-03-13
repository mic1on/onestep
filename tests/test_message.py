from onestep.message import Extra, Message


def test_message_extra():
    extra = Extra(task_id='123', publish_time=1234567890.123, failure_count=1)
    extra.new_attr = 'new_attr'
    assert extra.task_id == '123'
    assert extra.publish_time == 1234567890.123
    assert extra.failure_count == 1
    assert extra.new_attr == 'new_attr'


def test_message_to_dict():
    extra = Extra(task_id='123', publish_time=1234567890.123, failure_count=1)
    message = Message(body={'key': 'value'}, extra=extra)
    expected_output = {'body': {'key': 'value'},
                       'extra': {'task_id': '123', 'publish_time': 1234567890.123, 'failure_count': 1}}
    assert message.to_dict() == expected_output


def test_message_replace():
    msg = Message(body={'key': 'value'}, extra={'task_id': '123', 'publish_time': 1234567890.123, 'failure_count': 0})
    msg = msg.replace(body={'new_key': 'new_value'})
    expected_dict = {'body': {'new_key': 'new_value'},
                     'extra': {'task_id': '123', 'publish_time': 1234567890.123, 'failure_count': 0}}
    assert msg.to_dict() == expected_dict


def test_message_set_exception():
    msg = Message(body={'key': 'value'}, extra={'task_id': '123', 'publish_time': 1234567890.123, 'failure_count': 0})
    try:
        raise ValueError('test')
    except Exception:
        msg.set_exception()
    # msg.set_exception(ValueError('test'))
    assert str(msg.exception) == 'test'
    assert msg.exception.exc_type is ValueError
    assert msg.failure_count == 1
    print(msg.to_json(True))
