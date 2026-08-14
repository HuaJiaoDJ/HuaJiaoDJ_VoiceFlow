import subprocess
import time

from pynput.keyboard import Controller, Key

_keyboard = Controller()


def get_clipboard():
    try:
        return subprocess.run(["pbpaste"], capture_output=True, timeout=5).stdout.decode(
            "utf-8", errors="replace"
        )
    except Exception:
        return None


def set_clipboard(text):
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), timeout=5)


def send_paste():
    with _keyboard.pressed(Key.cmd):
        _keyboard.tap("v")


def send_copy():
    with _keyboard.pressed(Key.cmd):
        _keyboard.tap("c")


def press_enter():
    _keyboard.tap(Key.enter)


def paste_text(text, restore=True, restore_delay=1.0, then_enter=False):
    """Copy text to clipboard, Cmd+V into the active app, restore old clipboard."""
    previous = get_clipboard() if restore else None
    set_clipboard(text)
    time.sleep(0.1)
    send_paste()
    if then_enter:
        time.sleep(0.15)
        press_enter()
    if restore and previous is not None:
        time.sleep(restore_delay)
        set_clipboard(previous)


def copy_selection():
    """Cmd+C the current selection and return it, or None if nothing copied."""
    marker = "\x00voiceflow-no-selection\x00"
    set_clipboard(marker)
    time.sleep(0.1)
    send_copy()
    time.sleep(0.25)
    text = get_clipboard()
    if text is None or text == marker:
        return None
    return text
