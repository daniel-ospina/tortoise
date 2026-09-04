"""W3 harness corpus: paths, fixtures_hash, manifest, pending baseline.

Mirrors the W2-b corpus layer (tests/eval/write_path/corpus.py) with the
harness's own fixture/gold/baseline dirs.  The harness corpus is the
Cat-34-style scripted-conversation set across the five suites; the sealed
gold lives separately; ``fixtures_hash`` covers fixture AND gold files (a
gold-only edit changes the hash ⇒ invalidates committed baselines).

The baseline machinery (bless/compare/validate + per-posture files) is
delegated to the shared write_path schema; the pending-baseline shape is
built here with the harness config snapshot.
"""
from __future__ import annotations

from pathlib import Path

from eval.harness import schema
from eval.write_path import schema as ws

HARNESS_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = HARNESS_DIR / "fixtures"
GOLD_DIR = HARNESS_DIR / "gold"
BASELINES_DIR = HARNESS_DIR / "baselines"
RECEIPTS_DIR = HARNESS_DIR / "receipts"
MANIFEST_PATH = HARNESS_DIR / "_manifest.json"

# Baseline config snapshot the W3 runner is designed around (BPRE default;
# the graded reflex decision layer lands via W4 — the initial harness runs a
# NULL reflex, which is honest and publishable per the fix-wave protocol).
BASELINE_CONFIG: dict = {
    "suites": sorted(schema.SUITE_VALUES),
    "mode": "BPRE",
    "reflex": "null",          # W4 lands the graded reflex; null = honest baseline
    "holdout_excluded": True,  # gate corpus excludes holdout fixtures
    "seed": 42,
    "extractor_posture": "llm",
}

FIXTURE_GLOB = "fixtures/*.json"
GOLD_GLOB = "gold/*.gold.json"


def fixture_path(session_id: str, root: Path = HARNESS_DIR) -> Path:
    return root / "fixtures" / f"{session_id}.json"


def gold_path(session_id: str, root: Path = HARNESS_DIR) -> Path:
    return root / "gold" / f"{session_id}.gold.json"


def baseline_path(root: Path = HARNESS_DIR, *, posture: str = "llm") -> Path:
    """Per-posture baseline file (same convention as W2-b): main.json = the
    product-lane number; m2.json = the deterministic echo lane (used when the
    harness replay runs the m2 extraction seam in CI)."""
    return root / "baselines" / ("m2.json" if posture == "m2" else "main.json")


def load_baseline(root: Path = HARNESS_DIR, *, posture: str = "llm") -> dict:
    return schema.read_json(baseline_path(root, posture=posture))


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def corpus_file_paths(root: Path = HARNESS_DIR) -> list[Path]:
    """Every fixture + gold file under ``root`` (sorted, for hashing)."""
    paths = sorted((root / "fixtures").glob("*.json"))
    paths += sorted((root / "gold").glob("*.gold.json"))
    return paths


def session_ids(root: Path = HARNESS_DIR) -> list[str]:
    """Sorted session ids = fixture filename stems (fixture-keyed corpus)."""
    return sorted(p.stem for p in (root / "fixtures").glob("*.json"))


def load_fixture(session_id: str, root: Path = HARNESS_DIR) -> dict:
    return schema.read_json(fixture_path(session_id, root))


def load_gold(session_id: str, root: Path = HARNESS_DIR) -> dict:
    return schema.read_json(gold_path(session_id, root))


def compute_fixtures_hash(root: Path = HARNESS_DIR) -> str:
    """sha256 over every fixture + gold file (``sha256:<hex>`` of the joined
    digest, path-prefixed and sorted — deterministic, independent of
    filesystem ordering).  Coverage scope is fixture AND gold files."""
    digests: list[tuple[str, str]] = []
    for path in corpus_file_paths(root):
        digests.append((_relative(path, root), ws.sha256_file(path)))
    payload = "\n".join(f"{rel}:{digest}" for rel, digest in sorted(digests)).encode("utf-8")
    return ws.sha256_bytes(payload)


def load_manifest(root: Path = HARNESS_DIR) -> dict:
    return schema.read_json(MANIFEST_PATH if root == HARNESS_DIR else root / "_manifest.json")


def verify_manifest(root: Path = HARNESS_DIR) -> dict:
    """Verify a corpus ``_manifest.json`` against the on-disk files.

    Returns ``{"ok", "missing", "extra", "mismatched", "malformed",
    "fixtures_hash"}`` — the W3 pre-flight + E2E-4 drift gate.
    """
    manifest_path = root / "_manifest.json"
    try:
        manifest = schema.read_json(manifest_path)
    except Exception as exc:
        return {"ok": False, "missing": [], "extra": [],
                "mismatched": [], "malformed": f"{type(exc).__name__}: {exc}",
                "fixtures_hash": ""}
    files = manifest.get("files")
    if not isinstance(files, dict):
        return {"ok": False, "missing": [], "extra": [],
                "mismatched": [], "malformed": "manifest.files missing",
                "fixtures_hash": ""}
    missing, extra, mismatched = [], [], []
    on_disk = {_relative(p, root): p for p in corpus_file_paths(root)}
    for rel, digest in files.items():
        if rel not in on_disk:
            missing.append(rel)
        elif ws.sha256_file(on_disk[rel]) != digest:
            mismatched.append(rel)
    for rel in on_disk:
        if rel not in files:
            extra.append(rel)
    return {
        "ok": not (missing or extra or mismatched),
        "missing": missing, "extra": extra, "mismatched": mismatched,
        "malformed": None,
        "fixtures_hash": compute_fixtures_hash(root),
    }


def first_run_pending_baseline(root: Path = HARNESS_DIR, *, posture: str = "llm") -> dict:
    """The first-run-pending baseline (benchmark-first state).  Empty metrics/
    history + null judge_pin/justification: no preset bar — the runner
    measures the CURRENT (no-reflex) harness first, then targets are set from
    first-run data.  ``posture`` names the extractor lane (config snapshot
    carries it so a run on the other lane is a config mismatch ⇒
    inconclusive)."""
    config = dict(BASELINE_CONFIG)
    config["extractor_posture"] = posture
    return {
        "schema_version": schema.SCHEMA_VERSION,
        "fixtures_hash": compute_fixtures_hash(root),
        "judge_pin": None,
        "config": config,
        "justification": None,
        "metrics": {},
        "history": [],
    }


def holdout_ids(root: Path = HARNESS_DIR) -> list[str]:
    """Holdout membership (PINNED per fixture — never seed-derived): the ids
    of fixtures with ``holdout: true``.  The gate corpus excludes them; the
    holdout set is a frozen evaluation set for the W4 reflex once landed."""
    return sorted(
        sid for sid in session_ids(root)
        if load_fixture(sid, root).get("holdout") is True
    )
