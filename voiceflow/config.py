import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
DICTIONARY_PATH = os.path.join(BASE_DIR, "dictionary.json")

DEFAULT_CONFIG = {
    "hotkeys": {
        # Single key ("ctrl_r") or a combo ("cmd+shift+v"). Use modifier-only
        # keys for push-to-talk: they do nothing on their own and there's no
        # key-repeat. Generic "cmd"/"shift"/"ctrl"/"alt" match either side;
        # "cmd_r"/"ctrl_r"/etc. match a specific side.
        "dictate": "cmd+shift",
        "command": "ctrl_r",
        # Turns hands-free auto-transcribe on and off.
        "auto": "ctrl+alt+a",
        # "hold"   = push-to-talk: record while the combo is held.
        # "toggle" = press once to start, press again to stop.
        "mode": "hold",
    },
    "stt": {
        "model": "small",
        "compute_type": "int8",
        "language": None,
        "beam_size": 1,
    },
    "llm": {
        # backend: ollama | lmstudio | anthropic | openai | groq | openrouter | custom
        # Switch with:  ./run.sh --set-backend <name>
        # List models:  ./run.sh --list-models
        "backend": "ollama",
        "model": "qwen3:8b",
        "enabled": True,
        # null = use the provider's default endpoint (required for "custom").
        "base_url": None,
        # API keys resolve from: this field -> $api_key_env -> the provider's
        # default env var -> macOS Keychain. Prefer the Keychain:
        #   ./run.sh --set-key anthropic
        "api_key": None,
        "api_key_env": None,
        "fast_mode_max_words": 4,
        "timeout": 60,
        "keep_alive": "30m",
    },
    "paste": {
        "restore_clipboard": True,
        "restore_delay": 1.0,
    },
    "audio": {
        "sample_rate": 16000,
        "input_device": None,
        "min_duration": 0.3,
    },
    "context": {
        "app_styles": {
            "Slack": "chat",
            "Messages": "chat",
            "Discord": "chat",
            "WeChat": "chat",
            "Telegram": "chat",
            "Mail": "email",
            "Notes": "docs",
            "Pages": "docs",
            "Microsoft Word": "docs",
            "Obsidian": "docs",
            "Code": "code",
            "Cursor": "code",
            "Terminal": "code",
            "iTerm2": "code",
            "Claude": "ai_prompt",
            "ChatGPT": "ai_prompt",
            "LM Studio": "ai_prompt",
        },
        "default_style": "default",
    },
    "auto": {
        # Hands-free mode: listen continuously, transcribe each utterance as
        # you finish it. Toggle with the `auto` hotkey or the menu bar.
        "start_on_launch": False,
        # Mic level (0-1) that counts as speech. Measured on this machine:
        # noise floor peaks ~0.047, normal speech runs 0.10-0.47. 0.07 sits
        # between the two. Raise it if background noise triggers takes.
        "start_level": 0.07,
        # Hysteresis: once speaking, stay in the take until it drops below this.
        "end_level": 0.04,
        # Speech must exceed start_level this long to begin (rejects clicks).
        "start_duration": 0.15,
        # Silence this long ends the utterance and sends it for transcription.
        "end_silence": 1.0,
        # Audio kept from *before* the trigger, so the first word survives.
        "preroll": 0.5,
        # Hard cap on one utterance, so a noisy room can't record forever.
        "max_seconds": 90,
    },
    "preview": {
        # Live captions: show your words in the overlay while you're still
        # speaking. A second, much faster Whisper model does the running
        # transcription; the accurate model still produces the final text.
        "enabled": True,
        # Measured on this machine over a 6s clip: tiny 0.43s/pass but
        # misheard "something else" as "some videos"; base 0.67s/pass and got
        # it right; small 2.19s — accurate but far too slow to run live.
        # "base" is the accuracy/speed sweet spot. Drop to "tiny" on a loaded
        # machine, at the cost of more wrong words in the caption.
        "model": "base",
        # Seconds between preview passes. A pass on a 4s window is ~0.66s,
        # so 0.7 keeps the caption about as fresh as the model allows.
        "interval": 0.7,
        # Don't bother previewing until there's this much audio.
        "min_audio": 0.6,
        # Only the last N seconds are re-transcribed each pass, so the caption
        # tracks what you're saying *now* rather than replaying the whole take.
        # Measured on fast continuous speech: a 4s window yields ~62 characters
        # — about one line, which is all that can be shown — at the quickest
        # pass time. Wider windows just transcribe text that gets truncated
        # away, adding lag for nothing.
        "window_seconds": 4.0,
    },
    "overlay": {
        # Floating waveform HUD shown while dictating.
        "enabled": True,
        "y_offset": 120,  # points above the bottom of the screen (auto mode)
        # Drag the pill to move it; the spot is remembered here as
        # {"x": ..., "y": ...} in screen points. null = auto-place at the
        # bottom-centre of whichever display the mouse is on.
        "position": None,
        # true = click-through (can't be dragged, never blocks a click).
        "locked": False,
        # Size of the waveform pill: 1.0 is the default, 0.8 small, 1.3 large.
        "scale": 1.0,
        # Caption text size in points. Subtitle scale: 18 small,
        # 22 default, 28 for presenting.
        "font_size": 22,
    },
    "sounds": True,
}

DEFAULT_DICTIONARY = {
    "vocabulary": [
        "HuaJiaoDJ VoiceFlow", "Ollama", "LM Studio", "faster-whisper", "Whisper",
        "VAD", "Tauri", "SQLite", "qwen3",
    ],
    "replacements": {
        "jason": "JSON",
        "get hub": "GitHub",
        "pie thon": "Python",
    },
    "snippets": {
        "my email": "you@example.com",
        "my signature": "Best regards,\nYour Name",
    },
}


def _load_json(path, default):
    if not os.path.exists(path):
        with open(path, "w") as f:
            json.dump(default, f, indent=2, ensure_ascii=False)
        return json.loads(json.dumps(default))
    with open(path) as f:
        data = json.load(f)
    # fill missing top-level keys from defaults
    for key, value in default.items():
        if key not in data:
            data[key] = value
        elif isinstance(value, dict):
            for k2, v2 in value.items():
                data[key].setdefault(k2, v2)
    return data


def load_config():
    return _load_json(CONFIG_PATH, DEFAULT_CONFIG)


def load_dictionary():
    return _load_json(DICTIONARY_PATH, DEFAULT_DICTIONARY)
