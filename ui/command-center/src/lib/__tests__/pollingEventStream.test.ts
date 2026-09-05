import { afterEach, describe, expect, it, vi } from "vitest";
import { PollingEventStream } from "../pollingEventStream";

describe("PollingEventStream", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("allows only one in-flight poll and emits no stale completion after disconnect", async () => {
    vi.useFakeTimers();
    let resolvePoll: (events: []) => void = () => undefined;
    const onPoll = vi.fn(() => new Promise<[]>((resolve) => {
      resolvePoll = resolve;
    }));
    const stream = new PollingEventStream(10, onPoll);
    const listener = vi.fn();
    stream.subscribe(listener);

    vi.advanceTimersByTime(40);
    await Promise.resolve();
    expect(onPoll).toHaveBeenCalledTimes(1);

    stream.disconnect();
    resolvePoll([]);
    await Promise.resolve();
    expect(listener).not.toHaveBeenCalled();
  });
});
