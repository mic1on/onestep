import { useState } from "react";
import { useParams } from "react-router-dom";

import { EmptyState } from "../../components/ui/EmptyState";
import { SignalConsoleHeader } from "../../components/ui/SignalConsoleHeader";
import { PipelineEditor } from "../../features/pipelines/components/PipelineEditor";
import {
  usePipelineConnectorsQuery,
  usePipelineCredentialsQuery,
  usePipelineQuery,
  useUpdatePipelineMutation,
  useValidatePipelineMutation,
} from "../../features/pipelines/queries";
import { exportPipeline } from "../../lib/api/client";
import type { PipelineGraph } from "../../lib/api/types";

export function PipelineEditorPage() {
  const { pipelineId } = useParams<{ pipelineId: string }>();
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const pipelineQuery = usePipelineQuery(pipelineId ?? "");
  const connectorsQuery = usePipelineConnectorsQuery();
  const credentialsQuery = usePipelineCredentialsQuery();
  const updateMutation = useUpdatePipelineMutation(pipelineId ?? "");
  const validateMutation = useValidatePipelineMutation(pipelineId ?? "");

  if (!pipelineId) {
    return <EmptyState title="Missing pipeline" body="The route needs a pipeline id." />;
  }
  if (pipelineQuery.isPending || connectorsQuery.isPending || credentialsQuery.isPending) {
    return <div className="loading-block">Loading pipeline...</div>;
  }
  if (pipelineQuery.error) {
    return <EmptyState title="Unable to load pipeline" body={String(pipelineQuery.error)} />;
  }

  const pipeline = pipelineQuery.data;
  if (!pipeline) {
    return <EmptyState title="Pipeline not found" body="The requested pipeline does not exist." />;
  }

  async function handleGraphChange(graph: PipelineGraph) {
    await updateMutation.mutateAsync({ graph });
  }

  async function handleValidate() {
    await validateMutation.mutateAsync();
  }

  async function handleExport() {
    const blob = await exportPipeline(pipeline.id);
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${pipeline.id}.zip`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="ref-console-page pipeline-builder-editor-page">
      <SignalConsoleHeader
        kicker="Pipeline Builder"
        title={pipeline.name}
        description={<p className="signal-console-hero-note">Status {pipeline.status}</p>}
        side={
          <div className="ref-page-actions">
            <button type="button" onClick={() => void handleValidate()}>
              Validate
            </button>
            <button type="button" onClick={() => void handleExport()}>
              Export
            </button>
          </div>
        }
      />
      <PipelineEditor
        connectors={connectorsQuery.data?.items ?? []}
        credentials={credentialsQuery.data?.items ?? []}
        graph={pipeline.graph}
        onGraphChange={(graph) => void handleGraphChange(graph)}
        onSelectedNodeChange={setSelectedNodeId}
        selectedNodeId={selectedNodeId}
      />
    </div>
  );
}
