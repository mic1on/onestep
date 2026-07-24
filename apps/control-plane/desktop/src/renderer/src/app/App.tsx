import { QueryClientProvider } from "@tanstack/react-query";
import { queryClient } from "./query-client";
import { ActiveRoute } from "./routes";
import { useWorkbenchStore } from "./workbench-store";
import { Inspector } from "../components/chrome/Inspector";
import { LeftRail } from "../components/chrome/LeftRail";
import { Sidebar } from "../components/chrome/Sidebar";
import { Titlebar } from "../components/chrome/Titlebar";

export function App() {
  const inspectorOpen = useWorkbenchStore((state) => state.inspectorOpen);

  return (
    <QueryClientProvider client={queryClient}>
      <div className="app-shell">
        <Titlebar />
        <div className="workbench">
          <LeftRail />
          <Sidebar />
          <main className={inspectorOpen ? "workspace with-inspector" : "workspace"}>
            <section className="workspace-main">
              <ActiveRoute />
            </section>
            {inspectorOpen ? <Inspector /> : null}
          </main>
        </div>
      </div>
    </QueryClientProvider>
  );
}
