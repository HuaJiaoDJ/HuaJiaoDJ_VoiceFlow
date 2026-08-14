# HuaJiaoDJ_VoiceFlow

Local push-to-talk dictation for macOS — speak anywhere, get clean text.

```
hold a hotkey → mic capture → HuaJiaoDJ VoiceFlow → LLM cleanup → paste into any app
```

Speech never leaves your machine: transcription runs locally with
`faster-whisper`. The cleanup pass — removing "um", adding punctuation,
handling "actually, make that Thursday" — runs on a local model through Ollama
by default, or on a cloud API if you'd rather.

**macOS only.** See [Platform support](#platform-support) below.

---

## Requirements

| | |
|---|---|
| **macOS** | 13 (Ventura) or later |
| **Mac** | Apple Silicon or Intel |
| **Python** | 3.11–3.13 (**not 3.14** — no `ctranslate2` wheels yet) |
| **Disk** | ~2 GB (Python packages + the Whisper model) |
| **Ollama** | optional but recommended, for the cleanup pass |

Install Python and Ollama if you don't have them:

```bash
brew install python@3.13
```

```bash
brew install --cask ollama
```

---

## Install

From the project folder:

```bash
./install.sh
```

That script does everything: creates the virtual environment, installs
dependencies, builds both app bundles with the correct paths for wherever you
put this folder, ad-hoc code-signs them, registers the login agent, and starts
the app. It's safe to re-run at any time.

Then pull a cleanup model:

```bash
ollama pull qwen3:8b
```

### Grant permissions (one-time)

HuaJiaoDJ_VoiceFlow asks for two permissions on first launch. Approve both:

- **Microphone** — to hear you.
- **Accessibility** — to watch for the hotkey and paste into other apps.

If no prompt appears, add it by hand: **System Settings → Privacy & Security →
Accessibility → `+`** and select **HuaJiaoDJ_VoiceFlow.app** from this folder.

> **Why an .app bundle?** macOS attaches these grants to a signed application
> with a stable identity. A bare Python script launched by `launchd` cannot
> hold them — it silently ends up with dead hotkeys. `install.sh` builds and
> signs the bundle so the grants stick across reboots.

---

## Using it

**Hold ⌃⌥Z, speak, release.** Cleaned-up text pastes wherever your cursor is.
A small waveform appears while it listens.

| Action | How |
|---|---|
| Dictate | Hold **⌃⌥Z**, speak, release |
| Rewrite selected text | Select text, hold **Right Control**, say e.g. *"make this concise"* |
| Press Return after pasting | End with *"...press enter"* |
| Snippets | Say a saved trigger phrase alone (e.g. *"my email"*) |

Short utterances (≤4 words) skip the cleanup model and paste almost instantly.

### Settings

Open **HuaJiaoDJ_VoiceFlow Settings.app** (in this folder — drag it to your Dock or
Applications), or click the **waveform icon** in the menu bar, or run:

```bash
./run.sh --settings
```

From there you can set:

- **Backend and model** — Ollama, LM Studio, Anthropic, OpenAI, Groq,
  OpenRouter, or any OpenAI-compatible endpoint. The model list loads live from
  whichever backend you pick.
- **API key** — stored in the macOS Keychain, never written to disk in plain
  text. Type it, click **Store**, and the field clears.
- **Hotkeys** — click **Record** and press the combination you want.
- **Dictation mode**
  - *Hold to talk* — record while the keys are held (default).
  - *Toggle* — press once to start, press again to stop. Good for long dictation.
- **Feedback** — sounds, waveform overlay, and whether to run cleanup at all.

Changes save to `config.json` and apply on your next keypress — no restart.

---

## Everyday commands

```bash
open -a "HuaJiaoDJ_VoiceFlow.app"        # start (or restart) dictation
```

```bash
pkill -f voiceflow             # stop until next login
```

```bash
tail -f /tmp/voiceflow.log     # watch what it's doing
```

Turn off auto-start at login:

```bash
launchctl bootout gui/$(id -u)/com.huajiaodj.voiceflow.dictation
```

Terminal alternatives to the Settings window:

```bash
./run.sh --show-config         # active backend, model, key status
./run.sh --list-models         # models the current backend can serve
./run.sh --set-model qwen3.6:latest
./run.sh --set-backend anthropic
./run.sh --set-key anthropic   # prompts; hidden input; goes to Keychain
```

---

## Troubleshooting

**Hotkey does nothing.** Accessibility permission isn't active. Check
`/tmp/voiceflow.log` for `accessibility: granted`. If it says `NOT granted`,
add **HuaJiaoDJ_VoiceFlow.app** in System Settings → Privacy & Security → Accessibility,
then restart it. After *moving* the project folder, re-run `./install.sh` —
the signature and paths change, and macOS treats it as a different app.

**Text pastes into the wrong place.** Click into the target field first;
HuaJiaoDJ_VoiceFlow pastes wherever focus is at the moment you release the key.

**No menu bar icon.** A full menu bar hides overflow items (notably behind the
notch). Use **HuaJiaoDJ_VoiceFlow Settings.app** instead — it doesn't depend on the menu bar.

**Transcripts paste raw, uncleaned.** The cleanup backend is unreachable. Start
Ollama, or check `./run.sh --show-config` for a missing API key. Dictation
deliberately degrades to raw transcripts rather than failing.

**Frozen after many dictations.** Grab a stack dump before restarting — it says
exactly where it's stuck:

```bash
kill -USR1 $(pgrep -f voiceflow)
```

**`ctranslate2` fails to install.** You're on Python 3.14. Install 3.13
(`brew install python@3.13`), delete `.venv`, and re-run `./install.sh`.

---

## Configuration files

Both live in this folder and hot-reload while running.

- **`config.json`** — hotkeys, dictation mode, Whisper model size
  (`tiny`/`base`/`small`/`medium`), language pin, LLM backend and model,
  app→style map, overlay position, sounds.
- **`dictionary.json`** —
  - `vocabulary` — terms fed to Whisper as hints *and* to the cleanup model for
    correct spelling (names, jargon, acronyms)
  - `replacements` — deterministic fixes applied after transcription
  - `snippets` — trigger phrase → expanded text

Changing the Whisper model size is the one setting that needs a restart.

---

## Privacy

Audio and transcription stay on your Mac. With a local backend (Ollama or LM
Studio) nothing touches the network at all. **Selecting a cloud backend sends
your transcripts to that provider** — the settings window shows which backend
is active at all times.

The microphone stream stays open while HuaJiaoDJ_VoiceFlow runs, so the orange mic
indicator is always lit. Audio is only *retained* while you hold the hotkey.

---

## Platform support

macOS only, as built. Roughly half the code is already portable — the whole
core pipeline (`stt.py`, `audio.py`, `cleanup.py`, `providers.py`, `rules.py`,
`config.py`) is plain Python on cross-platform libraries.

The macOS-specific parts are the integration layer: clipboard and paste
(`pbcopy`/⌘V), the Quartz keyboard tap, foreground-app detection via
NSWorkspace, the Cocoa overlay and settings window, Keychain storage, TCC
permissions, and the `.app`/LaunchAgent packaging. A Windows or Linux port
means reimplementing that layer — a real project, not an afternoon.

---

## How it works

```
voiceflow/
  main.py         daemon: hotkey listener, edge detection, pipeline orchestration
  audio.py        persistent 16 kHz mono mic stream + live level metering
  stt.py          faster-whisper wrapper (greedy decode, VAD trim, hotwords)
  cleanup.py      LLM cleanup + selection-rewrite, structured JSON output
  providers.py    backend registry, Keychain keys, model discovery
  rules.py        snippets, replacements, trailing "press enter"
  context.py      frontmost app → writing style
  paste.py        clipboard save → ⌘V → restore
  overlay.py      floating waveform HUD (non-activating, click-through)
  ui.py           menu bar item + settings window
  permissions.py  Microphone and Accessibility requests
  settings.py     command-line settings interface
```

Two details that took real work and are easy to regress:

**The overlay must never take focus.** It's a non-activating `NSPanel` ordered
front without activation. If it became key, the synthetic ⌘V would paste into
the overlay's app instead of your document.

**Hotkey callbacks must be instant.** macOS disables an event tap whose
callback runs slow — the failure looks like "works for a while, then stops."
Key events are queued and handled on a worker thread, and HuaJiaoDJ_VoiceFlow ignores the
keystrokes it synthesizes itself so they can't corrupt held-key tracking.

---

## Roadmap

- [x] Push-to-talk dictation with local Whisper
- [x] LLM cleanup with fast-path for short utterances
- [x] Dictionary, replacements, snippets
- [x] Command mode (rewrite a selection)
- [x] Context awareness (active app → tone)
- [x] Waveform overlay, menu bar, settings window
- [x] Swappable local/cloud backends with Keychain-stored keys
- [x] Toggle (latch) dictation mode
- [ ] Dictation history and undo-last-paste
- [ ] Streaming transcription, so long dictations paste instantly
- [ ] whisper.cpp + Core ML backend for Neural Engine acceleration

---

## License

MIT — see [LICENSE](LICENSE).
