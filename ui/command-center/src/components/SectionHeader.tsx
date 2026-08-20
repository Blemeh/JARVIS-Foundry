import type { ReactNode } from "react";

export function SectionHeader({
  kicker,
  title,
  description,
  actions,
}: {
  kicker?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="page-head">
      <div>
        {kicker ? <p className="t-label text-muted page-kicker">{kicker}</p> : null}
        <h1 className="t-display">{title}</h1>
        {description ? <p className="prose page-desc">{description}</p> : null}
      </div>
      {actions ? <div className="page-head-actions">{actions}</div> : null}
    </div>
  );
}
