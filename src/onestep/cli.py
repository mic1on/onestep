import argparse
import importlib
import logging
import sys
from onestep import step, __version__

LOGFORMAT = "[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s"


def setup_logging():
    logging.basicConfig(level=logging.DEBUG, format=LOGFORMAT, stream=sys.stdout)

    # exclude amqpstorm logs
    logging.getLogger("amqpstorm").setLevel(logging.CRITICAL)
    return logging.getLogger("onestep")


logger = setup_logging()


def parse_args():
    parser = argparse.ArgumentParser(
        description='run onestep'
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "step", nargs='?',
        help="the run step",
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
    group.add_argument(
        "--cron",
        help="the cron expression to test",
        type=str
    )
    return parser.parse_args()


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
    try:
        importlib.import_module(args.step)
        step.start(group=args.group, block=True, print_jobs=args.print)
    except ModuleNotFoundError:
        logger.error(f"Module `{args.step}` not found.")
    except KeyboardInterrupt:
        step.shutdown()


if __name__ == '__main__':
    sys.exit(main())
