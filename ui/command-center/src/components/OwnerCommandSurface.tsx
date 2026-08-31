import { useState, type FormEvent } from "react";
import { ArrowRight } from "lucide-react";
import { Link } from "react-router-dom";
import { useCommandCenter } from "../context/CommandCenterContext";
import type { OwnerGoalResult } from "../lib/contracts";
import { sourceModeLabel } from "../lib/sourceMode";

type CommandProgress =
  | { phase: "idle" }
  | { phase: "submitted"; objective: string; result: OwnerGoalResult }
  | { phase: "error"; objective: string; message: string };

export function OwnerCommandSurface() {
  const { client, stopActive, sourceMode, mutationsEnabled } = useCommandCenter();
  const [objective, setObjective] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [progress, setProgress] = useState<CommandProgress>({ phase: "idle" });

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const text = objective.trim();
    if (!text || stopActive || submitting) return;

    setSubmitting(true);
    try {
      const result = await client.submitOwnerGoal(text);
      setProgress({ phase: "submitted", objective: text, result });
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

  return (
    <section className="owner-command" aria-label="Command JARVIS">
      <h2 className="owner-command-title">Command JARVIS</h2>
      <form className="owner-command-form" onSubmit={(e) => void submit(e)}>
        <input
          className="owner-command-input"
          value={objective}
          onChange={(e) => setObjective(e.target.value)}
          placeholder="Tell JARVIS what you want..."
          aria-label="Owner objective for JARVIS"
          disabled={disabled}
        />
        <button type="submit" className="owner-command-submit" disabled={disabled || !objective.trim()} aria-label="Submit objective">
          <ArrowRight size={18} aria-hidden="true" />
        </button>
      </form>

      {progress.phase === "submitted" ? (
        <div className="owner-command-progress" role="status">
          <div className="owner-command-line">
            <span className="owner-command-role">You</span>
            <p>{progress.objective}</p>
          </div>
          <div className="owner-command-line">
            <span className="owner-command-role">JARVIS</span>
            <p>{progress.result.accepted ? "Goal accepted" : progress.result.error ?? "Goal not accepted"}</p>
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
            <span className="owner-command-role">You</span>
            <p>{progress.objective}</p>
          </div>
          <div className="owner-command-line">
            <span className="owner-command-role">JARVIS</span>
            <p>{progress.message}</p>
          </div>
        </div>
      ) : null}

      {stopActive ? (
        <p className="owner-command-gate">Commands frozen while STOP is active.</p>
      ) : !mutationsEnabled ? (
        <p className="owner-command-gate">
          Mutations gated in {sourceModeLabel(sourceMode)} mode.{" "}
          <Link to="/owner">Open owner controls</Link>
        </p>
      ) : null}
    </section>
  );
}
