import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { join } from "node:path";

const baseUrl = process.env.REMASTER_BASE ?? "http://localhost:4523";
const outDir = join(process.cwd(), "remaster-shots");
mkdirSync(outDir, { recursive: true });

const routes = [
  { path: "/?scenario=NORMAL_OPERATION", slug: "jarvis" },
  { path: "/work?scenario=NORMAL_OPERATION", slug: "work" },
  { path: "/world?scenario=NORMAL_OPERATION", slug: "world" },
  { path: "/capabilities?scenario=NORMAL_OPERATION", slug: "capabilities" },
  { path: "/you?scenario=NORMAL_OPERATION", slug: "you" },
  { path: "/activity?scenario=NORMAL_OPERATION", slug: "activity" },
  { path: "/evidence?scenario=NORMAL_OPERATION", slug: "evidence" },
];
const viewports = [
  { name: "1920", width: 1920, height: 1080 },
  { name: "laptop", width: 1366, height: 864 },
];

const browser = await chromium.launch({ headless: true });
let fails = 0;
for (const vp of viewports) {
  const ctx = await browser.newContext({ viewport: { width: vp.width, height: vp.height } });
  const page = await ctx.newPage();
  for (const r of routes) {
    const url = `${baseUrl}${r.path}`;
    await page.goto(url, { waitUntil: "networkidle" });
    await page.waitForTimeout(600);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
    if (overflow) { fails++; console.log(`OVERFLOW ${r.slug} @ ${vp.name}`); }
    await page.screenshot({ path: join(outDir, `${r.slug}-${vp.name}.png`), fullPage: false });
    console.log(`shot ${r.slug}-${vp.name} overflow=${overflow}`);
  }
  await ctx.close();
}
await browser.close();
console.log(fails === 0 ? "ALL VIEWPORTS OK" : `${fails} OVERFLOWS`);
