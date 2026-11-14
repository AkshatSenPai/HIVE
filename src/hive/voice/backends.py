"""Voice backends — STT (speech->text) and TTS (text->speech).

Mirrors the model-backend pattern (stub / ollama / anthropic):

- **StubVoiceBackend** (default): no models, no install. `transcribe` returns a
  configured canned string so the whole voice loop + tests run headless;
  `speak` returns a short valid WAV (a soft tone). This is what CI and offline
  development use.
- **LocalVoiceBackend**: real, local, $0 — faster-whisper (STT) + kokoro (TTS),
  the same stack as Zenith. CPU by default. Enabled with HIVE_VOICE_BACKEND=local.
  Both models auto-download from HuggingFace on first use (Kokoro-82M ~330 MB;
  the Whisper weights per HIVE_WHISPER_MODEL). kokoro is torch-driven and (with
  spacy/blis) needs Python 3.11 — install the [voice] extra into a 3.11 venv
  (see pyproject). Models load lazily on first transcribe/speak.

Audio contract everywhere: 16 kHz mono for STT input; TTS returns a WAV byte
string. The stub + the STT decode path use stdlib `wave` + numpy (no ffmpeg);
the local Kokoro TTS writes its 24 kHz WAV via soundfile.
"""

from __future__ import annotations

import io
import math
import os
import struct
import wave
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from hive.config import HiveConfig

STT_SAMPLE_RATE = 16000
KOKORO_SAMPLE_RATE = 24000  # Kokoro outputs 24 kHz audio


class VoiceBackend(Protocol):
    name: str

    def transcribe(self, wav_bytes: bytes) -> str: ...
    def speak(self, text: str) -> bytes: ...

    @property
    def ready(self) -> bool: ...


# -- WAV helpers (stdlib only) -------------------------------------------------


