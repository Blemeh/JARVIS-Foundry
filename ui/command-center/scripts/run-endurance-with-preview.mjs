import { spawn } from "node:child_process"
import { setTimeout as delay } from "node:timers/promises"

const port = Number(process.env.ENDURANCE_PORT ?? 4523)
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

const preview = spawn("npx", ["vite", "preview", "--port", String(port), "--strictPort"], {
  cwd: process.cwd(),
  shell: true,
  stdio: "inherit",
})

preview.on("error", (err) => {
  console.error("Failed to start preview:", err)
  process.exit(1)
})

await waitForPort()

const endurance = spawn("node", ["scripts/endurance-runner.mjs"], {
  cwd: process.cwd(),
  shell: true,
  stdio: "inherit",
  env: { ...process.env, ENDURANCE_BASE: baseUrl },
})

const shutdown = () => {
  endurance.kill()
  preview.kill()
}

process.on("SIGINT", shutdown)
process.on("SIGTERM", shutdown)

endurance.on("exit", (code) => {
  preview.kill()
  process.exit(code ?? 1)
})
