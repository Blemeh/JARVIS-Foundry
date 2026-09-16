import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type { ConnectionState, JarvisClient, MockEventType, Notification, Scenario, TrustSummary } from "../lib/contracts";
import { createJarvisClient, resolveClientMode } from "../lib/clientFactory";
import { MockJarvisClient } from "../lib/mockClient";
import { resolveSourceMode, type ResolvedSourceMode } from "../lib/sourceMode";
import { useLocale } from "../i18n/LocaleContext";

const DEMO_SEQUENCE: MockEventType[] = [
  "verification.failed",
  "repair.created",
  "worker.recovered",
  "verification.passed",
];

interface CommandCenterState {
  client: JarvisClient;
  mode: "simulated" | "live";
  sourceMode: ResolvedSourceMode;
  connection: ConnectionState;
  /** Bumped after every event/scenario mutation so mounted surfaces re-read state. */
  version: number;
  scenario: Scenario | null;
  streamConnected: boolean;
  stopActive: boolean;
  paused: boolean;
  stopActivatedAt: string | null;
  resumeTargetId: string | null;
  stopDialogOpen: boolean;
  openStopDialog: () => void;
  closeStopDialog: () => void;
  activateStop: () => Promise<void>;
  resetStop: () => Promise<void>;
  trustDialogOpen: boolean;
  openTrustDialog: () => void;
  closeTrustDialog: () => void;
  trustSummary: TrustSummary | null;
  trustSummaryStatus: "loading" | "ready" | "error";
  notifications: Notification[];
  notificationsStatus: "loading" | "ready" | "error";
  notificationsError: string | null;
  unreadCount: number;
  notifyOpen: boolean;
  openNotify: () => void;
  closeNotify: () => void;
  runScenario: (name: Scenario) => void;
  reconnectStream: () => Promise<void>;
  simulateEvent: (type: MockEventType) => void;
  simulateNextEvent: () => void;
  demoIndex: number;
  acknowledgeNotification: (id: string) => void;
  resolveNotification: (id: string) => void;
  acknowledgeAll: () => void;
  mutationsEnabled: boolean;
}

const CommandCenterContext = createContext<CommandCenterState | null>(null);

function sameConnection(left: ConnectionState, right: ConnectionState): boolean {
  return left.connected === right.connected
    && left.source === right.source
    && left.stale === right.stale
    && left.lastFetchedAt === right.lastFetchedAt
    && left.error === right.error;
}

