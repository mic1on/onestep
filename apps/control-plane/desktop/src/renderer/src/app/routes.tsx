import { useWorkbenchStore } from "./workbench-store";
import { AgentsPage } from "../features/agents/AgentsPage";
import { CommandCenterPage } from "../features/command-center/CommandCenterPage";
import { ConnectorsPage } from "../features/connectors/ConnectorsPage";
import { NotificationsPage } from "../features/notifications/NotificationsPage";
import { ServicesPage } from "../features/services/ServicesPage";
import { SettingsPage } from "../features/settings/SettingsPage";
import { WorkersPage } from "../features/workers/WorkersPage";

export function ActiveRoute() {
  const section = useWorkbenchStore((state) => state.section);
  if (section === "services") return <ServicesPage />;
  if (section === "workers") return <WorkersPage />;
  if (section === "agents") return <AgentsPage />;
  if (section === "connectors") return <ConnectorsPage />;
  if (section === "notifications") return <NotificationsPage />;
  if (section === "settings") return <SettingsPage />;
  return <CommandCenterPage />;
}
