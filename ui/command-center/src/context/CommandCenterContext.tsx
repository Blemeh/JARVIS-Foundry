import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { ConnectionState, JarvisClient, MockEventType, Notification, Scenario, TrustSummary } from "../lib/contracts";
import { createJarvisClient, resolveClientMode } from "../lib/clientFactory";
import { MockJarvisClient } from "../lib/mockClient";
import { resolveSourceMode, type ResolvedSourceMode } from "../lib/sourceMode";

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
  stopActivatedAt: string | null;
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

export function CommandCenterProvider({ children }: { children: ReactNode }) {
  const [client] = useState<JarvisClient>(() => createJarvisClient());
  const mode = resolveClientMode();
  const mockClient = client instanceof MockJarvisClient ? client : null;

  const [version, setVersion] = useState(0);
  const [connection, setConnection] = useState<ConnectionState>(client.connection);
  const [scenario, setScenario] = useState<Scenario | null>(null);
  const [streamConnected, setStreamConnected] = useState(true);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [notificationsStatus, setNotificationsStatus] = useState<"loading" | "ready" | "error">("loading");
  const [notificationsError, setNotificationsError] = useState<string | null>(null);
  const [demoIndex, setDemoIndex] = useState(0);
  const [stopActive, setStopActive] = useState(false);
  const [stopActivatedAt, setStopActivatedAt] = useState<string | null>(null);
  const [stopDialogOpen, setStopDialogOpen] = useState(false);
  const [trustDialogOpen, setTrustDialogOpen] = useState(false);
  const [trustSummary, setTrustSummary] = useState<TrustSummary | null>(null);
  const [trustSummaryStatus, setTrustSummaryStatus] = useState<"loading" | "ready" | "error">("loading");
  const [notifyOpen, setNotifyOpen] = useState(false);
  const appliedScenario = useState<Scenario | null>(null);
  const appliedScenarioRef = appliedScenario[0];
  const setAppliedScenario = appliedScenario[1];

  const refreshNotifications = useCallback(async () => {
    try {
      const [list, trust] = await Promise.all([
        client.listNotifications(),
        client.getTrustSummary(),
      ]);
      setNotifications(list);
      setNotificationsStatus("ready");
      setNotificationsError(null);
      setTrustSummary(trust);
      setTrustSummaryStatus("ready");
      setConnection(client.connection);
      if (mode === "live") {
        const active = trust.gateStatus === "GATED" || trust.state === "SUSPENDED";
        setStopActive(active);
      }
    } catch (err: unknown) {
      setNotifications([]);
      setNotificationsStatus("error");
      setNotificationsError(err instanceof Error ? err.message : String(err));
      setTrustSummary(null);
      setTrustSummaryStatus("error");
      setConnection(client.connection);
    }
  }, [client, mode]);

  useEffect(() => {
    const id = window.setTimeout(() => void refreshNotifications(), 0);
    return () => window.clearTimeout(id);
  }, [refreshNotifications]);

  useEffect(() => {
    const stream = client.subscribeEvents();
    const unsubscribe = stream.subscribe((event) => {
      setVersion((v) => v + 1);
      setStreamConnected(stream.connected);
      void refreshNotifications();
      if (event.type === "stop.simulated") {
        setStopActive(true);
        setStopActivatedAt(event.at);
      }
      if (event.type === "resume") {
        setStopActive(false);
        setStopActivatedAt(null);
      }
    });
    return unsubscribe;
  }, [client, refreshNotifications]);

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
    const result = await client.resumeOperations();
    if (result.accepted) {
      setStopActive(false);
      setStopActivatedAt(null);
      if (mockClient) await mockClient.resumeSimulation();
      setVersion((v) => v + 1);
      void refreshNotifications();
    }
  }, [client, mockClient, refreshNotifications]);

  const runScenario = useCallback(
    async (name: Scenario) => {
      if (mode === "live") return;
      if (!mockClient) return;
      if (appliedScenarioRef === name) return;
      setAppliedScenario(name);
      await mockClient.runScenario(name);
      setScenario(name);
      setStreamConnected(mockClient.subscribeEvents().connected);
      setVersion((v) => v + 1);
      void refreshNotifications();
    },
    [mockClient, mode, refreshNotifications, appliedScenarioRef, setAppliedScenario],
  );

  const reconnectStream = useCallback(async () => {
    if (!mockClient) return;
    await mockClient.reconnectStream();
    setStreamConnected(mockClient.subscribeEvents().connected);
    setVersion((v) => v + 1);
    void refreshNotifications();
  }, [mockClient, refreshNotifications]);

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
      stopActivatedAt,
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
      stopActivatedAt,
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