export function CommandCenterProvider({ children }: { children: ReactNode }) {
  const [client] = useState<JarvisClient>(() => createJarvisClient());
  const mode = resolveClientMode();
  const mockClient = client instanceof MockJarvisClient ? client : null;
  const { locale, routing } = useLocale();

  const [version, setVersion] = useState(0);
  const [connection, setConnection] = useState<ConnectionState>(client.connection);
  const [scenario, setScenario] = useState<Scenario | null>(null);
  const [streamConnected, setStreamConnected] = useState(client.connection.connected);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [notificationsStatus, setNotificationsStatus] = useState<"loading" | "ready" | "error">("loading");
  const [notificationsError, setNotificationsError] = useState<string | null>(null);
  const [demoIndex, setDemoIndex] = useState(0);
  const [stopActive, setStopActive] = useState(false);
  const [paused, setPaused] = useState(false);
  const [stopActivatedAt, setStopActivatedAt] = useState<string | null>(null);
  const [resumeTargetId, setResumeTargetId] = useState<string | null>(null);
  const [stopDialogOpen, setStopDialogOpen] = useState(false);
  const [trustDialogOpen, setTrustDialogOpen] = useState(false);
  const [trustSummary, setTrustSummary] = useState<TrustSummary | null>(null);
  const [trustSummaryStatus, setTrustSummaryStatus] = useState<"loading" | "ready" | "error">("loading");
  const [notifyOpen, setNotifyOpen] = useState(false);
  // ScenarioBootstrap runs from a StrictMode effect in development.  A
  // state-only guard is asynchronous, so the effect can apply the same
  // scenario twice before React commits the state update (duplicating
  // timeline/notification IDs).  A ref is the synchronous idempotency key;
  // the public scenario state remains the render-visible projection.
  const appliedScenarioRef = useRef<Scenario | null>(null);

  const refreshNotifications = useCallback(async () => {
    try {
      const [list, trust, ownerSurface] = await Promise.all([
        client.listNotifications(),
        client.getTrustSummary(),
        client.getOwnerControlSurface?.() ?? Promise.resolve(null),
      ]);
      setNotifications(list);
      setNotificationsStatus("ready");
      setNotificationsError(null);
      setTrustSummary(trust);
      setTrustSummaryStatus("ready");
      const nextConnection = client.connection;
      setConnection(nextConnection);
      if (mode === "live") {
        setStreamConnected(nextConnection.connected && !nextConnection.stale);
      }
      const stopped = Boolean(ownerSurface?.stopped);
      const nextPaused = Boolean(ownerSurface?.paused);
      if (ownerSurface) {
        setStopActive(stopped);
        setPaused(nextPaused);
        setResumeTargetId(ownerSurface.resumableDirectiveIds[0] ?? null);
        if (!stopped) setStopActivatedAt(null);
      }
    } catch (err: unknown) {
      setNotifications([]);
      setNotificationsStatus("error");
      setNotificationsError(err instanceof Error ? err.message : String(err));
      setTrustSummary(null);
      setTrustSummaryStatus("error");
      const nextConnection = client.connection;
      setConnection(nextConnection);
      if (mode === "live") setStreamConnected(false);
    }
  }, [client, mode]);

  useEffect(() => {
    if (mode !== "live") return;
    void client.saveOwnerUiPrefs({ locale, notificationRouting: routing }).catch(() => undefined);
  }, [client, locale, mode, routing]);

  useEffect(() => {
    if (mode !== "live" || routing !== "in-app-toast") return;
    void client.deliverAttentionToasts().catch(() => undefined);
  }, [client, mode, routing, version]);

  useEffect(() => {
    const id = window.setTimeout(() => void refreshNotifications(), 0);
    return () => window.clearTimeout(id);
  }, [refreshNotifications]);

  useEffect(() => {
    const stream = client.subscribeEvents();
    const syncConnection = () => {
      const next = client.connection;
      setConnection((current) => sameConnection(current, next) ? current : next);
      setStreamConnected(stream.connected && (mode === "simulated" || next.connected && !next.stale));
    };
    const unsubscribe = stream.subscribe((event) => {
      setVersion((v) => v + 1);
      syncConnection();
      void refreshNotifications();
      if (event.type === "stop.simulated") {
        setStopActive(true);
        setStopActivatedAt(event.at);
      }
      if (event.type === "resume") {
        setStopActive(false);
        setPaused(false);
        setStopActivatedAt(null);
      }
    });
    syncConnection();
    const connectionTimer = window.setInterval(syncConnection, 1000);
    return () => {
      window.clearInterval(connectionTimer);
      unsubscribe();
    };
  }, [client, mode, refreshNotifications]);

  const openStopDialog = useCallback(() => setStopDialogOpen(true), []);
  const closeStopDialog = useCallback(() => setStopDialogOpen(false), []);
  const openTrustDialog = useCallback(() => setTrustDialogOpen(true), []);
  const closeTrustDialog = useCallback(() => setTrustDialogOpen(false), []);
  const openNotify = useCallback(() => setNotifyOpen(true), []);
  const closeNotify = useCallback(() => setNotifyOpen(false), []);

  const activateStop = useCallback(async () => {
    const result = await client.activateStop("owner requested STOP from command center UI");
    if (result.accepted) {
      setStopActive(true);
      setStopActivatedAt(result.at);
      setVersion((v) => v + 1);
      void refreshNotifications();
    }
  }, [client, refreshNotifications]);

  const resetStop = useCallback(async () => {
    if (!mockClient && !resumeTargetId) return;
    const result = await client.resumeOperations(resumeTargetId ?? undefined);
    if (result.accepted) {
      if (mockClient) {
        await mockClient.resumeSimulation();
        setStopActive(false);
        setPaused(false);
        setStopActivatedAt(null);
        setResumeTargetId(null);
      }
      setVersion((v) => v + 1);
      await refreshNotifications();
    }
  }, [client, mockClient, refreshNotifications, resumeTargetId]);

  const runScenario = useCallback(
    async (name: Scenario) => {
      if (mode === "live") return;
      if (!mockClient) return;
      if (appliedScenarioRef.current === name) return;
      appliedScenarioRef.current = name;
      await mockClient.runScenario(name);
      setScenario(name);
      setStreamConnected(mockClient.subscribeEvents().connected);
      setVersion((v) => v + 1);
      void refreshNotifications();
    },
    [mockClient, mode, refreshNotifications],
  );

  const reconnectStream = useCallback(async () => {
    try {
      await client.reconnectStream();
      setVersion((v) => v + 1);
      void refreshNotifications();
    } finally {
      const next = client.connection;
      setConnection(next);
      setStreamConnected(next.connected && !next.stale);
    }
  }, [client, refreshNotifications]);

  const simulateEvent = useCallback(
    async (type: MockEventType) => {
      if (!mockClient) return;
      await mockClient.simulateEvent(type);
    },
    [mockClient],
  );

  const simulateNextEvent = useCallback(async () => {
    if (!mockClient) return;
    const type = DEMO_SEQUENCE[demoIndex % DEMO_SEQUENCE.length];
    setDemoIndex((i) => i + 1);
    await mockClient.simulateEvent(type);
  }, [mockClient, demoIndex]);

  const acknowledgeNotification = useCallback(
    async (id: string) => {
      await client.acknowledgeNotification(id);
      void refreshNotifications();
    },
    [client, refreshNotifications],
  );

  const resolveNotification = useCallback(
    async (id: string) => {
      await client.resolveNotification(id);
      void refreshNotifications();
    },
    [client, refreshNotifications],
  );

  const acknowledgeAll = useCallback(async () => {
    await Promise.all(notifications.filter((n) => n.state === "UNREAD").map((n) => client.acknowledgeNotification(n.id)));
    void refreshNotifications();
  }, [client, notifications, refreshNotifications]);

  const sourceMode = resolveSourceMode(mode, connection);
  const unreadCount = useMemo(() => notifications.filter((n) => n.state === "UNREAD").length, [notifications]);
  // A live control is available only after a canonical snapshot is connected.
  // Degraded/unknown/disconnected sources fail closed rather than attempting a
  // mutation against an untrusted or stale runtime.
  const mutationsEnabled = !stopActive && (sourceMode === "SIMULATED" || sourceMode === "LIVE");

  const value = useMemo<CommandCenterState>(
    () => ({
      client,
      mode,
      sourceMode,
      connection,
      version,
      scenario,
      streamConnected,
      stopActive,
      paused,
      stopActivatedAt,
      resumeTargetId,
      stopDialogOpen,
      openStopDialog,
      closeStopDialog,
      activateStop,
      resetStop,
      trustDialogOpen,
      openTrustDialog,
      closeTrustDialog,
      trustSummary,
      trustSummaryStatus,
      notifications,
      notificationsStatus,
      notificationsError,
      unreadCount,
      notifyOpen,
      openNotify,
      closeNotify,
      runScenario,
      reconnectStream,
      simulateEvent,
      simulateNextEvent,
      demoIndex,
      acknowledgeNotification,
      resolveNotification,
      acknowledgeAll,
      mutationsEnabled,
    }),
    [
      client,
      mode,
      sourceMode,
      connection,
      version,
      scenario,
      streamConnected,
      stopActive,
      paused,
      stopActivatedAt,
      resumeTargetId,
      stopDialogOpen,
      openStopDialog,
      closeStopDialog,
      activateStop,
      resetStop,
      trustDialogOpen,
      openTrustDialog,
      closeTrustDialog,
      trustSummary,
      trustSummaryStatus,
      notifications,
      notificationsStatus,
      notificationsError,
      unreadCount,
      notifyOpen,
      openNotify,
      closeNotify,
      runScenario,
      reconnectStream,
      simulateEvent,
      simulateNextEvent,
      demoIndex,
      acknowledgeNotification,
      resolveNotification,
      acknowledgeAll,
      mutationsEnabled,
    ],
  );

  return <CommandCenterContext.Provider value={value}>{children}</CommandCenterContext.Provider>;
}

export function useCommandCenter() {
  const ctx = useContext(CommandCenterContext);
  if (!ctx) throw new Error("useCommandCenter must be used inside CommandCenterProvider");
  return ctx;
}
