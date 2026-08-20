import type { ReactNode } from "react";

interface TooltipProps {
  label: string;
  children: ReactNode;
  side?: "top" | "bottom";
  id?: string;
}

export function Tooltip({ label, children, side = "top", id }: TooltipProps) {
  const bubbleId = id ?? `tt-${label.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}`;
  return (
    <span className={`tooltip-wrap tooltip-${side}`}>
      {children}
      <span className="tooltip-bubble" id={bubbleId} role="tooltip">
        {label}
      </span>
    </span>
  );
}
