export function formatHash(hash: string, keep = 12): string {
  return hash.length > keep + 4 ? `${hash.slice(0, keep)}...${hash.slice(-4)}` : hash;
}

export function formatClock(date: Date): { time: string; date: string; tz: string } {
  const time = new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
  const weekday = new Intl.DateTimeFormat("en", { weekday: "short" }).format(date);
  const pad = (n: number) => String(n).padStart(2, "0");
  const dateStr = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${weekday.toUpperCase()}`;
  const tz = new Intl.DateTimeFormat(undefined, { timeZoneName: "short" })
    .formatToParts(date)
    .find((p) => p.type === "timeZoneName")?.value;
  return { time, date: dateStr.toUpperCase(), tz: tz ?? "LOCAL" };
}

export function relativeTime(fromIso: string, now = new Date()): string {
  const diffMs = Math.max(0, now.getTime() - new Date(fromIso).getTime());
  const sec = Math.floor(diffMs / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  return `${Math.floor(hr / 24)}d ago`;
}
