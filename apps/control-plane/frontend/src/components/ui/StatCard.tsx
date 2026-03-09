type StatCardProps = {
  label: string;
  value: string;
  hint?: string;
  tone?: "default" | "success" | "warning" | "danger";
};

export function StatCard({ label, value, hint, tone = "default" }: StatCardProps) {
  return (
    <article className={`stat-card stat-card-${tone}`}>
      <p>{label}</p>
      <strong>{value}</strong>
      {hint ? <span>{hint}</span> : null}
    </article>
  );
}
