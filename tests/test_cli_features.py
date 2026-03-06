"""CLI 功能测试"""
import sys
import pytest

from onestep import cli


def test_version_argument(capsys):
    """测试 --version 参数"""
    sys.argv = ["onestep", "--version"]
    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    # --version 会触发 SystemExit(0)
    assert exc_info.value.code == 0

    captured = capsys.readouterr()
    assert "OneStep" in captured.out


def test_cron_valid_expression(capsys):
    """测试有效的 cron 表达式"""
    sys.argv = ["onestep", "--cron", "*/5 * * * *"]
    code = cli.main()

    assert code == 0
    captured = capsys.readouterr()
    assert "Cron 表达式: */5 * * * *" in captured.out
    assert "未来 10 次执行时间:" in captured.out


def test_cron_invalid_expression(capsys, caplog):
    """测试无效的 cron 表达式"""
    sys.argv = ["onestep", "--cron", "invalid"]
    code = cli.main()

    assert code == 1
    assert "无效的 cron 表达式: invalid" in caplog.text


def test_help_argument(capsys):
    """测试 --help 参数"""
    sys.argv = ["onestep", "--help"]
    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    # --help 会触发 SystemExit(0)
    assert exc_info.value.code == 0

    captured = capsys.readouterr()
    assert "OneStep - 分布式异步任务框架" in captured.out
    assert "Examples:" in captured.out
    assert "运行 example.py 中的 step" in captured.out
