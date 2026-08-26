import { execFileSync, spawn } from "node:child_process"
import net from "node:net"
import { join } from "node:path"

async function allocatePort() {
  const server = net.createServer()
  return await new Promise((resolve, reject) => {
    server.once("error", reject)
    server.listen(0, "127.0.0.1", () => {
      const address = server.address()
      const port = typeof address === "object" && address ? address.port : 0
      server.close((error) => {
        if (error) return reject(error)
        if (!port) return reject(new Error("could not allocate an isolated Playwright port"))
        resolve(port)
      })
    })
  })
}

const port = Number(process.env.JARVIS_PLAYWRIGHT_PORT) || await allocatePort()
const cli = join(process.cwd(), "node_modules", "@playwright", "test", "cli.js")
const systemRoot = process.env.SystemRoot || process.env.WINDIR || "C:\\Windows"
const pathValue = [join(systemRoot, "System32"), join(systemRoot), process.env.PATH]
  .filter(Boolean)
  .join(process.platform === "win32" ? ";" : ":")
const viteBin = join(process.cwd(), "node_modules", "vite", "bin", "vite.js")
const vite = spawn(process.execPath, [viteBin, "--host", "127.0.0.1", "--port", String(port), "--strictPort"], {
  cwd: process.cwd(),
  stdio: "ignore",
  env: { ...process.env, PATH: pathValue, VITE_JARVIS_MODE: process.env.VITE_JARVIS_MODE ?? "simulated" },
  windowsHide: true,
})

async function waitForServer() {
  const deadline = Date.now() + 30_000
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/`)
      if (response.ok) return
    } catch {
      // Vite is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
  throw new Error(`Playwright server did not become ready on port ${port}`)
}

function stopVite() {
  if (!vite.pid || vite.exitCode !== null) return
  const taskkill = join(systemRoot, "System32", "taskkill.exe")
  try {
    execFileSync(taskkill, ["/PID", String(vite.pid), "/T", "/F"], { stdio: "ignore", windowsHide: true })
  } catch {
    // The server may already have exited; cleanup remains best-effort.
  }
}

try {
  await waitForServer()
} catch (error) {
  stopVite()
  console.error(error instanceof Error ? error.message : String(error))
  process.exit(1)
}

const child = spawn(process.execPath, [cli, "test", ...process.argv.slice(2)], {
  cwd: process.cwd(),
  stdio: "inherit",
  env: {
    ...process.env,
    PATH: pathValue,
    JARVIS_PLAYWRIGHT_PORT: String(port),
    JARVIS_PLAYWRIGHT_USE_WEBSERVER: "0",
  },
  windowsHide: true,
})

child.on("error", (error) => {
  stopVite()
  console.error(`Playwright launcher failed: ${error.message}`)
  process.exitCode = 1
})
child.on("exit", (code, signal) => {
  stopVite()
  if (signal) {
    process.kill(process.pid, signal)
  } else {
    process.exitCode = code ?? 1
  }
})
