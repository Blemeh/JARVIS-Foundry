import { existsSync, mkdirSync, readFileSync, unlinkSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { execSync } from "node:child_process";

const laneDir = join(process.cwd(), ".lane");

export function laneRecordPath(laneId) {
  return join(laneDir, `${laneId}.json`);
}

export function readLaneRecord(laneId) {
  const path = laneRecordPath(laneId);
  if (!existsSync(path)) return null;
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return null;
  }
}

export function writeLaneRecord(laneId, record) {
  mkdirSync(laneDir, { recursive: true });
  writeFileSync(laneRecordPath(laneId), `${JSON.stringify({ ...record, laneId, updatedAt: new Date().toISOString() }, null, 2)}\n`);
}

export function clearLaneRecord(laneId) {
  const path = laneRecordPath(laneId);
  if (existsSync(path)) unlinkSync(path);
}

/** Returns PIDs listening on a TCP port on Windows, empty if unknown/unavailable. */
export function pidsOnPort(port) {
  try {
    const out = execSync(`netstat -ano | findstr :${port}`, { encoding: "utf8" });
    const pids = new Set();
    for (const line of out.split(/\r?\n/)) {
      if (!line.includes("LISTENING")) continue;
      const parts = line.trim().split(/\s+/);
      const pid = Number(parts[parts.length - 1]);
      if (Number.isFinite(pid) && pid > 0) pids.add(pid);
    }
    return [...pids];
  } catch {
    return [];
  }
}

export function killPid(pid) {
  try {
    execSync(`taskkill /PID ${pid} /T /F`, { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}

export function shutdownLane(laneId) {
  const record = readLaneRecord(laneId);
  if (!record) return;
  for (const pid of record.pids ?? []) {
    killPid(pid);
  }
  clearLaneRecord(laneId);
}

/**
 * Ensure the lane owns the port or the port is free.
 * Returns false when a foreign process holds the port.
 */
export function assertLanePortFree(laneId, port) {
  const listeners = pidsOnPort(port);
  if (listeners.length === 0) return true;
  const record = readLaneRecord(laneId);
  const owned = new Set(record?.pids ?? []);
  const foreign = listeners.filter((pid) => !owned.has(pid));
  if (foreign.length === 0) return true;
  return false;
}

export function registerLaneProcess(laneId, port, pid) {
  const existing = readLaneRecord(laneId) ?? { port, pids: [] };
  const pids = new Set(existing.pids ?? []);
  pids.add(pid);
  writeLaneRecord(laneId, { port, pids: [...pids] });
}
