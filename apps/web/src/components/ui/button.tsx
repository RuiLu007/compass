import type { ComponentPropsWithRef } from "react";
import { LoaderCircle } from "lucide-react";

type ButtonProps = ComponentPropsWithRef<"button"> & {
  variant?: "primary" | "secondary" | "quiet" | "danger";
  busy?: boolean;
};

export function UiButton({
  variant = "primary",
  type = "button",
  busy = false,
  disabled,
  className,
  children,
  ...props
}: ButtonProps) {
  return (
    <button
      {...props}
      type={type}
      className={className ? `ui-button ${className}` : "ui-button"}
      data-variant={variant}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
    >
      {busy && <LoaderCircle className="ui-spinner" size={16} aria-hidden="true" />}
      {children}
    </button>
  );
}
