import { test, expect, type Page } from "@playwright/test"

const scenarios = [
  "NORMAL_OPERATION",
  "STOP_ACTIVE",
  "MISSION_BLOCKED",
  "WORKER_CRASH",
  "EMPTY_STATE",
] as const

type Scenario = (typeof scenarios)[number]

type MatrixRoute = {
  path: string
  slug: string
  assertVisible: (page: Page, scenario: Scenario) => Promise<void>
}

const matrixRoutes: MatrixRoute[] = [
  {
    path: "/",
    slug: "overview",
    assertVisible: async (page, scenario) => {
      await expect(page.getByRole("heading", { name: scenario === "EMPTY_STATE" ? "No active focus is recorded" : "Hybrid Worker Activation" })).toBeVisible()
    },
  },
  {
    path: "/executive",
    slug: "executive",
    assertVisible: async (page) => {
      await expect(page.getByRole("heading", { name: "Autonomous Executive", exact: true })).toBeVisible()
    },
  },
  {
    path: "/missions",
    slug: "missions",
    assertVisible: async (page, scenario) => {
      await expect(page.getByRole("heading", { name: "Mission Vectors" })).toBeVisible()
      if (scenario === "EMPTY_STATE") {
        await expect(page.getByText("No missions")).toBeVisible()
      }
    },
  },
  {
    path: "/missions/M-HYB-014",
    slug: "mission-detail",
    assertVisible: async (page, scenario) => {
      if (scenario === "EMPTY_STATE") {
        await expect(page.getByText("Mission not found")).toBeVisible()
      } else {
        await expect(page.getByRole("heading", { name: "Hybrid Worker Activation" })).toBeVisible()
      }
    },
  },
  {
    path: "/agents",
    slug: "agents",
    assertVisible: async (page) => {
      await expect(page.getByRole("heading", { name: "Specialist Fleet" })).toBeVisible()
    },
  },
  {
    path: "/agents/WRK-HERMES",
    slug: "agent-detail",
    assertVisible: async (page, scenario) => {
      if (scenario === "EMPTY_STATE") {
        await expect(page.getByText("Agent not found")).toBeVisible()
      } else {
        await expect(page.getByRole("heading", { name: "Hermes Orchestrator" })).toBeVisible()
      }
    },
  },
  {
    path: "/memory",
    slug: "memory",
    assertVisible: async (page) => {
      await expect(page.getByRole("heading", { name: "Knowledge Graph" })).toBeVisible()
    },
  },
  {
    path: "/memory/n-verdict",
    slug: "memory-detail",
    assertVisible: async (page) => {
      await expect(page.getByRole("heading", { name: "Knowledge Graph" })).toBeVisible()
    },
  },
  {
    path: "/evidence",
    slug: "evidence",
    assertVisible: async (page, scenario) => {
      await expect(page.getByRole("heading", { name: "Evidence Ledger" })).toBeVisible()
      if (scenario === "EMPTY_STATE") {
        await expect(page.getByText("No evidence matches")).toBeVisible()
      }
    },
  },
  {
    path: "/evidence/EV-1131-007",
    slug: "evidence-detail",
    assertVisible: async (page, scenario) => {
      if (scenario === "EMPTY_STATE") {
        await expect(page.getByText("Evidence not found")).toBeVisible()
      } else {
        await expect(page.getByRole("heading", { name: "Ledger entry" })).toBeVisible()
      }
    },
  },
  {
    path: "/timeline",
    slug: "timeline",
    assertVisible: async (page, scenario) => {
      await expect(page.getByRole("heading", { name: "Evidence Spine" })).toBeVisible()
      if (scenario === "EMPTY_STATE") {
        await expect(page.getByText("Empty timeline")).toBeVisible()
      }
    },
  },
  {
    path: "/systems",
    slug: "systems",
    assertVisible: async (page) => {
      await expect(page.getByRole("heading", { name: "Telemetry" })).toBeVisible()
    },
  },
  {
    path: "/systems/SYS-STOP",
    slug: "system-detail",
    assertVisible: async (page, scenario) => {
      if (scenario === "EMPTY_STATE") {
        await expect(page.getByText("System not found")).toBeVisible()
      } else {
        await expect(page.getByRole("heading", { name: "STOP subsystem" })).toBeVisible()
      }
    },
  },
  {
    path: "/notifications",
    slug: "notifications",
    assertVisible: async (page) => {
      await expect(page.getByRole("heading", { name: "Notification / Exception Center" })).toBeVisible()
    },
  },
  {
    path: "/missing-route",
    slug: "not-found",
    assertVisible: async (page) => {
      await expect(page.getByText("Route not found")).toBeVisible()
    },
  },
]

