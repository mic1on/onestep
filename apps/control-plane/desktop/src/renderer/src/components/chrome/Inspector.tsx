import { useWorkbenchStore } from "../../app/workbench-store";
import { ServiceDetailPage } from "../../features/services/ServiceDetailPage";

export function Inspector() {
  const section = useWorkbenchStore((state) => state.section);

  return (
    <aside className="inspector">
      <div style={{ padding: 14, borderBottom: "1px solid var(--border)" }}>
        <strong>Inspector</strong>
      </div>
      <div style={{ padding: 14 }}>
        {section === "services" ? (
          <ServiceDetailPage />
        ) : (
          <div style={{ color: "var(--text-muted)" }}>
            Select a service, task, command, or agent to inspect details.
          </div>
        )}
      </div>
    </aside>
  );
}
