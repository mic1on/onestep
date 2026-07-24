import type { HTMLAttributes } from 'react';
import logoSvg from '../assets/logo-3.svg?raw';

interface BrandLogoProps extends Omit<HTMLAttributes<HTMLSpanElement>, 'children'> {
  decorative?: boolean;
  label?: string;
}

export default function BrandLogo({
  className = '',
  decorative = false,
  label = 'OneStep',
  ...props
}: BrandLogoProps) {
  return (
    <span
      {...props}
      aria-hidden={decorative ? true : undefined}
      aria-label={decorative ? undefined : label}
      className={`brand-logo inline-block ${className}`.trim()}
      role={decorative ? undefined : 'img'}
      dangerouslySetInnerHTML={{ __html: logoSvg }}
    />
  );
}
