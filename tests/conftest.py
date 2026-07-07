from pathlib import Path

import pytest

from hive.adapter import load_adapter
from hive.agents.model import StubModelClient
from hive.config import HiveConfig
from hive.runtime import Runtime

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ADAPTER = REPO_ROOT / "adapters" / "example"


@pytest.fixture
def adapter():
    return load_adapter(EXAMPLE_ADAPTER)


@pytest.fixture
def runtime(tmp_path):
    config = HiveConfig(adapter_dir=EXAMPLE_ADAPTER, data_dir=tmp_path / ".hive")
    return Runtime(config, model=StubModelClient())
