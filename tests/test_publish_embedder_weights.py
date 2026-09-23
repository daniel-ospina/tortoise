"""Tests for tools/publish_embedder_weights.py — the #2898 artifact packager.

The asset's SHA-256 is a **constant** in `tools/embedder_provision.py`, which
every CI provisioning job verifies before extracting. So the packager's only
real contract is **byte-reproducibility**: if a republish produces different
bytes, every provisioning job reddens with a checksum mismatch and the "recovery
is one command" claim in the design is false.

Three sources of nondeterminism were measured before they were neutralised, and
each has a test here that fails without its fix:

1. gzip's **FNAME** header (derived from the output file's name) → two different
   `dest` names must still hash the same;
2. member **mtime** (including on directory and symlink members);
3. member **mode** read from the source tree (macOS reports a symlink as 0o755,
   Linux as 0o777; a regular file's mode leaks the umask) → mutating the source
   modes between builds must not change the digest.

Plus the derivation pins: the release tag/asset name are derived from
`REVISION` and must stay in lockstep with `tortoise/embeddings.py`, so a
revision bump cannot leave the URL pointing at the previous model's bytes.
"""
from __future__ import annotations

import hashlib
import os
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import publish_embedder_weights as pub

_CACHE_DIRNAME = pub.CACHE_DIRNAME


def _make_tree(root: Path, *, mode_regular: int = 0o644) -> Path:
    """A faithful miniature of the HF cache layout for one snapshot dir.

    Real shape (verified against a populated cache): `blobs/` holds real files,
    `snapshots/<rev>/<name>` are SYMLINKS into `../../blobs/<hash>`, `refs/main`
    holds the revision, and interrupted downloads leave `*.incomplete` debris in
    `blobs/`.
    """
    rev = pub.REVISION
    base = root / _CACHE_DIRNAME
    (base / "blobs").mkdir(parents=True)
    (base / "snapshots" / rev).mkdir(parents=True)
    (base / "refs").mkdir(parents=True)

    blob = base / "blobs" / ("a" * 64)
    blob.write_bytes(b"weights-bytes")
    blob.chmod(mode_regular)

    other = base / "blobs" / ("b" * 64)
    other.write_bytes(b"{}")
    other.chmod(mode_regular)

    # Interrupted-download debris — must never be packaged.
    (base / "blobs" / ("c" * 64 + ".1234.incomplete")).write_bytes(b"")

    snap = base / "snapshots" / rev
    link = snap / "model.safetensors"
    os.symlink("../../blobs/" + "a" * 64, link)
    # NOTE: no `link.lchmod(...)` here. A symlink's own mode is not portable —
    # macOS supports `chmod` on a link, Linux does NOT (`os.chmod in
    # os.supports_follow_symlinks` is False there, so `Path.lchmod` raises
    # NotImplementedError even though `hasattr` is True — it reddened 7 of these
    # tests on linux-latest). It is also POINTLESS: `build_archive` normalises
    # every symlink member to MODE_SYMLINK, so a source-mode change cannot reach
    # the archive. The cross-mode reproducibility test keeps its teeth through
    # `mode_regular`, which DOES reach the bytes.
    os.symlink("../../blobs/" + "b" * 64, snap / "config.json")

    (base / "refs" / "main").write_text(rev, encoding="utf-8")
    (base / "REFERENCE.txt").write_bytes(b"not a symlink")
    return base


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ── 1. Reproducibility ────────────────────────────────────────────────────


def test_build_is_byte_reproducible_across_dest_names(tmp_path):
    """gzip FNAME: the output file's NAME must not reach the bytes."""
    base = _make_tree(tmp_path / "cache")

    a = tmp_path / "alpha.tar.gz"
    b = tmp_path / "beta.tar.gz"
    pub.build_archive(base, a)
    pub.build_archive(base, b)

    assert _sha256(a) == _sha256(b), (
        "the same source tree hashed differently for two destinations — gzip is "
        "embedding the output basename in its FNAME header (pass filename=\"\")"
    )


def test_build_is_byte_reproducible_across_source_modes(tmp_path):
    """Mode must be NORMALISED, never read from the host's lstat.

    macOS reports a symlink as 0o755 where Linux reports 0o777, and a regular
    file's mode leaks the umask — so a mode read from the source tree makes a
    Linux rebuild hash differently from a macOS one.
    """
    first = _make_tree(tmp_path / "one", mode_regular=0o644)
    second = _make_tree(tmp_path / "two", mode_regular=0o664)

    a = tmp_path / "one.tar.gz"
    b = tmp_path / "two.tar.gz"
    pub.build_archive(first, a)
    pub.build_archive(second, b)

    assert _sha256(a) == _sha256(b), (
        "source file/symlink modes changed the digest — the packager is reading "
        "mode from os.lstat instead of normalising it (regular 0o644 / dir "
        "0o755 / symlink 0o777)"
    )


def test_build_is_byte_reproducible_across_mtimes(tmp_path):
    """Member mtime must be fixed — including on the DIRECTORY members."""
    base = _make_tree(tmp_path / "cache")
    a = tmp_path / "a.tar.gz"
    pub.build_archive(base, a)

    os.utime(base, (1, 1))
    os.utime(base / "snapshots", (2, 2))
    os.utime(base / "refs" / "main", (3, 3))
    b = tmp_path / "b.tar.gz"
    pub.build_archive(base, b)

    assert _sha256(a) == _sha256(b), "a directory/file mtime reached the archive"


