import { useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, Check, CircleAlert, Command, Search, Send, Sparkles } from "lucide-react";
import { useCommandCenter } from "../context/CommandCenterContext";
import type { OwnerDelegationMode, SearchResultItem, SearchResults } from "../lib/contracts";
import { agentRunToWorker } from "../lib/cohesion";
import { sourceModeLabel } from "../lib/sourceMode";
import { StopControl } from "./EmergencyStop";

const groupOrder: { key: keyof SearchResults; label: string }[] = [
  { key: "missions", label: "Work" },
  { key: "agents", label: "Agents" },
  { key: "evidence", label: "Evidence" },
  { key: "systems", label: "Systems" },
  { key: "memory", label: "Memory" },
  { key: "timeline", label: "Activity" },
];

interface Intent { label: string; to: string }

function parseCommandIntent(raw: string): Intent | null {
  const s = raw.trim().toLowerCase();
  if (!s) return null;
  const destinations: Array<[RegExp, string, string]> = [
    [/^(?:open|show|go to)\s+(?:jarvis|home|focus)$/, "Open current focus", "/"],
    [/^(?:open|show|go to)\s+work$/, "Open work", "/work"],
    [/^(?:open|show|go to)\s+world$/, "Open world", "/world"],
    [/^(?:open|show|go to)\s+capabilities$/, "Open capabilities", "/capabilities"],
    [/^(?:open|show|go to)\s+(?:you|owner)$/, "Open owner context", "/you"],
    [/^(?:open|show|go to)\s+(?:activity|timeline)$/, "Open activity", "/activity"],
    [/^(?:open|show|go to)\s+evidence$/, "Open evidence", "/evidence"],
  ];
  for (const [pattern, label, to] of destinations) if (pattern.test(s)) return { label, to };

  const mission = s.match(/(?:show|open|view)?\s*missions?\s+([a-z]{1,4}-[a-z0-9-]+)/i);
  if (mission) return { label: `Open mission ${mission[1].toUpperCase()}`, to: `/missions/${mission[1].toUpperCase()}` };
  const agent = s.match(/(?:show|open|view)\s+(?:agent|worker)\s*([a-z]{1,4}-[a-z0-9-]+|a-\d+)/i);
  if (agent) {
    const id = agent[1].toUpperCase();
    return { label: `Open agent ${id}`, to: `/agents/${agentRunToWorker[id] ?? id}` };
  }
  const evidence = s.match(/(?:show|open|view)\s+evidence\s*([a-z]{2,4}-[a-z0-9-]+)/i);
  if (evidence) return { label: `Open evidence ${evidence[1].toUpperCase()}`, to: `/evidence/${evidence[1].toUpperCase()}` };
  if (/failed\s+verification/.test(s)) return { label: "Show failed verification", to: "/evidence?verdict=FAILED" };
  if (/blocked\s+(?:work|missions)/.test(s)) return { label: "Show blocked work", to: "/work?status=blocked" };
  if (/degraded\s+systems/.test(s)) return { label: "Show degraded systems", to: "/systems?health=degraded" };
  return null;
}

const delegationModes = [
  { label: "ASK", detail: "Get a briefing", value: "TELL_ME" },
  { label: "GOAL", detail: "Set an outcome", value: "HANDLE" },
  { label: "VETO", detail: "Set a boundary", value: "VETO" },
] as const;
type DelegationMode = (typeof delegationModes)[number]["label"];

function delegationBody(raw: string): string {
  const trimmed = raw.trimStart();
  const currentPrefix = delegationModes.find((item) =>
    trimmed.toLowerCase().startsWith(`${item.label.toLowerCase()}:`));
  return currentPrefix ? trimmed.slice(currentPrefix.label.length + 1).trimStart() : trimmed;
}

const modePlaceholders: Record<DelegationMode, string> = {
  ASK: "Ask JARVIS for a briefing…",
  GOAL: "What outcome should JARVIS work toward?",
  VETO: "What should JARVIS avoid?",
};

