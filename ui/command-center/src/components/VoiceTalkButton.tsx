import { useCallback, useEffect, useRef, useState } from "react"
import { Mic, MicOff } from "lucide-react"
import { useCommandCenter } from "../context/CommandCenterContext"
import { useLocale } from "../i18n/LocaleContext"
import type { VoiceTextResult } from "../lib/contracts"

type SpeechRec = {
  lang: string
  interimResults: boolean
  continuous: boolean
  onresult: ((event: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null
  onerror: ((event: { error: string }) => void) | null
  onend: (() => void) | null
  start: () => void
  stop: () => void
  abort: () => void
}

type SpeechRecCtor = new () => SpeechRec

function recognitionCtor(): SpeechRecCtor | null {
  const speechWindow = window as Window & {
    SpeechRecognition?: SpeechRecCtor
    webkitSpeechRecognition?: SpeechRecCtor
  }
  return speechWindow.SpeechRecognition ?? speechWindow.webkitSpeechRecognition ?? null
}

function speakConfirmation(text: string, locale: "en" | "sq"): boolean {
  const synth = window.speechSynthesis
  if (!synth || !text.trim()) return false
  synth.cancel()
  const utterance = new SpeechSynthesisUtterance(text)
  utterance.lang = locale === "sq" ? "sq-AL" : "en-US"
  synth.speak(utterance)
  return true
}

function interruptSpeech(): boolean {
  const synth = window.speechSynthesis
  if (!synth) return false
  if (synth.speaking || synth.pending) {
    synth.cancel()
    return true
  }
  return false
}

export function VoiceTalkButton({
  onResult,
}: {
  onResult?: (result: VoiceTextResult) => void
}) {
  const { client, sourceMode } = useCommandCenter()
  const { t, locale } = useLocale()
  const [listening, setListening] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const recognitionRef = useRef<SpeechRec | null>(null)
  const Ctor = typeof window === "undefined" ? null : recognitionCtor()
  const allowed = sourceMode === "LIVE" || sourceMode === "SIMULATED"

  const stopListening = useCallback(() => {
    recognitionRef.current?.stop()
    recognitionRef.current = null
    setListening(false)
  }, [])

  const submitTranscript = useCallback(async (transcript: string) => {
    const text = transcript.trim()
    if (!text) {
      setError(t("voice.noSpeech"))
      return
    }
    setBusy(true)
    setError(null)
    try {
      const result = client.submitVoiceText
        ? await client.submitVoiceText(text, locale)
        : { status: "UNAVAILABLE", transcript: text, responseText: t("voice.unavailable") }
      onResult?.(result)
      setMessage(result.responseText || result.error || text)
      const spoken = speakConfirmation(result.responseText || "", locale === "sq" ? "sq" : "en")
      if (!spoken && result.responseText) setError(t("voice.spokenUnavailable"))
    } catch (err) {
      setError(err instanceof Error ? err.message : t("voice.micError"))
    } finally {
      setBusy(false)
    }
  }, [client, locale, onResult, t])

  const startListening = useCallback(() => {
    if (!Ctor || !allowed || busy || listening) return
    setError(null)
    if (interruptSpeech()) setMessage(t("voice.interrupt"))
    const recognition = new Ctor()
    recognition.lang = locale === "sq" ? "sq-AL" : "en-US"
    recognition.interimResults = false
    recognition.continuous = false
    recognition.onresult = (event) => {
      const first = event.results[0]?.[0]?.transcript ?? ""
      stopListening()
      void submitTranscript(first)
    }
    recognition.onerror = (event) => {
      stopListening()
      if (event.error === "not-allowed" || event.error === "service-not-allowed") {
        setError(t("voice.micDenied"))
      } else if (event.error === "no-speech") {
        setError(t("voice.noSpeech"))
      } else if (event.error === "audio-capture") {
        setError(t("voice.micError"))
      } else {
        setError(t("voice.micError"))
      }
    }
    recognition.onend = () => {
      recognitionRef.current = null
      setListening(false)
    }
    recognitionRef.current = recognition
    try {
      recognition.start()
      setListening(true)
    } catch {
      setError(t("voice.micError"))
      recognitionRef.current = null
    }
  }, [Ctor, allowed, busy, listening, locale, stopListening, submitTranscript, t])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!(event.ctrlKey && event.shiftKey && event.code === "Space")) return
      event.preventDefault()
      if (listening) stopListening()
      else startListening()
    }
    window.addEventListener("keydown", onKey, true)
    return () => window.removeEventListener("keydown", onKey, true)
  }, [listening, startListening, stopListening])

  useEffect(() => () => {
    recognitionRef.current?.abort()
    recognitionRef.current = null
  }, [])

  const disabled = !allowed || busy || (!Ctor && !listening)
  return (
    <div className="voice-talk">
      <button
        type="button"
        className={listening ? "owner-command-talk is-listening" : "owner-command-talk"}
        aria-pressed={listening}
        aria-label={listening ? t("voice.listening") : t("voice.listen")}
        title={t("voice.holdHint")}
        disabled={disabled}
        onClick={() => (listening ? stopListening() : startListening())}
      >
        {Ctor ? <Mic size={16} aria-hidden="true" /> : <MicOff size={16} aria-hidden="true" />}
      </button>
      <p className="voice-talk-hint">{listening ? t("voice.listening") : t("voice.holdHint")}</p>
      {!Ctor ? <p className="voice-talk-error" role="status">{t("voice.unavailable")}</p> : null}
      {error ? <p className="voice-talk-error" role="alert">{error}</p> : null}
      {message ? <p className="voice-talk-status" role="status">{message}</p> : null}
    </div>
  )
}
