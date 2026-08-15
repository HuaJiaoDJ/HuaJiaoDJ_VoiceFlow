import argparse
import contextlib
import faulthandler
import os
import queue
import signal
import subprocess
import sys
import threading
import time

from pynput import keyboard
from Quartz import (
    CGEventGetFlags,
    CGEventGetIntegerValueField,
    kCGKeyboardEventKeycode,
)

from . import config as cfg
from .audio import Recorder
from .cleanup import CleanupEngine
from .context import frontmost_app_name, style_for_app
from .paste import copy_selection, paste_text
from .rules import apply_replacements, extract_press_enter, match_snippet
from . import power
from . import settings

SOUND_START = "/System/Library/Sounds/Pop.aiff"
SOUND_DONE = "/System/Library/Sounds/Bottle.aiff"
SOUND_ERROR = "/System/Library/Sounds/Basso.aiff"


Key = keyboard.Key
KeyCode = keyboard.KeyCode

# Modifier families that should match either the left or right physical key
# when the hotkey spec uses the generic name (e.g. "cmd" matches cmd_l/cmd_r).
GENERIC_MODIFIERS = {"cmd", "shift", "ctrl", "alt"}

# Normalize a spec token to a canonical form.
_SPEC_ALIAS = {
    "command": "cmd", "cmd_l": "cmd_l", "cmd_r": "cmd_r",
    "control": "ctrl", "ctrl_l": "ctrl_l", "ctrl_r": "ctrl_r",
    "option": "alt", "opt": "alt", "alt_l": "alt_l", "alt_r": "alt_r",
    "shift_l": "shift_l", "shift_r": "shift_r",
}

# Normalize a pynput key event to a specific token (keeps left/right apart).
_KEY_TOKEN = {
    Key.cmd: "cmd_l", getattr(Key, "cmd_l", Key.cmd): "cmd_l", Key.cmd_r: "cmd_r",
    Key.shift: "shift_l", getattr(Key, "shift_l", Key.shift): "shift_l", Key.shift_r: "shift_r",
    Key.ctrl: "ctrl_l", getattr(Key, "ctrl_l", Key.ctrl): "ctrl_l", Key.ctrl_r: "ctrl_r",
    Key.alt: "alt_l", getattr(Key, "alt_l", Key.alt): "alt_l", Key.alt_r: "alt_r",
    getattr(Key, "alt_gr", Key.alt_r): "alt_r",
}


def parse_hotkey(spec):
    """Parse 'cmd+shift+v' / 'ctrl_r' / 'alt_r' into a frozenset of tokens."""
    parts = [p.strip().lower() for p in spec.replace(" ", "+").split("+") if p.strip()]
    tokens = set()
    for p in parts:
        if p in _SPEC_ALIAS:
            tokens.add(_SPEC_ALIAS[p])
        elif len(p) == 1 or hasattr(Key, p):
            tokens.add(p)
        else:
            raise SystemExit(f"Unknown hotkey token in config: {p!r} (from {spec!r})")
    if not tokens:
        raise SystemExit(f"Empty hotkey in config: {spec!r}")
    return frozenset(tokens)


# macOS ANSI virtual keycodes -> characters. Needed because with modifiers
# held the event's char is transformed (Ctrl+Z arrives as '\x1a', Option+Z
# as 'Ω'); the hardware keycode is stable regardless of modifiers.
_VK_CHAR = {
    0: "a", 1: "s", 2: "d", 3: "f", 4: "h", 5: "g", 6: "z", 7: "x", 8: "c",
    9: "v", 11: "b", 12: "q", 13: "w", 14: "e", 15: "r", 16: "y", 17: "t",
    18: "1", 19: "2", 20: "3", 21: "4", 22: "6", 23: "5", 25: "9", 26: "7",
    28: "8", 29: "0", 31: "o", 32: "u", 34: "i", 35: "p", 37: "l", 38: "j",
    40: "k", 45: "n", 46: "m",
}


