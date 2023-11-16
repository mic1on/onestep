import pytest
from onestep.message import Extra


@pytest.fixture
def extra_instance():
    # 创建一个 Extra 实例供测试使用
    return Extra()


def test_to_dict(extra_instance):
    # 测试 to_dict 方法是否返回正确的字典
    expected_dict = {
        'task_id': extra_instance.task_id,
        'publish_time': extra_instance.publish_time,
        'failure_count': extra_instance.failure_count,
    }
    assert extra_instance.to_dict() == expected_dict


def test_str(extra_instance):
    # 测试 __str__ 方法是否返回正确的字符串表示
    expected_str = str(extra_instance.to_dict())
    assert str(extra_instance) == expected_str


def test_custom_values():
    # 测试使用自定义值创建 Extra 实例是否正确
    task_id = 'custom_task_id'
    publish_time = 123456789.0
    failure_count = 2

    extra = Extra(task_id=task_id, publish_time=publish_time, failure_count=failure_count)
    assert extra.task_id == task_id
    assert extra.publish_time == publish_time
    assert extra.failure_count == failure_count


def test_default_values():
    # 测试使用默认值创建 Extra 实例是否正确
    extra = Extra()
    assert isinstance(extra.task_id, str)
    assert isinstance(extra.publish_time, float)
    assert extra.failure_count == 0
