import { AlertTriangle, Inbox } from "lucide-react";
import { useLocale } from "../i18n/LocaleContext";

export function LoadingState({ label }: { label?: string }) {
  const { t } = useLocale();
  return (
    <div className="state-block" role="status" aria-live="polite" aria-label={label ?? t("state.loading")}>
      <div className="skeleton" style={{ width: "42%", height: 22 }} />
      <div className="skeleton" style={{ width: "100%", height: 64 }} />
      <div className="skeleton" style={{ width: "100%", height: 64 }} />
      <span className="t-label text-muted" style={{ position: "absolute", width: 1, height: 1, overflow: "hidden", clip: "rect(0 0 0 0)" }}>{label ?? t("state.loading")}</span>
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
