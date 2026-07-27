# CLI-Managed Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `onestep run` configure non-destructive stdout logging and task lifecycle event logs so application modules no longer repeat logging bootstrap code.

**Architecture:** Add an idempotent structured-event helper to `OneStepApp`, then keep process-level policy in `onestep.cli`. The CLI loads the target first, resolves `--log-level` against YAML state and the INFO default, installs a stdout handler only when the root logger is unconfigured, and enables the app helper unless task events are disabled. Direct runtime embedding remains untouched.

**Tech Stack:** Python 3.9+, standard-library `argparse` and `logging`, pytest, uv workspace lockfile

---

## File Map

- Modify `src/onestep/app.py`: expose idempotent `enable_structured_event_logging()` without changing normal app construction.
- Modify `src/onestep/cli.py`: parse run-only logging options, resolve levels, install the CLI handler, and enable task events.
- Modify `tests/contract/test_runtime_contract.py`: cover helper creation, reuse, and custom event logger preservation.
- Modify `tests/test_cli.py`: cover CLI parsing, precedence, handler ownership, event registration, and check isolation.
- Modify `example/cli_app.py`: demonstrate that CLI-run apps no longer bootstrap logging.
- Modify `README.md` and `README.zh-CN.md`: document normal, debug, disabled-event, YAML, and embedded usage.
- Modify `CHANGELOG.md`, `pyproject.toml`, and `uv.lock`: prepare core version `1.7.2` as required for shipping.

### Task 1: Idempotent Structured Event Logging Helper

**Files:**
- Modify: `src/onestep/app.py`
- Test: `tests/contract/test_runtime_contract.py`

- [ ] **Step 1: Write failing helper contract tests**

Add tests that verify a default logger is created once and a custom registered
instance is returned unchanged:

```python
def test_enable_structured_event_logging_is_idempotent() -> None:
    app = OneStepApp("structured-event-helper")

    first = app.enable_structured_event_logging()
    second = app.enable_structured_event_logging()

    assert isinstance(first, StructuredEventLogger)
    assert second is first
    assert app.describe()["hooks"]["events"] == 1


def test_enable_structured_event_logging_preserves_registered_logger() -> None:
    app = OneStepApp("custom-structured-event-helper")
    custom = StructuredEventLogger(logger=logging.getLogger("custom.events"))
    app.on_event(custom)

    resolved = app.enable_structured_event_logging()

    assert resolved is custom
    assert app.describe()["hooks"]["events"] == 1
```

- [ ] **Step 2: Run the focused tests and confirm the missing method failure**

Run:

```bash
uv run pytest tests/contract/test_runtime_contract.py -k enable_structured_event_logging -v
```

Expected: both tests fail with `AttributeError: 'OneStepApp' object has no attribute 'enable_structured_event_logging'`.

- [ ] **Step 3: Implement the helper**

Import `StructuredEventLogger` next to `TaskEvent`, then add this public method near
the event hook registration methods:

```python
def enable_structured_event_logging(self) -> StructuredEventLogger:
    for handler in self._event_handlers:
        if isinstance(handler, StructuredEventLogger):
            return handler
    handler = StructuredEventLogger()
    self.on_event(handler)
    return handler
```

The method intentionally checks only `StructuredEventLogger`; unrelated event hooks
must continue to coexist with the default logger.

- [ ] **Step 4: Run focused helper and existing event logger tests**

Run:

```bash
uv run pytest tests/contract/test_runtime_contract.py -k 'enable_structured_event_logging or structured_event_logger' -v
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit the helper**

```bash
git add src/onestep/app.py tests/contract/test_runtime_contract.py
git commit -m "feat: add structured event logging helper"
```

### Task 2: CLI Logging Bootstrap And Options

**Files:**
- Modify: `src/onestep/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing parser tests**

Import `parse_args` and add tests for the run-only options:

```python
from onestep.cli import main, parse_args


def test_cli_run_parses_logging_options() -> None:
    args = parse_args(
        ["run", "worker:app", "--log-level", "DEBUG", "--no-task-events"]
    )

    assert args.log_level == "DEBUG"
    assert args.task_events is False


def test_cli_run_enables_task_events_by_default() -> None:
    args = parse_args(["run", "worker:app"])

    assert args.log_level is None
    assert args.task_events is True
```

