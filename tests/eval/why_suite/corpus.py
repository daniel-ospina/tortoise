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

from dataclasses import dataclass
from pathlib import Path

from eval.why_suite import schema
from eval.write_path import schema as ws

WHY_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = WHY_DIR / "corpus_manifest.json"
GOLD_DIR = WHY_DIR / "gold"
BASELINES_DIR = WHY_DIR / "baselines"
RECEIPTS_DIR = WHY_DIR / "receipts"
SELF_MANIFEST_PATH = WHY_DIR / "_manifest.json"

# ── Corpus profiles (#2275) ────────────────────────────────────────────────
# The why-suite runs over MORE THAN ONE jointly-pinned corpus.  The v1
# corpus is the same-team authored A11 pilot (issue #2100) and its manifest /
# gold / baselines are FROZEN — never mutated by a second corpus.  The
# adversarial corpus (#2275) is a SECOND jointly-pinned fixture set whose
# fixtures plant the structures the v1 corpus cannot falsify (near-tie
# posteriors, multi-NAND clusters wider than the ≤2 surfacing cap, supersede
# chains stacked on conflicts, decisions with 3+ alternatives, and a
# contested-threshold boundary at variance == 0.04 exactly).
#
# A profile scopes exactly four things: which manifest/gold files
# ``fixtures_hash`` covers, which sealed gold the runner grades against,
# which committed baselines a run compares to (a SEPARATE lane, so the v1 A11
# numbers are untouched), and which topic families the seed plants.
#
# Cross-corpus runs never compare: the hash covers different files, and the
# baseline config carries ``corpus`` (see BASELINE_CONFIG), so a run of one
# corpus against the other's baseline is an inconclusive config mismatch, not
# a silently-passing compare.
DEFAULT_CORPUS = "why_suite"
ADVERSARIAL_CORPUS = "why_suite_adversarial"


@dataclass(frozen=True)
class CorpusProfile:
    """One jointly-pinned corpus: its fixture files, its lane, its families."""

    name: str
    protocol: str
    seed: int
    manifest_file: str
    gold_file: str
    self_manifest_file: str
    baselines: dict[str, str]  # posture ("llm" | "m2") → baseline file
    families: tuple[str, ...]
    points: int


PROFILES: dict[str, CorpusProfile] = {
    DEFAULT_CORPUS: CorpusProfile(
        name=DEFAULT_CORPUS,
        protocol="why_suite_corpus_v1",
        seed=42,
        manifest_file="corpus_manifest.json",
        gold_file="gold/why_suite.gold.json",
        self_manifest_file="_manifest.json",
        baselines={"llm": "baselines/main.json", "m2": "baselines/m2.json"},
        families=("p9", "decision", "superseded", "plain", "clean"),
        points=40,
    ),
    ADVERSARIAL_CORPUS: CorpusProfile(
        name=ADVERSARIAL_CORPUS,
        protocol="why_suite_corpus_v2_adversarial",
        seed=4275,
        manifest_file="corpus_manifest_adversarial.json",
        gold_file="gold/why_suite_adversarial.gold.json",
        self_manifest_file="_manifest_adversarial.json",
        baselines={
            "llm": "baselines/adversarial_main.json",
            "m2": "baselines/adversarial_m2.json",
        },
        families=(
            "near_tie",
            "multi_nand",
            "chain",
            "wide_decision",
            "precedence",
            "boundary",
            "clean",
        ),
        points=23,
    ),
}
CORPUS_NAMES = frozenset(PROFILES)

# Why-suite corpus identity (protocol shared with W4-a's E2E-1 seeding).
CORPUS_PROTOCOL = PROFILES[DEFAULT_CORPUS].protocol
CORPUS_SEED = PROFILES[DEFAULT_CORPUS].seed


def profile(corpus_name: str = DEFAULT_CORPUS) -> CorpusProfile:
    """Resolve a corpus profile by name (unknown name is a LOUD error)."""
    try:
        return PROFILES[corpus_name]
    except KeyError as exc:  # pragma: no cover - guarded by CLI/schema
        raise ValueError(
            f"unknown why-suite corpus {corpus_name!r} (known: {sorted(CORPUS_NAMES)})"
        ) from exc

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
    # #2275: the corpus a run grades.  It rides the config snapshot so a run
    # of one corpus against the other corpus's baseline is an inconclusive
    # CONFIG mismatch (compare_run) as well as a hash mismatch — belt and
    # braces on top of the fixtures_hash scoping below.
    "corpus": DEFAULT_CORPUS,
}


