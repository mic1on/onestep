import type { ReactNode } from "react";
import { Bell, Cable, Command, Cpu, Layers3, Settings, SquareTerminal } from "lucide-react";
import { clsx } from "clsx";
import { useWorkbenchStore, type NavSection } from "../../app/workbench-store";

const items: { id: NavSection; label: string; icon: ReactNode }[] = [
  { id: "command-center", label: "Command Center", icon: <Command size={18} /> },
  { id: "services", label: "Services", icon: <Layers3 size={18} /> },
  { id: "workers", label: "Workers", icon: <SquareTerminal size={18} /> },
  { id: "agents", label: "Agents", icon: <Cpu size={18} /> },
  { id: "connectors", label: "Connectors", icon: <Cable size={18} /> },
  { id: "notifications", label: "Notifications", icon: <Bell size={18} /> },
  { id: "settings", label: "Settings", icon: <Settings size={18} /> },
];

export function LeftRail() {
  const section = useWorkbenchStore((state) => state.section);
  const setSection = useWorkbenchStore((state) => state.setSection);

  return (
    <nav className="left-rail" aria-label="Primary navigation">
      {items.map((item) => (
        <button
          key={item.id}
          title={item.label}
          aria-label={item.label}
          className={clsx("icon-button", section === item.id && "primary")}
          onClick={() => setSection(item.id)}
        >
          {item.icon}
        </button>
      ))}
    </nav>
  );
}
