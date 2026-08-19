import sys
import types

import pytest

from onestep import MemoryQueue, OneStepApp
from onestep.cli import main, parse_args
from onestep.render import render_mermaid
from onestep.task import EmitBinding, EmitRoute


def _handler_module():
    module = types.ModuleType("render_handlers")

    async def sync(ctx, payload):
        return payload

    module.sync = sync
    return module


def _lines(output: str) -> list[str]:
    return [line.strip() for line in output.splitlines()]


def test_render_mermaid_basic_topology() -> None:
    app = OneStepApp("render-basic")

    @app.task(source=MemoryQueue("incoming"), emit=MemoryQueue("processed"))
    async def consume(ctx, payload):
        return payload

    output = render_mermaid(app)
    lines = _lines(output)

    assert lines[0] == "graph LR"
    assert "%% app: render-basic" in lines
    assert 'n0["consume<br/>concurrency=1 · retry=NoRetry"]' in lines
    assert 'n1["incoming<br/>MemoryQueue"]' in lines
    assert 'n2["processed<br/>MemoryQueue"]' in lines
    assert "n1 --> n0" in lines
    assert 'n0 -->|"emit"| n2' in lines


def test_render_mermaid_task_meta_includes_timeout_and_concurrency() -> None:
    app = OneStepApp("render-meta")

    @app.task(source=MemoryQueue("in"), emit=MemoryQueue("out"), concurrency=4, timeout_s=300.0)
    async def consume(ctx, payload):
        return payload

    output = render_mermaid(app)
    assert "concurrency=4 · retry=NoRetry · timeout=300s" in output


def test_render_mermaid_shared_resource_deduplicates_across_tasks() -> None:
    shared = MemoryQueue("shared")
    app = OneStepApp("render-shared")

    @app.task(source=MemoryQueue("in"), emit=shared)
    async def first(ctx, payload):
        return payload

    @app.task(source=shared, emit=MemoryQueue("out"))
    async def second(ctx, payload):
        return payload

    output = render_mermaid(app)
    lines = _lines(output)

    shared_declarations = [line for line in lines if '"shared<br/>MemoryQueue"]' in line]
    assert len(shared_declarations) == 1
    shared_id = shared_declarations[0].split("[", 1)[0]
    first_id = next(line.split("[", 1)[0] for line in lines if '"first<br/>' in line)
    second_id = next(line.split("[", 1)[0] for line in lines if '"second<br/>' in line)
    assert f'{first_id} -->|"emit"| {shared_id}' in lines
    assert f"{shared_id} --> {second_id}" in lines


def test_render_mermaid_emit_transform_label() -> None:
    app = OneStepApp("render-transform")

    def project(ctx, payload, result):
        return result

    @app.task(
        source=MemoryQueue("in"),
        emit=EmitBinding(
            sink=MemoryQueue("out"),
            transform=project,
            transform_ref="pkg.transforms:project",
        ),
    )
    async def consume(ctx, payload):
        return payload

    output = render_mermaid(app)
    assert 'n0 -->|"emit · pkg.transforms:project"| n2' in _lines(output)


def test_render_mermaid_conditional_routes() -> None:
    app = OneStepApp("render-route")

    def is_active(ctx, payload, result):
        return True

    @app.task(
        source=MemoryQueue("in"),
        emit=EmitRoute(
            predicate=is_active,
            predicate_ref="pkg.predicates:is_active",
            then_bindings=(
                EmitBinding(
                    sink=MemoryQueue("active"),
                    transform=lambda ctx, p, r: r,
                    transform_ref="pkg.transforms:tag",
                ),
            ),
            otherwise_sinks=(MemoryQueue("fallback"),),
        ),
    )
    async def consume(ctx, payload):
        return payload

    output = render_mermaid(app)
    lines = _lines(output)

    assert 'n0 -->|"when pkg.predicates:is_active · pkg.transforms:tag"| n2' in lines
    assert 'n0 -->|"otherwise"| n3' in lines
    # Route sinks must not be re-rendered as plain emit edges.
    assert 'n0 -->|"emit"|' not in lines


