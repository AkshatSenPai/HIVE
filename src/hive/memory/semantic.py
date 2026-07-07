"""Semantic memory: the markdown vault (PRD §4).

Human-readable, Obsidian-compatible, shared with Zenith. Path safety is
non-negotiable — every access resolves inside the vault root or it raises.
"""

from __future__ import annotations

from pathlib import Path


class VaultPathError(Exception):
    pass


class Vault:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _safe(self, relative: str) -> Path:
        candidate = (self.root / relative).resolve()
        if not candidate.is_relative_to(self.root):
            raise VaultPathError(f"path escapes vault: {relative}")
        return candidate

    def read(self, relative: str) -> str:
        path = self._safe(relative)
        if not path.exists():
            raise FileNotFoundError(relative)
        return path.read_text(encoding="utf-8")

    def write(self, relative: str, content: str) -> Path:
        path = self._safe(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def exists(self, relative: str) -> bool:
        try:
            return self._safe(relative).exists()
        except VaultPathError:
            return False

    def list(self, subdir: str = ".") -> list[str]:
        base = self._safe(subdir)
        if not base.exists():
            return []
        # Forward slashes regardless of OS — these paths travel through the API.
        return sorted(
            p.relative_to(self.root).as_posix() for p in base.rglob("*.md") if p.is_file()
        )
