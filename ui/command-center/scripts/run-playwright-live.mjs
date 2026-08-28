import { execFileSync, spawn } from "node:child_process"
import net from "node:net"
import { join, resolve } from "node:path"

async function allocatePort() {
  const server = net.createServer()
  return await new Promise((resolvePort, reject) => {
    server.once("error", reject)
    server.listen(0, "127.0.0.1", () => {
      const address = server.address()
      const port = typeof address === "object" && address ? address.port : 0
      server.close((error) => {
        if (error) return reject(error)
        if (!port) return reject(new Error("could not allocate an isolated port"))
        resolvePort(port)
      })
    })
  })
}

async function waitFor(url, child, label, ready) {
  const deadline = Date.now() + 45_000
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`${label} exited before becoming ready (${child.exitCode})`)
    try {
      const response = await fetch(url)
      if (response.ok) {
        // Drain the readiness response before opening the next connection.
        // Node's undici parser can assert when a large snapshot body is left
        // paused while the child processes are being launched.
        if (!ready) {
          await response.arrayBuffer()
          return
        }
        const payload = await response.json().catch(() => null)
        if (ready(payload)) return
      }
    } catch {
      // The disposable server is still booting.
    }
    await new Promise((resolveDelay) => setTimeout(resolveDelay, 100))
  }
  throw new Error(`${label} did not become ready: ${url}`)
}

const root = resolve(process.cwd(), "..", "..")
const sourceRoot = join(root, "src")
const apiPort = Number(process.env.JARVIS_LANE3_API_PORT) || await allocatePort()
const uiPort = Number(process.env.JARVIS_PLAYWRIGHT_PORT) || await allocatePort()
const systemRoot = process.env.SystemRoot || process.env.WINDIR || "C:\\Windows"
const pathValue = ["C:\\Program Files\\nodejs", "C:\\Program Files\\Git\\cmd", "C:\\Program Files\\Git\\bin", join(systemRoot, "System32"), join(systemRoot), process.env.PATH]
  .filter(Boolean)
  .join(process.platform === "win32" ? ";" : ":")
const evidenceOut = join(root, "evidence", "lane3-product-truth", "autonomy-proof.json")
const captureDir = join(root, "evidence", "lane3-product-truth", "screenshots")
// The desktop's bundled shell can intentionally expose a short PATH. Use the
// Windows launcher when no explicit interpreter was supplied so the proof does
// not accidentally bind to an unavailable Microsoft Store shim.
const python = process.env.JARVIS_PYTHON || (process.platform === "win32" ? "py" : "python3")
const pythonArgs = process.platform === "win32" && python.toLowerCase() === "py" ? ["-3"] : []
const apiScript = join(root, "scripts", "lane3_autonomy_fixture_server.py")
const viteBin = join(process.cwd(), "node_modules", "vite", "bin", "vite.js")
const cli = join(process.cwd(), "node_modules", "@playwright", "test", "cli.js")

const api = spawn(python, [...pythonArgs, apiScript, "--port", String(apiPort), "--evidence-out", evidenceOut], {
  cwd: root,
  stdio: ["ignore", "pipe", "pipe"],
  env: { ...process.env, PATH: pathValue, PYTHONPATH: sourceRoot },
  windowsHide: true,
})
api.stdout.on("data", (chunk) => process.stdout.write(`[lane3-api] ${chunk}`))
api.stderr.on("data", (chunk) => process.stderr.write(`[lane3-api] ${chunk}`))

const apiBase = `http://127.0.0.1:${apiPort}/api`
const vite = spawn(process.execPath, [viteBin, "--host", "127.0.0.1", "--port", String(uiPort), "--strictPort"], {
  cwd: process.cwd(),
  stdio: "ignore",
  env: {
    ...process.env,
    PATH: pathValue,
    VITE_JARVIS_MODE: "live",
    VITE_JARVIS_API_BASE: apiBase,
    VITE_JARVIS_POLL_MS: "60000",
  },
  windowsHide: true,
})

function stop(child) {
  if (!child.pid || child.exitCode !== null) return
  const taskkill = join(systemRoot, "System32", "taskkill.exe")
  try {
    execFileSync(taskkill, ["/PID", String(child.pid), "/T", "/F"], { stdio: "ignore", windowsHide: true })
  } catch {
    // Best-effort cleanup if the child already exited.
  }
}

try {
  await waitFor(
    `${apiBase}/command-center`,
    api,
    "disposable Foundry API",
    (payload) => Boolean(
      payload &&
      Array.isArray(payload.missions) &&
      payload.missions.some((mission) => ["COMPLETE", "COMPLETED"].includes(String(mission?.state).toUpperCase())) &&
      Array.isArray(payload.memory) &&
      payload.memory.length > 0 &&
      payload.briefing_packet,
    ),
  )
  await waitFor(`http://127.0.0.1:${uiPort}/`, vite, "Vite Command Center")
  const child = spawn(process.execPath, [cli, "test", ...process.argv.slice(2)], {
    cwd: process.cwd(),
    stdio: "inherit",
    env: {
      ...process.env,
      PATH: pathValue,
      VITE_JARVIS_MODE: "live",
      VITE_JARVIS_API_BASE: apiBase,
      VITE_JARVIS_POLL_MS: "60000",
      JARVIS_PLAYWRIGHT_PORT: String(uiPort),
      JARVIS_PLAYWRIGHT_USE_WEBSERVER: "0",
      JARVIS_CAPTURE_DIR: captureDir,
    },
    windowsHide: true,
  })
  child.on("error", (error) => {
    stop(vite)
    stop(api)
    console.error(`Playwright launcher failed: ${error.message}`)
    process.exitCode = 1
  })
  child.on("exit", (code, signal) => {
    stop(vite)
    stop(api)
    if (signal) process.kill(process.pid, signal)
    else process.exitCode = code ?? 1
  })
} catch (error) {
  stop(vite)
  stop(api)
  console.error(error instanceof Error ? error.message : String(error))
  process.exitCode = 1
}