def key_token(key):
    """Normalize a pynput key event to a specific token string."""
    if key in _KEY_TOKEN:
        return _KEY_TOKEN[key]
    if isinstance(key, KeyCode):
        if key.vk in _VK_CHAR:
            return _VK_CHAR[key.vk]
        if key.char:
            return key.char.lower()
    if isinstance(key, Key):
        return key.name
    return str(key)


# CGEvent flag masks, for deciding whether a keystroke belongs to a hotkey.
_FLAG_FOR_MOD = {
    "shift": 0x00020000,
    "ctrl": 0x00040000,
    "alt": 0x00080000,
    "cmd": 0x00100000,
}
kCGEventKeyDown, kCGEventKeyUp = 10, 11


def _mod_family(tok):
    """'ctrl_r' -> 'ctrl'; returns None for non-modifier tokens like 'z'."""
    for fam in _FLAG_FOR_MOD:
        if tok == fam or tok.startswith(fam + "_"):
            return fam
    return None


def spec_plain_keys(spec):
    """The non-modifier tokens of a hotkey (e.g. {'z'} for 'ctrl+alt+z')."""
    return {t for t in spec if _mod_family(t) is None}


def spec_claims_key(spec, token, flags):
    """True if `token` is the hotkey's character key and the combo's modifiers
    are held — i.e. this keystroke is ours and must not reach the focused app.
    Without this, Ctrl+Option+Z would type macOS's Option+Z character (Ω)."""
    if token is None:
        return False
    needed = 0
    plain = set()
    for tok in spec:
        fam = _mod_family(tok)
        if fam:
            needed |= _FLAG_FOR_MOD[fam]
        else:
            plain.add(tok)
    if token not in plain:
        return False
    return (flags & needed) == needed


# token -> macOS virtual keycodes. Generic modifiers map to both sides.
_VK_FOR_TOKEN = {
    "cmd_l": (55,), "cmd_r": (54,), "cmd": (55, 54),
    "shift_l": (56,), "shift_r": (60,), "shift": (56, 60),
    "alt_l": (58,), "alt_r": (61,), "alt": (58, 61),
    "ctrl_l": (59,), "ctrl_r": (62,), "ctrl": (59, 62),
}
_CHAR_VK = {c: vk for vk, c in _VK_CHAR.items()}
kCGEventSourceStateHIDSystemState = 1


def keys_physically_held(spec):
    """Ask the HID layer whether the hotkey is *actually* still down.

    The event stream can lie: a dropped or system-injected key-up ends a take
    early. Hardware state is the ground truth, so a release edge is only
    believed when the keys really are up. Returns None if unknowable.
    """
    try:
        from Quartz import CGEventSourceKeyState
    except Exception:
        return None
    for tok in spec:
        vks = _VK_FOR_TOKEN.get(tok)
        if vks is None:
            vk = _CHAR_VK.get(tok)
            vks = (vk,) if vk is not None else ()
        if not vks:
            return None  # can't map this token; don't second-guess the event
        if not any(CGEventSourceKeyState(kCGEventSourceStateHIDSystemState, vk)
                   for vk in vks):
            return False
    return True


def hotkey_satisfied(spec, down):
    """True if every token in the spec is currently held in `down`."""
    for tok in spec:
        if tok in GENERIC_MODIFIERS:
            if not any(d == tok or d.startswith(tok + "_") for d in down):
                return False
        elif tok not in down:
            return False
    return True


