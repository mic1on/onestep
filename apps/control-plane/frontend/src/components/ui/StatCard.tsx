type StatCardProps = {
  label: string;
  value: string;
  hint?: string;
  tone?: "default" | "success" | "warning" | "danger";
  size?: "default" | "compact";
};

export function StatCard({ label, value, hint, tone = "default", size = "default" }: StatCardProps) {
  return (
    <article className={`stat-card stat-card-${tone} stat-card-${size}`}>
      <p>{label}</p>
      <strong>{value}</strong>
      {hint ? <span>{hint}</span> : null}
    </article>
  );
}
