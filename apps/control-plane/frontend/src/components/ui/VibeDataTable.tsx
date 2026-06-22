import type { ReactNode } from "react";

export type VibeDataTableColumn = {
  key: string;
  label: ReactNode;
};

type VibeDataTableProps = {
  bodyClassName?: string;
  children: ReactNode;
  className?: string;
  columns: VibeDataTableColumn[];
  gridClassName?: string;
  headClassName?: string;
};

type VibeDataRowProps = {
  children: ReactNode;
  className?: string;
  gridClassName?: string;
};

export function VibeDataTable({
  bodyClassName,
  children,
  className,
  columns,
  gridClassName,
  headClassName,
}: VibeDataTableProps) {
  return (
    <section className={["ref-table-card", className].filter(Boolean).join(" ")}>
      <div className={["ref-table-head", headClassName, gridClassName].filter(Boolean).join(" ")}>
        {columns.map((column) => (
          <span key={column.key}>{column.label}</span>
        ))}
      </div>
      <div className={["ref-table-body", bodyClassName].filter(Boolean).join(" ")}>
        {children}
      </div>
    </section>
  );
}

export function VibeDataRow({ children, className, gridClassName }: VibeDataRowProps) {
  return (
    <article className={["ref-table-row", className, gridClassName].filter(Boolean).join(" ")}>
      {children}
    </article>
  );
}
