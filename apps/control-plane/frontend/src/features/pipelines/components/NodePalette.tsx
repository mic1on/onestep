import type { DragEvent } from "react";

import type { PipelineConnectorDescriptor } from "../../../lib/api/types";

export const NODE_PALETTE_CONNECTOR_MIME = "application/x-onestep-connector";

type NodePaletteProps = {
  connectors: PipelineConnectorDescriptor[];
  onAddNode: (connector: PipelineConnectorDescriptor) => void;
};

const CATEGORY_LABELS = {
  source: "Sources",
  handler: "Handlers",
  sink: "Sinks",
};

export function NodePalette({ connectors, onAddNode }: NodePaletteProps) {
  function startDrag(
    event: DragEvent<HTMLButtonElement>,
    connector: PipelineConnectorDescriptor,
  ) {
    event.dataTransfer.setData(NODE_PALETTE_CONNECTOR_MIME, connector.type);
    event.dataTransfer.effectAllowed = "copy";
  }

  return (
    <aside className="pipeline-node-palette" aria-label="Node library">
      {(["source", "handler", "sink"] as const).map((category) => (
        <section className="pipeline-palette-group" key={category}>
          <h3>{CATEGORY_LABELS[category]}</h3>
          <div className="pipeline-palette-stack">
            {connectors
              .filter((connector) => connector.category === category)
              .map((connector) => (
                <button
                  className="pipeline-palette-node"
                  draggable
                  key={connector.type}
                  onClick={() => onAddNode(connector)}
                  onDragStart={(event) => startDrag(event, connector)}
                  type="button"
                >
                  <strong>{connector.label}</strong>
                  <span>{connector.description}</span>
                </button>
              ))}
          </div>
        </section>
      ))}
    </aside>
  );
}
