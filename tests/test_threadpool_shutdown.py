"""ThreadPoolWorker 优雅关闭测试"""
import threading
import time
from queue import Queue

import pytest

from onestep import MemoryBroker
from onestep.onestep import SyncOneStep
from onestep.worker import ThreadPoolWorker


def test_threadpool_shutdown_waits_for_completion():
    """测试 ThreadPoolWorker shutdown 会等待任务完成"""
    # 使用一个标志来跟踪任务是否执行完成
    task_completed = threading.Event()
    task_started = threading.Event()

    def task(message):
        task_started.set()  # 标记任务已开始
        time.sleep(0.5)  # 模拟耗时任务
        task_completed.set()  # 标记任务已完成

    # 创建 broker 和 worker
    broker = MemoryBroker()
    onestep = SyncOneStep(fn=task)
    worker = ThreadPoolWorker(onestep, broker, workers=2)

    # 启动 worker
    worker.start()

    # 发送消息
    broker.publish("test")
    task_started.wait(timeout=1)  # 等待任务开始

    # 关闭 worker（应该等待任务完成）
    worker.shutdown()

    # 验证任务已完成
    assert task_completed.is_set(), "任务应该在 shutdown 之前完成"


def test_threadpool_shutdown_handles_no_tasks():
    """测试 ThreadPoolWorker shutdown 在没有任务时也能正常工作"""
    broker = MemoryBroker()
    onestep = SyncOneStep(fn=lambda msg: None)
    worker = ThreadPoolWorker(onestep, broker, workers=2)

    worker.start()
    time.sleep(0.1)  # 给一点启动时间

    # 没有发送任何消息，直接关闭
    worker.shutdown()

    # 应该正常关闭，没有异常
    assert worker._shutdown


def test_threadpool_concurrent_shutdown():
    """测试并发调用 shutdown 不会出错"""
    broker = MemoryBroker()
    onestep = SyncOneStep(fn=lambda msg: time.sleep(0.1))
    worker = ThreadPoolWorker(onestep, broker, workers=2)

    worker.start()

    # 发送一些消息
    for _ in range(5):
        broker.publish("test")

    time.sleep(0.1)

    # 并发关闭
    threads = []
    for _ in range(3):
        t = threading.Thread(target=worker.shutdown)
        t.start()
        threads.append(t)

    for t in threads:
        t.join(timeout=2)

    assert worker._shutdown


def test_threadpool_executor_shutdowns_properly():
    """测试线程池在 shutdown 后不再接受新任务"""
    broker = MemoryBroker()
    onestep = SyncOneStep(fn=lambda msg: time.sleep(0.1))
    worker = ThreadPoolWorker(onestep, broker, workers=2)

    worker.start()

    # 关闭 worker
    worker.shutdown()

    # 验证 executor 已关闭
    assert worker.executor._shutdown


def test_threadpool_multiple_messages():
    """测试线程池可以并发处理多个消息"""
    completed_count = 0
    lock = threading.Lock()

    def task(message):
        nonlocal completed_count
        time.sleep(0.2)
        with lock:
            completed_count += 1

    broker = MemoryBroker()
    onestep = SyncOneStep(fn=task)
    worker = ThreadPoolWorker(onestep, broker, workers=3)

    worker.start()

    # 发送 5 个消息
    for _ in range(5):
        broker.publish("test")

    # 等待所有任务完成
    time.sleep(1)

    # 关闭
    worker.shutdown()

    # 验证所有消息都被处理了
    assert completed_count == 5, f"应该处理 5 个消息，实际处理了 {completed_count} 个"
