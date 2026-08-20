import type { ReactNode } from "react";

interface PanelProps {
  title?: ReactNode;
  icon?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  corners?: boolean;
  padded?: boolean;
  as?: "section" | "div";
}

export function Panel({
  title,
  icon,
  actions,
  children,
  className = "",
  corners = true,
  padded = true,
  as: Tag = "section",
}: PanelProps) {
  return (
    <Tag className={`panel ${corners ? "panel-corner" : ""} ${className}`} aria-label={typeof title === "string" ? title : undefined}>
      {title !== undefined ? (
        <div className="panel-header">
          <h2 className="t-label panel-title text-muted">
            {icon}
            {title}
          </h2>
          {actions}
        </div>
      ) : null}
      <div className={padded ? "panel-body" : undefined}>{children}</div>
    </Tag>
  );
}
