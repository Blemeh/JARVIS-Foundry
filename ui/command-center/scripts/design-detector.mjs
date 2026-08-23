import { readFileSync, readdirSync, statSync } from "node:fs"
import { join, extname } from "node:path"

const root = process.cwd()
const srcDir = join(root, "src")
const exts = new Set([".ts", ".tsx", ".js", ".jsx", ".css", ".html"])
const failures = []

const requiredFiles = [
  "DONOR_ATTRIBUTION.md",
  "src/components/jarvis-core/JarvisCoreStage.tsx",
  "src/components/jarvis-core/JarvisScene.tsx",
  "src/components/jarvis-core/JarvisCoreFallback.tsx",
  "src/lib/jarvisCoreState.ts",
]

const forbiddenPatterns = [
  { id: "gemini", pattern: /gemini/i, allow: ["DONOR_ATTRIBUTION.md"] },
  { id: "donor-api", pattern: /jarvis-backend-cybf\.onrender\.com|harsh-raj00\/my-jarvis\/.*api/i, allow: ["DONOR_ATTRIBUTION.md"] },
  { id: "speech-api", pattern: /SpeechRecognition|webkitSpeechRecognition|speechSynthesis/i, allow: [] },
  { id: "three-donor", pattern: /@react-three\/fiber|from ['"]three['"]/, allow: [] },
]

const walk = (dir) => {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    const st = statSync(full)
    if (st.isDirectory()) walk(full)
    else if (exts.has(extname(full))) scanFile(full)
  }
}

const scanFile = (file) => {
  const rel = file.replace(/\\/g, "/").slice(root.replace(/\\/g, "/").length + 1)
  const text = readFileSync(file, "utf8")
  for (const rule of forbiddenPatterns) {
    if (rule.allow.some((a) => rel.endsWith(a))) continue
    if (rule.pattern.test(text)) {
      failures.push({ kind: rule.id, file: rel })
    }
  }
}

for (const rel of requiredFiles) {
  try {
    statSync(join(root, rel))
  } catch {
    failures.push({ kind: "missing-required", file: rel })
  }
}

walk(srcDir)
scanFile(join(root, "index.html"))

const report = {
  scannedAt: new Date().toISOString(),
  failures,
  pass: failures.length === 0,
}

console.log(JSON.stringify(report, null, 2))
process.exit(report.pass ? 0 : 1)
