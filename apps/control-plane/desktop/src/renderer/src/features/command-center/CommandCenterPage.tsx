import { useQuery } from "@tanstack/react-query";
import { Activity, Cpu, Layers3, RefreshCw } from "lucide-react";
import { listWorkerAgents } from "../../api/agents";
import { listRecentCommands } from "../../api/commands";
import { listServices } from "../../api/services";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { DataTable } from "../../components/ui/DataTable";
import { EmptyState } from "../../components/ui/EmptyState";
import { StatusDot } from "../../components/ui/StatusDot";
import { healthTone } from "../../lib/status";

export function CommandCenterPage() {
  const services = useQuery({ queryKey: ["services"], queryFn: () => listServices() });
  const commands = useQuery({ queryKey: ["commands"], queryFn: listRecentCommands });
  const agents = useQuery({ queryKey: ["worker-agents"], queryFn: listWorkerAgents });

  const healthyServices = services.data?.filter((service) => service.health === "healthy").length ?? 0;
  const totalServices = services.data?.length ?? 0;
  const connectedAgents = agents.data?.filter((agent) => agent.status === "online").length ?? 0;

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1 className="page-title">Command Center</h1>
          <div style={{ color: "var(--text-muted)", marginTop: 3 }}>
            Local control plane overview
          </div>
        </div>
        <Button
          icon={<RefreshCw size={14} />}
          onClick={() => void Promise.all([services.refetch(), commands.refetch(), agents.refetch()])}
        >
          Refresh
        </Button>
      </header>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 12 }}>
        <div className="panel" style={{ padding: 14 }}>
          <Badge>
            <Layers3 size={12} /> Services
          </Badge>
          <div style={{ fontSize: 28, fontWeight: 700, marginTop: 10 }}>
            {healthyServices}/{totalServices}
          </div>
          <div style={{ color: "var(--text-muted)" }}>healthy</div>
        </div>
        <div className="panel" style={{ padding: 14 }}>
          <Badge>
            <Cpu size={12} /> Agents
          </Badge>
          <div style={{ fontSize: 28, fontWeight: 700, marginTop: 10 }}>{connectedAgents}</div>
          <div style={{ color: "var(--text-muted)" }}>online</div>
        </div>
        <div className="panel" style={{ padding: 14 }}>
          <Badge>
            <Activity size={12} /> Commands
          </Badge>
          <div style={{ fontSize: 28, fontWeight: 700, marginTop: 10 }}>{commands.data?.length ?? 0}</div>
          <div style={{ color: "var(--text-muted)" }}>recent</div>
        </div>
      </div>

      <section className="panel" style={{ overflow: "hidden" }}>
        <div style={{ padding: "12px 14px", borderBottom: "1px solid var(--border)", fontWeight: 650 }}>
          Recent Commands
        </div>
        {commands.data && commands.data.length > 0 ? (
          <DataTable>
            <thead>
              <tr>
                <th>Status</th>
                <th>Type</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {commands.data.map((command) => (
                <tr key={command.id}>
                  <td>
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                      <StatusDot tone={healthTone(command.status)} />
                      {command.status}
                    </span>
                  </td>
                  <td>{command.command_type ?? "command"}</td>
                  <td>{command.updated_at ?? command.created_at ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </DataTable>
        ) : (
          <div style={{ padding: 14 }}>
            <EmptyState title="No commands yet" detail="Commands will appear here after agents receive work." />
          </div>
        )}
      </section>
    </div>
  );
}
