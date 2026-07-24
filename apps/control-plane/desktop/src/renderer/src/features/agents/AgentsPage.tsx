import { useQuery } from "@tanstack/react-query";
import { listWorkerAgents } from "../../api/agents";
import { DataTable } from "../../components/ui/DataTable";
import { EmptyState } from "../../components/ui/EmptyState";
import { StatusDot } from "../../components/ui/StatusDot";
import { healthTone } from "../../lib/status";

export function AgentsPage() {
  const agents = useQuery({ queryKey: ["worker-agents"], queryFn: listWorkerAgents });

  return (
    <div className="page">
      <header className="page-header">
        <h1 className="page-title">Agents</h1>
      </header>
      <section className="panel" style={{ overflow: "hidden" }}>
        {agents.data && agents.data.length > 0 ? (
          <DataTable>
            <thead>
              <tr>
                <th>Name</th>
                <th>Status</th>
                <th>Last Seen</th>
              </tr>
            </thead>
            <tbody>
              {agents.data.map((agent) => (
                <tr key={agent.id}>
                  <td>{agent.name}</td>
                  <td>
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                      <StatusDot tone={healthTone(agent.status)} />
                      {agent.status ?? "unknown"}
                    </span>
                  </td>
                  <td>{agent.last_seen_at ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </DataTable>
        ) : (
          <div style={{ padding: 14 }}>
            <EmptyState title="No agents connected" detail="Start a worker agent to accept desktop deployments." />
          </div>
        )}
      </section>
    </div>
  );
}
