export function NotificationsPage() {
  return (
    <div className="page">
      <header className="page-header"><h1 className="page-title">Notifications</h1></header>
      <div className="panel" style={{ padding: 14 }}>
        <strong>Notification Channels</strong>
        <div style={{ marginTop: 6, color: "var(--text-muted)" }}>
          Notification channels and delivery history will be managed here.
        </div>
      </div>
    </div>
  );
}