class VoiceFlow:
    def __init__(self, conf, dictionary):
        self.conf = conf
        self.dictionary = dictionary
        self.recorder = Recorder(
            sample_rate=conf["audio"]["sample_rate"],
            device=conf["audio"]["input_device"],
        )
        self.cleanup = CleanupEngine(conf["llm"])
        self.transcriber = None  # loaded in run()
        self.mode = None  # None | "dictate" | "command"
        self.selection = None
        self.target_app = None
        self.busy = threading.Lock()
        self.debug = False
        self._events = queue.Queue()
        self._down = set()  # tokens for keys currently held
        self._injecting = False  # True while we synthesize Cmd+V / Cmd+C
        self._config_mtimes = {}
        self._overlay = None  # the waveform HUD module, if enabled
        self._claimed_down = set()  # hotkey keys we swallowed the press for
        self._began_at = 0.0        # start of the current take
        self.auto_on = False        # hands-free transcribe active
        self.auto_key = None
        self._menubar = None
        self._auto_held = False
        self._dictate_held = False  # combo level, for edge detection
        self._command_held = False

    # ---------- feedback ----------

    def sound(self, path):
        if self.conf["sounds"]:
            subprocess.Popen(["afplay", path], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)

    def log(self, msg):
        print(msg, flush=True)

    def hud(self, action):
        """Drive the waveform overlay; never let UI trouble break dictation."""
        if self._overlay is None:
            return
        try:
            getattr(self._overlay, action)()
        except Exception as e:
            self.log(f"[overlay] {action} failed: {e}")

    # ---------- hotkey handling ----------
    #
    # The pynput listener runs on a macOS CGEventTap. If a tap callback is
    # slow (opening the audio stream, AppKit calls, subprocesses), macOS
    # disables the tap and never re-enables it — hotkeys "work then stop".
    # So the callbacks do nothing but enqueue the event; a worker thread
    # (_event_loop) does the real work.

    def on_press(self, key):
        try:
            self._events.put_nowait(("press", key))
        except Exception:
            pass

    def on_release(self, key):
        try:
            self._events.put_nowait(("release", key))
        except Exception:
            pass

    def _intercept(self, event_type, event):
        """Runs on the event tap after on_press/on_release have been queued.
        Return None to swallow the keystroke, or the event to let it through.
        We swallow only our hotkey's character key, so the focused app never
        sees it (otherwise Option+Z inserts 'Ω' before the dictation pastes).
        Must stay fast — it runs inside the tap callback."""
        try:
            if event_type not in (kCGEventKeyDown, kCGEventKeyUp):
                return event
            keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
            token = _VK_CHAR.get(keycode)
            if token is None:
                return event
            flags = CGEventGetFlags(event)
            specs = [self.dictate_key, self.command_key]
            if self.auto_key is not None:
                specs.append(self.auto_key)   # else Option+A types 'å'
            hotkey_letters = set()
            for spec in specs:
                hotkey_letters |= spec_plain_keys(spec)
            claimed = any(spec_claims_key(spec, token, flags) for spec in specs)

            # The "while recording" escape hatch only applies to push-to-talk, where
            # the key is physically held for the whole take. In toggle mode the
            # key is up while recording, so this must not fire — otherwise every
            # letter matching the hotkey would be eaten as the user types.
            held_mode = self.conf["hotkeys"].get("mode", "hold") == "hold"

            if event_type == kCGEventKeyDown:
                # Own the key for the whole hold. Modifiers are released a few
                # milliseconds apart, so a trailing auto-repeat can arrive with
                # only Option still down — which is the Ω key. Re-checking the
                # modifiers per event let that one through; sticking to the
                # original claim (or an active recording) never does.
                if claimed or token in self._claimed_down or (
                        held_mode and self.mode is not None
                        and token in hotkey_letters):
                    self._claimed_down.add(token)
                    swallow = True
                else:
                    swallow = False
            else:  # key up — always release the claim so 'z' stays typable
                swallow = token in self._claimed_down
                self._claimed_down.discard(token)

            if self.debug:
                kind = "down" if event_type == kCGEventKeyDown else "up"
                self.log(f"[tap] {kind} key={token} vk={keycode} "
                         f"flags=0x{flags:08x} swallow={swallow}")
            if swallow:
                return None
        except Exception as e:
            if self.debug:
                self.log(f"[tap] error: {e}")
        return event

    def _event_loop(self):
        while True:
            kind, key = self._events.get()
            try:
                # Ignore the keystrokes we synthesize for paste/copy — otherwise
                # a synthetic Cmd release can wipe a physically-held key from
                # _down and the combo silently stops matching.
                if self._injecting:
                    continue
                token = key_token(key)
                if kind == "press":
                    self._down.add(token)
                else:
                    self._down.discard(token)
                    # Invariant: nothing physically held => no outstanding
                    # claims. Guards against a dropped key-up permanently
                    # swallowing a letter.
                    if not self._down:
                        self._claimed_down.clear()
                if self.debug:
                    self.log(f"[debug] {kind:7} {token} down={sorted(self._down)} "
                             f"(mode={self.mode})")
                if kind == "press":
                    self._maybe_reload_config()

                # Work on edges, not levels: a combo "fires" only on the
                # transition into being fully held. Key auto-repeat keeps the
                # level True, so edge detection ignores it for free.
                # Auto-transcribe toggle is mode-independent: one press flips it.
                auto_now = (self.auto_key is not None
                            and hotkey_satisfied(self.auto_key, self._down))
                if auto_now and not self._auto_held and kind == "press":
                    self._auto_held = True
                    self.set_auto(not self.auto_on)
                    continue
                self._auto_held = auto_now

                dictate_now = hotkey_satisfied(self.dictate_key, self._down)
                command_now = hotkey_satisfied(self.command_key, self._down)
                dictate_edge = dictate_now and not self._dictate_held
                command_edge = command_now and not self._command_held
                dictate_release = self._dictate_held and not dictate_now
                command_release = self._command_held and not command_now
                self._dictate_held, self._command_held = dictate_now, command_now

                toggle = self.conf["hotkeys"].get("mode", "hold") == "toggle"
                if toggle:
                    # Press to start, press again to stop; releases are ignored.
                    if dictate_edge:
                        if self.mode == "dictate":
                            self.finish()
                        elif self.mode is None:
                            self.begin("dictate")
                    elif command_edge:
                        if self.mode == "command":
                            self.finish()
                        elif self.mode is None:
                            self.begin("command")
                else:
                    if self.mode is None and dictate_edge:
                        self.begin("dictate")
                    elif self.mode is None and command_edge:
                        self.begin("command")
                    elif self.mode == "dictate" and dictate_release:
                        # Trust hardware over the event stream: a spurious
                        # key-up must not cut a long take short.
                        if keys_physically_held(self.dictate_key) is True:
                            self.log(f"[keep] ignored a phantom release of "
                                     f"{token} after "
                                     f"{time.time() - self._began_at:.0f}s — "
                                     f"keys are still physically down")
                            # Re-add the exact token that lied, so the real
                            # key-up still clears it later.
                            self._down.add(token)
                            self._dictate_held = True
                        else:
                            self.log(f"[end] released after "
                                     f"{time.time() - self._began_at:.1f}s "
                                     f"(key up: {token}, "
                                     f"still down: {sorted(self._down)})")
                            self.finish()
                    elif self.mode == "command" and command_release:
                        self.finish()
            except Exception as e:
                import traceback
                self.log(f"[error] event handling failed: {e!r}\n"
                         + traceback.format_exc())
                self.mode = None
                if self.busy.locked():
                    try:
                        self.busy.release()
                    except RuntimeError:
                        pass

    # ---------- hands-free auto transcribe ----------

    def set_auto(self, on):
        """Turn hands-free mode on or off (hotkey, menu bar, or config)."""
        on = bool(on)
        if on == self.auto_on:
            return
        self.auto_on = on
        self.log(f"[auto] {'ON — listening continuously' if on else 'OFF'}")
        self.hud("show_idle" if on else "hide")
        self.sound(SOUND_DONE if on else SOUND_START)
        if on:
            try:
                power.begin("VoiceFlow auto-transcribe is listening")
            except Exception:
                pass
        else:
            power.end()
        try:
            if self._menubar is not None:
                self._menubar.refresh_auto(on)
        except Exception:
            pass

    def _auto_loop(self):
        """Watch the mic level and cut it into utterances.

        Speech starts a take once the level holds above `start_level`; a pause
        longer than `end_silence` ends it and hands the audio to the normal
        transcribe → clean → paste pipeline.
        """
        FRAME = 0.05
        speaking = False
        voiced = silence = 0.0
        started = 0.0
        peak, last_beat = 0.0, 0.0
        while True:
            time.sleep(FRAME)
            try:
                if not self.auto_on:
                    if speaking:
                        # Switched off mid-sentence: transcribe what was said
                        # rather than discarding it. Dropping the take is the
                        # surprising behaviour — you spoke, so you expect text.
                        audio = self.recorder.stop()
                        speaking = False
                        self.mode = None
                        self.log("[auto] turned off mid-utterance — "
                                 "transcribing the last take")
                        # Deliberately do NOT re-show the HUD here. Off has to
                        # look off: re-showing it as "processing" made the pill
                        # reappear a frame after the toggle hid it, so a single
                        # press looked like it had done nothing. The take still
                        # transcribes and pastes — just without the indicator.
                        threading.Thread(target=self._process,
                                         args=("dictate", audio),
                                         daemon=True).start()
                    continue
                a = self.conf["auto"]
                level = self.recorder.level

                if not speaking:
                    # Heartbeat: shows whether the mic is reaching the speech
                    # threshold, and whether anything is blocking detection.
                    peak = max(peak, level)
                    if time.time() - last_beat >= 5.0:
                        blocked = ("manual take" if self.mode is not None
                                   else "busy" if self.busy.locked() else "no")
                        self.log(f"[auto] listening — peak level {peak:.2f} "
                                 f"(need {a['start_level']:.2f}), blocked: {blocked}")
                        peak, last_beat = 0.0, time.time()
                    # Never interrupt a manual take or an in-flight paste.
                    if self.mode is not None or self.busy.locked():
                        voiced = 0.0
                        continue
                    voiced = voiced + FRAME if level >= a["start_level"] else 0.0
                    if voiced >= a["start_duration"]:
                        if not self.busy.acquire(blocking=False):
                            voiced = 0.0
                            continue
                        self.mode = "auto"
                        self.target_app = frontmost_app_name()
                        self.selection = None
                        self._began_at = time.time()
                        self.recorder.start(preroll=a["preroll"])
                        self.hud("show_listening")
                        speaking, silence, started = True, 0.0, time.time()
                        self.log(f"[auto] speech detected (app: {self.target_app})")
                else:
                    silence = silence + FRAME if level < a["end_level"] else 0.0
                    overlong = time.time() - started >= a["max_seconds"]
                    if silence >= a["end_silence"] or overlong:
                        audio = self.recorder.stop()
                        speaking = False
                        self.mode = None
                        self.hud("show_processing")
                        self.log(f"[auto] utterance ended after "
                                 f"{time.time() - started:.1f}s"
                                 + (" (max length)" if overlong else ""))
                        threading.Thread(target=self._process,
                                         args=("dictate", audio),
                                         daemon=True).start()
            except Exception as e:
                self.log(f"[auto] error: {e}")
                speaking = False
                self.mode = None
                self._release_busy()

    def _release_busy(self):
        if self.busy.locked():
            try:
                self.busy.release()
            except RuntimeError:
                pass

    def _maybe_reload_config(self):
        """Hot-reload config.json / dictionary.json when they change on disk.
        Applies sounds, hotkeys, LLM, paste, and context settings live;
        STT model changes still need a restart."""
        changed = False
        for path in (cfg.CONFIG_PATH, cfg.DICTIONARY_PATH):
            try:
                mtime = os.stat(path).st_mtime
            except OSError:
                continue
            if self._config_mtimes.get(path) != mtime:
                self._config_mtimes[path] = mtime
                changed = True
        if not changed:
            return
        try:
            old_stt = self.conf["stt"]
            self.conf = cfg.load_config()
            self.dictionary = cfg.load_dictionary()
            self.cleanup.cfg = self.conf["llm"]
            self.dictate_key = parse_hotkey(self.conf["hotkeys"]["dictate"])
            self.command_key = parse_hotkey(self.conf["hotkeys"]["command"])
            hk_auto = self.conf["hotkeys"].get("auto")
            self.auto_key = parse_hotkey(hk_auto) if hk_auto else None
            self.log(f"[config] reloaded — sounds={'on' if self.conf['sounds'] else 'off'}, "
                     f"dictate=[{self.conf['hotkeys']['dictate']}], "
                     f"command=[{self.conf['hotkeys']['command']}]")
            if self.conf["stt"] != old_stt:
                self.log("[config] note: stt changes need a restart to take effect")
        except (SystemExit, Exception) as e:
            self.log(f"[config] reload failed, keeping previous settings: {e}")

    def begin(self, mode):
        if not self.busy.acquire(blocking=False):
            self.log("[busy] still processing the previous dictation — try again in a moment")
            return
        self.mode = mode
        self.target_app = frontmost_app_name()
        self.selection = None
        try:
            self.recorder.start()
        except Exception as e:
            self.log(f"[error] could not start recording: {e}")
            self.sound(SOUND_ERROR)
            self.mode = None
            self.busy.release()
            return
        self._began_at = time.time()
        # We swallow the hotkey, so macOS sees no input while it's held. Without
        # this the idle timer runs on and display sleep cuts the take short.
        try:
            power.begin()
        except Exception as e:
            self.log(f"[power] could not hold wake assertion: {e}")
        self.sound(SOUND_START)
        self.hud("show_listening")
        self.log(f"[{mode}] recording... (app: {self.target_app})")

    def finish(self):
        mode = self.mode
        audio = self.recorder.stop()
        self.mode = None
        power.end()
        self.hud("show_processing")
        # process off the listener thread so hotkeys stay responsive
        threading.Thread(target=self._process, args=(mode, audio), daemon=True).start()

    # ---------- pipeline ----------

    def _process(self, mode, audio):
        try:
            # Grab the selection for command mode via Cmd+C (guarded injection).
            if mode == "command":
                with self._inject_guard():
                    self.selection = copy_selection()
                if not self.selection:
                    self.log("[command] no text selected — select text first")
                    self.sound(SOUND_ERROR)
                    return
            min_samples = self.conf["audio"]["min_duration"] * self.conf["audio"]["sample_rate"]
            if audio is None or len(audio) < min_samples:
                self.log("[skip] recording too short")
                return
            t0 = time.time()
            transcript = self.transcriber.transcribe(audio)
            self.log(f"[stt {time.time() - t0:.1f}s] {transcript!r}")
            if not transcript:
                self.sound(SOUND_ERROR)
                return
            if mode == "command":
                final, do_enter = self._run_command(transcript)
            else:
                final, do_enter = self._run_dictation(transcript)
            if final is None:
                self.sound(SOUND_ERROR)
                return
            with self._inject_guard():
                paste_text(
                    final,
                    restore=self.conf["paste"]["restore_clipboard"],
                    restore_delay=self.conf["paste"]["restore_delay"],
                    then_enter=do_enter,
                )
            self.sound(SOUND_DONE)
            self.log(f"[pasted] {final!r}" + (" +enter" if do_enter else ""))
        finally:
            self.hud("show_idle" if self.auto_on else "hide")
            self.busy.release()

    @contextlib.contextmanager
    def _inject_guard(self):
        """Suppress capture of our own synthetic keystrokes, then resync
        the held-keys set so a missed/echoed modifier can't wedge the combo."""
        self._injecting = True
        try:
            yield
        finally:
            time.sleep(0.3)      # let trailing synthetic key events flush
            self._down.clear()   # forget everything; require a fresh press
            self._injecting = False

    def _run_dictation(self, transcript):
        snippet = match_snippet(transcript, self.dictionary["snippets"])
        if snippet is not None:
            self.log("[snippet] matched")
            return snippet, False
        text = apply_replacements(transcript, self.dictionary["replacements"])
        text, do_enter = extract_press_enter(text)
        llm = self.conf["llm"]
        fast = len(text.split()) <= llm["fast_mode_max_words"]
        if not llm["enabled"] or fast:
            if fast:
                self.log("[fast mode] skipping LLM cleanup")
            return text, do_enter
        style = style_for_app(self.target_app, self.conf["context"])
        t0 = time.time()
        try:
            cleaned, llm_enter = self.cleanup.cleanup(
                text, style=style, vocabulary=self.dictionary["vocabulary"]
            )
        except Exception as e:
            self.log(f"[llm error] {e} — pasting raw transcript")
            return text, do_enter
        self.log(f"[llm {time.time() - t0:.1f}s, style={style}] cleaned")
        cleaned = apply_replacements(cleaned, self.dictionary["replacements"])
        return cleaned, do_enter or llm_enter

    def _run_command(self, instruction):
        self.log(f"[command] transforming {len(self.selection)} chars")
        t0 = time.time()
        try:
            result, _ = self.cleanup.transform(self.selection, instruction)
        except Exception as e:
            self.log(f"[llm error] {e} — command mode needs the LLM, aborting")
            return None, False
        self.log(f"[llm {time.time() - t0:.1f}s] transformed")
        return result, False

    # ---------- lifecycle ----------

    def run(self):
        # Ask macOS for the two permissions up front. These calls pop the
        # native dialogs and register VoiceFlow in System Settings so its
        # toggles appear (a bare audio stream / key listener does not).
        from .permissions import request_accessibility, request_microphone
        mic = request_microphone()
        self.log(f"[permission] microphone: {mic}")
        trusted = request_accessibility(prompt=True)
        if trusted:
            self.log("[permission] accessibility: granted")
        else:
            self.log("[permission] accessibility: NOT granted — enable VoiceFlow in "
                     "System Settings > Privacy & Security > Accessibility")

        hk = self.conf["hotkeys"]
        self.dictate_key = parse_hotkey(hk["dictate"])
        self.command_key = parse_hotkey(hk["command"])
        self.auto_key = parse_hotkey(hk["auto"]) if hk.get("auto") else None
        for path in (cfg.CONFIG_PATH, cfg.DICTIONARY_PATH):
            try:
                self._config_mtimes[path] = os.stat(path).st_mtime
            except OSError:
                pass
        stt = self.conf["stt"]
        self.log(f"Loading Whisper model {stt['model']!r} (first run downloads it)...")
        from .stt import Transcriber
        self.transcriber = Transcriber(
            model=stt["model"],
            compute_type=stt["compute_type"],
            language=stt["language"],
            beam_size=stt["beam_size"],
            hotwords=self.dictionary["vocabulary"],
        )
        self.log("Model loaded.")
        mode = self.conf["hotkeys"].get("mode", "hold")
        self.log(f"Dictation mode: {'toggle (press on / press off)' if mode == 'toggle' else 'hold to talk'}")
        # Open the persistent mic stream now so the first dictation is instant
        # and any audio-device problem surfaces at launch, not mid-use.
        self.recorder._ensure_stream()
        self.log("Microphone stream open.")
        self.log(f"Hold [{hk['dictate']}] to dictate, release to paste.")
        self.log(f"Select text, hold [{hk['command']}] and speak an instruction to transform it.")
        self.log("Ctrl+C here to quit.\n")
        auto_hk = hk.get("auto")
        if auto_hk:
            self.log(f"Press [{auto_hk}] to toggle hands-free auto transcribe.")
        threading.Thread(target=self._auto_loop, daemon=True).start()
        if self.conf["auto"].get("start_on_launch"):
            self.set_auto(True)
        threading.Thread(target=self._event_loop, daemon=True).start()
        listener = keyboard.Listener(
            on_press=self.on_press,
            on_release=self.on_release,
            darwin_intercept=self._intercept,
        )
        listener.start()

        # Cocoa must own the main thread, so the HUD runs here and the key
        # listener runs on its own thread (pynput gives it its own run loop).
        ov = self.conf.get("overlay", {})
        if ov.get("enabled", True):
            try:
                from . import overlay, ui
                overlay.start(self.recorder, y_offset=float(ov.get("y_offset", 120)),
                              position=ov.get("position"),
                              locked=bool(ov.get("locked", False)))
                self._overlay = overlay
                self.log("Waveform overlay ready.")
                try:
                    self._menubar = ui.start(self)
                    self.log("Menu bar ready — click the waveform icon for Settings "
                             "(or open 'HuaJiaoDJ_VoiceFlow Settings.app').")
                except Exception as e:
                    self.log(f"[ui] menu bar unavailable ({e})")
                overlay.run_forever()
                return
            except Exception as e:
                self._overlay = None
                self.log(f"[overlay] disabled ({e}) — dictation still works")
        listener.join()


