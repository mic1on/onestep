import { forwardRef, type ReactNode } from "react";

type WorkerConfigSectionProps = {
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
};

export const WorkerConfigSection = forwardRef<HTMLDivElement, WorkerConfigSectionProps>(
  function WorkerConfigSection({ actions, children, className }, ref) {
    return (
      <div className={["worker-config-section", className].filter(Boolean).join(" ")} ref={ref}>
        {actions ? <div className="worker-config-section-actions">{actions}</div> : null}
        {children}
      </div>
    );
  },
);
