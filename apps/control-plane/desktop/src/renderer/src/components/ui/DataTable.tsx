import type { ReactNode } from "react";

export function DataTable({ children }: { children: ReactNode }) {
  return <table className="table">{children}</table>;
}
