"""Owner-channel voice: stub/local backend selection, WAV helpers, the spoken
brief, and the /voice + /brief endpoints. All run on the stub — no models."""

import io
import wave

from fastapi.testclient import TestClient

from hive.agents.model import StubModelClient
from hive.api import create_app
from hive.config import HiveConfig
from hive.events.bus import Event
from hive.governance.digest import build_brief
from hive.runtime import Runtime
from hive.voice.backends import (
    LocalVoiceBackend,
    StubVoiceBackend,
    make_voice_backend,
    read_wav_mono16k,
)
from tests.conftest import EXAMPLE_ADAPTER


def make_runtime(tmp_path, **cfg) -> Runtime:
    config = HiveConfig(adapter_dir=EXAMPLE_ADAPTER, data_dir=tmp_path / ".hive", **cfg)
    return Runtime(config, model=StubModelClient())


def lead(subject="Acme") -> Event:
    return Event(type="lead.new", metadata={
        "subject": subject, "raw_context": "2400 sq yd", "reply_to": "b@acme.example",
    })


# -- backend selection ---------------------------------------------------------


def test_default_voice_backend_is_stub(tmp_path):
    rt = make_runtime(tmp_path)
    assert rt.voice.name == "stub"
    assert rt.voice.ready is True


def test_local_backend_ready_reflects_dep_availability(tmp_path):
    rt = make_runtime(tmp_path, voice_backend="local")
    assert isinstance(rt.voice, LocalVoiceBackend)
    # `ready` = the voice packages (STT + TTS + WAV I/O) all importable; it must be
    # a bool and never raise, whether or not the [voice] extra is installed here.
    import importlib.util as u

    expected = all(
        u.find_spec(pkg) is not None
        for pkg in ("faster_whisper", "kokoro", "soundfile")
    )
    assert isinstance(rt.voice.ready, bool)
    assert rt.voice.ready is expected


def test_make_voice_backend_from_config(tmp_path):
    stub_cfg = HiveConfig(adapter_dir=EXAMPLE_ADAPTER, data_dir=tmp_path / ".h1")
    assert make_voice_backend(stub_cfg).name == "stub"
    local_cfg = HiveConfig(adapter_dir=EXAMPLE_ADAPTER, data_dir=tmp_path / ".h2", voice_backend="local")
    assert make_voice_backend(local_cfg).name == "local"


# -- stub backend + WAV helpers ------------------------------------------------


def test_stub_transcribe_returns_canned():
    assert StubVoiceBackend("approve").transcribe(b"anything") == "approve"


