import "@xyflow/react/dist/style.css";

import {
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
  type NodeProps,
  type ReactFlowInstance,
} from "@xyflow/react";
import { useCallback, useMemo, useState, type DragEvent } from "react";

import type {
  PipelineConnectorDescriptor,
  PipelineCredential,
  PipelineGraph,
  PipelineGraphEdge,
  PipelineGraphNode,
} from "../../../lib/api/types";
import { NODE_PALETTE_CONNECTOR_MIME, NodePalette } from "./NodePalette";
import { PropertyPanel } from "./PropertyPanel";

type PipelineNodeData = {
  kind: PipelineGraphNode["kind"];
  label: string;
};

type PipelineEditorProps = {
  connectors: PipelineConnectorDescriptor[];
  credentials: PipelineCredential[];
  graph: PipelineGraph;
  onGraphChange: (graph: PipelineGraph) => void;
  onSelectedNodeChange: (nodeId: string | null) => void;
  selectedNodeId: string | null;
};

const nodeTypes = {
  pipelineNode: PipelineFlowNode,
};

export function PipelineEditor({
  connectors,
  credentials,
  graph,
  onGraphChange,
  onSelectedNodeChange,
  selectedNodeId,
}: PipelineEditorProps) {
  const [flowInstance, setFlowInstance] = useState<ReactFlowInstance | null>(null);
  const selectedNode = graph.nodes.find((node) => node.id === selectedNodeId) ?? null;
  const selectedConnector = selectedNode
    ? connectors.find((connector) => connector.type === selectedNode.type) ?? null
    : null;

  const nodes = useMemo(
    () =>
      graph.nodes.map((node) => ({
        id: node.id,
        type: "pipelineNode",
        position: node.position,
        selected: node.id === selectedNodeId,
        data: {
          kind: node.kind,
          label: connectors.find((connector) => connector.type === node.type)?.label ?? node.type,
        },
      })),
    [connectors, graph.nodes, selectedNodeId],
  ) satisfies Node<PipelineNodeData>[];

  const edges = useMemo(
    () =>
      graph.edges.map((edge) => ({
        id: edgeId(edge),
        source: edge.from,
        target: edge.to,
        animated: Boolean(edge.condition),
        markerEnd: { type: MarkerType.ArrowClosed },
      })),
    [graph.edges],
  ) satisfies Edge[];

  const updateFromFlow = useCallback(
    (nextNodes: Node[], nextEdges: Edge[]) => {
      onGraphChange({
        nodes: nextNodes.map((node) => {
          const previous = graph.nodes.find((item) => item.id === node.id);
          return {
            ...(previous ?? createGraphNode(node.id, connectors[0], node.position)),
            position: node.position,
          };
        }),
        edges: nextEdges.map((edge) => {
          const previous = graph.edges.find(
            (item) => item.from === edge.source && item.to === edge.target,
          );
          return {
            ...(previous ?? {}),
            from: edge.source,
            to: edge.target,
          };
        }),
      });
    },
    [connectors, graph.edges, graph.nodes, onGraphChange],
  );

  const handleNodesChange = useCallback(
    (changes: NodeChange[]) => {
      updateFromFlow(applyNodeChanges(changes, nodes), edges);
    },
    [edges, nodes, updateFromFlow],
  );

  const handleEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      updateFromFlow(nodes, applyEdgeChanges(changes, edges));
    },
    [edges, nodes, updateFromFlow],
  );

  const handleConnect = useCallback(
    (connection: Connection) => {
      if (!connection.source || !connection.target) {
        return;
      }
      const duplicate = graph.edges.some(
        (edge) => edge.from === connection.source && edge.to === connection.target,
      );
      if (duplicate) {
        return;
      }
      updateFromFlow(
        nodes,
        addEdge(
          {
            ...connection,
            markerEnd: { type: MarkerType.ArrowClosed },
          },
          edges,
        ),
      );
    },
    [edges, graph.edges, nodes, updateFromFlow],
  );

  const addNode = useCallback(
    (connector: PipelineConnectorDescriptor, position = nextNodePosition(graph.nodes.length)) => {
      const node = createGraphNode(uniqueNodeId(graph, connector.type), connector, position);
      onGraphChange({ ...graph, nodes: [...graph.nodes, node] });
      onSelectedNodeChange(node.id);
    },
    [graph, onGraphChange, onSelectedNodeChange],
  );

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    const connectorType = event.dataTransfer.getData(NODE_PALETTE_CONNECTOR_MIME);
    const connector = connectors.find((item) => item.type === connectorType);
    if (!connector || !flowInstance) {
      return;
    }
    addNode(
      connector,
      flowInstance.screenToFlowPosition({ x: event.clientX, y: event.clientY }),
    );
  }

  function handleNodeChange(node: PipelineGraphNode) {
    onGraphChange({
      ...graph,
      nodes: graph.nodes.map((item) => (item.id === node.id ? node : item)),
    });
  }

  return (
    <div className="pipeline-editor-shell">
      <NodePalette connectors={connectors} onAddNode={addNode} />
      <div
        className="pipeline-flow-pane"
        onDragOver={(event) => event.preventDefault()}
        onDrop={handleDrop}
      >
        <ReactFlow
          edges={edges}
          fitView
          nodeTypes={nodeTypes}
          nodes={nodes}
          onConnect={handleConnect}
          onEdgesChange={handleEdgesChange}
          onInit={setFlowInstance}
          onNodeClick={(_, node) => onSelectedNodeChange(node.id)}
          onNodesChange={handleNodesChange}
          onPaneClick={() => onSelectedNodeChange(null)}
        >
          <Background />
          <Controls />
        </ReactFlow>
      </div>
      <PropertyPanel
        connector={selectedConnector}
        credentials={credentials}
        node={selectedNode}
        onChange={handleNodeChange}
      />
    </div>
  );
}

function PipelineFlowNode({ data }: NodeProps<Node<PipelineNodeData>>) {
  const canReceive = data.kind !== "source";
  const canEmit = data.kind !== "sink";
  return (
    <div className={`pipeline-flow-node is-${data.kind}`}>
      {canReceive ? <Handle type="target" position={Position.Left} /> : null}
      <span>{data.kind}</span>
      <strong>{data.label}</strong>
      {canEmit ? <Handle type="source" position={Position.Right} /> : null}
    </div>
  );
}

function createGraphNode(
  id: string,
  connector: PipelineConnectorDescriptor | undefined,
  position: { x: number; y: number },
): PipelineGraphNode {
  const kind = connector?.category ?? "handler";
  return {
    id,
    type: connector?.type ?? "handler",
    kind,
    credential_ref: null,
    config: {},
    mode: kind === "handler" ? "visual" : null,
    mapping: kind === "handler" ? { value: "{{ payload }}" } : {},
    code: null,
    input_schema: {},
    position,
  };
}

function edgeId(edge: PipelineGraphEdge) {
  return `${edge.from}->${edge.to}`;
}

function nextNodePosition(index: number) {
  return { x: 80 + index * 64, y: 80 + index * 32 };
}

function uniqueNodeId(graph: PipelineGraph, connectorType: string) {
  const base = connectorType.replace(/[^A-Za-z0-9_]+/g, "_");
  let index = graph.nodes.length + 1;
  let id = `${base}_${index}`;
  while (graph.nodes.some((node) => node.id === id)) {
    index += 1;
    id = `${base}_${index}`;
  }
  return id;
}
