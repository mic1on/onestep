from __future__ import annotations

import ast
import re
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

from onestep_control_plane_api.pipeline_builder.connectors import CONNECTOR_BY_TYPE
from onestep_control_plane_api.pipeline_builder.schemas import GraphEdge, GraphNode, PipelineGraph


class PipelineCompileError(ValueError):
    pass


@dataclass(frozen=True)
class CompiledPipeline:
    graph: PipelineGraph
    order: list[str]
    generated_handlers: dict[str, str]
    generated_predicates: dict[str, str]
    required_credentials: list[str]


class PipelineCompiler:
    def compile(self, graph: PipelineGraph, credentials: dict[str, dict[str, Any]]) -> CompiledPipeline:
        self.validate_graph(graph, credentials)
        return CompiledPipeline(
            graph=graph,
            order=self.topological_order(graph),
            generated_handlers={
                node.id: self.generate_handler_code(node)
                for node in graph.nodes
                if (node.kind or self.infer_kind(node)) == "handler"
            },
            generated_predicates={
                predicate_name(edge): self.generate_predicate_code(edge)
                for edge in graph.edges
                if _edge_condition(edge)
            },
            required_credentials=sorted(
                {
                    node.credential_ref
                    for node in graph.nodes
                    if node.credential_ref is not None
                }
            ),
        )

    def validate_graph(self, graph: PipelineGraph, credentials: dict[str, dict[str, Any]]) -> None:
        if not graph.nodes:
            raise PipelineCompileError("pipeline graph must contain at least one node")

        nodes = {node.id: node for node in graph.nodes}
        incoming: dict[str, list[str]] = {node.id: [] for node in graph.nodes}
        outgoing: dict[str, list[str]] = {node.id: [] for node in graph.nodes}

        for edge in graph.edges:
            if edge.from_ not in nodes:
                raise PipelineCompileError(f"edge references missing source node {edge.from_}")
            if edge.to not in nodes:
                raise PipelineCompileError(f"edge references missing target node {edge.to}")
            if _edge_condition(edge):
                source_kind = nodes[edge.from_].kind or self.infer_kind(nodes[edge.from_])
                if source_kind != "handler":
                    raise PipelineCompileError("conditional edges must start from a handler node")
                self._condition_to_python_expression(edge.condition or "")
            outgoing[edge.from_].append(edge.to)
            incoming[edge.to].append(edge.from_)

        for node in graph.nodes:
            kind = node.kind or self.infer_kind(node)
            if node.type not in CONNECTOR_BY_TYPE:
                raise PipelineCompileError(f"unknown node type {node.type}")
            if kind == "source" and incoming[node.id]:
                raise PipelineCompileError(f"source node {node.id} cannot have incoming edges")
            if kind == "sink" and outgoing[node.id]:
                raise PipelineCompileError(f"sink node {node.id} cannot have outgoing edges")
            if kind != "source" and not incoming[node.id]:
                raise PipelineCompileError(f"{kind} node {node.id} requires at least one incoming edge")
            if kind != "sink" and not outgoing[node.id]:
                raise PipelineCompileError(f"{kind} node {node.id} requires at least one outgoing edge")
            if node.credential_ref and node.credential_ref not in credentials:
                raise PipelineCompileError(f"credential {node.credential_ref} is not defined")
            if kind == "handler":
                self.validate_handler(node)

        self._validate_connected(graph, incoming, outgoing)
        self.topological_order(graph)

    def topological_order(self, graph: PipelineGraph) -> list[str]:
        outgoing: dict[str, list[str]] = defaultdict(list)
        indegree = {node.id: 0 for node in graph.nodes}
        for edge in graph.edges:
            outgoing[edge.from_].append(edge.to)
            indegree[edge.to] += 1

        queue = deque([node_id for node_id, degree in indegree.items() if degree == 0])
        order: list[str] = []
        while queue:
            node_id = queue.popleft()
            order.append(node_id)
            for target in outgoing[node_id]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    queue.append(target)

        if len(order) != len(indegree):
            raise PipelineCompileError("pipeline graph must be acyclic")
        return order

    def validate_handler(self, node: GraphNode) -> None:
        if node.mode == "code":
            if not node.code or not node.code.strip():
                raise PipelineCompileError(f"handler node {node.id} is missing code")
            ast.parse(node.code)
            return
        if not node.mapping:
            raise PipelineCompileError(f"handler node {node.id} visual mapping is empty")
        for target, expression in node.mapping.items():
            if not target.strip():
                raise PipelineCompileError(f"handler node {node.id} has an empty mapping target")
            self._validate_mapping_expression(expression)

    def generate_handler_code(self, node: GraphNode) -> str:
        if node.mode == "code" and node.code:
            return node.code
        lines = ["async def handler(ctx, payload):", "    return {"]
        for target, expression in sorted(node.mapping.items()):
            python_expression = self._mapping_to_python_expression(expression)
            lines.append(f"        {target!r}: {python_expression},")
        lines.extend(["    }", ""])
        return "\n".join(lines)

    def generate_predicate_code(self, edge: GraphEdge) -> str:
        expression = self._condition_to_python_expression(edge.condition or "")
        return "\n".join(
            [
                f"def {predicate_name(edge)}(ctx, payload, result):",
                f"    return bool({expression})",
                "",
            ]
        )

    @staticmethod
    def infer_kind(node: GraphNode) -> str:
        if node.type == "handler":
            return "handler"
        if node.type.endswith("_sink"):
            return "sink"
        return "source"

    @staticmethod
    def _validate_connected(
        graph: PipelineGraph,
        incoming: dict[str, list[str]],
        outgoing: dict[str, list[str]],
    ) -> None:
        if not graph.nodes:
            return
        undirected: dict[str, set[str]] = {node.id: set() for node in graph.nodes}
        for node_id, sources in incoming.items():
            for source in sources:
                undirected[node_id].add(source)
                undirected[source].add(node_id)
        for node_id, targets in outgoing.items():
            for target in targets:
                undirected[node_id].add(target)
                undirected[target].add(node_id)

        seen: set[str] = set()
        stack = [graph.nodes[0].id]
        while stack:
            node_id = stack.pop()
            if node_id in seen:
                continue
            seen.add(node_id)
            stack.extend(undirected[node_id] - seen)
        if len(seen) != len(graph.nodes):
            raise PipelineCompileError("pipeline graph must be connected")

    def _validate_mapping_expression(self, value: str) -> None:
        self._mapping_to_python_expression(value)

    def _condition_to_python_expression(self, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise PipelineCompileError("conditional edge requires a condition expression")
        match = re.fullmatch(r"\{\{\s*(.+?)\s*\}\}", stripped)
        expression = match.group(1) if match else stripped
        try:
            ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            raise PipelineCompileError(f"conditional edge has invalid expression: {exc.msg}") from exc
        return _rewrite_result_names(expression)

    @staticmethod
    def _mapping_to_python_expression(value: str) -> str:
        stripped = value.strip()
        match = re.fullmatch(r"\{\{\s*(.+?)\s*\}\}", stripped)
        if not match:
            return repr(value)
        expression = match.group(1)
        ast.parse(expression, mode="eval")
        return _rewrite_payload_names(expression)


def _rewrite_payload_names(expression: str) -> str:
    return _rewrite_names(expression, "payload")


def _rewrite_result_names(expression: str) -> str:
    return _rewrite_names(expression, "result")


def _rewrite_names(expression: str, root_name: str) -> str:
    parsed = ast.parse(expression, mode="eval")

    class RewriteNames(ast.NodeTransformer):
        def visit_Name(self, node: ast.Name) -> ast.AST:
            if node.id in {"True", "False", "None", "ctx", "payload", "result"}:
                return node
            return ast.copy_location(
                ast.Subscript(
                    value=ast.Name(id=root_name, ctx=ast.Load()),
                    slice=ast.Constant(value=node.id),
                    ctx=node.ctx,
                ),
                node,
            )

    rewritten = RewriteNames().visit(parsed)
    ast.fix_missing_locations(rewritten)
    return ast.unparse(rewritten.body)


def predicate_name(edge: GraphEdge) -> str:
    return f"predicate_{_safe_key(edge.from_)}__{_safe_key(edge.to)}"


def _edge_condition(edge: GraphEdge) -> str | None:
    condition = edge.condition.strip() if edge.condition else ""
    return condition or None


def _safe_key(value: str) -> str:
    key = re.sub(r"[^0-9A-Za-z_]+", "_", value.strip()).strip("_")
    return key or "resource"
