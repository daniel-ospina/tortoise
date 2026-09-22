"""W3-b why-suite corpus: paths, fixtures_hash, jointly-pinned manifest,
pending baselines (epic #2080, issue #2100).

Mirrors the W3-a harness corpus layer (tests/eval/harness/corpus.py) with
the why-suite's own corpus shape.  The why-suite corpus is the SHARED
planted-conflict corpus of epic E2E-1/E2E-7 — 40 fictional points (30
conflicted incl. 10 P9 / 5 decision / 5 superseded subsets + 10 clean)
seeded deterministically per the JOINTLY-PINNED corpus manifest
(``corpus_manifest.json``, fixture-side: harness-visible seeding spec only —
a ``gold`` key inside it is a VALIDATION ERROR) + the sealed gold
(``gold/why_suite.gold.json``: per-point ``expected.conflict_surfacing`` +
``expected.dig_deeper_targets``).  ``fixtures_hash`` covers manifest AND
gold files (a gold-only edit changes the hash ⇒ invalidates committed
baselines).

The jointly-pinned manifest is the drift gate BETWEEN this suite and W4-a's
E2E-1 seeding (tests/test_w4_why_enrichment.py::_seed_e2e1_corpus): gold
entries resolve against the manifest at contract-validation time (an entry
referencing a topic the seed never plants, or a target kind the plant never
produces, is a validation error) and the seeding drift test re-derives
W4-a's actual planting on a hermetic graph and compares composition.

Baseline discipline is the shared write-path machinery (bless/compare/
validate + per-posture files); the why-suite config/metric vocabulary is
defined in schema.py.  No holdout split: the why-suite grades the FULL
corpus every run — E2E-1/E2E-7's denominators (30 conflicted / 10 clean /
P9 / decision / superseded) are corpus-wide and the suite itself is the A11
pilot gate (nothing downstream needs untouched data; every planted point is
a graded datum).
"""

from __future__ import annotations

from pathlib import Path

from eval.why_suite import schema
from eval.write_path import schema as ws

WHY_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = WHY_DIR / "corpus_manifest.json"
GOLD_DIR = WHY_DIR / "gold"
BASELINES_DIR = WHY_DIR / "baselines"
RECEIPTS_DIR = WHY_DIR / "receipts"
SELF_MANIFEST_PATH = WHY_DIR / "_manifest.json"

# Why-suite corpus identity (protocol shared with W4-a's E2E-1 seeding).
CORPUS_PROTOCOL = "why_suite_corpus_v1"
CORPUS_SEED = 42

# Baseline config snapshot the why-suite runner is designed around.  Mode is
# "full" (no holdout split — see module docstring); ``reflex`` is "graded"
# from day one because the graded artifact (the W4 why-block assembly) is
# ALREADY delivered — unlike W3-a's null-reflex seam which waited for the
# W4 delivery issue.  Standing bars (conflict-surfacing >= 0.95,
# dig-deeper navigation >= 0.95, false positives = 0) are armed immediately.
BASELINE_CONFIG: dict = {
    "suites": ["why_suite"],
    "mode": "full",
    "reflex": "graded",
    "holdout_excluded": False,  # no holdout split — full-corpus grading
    "seed": CORPUS_SEED,
    "extractor_posture": "llm",  # env seam overrides (see runner)
}


def load_manifest(root: Path = WHY_DIR) -> dict:
    """The jointly-pinned corpus manifest (the fixture-side seeding spec)."""
    path = root / "corpus_manifest.json" if root != WHY_DIR else MANIFEST_PATH
    return schema.read_json(path)


def gold_doc(root: Path = WHY_DIR) -> dict:
    """The sealed gold document (single gold file for the one corpus)."""
    return schema.read_json(gold_path(root))


def gold_path(root: Path = WHY_DIR) -> Path:
    return root / "gold" / "why_suite.gold.json"


def baseline_path(root: Path = WHY_DIR, *, posture: str = "llm") -> Path:
    """Per-posture baseline file (same convention as the W3-a harness):
    main.json = the product lane, m2.json = the deterministic lane.  This
    suite is zero-LLM (seeding + assembly + grading deterministic), so both
    lanes run identical code — the posture records the run's provenance and
    keeps the CI can-fail gate (m2) separate from the publish lane (main)."""
    return root / "baselines" / ("m2.json" if posture == "m2" else "main.json")


def load_baseline(root: Path = WHY_DIR, *, posture: str = "llm") -> dict:
    return schema.read_json(baseline_path(root, posture=posture))


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def corpus_file_paths(root: Path = WHY_DIR) -> list[Path]:
    """The corpus files covered by fixtures_hash: the jointly-pinned manifest
    (fixture side) + every sealed gold file (sorted — deterministic)."""
    paths = [root / "corpus_manifest.json"]
    paths += sorted((root / "gold").glob("*.gold.json"))
    return paths


def compute_fixtures_hash(root: Path = WHY_DIR) -> str:
    """sha256 over manifest + gold files (``sha256:<hex>`` of the joined
    digest, path-prefixed and sorted — deterministic).  Coverage scope =
    manifest AND gold (a gold-only edit changes the hash)."""
    digests: list[tuple[str, str]] = []
    for path in corpus_file_paths(root):
        digests.append((_relative(path, root), ws.sha256_file(path)))
    payload = "\n".join(f"{rel}:{digest}" for rel, digest in sorted(digests)).encode("utf-8")
    return ws.sha256_bytes(payload)


def verify_manifest(root: Path = WHY_DIR) -> dict:
    """Verify the corpus ``_manifest.json`` against on-disk files.

    Returns ``{"ok", "missing", "extra", "mismatched", "malformed",
    "fixtures_hash"}`` (same contract as the W3-a harness pre-flight).
    """
    manifest_path = root / "_manifest.json"
    try:
        manifest = schema.read_json(manifest_path)
    except Exception as exc:
        return {
            "ok": False,
            "missing": [],
            "extra": [],
            "mismatched": [],
            "malformed": f"{type(exc).__name__}: {exc}",
            "fixtures_hash": "",
        }
    files = manifest.get("files")
    if not isinstance(files, dict):
        return {
            "ok": False,
            "missing": [],
            "extra": [],
            "mismatched": [],
            "malformed": "manifest.files missing",
            "fixtures_hash": "",
        }
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
        "missing": missing,
        "extra": extra,
        "mismatched": mismatched,
        "malformed": None,
        "fixtures_hash": compute_fixtures_hash(root),
    }


def first_run_pending_baseline(
    root: Path = WHY_DIR, *, posture: str = "llm", fixtures_hash: str | None = None
) -> dict:
    """First-run-pending baseline (benchmark-first state).  Empty metrics +
    null judge_pin/justification: no preset bar — the runner publishes the
    first numbers and blesses a real baseline.  ``posture`` names the lane
    (config snapshot carries it so a run on the other lane is a config
    mismatch ⇒ inconclusive).  ``fixtures_hash`` may be passed explicitly
    (the generator passes the RENDERED hash — a first-time write must not
    read stale/missing files off disk); None computes from disk."""
    config = dict(BASELINE_CONFIG)
    config["extractor_posture"] = posture
    return {
        "schema_version": schema.SCHEMA_VERSION,
        "fixtures_hash": fixtures_hash or compute_fixtures_hash(root),
        "judge_pin": None,
        "config": config,
        "justification": None,
        "metrics": {},
        "history": [],
    }


def holdout_ids(root: Path = WHY_DIR) -> list[str]:
    """No holdout split in this corpus (see module docstring) — always empty."""
    del root  # corpus has no pinned holdout; kept for runner parity
    return []
