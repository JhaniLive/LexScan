"""Speech to text, locally.

The mic button records in the browser and Chainlit hands the raw PCM back here.
Transcription runs on this machine with faster-whisper — no audio leaves the
box, which matters when someone is describing a police matter or a family
dispute out loud.

Configured in .env:

    STT_MODEL     tiny | base | small | medium   (default: base)
    STT_LANGUAGE  e.g. en, hi, kn — blank lets Whisper detect it
    STT_DEVICE    cpu | cuda                     (default: cpu)
"""

import os

from dotenv import load_dotenv

load_dotenv()

MODEL_SIZE = os.getenv("STT_MODEL", "base").strip()
LANGUAGE = os.getenv("STT_LANGUAGE", "").strip() or None
DEVICE = os.getenv("STT_DEVICE", "cpu").strip()
COMPUTE_TYPE = os.getenv("STT_COMPUTE", "int8").strip()

# Live captions run on a smaller model than the final pass: they are redrawn
# every couple of seconds while someone is still speaking, so latency matters
# more than the last few percent of accuracy. The accurate model still gets the
# final say on the text that actually gets acted on.
LIVE_MODEL_SIZE = os.getenv("STT_LIVE_MODEL", "tiny").strip()
LIVE_ENABLED = os.getenv("STT_LIVE", "true").strip().lower() not in ("0", "false", "no")

# How often to redraw the caption, and how much audio one pass may cover.
LIVE_INTERVAL_SECONDS = float(os.getenv("STT_LIVE_INTERVAL", "1.6"))
LIVE_WINDOW_SECONDS = float(os.getenv("STT_LIVE_WINDOW", "12"))

# Below this, the recording is a mis-click rather than a sentence.
MIN_SECONDS = 0.4

_model = None
_live_model = None


class SpeechUnavailable(RuntimeError):
    pass


def available():
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def _get_model():
    """Load the model once, on first use. The first call downloads it."""
    global _model
    if _model is None:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise SpeechUnavailable(
                "Voice input needs the `faster-whisper` package — install it with "
                "`pip install faster-whisper`."
            )
        try:
            _model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE)
        except Exception as e:
            raise SpeechUnavailable(f"Could not load the '{MODEL_SIZE}' speech model: {e}")
    return _model


def _get_live_model():
    """The small model used for captions while someone is still speaking."""
    global _live_model
    if LIVE_MODEL_SIZE == MODEL_SIZE:
        return _get_model()
    if _live_model is None:
        from faster_whisper import WhisperModel

        _live_model = WhisperModel(
            LIVE_MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE
        )
    return _live_model


def warm_up():
    """Load the live model ahead of time so the first caption isn't slow."""
    if not LIVE_ENABLED:
        return
    try:
        _get_live_model()
    except Exception:
        pass


def _to_float32(pcm_bytes):
    """Chainlit sends signed 16-bit PCM; Whisper wants float32 in [-1, 1]."""
    import numpy as np

    if len(pcm_bytes) % 2:
        pcm_bytes = pcm_bytes[:-1]
    samples = np.frombuffer(pcm_bytes, dtype=np.int16)
    return samples.astype(np.float32) / 32768.0


def _resample(audio, source_rate, target_rate=16000):
    """Whisper expects 16 kHz; the browser records at whatever the config says."""
    if source_rate == target_rate or audio.size == 0:
        return audio

    import numpy as np

    duration = audio.size / source_rate
    target_length = int(duration * target_rate)
    if target_length <= 0:
        return audio
    positions = np.linspace(0, audio.size - 1, target_length)
    return np.interp(positions, np.arange(audio.size), audio).astype("float32")


def _run(audio, seconds):
    """Feed prepared audio to Whisper and join what comes back."""
    model = _get_model()
    segments, info = model.transcribe(
        audio,
        language=LANGUAGE,
        beam_size=5,
        vad_filter=True,          # drop the silence either side of the speech
        condition_on_previous_text=False,
    )
    text = " ".join(segment.text.strip() for segment in segments).strip()
    return text, getattr(info, "language", None), seconds


