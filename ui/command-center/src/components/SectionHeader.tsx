import type { ReactNode } from "react";
import { JarvisCoreBadge } from "./jarvis-core/JarvisCoreStage";

export function SectionHeader({
  kicker,
  title,
  description,
  actions,
  cinematic = true,
  activeMissionCount,
  verifyingCount,
  trustHealth,
}: {
  kicker?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
  cinematic?: boolean;
  activeMissionCount?: number;
  verifyingCount?: number;
  trustHealth?: string | null;
}) {
  if (!cinematic) {
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

  return (
    <div className="page-head cinematic-page-head">
      <div className="cinematic-core-rail">
        <div className="cinematic-core-rail__badge" aria-hidden="true">
          <JarvisCoreBadge
            activeMissionCount={activeMissionCount}
            verifyingCount={verifyingCount}
            trustHealth={trustHealth}
          />
        </div>
        <div className="cinematic-core-rail__copy">
          {kicker ? <p className="t-label text-muted page-kicker">{kicker}</p> : null}
          <h1 className="t-display">{title}</h1>
          {description ? <p className="prose page-desc">{description}</p> : null}
        </div>
      </div>
      {actions ? <div className="page-head-actions">{actions}</div> : null}
    </div>
  );
}
