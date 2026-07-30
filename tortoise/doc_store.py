"""Document store — plain markdown files in ~/.tortoise/docs/.

Obsidian pattern: local-first, no lock-in, no second DB.
Subdirectories (convention, not enforced):
  conversations/ — agent conversation transcripts
  research/     — research briefs and findings
  entities/     — entity documentation

FalkorDB indexes metadata for search — this module is just filesystem I/O.
"""

import os
import tempfile
from pathlib import Path


class DocStore:
    """Markdown document store on the local filesystem.

    Usage:
        store = DocStore()
        store.write("conversations", "2026-07-22-session-42.md",
                     "# Session 42\\n\\nWhat we did...")
        docs = store.list("conversations")
    """

    def __init__(self, base_path: str | Path = "~/.tortoise/docs"):
        self.base_path = Path(base_path).expanduser().resolve()
        self._subdirs = ["conversations", "research", "entities"]
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        """Create base path and convention subdirectories if missing."""
        self.base_path.mkdir(parents=True, exist_ok=True)
        for sub in self._subdirs:
            (self.base_path / sub).mkdir(exist_ok=True)

    def _resolve(self, subdir: str, filename: str | None = None) -> Path:
        """Resolve a path under base_path. Rejects traversal attempts."""
        path = (self.base_path / subdir).resolve()
        if filename:
            path = (path / filename).resolve()
        # Guard against path traversal
        if not str(path).startswith(str(self.base_path)):
            raise ValueError(f"Path {path} escapes base_path {self.base_path}")
        return path

    def write(self, subdir: str, filename: str, content: str) -> Path:
        """Write a markdown file atomically. Creates subdir if missing. Returns file path.

        Uses tempfile + os.replace for atomic writes — concurrent writers
        always see a complete file from one writer or the other, never
        interleaved content.
        """
        dir_path = self._resolve(subdir)
        dir_path.mkdir(exist_ok=True)
        file_path = dir_path / filename
        # Write to a temp file in the same directory, then atomically replace.
        # os.replace is atomic on POSIX; on Windows it's a best-effort rename.
        with tempfile.NamedTemporaryFile(
            mode="w", dir=dir_path, delete=False, suffix=".tmp", encoding="utf-8"
        ) as tmp:
            tmp.write(content)
        os.replace(tmp.name, file_path)
        return file_path

    def read(self, subdir: str, filename: str) -> str:
        """Read a markdown file's content."""
        return self._resolve(subdir, filename).read_text()

    def list(self, subdir: str | None = None) -> list[str]:
        """List markdown filenames in a subdir. If subdir is None, list all."""
        if subdir:
            path = self._resolve(subdir)
            if not path.is_dir():
                return []
            return sorted(
                f.name for f in path.iterdir()
                if f.suffix == ".md" and f.is_file()
            )
        # List all files across all subdirectories
        results: list[str] = []
        for entry in sorted(self.base_path.iterdir()):
            if entry.is_dir():
                for f in sorted(entry.iterdir()):
                    if f.suffix == ".md" and f.is_file():
                        results.append(f"{entry.name}/{f.name}")
        return results

    def exists(self, subdir: str, filename: str) -> bool:
        """Check if a document exists."""
        return self._resolve(subdir, filename).is_file()

    def delete(self, subdir: str, filename: str) -> bool:
        """Delete a document. Returns True if it existed."""
        path = self._resolve(subdir, filename)
        if path.is_file():
            path.unlink()
            return True
        return False
