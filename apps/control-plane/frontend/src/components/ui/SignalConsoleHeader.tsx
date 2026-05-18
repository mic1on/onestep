import type { ReactNode } from "react";

type SignalConsoleHeaderProps = {
  kicker: string;
  title: string;
  description: ReactNode;
  secondary?: ReactNode;
  side: ReactNode;
  className?: string;
};

export function SignalConsoleHeader({
  kicker,
  title,
  description,
  secondary,
  side,
  className,
}: SignalConsoleHeaderProps) {
  return (
    <section className={className ? `signal-console-hero signal-console-page-header ${className}` : "signal-console-hero signal-console-page-header"}>
      <div className="signal-console-page-header-main">
        <div className="signal-console-hero-copy signal-console-page-header-copy">
          <span className="signal-console-kicker">{kicker}</span>
          <h2>{title}</h2>
          <div className="signal-console-page-header-description">{description}</div>
          {secondary ? <div className="signal-console-page-header-secondary">{secondary}</div> : null}
        </div>

        <div className="signal-console-page-header-side">{side}</div>
      </div>
    </section>
  );
}
