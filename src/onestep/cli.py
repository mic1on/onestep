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
    parser.add_argument(
        "step",
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
    return parser.parse_args()


def main():
    args = parse_args()
    for path in args.path:
        sys.path.insert(0, path)
    logger.info(f"OneStep {__version__} is start up.")
    try:
        importlib.import_module(args.step)
        step.start(group=args.group, block=True, print_jobs=args.print)
    except KeyboardInterrupt:
        step.shutdown()


if __name__ == '__main__':
    sys.exit(main())
