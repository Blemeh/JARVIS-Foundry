# Lane 5 demos

Isolated only. These scripts do not touch live Foundry, Scheduler, or release pointers.

```powershell
cd C:\temp\JARVIS\max-lane-5-v15-human-interface-20260915
python var/lane5-demo/voice_roundtrip.py
python var/lane5-demo/owner_voice_demo.py
python var/lane5-demo/toast_demo.py
python var/lane5-demo/cc_voice_text.py
```

## Speech

- English: SAPI TTS WAV -> Windows Speech STT.
- Albanian: Edge TTS MP3. Windows Speech has no Albanian recognizer on this PC.

## Owner command

- Generated English GOAL audio, isolated DB, `OwnerControlService` via `VoiceGateway`.
- Albanian STOP/RESUME through the same gateway with same-language confirmation.
- STOP file is written only under `var/lane5-demo/generated/isolated-owner/`.

## Notification

- Real Windows toast through `winotify`.
- Duplicate `queue_id` is DEDUPED.

## UI

```powershell
cd ui/command-center
npm test -- src/i18n/__tests__/locale.test.tsx
npm run build
```

Locale toggle is in the header and Settings. Default English. SQ persists in localStorage.
