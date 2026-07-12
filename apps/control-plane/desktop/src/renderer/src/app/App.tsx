import { QueryClientProvider } from "@tanstack/react-query";
import { queryClient } from "./query-client";

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <div className="app-shell">
        <div className="titlebar">
          <div />
          <strong>OneStep</strong>
          <div />
        </div>
        <div className="workbench">
          <nav className="left-rail" />
          <aside className="sidebar" />
          <main className="workspace">
            <section className="workspace-main">
              <div className="page">
                <header className="page-header">
                  <h1 className="page-title">Command Center</h1>
                </header>
                <div className="panel" style={{ padding: 14 }}>
                  Desktop renderer ready.
                </div>
              </div>
            </section>
          </main>
        </div>
      </div>
    </QueryClientProvider>
  );
}
