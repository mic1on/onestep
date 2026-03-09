import type { ReactNode } from "react";

type CodeBlockProps = {
  children: ReactNode;
};

export function CodeBlock({ children }: CodeBlockProps) {
  return <pre className="json-block">{children}</pre>;
}
