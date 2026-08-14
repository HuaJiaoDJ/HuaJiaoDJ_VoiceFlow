"""CLI for inspecting and changing LLM settings without editing JSON by hand.

Every write goes through config.json, which the running daemon hot-reloads —
so a backend or model change takes effect without a restart.
"""
import getpass
import json
import sys

from . import config as cfg
from . import providers


def _save(conf):
    with open(cfg.CONFIG_PATH, "w") as f:
        json.dump(conf, f, indent=2, ensure_ascii=False)


def show_config(conf):
    llm = conf["llm"]
    backend = llm["backend"]
    p = providers.PROVIDERS.get(backend, {})
    print(f"backend  : {backend}  ({p.get('label', 'unknown')})")
    print(f"model    : {llm['model']}")
    print(f"endpoint : {providers.base_url(backend, llm) or '(unset)'}")
    print(f"api key  : {providers.key_status(backend, llm)}")
    print(f"cleanup  : {'enabled' if llm['enabled'] else 'disabled'}")


def list_backends(conf):
    active = conf["llm"]["backend"]
    for name, p in sorted(providers.PROVIDERS.items()):
        mark = "*" if name == active else " "
        key = "key required" if p.get("needs_key") else "no key"
        print(f" {mark} {name:<12} {p['label']:<38} ({key})")
    print("\n* = active.  Switch with: ./run.sh --set-backend <name>")


def list_models(conf, backend):
    llm = conf["llm"]
    backend = backend if isinstance(backend, str) else llm["backend"]
    try:
        models = providers.list_models(backend, llm)
    except Exception as e:
        print(f"[error] could not list models for {backend}: {e}", file=sys.stderr)
        return 1
    if not models:
        print(f"(no models reported by {backend})")
        return 0
    current = llm["model"]
    for m in models:
        print(f" {'*' if m == current else ' '} {m}")
    print(f"\n* = active.  Switch with: ./run.sh --set-model <name>")
    return 0


def set_backend(conf, name):
    try:
        p = providers.provider(name)
    except KeyError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 1
    llm = conf["llm"]
    llm["backend"] = name
    # Carry the provider's default model over so the pair is always coherent.
    if p.get("default_model"):
        llm["model"] = p["default_model"]
    _save(conf)
    print(f"backend -> {name}  (model: {llm['model']})")
    if p.get("needs_key") and providers.key_status(name, llm) == "MISSING":
        print(f"\n[!] {name} needs an API key. Store one with:\n"
              f"    ./run.sh --set-key {name}")
    if p["base_url"] is None:
        print(f"\n[!] Set llm.base_url in config.json for the {name} backend.")
    return 0


def set_model(conf, name):
    conf["llm"]["model"] = name
    _save(conf)
    print(f"model -> {name}  (backend: {conf['llm']['backend']})")
    return 0


def set_key(backend):
    """Prompt for the key locally and hand it straight to the Keychain.
    The value is never echoed, logged, or written to config.json."""
    try:
        providers.provider(backend)
    except KeyError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 1
    key = getpass.getpass(f"Paste the API key for {backend} (input hidden): ").strip()
    if not key:
        print("[cancelled] no key entered")
        return 1
    try:
        providers.keychain_set(backend, key)
    except Exception as e:
        print(f"[error] could not write to Keychain: {e}", file=sys.stderr)
        return 1
    print(f"Stored in Keychain as 'voiceflow-{backend}'.")
    print(f"Use it with: ./run.sh --set-backend {backend}")
    return 0


def handle_cli(args):
    """Run a settings subcommand if one was requested.
    Returns True when the process should exit instead of starting the daemon."""
    if args.set_key:
        sys.exit(set_key(args.set_key))
    if not (args.show_config or args.list_backends or args.list_models
            or args.set_backend or args.set_model):
        return False
    conf = cfg.load_config()
    if args.set_backend:
        sys.exit(set_backend(conf, args.set_backend))
    if args.set_model:
        sys.exit(set_model(conf, args.set_model))
    if args.list_backends:
        list_backends(conf)
        sys.exit(0)
    if args.list_models:
        sys.exit(list_models(conf, args.list_models))
    show_config(conf)
    sys.exit(0)
