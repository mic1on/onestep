export function SettingsPage() {
  return (
    <div className="page">
      <header className="page-header"><h1 className="page-title">Settings</h1></header>
      <div className="panel" style={{ padding: 14 }}>
        <strong>Desktop Data</strong>
        <div style={{ marginTop: 6, color: "var(--text-muted)" }}>
          OneStep stores local runtime data in the macOS application support directory.
        </div>
      </div>
    </div>
  );
}