def transcribe_pcm(pcm_bytes, sample_rate=24000):
    """Transcribe raw 16-bit PCM. Returns (text, detected_language, seconds)."""
    audio = _to_float32(pcm_bytes)
    seconds = audio.size / sample_rate if sample_rate else 0.0

    if seconds < MIN_SECONDS:
        return "", None, seconds

    return _run(_resample(audio, sample_rate), seconds)


def _looks_encoded(data, mime):
    """True when the bytes are a container (webm/ogg/mp4/wav), not bare samples."""
    if mime and not any(tag in mime.lower() for tag in ("pcm", "raw", "l16")):
        if any(tag in mime.lower() for tag in ("webm", "ogg", "mp4", "mpeg", "wav", "opus")):
            return True

    # Trust the bytes over the label — browsers are inconsistent about the mime.
    return data[:4] in (b"\x1aE\xdf\xa3", b"OggS", b"RIFF") or data[4:8] == b"ftyp"


def transcribe_encoded(data):
    """Transcribe an encoded clip (webm/ogg/mp4/wav) by decoding it first."""
    import tempfile
    import os

    handle, path = tempfile.mkstemp(suffix=".audio", prefix="lexscan-voice-")
    try:
        with os.fdopen(handle, "wb") as f:
            f.write(data)
        # faster-whisper decodes container formats itself, via PyAV.
        text, language, _ = _run(path, 0.0)
        return text, language
    except Exception as e:
        raise SpeechUnavailable(f"Could not decode that recording: {e}")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


class LiveTranscriber:
    """Rolling transcription of speech that is still arriving.

    Audio comes in as it is recorded. Every couple of seconds the pending window
    is re-transcribed with the small model and the caption is redrawn, so words
    appear while someone is still talking.

    The window is bounded: once it grows past LIVE_WINDOW_SECONDS the text so far
    is committed and the audio dropped, which keeps each pass the same cost
    whether the recording is ten seconds or ten minutes. Committing mid-phrase
    can clip a word — that is why this drives the caption only, and the finished
    recording is transcribed once more, in full, by the accurate model.
    """

    def __init__(self, sample_rate=24000):
        self.sample_rate = sample_rate
        self.committed = ""
        self.interim = ""
        self.pending = b""
        self.seconds_committed = 0.0
        self._since_last_pass = 0.0

    def feed(self, pcm_bytes):
        """Add newly recorded audio. Returns True when a caption pass is due."""
        self.pending += pcm_bytes
        # 16-bit mono: two bytes per sample.
        self._since_last_pass += len(pcm_bytes) / 2 / self.sample_rate
        return self._since_last_pass >= LIVE_INTERVAL_SECONDS

    @property
    def pending_seconds(self):
        return len(self.pending) / 2 / self.sample_rate

    def caption(self):
        """The full caption to display right now."""
        return " ".join(part for part in (self.committed, self.interim) if part).strip()

    def step(self):
        """Transcribe the pending window. Blocking — call it off the event loop."""
        self._since_last_pass = 0.0

        if self.pending_seconds < MIN_SECONDS:
            return self.caption()

        audio = _resample(_to_float32(self.pending), self.sample_rate)
        model = _get_live_model()
        segments, _ = model.transcribe(
            audio,
            language=LANGUAGE,
            beam_size=1,               # greedy: this pass is thrown away shortly
            vad_filter=True,
            condition_on_previous_text=False,
        )
        self.interim = " ".join(s.text.strip() for s in segments).strip()

        # Window full — freeze what we have and start a fresh one.
        if self.pending_seconds >= LIVE_WINDOW_SECONDS:
            if self.interim:
                self.committed = f"{self.committed} {self.interim}".strip()
            self.seconds_committed += self.pending_seconds
            self.pending = b""
            self.interim = ""

        return self.caption()

    def total_seconds(self):
        return self.seconds_committed + self.pending_seconds


def transcribe_audio(data, sample_rate=24000, mime=""):
    """Transcribe a recording, whether it arrived as raw PCM or encoded.

    Chrome, Firefox and Safari do not agree on what the microphone stream is
    handed over as, so both shapes are handled rather than assumed.
    """
    if not data:
        return "", None, 0.0

    if _looks_encoded(data, mime):
        text, language = transcribe_encoded(data)
        # No frame count to measure with; estimate from a typical bitrate.
        return text, language, len(data) / 4000.0

    return transcribe_pcm(data, sample_rate)
