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
        description='OneStep - 分布式异步任务框架',
        epilog='Examples:\n'
               '  onestep example.py                    # 运行 example.py 中的 step\n'
               '  onestep --group mygroup example.py   # 运行指定组的 step\n'
               '  onestep --cron "*/5 * * * *"          # 测试 cron 表达式\n'
               '  onestep --version                     # 显示版本号',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "step", nargs='?',
        help="要运行的 step 模块名称（如 example）",
        metavar='MODULE'
    )
    parser.add_argument(
        "--version", "-v",
        action="version",
        version=f"OneStep {__version__}"
    )
    parser.add_argument(
        "--group", "-G", default=None,
        help="要运行的组名称 (默认: OneStep)",
        type=str,
        metavar='GROUP'
    )
    parser.add_argument(
        "--print",
        action="store_true",
        help="启动前打印所有已注册的 job 信息"
    )
    parser.add_argument(
        "--path", "-P", default=".", nargs="*", type=str,
        help="模块导入路径，可指定多个 (默认: 当前目录)",
        metavar='PATH'
    )
    group.add_argument(
        "--cron",
        help="测试 cron 表达式，显示未来 10 次执行时间",
        type=str,
        metavar='EXPR'
    )
    return parser.parse_args()


def main():
    args = parse_args()
    for path in args.path:
        sys.path.insert(0, path)
    if args.cron:
        from croniter import croniter  # type: ignore[import-untyped]
        from datetime import datetime
        try:
            cron = croniter(args.cron, datetime.now())
            print(f"Cron 表达式: {args.cron}")
            print("未来 10 次执行时间:")
            for i, next_time in enumerate([cron.get_next(datetime) for _ in range(10)], 1):
                print(f"  {i}. {next_time.strftime('%Y-%m-%d %H:%M:%S')}")
        except ValueError as e:
            logger.error(f"无效的 cron 表达式: {args.cron}")
            logger.error(f"错误: {e}")
            return 1
        return 0

    logger.info(f"OneStep {__version__} 启动中...")
    try:
        importlib.import_module(args.step)
        step.start(group=args.group, block=True, print_jobs=args.print)
        return 0
    except ModuleNotFoundError as e:
        missing_module = getattr(e, "name", None)
        if missing_module == args.step:
            logger.error(f"❌ 找不到模块: {args.step}")
            logger.error(f"请检查:")
            logger.error(f"  1. 模块名称是否正确")
            logger.error(f"  2. 文件是否存在于指定路径: {' '.join(args.path)}")
            logger.error(f"  3. 是否需要使用 --path 参数指定模块路径")
            return 2
        logger.error(f"❌ 导入模块失败: {args.step}")
        logger.error(f"缺少依赖: {missing_module}")
        logger.error(f"请使用 'pip install {missing_module}' 安装缺失的依赖")
        logger.exception("详细信息:")
        return 1
    except ImportError as e:
        logger.error(f"❌ 导入模块失败: {args.step}")
        logger.error(f"原因: {e}")
        logger.exception("详细信息:")
        return 1
    except Exception as e:
        logger.error(f"❌ 启动失败: {args.step}")
        logger.error(f"错误类型: {type(e).__name__}")
        logger.error(f"错误信息: {e}")
        logger.exception("详细信息:")
        return 1
    except KeyboardInterrupt:
        logger.info("接收到中断信号，正在关闭...")
        step.shutdown()
        logger.info("已关闭")
        return 130


if __name__ == '__main__':
    sys.exit(main())
