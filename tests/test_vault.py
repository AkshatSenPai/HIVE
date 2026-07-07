import pytest

from hive.memory.semantic import Vault, VaultPathError


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
