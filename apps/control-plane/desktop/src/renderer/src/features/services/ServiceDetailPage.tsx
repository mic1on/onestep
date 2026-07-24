import { useQuery } from "@tanstack/react-query";
import { useWorkbenchStore } from "../../app/workbench-store";
import { listServiceInstances, listServiceTasks } from "../../api/services";
import { StatusDot } from "../../components/ui/StatusDot";
import { healthTone } from "../../lib/status";

export function ServiceDetailPage() {
  const serviceName = useWorkbenchStore((state) => state.selectedServiceName);
  const tasks = useQuery({
    queryKey: ["service-tasks", serviceName],
    queryFn: () => listServiceTasks(serviceName!),
    enabled: Boolean(serviceName),
  });
  const instances = useQuery({
    queryKey: ["service-instances", serviceName],
    queryFn: () => listServiceInstances(serviceName!),
    enabled: Boolean(serviceName),
  });

  if (!serviceName) {
    return <div style={{ color: "var(--text-muted)" }}>Select a service to inspect it.</div>;
  }

  return (
    <div style={{ display: "grid", gap: 14 }}>
      <div>
        <div style={{ color: "var(--text-muted)", marginBottom: 4 }}>Service</div>
        <strong>{serviceName}</strong>
      </div>
      <div>
        <strong>Tasks</strong>
        <div style={{ display: "grid", gap: 8, marginTop: 8 }}>
          {(tasks.data ?? []).map((task) => (
            <div key={task.name} style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
              <span>{task.name}</span>
              <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                <StatusDot tone={healthTone(task.status)} />
                {task.status ?? "unknown"}
              </span>
            </div>
          ))}
          {tasks.data?.length === 0 ? <span style={{ color: "var(--text-muted)" }}>No tasks reported.</span> : null}
        </div>
      </div>
      <div>
        <strong>Instances</strong>
        <div style={{ display: "grid", gap: 8, marginTop: 8 }}>
          {(instances.data ?? []).map((instance) => (
            <div key={instance.instance_id} style={{ display: "grid", gap: 2 }}>
              <span>{instance.instance_id}</span>
              <span style={{ color: "var(--text-muted)" }}>{instance.last_seen_at ?? "-"}</span>
            </div>
          ))}
          {instances.data?.length === 0 ? <span style={{ color: "var(--text-muted)" }}>No instances reported.</span> : null}
        </div>
      </div>
    </div>
  );
}
