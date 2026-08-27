"""GitHub ``docs/`` → staged corpus fetcher (#1726 / #1714 Slice 1).

Remote ``docs/`` folders become corpus documents (Sources only — NO claim
extraction; that is deferred follow-up #1724).

Phase 1 (fetch): Contents-API walk reusing the indexer httpx fetch pattern
(rate-limit backoff + pagination — ``GitHubIndexer._get``):
``GET /repos/{repo}/git/trees/{branch}?recursive=1`` (the GitHub recursion
cap's ``truncated`` flag is surfaced honestly in stats), filtered to
``docs/`` blobs, incremental **tree-by-sha** (an unchanged tree short-
circuits the whole walk), per-path **blob-sha dedup** (only changed blobs
are fetched — ``GET /repos/{repo}/git/blobs/{sha}``, base64 → UTF-8).

Phase 2 (stage): changed blobs are written under
``{TORTOISE_INGEST_BASE_DIR}/{team_id}/{repo}/...`` (team-partitioned —
team A blobs are never picked up by team B, T2-P2b). The staged corpus is
then ingested by the deterministic corpus pipeline (``index_directory`` —
``compute_file_hash`` dedup, ``derive_document_id``, classification), so an
unchanged re-ingest produces 0 new nodes (falsification (f)).

Input guards (T1-P16 + cycle-3, folded):
- **text-type guard**: binary / non-UTF-8 blobs are SKIPPED with an honest
  skipped count (never staged — the corpus pipeline is text-only).
- **max-blob-size constant**: oversized blobs are skipped BEFORE the fetch
  (the recursive-tree entry carries ``size``) with an honest skipped count.
- **atomic-or-reconciled staging**: per-file writes are atomic (temp +
  rename); a mid-walk failure cleans up THIS run's staged files (no half-
  fetched content for the next corpus pass) and the manifest is only
  updated on full success; files that disappear from the tree are removed
  on the next walk (reconciled — no stale files).

Fail-closed: the fetcher refuses to walk when ``TORTOISE_INGEST_BASE_DIR``
is unset (defense in depth — the hosted job is the primary tenant gate).
Every path segment written to disk is validated (no ``..`` / absolute
leak) — staging is server-owned by construction, never user-supplied.

Token scope: ``repo`` (covers the Contents API).
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from tortoise.indexer.github_indexer import (
    _GITHUB_API,
    GitHubFetchError,
    GitHubIndexer,
)

logger = logging.getLogger(__name__)

# Max staged blob size (input guard, T1-P16): a blob larger than this is
# skipped with an honest status — bounded well below TORTOISE_MAX_FILE_MB
# (50 MiB default) so the deterministic corpus pipeline's own size guard
# never trips on a file this fetcher staged.
MAX_DOCS_BLOB_BYTES = 1024 * 1024  # 1 MiB

# Path-segment validation for server-owned staging: team_id / repo
# segments must be conservative safe tokens (no traversal, no slashes).
_SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

# Branch fallback order (an explicit branch 404 → probe the defaults).
_BRANCH_FALLBACKS = ("master", "main")


def _safe_segment(value: str, what: str) -> str:
    if not _SAFE_SEGMENT_RE.match(value):
        raise GitHubFetchError(
            f"unsafe {what} segment {value!r} for ingest staging (rejected)")
    return value


class GitHubDocsIndexer(GitHubIndexer):
    """Contents-API docs fetcher: tree walk → changed-blob fetch → stage.

    Reuses ``GitHubIndexer``'s httpx fetch layer (``_get`` bounded
    retry-with-backoff on 429/5xx, terminal 401/403 ⇒ ``GitHubFetchError``).
    """

    # ── staging layout ─────────────────────────────────────────────

    @staticmethod
    def _ingest_base() -> str:
        """The server-owned sandbox root — fail-closed when unset."""
        raw = os.environ.get("TORTOISE_INGEST_BASE_DIR", "").strip()
        if not raw:
            raise GitHubFetchError(
                "TORTOISE_INGEST_BASE_DIR is not set — docs indexing "
                "requires a server-owned ingest sandbox; no staging writes "
                "performed")
        return os.path.realpath(os.path.expanduser(raw))

    @classmethod
    def team_root(cls, team_id: str) -> Path:
        """{base}/{team_id} — the team-partitioned ingest root.

        This is the CORPUS ROOT for the deterministic ingest: rel-paths
        embed ``{owner}/{repo}/docs/...`` so doc ids are REPO-UNIQUE (two
        repos with identical docs paths never share a Document node — the
        derive_document_id path-collision edge)."""
        base = cls._ingest_base()
        return Path(base) / _safe_segment(team_id, "team_id")

    @classmethod
    def _team_paths(cls, team_id: str, repo: str) -> tuple[Path, Path]:
        """(corpus_dir, manifest_path) under the team partition.

        corpus_dir = {base}/{team_id}/{owner}/{repo} — the per-repo fetch
        target (files at corpus_dir/docs/...)
        manifest   = {base}/{team_id}/.manifest/{repo}.json (server-owned
        meta, OUTSIDE the indexed file set — non-md, ignored by the walk).
        """
        team = cls.team_root(team_id)
        parts = repo.split("/", 1)
        if len(parts) != 2:
            raise GitHubFetchError(f"invalid repo full-name {repo!r}")
        owner, name = parts
        _safe_segment(owner, "owner")
        _safe_segment(name, "repo")
        return (team / owner / name,
                team / ".manifest" / owner / f"{name}.json")

    @staticmethod
    def _load_manifest(manifest_path: Path) -> dict:
        if not manifest_path.is_file():
            return {}
        try:
            with open(manifest_path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _write_manifest(manifest_path: Path, manifest: dict) -> None:
        """Atomic manifest write (temp + rename) — never a partial manifest
        for the next run to misread."""
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = manifest_path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(manifest, f, sort_keys=True)
        os.replace(tmp, manifest_path)

    # ── tree walk ──────────────────────────────────────────────────

    async def _get_tree(self, client, repo: str,
                        branch: str) -> tuple[dict, str, bool]:
        """Fetch the recursive tree for a branch (with fallback probes).

        Returns (tree_json, branch_used, fell_back). Both probes 404 ⇒
        GitHubFetchError (honest fail — the job reports it, never a silent
        empty walk).
        """
        candidates = [branch]
        for fb in _BRANCH_FALLBACKS:
            if fb not in candidates:
                candidates.append(fb)
        fell_back = False
        last_status = None
        for cand in candidates:
            url = (f"{_GITHUB_API}/repos/{repo}/git/trees/"
                   f"{cand}?recursive=1")
            r = await self._get(client, url)
            if r.status_code == 200:
                tree = r.json()
                if not tree.get("sha"):
                    raise GitHubFetchError(
                        f"tree response missing sha for {repo}@{cand}")
                return tree, cand, fell_back
            last_status = r.status_code
            fell_back = True
        raise GitHubFetchError(
            f"no usable branch for {repo} (branch {branch!r} → "
            f"{last_status})")

    @staticmethod
    def _docs_entries(tree: dict) -> list[dict]:
        """Recursive-tree blob entries under ``docs/`` (path-sorted)."""
        entries = []
        for e in tree.get("tree") or []:
            if e.get("type") != "blob":
                continue
            path = e.get("path") or ""
            if path == "docs" or not path.startswith("docs/"):
                continue
            entries.append(e)
        return sorted(entries, key=lambda e: e.get("path", ""))

    # ── staging ────────────────────────────────────────────────────

    @staticmethod
    def _stage_path(corpus_dir: Path, entry_path: str) -> Path:
        """Corpus-relative target for a tree path — traversal-guarded.

        The tree path is split into segments and re-joined under the corpus
        dir; a ``..``/absolute segment or an out-of-root resolution is a
        hard error (defense in depth — the path originates from GitHub, but
        server-owned staging never trusts remote input).
        """
        if ".." in Path(entry_path).parts:
            raise GitHubFetchError(
                f"unsafe tree path {entry_path!r} (contains '..')")
        target = corpus_dir.joinpath(*Path(entry_path).parts)
        try:
            target.relative_to(corpus_dir)
        except ValueError:
            raise GitHubFetchError(
                f"tree path {entry_path!r} escapes the corpus dir") from None
        return target

    async def _fetch_blob(self, client, repo: str, entry: dict) -> bytes:
        """Fetch one blob (base64 → raw bytes)."""
        sha = entry.get("sha")
        if not sha:
            raise GitHubFetchError(f"blob entry missing sha: {entry}")
        r = await self._get(
            client, f"{_GITHUB_API}/repos/{repo}/git/blobs/{sha}")
        if r.status_code != 200:
            raise GitHubFetchError(
                f"GitHub blob fetch failed ({r.status_code}) for {repo} "
                f"{entry.get('path')}")
        payload = r.json()
        content = payload.get("content") or ""
        if payload.get("encoding") == "base64":
            try:
                return base64.b64decode(content)
            except (ValueError, TypeError) as e:
                raise GitHubFetchError(
                    f"blob base64 decode failed for {repo} "
                    f"{entry.get('path')}: {e}") from e
        return content.encode("utf-8") if isinstance(content, str) else content

    # ── top-level entry ────────────────────────────────────────────

    async def walk_repo(self, team_id: str, repo: str, *,
                        branch: str = "main") -> dict:
        """Fetch + stage ONE repo's ``docs/`` under the team partition.

        Returns stats (see the module docstring for the guard semantics).
        Raises GitHubFetchError on any failure — the caller marks the job
        failed with a readable error; the manifest is never advanced past a
        failed run (re-run resumes without gaps).
        """
        stats: dict[str, Any] = {
            "repo": repo, "branch": branch, "tree_sha": None,
            "tree_changed": True, "tree_truncated": False,
            "branch_fell_back": False, "docs_entries": 0,
            "blobs_fetched": 0, "blobs_unchanged": 0,
            "skipped_binary": 0, "skipped_oversized": 0,
            "files_staged": 0, "files_reconciled_removed": 0,
            "staged_corpus": "", "errors": [],
        }
        corpus_dir, manifest_path = self._team_paths(team_id, repo)
        stats["staged_corpus"] = str(corpus_dir)
        manifest = self._load_manifest(manifest_path)
        client = await self._get_client()
        pending: list[tuple[Path, Path]] = []  # (temp, target) staged this run
        try:
            tree, branch_used, fell_back = await self._get_tree(
                client, repo, branch)
            stats["branch"] = branch_used
            stats["branch_fell_back"] = fell_back
            stats["tree_sha"] = tree.get("sha")
            stats["tree_truncated"] = bool(tree.get("truncated"))
            entries = self._docs_entries(tree)
            stats["docs_entries"] = len(entries)

            # Fix 5: never short-circuit a truncated tree (partial view)
            if (not tree.get("truncated")
                    and tree.get("sha") == manifest.get("tree_sha")
                    and branch_used == manifest.get("branch")):
                stats["tree_changed"] = False
                await self._close()
                return stats

            manifest_blobs = manifest.get("blobs") or {}
            kept: dict[str, str] = {}  # path -> sha we will keep staged on disk
            for entry in entries:
                path = entry.get("path", "")
                sha = entry.get("sha")
                try:
                    size = int(entry.get("size") or 0)
                except (TypeError, ValueError):
                    size = 0
                # Fix 2a: oversized -> skip (NOT kept; stale file removed at
                # commit — never a staged file whose manifest sha was never
                # fetched).
                if size > MAX_DOCS_BLOB_BYTES:
                    stats["skipped_oversized"] += 1
                    continue
                # per-path blob-sha dedup — unchanged blob, no fetch.
                if manifest_blobs.get(path) == sha:
                    stats["blobs_unchanged"] += 1
                    kept[path] = sha
                    continue
                data = await self._fetch_blob(client, repo, entry)
                # text-type guard: binary / non-UTF-8 blobs are skipped with
                # an honest count (never staged — the corpus pipeline is
                # text-only). NUL bytes catch UTF-8-decodable binary.
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    stats["skipped_binary"] += 1
                    continue
                if "\x00" in text:
                    stats["skipped_binary"] += 1
                    continue
                target = self._stage_path(corpus_dir, path)
                tmp = target.with_name(f".{target.name}.staging")
                target.parent.mkdir(parents=True, exist_ok=True)
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(text)
                pending.append((tmp, target))
                kept[path] = sha
                stats["blobs_fetched"] += 1
                stats["files_staged"] += 1

            # ── COMMIT (only reached on full success) ──
            # 1. atomically move staged temp files into place.
            for tmp, target in pending:
                os.replace(tmp, target)
            # 2. remove stale files: any path the OLD manifest knew about that
            #    is no longer kept (deleted from tree, OR now skipped as
            #    oversized/binary) — Fix 2b. EXCEPT on a truncated tree: the
            #    recursive view is PARTIAL, so a path absent from the listing
            #    may simply sit beyond the truncation point — carry it forward
            #    instead of unlinking (never destroy corpus files we cannot
            #    see; Fix 5 × 2b interaction).
            if tree.get("truncated"):
                for old_path, old_sha in manifest_blobs.items():
                    if old_path not in kept:
                        kept[old_path] = old_sha
            else:
                for old_path in manifest_blobs:
                    if old_path in kept:
                        continue
                    stale = self._stage_path(corpus_dir, old_path)
                    try:
                        if stale.is_file():
                            stale.unlink()
                            stats["files_reconciled_removed"] += 1
                    except OSError as e:
                        stats["errors"].append(
                            f"reconcile unlink {old_path}: {e}")

            # Manifest only on FULL success — a failed run re-walks from the
            # old manifest (refetching everything changed since; idempotent).
            # ``kept`` records ONLY staged/unchanged paths — skipped blobs are
            # excluded so their stale files are reconciled next run.
            manifest = {
                "tree_sha": tree.get("sha"),
                "branch": branch_used,
                "blobs": kept,
            }
            self._write_manifest(manifest_path, manifest)
            await self._close()
            return stats
        except Exception as e:
            # Fix 1: on failure, discard ONLY the temp staging files; existing
            # corpus files + the manifest are left exactly as pre-run (a prior
            # successful corpus is never destroyed by a failed re-walk).
            for tmp, _target in pending:
                try:
                    if tmp.exists():
                        tmp.unlink()
                except OSError as ue:
                    logger.warning("docs staging cleanup failed for %s: %s",
                                   tmp, ue)
            await self._close()
            if isinstance(e, GitHubFetchError):
                raise
            raise GitHubFetchError(
                f"docs staging failed for {repo}: {e}") from e
