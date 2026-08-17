import collections
import math
import threading

import numpy as np
import sounddevice as sd


class Recorder:
    """Push-to-talk capture over a persistent input stream.

    The stream is opened once and kept running; start()/stop() only toggle
    whether the callback buffers audio. Repeatedly opening and closing
    CoreAudio streams (the previous design) can hang Pa_StopStream on macOS
    after a few cycles, which froze the whole app.
    """

    def __init__(self, sample_rate=16000, device=None):
        self.sample_rate = sample_rate
        self.device = device
        self._chunks = []
        self._capturing = False
        self._stream = None
        self._lock = threading.Lock()
        # Rolling loudness history (0..1) for the waveform overlay. Updated on
        # every callback whether or not we're capturing, so the HUD reacts the
        # instant it appears.
        self._levels = collections.deque([0.0] * 96, maxlen=96)
        # Always-on ring of the most recent audio. Auto-transcribe starts
        # recording only *after* it hears speech, so without this pre-roll the
        # first syllable would already be gone.
        self._ring = collections.deque()
        self._ring_samples = 0
        self._ring_max = int(sample_rate * 1.5)

    def _callback(self, indata, frames, time_info, status):
        rms = float(np.sqrt(np.mean(np.square(indata), dtype=np.float64)))
        # Speech RMS is tiny and very peaky; a log curve makes quiet speech
        # visible without the loud parts pinning the meter at full scale.
        level = 0.0 if rms <= 1e-6 else min(1.0, max(0.0, (math.log10(rms) + 3.0) / 2.4))
        chunk = indata.copy()
        with self._lock:
            self._levels.append(level)
            self._ring.append(chunk)
            self._ring_samples += len(chunk)
            while self._ring_samples - len(self._ring[0]) >= self._ring_max:
                self._ring_samples -= len(self._ring.popleft())
            if self._capturing:
                self._chunks.append(chunk)

    def snapshot(self):
        """Audio captured so far in the current take, without ending it.
        Used for live preview transcription while the user is still talking.

        The concatenate happens *outside* the lock. Joining a long take can
        take milliseconds, and the mic callback needs the same lock to store
        the next buffer — holding it that long drops incoming audio.
        """
        with self._lock:
            if not self._capturing or not self._chunks:
                return None
            chunks = list(self._chunks)      # cheap: references, not data
        return np.concatenate(chunks, axis=0).flatten()

    def levels(self):
        """Recent loudness history, oldest first, each 0..1."""
        with self._lock:
            return list(self._levels)

    @property
    def level(self):
        with self._lock:
            return self._levels[-1] if self._levels else 0.0

    def _ensure_stream(self):
        if self._stream is not None:
            return
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            device=self.device,
            callback=self._callback,
        )
        self._stream.start()

    def start(self, preroll=0.0):
        """Begin buffering. `preroll` seconds of already-captured audio are
        prepended, so speech that triggered the start isn't lost."""
        self._ensure_stream()
        with self._lock:
            self._chunks = []
            if preroll > 0.0 and self._ring:
                want = int(self.sample_rate * preroll)
                taken, got = [], 0
                for chunk in reversed(self._ring):
                    taken.append(chunk)
                    got += len(chunk)
                    if got >= want:
                        break
                self._chunks = list(reversed(taken))
            self._capturing = True

    def stop(self):
        """Returns mono float32 audio, or None if nothing was captured."""
        with self._lock:
            self._capturing = False
            if not self._chunks:
                return None
            audio = np.concatenate(self._chunks, axis=0)
            self._chunks = []
        return audio.flatten()

    @property
    def recording(self):
        return self._capturing