def load_manifest(root: Path = WHY_DIR, *, corpus: str = DEFAULT_CORPUS) -> dict:
    """The jointly-pinned corpus manifest (the fixture-side seeding spec)."""
    return schema.read_json(root / profile(corpus).manifest_file)


def gold_doc(root: Path = WHY_DIR, *, corpus: str = DEFAULT_CORPUS) -> dict:
    """The sealed gold document for one corpus."""
    return schema.read_json(gold_path(root, corpus=corpus))


def gold_path(root: Path = WHY_DIR, *, corpus: str = DEFAULT_CORPUS) -> Path:
    return root / profile(corpus).gold_file


def baseline_path(
    root: Path = WHY_DIR, *, posture: str = "llm", corpus: str = DEFAULT_CORPUS
) -> Path:
    """Per-posture, per-corpus baseline file (same convention as the W3-a
    harness): main.json = the product lane, m2.json = the deterministic lane.
    This suite is zero-LLM (seeding + assembly + grading deterministic), so
    both lanes run identical code — the posture records the run's provenance
    and keeps the CI can-fail gate (m2) separate from the publish lane
    (main).  The adversarial corpus (#2275) gets its OWN lane files so the v1
    A11 pilot numbers stay untouched."""
    profile_ = profile(corpus)
    rel = profile_.baselines.get("m2" if posture == "m2" else "llm")
    if rel is None:  # pragma: no cover - every profile declares both lanes
        raise ValueError(f"corpus {corpus!r} declares no baseline for posture {posture!r}")
    return root / rel


def load_baseline(
    root: Path = WHY_DIR, *, posture: str = "llm", corpus: str = DEFAULT_CORPUS
) -> dict:
    return schema.read_json(baseline_path(root, posture=posture, corpus=corpus))


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def corpus_file_paths(root: Path = WHY_DIR, *, corpus: str = DEFAULT_CORPUS) -> list[Path]:
    """The corpus files covered by fixtures_hash for ONE corpus: its
    jointly-pinned manifest (fixture side) + its sealed gold file (sorted —
    deterministic).  Scoped per corpus (#2275) so a second pinned fixture set
    can never move a blessed corpus's hash: v1's file list — and therefore
    its committed ``fixtures_hash`` — is byte-identical to before."""
    profile_ = profile(corpus)
    return sorted([root / profile_.manifest_file, root / profile_.gold_file])


def compute_fixtures_hash(root: Path = WHY_DIR, *, corpus: str = DEFAULT_CORPUS) -> str:
    """sha256 over ONE corpus's manifest + gold files (``sha256:<hex>`` of the
    joined digest, path-prefixed and sorted — deterministic).  Coverage scope
    = manifest AND gold (a gold-only edit changes the hash)."""
    digests: list[tuple[str, str]] = []
    for path in corpus_file_paths(root, corpus=corpus):
        digests.append((_relative(path, root), ws.sha256_file(path)))
    payload = "\n".join(f"{rel}:{digest}" for rel, digest in sorted(digests)).encode("utf-8")
    return ws.sha256_bytes(payload)


def verify_manifest(root: Path = WHY_DIR, *, corpus: str = DEFAULT_CORPUS) -> dict:
    """Verify one corpus's ``_manifest*.json`` against its on-disk files.

    Returns ``{"ok", "missing", "extra", "mismatched", "malformed",
    "fixtures_hash"}`` (same contract as the W3-a harness pre-flight).
    """
    profile_ = profile(corpus)
    manifest_path = root / profile_.self_manifest_file
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
    on_disk = {_relative(p, root): p for p in corpus_file_paths(root, corpus=corpus)}
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
        "fixtures_hash": compute_fixtures_hash(root, corpus=corpus),
    }


def first_run_pending_baseline(
    root: Path = WHY_DIR,
    *,
    posture: str = "llm",
    fixtures_hash: str | None = None,
    corpus: str = DEFAULT_CORPUS,
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
    config["seed"] = profile(corpus).seed
    config["corpus"] = corpus
    return {
        "schema_version": schema.SCHEMA_VERSION,
        "fixtures_hash": fixtures_hash or compute_fixtures_hash(root, corpus=corpus),
        "judge_pin": None,
        "config": config,
        "justification": None,
        "metrics": {},
        "history": [],
    }


def holdout_ids(root: Path = WHY_DIR, *, corpus: str = DEFAULT_CORPUS) -> list[str]:
    """No holdout split in either corpus (see module docstring) — always empty."""
    del root, corpus  # neither corpus has a pinned holdout; kept for runner parity
    return []
