import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useWorkbenchStore } from "../../app/workbench-store";
import { listServices } from "../../api/services";
import { Button } from "../../components/ui/Button";
import { DataTable } from "../../components/ui/DataTable";
import { EmptyState } from "../../components/ui/EmptyState";
import { StatusDot } from "../../components/ui/StatusDot";
import { healthTone } from "../../lib/status";

export function ServicesPage() {
  const selected = useWorkbenchStore((state) => state.selectedServiceName);
  const setSelected = useWorkbenchStore((state) => state.setSelectedServiceName);
  const services = useQuery({ queryKey: ["services"], queryFn: () => listServices() });

  return (
    <div className="page">
      <header className="page-header">
        <h1 className="page-title">Services</h1>
        <Button icon={<RefreshCw size={14} />} onClick={() => void services.refetch()}>
          Refresh
        </Button>
      </header>

      <section className="panel" style={{ overflow: "hidden" }}>
        {services.data && services.data.length > 0 ? (
          <DataTable>
            <thead>
              <tr>
                <th>Service</th>
                <th>Health</th>
                <th>Instances</th>
                <th>Tasks</th>
                <th>Last Seen</th>
              </tr>
            </thead>
            <tbody>
              {services.data.map((service) => (
                <tr
                  key={`${service.environment}:${service.name}`}
                  onClick={() => setSelected(service.name)}
                  style={{ background: selected === service.name ? "var(--accent-soft)" : undefined }}
                >
                  <td>{service.name}</td>
                  <td>
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                      <StatusDot tone={healthTone(service.health)} />
                      {service.health}
                    </span>
                  </td>
                  <td>{service.instance_count ?? "-"}</td>
                  <td>{service.task_count ?? "-"}</td>
                  <td>{service.last_seen_at ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </DataTable>
        ) : (
          <div style={{ padding: 14 }}>
            <EmptyState title="No services reported" detail="Start a OneStep runtime with control-plane reporting enabled." />
          </div>
        )}
      </section>
    </div>
  );
}
