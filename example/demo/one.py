class OneStep:

    def __init__(self, *args, **kwargs):
        print("__init__", kwargs)
        self.args = args
        self.kwargs = kwargs

    def __call__(self, fn, *_args, **_kwargs):
        def wrapper(*args, **kwargs):
            fn(*args, **kwargs)

        return wrapper


@OneStep(broker="1")
def hello(name):
    print(f"hello, {name}")


if __name__ == '__main__':
    hello("miclon")
