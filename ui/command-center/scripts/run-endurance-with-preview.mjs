import { spawn } from "node:child_process"
import { setTimeout as delay } from "node:timers/promises"
import {
  assertLanePortFree,
  clearLaneRecord,
  registerLaneProcess,
  shutdownLane,
} from "./laneProcess.mjs"

const port = Number(process.env.ENDURANCE_PORT ?? 4523)
const laneId = `endurance-${port}`
const baseUrl = `http://localhost:${port}`

async function waitForPort(timeoutMs = 60_000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const res = await fetch(baseUrl)
      if (res.ok) return
    } catch {
      // retry
    }
    await delay(500)
  }
  throw new Error(`Preview not ready on ${baseUrl}`)
}

if (!assertLanePortFree(laneId, port)) {
  console.error(
    `Endurance blocked: port ${port} is held by a foreign process (historical 4523/4524 collision). Stop the other lane or choose ENDURANCE_PORT.`,
  )
  process.exit(2)
}

const preview = spawn("npx", ["vite", "preview", "--port", String(port), "--strictPort"], {
  cwd: process.cwd(),
  shell: true,
  stdio: "inherit",
})

if (preview.pid) registerLaneProcess(laneId, port, preview.pid)

preview.on("error", (err) => {
  console.error("Failed to start preview:", err)
  shutdownLane(laneId)
  process.exit(1)
})

await waitForPort()

const endurance = spawn("node", ["scripts/endurance-runner.mjs"], {
  cwd: process.cwd(),
  shell: true,
  stdio: "inherit",
  env: { ...process.env, ENDURANCE_BASE: baseUrl },
})

if (endurance.pid) registerLaneProcess(laneId, port, endurance.pid)

const shutdown = (code = 1) => {
  shutdownLane(laneId)
  process.exit(code)
}

process.on("SIGINT", () => shutdown(130))
process.on("SIGTERM", () => shutdown(143))

preview.on("exit", () => clearLaneRecord(laneId))

endurance.on("exit", (code) => {
  shutdownLane(laneId)
  process.exit(code ?? 1)
})
