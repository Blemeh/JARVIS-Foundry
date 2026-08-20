import type { MockEvent, MockEventStream } from "./contracts";

type Listener = (event: MockEvent) => void;

/**
 * Frontend-only event channel behind the JarvisClient boundary.
 *
 * The real runtime adapter will replace this with a ledger/event subscription.
 * Nothing here touches a live Supervisor; every emitted event is a local
 * simulation used to prove that the Command Center surfaces stay coherent.
 */
export class LocalMockEventStream implements MockEventStream {
  private listeners = new Set<Listener>();
  private _connected = true;
  private eventSeq = 0;

  get connected() {
    return this._connected;
  }

  subscribe(listener: Listener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  emit(event: MockEvent) {
    if (!this._connected) return;
    this.listeners.forEach((l) => l(event));
  }

  nextEventId(type: string) {
    this.eventSeq += 1;
    return `EV-${type.replace(/[^a-z0-9]+/gi, "-").toUpperCase()}-${String(this.eventSeq).padStart(3, "0")}`;
  }

  disconnect() {
    this._connected = false;
  }

  reconnect() {
    this._connected = true;
  }
}
