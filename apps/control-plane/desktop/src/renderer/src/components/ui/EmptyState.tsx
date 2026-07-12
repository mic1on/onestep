export function EmptyState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="panel" style={{ padding: 18, color: "var(--text-muted)" }}>
      <strong style={{ display: "block", color: "var(--text)", marginBottom: 4 }}>{title}</strong>
      {detail}
    </div>
  );
}
