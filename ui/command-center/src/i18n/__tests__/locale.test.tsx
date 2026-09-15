import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"
import { LocaleProvider, LocaleToggle, useLocale } from "../LocaleContext"
import { LOCALE_STORAGE_KEY, NOTIFY_STORAGE_KEY } from "../catalog"

function Probe() {
  const { locale, routing } = useLocale()
  return <p>{`locale:${locale} routing:${routing}`}</p>
}

describe("Command Center locale", () => {
  it("defaults to English and persists the SQ toggle", async () => {
    window.localStorage.removeItem(LOCALE_STORAGE_KEY)
    window.localStorage.removeItem(NOTIFY_STORAGE_KEY)
    const user = userEvent.setup()
    render(
      <LocaleProvider>
        <LocaleToggle />
        <Probe />
      </LocaleProvider>,
    )
    expect(screen.getByText("locale:en routing:in-app-toast")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Shqip" }))
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("sq")
    expect(screen.getByText("locale:sq routing:in-app-toast")).toBeInTheDocument()
  })
})
