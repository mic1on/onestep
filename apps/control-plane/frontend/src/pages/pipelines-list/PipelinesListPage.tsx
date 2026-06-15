import { Link, useNavigate } from "react-router-dom";

import { useCreatePipelineMutation, useDeletePipelineMutation, usePipelinesQuery } from "../../features/pipelines/queries";
import { EmptyState } from "../../components/ui/EmptyState";
import { SignalConsoleHeader } from "../../components/ui/SignalConsoleHeader";

export function PipelinesListPage() {
  const pipelinesQuery = usePipelinesQuery();
  const createMutation = useCreatePipelineMutation();
  const deleteMutation = useDeletePipelineMutation();
  const navigate = useNavigate();
  const pipelines = pipelinesQuery.data?.items ?? [];

  async function handleCreate() {
    const pipeline = await createMutation.mutateAsync({
      name: "Untitled pipeline",
      description: "",
      graph: { nodes: [], edges: [] },
    });
    navigate(`/pipelines/${pipeline.id}`);
  }

  return (
    <div className="ref-console-page pipeline-builder-list-page">
      <SignalConsoleHeader
        kicker="Builder"
        title="Pipelines"
        description={
          <p className="signal-console-hero-note">
            Create and export OneStep worker definitions.
          </p>
        }
        side={
          <button type="button" onClick={() => void handleCreate()}>
            Create pipeline
          </button>
        }
      />
      {pipelinesQuery.error ? (
        <EmptyState title="Unable to load pipelines" body={String(pipelinesQuery.error)} />
      ) : null}
      {pipelinesQuery.isPending ? <div className="loading-block">Loading pipelines...</div> : null}
      {!pipelinesQuery.isPending && !pipelinesQuery.error && pipelines.length === 0 ? (
        <EmptyState
          title="No pipelines yet"
          body="Create a pipeline to start building a worker definition."
        />
      ) : null}
      {pipelines.length > 0 ? (
        <section className="ref-table-card">
          <div className="ref-table-body">
            {pipelines.map((pipeline) => (
              <article className="ref-table-row" key={pipeline.id}>
                <Link to={`/pipelines/${pipeline.id}`}>{pipeline.name}</Link>
                <span>{pipeline.status}</span>
                <button
                  type="button"
                  onClick={() => void deleteMutation.mutateAsync(pipeline.id)}
                >
                  Delete
                </button>
              </article>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}
