import { Pause } from "lucide-react"
import { useCommandCenter } from "../context/CommandCenterContext"
import { useLocale } from "../i18n/LocaleContext"

export function PauseBanner() {
  const { paused, stopActive, resetStop, resumeTargetId, sourceMode } = useCommandCenter()
  const { t } = useLocale()
  if (stopActive || !paused) return null
  const canResume = Boolean(resumeTargetId) || sourceMode === "SIMULATED"
  return (
    <div className="pause-banner-global" role="status">
      <Pause size={15} aria-hidden="true" />
      <span className="t-label">{t("pause.banner")}</span>
      <span className="t-mono pause-banner-note">{t("pause.note")}</span>
      <button
        type="button"
        className="pause-banner-resume"
        onClick={() => void resetStop()}
        disabled={!canResume}
      >
        {t("pause.resume")}
      </button>
    </div>
  )
}
