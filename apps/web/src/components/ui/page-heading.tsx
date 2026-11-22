import type { ReactNode } from "react";

export function UiPageHeading({
  title,
  titleId,
  children,
}: {
  title: string;
  titleId?: string;
  children?: ReactNode;
}) {
  return (
    <header className="ui-page-heading">
      <h2 id={titleId} title={title}>
        {title}
      </h2>
      {children}
    </header>
  );
}
