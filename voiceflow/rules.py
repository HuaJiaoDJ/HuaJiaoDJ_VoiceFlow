import re
import string


def _normalize(text):
    return text.lower().translate(str.maketrans("", "", string.punctuation)).strip()


def match_snippet(transcript, snippets):
    """If the whole utterance is a snippet trigger, return the expansion."""
    normalized = _normalize(transcript)
    for trigger, expansion in snippets.items():
        if normalized == _normalize(trigger):
            return expansion
    return None


def apply_replacements(text, replacements):
    """Deterministic word/phrase replacements, case-insensitive."""
    for wrong, right in replacements.items():
        text = re.sub(r"\b" + re.escape(wrong) + r"\b", right, text, flags=re.IGNORECASE)
    return text


_PRESS_ENTER_RE = re.compile(r"[,.\s]*(?:and\s+)?(?:press|hit)\s+enter[.!?\s]*$", re.IGNORECASE)


def extract_press_enter(text):
    """Detects a trailing 'press enter' command. Returns (text, press_enter)."""
    match = _PRESS_ENTER_RE.search(text)
    if match and match.start() > 0:
        return text[: match.start()].rstrip() , True
    return text, False
