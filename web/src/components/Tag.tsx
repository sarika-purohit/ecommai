import type { ReactNode } from "react";

export type TagTone = "plain" | "rule" | "off" | "pass" | "fail" | "unclear";

interface TagProps {
  tone?: TagTone;
  /** Shown in place of the eyelet, e.g. a lock for personal rules. */
  icon?: ReactNode;
  children: ReactNode;
  className?: string;
}

export function Tag({ tone = "plain", icon, children, className }: TagProps) {
  return (
    <span className={`tag ${className ?? ""}`} data-tone={tone} data-icon={icon ? "" : undefined}>
      {icon ? <span className="tag-icon">{icon}</span> : null}
      {children}
    </span>
  );
}
