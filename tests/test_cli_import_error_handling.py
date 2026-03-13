import logging
import sys

import pytest

from onestep import cli


def test_step_module_not_found(monkeypatch, caplog):
    monkeypatch.setattr(sys, "argv", ["onestep", "this_module_should_not_exist_12345"])
    with caplog.at_level(logging.INFO, logger="onestep"):
        code = cli.main()
    assert code == 2
    assert "step module not found: this_module_should_not_exist_12345" in caplog.text


def test_step_import_missing_dependency(tmp_path, monkeypatch, caplog):
    module_name = "onestep_test_step_missing_dep_12345"
    module_path = tmp_path / f"{module_name}.py"
    module_path.write_text("import this_dependency_should_not_exist_12345\n", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["onestep", module_name, "-P", str(tmp_path)])
    with caplog.at_level(logging.INFO, logger="onestep"):
        code = cli.main()

    assert code == 1
    assert f"failed to import step module: {module_name}" in caplog.text
    assert "caused by missing dependency" in caplog.text

    sys.modules.pop(module_name, None)


def test_step_import_other_exception(tmp_path, monkeypatch, caplog):
    module_name = "onestep_test_step_raise_12345"
    module_path = tmp_path / f"{module_name}.py"
    module_path.write_text("raise RuntimeError('boom')\n", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["onestep", module_name, "-P", str(tmp_path)])
    with caplog.at_level(logging.INFO, logger="onestep"):
        code = cli.main()

    assert code == 1
    assert f"failed to import step module: {module_name}" in caplog.text
    assert "import error" in caplog.text

    sys.modules.pop(module_name, None)
