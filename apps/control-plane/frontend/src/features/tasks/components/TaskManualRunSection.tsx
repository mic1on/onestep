import type { ReactNode } from "react";

type TaskManualRunSectionProps = {
  children: ReactNode;
  variant: "payload" | "reason" | "targets";
};

export function TaskManualRunSection({ children, variant }: TaskManualRunSectionProps) {
  return (
    <section
      className={[
        "task-manual-run-dialog-section",
        `task-manual-run-dialog-section-${variant}`,
      ].join(" ")}
    >
      {children}
    </section>
  );
}
