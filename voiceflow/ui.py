"""Menu-bar item and Settings window (native Cocoa, no extra dependencies).

Everything here runs on the main thread alongside the overlay's run loop.
Network calls (listing models) are pushed to a worker thread so the UI never
blocks. Saving writes config.json, which the daemon hot-reloads.
"""
import json
import threading

import objc
from AppKit import (
    NSApplication,
    NSBackingStoreBuffered,
    NSBox,
    NSButton,
    NSColor,
    NSComboBox,
    NSFont,
    NSImage,
    NSMenu,
    NSMenuItem,
    NSPopUpButton,
    NSSecureTextField,
    NSStatusBar,
    NSTextField,
    NSWindow,
)
from Foundation import NSMakeRect, NSObject

from . import config as cfg
from . import providers

NSWindowStyleMaskTitled = 1
NSWindowStyleMaskClosable = 2
NSWindowStyleMaskMiniaturizable = 4
NSVariableStatusItemLength = -1.0
NSSwitchButton = 3
NSEventMaskKeyDown = 1 << 10
NSEventMaskFlagsChanged = 1 << 12

# Modifier keyCodes -> side-specific tokens, for recording modifier-only combos.
_MOD_VK = {
    54: "cmd_r", 55: "cmd_l", 56: "shift_l", 60: "shift_r",
    58: "alt_l", 61: "alt_r", 59: "ctrl_l", 62: "ctrl_r",
}
_FN_VK = {
    122: "f1", 120: "f2", 99: "f3", 118: "f4", 96: "f5", 97: "f6",
    98: "f7", 100: "f8", 101: "f9", 109: "f10", 103: "f11", 111: "f12",
    105: "f13", 107: "f14", 113: "f15", 49: "space", 36: "enter", 48: "tab",
}


def _spec_from_key_event(event):
    """NSEvent -> a hotkey spec string like 'ctrl+alt+z'."""
    from .main import _VK_CHAR

    flags = event.modifierFlags()
    parts = []
    if flags & (1 << 18):
        parts.append("ctrl")
    if flags & (1 << 19):
        parts.append("alt")
    if flags & (1 << 17):
        parts.append("shift")
    if flags & (1 << 20):
        parts.append("cmd")
    vk = event.keyCode()
    key = _VK_CHAR.get(vk) or _FN_VK.get(vk)
    if key:
        parts.append(key)
    return "+".join(parts) if parts else None


class HotkeyRecorder(NSObject):
    """Captures the next key combo pressed and reports it back."""

    def initWithButton_callback_(self, button, callback):
        self = objc.super(HotkeyRecorder, self).init()
        self.button = button
        self.callback = callback
        self.monitor = None
        self.title = button.title()
        self.mods_seen = []
        return self

    @objc.python_method
    def start(self):
        if self.monitor is not None:
            return
        self.button.setTitle_("Press keys…")
        self.mods_seen = []
        app = NSApplication.sharedApplication()
        self.monitor = app.addLocalMonitorForEventsMatchingMask_handler_(
            NSEventMaskKeyDown | NSEventMaskFlagsChanged, self._handle
        )

    @objc.python_method
    def _handle(self, event):
        if event.type() == 10:  # keyDown -> a full combo with a character key
            spec = _spec_from_key_event(event)
            if spec:
                self._finish(spec)
            return None  # swallow so the key doesn't reach the field
        # flagsChanged: remember modifiers, and finalize when they're all released
        tok = _MOD_VK.get(event.keyCode())
        if tok and tok not in self.mods_seen:
            self.mods_seen.append(tok)
        if event.modifierFlags() & 0x1F0000 == 0 and self.mods_seen:
            self._finish("+".join(self.mods_seen))
        return None

    @objc.python_method
    def _finish(self, spec):
        self.stop()
        self.callback(spec)

    @objc.python_method
    def stop(self):
        if self.monitor is not None:
            NSApplication.sharedApplication().removeMonitor_(self.monitor)
            self.monitor = None
        self.button.setTitle_(self.title)


