import { useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, Check, History, Keyboard, Search, Terminal } from "lucide-react";
import { useCommandCenter } from "../context/CommandCenterContext";
import type { SearchResultItem, SearchResults } from "../lib/contracts";
import { agentRunToWorker } from "../lib/cohesion";

const groupOrder: { key: keyof SearchResults; label: string }[] = [
  { key: "missions", label: "Missions" },
  { key: "agents", label: "Agents" },
  { key: "evidence", label: "Evidence" },
  { key: "systems", label: "Systems" },
  { key: "memory", label: "Memory" },
  { key: "timeline", label: "Timeline" },
];

interface Intent {
  label: string;
  to: string;
}

function parseIntent(raw: string): Intent | null {
  const s = raw.trim().toLowerCase();
  if (!s) return null;

  const missionMatch = s.match(/(?:show|open|view|go to|navigate to)?\s*(?:mission(?:s)?)\s+([a-z]{1,3}-[a-z0-9-]+)/i);
  if (missionMatch) return { label: `Open mission ${missionMatch[1].toUpperCase()}`, to: `/missions/${missionMatch[1].toUpperCase()}` };

  const agentMatch = s.match(/(?:show|open|view)\s+(?:agent|worker)\s*([a-z]{1,4}-[a-z0-9-]+|a-\d+)/i);
  if (agentMatch) {
    const id = agentMatch[1].toUpperCase();
    const workerId = agentRunToWorker[id] ?? id;
    return { label: `Open agent ${id}`, to: `/agents/${workerId}` };
  }

  const evidenceMatch = s.match(/(?:show|open|view)\s+evidence\s*([a-z]{2,3}-[a-z0-9-]+)/i);
  if (evidenceMatch) return { label: `Open evidence ${evidenceMatch[1].toUpperCase()}`, to: `/evidence/${evidenceMatch[1].toUpperCase()}` };

  const memoryMatch = s.match(/(?:show|open|view)\s+memory\s*([a-z]{1,3}-[a-z0-9]+)/i);
  if (memoryMatch) return { label: `Open memory ${memoryMatch[1]}`, to: `/memory/${memoryMatch[1]}` };

  const systemMatch = s.match(/(?:show|open|view)\s+system\s+([a-z-]+)/i);
  if (systemMatch) {
    const name = systemMatch[1].toLowerCase();
    const map: Record<string, string> = {
      supervisor: "SYS-SUPERVISOR",
      gateway: "SYS-GATEWAY",
      hermes: "SYS-HERMES-WORKER",
      memory: "SYS-MEMORY",
      ledger: "SYS-LEDGER",
      stop: "SYS-STOP",
      verification: "SYS-VERIFICATION",
      routing: "SYS-ROUTING",
      workers: "SYS-WORKERS",
      worker: "SYS-WORKERS",
      connectors: "SYS-CONNECTORS",
      evidence: "SYS-EVIDENCE",
      foundry: "SYS-FOUNDRY",
    };
    const id = map[name] ?? `SYS-${name.toUpperCase()}`;
    return { label: `Open system ${id}`, to: `/systems/${id}` };
  }

  if (/show\s+failed\s+verification/.test(s)) return { label: "Failed verification results", to: "/evidence?verdict=FAILED" };
  if (/show\s+degraded\s+systems/.test(s)) return { label: "Degraded systems", to: "/systems?health=degraded" };
  if (/show\s+evidence\s+for\s+mission\s+([a-z]{1,3}-[a-z0-9-]+)/i.test(s)) {
    const m = s.match(/show\s+evidence\s+for\s+mission\s+([a-z]{1,3}-[a-z0-9-]+)/i)!;
    return { label: `Evidence for ${m[1].toUpperCase()}`, to: `/evidence?mission=${m[1].toUpperCase()}` };
  }
  if (/timeline\s+for\s+([a-z]{1,3}-[a-z0-9-]+)/i.test(s)) {
    const m = s.match(/timeline\s+for\s+([a-z]{1,3}-[a-z0-9-]+)/i)!;
    return { label: `Timeline for ${m[1].toUpperCase()}`, to: `/timeline?mission=${m[1].toUpperCase()}` };
  }
  if (/show\s+blocked\s+missions/.test(s)) return { label: "Blocked missions", to: "/missions?status=blocked" };
  if (/show\s+active\s+agents/.test(s)) return { label: "Active agents", to: "/agents?status=active" };
  if (/show\s+(supervisor|stop\b|hermes\b|gateway\b|ledger\b)/.test(s)) {
    const name = s.match(/show\s+(supervisor|stop\b|hermes\b|gateway\b|ledger\b)/)![1];
    const map: Record<string, string> = {
      supervisor: "SYS-SUPERVISOR",
      stop: "SYS-STOP",
      hermes: "SYS-HERMES-WORKER",
      gateway: "SYS-GATEWAY",
      ledger: "SYS-LEDGER",
    };
    return { label: `Open system ${map[name]}`, to: `/systems/${map[name]}` };
  }
  if (/notifications|alerts|exceptions/.test(s)) return { label: "Notification center", to: "/notifications" };
  return null;
}

