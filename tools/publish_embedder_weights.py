#!/usr/bin/env python3
"""publish_embedder_weights.py — package and publish the pinned embedder (#2898).

WHY THIS EXISTS
---------------
CI used to obtain `BAAI/bge-small-en-v1.5` at the pinned revision through an
`actions/cache@v4` entry over `~/.cache/huggingface`, falling back to a download
from ``huggingface.co``. Both halves are outside this repo's control:

* a GitHub cache is **write-once per key** and evicted (10 GB/repo, LRU, entries
  untouched >7 days removed), so it can only ever *leave* — it can never be a
  durability guarantee; and
* a hub download needs ``huggingface.co`` **plus** its CDN hosts
  (``cas-server.xethub*.hf.co``, ``transfer.xethub*.hf.co``, ``cdn-lfs-*.hf.co``
  …) — HF's own docs say the download fails if those are unreachable *even when
  ``huggingface.co`` is allowlisted*.

So the pinned revision is served from a host the runner provably can reach:
**this repository's own GitHub Release**. `tools/embedder_provision.py` fetches
the asset below, verifies its SHA-256 against a constant pinned in that tool, and
extracts it into the Hugging Face cache root. The hub leaves the test-time path.

WHAT IS PUBLISHED
-----------------
* tag   ``embedder-weights-bge-small-en-v1.5-<REVISION[:8]>``   (NOT ``v*`` — a
  ``v*`` tag fires ``publish-pypi.yml`` / ``publish-selfhost.yml`` /
  ``deploy-hosted.yml``; verified: publishing a non-``v*`` tag creates no
  ``push``-event workflow run)
* asset ``bge-small-en-v1.5-<REVISION[:8]>-hf-cache.tar.gz``    — the HF cache
  subtree ``models--BAAI--bge-small-en-v1.5/`` (``blobs/``, ``refs/``,
  ``snapshots/<rev>/``, ``.no_exist/``)

REPRODUCIBILITY — the whole point of the script
-----------------------------------------------
The asset's SHA-256 is a constant in `tools/embedder_provision.py`, so the
packaging MUST be byte-reproducible or a republish silently stops matching the
pin and every provisioning job reddens. Three sources of nondeterminism are
neutralised, each measured before it was written down:

1. **gzip's FNAME header.** ``gzip.GzipFile`` derives FNAME from
   ``fileobj.name``; building the same payload to ``alpha.tar.gz`` and
   ``beta.tar.gz`` produced *different* digests. Hence ``filename=""``.
   (``tarfile.open(mode="w:gz")`` is worse: its gzip header embeds ``mtime``.)
2. **Member metadata.** ``uid``/``gid``/``uname``/``gname`` are fixed, and
   ``mtime`` is fixed **on regular, directory AND symlink members**.
3. **Member mode.** Mode is NORMALISED, never read from ``os.lstat``: macOS
   reports a symlink as ``0o755`` where Linux reports ``0o777``, and a regular
   file's mode leaks the umask — so a mode read from the source tree makes a
   Linux rebuild hash differently from a macOS build.

Entry order is sorted, and ``*.incomplete`` (interrupted-download debris left in
``blobs/``) is excluded.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

# Keep in lockstep with tortoise/embeddings.py (EMBEDDING_MODEL /
# EMBEDDING_MODEL_REVISION). tests/test_embedder_provision.py pins the lockstep;
# tests/test_publish_embedder_weights.py pins the tag/asset derivation below.
MODEL = "BAAI/bge-small-en-v1.5"
REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
CACHE_DIRNAME = "models--BAAI--bge-small-en-v1.5"
MODEL_SLUG = "bge-small-en-v1.5"
REPO = "daniel-ospina/tortoise"

#: Fixed for every member — a rebuild must not depend on when it ran.
FIXED_MTIME = 0
#: Normalised modes (see the module docstring, point 3). Never `os.lstat`.
MODE_REGULAR = 0o644
MODE_DIR = 0o755
MODE_SYMLINK = 0o777
#: Interrupted-download debris in `blobs/` (`<sha>.<suffix>.incomplete`).
EXCLUDED_SUFFIX = ".incomplete"

_CHUNK = 1 << 20


def release_tag(revision: str = REVISION) -> str:
    """The release tag. Derived from the revision — never hand-maintained."""
    return f"embedder-weights-{MODEL_SLUG}-{revision[:8]}"


def asset_name(revision: str = REVISION) -> str:
    return f"{MODEL_SLUG}-{revision[:8]}-hf-cache.tar.gz"


def asset_url(revision: str = REVISION, repo: str = REPO) -> str:
    return f"https://github.com/{repo}/releases/download/{release_tag(revision)}/{asset_name(revision)}"


def default_cache_root() -> Path:
    """Where the HF cache lives. Mirrors huggingface_hub's own resolution.

    Imported rather than re-derived so it cannot drift from the library:
    ``HF_HUB_CACHE`` env → ``HUGGINGFACE_HUB_CACHE`` env → ``$HF_HOME/hub``,
    with ``HF_HOME`` defaulting to ``$XDG_CACHE_HOME/huggingface``.
    """
    try:
        from huggingface_hub.constants import HF_HUB_CACHE  # type: ignore

        return Path(HF_HUB_CACHE)
    except Exception:  # publish may run without the extra installed
        hf_home = os.environ.get("HF_HOME")
        if not hf_home:
            xdg = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
            hf_home = str(Path(xdg) / "huggingface")
        return Path(hf_home) / "hub"


def _iter_members(source: Path):
    """Yield (archive_name, path) sorted, skipping interrupted-download debris."""
    entries: list[tuple[str, Path]] = [(source.name, source)]
    for dirpath, dirnames, filenames in os.walk(source, followlinks=False):
        dirnames.sort()
        here = Path(dirpath)
        for name in sorted(dirnames) + sorted(filenames):
            p = here / name
            if name.endswith(EXCLUDED_SUFFIX):
                continue
            entries.append((str(p.relative_to(source.parent)), p))
    # The top-level directory itself comes first (so `extractall` at the HF cache
    # root recreates `models--BAAI--bge-small-en-v1.5/` with the archive's own
    # metadata), then lexical order.
    entries.sort(key=lambda e: (0 if e[1] == source else 1, e[0].count("/"), e[0]))
    for name, p in entries:
        yield name, p


def build_archive(source: Path, dest: Path) -> str:
    """Write a BYTE-REPRODUCIBLE tar.gz of `source` (the model cache subtree).

    `source` is the ``models--BAAI--bge-small-en-v1.5`` directory; members are
    stored under that same name, so extracting at the HF cache root recreates
    the layout the library reads. Returns the SHA-256 of the written file.
    """
    source = Path(source)
    if not source.is_dir():
        raise SystemExit(f"publish-embedder: cache subtree not found: {source}")

    with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as raw:
        raw_path = Path(raw.name)
    try:
        # mode="w" (uncompressed): compression is applied below with an explicit
        # gzip header. `tarfile.open(mode="w:gz")` embeds a timestamp.
        with tarfile.open(raw_path, mode="w", format=tarfile.PAX_FORMAT) as tf:
            tf.pax_headers = {}
            for name, path in _iter_members(source):
                ti = tf.gettarinfo(str(path), arcname=name)
                # Normalise everything the host could leak.
                ti.uid = ti.gid = 0
                ti.uname = ti.gname = ""
                ti.mtime = FIXED_MTIME
                ti.pax_headers = {}
                if ti.issym():
                    ti.mode = MODE_SYMLINK
                    tf.addfile(ti)
                elif ti.isdir():
                    ti.mode = MODE_DIR
                    tf.addfile(ti)
                else:
                    ti.mode = MODE_REGULAR
                    with open(path, "rb") as fh:
                        tf.addfile(ti, fh)

        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        with (
            open(raw_path, "rb") as src,
            open(dest, "wb") as out_fh,
            # filename="" — otherwise CPython writes the OUTPUT basename into the
            # gzip FNAME header and two different dest names hash differently.
            gzip.GzipFile(filename="", fileobj=out_fh, mode="wb", mtime=0) as gz,
        ):
            while True:
                chunk = src.read(_CHUNK)
                if not chunk:
                    break
                gz.write(chunk)
        with open(dest, "rb") as fh:
            for chunk in iter(lambda: fh.read(_CHUNK), b""):
                digest.update(chunk)
        return digest.hexdigest()
    finally:
        raw_path.unlink(missing_ok=True)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def publish(asset: Path, *, repo: str = REPO, revision: str = REVISION,
            notes_file: Path | None = None) -> str:
    """Create (or update) the release and upload the asset. Returns the tag.

    Deliberately NOT a workflow: `workflow_dispatch` requires the workflow file
    to exist on the DEFAULT branch, so a workflow could not publish the artifact
    *before* the PR that consumes it merges — the ordering trap this design
    depends on avoiding. It also does not earn a CI surface for an action taken
    once per embedder rotation.
    """
    tag = release_tag(revision)
    exists = subprocess.run(
        ["gh", "release", "view", tag, "--repo", repo],
        capture_output=True, text=True,
    ).returncode == 0
    if not exists:
        cmd = ["gh", "release", "create", tag, "--repo", repo,
               "--title", f"Embedder weights — {MODEL} @ {revision[:8]}",
               "--notes", (
                   f"Pinned embedding-model artifact for CI (#2898).\n\n"
                   f"- model: `{MODEL}`\n- revision: `{revision}`\n\n"
                   "Consumed by `tools/embedder_provision.py`, which verifies the "
                   "asset's SHA-256 against a constant pinned in that file before "
                   "extracting. Tag is deliberately **not** `v*` so the "
                   "`v*`-triggered publish/deploy workflows do not fire."
               )]
        if notes_file:
            cmd[-1] = notes_file.read_text(encoding="utf-8")
        subprocess.run([*cmd, str(asset)], check=True)
    else:
        subprocess.run(["gh", "release", "upload", tag, str(asset), "--repo", repo, "--clobber"],
                       check=True)
    return tag


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Package + publish the pinned embedder (#2898).")
    ap.add_argument("--cache-root", type=Path, default=None,
                    help="HF cache root (default: huggingface_hub's HF_HUB_CACHE)")
    ap.add_argument("--build", type=Path, metavar="DEST",
                    help="write the reproducible tarball to DEST")
    ap.add_argument("--print-sha256", action="store_true",
                    help="build to a temp path and print only the asset's SHA-256")
    ap.add_argument("--publish", action="store_true",
                    help="create/update the release and upload the asset")
    ap.add_argument("--repo", default=REPO)
    args = ap.parse_args(argv)

    root = args.cache_root or default_cache_root()
    source = Path(root) / CACHE_DIRNAME

    if args.print_sha256:
        with tempfile.TemporaryDirectory() as td:
            digest = build_archive(source, Path(td) / asset_name())
        print(digest)
        return 0

    if args.build:
        digest = build_archive(source, args.build)
        print(f"asset:  {args.build}")
        print(f"sha256: {digest}")
        print(f"tag:    {release_tag()}   (from {REVISION})")
        print(f"url:    {asset_url(repo=args.repo)}")
        return 0

    if args.publish:
        with tempfile.TemporaryDirectory() as td:
            asset = Path(td) / asset_name()
            digest = build_archive(source, asset)
            tag = publish(asset, repo=args.repo)
        print(f"published {tag}: {asset_name()} sha256={digest}")
        return 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
