import os

import pytest

from hive.config import HiveConfig
from hive.memory.semantic import LAYOUT_DIRS, MAP_FILE, Vault, VaultPathError
from hive.runtime import Runtime
from tests.conftest import EXAMPLE_ADAPTER


def test_write_read_roundtrip(tmp_path):
    vault = Vault(tmp_path / "vault")
    vault.write("clients/acme.md", "# Acme")
    assert vault.read("clients/acme.md") == "# Acme"
    assert vault.list() == ["clients/acme.md"]  # posix paths on every OS


def test_path_escape_blocked(tmp_path):
    vault = Vault(tmp_path / "vault")
    with pytest.raises(VaultPathError):
        vault.write("../outside.md", "nope")
    with pytest.raises(VaultPathError):
        vault.read("..\\..\\secrets.txt")


# -- layout, note listing, no-overwrite writes (vault_read plan, task 1) -------

def test_layout_created_once_and_map_never_overwritten(tmp_path):
    vault = Vault(tmp_path / "vault")
    vault.ensure_layout()
    for folder in LAYOUT_DIRS:
        assert (vault.root / folder).is_dir()
    assert "outputs/" in vault.read(MAP_FILE)
    (vault.root / MAP_FILE).write_text("owner edit", encoding="utf-8")
    vault.ensure_layout()  # e.g. HIVE restarting
    assert vault.read(MAP_FILE) == "owner edit"


def test_notes_newest_first_and_missing_is_empty(tmp_path):
    vault = Vault(tmp_path / "vault")
    old = vault.write("wiki/a.md", "old")
    new = vault.write("wiki/b.md", "new")
    vault.write("wiki/picture.png", "not a note")
    os.utime(old, (100, 100))
    os.utime(new, (200, 200))
    assert vault.notes("wiki") == ["wiki/b.md", "wiki/a.md"]
    assert vault.notes("wiki/a.md") == ["wiki/a.md"]
    assert vault.notes("outputs/none") == []


def test_notes_refuses_escape(tmp_path):
    vault = Vault(tmp_path / "vault")
    with pytest.raises(VaultPathError):
        vault.notes("../outside")


def test_write_new_never_overwrites(tmp_path):
    vault = Vault(tmp_path / "vault")
    vault.write_new("outputs/x.md", "first")
    with pytest.raises(FileExistsError):
        vault.write_new("outputs/x.md", "second")
    assert vault.read("outputs/x.md") == "first"


def test_runtime_lays_out_the_vault(tmp_path):
    rt = Runtime(HiveConfig(adapter_dir=EXAMPLE_ADAPTER, data_dir=tmp_path / ".hive"))
    assert (rt.vault.root / MAP_FILE).is_file()
    assert all((rt.vault.root / folder).is_dir() for folder in LAYOUT_DIRS)