export function CommandBar() {
  const { client, stopActive, sourceMode, ownerCommandsEnabled } = useCommandCenter();
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const [value, setValue] = useState("");
  const [results, setResults] = useState<SearchResults | null>(null);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [history, setHistory] = useState<string[]>([]);
  const [status, setStatus] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [sending, setSending] = useState(false);
  const [mode, setMode] = useState<DelegationMode>("ASK");

  const flat = useMemo(() => {
    if (!results) return [] as { item: SearchResultItem; group: string }[];
    return groupOrder.flatMap((group) => results[group.key].map((item) => ({ item, group: group.label })));
  }, [results]);
  const commandBody = delegationBody(value);
  const intent = parseCommandIntent(commandBody);

  useEffect(() => {
    const query = delegationBody(value).trim();
    if (!query) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void client.search(query).then((next) => { if (!cancelled) setResults(next); }).catch(() => { if (!cancelled) setResults(null); });
    }, 250);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [value, client]);

  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => {
      const target = event.target as HTMLElement;
      const typing = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target?.isContentEditable;
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        inputRef.current?.focus();
      } else if (!typing && event.key === "/") {
        event.preventDefault();
        inputRef.current?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const clearSoon = () => window.setTimeout(() => setStatus(null), 5200);
  const navigateTo = (to: string, label: string) => {
    setStatus({ kind: "ok", text: `${label} · local navigation` });
    setValue(""); setResults(null); setActiveIndex(-1); navigate(to); clearSoon();
  };

  const selectMode = (nextMode: DelegationMode) => {
    setMode(nextMode);
    setResults(null);
    setActiveIndex(-1);
    window.requestAnimationFrame(() => inputRef.current?.focus());
  };

  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    const body = delegationBody(value).trim();
    const command = body;
    if (!command || sending) return;
    setHistory((current) => [command, ...current.filter((entry) => entry !== command)].slice(0, 6));
    const parsed = parseCommandIntent(body);
    if (parsed) { navigateTo(parsed.to, parsed.label); return; }
    if (!ownerCommandsEnabled || stopActive) {
      setStatus({ kind: "error", text: `Command held · ${stopActive ? "STOP is active" : `${sourceModeLabel(sourceMode)} source is not writable`}` });
      clearSoon(); return;
    }
    setSending(true);
    try {
      const delegationMode = delegationModes.find((item) => item.label === mode)?.value as OwnerDelegationMode;
      const result = mode === "VETO"
        ? await client.submitVeto(command)
        : await client.submitOwnerGoal(command, delegationMode);
      if (result.accepted) {
        setValue(""); setResults(null);
        setStatus({ kind: "ok", text: `Owner command recorded${result.directiveId ? ` · ${result.directiveId}` : ""}` });
      } else {
        setStatus({ kind: "error", text: result.error ?? "Owner goal rejected" });
      }
    } catch (error) {
      setStatus({ kind: "error", text: error instanceof Error ? error.message : String(error) });
    } finally {
      setSending(false);
    }
  };

  const onKeyDown = (event: ReactKeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") { setResults(null); inputRef.current?.blur(); return; }
    if (event.key === "ArrowDown" && flat.length > 0) { event.preventDefault(); setActiveIndex((index) => (index + 1) % flat.length); }
    if (event.key === "ArrowUp" && flat.length > 0) { event.preventDefault(); setActiveIndex((index) => index <= 0 ? flat.length - 1 : index - 1); }
    if (event.key === "Enter" && activeIndex >= 0 && flat[activeIndex]) {
      event.preventDefault(); navigateTo(flat[activeIndex].item.to, flat[activeIndex].item.label);
    }
  };

  return (
    <div className="commandbar" role="region" aria-label="Persistent JARVIS command surface">
      <div className="commandbar-modes" aria-label="Command intent">
        <span className="commandbar-modes-label">Direct JARVIS</span>
        {delegationModes.map((item) => (
          <button
            key={item.label}
            type="button"
            className={`commandbar-mode ${mode === item.label ? "is-active" : ""}`}
            aria-pressed={mode === item.label}
            title={`${item.label}: ${item.detail}`}
            onClick={() => selectMode(item.label)}
          >
            <strong>{item.label}</strong>
            <small>{item.detail}</small>
          </button>
        ))}
      </div>
      <form className="commandbar-form" onSubmit={(event) => void submit(event)}>
        <label className="commandbar-mobile-mode">
          <span>Command intent</span>
          <select
            value={mode}
            aria-label="Command intent"
            onChange={(event) => selectMode(event.target.value as DelegationMode)}
          >
            {delegationModes.map((item) => <option key={item.label} value={item.label}>{item.label}</option>)}
          </select>
        </label>
        <Sparkles size={16} className="commandbar-icon" aria-hidden="true" />
        <input
          ref={inputRef}
          className="commandbar-input"
          value={value}
          onChange={(event) => { const next = event.target.value; setValue(next); setStatus(null); setActiveIndex(-1); if (!next.trim()) setResults(null); }}
          onKeyDown={onKeyDown}
          placeholder={stopActive ? "STOP active — inspect or resume before directing work" : modePlaceholders[mode]}
          aria-label={`Message JARVIS or search — ${mode} mode (${sourceModeLabel(sourceMode)})`}
          aria-expanded={Boolean(value && (intent || flat.length))}
          aria-controls="command-results"
          role="combobox"
          aria-autocomplete="list"
        />
        <span className="commandbar-shortcut" aria-hidden="true"><Command size={11} />K</span>
        <button type="submit" className="commandbar-send" disabled={!commandBody.trim() || sending || stopActive} aria-label="Send command">
          {sending ? <span className="commandbar-sending" /> : <Send size={15} aria-hidden="true" />}
        </button>
      </form>
      <StopControl compact label="Emergency STOP" />

      {status ? (
        <div className={`commandbar-status commandbar-status--${status.kind}`} role="status">
          {status.kind === "ok" ? <Check size={13} aria-hidden="true" /> : <CircleAlert size={13} aria-hidden="true" />}{status.text}
        </div>
      ) : null}
      {value && (intent || flat.length > 0) ? (
        <div className="command-results" id="command-results" role="listbox" aria-label="Command matches">
          {intent ? (
            <button type="button" className="command-intent" role="option" aria-selected="false" onClick={() => navigateTo(intent.to, intent.label)}>
              <ArrowRight size={14} aria-hidden="true" /><span>{intent.label}</span><small>Navigate</small>
            </button>
          ) : null}
          {flat.slice(0, 8).map(({ item, group }, index) => (
            <button key={`${item.kind}-${item.id}`} type="button" className={`command-result ${activeIndex === index ? "command-result-active" : ""}`} role="option" aria-selected={activeIndex === index} onClick={() => navigateTo(item.to, item.label)} onMouseEnter={() => setActiveIndex(index)}>
              <Search size={13} aria-hidden="true" /><span><strong>{item.label}</strong><small>{group} · {item.sublabel}</small></span>
            </button>
          ))}
        </div>
      ) : null}
      {!value && history.length > 0 ? <span className="commandbar-history" title={history.join(" · ")}>Last: {history[0]}</span> : null}
    </div>
  );
}
