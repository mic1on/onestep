from __PACKAGE_NAME__.transforms.demo import build_message


async def run_demo(ctx, item):
    message = build_message(item)
    print(message)