def test_render_mermaid_plain_sink_next_to_route() -> None:
    app = OneStepApp("render-mixed")

    def is_active(ctx, payload, result):
        return True

    @app.task(
        source=MemoryQueue("in"),
        emit=[
            MemoryQueue("audit"),
            EmitRoute(
                predicate=is_active,
                predicate_ref="pkg.predicates:is_active",
                then_sinks=(MemoryQueue("active"),),
            ),
        ],
    )
    async def consume(ctx, payload):
        return payload

    output = render_mermaid(app)
    lines = _lines(output)

    audit_declarations = [line for line in lines if '"audit<br/>MemoryQueue"]' in line]
    assert len(audit_declarations) == 1
    audit_id = audit_declarations[0].split("[", 1)[0]
    # The plain sink keeps its ordinary emit edge; only the route sinks are
    # covered by the conditional branch edges.
    assert f'n0 -->|"emit"| {audit_id}' in lines
    assert 'n0 -->|"when pkg.predicates:is_active"| n3' in lines


def test_render_mermaid_dead_letter_edge() -> None:
    app = OneStepApp("render-dlq")

    @app.task(source=MemoryQueue("in"), emit=MemoryQueue("out"), dead_letter=MemoryQueue("dlq"))
    async def consume(ctx, payload):
        return payload

    output = render_mermaid(app)
    assert 'n0 -.->|"dead_letter"| n3' in _lines(output)


def test_render_mermaid_escapes_special_characters() -> None:
    app = OneStepApp("render-escape")

    @app.task(
        source=MemoryQueue('weird & "name"'),
        emit=EmitBinding(
            sink=MemoryQueue("out"),
            transform=lambda ctx, p, r: r,
            transform_ref="pkg.transforms:pipe|quote",
        ),
    )
    async def consume(ctx, payload):
        return payload

    output = render_mermaid(app)
    assert "weird &amp; &quot;name&quot;" in output
    assert "emit · pkg.transforms:pipe&#124;quote" in output


def test_render_mermaid_empty_app() -> None:
    output = render_mermaid(OneStepApp("empty"))
    assert _lines(output) == ["graph LR", "%% app: empty"]


def test_cli_render_prints_mermaid(capsys) -> None:
    app = OneStepApp("cli-render")

    @app.task(source=MemoryQueue("in"), emit=MemoryQueue("out"))
    async def consume(ctx, payload):
        return payload

    module = types.ModuleType("testsupport_cli_render")
    module.app = app
    sys.modules["testsupport_cli_render"] = module
    try:
        exit_code = main(["render", "testsupport_cli_render:app"])
    finally:
        sys.modules.pop("testsupport_cli_render", None)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out.startswith("graph LR")
    assert "%% app: cli-render" in captured.out
    assert "consume" in captured.out


def test_cli_render_rejects_unknown_format() -> None:
    with pytest.raises(SystemExit):
        parse_args(["render", "worker.yaml", "--format", "ascii"])


def test_cli_render_is_not_swallowed_by_run_shorthand() -> None:
    args = parse_args(["render", "worker.yaml"])
    assert args.command == "render"
    assert args.format == "mermaid"


def test_cli_render_accepts_yaml_target(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    module = _handler_module()
    sys.modules["render_handlers"] = module
    try:
        (tmp_path / "worker.yaml").write_text(
            "\n".join(
                [
                    "apiVersion: onestep/v1alpha1",
                    "kind: App",
                    "app:",
                    "  name: render-yaml",
                    "resources:",
                    "  incoming:",
                    "    type: memory",
                    "  outgoing:",
                    "    type: memory",
                    "tasks:",
                    "  - name: sync",
                    "    source: incoming",
                    "    handler:",
                    "      ref: render_handlers:sync",
                    "    emit:",
                    "      - outgoing",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        exit_code = main(["render", "worker.yaml"])
    finally:
        sys.modules.pop("render_handlers", None)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out.startswith("graph LR")
    assert "%% app: render-yaml" in captured.out
    assert "sync" in captured.out
