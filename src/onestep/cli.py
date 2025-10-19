import argparse
import importlib
import logging
import sys
from onestep import step, __version__

LOGFORMAT = "[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s"


def setup_logging():
    # 设置全局日志级别为INFO，避免第三方库的DEBUG日志输出
    logging.basicConfig(level=logging.INFO, format=LOGFORMAT, stream=sys.stdout)

    # exclude amqpstorm logs
    logging.getLogger("amqpstorm").setLevel(logging.CRITICAL)
    
    # 获取onestep的logger并设置为DEBUG级别以便调试
    onestep_logger = logging.getLogger("onestep")
    onestep_logger.setLevel(logging.DEBUG)
    
    return onestep_logger


logger = setup_logging()


def parse_args():
    parser = argparse.ArgumentParser(
        description='run onestep'
    )
    # 位置参数不允许放在互斥组中，这里改为手动校验
    parser.add_argument(
        "step", nargs='*',
        help="the run step(s)",
    )
    parser.add_argument(
        "--group", "-G", default=None,
        help="the run group",
        type=str
    )
    parser.add_argument(
        "--print",
        action="store_true",
        help="enable printing")
    parser.add_argument(
        "--path", "-P", default=".", nargs="*", type=str,
        help="the step import path (default: current running directory)"
    )
    parser.add_argument(
        "--cron",
        help="the cron expression to test",
        type=str
    )
    args = parser.parse_args()
    # 手动互斥校验：不可同时指定 step 与 --cron
    if args.cron and args.step:
        parser.error("`step` and `--cron` are mutually exclusive")
    # 未指定 cron 时，必须至少一个 step
    if not args.cron and len(args.step) == 0:
        parser.error("please specify at least one step module")
    return args


def main():
    args = parse_args()
    for path in args.path:
        sys.path.insert(0, path)
    if args.cron:
        from croniter import croniter
        from datetime import datetime
        cron = croniter(args.cron, datetime.now())
        for _ in range(10):
            print(cron.get_next(datetime))
        return

    logger.info(f"OneStep {__version__} is start up.")

    imported = 0
    for module in args.step:
        try:
            importlib.import_module(module)
            imported += 1
            continue
        except ModuleNotFoundError:
            # 兼容文件路径与斜杠风格：转换为模块路径再尝试
            alt = module
            if alt.endswith('.py'):
                alt = alt[:-3]
            alt = alt.replace('/', '.').replace('\\', '.')
            if alt != module:
                try:
                    importlib.import_module(alt)
                    logger.debug(f"Imported `{module}` as `{alt}`")
                    imported += 1
                    continue
                except ModuleNotFoundError:
                    pass
            logger.error(f"Module `{module}` not found.")

    if imported == 0:
        logger.error("No valid step modules were imported; exiting.")
        return

    try:
        step.start(group=args.group, block=True, print_jobs=args.print)
    except KeyboardInterrupt:
        step.shutdown()


if __name__ == '__main__':
    sys.exit(main())