def check_llm(conf):
    """Warn at startup about an unreachable endpoint or a missing API key —
    both would otherwise only surface on the first dictation."""
    import requests
    from . import providers

    llm = conf["llm"]
    if not llm["enabled"]:
        return
    backend = llm["backend"]
    try:
        p = providers.provider(backend)
    except KeyError as e:
        print(f"[warn] {e}")
        return
    print(f"[llm] {backend} / {llm['model']}")
    if p.get("needs_key") and providers.key_status(backend, llm) == "MISSING":
        print(f"[warn] no API key for {backend} — cleanup will fall back to raw "
              f"transcripts. Store one with: ./run.sh --set-key {backend}")
        return
    url = providers.base_url(backend, llm)
    if not url:
        print(f"[warn] no base_url set for the {backend} backend")
        return
    if url.startswith("http://localhost") or url.startswith("http://127.0.0.1"):
        try:
            requests.get(url, timeout=2)
        except Exception:
            print(f"[warn] {backend} not reachable at {url} — "
                  "dictation will paste raw transcripts until it is running.")


def main():
    # `kill -USR1 <pid>` dumps all thread stacks to stderr — lets us find
    # exactly where a hang is without root/py-spy.
    faulthandler.register(signal.SIGUSR1)
    parser = argparse.ArgumentParser(description="HuaJiaoDJ_VoiceFlow: local push-to-talk dictation")
    parser.add_argument("--no-llm", action="store_true", help="skip LLM cleanup")
    parser.add_argument("--debug", action="store_true", help="log every raw key press/release")
    parser.add_argument("--settings", action="store_true",
                        help="open the Settings window (works with or without the daemon)")
    parser.add_argument("--show-config", action="store_true",
                        help="print the active LLM backend, model, and key status")
    parser.add_argument("--list-backends", action="store_true",
                        help="list available LLM backends")
    parser.add_argument("--list-models", nargs="?", const=True, metavar="BACKEND",
                        help="list models the backend can serve (default: active backend)")
    parser.add_argument("--set-backend", metavar="NAME",
                        help="switch LLM backend and save it to config.json")
    parser.add_argument("--set-model", metavar="NAME",
                        help="switch model and save it to config.json")
    parser.add_argument("--set-key", metavar="BACKEND",
                        help="store an API key for BACKEND in the macOS Keychain (prompts)")
    args = parser.parse_args()
    if args.settings:
        from . import ui
        ui.run_standalone()
        return
    if settings.handle_cli(args):
        return
    conf = cfg.load_config()
    dictionary = cfg.load_dictionary()
    if args.no_llm:
        conf["llm"]["enabled"] = False
    check_llm(conf)
    app = VoiceFlow(conf, dictionary)
    app.debug = args.debug
    try:
        app.run()
    except KeyboardInterrupt:
        print("\nbye")
        sys.exit(0)


if __name__ == "__main__":
    main()
