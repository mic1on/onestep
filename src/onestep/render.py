"""Render a :class:`~onestep.OneStepApp` worker topology as a Mermaid flowchart.

The renderer is a read-only mapping from an app's task specs to text: it adds
no runtime behavior and no dependencies. Resource nodes are deduplicated by
``(name, type)``, so a resource shared by multiple tasks (for example a queue
that one task emits to and another task consumes from) appears once and the
chart shows the chaining for free.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import OneStepApp
    from .connectors.base import Sink
    from .task import TaskSpec

__all__ = ["render_mermaid"]


def render_mermaid(app: "OneStepApp") -> str:
    """Render an app's worker topology as a Mermaid ``graph LR`` chart."""

    lines = ["graph LR", f"  %% app: {_escape_comment(app.name)}"]

    nodes = _NodeRegistry()
    edges: list[str] = []

    for task in app.tasks:
        task_id = nodes.task(task)
        if task.source is not None:
            edges.append(f"  {nodes.resource(task.source)} --> {task_id}")

        routed = Counter(_render_routes(task, nodes, edges, task_id))

        for binding in task.emit_bindings:
            key = _sink_key(binding.sink)
            if routed[key] > 0:
                routed[key] -= 1
                continue
            label = _edge_label("emit", binding.transform_ref)
            edges.append(f"  {task_id} -->|{label}| {nodes.resource(binding.sink)}")

        for sink in task.dead_letter_sinks:
            label = _edge_label("dead_letter")
            edges.append(f"  {task_id} -.->|{label}| {nodes.resource(sink)}")

    lines.extend(nodes.declarations())
    lines.extend(edges)
    return "\n".join(lines) + "\n"


def _render_routes(
    task: "TaskSpec",
    nodes: "_NodeRegistry",
    edges: list[str],
    task_id: str,
) -> list[tuple[str, str]]:
    """Render conditional emit routes and return the sink keys they covered."""

    route_sink_keys: list[tuple[str, str]] = []
    for route in task.emit_routes:
        # A plain sink (``emit=some_sink``) is normalized into a route without
        # a predicate; render it as an ordinary emit edge.
        then_prefix = f"when {route.predicate_ref}" if route.predicate_ref else "emit"
        for bindings, prefix in (
            (route.then_bindings, then_prefix),
            (route.otherwise_bindings, "otherwise"),
        ):
            for binding in bindings:
                route_sink_keys.append(_sink_key(binding.sink))
                label = _edge_label(prefix, binding.transform_ref)
                edges.append(f"  {task_id} -->|{label}| {nodes.resource(binding.sink)}")
    return route_sink_keys


def _sink_key(sink: "Sink") -> tuple[str, str]:
    return (str(sink.name), type(sink).__name__)


def _edge_label(prefix: str, transform_ref: str | None = None) -> str:
    label = prefix
    if transform_ref:
        label = f"{label} · {transform_ref}"
    return f'"{_escape_text(label)}"'


class _NodeRegistry:
    """Allocate node ids, deduplicating resources by ``(name, type)``."""

    def __init__(self) -> None:
        self._nodes: dict[tuple[str, str, str], str] = {}
        self._labels: dict[str, str] = {}

    def resource(self, sink: "Sink") -> str:
        name = str(sink.name)
        type_name = type(sink).__name__
        return self._register(
            ("resource", name, type_name),
            f"{_escape_text(name)}<br/>{_escape_text(type_name)}",
        )

    def task(self, task: "TaskSpec") -> str:
        label = _escape_text(task.name)
        meta = _task_meta(task)
        if meta:
            label = f"{label}<br/>{meta}"
        return self._register(("task", task.name, ""), label)

    def _register(self, key: tuple[str, str, str], label: str) -> str:
        node_id = self._nodes.get(key)
        if node_id is None:
            node_id = f"n{len(self._labels)}"
            self._nodes[key] = node_id
            self._labels[node_id] = label
        return node_id

    def declarations(self) -> list[str]:
        return [f'  {node_id}["{label}"]' for node_id, label in self._labels.items()]


def _task_meta(task: "TaskSpec") -> str:
    parts = [f"concurrency={task.concurrency}", f"retry={type(task.retry).__name__}"]
    if task.timeout_s is not None:
        parts.append(f"timeout={_format_seconds(task.timeout_s)}s")
    return " · ".join(parts)


def _format_seconds(value: float) -> str:
    return f"{value:g}"


def _escape_text(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("|", "&#124;")
    )


def _escape_comment(text: str) -> str:
    return str(text).replace("\n", " ")
