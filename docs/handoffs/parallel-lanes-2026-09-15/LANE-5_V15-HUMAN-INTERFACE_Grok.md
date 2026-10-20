# LANE 5 MASTER PROMPT — V15 Human Interface (this phase is NOT started — build the product)

**Paste this entire file as the first message in a new Grok chat.**  
**Preferred model: Grok. Fallback: GPT.**

---

You are a principal JARVIS engineer and product builder. **V15 has not been built.** There is a fixture voice gateway and an English Command Center. That is not a human interface. Tonight you implement **as much of V15 as one strong session can land**: real speech in/out on Windows, Albanian + English, owner GOAL by voice, interruption, notifications that actually reach the owner, and Command Center chrome that is bilingual and usable.

JARVIS is a private owner system. Do not spend the night on HMAC cryptography research, accessibility audits, or test volume. Reuse `OwnerControlService` because that **is** how STOP/GOAL already work — not because you are writing a security product.

Do not touch live foundry DB / Scheduler / release activate. Isolated worktree. Commit on your branch. Do not merge main.

If you finish a slice, **immediately start the next**. The definition of done is capabilities the owner can feel, not coverage %.

## Mission (V15 completion north star — get as close as possible tonight)

The owner should be able to speak or type, in Albanian or English: “handle this”, “stop”, “pause”, “continue”, “never do X”. JARVIS speaks back briefly. A Windows toast appears when JARVIS needs the owner. Command Center shows the same truth in the chosen language. STOP still works if the microphone dies.

## Current truth

- Base + live: `4b2ae28da582b369982661c2a1d2b1792e2f48cb`
- Worktree:

```powershell
New-Item -ItemType Directory -Force -Path C:\temp\JARVIS | Out-Null
cd "C:\Users\b\Documents\ChatGPT\Autonomous AI powered by Hoowen\JARVIS-Foundry"
git fetch origin
git worktree add -b candidate/lane-5-v15-human-interface-20260915 "C:\temp\JARVIS\lane-5-v15-human-interface-20260915" 4b2ae28da582b369982661c2a1d2b1792e2f48cb
cd "C:\temp\JARVIS\lane-5-v15-human-interface-20260915"
```

## What exists (tiny — you will grow it)

**Voice** — `src/jarvis_foundry/voice.py`

- Protocols: `SpeechToTextAdapter`, `TextToSpeechAdapter`
- Fixtures only: `FixtureSTT`, `FixtureTTS`
- `VoiceGateway.handle(audio, nonce, signature, ...)`
- English-only. Patterns: STOP_EVERYTHING, PAUSE, RESUME. **No GOAL. No VETO. No Albanian. No real mic.**
- Language not in `{en, eng, en-us, en-gb}` is rejected. **Delete that restriction** as a product behavior — V15 is bilingual.
- Still call `OwnerControlService.apply` / `resume` / `sign_command` for the actual directive. Do not invent VoiceAuthority.

**Briefings** — already bilingual at data level:

- `src/jarvis_foundry/convergence/briefings_l10n.py` `render_briefing(snapshot, lang)` for `en`/`sq`
- `src/jarvis_foundry/reporting/briefing.py`

**Command Center** — `ui/command-center/`

- Full shell: Overview, Work, Missions, Notifications, Settings, Executive, …
- `Settings.tsx` literally says **“Notification routing NOT YET INTEGRATED”** — that is your job
- Ledger-driven via `src/lib/realClient.ts` + `snapshotMapper.ts`. Do not fake live activity.
- English chrome only. No i18n provider.

**Attention** — `decision_queue.py`, chief_of_staff projections, in-app `Notifications.tsx` / bell. No OS toast.

Stay out of Playwright internals, UIA, GitHub HTTP, self-expansion, `mission/loop.py` rewrites.

You **may** add modules next to `voice.py` (e.g. `voice_windows.py`, `voice_commands.py`), UI locale files, and a toast adapter. You **may** extend `command_center.py` snapshot with `ui_locale` if the UI cannot toggle otherwise — keep it a projection, not a second world model.

