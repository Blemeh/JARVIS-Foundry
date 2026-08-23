import { AlertTriangle, Loader2, Inbox } from "lucide-react";

export function LoadingState({ label = "Loading" }: { label?: string }) {
  return (
    <div className="state-block" role="status" aria-live="polite">
      <Loader2 size={18} className="spin" aria-hidden="true" />
      <span className="t-label text-muted">{label}...</span>
    </div>
  );
}

export function EmptyState({ label = "Nothing to show", detail }: { label?: string; detail?: string }) {
  return (
    <div className="state-block">
      <Inbox size={18} className="text-muted" aria-hidden="true" />
      <span className="t-label text-muted">{label}</span>
      {detail ? <p className="prose">{detail}</p> : null}
    </div>
  );
}

export function ErrorState({
  label = "Failed to load",
  detail,
  onRetry,
  retryLabel = "Retry",
}: {
  label?: string;
  detail?: string;
  onRetry?: () => void;
  retryLabel?: string;
}) {
  return (
    <div className="state-block state-error" role="alert">
      <AlertTriangle size={18} aria-hidden="true" />
      <span className="t-label">{label}</span>
      {detail ? <p className="prose">{detail}</p> : null}
      {onRetry ? (
        <button type="button" className="btn" onClick={onRetry}>
          {retryLabel}
        </button>
      ) : null}
    </div>
  );
}
