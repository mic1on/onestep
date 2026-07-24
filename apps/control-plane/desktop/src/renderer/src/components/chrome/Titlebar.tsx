import { PanelRight, RefreshCw, Search } from "lucide-react";
import { useWorkbenchStore } from "../../app/workbench-store";
import { IconButton } from "../ui/IconButton";

export function Titlebar() {
  const inspectorOpen = useWorkbenchStore((state) => state.inspectorOpen);
  const setInspectorOpen = useWorkbenchStore((state) => state.setInspectorOpen);

  return (
    <header className="titlebar">
      <div />
      <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
        <strong>OneStep</strong>
        <span style={{ color: "var(--text-muted)" }}>Control Plane</span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, paddingRight: 10 }}>
        <IconButton label="Search" icon={<Search size={15} />} />
        <IconButton label="Refresh" icon={<RefreshCw size={15} />} />
        <IconButton
          label={inspectorOpen ? "Hide inspector" : "Show inspector"}
          icon={<PanelRight size={15} />}
          onClick={() => setInspectorOpen(!inspectorOpen)}
        />
      </div>
    </header>
  );
}