def pcm16_wav(samples: list[int], sample_rate: int) -> bytes:
    """Write mono 16-bit PCM samples (ints in [-32768, 32767]) to a WAV byte string."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(struct.pack("<%dh" % len(samples), *samples))
    return buf.getvalue()


def read_wav_mono16k(wav_bytes: bytes):
    """Decode a WAV byte string to a float32 numpy array at 16 kHz mono.
    Accepts any sample rate / channel count and normalizes."""
    import numpy as np

    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        channels = w.getnchannels()
        width = w.getsampwidth()
        rate = w.getframerate()
        frames = w.readframes(w.getnframes())

    if width != 2:
        raise ValueError(f"expected 16-bit PCM WAV, got {width * 8}-bit")
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:  # average channels down to mono
        audio = audio.reshape(-1, channels).mean(axis=1)
    if rate != STT_SAMPLE_RATE and audio.size:  # linear resample to 16 kHz
        n_out = int(round(audio.size * STT_SAMPLE_RATE / rate))
        if n_out > 0:
            audio = np.interp(
                np.linspace(0, audio.size - 1, n_out, dtype=np.float32),
                np.arange(audio.size, dtype=np.float32),
                audio,
            ).astype(np.float32)
    return audio


# -- stub ----------------------------------------------------------------------


class StubVoiceBackend:
    """Offline voice: canned transcript, a soft tone for speech. $0, no install."""

    name = "stub"

    def __init__(self, canned_transcript: str = "give me a brief") -> None:
        self.canned_transcript = canned_transcript

    @property
    def ready(self) -> bool:
        return True

    def transcribe(self, wav_bytes: bytes) -> str:
        return self.canned_transcript

    def speak(self, text: str) -> bytes:
        # ~350 ms 440 Hz tone, amplitude scaled down — a placeholder voice.
        rate, secs, freq = 22050, 0.35, 440.0
        samples = [
            int(9000 * math.sin(2 * math.pi * freq * (i / rate)))
            for i in range(int(rate * secs))
        ]
        return pcm16_wav(samples, rate)


# -- local (real models) -------------------------------------------------------


class LocalVoiceBackend:
    """faster-whisper (STT) + kokoro (TTS). CPU, local, free — Zenith's stack.

    Lazy-loaded: constructing this is cheap; the STT model and the Kokoro
    pipeline load on first transcribe/speak (and download from HuggingFace on
    the very first call). `ready` reports whether the voice packages are
    importable — without loading models — so the API can advertise voice status
    honestly (stub vs live) without paying the load cost.
    """

    name = "local"

    def __init__(
        self,
        whisper_model: str = "base",
        kokoro_voice: str = "af_heart",
        kokoro_lang: str = "a",
        model_dir: str = "",
    ) -> None:
        self.whisper_model = whisper_model
        self.kokoro_voice = kokoro_voice
        self.kokoro_lang = kokoro_lang  # a=American, b=British English
        self.model_dir = model_dir      # optional dir for the models; "" => shared HF cache
        # Point both models' HuggingFace cache at model_dir when asked. Must be set
        # HERE (at construction) — before faster-whisper/kokoro import huggingface_hub,
        # which freezes its cache path at import time — so the override actually takes.
        if model_dir:
            os.environ.setdefault("HF_HOME", model_dir)
        self._stt = None
        self._tts = None

    @property
    def ready(self) -> bool:
        """True when the voice packages (STT + TTS + WAV I/O) are all importable.
        The models auto-download on first use, so dependency-presence is the honest
        'is voice live' signal. Never raises — a stub-only interpreter reports
        not-ready. soundfile is checked too: it is a separate PyPI package that
        `speak()` needs but neither kokoro nor faster-whisper pulls in."""
        try:
            import importlib.util as u

            return all(
                u.find_spec(pkg) is not None
                for pkg in ("faster_whisper", "kokoro", "soundfile")
            )
        except Exception:
            return False

    def _stt_model(self):
        if self._stt is None:
            from faster_whisper import WhisperModel

            self._stt = WhisperModel(self.whisper_model, device="cpu", compute_type="int8")
        return self._stt

    def _tts_pipeline(self):
        """Load the Kokoro pipeline once (downloads Kokoro-82M from HF on first
        call). Tries the richest constructor first and falls back as kwargs vary
        across kokoro versions, so a minor bump can't brick startup (Zenith rule)."""
        if self._tts is None:
            from kokoro import KPipeline

            for kwargs in (
                {"lang_code": self.kokoro_lang, "repo_id": "hexgrad/Kokoro-82M", "device": "cpu"},
                {"lang_code": self.kokoro_lang, "device": "cpu"},
                {"lang_code": self.kokoro_lang},
            ):
                try:
                    self._tts = KPipeline(**kwargs)
                    break
                except TypeError:
                    continue
            else:  # pragma: no cover — all signatures rejected
                self._tts = KPipeline(lang_code=self.kokoro_lang)
        return self._tts

    def transcribe(self, wav_bytes: bytes) -> str:
        audio = read_wav_mono16k(wav_bytes)
        if audio.size == 0:  # a stray mic tap / silence -> no speech, not an error (Zenith rule)
            return ""
        segments, _ = self._stt_model().transcribe(
            audio,
            language="en",
            beam_size=5,                       # beam search — Zenith's accuracy setting
            vad_filter=True,                   # skip silence
            condition_on_previous_text=False,  # curb hallucinated / looping output (Zenith)
        )
        return "".join(seg.text for seg in segments).strip()  # seg.text is space-prefixed

    def speak(self, text: str) -> bytes:
        """Kokoro -> 24 kHz 16-bit WAV bytes. Kokoro yields (graphemes, phonemes,
        audio) per chunk; concatenate the audio tensors and write one WAV."""
        import numpy as np
        import soundfile as sf

        pipe = self._tts_pipeline()
        chunks = [audio for _, _, audio in pipe(text, voice=self.kokoro_voice)]
        if not chunks:
            return pcm16_wav([], KOKORO_SAMPLE_RATE)  # nothing to say -> valid empty WAV
        samples = np.concatenate([
            c.detach().cpu().numpy() if hasattr(c, "detach") else np.asarray(c)
            for c in chunks
        ])
        buf = io.BytesIO()
        sf.write(buf, samples, KOKORO_SAMPLE_RATE, format="WAV", subtype="PCM_16")
        return buf.getvalue()


def make_voice_backend(config: "HiveConfig") -> VoiceBackend:
    if config.voice_backend == "local":
        model_dir = config.voice_model_dir
        return LocalVoiceBackend(
            whisper_model=config.whisper_model,
            kokoro_voice=config.kokoro_voice,
            kokoro_lang=config.kokoro_lang,
            model_dir=str(model_dir) if model_dir else "",
        )
    return StubVoiceBackend()
