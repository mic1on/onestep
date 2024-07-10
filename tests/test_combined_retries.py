from onestep.retry import AllRetry, AnyRetry, RetryStatus


def test_all_retry():
    def retry1(message):
        return RetryStatus.CONTINUE

    def retry2(message):
        return RetryStatus.END_WITH_CALLBACK

    all_retry = AllRetry(retry1, retry2)

    assert all_retry("") == RetryStatus.END_WITH_CALLBACK
    all_retry = AllRetry(retry1, retry1)

    assert all_retry("") == RetryStatus.CONTINUE


# 测试 AnyRetry 类
def test_any_retry():
    def retry1(message):
        return RetryStatus.CONTINUE

    def retry2(message):
        return RetryStatus.END_WITH_CALLBACK

    any_retry = AnyRetry(retry1, retry2)
    assert any_retry("") == RetryStatus.CONTINUE

    any_retry = AnyRetry(retry1, retry1)
    assert any_retry("") == RetryStatus.CONTINUE

    any_retry = AnyRetry(retry2, retry2)
    assert any_retry("") == RetryStatus.END_WITH_CALLBACK
