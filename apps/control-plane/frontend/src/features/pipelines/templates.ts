import type { PipelineGraph } from "../../lib/api/types";

export function emptyPipelineGraph(): PipelineGraph {
  return { nodes: [], edges: [] };
}
