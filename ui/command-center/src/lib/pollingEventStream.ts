import type { MockEvent, MockEventStream } from "./contracts";

/** Polls the live ledger projection and emits only events returned by it. */
export class PollingEventStream implements MockEventStream {
  private listeners = new Set<(event: MockEvent) => void>();
  private _connected = true;
  private timer: ReturnType<typeof setInterval> | null = null;
  private pollInFlight = false;
  private generation = 0;

  constructor(
    private readonly intervalMs = 5000,
    private readonly onPoll?: () => Promise<MockEvent[]>,
  ) {}

  private startTimer(): void {
    if (this.timer || !this._connected || this.listeners.size === 0) return;
    this.timer = setInterval(() => void this.pollOnce(), this.intervalMs);
  }

  private async pollOnce(): Promise<void> {
    if (!this._connected || this.pollInFlight) return;
    const generation = this.generation;
    this.pollInFlight = true;
    try {
      const events = (await this.onPoll?.()) ?? [];
      if (!this._connected || generation !== this.generation) return;
      for (const event of events) {
        for (const fn of this.listeners) fn(event);
      }
    } catch {
      // The adapter records the fetch error in its connection state. A poll
      // failure is not a ledger event and must never be synthesized as one.
    } finally {
      this.pollInFlight = false;
    }
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
    this.generation += 1;
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  reconnect(): void {
    this._connected = true;
    this.generation += 1;
    this.startTimer();
  }

  get connected(): boolean {
    return this._connected;
  }
}
