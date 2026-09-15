import { AlertTriangle, Loader2, Inbox } from "lucide-react";
import { useLocale } from "../i18n/LocaleContext";

export function LoadingState({ label }: { label?: string }) {
  const { t } = useLocale();
  return (
    <div className="state-block" role="status" aria-live="polite">
      <Loader2 size={18} className="spin" aria-hidden="true" />
      <span className="t-label text-muted">{label ?? t("state.loading")}...</span>
    </div>
  );
}

export function EmptyState({ label, detail }: { label?: string; detail?: string }) {
  const { t } = useLocale();
  return (
    <div className="state-block">
      <Inbox size={18} className="text-muted" aria-hidden="true" />
      <span className="t-label text-muted">{label ?? t("state.empty")}</span>
      {detail ? <p className="prose">{detail}</p> : null}
    </div>
  );
}

export function ErrorState({
  label,
  detail,
  onRetry,
  retryLabel,
}: {
  label?: string;
  detail?: string;
  onRetry?: () => void;
  retryLabel?: string;
}) {
  const { t } = useLocale();
  return (
    <div className="state-block state-error" role="alert">
      <AlertTriangle size={18} aria-hidden="true" />
      <span className="t-label">{label ?? t("state.error")}</span>
      {detail ? <p className="prose">{detail}</p> : null}
      {onRetry ? (
        <button type="button" className="btn" onClick={onRetry}>
          {retryLabel ?? t("state.retry")}
        </button>
      ) : null}
    </div>
  );
}
