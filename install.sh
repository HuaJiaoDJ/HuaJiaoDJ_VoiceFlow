#!/bin/zsh
# HuaJiaoDJ_VoiceFlow installer for macOS.
# Creates the venv, installs dependencies, builds the .app bundles with the
# correct paths for wherever this project lives, and installs the login agent.
set -e

DIR="${0:A:h}"
cd "$DIR"

BOLD=$'\033[1m'; DIM=$'\033[2m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RESET=$'\033[0m'
step() { print -P "\n${BOLD}==> $1${RESET}"; }
ok()   { print -P "${GREEN}  ok${RESET} $1"; }
warn() { print -P "${YELLOW}  !${RESET}  $1"; }

# ---------------------------------------------------------------- 1. Python
step "Finding a suitable Python"
# 3.14+ has no ctranslate2 wheels yet; 3.11-3.13 are known good.
PY=""
for cand in \
    /opt/homebrew/opt/python@3.13/bin/python3.13 \
    /opt/homebrew/opt/python@3.12/bin/python3.12 \
    /opt/homebrew/opt/python@3.11/bin/python3.11 \
    $(command -v python3.13 2>/dev/null) \
    $(command -v python3.12 2>/dev/null) \
    $(command -v python3.11 2>/dev/null); do
  [[ -x "$cand" ]] && { PY="$cand"; break; }
done
if [[ -z "$PY" ]]; then
  print "No suitable Python found (need 3.11-3.13)."
  print "Install one with:  brew install python@3.13"
  exit 1
fi
ok "$($PY --version) at $PY"

# ------------------------------------------------------------ 2. Environment
step "Creating the virtual environment"
[[ -d .venv ]] || "$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
ok ".venv ready"

step "Installing dependencies (this downloads ~200MB, give it a minute)"
.venv/bin/pip install -q -r requirements.txt
ok "dependencies installed"

# --------------------------------------------------------------- 3. Bundles
# macOS attaches Accessibility/Microphone grants to a signed .app bundle with a
# stable identity — a bare Python process cannot hold them reliably.
build_app() {
  local app="$1" name="$2" exe="$3" ident="$4" args="$5" uielement="$6"
  rm -rf "$app"
  mkdir -p "$app/Contents/MacOS"
  cat > "$app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>$name</string>
    <key>CFBundleDisplayName</key><string>$name</string>
    <key>CFBundleIdentifier</key><string>$ident</string>
    <key>CFBundleVersion</key><string>0.1.0</string>
    <key>CFBundleShortVersionString</key><string>0.1.0</string>
    <key>CFBundleExecutable</key><string>$exe</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>LSMinimumSystemVersion</key><string>13.0</string>
    <key>NSMicrophoneUsageDescription</key><string>HuaJiaoDJ_VoiceFlow transcribes your speech locally for dictation.</string>
$uielement
</dict>
</plist>
PLIST
  cat > "$app/Contents/MacOS/$exe" <<SH
#!/bin/zsh
cd "$DIR" || exit 1
exec .venv/bin/python -m voiceflow $args
SH
  chmod +x "$app/Contents/MacOS/$exe"
  codesign --force --deep --sign - "$app" >/dev/null 2>&1
  /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$app" >/dev/null 2>&1 || true
}

step "Building the app bundles"
# The daemon is an agent app: no Dock icon, no app-switcher entry.
build_app "HuaJiaoDJ_VoiceFlow.app" "HuaJiaoDJ_VoiceFlow" "HuaJiaoDJ_VoiceFlow" "com.huajiaodj.voiceflow.dictation" \
          ">> /tmp/voiceflow.log 2>&1" "    <key>LSUIElement</key><true/>"
ok "HuaJiaoDJ_VoiceFlow.app (the dictation daemon)"
build_app "HuaJiaoDJ_VoiceFlow Settings.app" "HuaJiaoDJ_VoiceFlow Settings" "HuaJiaoDJ_VoiceFlowSettings" \
          "com.huajiaodj.voiceflow.settings" "--settings" ""
ok "HuaJiaoDJ_VoiceFlow Settings.app (the settings window)"

# ------------------------------------------------------------ 4. Login agent
step "Installing the login agent"
PLIST_PATH="$HOME/Library/LaunchAgents/com.huajiaodj.voiceflow.dictation.plist"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.huajiaodj.voiceflow.dictation</string>
    <!-- Launch via LaunchServices so the process keeps the bundle's identity
         and its Accessibility/Microphone grants apply. -->
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/open</string>
        <string>-a</string>
        <string>$DIR/HuaJiaoDJ_VoiceFlow.app</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>ProcessType</key><string>Interactive</string>
</dict>
</plist>
PLIST
plutil -lint "$PLIST_PATH" >/dev/null
launchctl bootout "gui/$(id -u)/com.huajiaodj.voiceflow.dictation" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH" 2>/dev/null || true
ok "starts automatically at login"

# ---------------------------------------------------------------- 5. Ollama
step "Checking the cleanup model backend"
if curl -s --max-time 3 http://localhost:11434/api/version >/dev/null 2>&1; then
  ok "Ollama is running"
else
  warn "Ollama not reachable. Cleanup will be skipped until it runs."
  warn "Install from https://ollama.com, then:  ollama pull qwen3:8b"
fi

print -P "\n${BOLD}${GREEN}Installed.${RESET} Two things left, both one-time:\n"
print -P "  ${BOLD}1.${RESET} Grant permissions when macOS asks (Microphone + Accessibility)."
print -P "     If no prompt appears, add ${BOLD}HuaJiaoDJ_VoiceFlow.app${RESET} manually under"
print -P "     System Settings > Privacy & Security > Accessibility."
print -P "  ${BOLD}2.${RESET} Open ${BOLD}HuaJiaoDJ_VoiceFlow Settings.app${RESET} to pick your model and hotkey.\n"
print -P "  Starting it now...\n"
open -a "$DIR/HuaJiaoDJ_VoiceFlow.app"
