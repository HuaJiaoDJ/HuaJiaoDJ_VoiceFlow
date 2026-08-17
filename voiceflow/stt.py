from faster_whisper import WhisperModel


class Transcriber:
    def __init__(self, model="small", compute_type="int8", language=None,
                 beam_size=1, hotwords=None):
        self.language = language
        self.beam_size = beam_size
        self.hotwords = " ".join(hotwords) if hotwords else None
        self.model = WhisperModel(model, device="cpu", compute_type=compute_type)

    def transcribe(self, audio):
        """audio: mono float32 numpy array at 16kHz. Returns plain text."""
        segments, _info = self.model.transcribe(
            audio,
            language=self.language,
            beam_size=self.beam_size,
            temperature=0.0,
            condition_on_previous_text=False,
            vad_filter=True,
            hotwords=self.hotwords,
        )
        return " ".join(seg.text.strip() for seg in segments).strip()

    def transcribe_words(self, audio):
        """Same, but returns [(word, end_seconds), ...].

        Live captions need to know *when* each word was spoken so they can
        commit finished speech by audio position. Diffing successive
        transcriptions by text does not work: the model rewords earlier parts
        between passes, the match fails, and whole phrases get shown twice.
        """
        segments, _info = self.model.transcribe(
            audio,
            language=self.language,
            beam_size=self.beam_size,
            temperature=0.0,
            condition_on_previous_text=False,
            vad_filter=True,
            hotwords=self.hotwords,
            word_timestamps=True,
        )
        out = []
        for seg in segments:
            for w in (seg.words or []):
                text = w.word.strip()
                if text:
                    out.append((text, float(w.end)))
        return out
