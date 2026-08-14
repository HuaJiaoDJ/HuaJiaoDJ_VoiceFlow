"""LLM backend registry: local servers and cloud APIs.

Adding a backend here makes it available to config.json's `llm.backend`.
API keys are never stored in config.json by default — they resolve from the
macOS Keychain or an environment variable (see resolve_api_key).
"""
import os
import subprocess

# kind drives which request shape cleanup.py builds:
#   "ollama"    -> Ollama native /api/chat
#   "openai"    -> OpenAI-compatible /chat/completions
#   "anthropic" -> Anthropic Messages API (official SDK)
PROVIDERS = {
    "ollama": {
        "kind": "ollama",
        "label": "Ollama (local)",
        "base_url": "http://localhost:11434",
        "needs_key": False,
        "default_model": "qwen3:8b",
    },
    "lmstudio": {
        "kind": "openai",
        "label": "LM Studio (local)",
        "base_url": "http://localhost:1234/v1",
        "needs_key": False,
        "default_model": "local-model",
    },
    "anthropic": {
        "kind": "anthropic",
        "label": "Anthropic API",
        "base_url": "https://api.anthropic.com",
        "needs_key": True,
        "key_env": "ANTHROPIC_API_KEY",
        # Opus 5 is the current flagship. For lower latency on short dictation
        # you can set "claude-haiku-4-5" or "claude-sonnet-5" instead.
        "default_model": "claude-opus-5",
    },
    "openai": {
        "kind": "openai",
        "label": "OpenAI API",
        "base_url": "https://api.openai.com/v1",
        "needs_key": True,
        "key_env": "OPENAI_API_KEY",
        "default_model": "gpt-4o-mini",
    },
    "groq": {
        "kind": "openai",
        "label": "Groq API",
        "base_url": "https://api.groq.com/openai/v1",
        "needs_key": True,
        "key_env": "GROQ_API_KEY",
        "default_model": "llama-3.3-70b-versatile",
    },
    "openrouter": {
        "kind": "openai",
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "needs_key": True,
        "key_env": "OPENROUTER_API_KEY",
        "default_model": "anthropic/claude-sonnet-5",
    },
    "custom": {
        "kind": "openai",
        "label": "Custom OpenAI-compatible endpoint",
        "base_url": None,  # must be set via llm.base_url in config.json
        "needs_key": True,
        "key_env": "VOICEFLOW_API_KEY",
        "default_model": "",
    },
}

KEYCHAIN_ACCOUNT = "huajiaodj-voiceflow"


def provider(backend):
    if backend not in PROVIDERS:
        raise KeyError(
            f"Unknown backend {backend!r}. Available: {', '.join(sorted(PROVIDERS))}"
        )
    return PROVIDERS[backend]


def base_url(backend, llm_cfg):
    """Config override wins, else the provider default."""
    return (llm_cfg.get("base_url") or provider(backend)["base_url"] or "").rstrip("/")


# ---------- API keys ----------

def keychain_get(backend):
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-a", KEYCHAIN_ACCOUNT,
             "-s", f"voiceflow-{backend}", "-w"],
            capture_output=True, timeout=10,
        )
        if out.returncode == 0:
            return out.stdout.decode().strip() or None
    except Exception:
        pass
    return None


def keychain_set(backend, key):
    """Store a key in the login Keychain (-U updates an existing entry)."""
    subprocess.run(
        ["security", "add-generic-password", "-a", KEYCHAIN_ACCOUNT,
         "-s", f"voiceflow-{backend}", "-w", key, "-U"],
        check=True, timeout=10,
    )


def resolve_api_key(backend, llm_cfg):
    """Order: explicit config value -> named env var -> provider's default env
    var -> macOS Keychain. Returns None when the provider needs no key."""
    p = provider(backend)
    if not p.get("needs_key"):
        return None
    if llm_cfg.get("api_key"):
        return llm_cfg["api_key"]
    for env_name in (llm_cfg.get("api_key_env"), p.get("key_env")):
        if env_name and os.environ.get(env_name):
            return os.environ[env_name]
    return keychain_get(backend)


def key_status(backend, llm_cfg):
    """Human-readable source of the key, without revealing it."""
    p = PROVIDERS.get(backend, {})
    if not p.get("needs_key"):
        return "not required"
    if llm_cfg.get("api_key"):
        return "set in config.json"
    for env_name in (llm_cfg.get("api_key_env"), p.get("key_env")):
        if env_name and os.environ.get(env_name):
            return f"from ${env_name}"
    return "in Keychain" if keychain_get(backend) else "MISSING"


# ---------- model discovery ----------

def list_models(backend, llm_cfg, timeout=15):
    """Ask the backend which models it can serve. Raises on transport errors."""
    import requests

    p = provider(backend)
    url = base_url(backend, llm_cfg)
    if not url:
        raise RuntimeError(f"No base_url configured for backend {backend!r}")
    key = resolve_api_key(backend, llm_cfg)
    if p.get("needs_key") and not key:
        raise RuntimeError(
            f"No API key for {backend}. Store one with:\n"
            f"  ./run.sh --set-key {backend}"
        )

    if p["kind"] == "ollama":
        r = requests.get(f"{url}/api/tags", timeout=timeout)
        r.raise_for_status()
        return sorted(m["name"] for m in r.json().get("models", []))

    if p["kind"] == "anthropic":
        r = requests.get(
            f"{url}/v1/models",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
            timeout=timeout,
        )
        r.raise_for_status()
        return sorted(m["id"] for m in r.json().get("data", []))

    # openai-compatible
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    r = requests.get(f"{url}/models", headers=headers, timeout=timeout)
    r.raise_for_status()
    return sorted(m["id"] for m in r.json().get("data", []))