# ── 2. Content shape ──────────────────────────────────────────────────────


def test_archive_layout_extracts_at_the_cache_root(tmp_path):
    """Members are stored under the cache-dirname, so extracting at the HF cache
    ROOT recreates the tree the library reads."""
    base = _make_tree(tmp_path / "cache")
    dest = tmp_path / "asset.tar.gz"
    pub.build_archive(base, dest)

    with tarfile.open(dest, "r:gz") as tf:
        names = tf.getnames()

    assert _CACHE_DIRNAME in names
    assert f"{_CACHE_DIRNAME}/refs/main" in names
    assert f"{_CACHE_DIRNAME}/snapshots/{pub.REVISION}/model.safetensors" in names
    # Extraction into a fresh dir must round-trip the whole tree.
    out = tmp_path / "out"
    with tarfile.open(dest, "r:gz") as tf:
        tf.extractall(out, filter="data")
    assert (out / _CACHE_DIRNAME / "refs" / "main").read_text(encoding="utf-8") == pub.REVISION
    assert (out / _CACHE_DIRNAME / "snapshots" / pub.REVISION / "model.safetensors").read_bytes() \
        == b"weights-bytes"


def test_incomplete_blobs_are_excluded(tmp_path):
    """`blobs/*.incomplete` is interrupted-download debris, not model data."""
    base = _make_tree(tmp_path / "cache")
    dest = tmp_path / "asset.tar.gz"
    pub.build_archive(base, dest)

    with tarfile.open(dest, "r:gz") as tf:
        names = tf.getnames()
    assert not [n for n in names if n.endswith(".incomplete")], (
        f"interrupted-download debris was packaged: {names}"
    )


def test_symlinks_are_preserved_not_dereferenced(tmp_path):
    """The snapshot entries are symlinks into `blobs/`; dereferencing them would
    duplicate the 133 MB weights and break the cache's content addressing."""
    base = _make_tree(tmp_path / "cache")
    dest = tmp_path / "asset.tar.gz"
    pub.build_archive(base, dest)

    with tarfile.open(dest, "r:gz") as tf:
        member = tf.getmember(f"{_CACHE_DIRNAME}/snapshots/{pub.REVISION}/model.safetensors")
    assert member.issym(), "the snapshot symlink was dereferenced into a regular file"
    assert member.linkname == "../../blobs/" + "a" * 64
    assert member.mode == pub.MODE_SYMLINK


# ── 3. Derivation + lockstep pins ─────────────────────────────────────────


def test_tag_and_asset_are_derived_from_the_revision():
    """A hand-maintained tag could point at the PREVIOUS model's bytes after a
    revision bump — the URL must be a function of REVISION."""
    assert pub.release_tag() == f"embedder-weights-{pub.MODEL_SLUG}-{pub.REVISION[:8]}"
    assert pub.asset_name() == f"{pub.MODEL_SLUG}-{pub.REVISION[:8]}-hf-cache.tar.gz"
    assert pub.release_tag("deadbeef" + "0" * 32) != pub.release_tag(), (
        "the tag does not move with the revision"
    )
    assert pub.release_tag() in pub.asset_url()
    assert pub.asset_name() in pub.asset_url()


def test_asset_url_keeps_the_tag_when_repo_is_overridden():
    """Regression: `asset_url(repo=…)` was once called as `asset_url(args.repo)`,
    which bound the SLUG to `revision` and produced a tag ending `-daniel-o`.
    Pin the printed URL from `main()`, not just the helper's default."""
    url = pub.asset_url(repo="owner/name")
    assert url.startswith("https://github.com/owner/name/releases/download/")
    assert f"/{pub.release_tag()}/" in url, url
    assert pub.REVISION[:8] in url


def test_main_build_prints_a_url_built_from_the_revision(tmp_path, capsys):
    _make_tree(tmp_path)
    dest = tmp_path / "asset.tar.gz"
    rc = pub.main(["--cache-root", str(tmp_path), "--build", str(dest)])
    out = capsys.readouterr().out
    assert rc == 0
    assert f"/{pub.release_tag()}/" in out, out
    assert "-daniel-o/" not in out, out


def test_tag_is_not_v_star():
    """A `v*` tag fires publish-pypi / publish-selfhost / deploy-hosted."""
    assert not pub.release_tag().startswith("v"), (
        "a v*-shaped tag would trigger the v*-gated publish/deploy workflows"
    )


def test_pins_match_the_shipped_embedder():
    """Lockstep with tortoise/embeddings.py — a drifted pin would serve the
    WRONG embedder from the artifact (same contract as test_embedder_provision)."""
    import tortoise.embeddings as emb

    assert pub.MODEL == emb.EMBEDDING_MODEL
    assert pub.REVISION == emb.EMBEDDING_MODEL_REVISION


def test_cache_root_is_the_library_resolution(tmp_path, monkeypatch):
    """`default_cache_root()` must mirror huggingface_hub, not re-derive it."""
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hfhome"))
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    monkeypatch.delenv("HUGGINGFACE_HUB_CACHE", raising=False)
    try:
        from huggingface_hub.constants import HF_HUB_CACHE

        assert pub.default_cache_root() == Path(HF_HUB_CACHE)
    except ImportError:  # pragma: no cover — extra not installed
        assert pub.default_cache_root() == tmp_path / "hfhome" / "hub"


@pytest.mark.parametrize("suffix", [".incomplete"])
def test_excluded_suffix_is_declared(suffix):
    assert suffix == pub.EXCLUDED_SUFFIX