- [ ] **Step 2: Run parser tests and confirm unknown-argument failures**

Run:

```bash
uv run pytest tests/test_cli.py -k 'parses_logging_options or enables_task_events_by_default' -v
```

Expected: tests fail because `run` does not yet accept the options.

- [ ] **Step 3: Add run-only arguments**

Add these arguments to `run_parser`:

```python
run_parser.add_argument(
    "--log-level",
    choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
    default=None,
    help="Set the onestep logger level (default: YAML app.logging.level or INFO)",
)
run_parser.add_argument(
    "--task-events",
    action=argparse.BooleanOptionalAction,
    default=True,
    help="Emit task lifecycle events as logs (default: enabled)",
)
```

Python 3.9 is the minimum supported version, so `BooleanOptionalAction` is available.

- [ ] **Step 4: Run parser tests and CLI help coverage**

Run:

```bash
uv run pytest tests/test_cli.py -k 'parses_logging_options or enables_task_events_by_default or cli_help' -v
```

Expected: all selected tests pass and existing help behavior remains valid.

- [ ] **Step 5: Write failing runtime bootstrap tests**

Add a context manager that snapshots and restores root/onestep logger state so tests
cannot leak global logging mutations:

```python
@contextmanager
def isolated_logging():
    root = logging.getLogger()
    framework = logging.getLogger("onestep")
    root_handlers = list(root.handlers)
    root_level = root.level
    framework_level = framework.level
    try:
        root.handlers = []
        root.setLevel(logging.WARNING)
        framework.setLevel(logging.NOTSET)
        yield root, framework
    finally:
        root.handlers = root_handlers
        root.setLevel(root_level)
        framework.setLevel(framework_level)
```

Use a stubbed `app.run` to inspect state immediately before execution:

```python
def test_cli_run_configures_info_stdout_logging_and_task_events(capsys) -> None:
    app = OneStepApp("cli-logging-default")
    observed = {}

    def run() -> None:
        observed["level"] = logging.getLogger("onestep").level
        observed["event_hooks"] = app.describe()["hooks"]["events"]
        logging.getLogger("onestep.cli-logging-default").info("business message")

    app.run = run
    with isolated_logging(), registered_module("testsupport_cli_logging", app=app):
        assert main(["run", "testsupport_cli_logging:app"]) == 0

    assert observed == {"level": logging.INFO, "event_hooks": 1}
    assert "business message" in capsys.readouterr().out
```

Add companion tests for level selection and event registration:

```python
def test_cli_run_explicit_log_level_wins() -> None:
    app = OneStepApp("cli-logging-debug")
    observed = {}
    app.run = lambda: observed.setdefault("level", logging.getLogger("onestep").level)

    with isolated_logging(), registered_module("testsupport_cli_debug", app=app):
        assert main(["run", "testsupport_cli_debug:app", "--log-level", "DEBUG"]) == 0

    assert observed["level"] == logging.DEBUG


def test_cli_run_can_disable_automatic_task_events() -> None:
    app = OneStepApp("cli-no-task-events")
    app.run = lambda: None

    with isolated_logging(), registered_module("testsupport_cli_no_events", app=app):
        assert main(["run", "testsupport_cli_no_events:app", "--no-task-events"]) == 0

    assert app.describe()["hooks"]["events"] == 0


@pytest.mark.parametrize("disable_automatic", [False, True])
def test_cli_run_preserves_registered_structured_event_logger(
    disable_automatic: bool,
) -> None:
    app = OneStepApp("cli-custom-task-events")
    custom = StructuredEventLogger(logger=logging.getLogger("custom.events"))
    app.on_event(custom)
    app.run = lambda: None
    argv = ["run", "testsupport_cli_custom_events:app"]
    if disable_automatic:
        argv.append("--no-task-events")

    with isolated_logging(), registered_module(
        "testsupport_cli_custom_events", app=app
    ):
        assert main(argv) == 0

    assert app.describe()["hooks"]["events"] == 1


def test_cli_run_preserves_existing_root_handler() -> None:
    app = OneStepApp("cli-existing-handler")
    app.run = lambda: None
    existing = logging.StreamHandler()
    formatter = logging.Formatter("CUSTOM %(message)s")
    existing.setFormatter(formatter)

    with isolated_logging() as (root, _), registered_module(
        "testsupport_cli_existing_handler", app=app
    ):
        root.addHandler(existing)
        assert main(["run", "testsupport_cli_existing_handler:app"]) == 0
        assert root.handlers == [existing]
        assert existing.formatter is formatter


def test_cli_check_does_not_configure_logging_or_task_events() -> None:
    app = OneStepApp("cli-check-logging")

    with isolated_logging() as (root, framework), registered_module(
        "testsupport_cli_check_logging", app=app
    ):
        assert main(["check", "testsupport_cli_check_logging:app"]) == 0
        assert root.handlers == []
        assert framework.level == logging.NOTSET

    assert app.describe()["hooks"]["events"] == 0
```

