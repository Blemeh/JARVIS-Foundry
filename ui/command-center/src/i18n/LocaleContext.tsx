import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react"
import {
  CATALOGS,
  EN,
  LOCALE_STORAGE_KEY,
  NOTIFY_STORAGE_KEY,
  readStoredLocale,
  readStoredRouting,
  type NotificationRouting,
  type UiLocale,
} from "./catalog"

export type MessageKey = keyof typeof EN

interface LocaleState {
  locale: UiLocale
  routing: NotificationRouting
  t: (key: MessageKey) => string
  setLocale: (locale: UiLocale) => void
  setRouting: (routing: NotificationRouting) => void
}

const LocaleContext = createContext<LocaleState | null>(null)

function persistLocale(locale: UiLocale) {
  try {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, locale)
  } catch {
    /* ignore quota */
  }
}

function persistRouting(routing: NotificationRouting) {
  try {
    window.localStorage.setItem(NOTIFY_STORAGE_KEY, routing)
  } catch {
    /* ignore quota */
  }
}

export function LocaleProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<UiLocale>(() => (typeof window === "undefined" ? "en" : readStoredLocale()))
  const [routing, setRoutingState] = useState<NotificationRouting>(() => (
    typeof window === "undefined" ? "in-app-toast" : readStoredRouting()
  ))
  const setLocale = useCallback((next: UiLocale) => {
    setLocaleState(next)
    persistLocale(next)
    document.documentElement.lang = next === "sq" ? "sq" : "en"
  }, [])
  const setRouting = useCallback((next: NotificationRouting) => {
    setRoutingState(next)
    persistRouting(next)
  }, [])
  const value = useMemo<LocaleState>(() => ({
    locale,
    routing,
    t: (key) => CATALOGS[locale][key] ?? EN[key],
    setLocale,
    setRouting,
  }), [locale, routing, setLocale, setRouting])
  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>
}

export function useLocale(): LocaleState {
  const value = useContext(LocaleContext)
  if (value) return value
  return {
    locale: "en",
    routing: "in-app-toast",
    t: (key) => EN[key],
    setLocale: () => undefined,
    setRouting: () => undefined,
  }
}

export function LocaleToggle({ compact = false }: { compact?: boolean }) {
  const { locale, setLocale, t } = useLocale()
  return (
    <div className="locale-toggle" role="group" aria-label={t("settings.locale")}>
      <button
        type="button"
        className={locale === "en" ? "locale-toggle-btn is-active" : "locale-toggle-btn"}
        onClick={() => setLocale("en")}
        aria-pressed={locale === "en"}
      >
        {compact ? "EN" : t("settings.localeEn")}
      </button>
      <button
        type="button"
        className={locale === "sq" ? "locale-toggle-btn is-active" : "locale-toggle-btn"}
        onClick={() => setLocale("sq")}
        aria-pressed={locale === "sq"}
      >
        {compact ? "SQ" : t("settings.localeSq")}
      </button>
    </div>
  )
}
