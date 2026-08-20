import type { MockEvent, MockEventStream } from "./contracts";

/** Poll-driven refresh signal for live mode (no mock fixture events). */
export class PollingEventStream implements MockEventStream {
  private listeners = new Set<(event: MockEvent) => void>();
  private _connected = true;
  private timer: ReturnType<typeof setInterval> | null = null;

  constructor(private readonly intervalMs = 5000, private readonly onPoll?: () => void) {}

  subscribe(listener: (event: MockEvent) => void): () => void {
    this.listeners.add(listener);
    if (!this.timer && this._connected) {
      this.timer = setInterval(() => {
        if (!this._connected) return;
        this.onPoll?.();
        const event: MockEvent = {
          id: `poll-${Date.now()}`,
          type: "mission.started",
          at: new Date().toISOString(),
          detail: "snapshot refresh",
        };
        for (const fn of this.listeners) fn(event);
      }, this.intervalMs);
    }
    return () => this.listeners.delete(listener);
  }

  emit(_event: MockEvent): void {
    /* live mode does not emit synthetic fixture events */
  }

  disconnect(): void {
    this._connected = false;
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  reconnect(): void {
    this._connected = true;
  }

  get connected(): boolean {
    return this._connected;
  }
}