export function CommandBar() {
  const { client, stopActive } = useCommandCenter();
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const [value, setValue] = useState("");
  const [results, setResults] = useState<SearchResults | null>(null);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [history, setHistory] = useState<string[]>([]);
  const [historyIndex, setHistoryIndex] = useState(-1);
  const [status, setStatus] = useState<string | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);

  const flat = useMemo(() => {
    if (!results) return [] as { item: SearchResultItem; group: string }[];
    return groupOrder.flatMap((g) =>
      results[g.key].map((item) => ({ item, group: g.label })),
    );
  }, [results]);

  useEffect(() => {
    if (!value.trim()) return;
    let cancelled = false;
    void client.search(value).then((r) => {
      if (!cancelled) setResults(r);
    });
    return () => {
      cancelled = true;
    };
  }, [value, client]);

  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      const target = e.target as HTMLElement;
      const typing =
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        target instanceof HTMLSelectElement ||
        target?.isContentEditable;
      if (typing) return;
      if (e.key === "/") {
        e.preventDefault();
        inputRef.current?.focus();
      } else if (e.key === "?") {
        e.preventDefault();
        setHelpOpen((v) => !v);
      } else if (e.key === "g") {
        const done = (letter: string) => {
          const map: Record<string, string> = { m: "/missions", a: "/agents", e: "/evidence", t: "/timeline", s: "/systems" };
          if (map[letter]) navigate(map[letter]);
        };
        const handler = (ev: globalThis.KeyboardEvent) => {
          document.removeEventListener("keydown", handler);
          if (!ev.ctrlKey && !ev.metaKey && !ev.altKey) done(ev.key.toLowerCase());
        };
        document.addEventListener("keydown", handler);
        window.setTimeout(() => document.removeEventListener("keydown", handler), 600);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [navigate]);

  const navigateTo = (to: string, label: string) => {
    setStatus(`LOCAL NAVIGATION - opened ${label}. No runtime command executed.`);
    window.setTimeout(() => setStatus(null), 4200);
    setResults(null);
    setActiveIndex(-1);
    setValue("");
    navigate(to);
  };

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    const cmd = value.trim();
    if (!cmd) return;
    setHistory((h) => [cmd, ...h].slice(0, 8));
    setHistoryIndex(-1);
    const intent = parseIntent(cmd);
    if (intent) {
      navigateTo(intent.to, intent.label);
      return;
    }
    if (cmd === "help") {
      setHelpOpen(true);
      setValue("");
      return;
    }
    setValue("");
    setStatus(`LOCAL ONLY - "${cmd}" captured in this candidate. No runtime invocation.`);
    window.setTimeout(() => setStatus(null), 4200);
  };

  const onKeyDown = (e: ReactKeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Escape") {
      setResults(null);
      setHelpOpen(false);
      inputRef.current?.blur();
      return;
    }
    if (e.key === "ArrowDown" && flat.length > 0) {
      e.preventDefault();
      setActiveIndex((i) => (i + 1) % flat.length);
    } else if (e.key === "ArrowUp" && flat.length > 0) {
      e.preventDefault();
      setActiveIndex((i) => (i <= 0 ? flat.length - 1 : i - 1));
    } else if (e.key === "Enter") {
      if (activeIndex >= 0 && flat[activeIndex]) {
        e.preventDefault();
        navigateTo(flat[activeIndex].item.to, flat[activeIndex].item.label);
      }
    } else if (e.key === "ArrowUp" && history.length > 0) {
      e.preventDefault();
      const next = Math.min(historyIndex + 1, history.length - 1);
      setHistoryIndex(next);
      setValue(history[next]);
    } else if (e.key === "ArrowDown" && historyIndex >= 0) {
      e.preventDefault();
      const next = historyIndex - 1;
      setHistoryIndex(next);
      setValue(next >= 0 ? history[next] : "");
    }
  };

  const intent = parseIntent(value);

  return (
    <footer className="commandbar" role="contentinfo" aria-label="Command surface">
      <div className="commandbar-inner">
        <Terminal size={18} className="commandbar-icon" aria-hidden="true" />
        <form className="commandbar-form" onSubmit={submit} aria-label="Simulated command and search">
          <Search size={14} className="commandbar-search-icon" aria-hidden="true" />
          <input
            ref={inputRef}
            className="commandbar-input"
            value={value}
            onChange={(e) => {
              const v = e.target.value;
              setValue(v);
              if (!v.trim()) {
                setResults(null);
                setActiveIndex(-1);
              }
            }}
            onKeyDown={onKeyDown}
            placeholder={
              stopActive
                ? "Search frozen during simulated STOP..."
                : "Search missions, agents, evidence, systems... or press /"
            }
            aria-label="Command and search input (simulated, local only)"
            aria-expanded={flat.length > 0 || intent !== null}
            role="combobox"
            aria-controls="command-results"
            aria-autocomplete="list"
          />
          <button
            type="submit"
            className="btn btn-ghost commandbar-execute"
            disabled={stopActive}
            title={stopActive ? "Mutation-like commands are frozen during simulated STOP" : "Execute (local only)"}
          >
            Execute
          </button>
        </form>
        <div className="commandbar-side">
          {status ? (
            <span className="commandbar-status" role="status">
              <Check size={12} aria-hidden="true" />
              {status}
            </span>
          ) : (
            <>
              <span className="commandbar-suggest-label">
                <History size={12} aria-hidden="true" />
                {history.length > 0 ? `History ${history.length}` : "Try:"}
              </span>
              {["show mission M-HYB-014", "show failed verification", "show degraded systems"].map((s) => (
                <button
                  key={s}
                  type="button"
                  className="commandbar-suggest"
                  onClick={() => setValue(s)}
                >
                  {s}
                </button>
              ))}
              <button
                type="button"
                className="commandbar-help-btn"
                aria-label="Keyboard shortcuts"
                aria-expanded={helpOpen}
                onClick={() => setHelpOpen((v) => !v)}
              >
                <Keyboard size={14} aria-hidden="true" />
              </button>
            </>
          )}
        </div>
      </div>

      {flat.length > 0 || intent ? (
        <div className="command-results" id="command-results" role="listbox" aria-label="Search results">
          {intent ? (
            <button type="button" className="command-intent" role="option" aria-selected={activeIndex === -1} onClick={() => navigateTo(intent!.to, intent!.label)} onMouseEnter={() => setActiveIndex(-1)}>
              <ArrowRight size={13} aria-hidden="true" />
              <span className="t-label">Navigate</span>
              <span className="t-mono">{intent.label}</span>
            </button>
          ) : null}
          {groupOrder.map((g) => {
            const items = results?.[g.key] ?? [];
            if (items.length === 0) return null;
            return (
              <div key={g.key} className="command-group">
                <span className="t-label command-group-label">{g.label}</span>
                {items.map((item) => {
                  const idx = flat.findIndex((f) => f.item.id === item.id && f.item.kind === item.kind);
                  return (
                    <button
                      key={`${item.kind}-${item.id}`}
                      type="button"
                      className={`command-result ${activeIndex === idx ? "command-result-active" : ""}`}
                      role="option"
                      aria-selected={activeIndex === idx}
                      onClick={() => navigateTo(item.to, item.label)}
                      onMouseEnter={() => setActiveIndex(idx)}
                    >
                      <span className="t-mono command-result-id">{item.id}</span>
                      <span className="command-result-main">
                        <span className="command-result-label">{item.label}</span>
                        <span className="t-mono command-result-sub">{item.sublabel}</span>
                      </span>
                    </button>
                  );
                })}
              </div>
            );
          })}
          {flat.length === 0 && !intent ? (
            <div className="command-empty">
              <span className="t-mono text-dim">No fixture objects match "{value}".</span>
            </div>
          ) : null}
        </div>
      ) : null}

      {helpOpen ? (
        <div className="command-help" role="dialog" aria-label="Keyboard shortcuts">
          <span className="t-label command-help-title">Shortcuts</span>
          <ul className="command-help-list">
            <li><span className="kbd">/</span><span className="t-mono">Focus search / command</span></li>
            <li><span className="kbd">g</span><span className="t-mono">then <span className="kbd">m</span> missions · <span className="kbd">a</span> agents · <span className="kbd">e</span> evidence · <span className="kbd">t</span> timeline · <span className="kbd">s</span> systems</span></li>
            <li><span className="kbd">?</span><span className="t-mono">Toggle this help</span></li>
            <li><span className="kbd">Esc</span><span className="t-mono">Close dialogs / search</span></li>
          </ul>
          <span className="t-mono text-dim command-help-note">Navigation is local. No live JARVIS command is ever invoked.</span>
        </div>
      ) : null}
    </footer>
  );
}
