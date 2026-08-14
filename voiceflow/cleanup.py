import json
import re

import requests

from . import providers

STYLE_GUIDES = {
    "default": "Natural, clear written text.",
    "chat": "Casual chat message. Lowercase-friendly, relaxed punctuation, keep it short.",
    "email": "Professional email prose. Complete sentences, proper punctuation.",
    "docs": "Clear document writing. Well-structured sentences and paragraphs.",
    "code": "Technical writing. Keep identifiers, commands, and file names verbatim.",
    "ai_prompt": "A prompt for an AI assistant. Keep instructions precise and complete.",
}

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "press_enter": {"type": "boolean"},
    },
    "required": ["text", "press_enter"],
}

CLEANUP_PROMPT = """You are a dictation cleanup engine. Convert the raw speech transcript into text ready to paste.

Rules:
- Return only the final text in the "text" field.
- Remove filler words (um, uh, like, you know) and false starts.
- Add punctuation and capitalization.
- Preserve the speaker's meaning and language. Do not translate.
- Handle backtracking such as "actually", "no wait", "scratch that": keep only the corrected version.
- Do not add facts, do not answer questions, do not expand on the content.
- Keep the style: {style}.
- Spell these terms exactly as written when they appear: {vocabulary}.
- If the speaker ends by saying "press enter" or "hit enter", remove that phrase and set "press_enter" to true. Otherwise set it to false.

Raw transcript:
{transcript}"""

TRANSFORM_PROMPT = """You are a text editing engine. Apply the spoken instruction to the given text and return the result.

Rules:
- Return only the edited text in the "text" field. Set "press_enter" to false.
- Preserve the original language and formatting style unless the instruction says otherwise.
- Do not add commentary or explanations.

Instruction (from speech):
{instruction}

Text to edit:
{text}"""


class CleanupEngine:
    def __init__(self, llm_cfg):
        self.cfg = llm_cfg

    def cleanup(self, transcript, style="default", vocabulary=()):
        prompt = CLEANUP_PROMPT.format(
            style=STYLE_GUIDES.get(style, STYLE_GUIDES["default"]),
            vocabulary=", ".join(vocabulary) or "(none)",
            transcript=transcript,
        )
        return self._generate(prompt)

    def transform(self, text, instruction):
        prompt = TRANSFORM_PROMPT.format(instruction=instruction, text=text)
        return self._generate(prompt)

    def _generate(self, prompt):
        """Returns (text, press_enter). Raises on connection/parse failure."""
        backend = self.cfg["backend"]
        kind = providers.provider(backend)["kind"]
        if kind == "ollama":
            return self._ollama(prompt, backend)
        if kind == "anthropic":
            return self._anthropic(prompt, backend)
        return self._openai(prompt, backend)

    @staticmethod
    def _parse(raw):
        """Structured output should be exact JSON; salvage a wrapped object if
        a provider adds prose around it."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if not match:
                raise
            data = json.loads(match.group(0))
        return data["text"], bool(data.get("press_enter"))

    def _ollama(self, prompt, backend):
        url = providers.base_url(backend, self.cfg)
        resp = requests.post(
            f"{url}/api/chat",
            json={
                "model": self.cfg["model"],
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "format": OUTPUT_SCHEMA,
                "think": False,
                "keep_alive": self.cfg.get("keep_alive", "30m"),
                "options": {"temperature": 0.2},
            },
            timeout=self.cfg["timeout"],
        )
        resp.raise_for_status()
        return self._parse(resp.json()["message"]["content"])

    def _openai(self, prompt, backend):
        url = providers.base_url(backend, self.cfg)
        key = providers.resolve_api_key(backend, self.cfg)
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        resp = requests.post(
            f"{url}/chat/completions",
            headers=headers,
            json={
                "model": self.cfg["model"],
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "dictation", "schema": OUTPUT_SCHEMA},
                },
            },
            timeout=self.cfg["timeout"],
        )
        resp.raise_for_status()
        return self._parse(resp.json()["choices"][0]["message"]["content"])

    def _anthropic(self, prompt, backend):
        import anthropic  # optional dependency; only needed for this backend

        key = providers.resolve_api_key(backend, self.cfg)
        if not key:
            raise RuntimeError(
                "No Anthropic API key. Store one with: ./run.sh --set-key anthropic"
            )
        client = anthropic.Anthropic(api_key=key, timeout=self.cfg["timeout"])
        message = client.messages.create(
            model=self.cfg["model"],
            max_tokens=2000,
            # Structured output guarantees the {text, press_enter} shape.
            # Low effort keeps thinking short — dictation needs sub-second turnaround.
            output_config={
                "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
                "effort": "low",
            },
            messages=[{"role": "user", "content": prompt}],
        )
        if message.stop_reason == "refusal":
            raise RuntimeError("Anthropic declined this request")
        text = next(b.text for b in message.content if b.type == "text")
        return self._parse(text)