def test_stub_speak_returns_valid_wav():
    wav = StubVoiceBackend().speak("hello hive")
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    with wave.open(io.BytesIO(wav), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getnframes() > 0


def test_read_wav_normalizes_to_16k_mono():
    wav = StubVoiceBackend().speak("x")  # 22.05 kHz mono tone
    audio = read_wav_mono16k(wav)
    assert audio.ndim == 1
    assert audio.dtype.name == "float32"
    assert -1.0 <= float(audio.min()) and float(audio.max()) <= 1.0
    assert audio.size > 0


# -- build_brief ---------------------------------------------------------------


def test_brief_all_clear_on_empty(tmp_path):
    rt = make_runtime(tmp_path)
    text = build_brief(rt.store, rt.adapter.policies)
    assert "All clear" in text
    assert "spent 0 cents today" in text


def test_brief_reports_pending_approval_and_spend(tmp_path):
    rt = make_runtime(tmp_path)
    rt.trigger(lead())  # -> one pending send approval
    text = build_brief(rt.store, rt.adapter.policies)
    assert "1 approval waiting" in text
    assert "Waiting for you:" in text
    assert "daily cap" in text  # example adapter sets a global_daily cap
    assert "cents" in text or "dollars" in text


def test_money_phrase_singular_and_bad_cap(tmp_path):
    from hive.governance.digest import _money_phrase, build_brief

    assert _money_phrase(1.0) == "1 dollar"       # singular, not "1 dollars"
    assert _money_phrase(2.0) == "2 dollars"
    assert _money_phrase(0.01) == "1 cent"
    assert _money_phrase(0.62) == "62 cents"
    assert _money_phrase(18000.0) == "18,000 dollars"
    # a misconfigured (non-numeric) daily cap must not 500 the brief
    rt = make_runtime(tmp_path)
    rt.adapter.policies.setdefault("budgets", {})["global_daily"] = {"max_usd": "oops"}
    text = build_brief(rt.store, rt.adapter.policies)  # must not raise
    assert "spent" in text and "daily cap" not in text  # cap skipped, not crashed


def test_brief_reports_attention(tmp_path):
    rt = make_runtime(tmp_path)
    # force a job into ESCALATED via the global-daily brake
    rt.trigger(lead("first"))
    j1 = rt.store.list_jobs()[0]
    rt.adapter.policies.setdefault("budgets", {})["global_daily"] = {"max_usd": j1.spend_usd / 2}
    rt.trigger(lead("second"))
    text = build_brief(rt.store, rt.adapter.policies)
    assert "needing attention" in text
    assert "Needs attention:" in text


# -- endpoints -----------------------------------------------------------------


def test_brief_endpoint(tmp_path):
    rt = make_runtime(tmp_path)
    client = TestClient(create_app(rt))
    body = client.get("/brief").json()
    assert "All clear" in body["text"]


def test_system_reports_voice_status(tmp_path):
    rt = make_runtime(tmp_path)
    voice = client_get_system(rt)["voice"]
    assert voice == {"backend": "stub", "ready": True}


def client_get_system(rt) -> dict:
    return TestClient(create_app(rt)).get("/system").json()


def test_voice_speak_endpoint_returns_wav(tmp_path):
    rt = make_runtime(tmp_path)
    client = TestClient(create_app(rt))
    resp = client.post("/voice/speak", json={"text": "you have one approval waiting"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/wav"
    assert resp.content[:4] == b"RIFF"


def test_voice_transcribe_endpoint(tmp_path):
    # stub transcribe returns its canned string regardless of audio
    rt = make_runtime(tmp_path, )
    rt.voice = StubVoiceBackend("review approvals")
    client = TestClient(create_app(rt))
    wav = StubVoiceBackend().speak("ignored")
    resp = client.post("/voice/transcribe", content=wav, headers={"content-type": "audio/wav"})
    assert resp.status_code == 200
    assert resp.json()["text"] == "review approvals"


# -- local backend round-trip (real models; auto-skips without the [voice] extra) --


def test_local_roundtrip_kokoro_to_whisper():
    """text -> Kokoro (TTS) -> WAV -> Whisper (STT) -> text. Verifies the real
    local pipeline with no live mic. Auto-skips unless the [voice] extra is
    installed (a Python 3.11 venv); the first run downloads the models from HF."""
    import pytest

    pytest.importorskip("faster_whisper")
    pytest.importorskip("kokoro")
    pytest.importorskip("soundfile")

    backend = LocalVoiceBackend(whisper_model="base", kokoro_voice="af_heart")
    assert backend.ready is True

    phrase = "the quick brown fox jumps over the lazy dog"
    try:  # first run downloads models from HuggingFace — skip (don't fail) if unreachable
        wav = backend.speak(phrase)
        heard = backend.transcribe(wav).lower()
    except Exception as exc:  # noqa: BLE001 — a network/model-load failure isn't a code defect
        pytest.skip(f"voice models unavailable: {type(exc).__name__}: {exc}")

    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    # Whisper won't be word-perfect; assert a few distinctive words survive.
    hits = sum(word in heard for word in ("quick", "brown", "fox", "lazy", "dog"))
    assert hits >= 3, f"round-trip lost too much: {heard!r}"
