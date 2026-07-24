import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { listWorkers } from "../../api/workers";
import { Button } from "../../components/ui/Button";
import { DataTable } from "../../components/ui/DataTable";
import { EmptyState } from "../../components/ui/EmptyState";

export function WorkersPage() {
  const workers = useQuery({ queryKey: ["workers"], queryFn: listWorkers });

  return (
    <div className="page">
      <header className="page-header">
        <h1 className="page-title">Workers</h1>
        <Button variant="primary" icon={<Plus size={14} />}>
          New Worker
        </Button>
      </header>
      <section className="panel" style={{ overflow: "hidden" }}>
        {workers.data && workers.data.length > 0 ? (
          <DataTable>
            <thead>
              <tr>
                <th>Name</th>
                <th>Status</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {workers.data.map((worker) => (
                <tr key={worker.id}>
                  <td>{worker.name}</td>
                  <td>{worker.status ?? "-"}</td>
                  <td>{worker.updated_at ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </DataTable>
        ) : (
          <div style={{ padding: 14 }}>
            <EmptyState title="No workers" detail="Create a worker package to deploy it to an agent." />
          </div>
        )}
      </section>
    </div>
  );
}