Add YAML precedence tests using a temporary config, `registered_yaml_module()`, and a
class-level `run` stub that records the loaded app's state:

```python
@pytest.mark.parametrize(
    ("cli_level", "expected"),
    [(None, logging.ERROR), ("DEBUG", logging.DEBUG)],
)
def test_cli_log_level_precedence_over_yaml(
    monkeypatch, tmp_path, cli_level: str | None, expected: int
) -> None:
    config_path = tmp_path / "logging.yaml"
    config_path.write_text(
        json.dumps({"app": {"name": "yaml-logging", "logging": {"level": "ERROR"}}, "tasks": []}),
        encoding="utf-8",
    )
    observed = {}
    monkeypatch.setattr(
        OneStepApp,
        "run",
        lambda self: observed.update(
            level=logging.getLogger("onestep").level,
            event_hooks=self.describe()["hooks"]["events"],
        ),
    )
    argv = ["run", str(config_path)]
    if cli_level is not None:
        argv.extend(["--log-level", cli_level])

    with isolated_logging(), registered_yaml_module():
        assert main(argv) == 0

    assert observed == {"level": expected, "event_hooks": 1}
```

- [ ] **Step 6: Run bootstrap tests and confirm failures**

Run:

```bash
uv run pytest tests/test_cli.py -k 'cli_logging or log_level_precedence or task_events' -v
```

Expected: the new runtime tests fail because CLI logging setup and automatic event registration do not exist.

- [ ] **Step 7: Implement logging resolution and setup**

Add imports and private helpers in `src/onestep/cli.py`:

```python
import logging

_CLI_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def _configure_run_logging(*, explicit_level: str | None) -> None:
    framework_logger = logging.getLogger("onestep")
    if explicit_level is not None:
        framework_logger.setLevel(getattr(logging, explicit_level))
    elif framework_logger.level == logging.NOTSET:
        framework_logger.setLevel(logging.INFO)

    root_logger = logging.getLogger()
    if root_logger.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_CLI_LOG_FORMAT))
    root_logger.addHandler(handler)
```

After loading the app and after the `check` early return, initialize `run`:

```python
try:
    _configure_run_logging(explicit_level=args.log_level)
    if args.task_events:
        app.enable_structured_event_logging()
    app.run()
except Exception as exc:
    print(f"onestep: {args.target} failed while running: {exc}", file=sys.stderr)
    return 1
```

The `framework_logger.level == logging.NOTSET` check preserves a YAML-applied explicit
level. An explicit CLI option always replaces it.

- [ ] **Step 8: Run focused and full CLI tests**

Run:

```bash
uv run pytest tests/test_cli.py -k 'cli_logging or log_level_precedence or task_events' -v
uv run pytest tests/test_cli.py -v
```

Expected: both commands pass.

- [ ] **Step 9: Commit CLI behavior**

```bash
git add src/onestep/cli.py tests/test_cli.py
git commit -m "feat: manage runtime logging from the CLI"
```

### Task 3: Application Guidance And Examples

**Files:**
- Modify: `example/cli_app.py`
- Modify: `README.md`
- Modify: `README.zh-CN.md`

- [ ] **Step 1: Simplify the CLI application example**

Remove `_build_logger()`, the `StructuredEventLogger` import, and
`app.on_event(StructuredEventLogger(...))`. Keep a normal business logger:

```python
import json
import logging
import os

from onestep import IntervalSource, OneStepApp

logger = logging.getLogger("onestep.cli_app")
```

Replace the handler's `print(json.dumps(...))` call with:

```python
logger.info(
    "synced users",
    extra={"service_name": ctx.config["service_name"], "payload": payload},
)
```

