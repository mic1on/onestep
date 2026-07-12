import { useWorkbenchStore } from "../../app/workbench-store";

const titles = {
  "command-center": "Command Center",
  services: "Services",
  workers: "Workers",
  agents: "Agents",
  connectors: "Connectors",
  notifications: "Notifications",
  settings: "Settings",
};

export function Sidebar() {
  const section = useWorkbenchStore((state) => state.section);

  return (
    <aside className="sidebar">
      <div style={{ padding: "14px 14px 10px", borderBottom: "1px solid var(--border)" }}>
        <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 4 }}>OneStep</div>
        <h2 style={{ margin: 0, fontSize: 16 }}>{titles[section]}</h2>
      </div>
      <div style={{ padding: 12, color: "var(--text-muted)" }}>
        Context navigation will populate from the active section.
      </div>
    </aside>
  );
}
