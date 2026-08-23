import { readFileSync, readdirSync, statSync } from "node:fs"
import { join, extname } from "node:path"

const srcDir = join(process.cwd(), "src")
const exts = new Set([".ts", ".tsx", ".js", ".jsx"])
const findings = []

const secretPatterns = [
  /(?:api[_-]?key|secret|password|token)\s*[:=]\s*['"][^'"]{8,}['"]/i,
  /sk-[a-zA-Z0-9]{20,}/,
  /AKIA[0-9A-Z]{16}/,
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
  const text = readFileSync(file, "utf8")
  const rel = file.replace(/\\/g, "/").split("/src/")[1]

  if (text.includes("dangerouslySetInnerHTML")) {
    findings.push({ kind: "dangerouslySetInnerHTML", file: rel, count: (text.match(/dangerouslySetInnerHTML/g) ?? []).length })
  }

  const blankTargets = [...text.matchAll(/target\s*=\s*["']_blank["']/g)]
  const missingRel = blankTargets.length > 0 && !/rel\s*=\s*["'][^"']*noopener/i.test(text)
  if (blankTargets.length > 0) {
    findings.push({ kind: "target_blank", file: rel, count: blankTargets.length, missingNoopener: missingRel })
  }

  for (const pattern of secretPatterns) {
    if (pattern.test(text)) findings.push({ kind: "possible_secret", file: rel, pattern: pattern.source })
  }
}

walk(srcDir)

const report = {
  scannedAt: new Date().toISOString(),
  dangerouslySetInnerHTML: findings.filter((f) => f.kind === "dangerouslySetInnerHTML"),
  targetBlank: findings.filter((f) => f.kind === "target_blank"),
  possibleSecrets: findings.filter((f) => f.kind === "possible_secret"),
  pass: findings.length === 0,
}

console.log(JSON.stringify(report, null, 2))
process.exit(report.pass ? 0 : 1)
