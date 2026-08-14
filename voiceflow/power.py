"""Keep the Mac awake while dictating.

VoiceFlow swallows its own hotkey so the character never reaches the focused
app — which also means macOS sees *no user input* for the whole time you hold
it. The idle timer keeps counting, and display sleep / screen saver kicks in
mid-sentence, releasing the modifiers and cutting the recording short.

Holding an IOKit power assertion for the duration of a take fixes that.
"""
from Foundation import NSBundle

_iokit = None
_assertion_id = None


def _lib():
    """Bind the two IOKit calls we need (PyObjC has no IOKit wrapper)."""
    global _iokit
    if _iokit is None:
        import objc
        bundle = NSBundle.bundleWithPath_(
            "/System/Library/Frameworks/IOKit.framework")
        functions = [
            ("IOPMAssertionCreateWithName", b"i@i@o^I"),
            ("IOPMAssertionRelease", b"iI"),
        ]
        namespace = {}
        objc.loadBundleFunctions(bundle, namespace, functions)
        _iokit = namespace
    return _iokit


def begin(reason="HuaJiaoDJ_VoiceFlow is recording"):
    """Prevent idle display sleep until end() is called. Safe to call twice."""
    global _assertion_id
    if _assertion_id is not None:
        return
    try:
        fn = _lib().get("IOPMAssertionCreateWithName")
        if fn is None:
            return
        # kIOPMAssertionTypeNoDisplaySleep, kIOPMAssertionLevelOn
        err, aid = fn("NoDisplaySleepAssertion", 255, reason, None)
        if err == 0:
            _assertion_id = aid
    except Exception:
        _assertion_id = None  # never let power management break dictation


def end():
    global _assertion_id
    if _assertion_id is None:
        return
    try:
        fn = _lib().get("IOPMAssertionRelease")
        if fn is not None:
            fn(_assertion_id)
    except Exception:
        pass
    finally:
        _assertion_id = None
