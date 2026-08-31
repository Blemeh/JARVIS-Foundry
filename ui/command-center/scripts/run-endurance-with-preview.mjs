import { spawn } from "node:child_process"
import net from "node:net"
import { join } from "node:path"
import { setTimeout as delay } from "node:timers/promises"
import {
  assertLanePortFree,
  clearLaneRecord,
  registerLaneProcess,
  shutdownLane,
} from "./laneProcess.mjs"

async function allocatePort() {
  const server = net.createServer()
  return await new Promise((resolve, reject) => {
    server.once("error", reject)
    server.listen(0, "127.0.0.1", () => {
      const address = server.address()
      const port = typeof address === "object" && address ? address.port : 0
      server.close((error) => {
        if (error) return reject(error)
        if (!port) return reject(new Error("could not allocate an isolated endurance port"))
        resolve(port)
      })
    })
  })
}

const configuredPort = Number(process.env.ENDURANCE_PORT)
const port = Number.isInteger(configuredPort) && configuredPort > 0
  ? configuredPort
  : await allocatePort()
const laneId = `endurance-${port}`
const baseUrl = `http://127.0.0.1:${port}`
const systemRoot = process.env.SystemRoot || process.env.WINDIR || "C:\\Windows"
const pathValue = [join(systemRoot, "System32"), join(systemRoot), process.env.PATH]
  .filter(Boolean)
  .join(process.platform === "win32" ? ";" : ":")

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

const viteBin = join(process.cwd(), "node_modules", "vite", "bin", "vite.js")
let stopping = false
const shutdown = (code = 1) => {
  if (stopping) return
  stopping = true
  shutdownLane(laneId)
  process.exit(code)
}

process.once("SIGINT", () => shutdown(130))
process.once("SIGTERM", () => shutdown(143))

const preview = spawn(process.execPath, [viteBin, "preview", "--host", "127.0.0.1", "--port", String(port), "--strictPort"], {
  cwd: process.cwd(),
  shell: false,
  stdio: "inherit",
  windowsHide: true,
  env: { ...process.env, PATH: pathValue },
})

if (preview.pid) registerLaneProcess(laneId, port, preview.pid)

preview.on("error", (err) => {
  console.error("Failed to start preview:", err)
  shutdown(1)
})

try {
  await waitForPort()
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error))
  shutdown(1)
}

const endurance = spawn(process.execPath, ["scripts/endurance-runner.mjs"], {
  cwd: process.cwd(),
  shell: false,
  stdio: "inherit",
  windowsHide: true,
  env: { ...process.env, PATH: pathValue, ENDURANCE_BASE: baseUrl },
})

if (endurance.pid) registerLaneProcess(laneId, port, endurance.pid)

preview.on("exit", (code) => {
  if (!stopping) {
    console.error(`Preview exited before endurance completed (code ${code ?? "unknown"})`)
    shutdown(1)
    return
  }
  clearLaneRecord(laneId)
})

endurance.on("error", (error) => {
  console.error("Failed to start endurance runner:", error)
  shutdown(1)
})

endurance.on("exit", (code) => {
  if (stopping) return
  stopping = true
  shutdownLane(laneId)
  process.exit(code ?? 1)
})
