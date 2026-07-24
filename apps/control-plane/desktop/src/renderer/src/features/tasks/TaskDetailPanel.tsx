import type { TaskSummary } from "../../api/types";
import { StatusDot } from "../../components/ui/StatusDot";
import { healthTone } from "../../lib/status";

export function TaskDetailPanel({ task }: { task: TaskSummary }) {
  return (
    <div style={{ display: "grid", gap: 8 }}>
      <strong>{task.name}</strong>
      <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
        <StatusDot tone={healthTone(task.status)} />
        {task.status ?? "unknown"}
      </span>
      <span style={{ color: "var(--text-muted)" }}>Last event: {task.last_event_at ?? "-"}</span>
    </div>
  );
}
