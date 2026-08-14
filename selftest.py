"""End-to-end pipeline check without mic or hotkeys.

Synthesizes speech with macOS `say`, transcribes it with faster-whisper,
then runs the LLM cleanup. Run: .venv/bin/python selftest.py
"""
import os
import subprocess
import sys
import tempfile
import time

from faster_whisper import decode_audio

from voiceflow import config as cfg
from voiceflow.cleanup import CleanupEngine
from voiceflow.rules import apply_replacements, extract_press_enter, match_snippet
from voiceflow.stt import Transcriber

PHRASE = ("Um, so hey, can you, uh, send me the report by Friday? "
          "Actually no wait, make that Thursday. Thanks!")


def main():
    conf = cfg.load_config()
    dictionary = cfg.load_dictionary()

    print("1. Synthesizing test speech with `say`...")
    with tempfile.TemporaryDirectory() as td:
        aiff = os.path.join(td, "t.aiff")
        subprocess.run(["say", "-o", aiff, PHRASE], check=True)
        audio = decode_audio(aiff, sampling_rate=16000)

    print(f"2. Loading Whisper model {conf['stt']['model']!r}...")
    t0 = time.time()
    transcriber = Transcriber(
        model=conf["stt"]["model"],
        compute_type=conf["stt"]["compute_type"],
        language=conf["stt"]["language"],
        beam_size=conf["stt"]["beam_size"],
        hotwords=dictionary["vocabulary"],
    )
    print(f"   loaded in {time.time() - t0:.1f}s")

    t0 = time.time()
    transcript = transcriber.transcribe(audio)
    print(f"3. Transcript ({time.time() - t0:.1f}s): {transcript!r}")
    if not transcript:
        sys.exit("FAIL: empty transcript")

    assert match_snippet("my email", dictionary["snippets"])
    assert extract_press_enter("hello world press enter")[1] is True
    assert apply_replacements("send the jason file", {"jason": "JSON"}) == "send the JSON file"
    print("4. Rules (snippets / replacements / press-enter): OK")

    print(f"5. LLM cleanup via {conf['llm']['backend']} ({conf['llm']['model']})...")
    engine = CleanupEngine(conf["llm"])
    t0 = time.time()
    cleaned, press = engine.cleanup(transcript, style="email",
                                    vocabulary=dictionary["vocabulary"])
    print(f"   cleaned ({time.time() - t0:.1f}s): {cleaned!r} press_enter={press}")

    print("\nSELFTEST PASSED")


if __name__ == "__main__":
    main()
