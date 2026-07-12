export function Inspector() {
  return (
    <aside className="inspector">
      <div style={{ padding: 14, borderBottom: "1px solid var(--border)" }}>
        <strong>Inspector</strong>
      </div>
      <div style={{ padding: 14, color: "var(--text-muted)" }}>
        Select a service, task, command, or agent to inspect details.
      </div>
    </aside>
  );
}
