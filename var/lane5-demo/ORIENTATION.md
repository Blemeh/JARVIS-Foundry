# Lane 5 orientation

Inspected on `4b2ae28da582b369982661c2a1d2b1792e2f48cb` in worktree
`C:\temp\JARVIS\max-lane-5-v15-human-interface-20260915`.

## Authority

- Canonical owner commands live in `OwnerControlService` / `_COMMANDS`:
  `GOAL`, `CONSTRAINT`, `ADD_CONSTRAINT`, `VETO_ACTION`, `STOP_MISSION`,
  `STOP_EVERYTHING`, `PAUSE`, `RESUME`, `REVOKE`, `CANCEL_MISSION`,
  `GATE_REEVALUATE`, `REPRIORITIZE`, `REDIRECT`, `RESTART_RECOVERY`,
  `DELEGATE`, `STANDING_ORDER`.
- HMAC is `sign_command(secret, command_type, target, exact_text, nonce)`.
  Nonces are durable. This lane reuses that path. Voice is not an authority.
- STOP sentinel remains independent of STT/TTS/UI.

## Voice (before)

- `src/jarvis_foundry/voice.py`: Protocols + `FixtureSTT` / `FixtureTTS` +
  `VoiceGateway.handle`.
- English-only. Patterns: STOP_EVERYTHING, PAUSE, RESUME. No GOAL/VETO/SQ.
- Existing tests sign `STOP_EVERYTHING` with exact_text = full transcript.

## Briefings

- `convergence/briefings_l10n.py` `render_briefing(snapshot, lang)` for `en`/`sq`.
- `reporting/briefing.py` also has EN/SQ rendering. Voice/UI consume the
  Command Center snapshot, not a second briefing brain.

## UI

- Command Center chrome was English-only. No i18n provider.
- `Settings.tsx` said notification routing was not integrated.
- Owner commands: GOAL via `OwnerCommandSurface`, STOP via `EmergencyStop`,
  RESUME via stop dialog. No PAUSE/VETO on that surface.
- Notifications are in-app from ledger projections. No OS toast.
- localStorage was unused for locale.

## Hardware on this PC

- SAPI voices: Microsoft David / Zira, English only.
- `pywin32` present. `edge-tts` and `winotify` installed for this lane.
- Windows Speech STT transcribed a SAPI WAV of "Stopped." as `Stopped`.
- Albanian TTS is Edge neural (`sq-AL-AnilaNeural`). Albanian STT is not
  available on the Windows recognizer.

## Design chosen

- `MatchedUtterance` is the data shape. Table-driven matcher. Remainder is
  the GOAL/VETO/REDIRECT exact_text.
- Thin adapters next to `voice.py`. No VoiceAuthority.
- LocaleProvider + catalogs. Default English so existing UI tests stay valid.
- Toast router keyed by `queue_id` / attention identity. JSON file dedup,
  not a new database.
