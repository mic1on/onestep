import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { listConnectors } from "../../api/connectors";
import { Button } from "../../components/ui/Button";
import { DataTable } from "../../components/ui/DataTable";
import { EmptyState } from "../../components/ui/EmptyState";

export function ConnectorsPage() {
  const connectors = useQuery({ queryKey: ["connectors"], queryFn: listConnectors });

  return (
    <div className="page">
      <header className="page-header">
        <h1 className="page-title">Connectors</h1>
        <Button variant="primary" icon={<Plus size={14} />}>
          New Connector
        </Button>
      </header>
      <section className="panel" style={{ overflow: "hidden" }}>
        {connectors.data && connectors.data.length > 0 ? (
          <DataTable>
            <thead>
              <tr>
                <th>Name</th>
                <th>Type</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {connectors.data.map((connector) => (
                <tr key={connector.id}>
                  <td>{connector.name}</td>
                  <td>{connector.type}</td>
                  <td>{connector.updated_at ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </DataTable>
        ) : (
          <div style={{ padding: 14 }}>
            <EmptyState title="No connectors" detail="Connectors created in OneStep will appear here." />
          </div>
        )}
      </section>
    </div>
  );
}
