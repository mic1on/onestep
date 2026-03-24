import { Outlet } from "react-router-dom";

export function AppShell() {
  return (
    <div className="app-shell">
      <main className="app-main">
        <div className="page-shell">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
