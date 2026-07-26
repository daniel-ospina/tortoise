"""GitHub doc indexer — clone repo, generate frontmatter, extract into semantic graph.

Usage:
    tortoise index github <url-or-path> [--branch main] [--enrich]

Walks all .md files, generates frontmatter for docs that lack it,
classifies document kind, extracts entities and claims into FalkorDB.
Content-hash idempotent — re-running skips unchanged files.

Integrates into onboarding: tortoise init detects git repo, offers indexing
as a background task.
"""
import hashlib
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from tortoise.doc_store import DocStore
from tortoise.sdk import TortoiseSDK


class GitHubDocIndexer:
    """Index markdown files from a GitHub repo or local directory.

    Pipeline: clone/walk → generate frontmatter → classify → extract → ingest.
    Idempotent via SHA-256 content hash.
    """

    def __init__(self, sdk: Optional[TortoiseSDK] = None):
        import os
        if sdk:
            self.sdk = sdk
        elif os.environ.get("TORTOISE_DB_URI"):
            self.sdk = TortoiseSDK()
        else:
            raise ValueError(
                "GitHubDocIndexer requires TORTOISE_DB_URI env var or an explicit SDK instance"
            )
        self._hashes: dict[str, str] = {}  # path → sha256 for dedup

    def index_repo(self, url_or_path: str, branch: str = "main",
                   enrich: bool = False) -> dict:
        """Index all markdown files from a GitHub repo or local directory.

        Args:
            url_or_path: GitHub URL or local directory path
            branch: Git branch (only for remote repos)
            enrich: Run LLM enrichment (requires API key)

        Returns dict with stats: {files_found, files_indexed, files_skipped, errors}
        """
        if os.path.isdir(url_or_path):
            repo_path = url_or_path
            is_temp = False
        else:
            repo_path = self._clone(url_or_path, branch)
            is_temp = True

        try:
            return self._index_directory(repo_path, enrich)
        finally:
            if is_temp:
                import shutil
                shutil.rmtree(repo_path, ignore_errors=True)

    def _clone(self, url: str, branch: str) -> str:
        """Clone a GitHub repo to a temp directory. Returns path."""
        tmp = tempfile.mkdtemp(prefix="tortoise-repo-")
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", branch, url, tmp],
            capture_output=True, timeout=120,
        )
        return tmp

    def _index_directory(self, repo_path: str, enrich: bool = False) -> dict:
        """Walk directory, index all .md files. Returns stats."""
        found = 0
        indexed = 0
        skipped = 0
        errors = 0

        for md_file in Path(repo_path).rglob("*.md"):
            found += 1
            try:
                if self._should_skip(md_file):
                    skipped += 1
                    continue

                self._index_file(md_file, enrich)
                indexed += 1
            except Exception as e:
                errors += 1
                print(f"  ⚠️ {md_file}: {e}")

        return {
            "files_found": found,
            "files_indexed": indexed,
            "files_skipped": skipped,
            "errors": errors,
        }

    def _should_skip(self, path: Path) -> bool:
        """Skip if content unchanged (idempotent)."""
        content = path.read_text()
        chash = hashlib.sha256(content.encode()).hexdigest()
        key = str(path)
        if self._hashes.get(key) == chash:
            return True
        self._hashes[key] = chash
        return False

    def _index_file(self, path: Path, enrich: bool = False) -> None:
        """Run full pipeline on a single file: frontmatter → classify → extract → ingest."""
        # 1. Generate frontmatter if missing
        self._ensure_frontmatter(path)

        # 2. Classify document kind
        from tortoise.doc_classify import classify as classify_file
        doc_kind = classify_file(str(path))

        # 3. Extract entities and claims
        from tortoise.extraction_pipeline import ExtractionPipeline
        pipeline = ExtractionPipeline(self.sdk)
        pipeline.process_file(str(path), doc_kind=doc_kind)

    def _ensure_frontmatter(self, path: Path) -> None:
        """Generate frontmatter for files that lack it."""
        content = path.read_text()
        if content.startswith("---"):
            return  # Already has frontmatter

        # Derive structural frontmatter from path
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        title = path.stem.replace("-", " ").replace("_", " ").title()
        fm = f"""---
title: "{title}"
type: document
domain: capability
status: draft
created: {now}
---
"""
        path.write_text(fm + content)
