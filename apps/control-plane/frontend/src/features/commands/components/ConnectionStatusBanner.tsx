type ConnectionStatusBannerProps = {
  tone: "info" | "warning" | "error";
  title: string;
  body: string;
  detail?: string | null;
};

export function ConnectionStatusBanner({
  tone,
  title,
  body,
  detail,
}: ConnectionStatusBannerProps) {
  return (
    <section
      aria-live="polite"
      className={`ref-status-banner is-${tone}`}
      role={tone === "error" ? "alert" : "status"}
    >
      <div className="ref-status-banner-copy">
        <strong>{title}</strong>
        <p>{body}</p>
        {detail ? <small>{detail}</small> : null}
      </div>
    </section>
  );
}
