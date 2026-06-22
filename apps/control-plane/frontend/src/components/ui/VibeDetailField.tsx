import type { ReactNode } from "react";

type VibeDetailFieldProps = {
  label: ReactNode;
  value: ReactNode;
};

export function VibeDetailField({ label, value }: VibeDetailFieldProps) {
  return (
    <div className="detail-dialog-field">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}