const forbiddenTelemetry = ["99.8", "$0.38", "Integrity node online"]

async function attachConsoleGuard(page: Page, label: string) {
  const errors: string[] = []
  page.on("pageerror", (err) => errors.push(`pageerror@${label}: ${err.message}`))
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`console.error@${label}: ${msg.text()}`)
  })
  return errors
}

for (const route of matrixRoutes) {
  for (const scenario of scenarios) {
    test(`matrix ${route.slug} @ ${scenario}`, async ({ page }) => {
      const label = `${route.slug}/${scenario}`
      const errors = await attachConsoleGuard(page, label)
      await page.goto(`${route.path}?scenario=${scenario}`)
      await expect(page.locator("main#main-content")).toBeVisible()
      await route.assertVisible(page, scenario)

      const body = await page.locator("body").innerText()
      for (const forbidden of forbiddenTelemetry) {
        expect(body, `${label} must not show fabricated telemetry ${forbidden}`).not.toContain(forbidden)
      }

      if (route.slug !== "not-found") {
        await expect(page.locator(".data-source-pill")).toContainText("SIMULATED")
      }

      expect(errors, `${label} console/page errors`).toEqual([])
    })
  }
}

test("matrix rapid route changes do not leak stale headings", async ({ page }) => {
  const errors = await attachConsoleGuard(page, "rapid-nav")
  const sequence = ["/missions", "/agents", "/evidence", "/systems", "/"]
  for (const path of sequence) {
    await page.goto(path)
    await expect(page.locator("main#main-content")).toBeVisible()
  }
  await page.goto("/missions")
  await expect(page.getByRole("heading", { name: "Mission Vectors" })).toBeVisible()
  expect(errors).toEqual([])
})

test("matrix refresh on systems keeps projection label", async ({ page }) => {
  const errors = await attachConsoleGuard(page, "refresh-systems")
  await page.goto("/systems")
  await expect(page.getByText(/projection v/i)).toBeVisible()
  await page.reload()
  await expect(page.getByRole("heading", { name: "Telemetry" })).toBeVisible()
  await expect(page.getByText(/projection v/i)).toBeVisible()
  expect(errors).toEqual([])
})

const extendedScenarios = ["STREAM_DISCONNECTED", "PROVIDER_DEGRADED"] as const

for (const scenario of extendedScenarios) {
  test(`matrix extended scenario ${scenario} surfaces honest state`, async ({ page }) => {
    const errors = await attachConsoleGuard(page, scenario)
    await page.goto(`/?scenario=${scenario}`)
    await expect(page.getByRole("heading", { name: "Hybrid Worker Activation" })).toBeVisible()
    await expect(page.locator(".data-source-pill")).toContainText("SIMULATED")
    if (scenario === "STREAM_DISCONNECTED") {
      await expect(page.locator(".topbar-disconnect").getByText("Updates paused", { exact: true })).toBeVisible()
    }
    if (scenario === "PROVIDER_DEGRADED") {
      await page.goto(`/systems?scenario=${scenario}`)
      await expect(page.getByText("DEGRADED", { exact: true }).first()).toBeVisible()
    }
    expect(errors).toEqual([])
  })
}
