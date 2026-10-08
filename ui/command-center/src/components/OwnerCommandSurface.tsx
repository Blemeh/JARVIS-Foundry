import { useState, type FormEvent } from "react";
import { ArrowRight } from "lucide-react";
import { Link } from "react-router-dom";
import { useCommandCenter } from "../context/CommandCenterContext";
import type { OwnerGoalResult } from "../lib/contracts";
import { sourceModeLabel } from "../lib/sourceMode";
import { LocaleToggle, useLocale } from "../i18n/LocaleContext";
import { VoiceTalkButton } from "./VoiceTalkButton";

type CommandTab = "GOAL" | "ASK" | "VETO";
type CommandProgress =
  | { phase: "idle" }
  | { phase: "submitted"; objective: string; result: OwnerGoalResult; tab: CommandTab }
  | { phase: "error"; objective: string; message: string };

const commandTabs: { value: CommandTab; detail: string }[] = [
  { value: "GOAL", detail: "Tell JARVIS what outcome is wanted" },
  { value: "ASK", detail: "Advice or briefing" },
  { value: "VETO", detail: "Hold an action" },
];

const tabPlaceholders: Record<CommandTab, string> = {
  GOAL: "Tell JARVIS what outcome you want…",
  ASK: "Ask for advice or a briefing…",
  VETO: "Name the action to hold…",
};

export function OwnerCommandSurface() {
  const { client, stopActive, sourceMode, mutationsEnabled } = useCommandCenter();
  const { t, locale } = useLocale();
  const [objective, setObjective] = useState("");
  const [tab, setTab] = useState<CommandTab>("GOAL");
  const [submitting, setSubmitting] = useState(false);
  const [progress, setProgress] = useState<CommandProgress>({ phase: "idle" });

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const text = objective.trim();
    if (!text || stopActive || submitting) return;

    setSubmitting(true);
    try {
      let result: OwnerGoalResult;
      if (tab === "ASK") result = await client.submitOwnerGoal(text, "TELL_ME");
      else if (tab === "VETO") result = await client.submitVeto(text);
      else result = await client.submitOwnerGoal(text);
      setProgress({ phase: "submitted", objective: text, result, tab });
      if (result.accepted) setObjective("");
    } catch (err) {
      setProgress({
        phase: "error",
        objective: text,
        message: err instanceof Error ? err.message : "Submission failed",
      });
    } finally {
      setSubmitting(false);
    }
  };

  const disabled = stopActive || submitting || !mutationsEnabled;
  const acceptedLabel = (submittedTab: CommandTab) => {
    if (submittedTab === "VETO") return t("command.vetoAccepted");
    if (submittedTab === "ASK") return "Request recorded.";
    return t("command.accepted");
  };

  return (
    <section className="owner-command" aria-label={t("command.title")}>
      <div className="owner-command-head">
        <h2 className="owner-command-title">{t("command.title")}</h2>
        <LocaleToggle compact />
      </div>
      <div className="owner-command-tabs" role="tablist" aria-label="Command mode">
        {commandTabs.map((item) => (
          <button
            key={item.value}
            type="button"
            role="tab"
            aria-selected={tab === item.value}
            className={`owner-command-tab ${tab === item.value ? "is-active" : ""} ${item.value === "GOAL" ? "is-primary" : ""}`}
            title={`${item.value}: ${item.detail}`}
            onClick={() => setTab(item.value)}
            disabled={disabled}
          >
            <strong>{item.value}</strong>
            <small>{item.value === "GOAL" ? `Primary · ${item.detail}` : item.detail}</small>
          </button>
        ))}
      </div>
      <form className="owner-command-form" onSubmit={(e) => void submit(e)}>
        <input
          className="owner-command-input"
          value={objective}
          onChange={(e) => setObjective(e.target.value)}
          placeholder={tabPlaceholders[tab]}
          aria-label={t("command.objective")}
          lang={locale}
          disabled={disabled}
        />
        <button type="submit" className="owner-command-submit" disabled={disabled || !objective.trim()} aria-label={t("command.submit")}>
          <ArrowRight size={18} aria-hidden="true" />
        </button>
      </form>
      <VoiceTalkButton />

      {progress.phase === "submitted" ? (
        <div className="owner-command-progress" role="status">
          <div className="owner-command-line">
            <span className="owner-command-role">{t("command.you")}</span>
            <p>{progress.objective}</p>
          </div>
          <div className="owner-command-line">
            <span className="owner-command-role">{t("command.jarvis")}</span>
            <p>{progress.result.accepted ? acceptedLabel(progress.tab) : progress.result.error ?? t("command.notAccepted")}</p>
          </div>
          {progress.result.directiveId ? (
            <p className="owner-command-meta t-mono">
              Directive {progress.result.directiveId}
              {progress.result.eventId ? ` · Event ${progress.result.eventId}` : ""}
            </p>
          ) : null}
          {!progress.result.accepted && sourceMode !== "LIVE" ? (
            <p className="owner-command-meta text-muted">
              {sourceModeLabel(sourceMode)} mode — goal routing may be gated.
            </p>
          ) : null}
        </div>
      ) : null}

      {progress.phase === "error" ? (
        <div className="owner-command-progress owner-command-progress--error" role="alert">
          <div className="owner-command-line">
            <span className="owner-command-role">{t("command.you")}</span>
            <p>{progress.objective}</p>
          </div>
          <div className="owner-command-line">
            <span className="owner-command-role">{t("command.jarvis")}</span>
            <p>{progress.message}</p>
          </div>
        </div>
      ) : null}

      {stopActive ? (
        <p className="owner-command-gate">{t("command.pausedFrozen")}</p>
      ) : !mutationsEnabled ? (
        <p className="owner-command-gate">
          Mutations gated in {sourceModeLabel(sourceMode)} mode.{" "}
          <Link to="/owner">Open owner controls</Link>
        </p>
      ) : null}
    </section>
  );
}
