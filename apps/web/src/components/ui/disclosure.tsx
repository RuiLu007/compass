import type { ReactNode } from "react";
import { ChevronDown } from "lucide-react";

export function UiDisclosure({
  label,
  children,
  defaultOpen,
  className,
}: {
  label: string;
  children: ReactNode;
  defaultOpen?: boolean;
  className?: string;
}) {
  return (
    <details
      className={className ? `ui-disclosure ${className}` : "ui-disclosure"}
      open={defaultOpen}
    >
      <summary>
        <span>{label}</span>
        <ChevronDown size={16} aria-hidden="true" />
      </summary>
      <div className="ui-disclosure-body">{children}</div>
    </details>
  );
}
