import type { ReactNode } from "react";

interface MetricCardProps {
  label: string;
  value: ReactNode;
  delta?: ReactNode;
  tone?: "accent" | "teal" | "amber" | "error" | "default";
}

export function MetricCard({ label, value, delta, tone = "default" }: MetricCardProps) {
  return (
    <div className="metric panel panel-corner">
      <span className="t-label text-muted">{label}</span>
      <div className="metric-value-row">
        <span className={`metric-value ${tone !== "default" ? `metric-${tone}` : ""}`}>{value}</span>
        {delta ? <span className="t-mono metric-delta">{delta}</span> : null}
      </div>
    </div>
  );
}
