import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useClock } from "../../lib/useClock";

function ClockProbe() {
  const clock = useClock();
  return (
    <span>
      <span data-testid="time">{clock.time}</span>
      <span data-testid="date">{clock.date}</span>
      <span data-testid="tz">{clock.tz}</span>
    </span>
  );
}

describe("Clock", () => {
  it("renders real local time, date, and timezone", () => {
    render(<ClockProbe />);
    expect(screen.getByTestId("time").textContent).toMatch(/^\d{2}:\d{2}:\d{2}$/);
    expect(screen.getByTestId("date").textContent).toMatch(/\d{4}-\d{2}-\d{2}/);
    expect(screen.getByTestId("tz").textContent!.length).toBeGreaterThan(0);
  });
});
