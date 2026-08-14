"""Request macOS Accessibility and Microphone permissions using the official
TCC APIs. Calling these pops the native system dialogs and — importantly —
registers the app in System Settings so its toggles actually appear.
"""


def request_accessibility(prompt=True):
    """Return True if this process is trusted for Accessibility (input
    monitoring). When prompt=True and it isn't, macOS shows the standard
    'wants to control this computer' dialog and adds the app to the list."""
    try:
        from ApplicationServices import (
            AXIsProcessTrustedWithOptions,
            kAXTrustedCheckOptionPrompt,
        )
        options = {kAXTrustedCheckOptionPrompt: bool(prompt)}
        return bool(AXIsProcessTrustedWithOptions(options))
    except Exception:
        try:
            from ApplicationServices import AXIsProcessTrusted
            return bool(AXIsProcessTrusted())
        except Exception:
            return True  # non-macOS or API missing: don't block startup


def request_microphone():
    """Trigger the microphone permission prompt and register the app in the
    Microphone list. Returns a status string: authorized / denied / prompted."""
    try:
        import AVFoundation
        media = AVFoundation.AVMediaTypeAudio
        status = AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(media)
        # 0 notDetermined, 1 restricted, 2 denied, 3 authorized
        if status == 3:
            return "authorized"
        if status == 0:
            # Fire the prompt; the callback is async, we don't block on it.
            AVFoundation.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
                media, lambda granted: None
            )
            return "prompted"
        return "denied"
    except Exception as e:
        return f"unknown ({e})"
