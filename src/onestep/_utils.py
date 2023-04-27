import functools
import logging


def catch_error(return_val=None):
    """
    捕获异常装饰器
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logging.debug(e)
                return return_val

        return wrapper

    return decorator