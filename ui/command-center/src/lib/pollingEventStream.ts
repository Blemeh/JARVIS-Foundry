import type { MockEvent, MockEventStream } from "./contracts";

/** Polls the live ledger projection and emits only events returned by it. */
export class PollingEventStream implements MockEventStream {
  private listeners = new Set<(event: MockEvent) => void>();
  private _connected = true;
  private timer: ReturnType<typeof setInterval> | null = null;

  constructor(
    private readonly intervalMs = 5000,
    private readonly onPoll?: () => Promise<MockEvent[]>,
  ) {}

  private startTimer(): void {
    if (this.timer || !this._connected || this.listeners.size === 0) return;
    this.timer = setInterval(async () => {
      if (!this._connected) return;
      try {
        const events = (await this.onPoll?.()) ?? [];
        for (const event of events) {
          for (const fn of this.listeners) fn(event);
        }
      } catch {
        // The adapter records the fetch error in its connection state. A poll
        // failure is not a ledger event and must never be synthesized as one.
      }
    }, this.intervalMs);
  }

  subscribe(listener: (event: MockEvent) => void): () => void {
    this.listeners.add(listener);
    this.startTimer();
    return () => {
      this.listeners.delete(listener);
      if (this.listeners.size === 0 && this.timer) {
        clearInterval(this.timer);
        this.timer = null;
      }
    };
  }

  emit(event: MockEvent): void {
    for (const fn of this.listeners) fn(event);
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
    this.startTimer();
  }

  get connected(): boolean {
    return this._connected;
  }
}
