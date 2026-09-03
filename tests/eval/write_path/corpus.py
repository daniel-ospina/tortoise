"""W2 write-path corpus access + fixtures_hash verification (issue #2097, W2-a).

Runner-facing API for the committed planted-gold corpus under
``tests/eval/write_path/``: canonical paths, corpus inventory (session ids +
harnesses), sha256 ``fixtures_hash`` coverage (fixture AND gold files — a
gold-only edit invalidates baselines), manifest verification, and the
first-run-pending baseline factory the generator commits.

Consumed by: this package's generator (``generate_corpus.py``), the contract
tests, and — from the sibling issue — the W2-b benchmark runner (#2098)
pre-flight (E2E-2: fixtures_hash verification + config-mismatch ⇒
inconclusive, never a rubber-stamp).

Every function accepts an optional ``root`` (defaults to the committed
write_path dir) so consumers can run the same checks over a temporary or
worktree copy — the gold-only-edit ⇒ hash-mismatch gate is exercised without
touching committed bytes.

Hermetic: pure stdlib.
"""
from __future__ import annotations

from pathlib import Path

from tests.eval.write_path import schema

WRITE_PATH_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = WRITE_PATH_DIR / "fixtures"
GOLD_DIR = WRITE_PATH_DIR / "gold"
BASELINES_DIR = WRITE_PATH_DIR / "baselines"
MANIFEST_PATH = WRITE_PATH_DIR / "_manifest.json"
BASELINE_PATH = BASELINES_DIR / "main.json"

# Baseline config snapshot the W2 runner is designed around (plan §4.3.3
# example).  A run whose resolved config differs ⇒ compare verdict
# ``inconclusive`` — the committed config is the contract for the frozen
# corpus run.
BASELINE_CONFIG: dict = {
    "lanes": ["verbatim", "facts", "dream"],
    "mode": "BPRE",          # cost-bounded default; `--full` is opt-in
    "harness": "all",        # seams graded
    "seed": 42,              # deterministic within-run ordering only
}

FIXTURE_GLOB = "fixtures/*.json"
GOLD_GLOB = "gold/*.gold.json"


def fixture_path(session_id: str, root: Path = WRITE_PATH_DIR) -> Path:
    return root / "fixtures" / f"{session_id}.json"


def gold_path(session_id: str, root: Path = WRITE_PATH_DIR) -> Path:
    return root / "gold" / f"{session_id}.gold.json"


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def corpus_file_paths(root: Path = WRITE_PATH_DIR) -> list[Path]:
    """Every fixture + gold file in the corpus (the fixtures_hash scope)."""
    return sorted(list((root / "fixtures").glob("*.json")) + list((root / "gold").glob("*.gold.json")))


def session_ids(root: Path = WRITE_PATH_DIR) -> list[str]:
    """Session ids from the fixture files (sorted, stable)."""
    return sorted(p.stem for p in (root / "fixtures").glob("*.json"))


def load_fixture(session_id: str, root: Path = WRITE_PATH_DIR) -> dict:
    return schema.read_json(fixture_path(session_id, root))


def load_gold(session_id: str, root: Path = WRITE_PATH_DIR) -> dict:
    return schema.read_json(gold_path(session_id, root))


def load_baseline(root: Path = WRITE_PATH_DIR) -> dict:
    return schema.read_json(root / "baselines" / "main.json")


def compute_fixtures_hash(root: Path = WRITE_PATH_DIR) -> str:
    """sha256 over every fixture + gold file (``sha256:<hex>`` of the joined digest).

    The digest is computed over each file's raw bytes with its relative path
    prefix, sorted by relative path — deterministic and independent of
    filesystem ordering.  Coverage scope is fixture AND gold files (a
    gold-only edit changes the hash ⇒ invalidates committed baselines).
    """
    digests: list[tuple[str, str]] = []
    for path in corpus_file_paths(root):
        digests.append((_relative(path, root), schema.sha256_file(path)))
    payload = "\n".join(f"{rel}:{digest}" for rel, digest in sorted(digests)).encode("utf-8")
    return schema.sha256_bytes(payload)


def load_manifest(root: Path = WRITE_PATH_DIR) -> dict:
    return schema.read_json(root / "_manifest.json")


def verify_manifest(root: Path = WRITE_PATH_DIR) -> dict:
    """Verify a corpus ``_manifest.json`` against the on-disk files.

    Returns ``{"ok": bool, "missing": [...], "extra": [...], "mismatched": [...],
    "malformed": str | None, "fixtures_hash": str}``.  ``ok`` is True only
    when the manifest's per-file sha256 entries cover exactly the fixture + gold
    files and every digest matches.  A gold-only edit surfaces here as
    ``mismatched``/``extra`` — the W2 pre-flight + E2E-2 negative gate
    (gold-only edit ⇒ mismatch ⇒ inconclusive, never a rubber-stamp).
    ``malformed`` is set (str) when the manifest document itself is not a
    dict-shaped digest map — the gate reports it, never raises.
    """
    manifest = load_manifest(root)
    if not isinstance(manifest, dict):
        return {
            "ok": False,
            "missing": [],
            "extra": [],
            "mismatched": [],
            "malformed": (
                "manifest document is not an object "
                f"(got {type(manifest).__name__}) — gate reports, never raises"),
            "fixtures_hash": compute_fixtures_hash(root),
        }
    manifest_files = manifest.get("files")
    if not isinstance(manifest_files, dict):
        return {
            "ok": False,
            "missing": [],
            "extra": [],
            "mismatched": [],
            "malformed": "manifest.files is not an object mapping rel paths to digests",
            "fixtures_hash": compute_fixtures_hash(root),
        }
    on_disk = {_relative(p, root): p for p in corpus_file_paths(root)}
    missing = [rel for rel in manifest_files if rel not in on_disk]
    extra = [rel for rel in on_disk if rel not in manifest_files]
    mismatched = [
        rel
        for rel, path in on_disk.items()
        if rel in manifest_files and manifest_files[rel] != schema.sha256_file(path)
    ]
    expected_hash = manifest.get("fixtures_hash")
    actual_hash = compute_fixtures_hash(root)
    ok = (
        not missing
        and not extra
        and not mismatched
        and bool(expected_hash)
        and expected_hash == actual_hash
    )
    return {
        "ok": ok,
        "missing": sorted(missing),
        "extra": sorted(extra),
        "mismatched": sorted(mismatched),
        "malformed": None,
        "fixtures_hash": actual_hash,
    }


def first_run_pending_baseline(root: Path = WRITE_PATH_DIR) -> dict:
    """The first-run-pending baseline (benchmark-first state).

    Empty ``metrics``/``history`` + null ``judge_pin``/``justification``: no
    quality bar is preset — the corpus + runner measure the CURRENT write path
    first, then targets are set from first-run data.  W2-b publishes the first
    (expected-bad) number per the fix-wave protocol and replaces this file via
    ``schema.bless_baseline`` with a justification.
    """
    return {
        "schema_version": schema.SCHEMA_VERSION,
        "fixtures_hash": compute_fixtures_hash(root),
        "judge_pin": None,
        "config": dict(BASELINE_CONFIG),
        "justification": None,
        "metrics": {},
        "history": [],
    }