class SettingsController(NSObject):
    def init(self):
        self = objc.super(SettingsController, self).init()
        self.window = None
        self.recorder = None
        self.conf = cfg.load_config()
        return self

    # ---------- construction ----------

    @objc.python_method
    def _label(self, text, x, y, w=140, bold=False):
        f = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, w, 18))
        f.setStringValue_(text)
        f.setBezeled_(False)
        f.setDrawsBackground_(False)
        f.setEditable_(False)
        f.setSelectable_(False)
        if bold:
            f.setFont_(NSFont.boldSystemFontOfSize_(12))
        else:
            f.setFont_(NSFont.systemFontOfSize_(12))
        self.view.addSubview_(f)
        return f

    @objc.python_method
    def _separator(self, y):
        box = NSBox.alloc().initWithFrame_(NSMakeRect(20, y, 480, 1))
        box.setBoxType_(2)
        self.view.addSubview_(box)

    @objc.python_method
    def _button(self, title, x, y, w, action):
        b = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, w, 26))
        b.setTitle_(title)
        b.setBezelStyle_(1)
        b.setTarget_(self)
        b.setAction_(action)
        self.view.addSubview_(b)
        return b

    @objc.python_method
    def buildWindow(self):
        W, H = 520, 720
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W, H),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
            | NSWindowStyleMaskMiniaturizable,
            NSBackingStoreBuffered, False,
        )
        self.window.setTitle_("HuaJiaoDJ_VoiceFlow Settings")
        self.window.setReleasedWhenClosed_(False)
        self.view = self.window.contentView()

        y = H - 44
        self._label("Cleanup model", 20, y, 200, bold=True)

        y -= 32
        self._label("Backend", 20, y + 3, 90)
        self.backend_popup = NSPopUpButton.alloc().initWithFrame_(
            NSMakeRect(115, y, 240, 26))
        for name in sorted(providers.PROVIDERS):
            self.backend_popup.addItemWithTitle_(name)
        self.backend_popup.setTarget_(self)
        self.backend_popup.setAction_("backendChanged:")
        self.view.addSubview_(self.backend_popup)

        y -= 34
        self._label("Model", 20, y + 3, 90)
        self.model_combo = NSComboBox.alloc().initWithFrame_(
            NSMakeRect(115, y, 240, 26))
        self.model_combo.setCompletes_(True)
        self.view.addSubview_(self.model_combo)
        self._button("Refresh", 365, y, 90, "refreshModels:")

        y -= 34
        self._label("API key", 20, y + 3, 90)
        self.key_field = NSSecureTextField.alloc().initWithFrame_(
            NSMakeRect(115, y, 240, 26))
        self.key_field.setPlaceholderString_("paste key, then Store")
        self.view.addSubview_(self.key_field)
        self._button("Store", 365, y, 90, "storeKey:")

        y -= 24
        self.key_status = self._label("", 115, y, 380)
        self.key_status.setFont_(NSFont.systemFontOfSize_(10))
        self.key_status.setTextColor_(NSColor.secondaryLabelColor())

        y -= 22
        self._separator(y)

        y -= 30
        self._label("Hotkeys", 20, y, 200, bold=True)

        y -= 32
        self._label("Dictation mode", 20, y + 3, 110)
        self.mode_popup = NSPopUpButton.alloc().initWithFrame_(
            NSMakeRect(135, y, 260, 26))
        self.mode_popup.addItemWithTitle_("Hold to talk (push-to-talk)")
        self.mode_popup.addItemWithTitle_("Toggle: press on, press off")
        self.view.addSubview_(self.mode_popup)

        y -= 34
        self._label("Dictate", 20, y + 3, 110)
        self.dictate_field = self._label("", 135, y + 4, 200)
        self.dictate_field.setFont_(NSFont.monospacedSystemFontOfSize_weight_(12, 0))
        self.dictate_btn = self._button("Record", 345, y, 110, "recordDictate:")

        y -= 34
        self._label("Command mode", 20, y + 3, 110)
        self.command_field = self._label("", 135, y + 4, 200)
        self.command_field.setFont_(NSFont.monospacedSystemFontOfSize_weight_(12, 0))
        self.command_btn = self._button("Record", 345, y, 110, "recordCommand:")

        y -= 34
        self._label("Auto transcribe", 20, y + 3, 110)
        self.auto_field = self._label("", 135, y + 4, 200)
        self.auto_field.setFont_(NSFont.monospacedSystemFontOfSize_weight_(12, 0))
        self.auto_btn = self._button("Record", 345, y, 110, "recordAuto:")

        y -= 26
        self.auto_launch_box = NSButton.alloc().initWithFrame_(
            NSMakeRect(135, y, 320, 22))
        self.auto_launch_box.setButtonType_(NSSwitchButton)
        self.auto_launch_box.setTitle_("Start auto transcribe when HuaJiaoDJ_VoiceFlow launches")
        self.view.addSubview_(self.auto_launch_box)

        y -= 30
        self._label("Mic sensitivity", 20, y + 3, 110)
        self.sens_popup = NSPopUpButton.alloc().initWithFrame_(
            NSMakeRect(135, y, 260, 26))
        for title in ("High — picks up quiet speech",
                      "Normal — recommended",
                      "Low — noisy rooms"):
            self.sens_popup.addItemWithTitle_(title)
        self.view.addSubview_(self.sens_popup)

        y -= 30
        self._label("End of speech", 20, y + 3, 110)
        self.pause_popup = NSPopUpButton.alloc().initWithFrame_(
            NSMakeRect(135, y, 260, 26))
        for title in ("After 0.6s pause — snappy",
                      "After 1.0s pause — recommended",
                      "After 1.8s pause — slow speakers"):
            self.pause_popup.addItemWithTitle_(title)
        self.view.addSubview_(self.pause_popup)

        y -= 26
        hint = self._label(
            "Tip: modifier-only combos (e.g. Right Control) avoid app conflicts.",
            135, y, 360)
        hint.setFont_(NSFont.systemFontOfSize_(10))
        hint.setTextColor_(NSColor.secondaryLabelColor())

        y -= 22
        self._separator(y)

        y -= 30
        self._label("Feedback", 20, y, 200, bold=True)

        y -= 28
        self.sounds_box = NSButton.alloc().initWithFrame_(NSMakeRect(20, y, 220, 22))
        self.sounds_box.setButtonType_(NSSwitchButton)
        self.sounds_box.setTitle_("Play start / done sounds")
        self.view.addSubview_(self.sounds_box)

        y -= 26
        self.overlay_box = NSButton.alloc().initWithFrame_(NSMakeRect(20, y, 260, 22))
        self.overlay_box.setButtonType_(NSSwitchButton)
        self.overlay_box.setTitle_("Show waveform overlay while recording")
        self.view.addSubview_(self.overlay_box)

        y -= 26
        self.llm_box = NSButton.alloc().initWithFrame_(NSMakeRect(20, y, 300, 22))
        self.llm_box.setButtonType_(NSSwitchButton)
        self.llm_box.setTitle_("Clean up transcripts with the model")
        self.view.addSubview_(self.llm_box)

        self.status = self._label("", 20, 22, 330)
        self.status.setFont_(NSFont.systemFontOfSize_(11))
        self.status.setTextColor_(NSColor.secondaryLabelColor())
        self._button("Save", 420, 18, 80, "save:")
        self._button("Revert", 335, 18, 80, "revert:")

    # ---------- data binding ----------

    @objc.python_method
    def loadIntoUI(self):
        self.conf = cfg.load_config()
        llm, hk = self.conf["llm"], self.conf["hotkeys"]
        self.backend_popup.selectItemWithTitle_(llm["backend"])
        self.model_combo.setStringValue_(llm["model"] or "")
        self.mode_popup.selectItemAtIndex_(
            1 if hk.get("mode", "hold") == "toggle" else 0)
        self.dictate_field.setStringValue_(hk["dictate"])
        self.auto_field.setStringValue_(hk.get("auto", ""))
        a = self.conf.get("auto", {})
        self.auto_launch_box.setState_(1 if a.get("start_on_launch") else 0)
        lvl = a.get("start_level", 0.18)
        self.sens_popup.selectItemAtIndex_(
            0 if lvl <= 0.05 else (2 if lvl >= 0.11 else 1))
        sil = a.get("end_silence", 1.0)
        self.pause_popup.selectItemAtIndex_(
            0 if sil <= 0.7 else (2 if sil >= 1.4 else 1))
        self.command_field.setStringValue_(hk["command"])
        self.sounds_box.setState_(1 if self.conf["sounds"] else 0)
        self.overlay_box.setState_(
            1 if self.conf.get("overlay", {}).get("enabled", True) else 0)
        self.llm_box.setState_(1 if llm["enabled"] else 0)
        self._refreshKeyStatus()
        self._loadModelsAsync()

    @objc.python_method
    def _refreshKeyStatus(self):
        backend = self.backend_popup.titleOfSelectedItem()
        llm = dict(self.conf["llm"], backend=backend)
        self.key_status.setStringValue_(f"key: {providers.key_status(backend, llm)}")
        needs = providers.PROVIDERS.get(backend, {}).get("needs_key", False)
        self.key_field.setEnabled_(bool(needs))

    @objc.python_method
    def _loadModelsAsync(self):
        backend = self.backend_popup.titleOfSelectedItem()
        llm = dict(self.conf["llm"], backend=backend)
        self.status.setStringValue_(f"Loading {backend} models…")

        def work():
            try:
                models = providers.list_models(backend, llm)
                err = None
            except Exception as e:
                models, err = [], str(e)
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "modelsLoaded:", (models, err), False)

        threading.Thread(target=work, daemon=True).start()

    def modelsLoaded_(self, payload):
        models, err = payload
        current = self.model_combo.stringValue()
        self.model_combo.removeAllItems()
        for m in models:
            self.model_combo.addItemWithObjectValue_(m)
        if err:
            self.status.setStringValue_(f"Could not list models: {err[:60]}")
        else:
            self.status.setStringValue_(f"{len(models)} models available")
            if current not in models and models:
                self.model_combo.setStringValue_(models[0])

    # ---------- actions ----------

    def backendChanged_(self, _sender):
        backend = self.backend_popup.titleOfSelectedItem()
        default = providers.PROVIDERS[backend].get("default_model") or ""
        self.model_combo.setStringValue_(default)
        self._refreshKeyStatus()
        self._loadModelsAsync()

    def refreshModels_(self, _sender):
        self._loadModelsAsync()

    def storeKey_(self, _sender):
        backend = self.backend_popup.titleOfSelectedItem()
        key = self.key_field.stringValue().strip()
        if not key:
            self.status.setStringValue_("Enter a key first.")
            return
        try:
            providers.keychain_set(backend, key)
        except Exception as e:
            self.status.setStringValue_(f"Keychain error: {e}")
            return
        self.key_field.setStringValue_("")   # never keep it in the field
        self._refreshKeyStatus()
        self.status.setStringValue_(f"Key stored in Keychain for {backend}.")

    @objc.python_method
    def _record(self, button, field):
        if self.recorder is not None:
            self.recorder.stop()

        def done(spec):
            field.setStringValue_(spec)
            self.status.setStringValue_(f"Hotkey set to [{spec}] — press Save.")
            self.recorder = None

        self.recorder = HotkeyRecorder.alloc().initWithButton_callback_(button, done)
        self.recorder.start()
        self.status.setStringValue_("Press the key combination…")

    def recordDictate_(self, _sender):
        self._record(self.dictate_btn, self.dictate_field)

    def recordCommand_(self, _sender):
        self._record(self.command_btn, self.command_field)

    def recordAuto_(self, _sender):
        self._record(self.auto_btn, self.auto_field)

    def revert_(self, _sender):
        self.loadIntoUI()
        self.status.setStringValue_("Reverted to saved settings.")

    def save_(self, _sender):
        from .main import parse_hotkey

        conf = cfg.load_config()
        dictate = self.dictate_field.stringValue().strip()
        command = self.command_field.stringValue().strip()
        for spec in (dictate, command):
            try:
                parse_hotkey(spec)
            except SystemExit as e:
                self.status.setStringValue_(str(e))
                return
        conf["hotkeys"]["dictate"] = dictate
        conf["hotkeys"]["command"] = command
        auto_spec = self.auto_field.stringValue().strip()
        if auto_spec:
            try:
                parse_hotkey(auto_spec)
            except SystemExit as e:
                self.status.setStringValue_(str(e))
                return
        conf["hotkeys"]["auto"] = auto_spec
        a = conf.setdefault("auto", {})
        a["start_on_launch"] = bool(self.auto_launch_box.state())
        a["start_level"] = (0.04, 0.07, 0.14)[self.sens_popup.indexOfSelectedItem()]
        a["end_level"] = round(a["start_level"] * 0.6, 3)
        a["end_silence"] = (0.6, 1.0, 1.8)[self.pause_popup.indexOfSelectedItem()]
        conf["hotkeys"]["mode"] = (
            "toggle" if self.mode_popup.indexOfSelectedItem() == 1 else "hold")
        conf["llm"]["backend"] = self.backend_popup.titleOfSelectedItem()
        conf["llm"]["model"] = self.model_combo.stringValue().strip()
        conf["llm"]["enabled"] = bool(self.llm_box.state())
        conf["sounds"] = bool(self.sounds_box.state())
        conf.setdefault("overlay", {})["enabled"] = bool(self.overlay_box.state())
        with open(cfg.CONFIG_PATH, "w") as f:
            json.dump(conf, f, indent=2, ensure_ascii=False)
        self.conf = conf
        self.status.setStringValue_("Saved — applies on your next keypress.")

    # ---------- window ----------

    @objc.python_method
    def show(self):
        if self.window is None:
            self.buildWindow()
        self.loadIntoUI()
        self.window.center()
        # Accessory apps must activate explicitly to accept keyboard input.
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)


