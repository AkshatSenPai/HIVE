"""Model backend selection: stub (default) / ollama (local, $0) / anthropic."""

import json
import urllib.request

import pytest

from hive.agents.model import OllamaModelClient, StubModelClient
from hive.config import HiveConfig
from hive.runtime import Runtime
from tests.conftest import EXAMPLE_ADAPTER


def make_runtime(tmp_path, **cfg) -> Runtime:
    return Runtime(HiveConfig(adapter_dir=EXAMPLE_ADAPTER, data_dir=tmp_path / ".hive", **cfg))


def test_default_backend_is_stub(tmp_path):
    rt = make_runtime(tmp_path)
    assert isinstance(rt.model, StubModelClient)
    assert rt.config.use_llm is False


def test_ollama_backend_selected(tmp_path):
    rt = make_runtime(tmp_path, model_backend="ollama", ollama_model="mistral:7b-instruct-q4_K_M")
    assert isinstance(rt.model, OllamaModelClient)
    assert rt.config.use_llm is False  # local = free, not "live paid LLM"
    # every tier routes to the local model
    assert set(rt.model.routing.values()) == {"mistral:7b-instruct-q4_K_M"}


def test_env_selection(monkeypatch, tmp_path):
    monkeypatch.setenv("HIVE_MODEL_BACKEND", "ollama")
    monkeypatch.setenv("HIVE_OLLAMA_MODEL", "llama3.2")
    config = HiveConfig.from_env()
    assert config.model_backend == "ollama"
    assert config.ollama_model == "llama3.2"


def test_use_llm_env_backcompat(monkeypatch):
    monkeypatch.setenv("HIVE_USE_LLM", "1")
    assert HiveConfig.from_env().model_backend == "anthropic"


def _ollama_running() -> bool:
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2) as raw:
            json.loads(raw.read())
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _ollama_running(), reason="ollama not running locally")
def test_ollama_live_completion():
    """Integration: a real local completion, $0. Skips when Ollama is down."""
    client = OllamaModelClient()
    response = client.complete("extractor", "You are terse.", "Reply with the single word: ready")
    assert response.usd == 0.0
    assert response.model.startswith("ollama/")
    assert response.text.strip() != ""
    assert response.output_tokens > 0