## Backlog — this is a build marathon, keep going

### Slice 1 — Real Windows speech I/O

Pick the thinnest stack that works on this PC:

- STT: Windows speech / faster-whisper / Vosk — whatever installs and transcribes a wav
- TTS: Windows SAPI (`pyttsx3` or PowerShell) or edge-tts

Implement adapters honoring the existing Protocols. Keep fixtures for when audio hardware is missing. `VoiceGateway` should accept `language_hint="sq"` and `language_hint="en"`.

Add `scripts/lane5_voice_roundtrip.py`: text → TTS wav → STT → print transcript. If mic is available, optional live record. Hardware missing is not a reason to skip the adapters.

### Slice 2 — Bilingual commands, including GOAL

Replace the tiny `_COMMAND_PATTERNS` list with a real matcher:

English: stop / stop everything / pause / resume / continue / never do that (VETO) / handle this: … / goal: …

Albanian (use natural forms, not machine-ugly): ndalo, ndalo gjithçka, pauzë, vazhdo, mos e bëj kurrë, qëllimi / merre këtë: …

Map to existing command types: `STOP_EVERYTHING`, `PAUSE`, `RESUME`, `VETO`, `GOAL`. Inspect `owner_controls.py` for exact type strings before inventing new ones. If GOAL needs `exact_text` as the remainder of the utterance, do that.

Speak the confirmation in the **same language** as the transcript.

STOP must still work via the existing STOP path if STT throws — e.g. a panic hotkey or “if adapters fail, do not block text STOP.” Don’t couple Supervisor STOP to the mic.

### Slice 3 — Interruption

While a fake “mission running” flag is set (in-memory or a sample event), a new GOAL/PAUSE must record as redirect, not get lost. Wire this to existing owner-control / decision queue if those events already exist. Do not start InterruptBus2.

### Slice 4 — Command Center i18n

- React locale: `en` | `sq`, persist in localStorage, toggle in Settings and header
- Translate chrome: nav, buttons, empty states, STOP banner
- Do **not** translate SHAs, mission ids, verdicts, or ledger fields
- Feed briefing bodies from `render_briefing` when a snapshot is present
- Default English

### Slice 5 — Notifications that leave the browser

- Implement Windows toast for projected attention items (PowerShell BurntToast, win10toast, `winotify`, or a tiny WinRT helper — pick one that works)
- Settings page: replace “NOT YET INTEGRATED” with: in-app only | in-app + Windows toast | off
- Idempotent: same attention id does not spam 20 toasts
- This is local OS, not email, not Gmail, not a new cloud push vendor

### Slice 6 — Command Center polish (product, not cinema)

- STOP banner obvious
- Owner command surface accepts GOAL in EN or SQ
- Empty/error states in the active locale
- No fake pulsing “AI thinking” theater
- Notifications route actually lists attention items + toast status

### Slice 7 — If still going

- Voice replies for “what are you doing?” from Command Center snapshot (briefing l10n)
- Wake-word optional; don’t block the lane on it
- `DEMO.md` with a transcript of an Albanian STOP and an English GOAL through the gateway (fixtures ok if wav roundtrip already proven)

## How to work

- Internal product. Don’t add CAPTCHA, WebAuthn, or new signing schemes.
- Don’t rewrite the whole CC in a new framework.
- Don’t “fix” Gmail.
- Tests: matcher table for EN/SQ commands + toast idempotency. That’s enough. Existing CC tests may assert “NOT YET INTEGRATED” — **update those asserts** when you integrate, don’t keep the lie.

## Final report

```text
VERDICT:
BASE: 4b2ae28da582b369982661c2a1d2b1792e2f48cb
FINAL:
BRANCH: candidate/lane-5-v15-human-interface-20260915
WORKTREE: C:\temp\JARVIS\lane-5-v15-human-interface-20260915
VOICE:
LOCALE:
TOAST:
CC:
DEMO:
LIVE JARVIS: not touched
NEXT:
```
