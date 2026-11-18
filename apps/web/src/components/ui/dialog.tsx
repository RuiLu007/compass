"use client";
import { Dialog } from "radix-ui";
import { X } from "lucide-react";
import { useState, type ReactNode, type RefObject } from "react";
export function Modal({
  title,
  description,
  open,
  onClose,
  children,
  wide = false,
  initialFocus,
  returnFocus,
}: {
  title: string;
  description?: string;
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
  initialFocus?: RefObject<HTMLElement | null>;
  returnFocus?: () => HTMLElement | null;
}) {
  const [opener] = useState(() =>
    typeof document === "undefined" ? null : (document.activeElement as HTMLElement | null),
  );
  return (
    <Dialog.Root
      open={open}
      onOpenChange={(value) => {
        if (!value) onClose();
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" data-sensitive-portal />
        <Dialog.Content
          onOpenAutoFocus={(event) => {
            if (initialFocus?.current) {
              event.preventDefault();
              initialFocus.current.focus();
            }
          }}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            const target = returnFocus?.() ?? opener;
            if (target?.isConnected && !target.closest("[inert]")) target.focus();
          }}
          className={`dialog ${wide ? "dialog-wide" : ""}`}
          data-sensitive-portal
          aria-describedby={description ? undefined : undefined}
        >
          <div className="dialog-head">
            <div>
              <Dialog.Title>{title}</Dialog.Title>
              {description && <Dialog.Description>{description}</Dialog.Description>}
            </div>
            <Dialog.Close className="icon-button" aria-label="关闭">
              <X size={18} />
            </Dialog.Close>
          </div>
          <div className="dialog-body">{children}</div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
