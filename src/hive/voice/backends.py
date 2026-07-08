"""Voice backends — STT (speech->text) and TTS (text->speech).

Mirrors the model-backend pattern (stub / ollama / anthropic):

- **StubVoiceBackend** (default): no models, no install. `transcribe` returns a
  configured canned string so the whole voice loop + tests run headless;
  `speak` returns a short valid WAV (a soft tone). This is what CI and offline
  development use.
- **LocalVoiceBackend**: real, local, $0 — faster-whisper (STT) + kokoro-onnx
  (TTS), both CPU, no torch, no ffmpeg. Enabled with HIVE_VOICE_BACKEND=local.
  Models load lazily on first use. Written against the documented APIs but,
  like AnthropicModelClient, unexercised until the models are installed.

Audio contract everywhere: 16 kHz mono for STT input; TTS returns a WAV byte
string. WAV encode/decode uses stdlib `wave` + numpy — no soundfile/ffmpeg.
"""

from __future__ import annotations

import io
import math
import struct
import wave
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from hive.config import HiveConfig

STT_SAMPLE_RATE = 16000


class VoiceBackend(Protocol):
    name: str

    def transcribe(self, wav_bytes: bytes) -> str: ...
    def speak(self, text: str) -> bytes: ...

    @property
    def ready(self) -> bool: ...


# -- WAV helpers (stdlib only) -------------------------------------------------


def pcm16_wav(samples: list[int] | "any", sample_rate: int) -> bytes:
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
    """faster-whisper (STT) + kokoro-onnx (TTS). CPU, local, free.

    Lazy-loaded: importing/constructing this is cheap; models load on first
    transcribe/speak. `ready` reports whether the model files are present
    without loading them, so the API can advertise voice status honestly.
    """

    name = "local"

    def __init__(
        self,
        whisper_model: str = "base",
        kokoro_voice: str = "af_heart",
        model_dir: str = "",
    ) -> None:
        self.whisper_model = whisper_model
        self.kokoro_voice = kokoro_voice
        self.model_dir = model_dir
        self._stt = None
        self._tts = None

    @property
    def ready(self) -> bool:
        try:
            import importlib.util
            from pathlib import Path

            has_whisper = importlib.util.find_spec("faster_whisper") is not None
            has_kokoro = importlib.util.find_spec("kokoro_onnx") is not None
            onnx = Path(self.model_dir) / "kokoro-v1.0.onnx"
            voices = Path(self.model_dir) / "voices-v1.0.bin"
            return has_whisper and has_kokoro and onnx.exists() and voices.exists()
        except Exception:
            return False

    def _stt_model(self):
        if self._stt is None:
            from faster_whisper import WhisperModel

            self._stt = WhisperModel(self.whisper_model, device="cpu", compute_type="int8")
        return self._stt

    def _tts_model(self):
        if self._tts is None:
            from pathlib import Path

            from kokoro_onnx import Kokoro

            self._tts = Kokoro(
                str(Path(self.model_dir) / "kokoro-v1.0.onnx"),
                str(Path(self.model_dir) / "voices-v1.0.bin"),
            )
        return self._tts

    def transcribe(self, wav_bytes: bytes) -> str:
        audio = read_wav_mono16k(wav_bytes)
        segments, _ = self._stt_model().transcribe(audio, language="en", vad_filter=True)
        return " ".join(seg.text for seg in segments).strip()

    def speak(self, text: str) -> bytes:
        import numpy as np

        samples, rate = self._tts_model().create(text, voice=self.kokoro_voice, speed=1.0, lang="en-us")
        pcm = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
        return pcm16_wav((pcm * 32767).astype(np.int16).tolist(), int(rate))


def make_voice_backend(config: "HiveConfig") -> VoiceBackend:
    if config.voice_backend == "local":
        return LocalVoiceBackend(
            whisper_model=config.whisper_model,
            kokoro_voice=config.kokoro_voice,
            model_dir=str(config.voice_model_dir),
        )
    return StubVoiceBackend()