Do not simplify `example/runtime_showcase.py`; its JSON formatter is an intentional
custom logging example.

- [ ] **Step 2: Document CLI-managed logging in English**

Add a concise section near the Python/YAML run instructions containing:

```markdown
### Logging

`onestep run` writes framework and task lifecycle logs to stdout at INFO level by
default. Application code only needs a logger under the `onestep` namespace:

```python
logger = logging.getLogger("onestep.billing_sync")
```

Use `--log-level DEBUG` for fetched/started and sink-success details, or
`--no-task-events` to disable the CLI-installed lifecycle logger. Existing logging
handlers are preserved. Direct `app.run()` / `app.serve()` embedding does not modify
host logging.
```

Also state that an explicit CLI level overrides YAML `app.logging.level`.

- [ ] **Step 3: Add equivalent Chinese guidance**

Document the same commands and ownership boundary in `README.zh-CN.md`, including:

```markdown
`onestep run` 默认以 INFO 级别将框架日志和任务生命周期事件写到 stdout。
应用代码只需创建 `onestep` 命名空间下的业务 logger，不再需要调用
`logging.basicConfig(force=True)` 或注册标准 `StructuredEventLogger`。
```

- [ ] **Step 4: Verify documentation and example syntax**

Run:

```bash
uv run python -m py_compile example/cli_app.py
rg -n -- "--log-level|--no-task-events|basicConfig" README.md README.zh-CN.md example/cli_app.py
git diff --check
```

Expected: compilation succeeds, both READMEs mention both options, the example has no
`basicConfig`, and `git diff --check` produces no output.

- [ ] **Step 5: Commit documentation and example updates**

```bash
git add example/cli_app.py README.md README.zh-CN.md
git commit -m "docs: simplify CLI application logging"
```

### Task 4: Release Preparation And Verification

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add the 1.7.2 changelog entry**

Insert at the top of `CHANGELOG.md`:

```markdown
## 1.7.2

- Makes `onestep run` configure non-destructive INFO-level stdout logging and task lifecycle event logs by default.
- Adds `--log-level` and `--no-task-events` while preserving application-installed handlers and structured event loggers.
- Keeps direct `OneStepApp.run()` / `OneStepApp.serve()` embedding in full control of host process logging.
```

- [ ] **Step 2: Bump the core version and refresh the lockfile**

Change the root project version:

```toml
version = "1.7.2"
```

Then run:

```bash
uv lock
```

Expected: the root `onestep` package entry in `uv.lock` changes from `1.7.1` to `1.7.2`; plugin versions remain unchanged.

- [ ] **Step 3: Run focused tests**

```bash
uv run pytest tests/test_cli.py tests/contract/test_runtime_contract.py -v
```

Expected: all tests pass.

- [ ] **Step 4: Run the complete core test suite**

```bash
uv run pytest -v
```

Expected: all core tests pass; live integration tests remain skipped unless their infrastructure is configured.

- [ ] **Step 5: Run package and diff checks**

```bash
uv build
git diff --check main...HEAD
git status --short
```

Expected: wheel and sdist build successfully, diff check is clean, and only intended release files remain uncommitted.

- [ ] **Step 6: Commit release preparation**

```bash
git add CHANGELOG.md pyproject.toml uv.lock
git commit -m "chore: prepare onestep 1.7.2"
```

- [ ] **Step 7: Review the complete branch**

```bash
git log --oneline main..HEAD
git diff --stat main...HEAD
git diff --check main...HEAD
```

Expected: the branch contains the design, helper, CLI, docs, and release commits with no unrelated files.

- [ ] **Step 8: Push and open the pull request**

```bash
git push -u origin feat/cli-logging
gh pr create --base main --head feat/cli-logging \
  --title "feat: manage runtime logging from the CLI" \
  --body $'Closes #84\n\n## Summary\n- configure non-destructive stdout logging for `onestep run`\n- emit task lifecycle logs by default with opt-out and level controls\n- simplify application logging setup and document embedding boundaries\n\n## Verification\n- `uv run pytest -v`\n- `uv build`'
```

Expected: GitHub returns a pull request URL linked to the implementation issue.

Do not create or push `v1.7.2` while opening the PR. The annotated tag belongs to the
actual package release after the PR is merged.
