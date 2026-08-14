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
    "overlay": {
        # Floating waveform HUD shown while dictating.
        "enabled": True,
        "y_offset": 120,  # points above the bottom of the screen
    },
    "sounds": True,
}

DEFAULT_DICTIONARY = {
    "vocabulary": [
        "Wispr Flow", "Ollama", "LM Studio", "faster-whisper", "Whisper",
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
