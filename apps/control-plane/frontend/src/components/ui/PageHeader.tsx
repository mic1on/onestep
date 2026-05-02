import type { ReactNode } from "react";

type PageHeaderProps = {
  title: string;
  titleMeta?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
};

export function PageHeader({ title, titleMeta, subtitle, actions }: PageHeaderProps) {
  return (
    <div className="page-header">
      <div>
        <div className="page-title-row">
          <h2>{title}</h2>
          {titleMeta ? <div className="page-title-meta">{titleMeta}</div> : null}
        </div>
        {subtitle ? <div className="page-subtitle">{subtitle}</div> : null}
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </div>
  );
}