class MenuBar(NSObject):
    """Status-bar item: the only always-visible entry point to the app."""

    def initWithSettings_app_(self, settings, app):
        self = objc.super(MenuBar, self).init()
        self.settings = settings
        self.app = app        # the VoiceFlow daemon, or None in standalone mode
        self.auto_item = None
        bar = NSStatusBar.systemStatusBar()
        self.item = bar.statusItemWithLength_(NSVariableStatusItemLength)
        button = self.item.button()
        # Prefer the native SF Symbol; fall back to text on older systems.
        image = None
        try:
            image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                "waveform", "HuaJiaoDJ_VoiceFlow")
        except Exception:
            pass
        if image is not None:
            image.setTemplate_(True)
            button.setImage_(image)
        else:
            button.setTitle_("VF")
        button.setToolTip_("HuaJiaoDJ_VoiceFlow — click for Settings")
        self.item.setVisible_(True)
        menu = NSMenu.alloc().init()
        self.auto_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Auto transcribe", "toggleAuto:", "")
        self.auto_item.setTarget_(self)
        menu.addItem_(self.auto_item)
        menu.addItem_(NSMenuItem.separatorItem())
        for title, action in (("Settings…", "openSettings:"), (None, None),
                              ("Quit HuaJiaoDJ_VoiceFlow", "quit:")):
            if title is None:
                menu.addItem_(NSMenuItem.separatorItem())
                continue
            mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, "")
            mi.setTarget_(self)
            menu.addItem_(mi)
        self.item.setMenu_(menu)
        self.refresh_auto(bool(getattr(self.app, "auto_on", False)))
        return self

    @objc.python_method
    def refresh_auto(self, on):
        """Reflect hands-free state: menu checkmark + a filled menu-bar icon."""
        if self.auto_item is not None:
            self.auto_item.setState_(1 if on else 0)
        try:
            name = "waveform.circle.fill" if on else "waveform"
            image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                name, "HuaJiaoDJ_VoiceFlow")
            if image is not None:
                image.setTemplate_(True)
                self.item.button().setImage_(image)
            self.item.button().setToolTip_(
                "HuaJiaoDJ_VoiceFlow — auto transcribe ON" if on
                else "HuaJiaoDJ_VoiceFlow — click for Settings")
        except Exception:
            pass

    def toggleAuto_(self, _sender):
        if self.app is None:
            return
        self.app.set_auto(not self.app.auto_on)

    def openSettings_(self, _sender):
        self.settings.show()

    def quit_(self, _sender):
        NSApplication.sharedApplication().terminate_(None)


class WindowCloser(NSObject):
    """Quits the standalone settings process when its window closes."""

    def windowWillClose_(self, _note):
        NSApplication.sharedApplication().terminate_(None)


_settings = None
_menubar = None
_closer = None


def start(app=None):
    """Create the menu-bar item and settings window. Main thread only.
    `app` is the running VoiceFlow daemon, so the menu can toggle auto mode."""
    global _settings, _menubar
    _settings = SettingsController.alloc().init()
    _menubar = MenuBar.alloc().initWithSettings_app_(_settings, app)
    return _menubar


def run_standalone():
    """Open Settings as its own app, independent of the running daemon.

    Saves go to config.json, which the daemon hot-reloads — so settings can be
    changed this way whether or not the daemon is running.
    """
    global _settings, _closer
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(0)  # Regular: appears in the Dock and takes focus
    _settings = SettingsController.alloc().init()
    _settings.show()
    _closer = WindowCloser.alloc().init()
    _settings.window.setDelegate_(_closer)
    app.activateIgnoringOtherApps_(True)
    app.run()
