"""W3-b why-suite corpus generator (epic #2080, issue #2100, DM-9).

Deterministic, idempotent generator for the planted-conflict corpus data
artifacts: the jointly-pinned ``corpus_manifest.json`` (fixture-side: the
harness-visible seeding spec — composition + topic keys + planted role
templates shared with W4-a's E2E-1 seeding) + the sealed
``gold/why_suite.gold.json`` (per-point ``expected.conflict_surfacing`` +
``expected.dig_deeper_targets``) + ``_manifest.json`` (fixture/gold hashes)
+ the first-run-pending ``baselines/{main,m2}.json``.

Authoring discipline (why the answer key is safe here — W2-a precedent):
* The corpus SPEC lives in THIS module (source code); the only committed
  DATA artifacts are ``corpus_manifest.json`` (harness-visible fields only
  — a ``gold`` key inside it is a validation error) + the sealed gold.
* The gold entries are DERIVED from the same per-family structure table the
  manifest renders (conflicted/clean flags + legal pointer kinds + planted
  roles) — gold↔manifest drift cannot ship silently (the generator fails
  loudly on any inconsistency; ``--check`` is byte-identical).
* Composition mirrors W4-a's E2E-1 seeding EXACTLY:
  tests/test_w4_why_enrichment.py::_seed_e2e1_corpus — 40 fictional points:
  10 P9 (posterior 1.5/1.5) + 5 decision + 5 superseded + 10 plain conflicted
  (posterior 2.0/2.0) + 10 clean.  The seeding drift test re-derives W4-a's
  real planting on a hermetic graph and compares composition + planted
  content against this manifest (the jointly-pinned drift gate).
* Output is byte-deterministic (sorted keys, fixed indent, no timestamps)
  so re-running the generator reproduces the committed corpus exactly.

Run from the repo root:
    uv run python tests/eval/why_suite/generate_corpus.py            # write
    uv run python tests/eval/why_suite/generate_corpus.py --check    # drift check
    uv run python tests/eval/why_suite/generate_corpus.py --validate # full validation
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # tests/ (eval package)

from eval.why_suite import corpus, schema

# ── Corpus identity ─────────────────────────────────────────────────────────
# Deterministic seed (jointly pinned with W4-a's E2E-1 seeding).  Topic keys
# mirror tests/test_w4_why_enrichment.py::_seed_e2e1_corpus exactly.
CORPUS_PROTOCOL = corpus.CORPUS_PROTOCOL
CORPUS_SEED = corpus.CORPUS_SEED
SOURCE_PIN = (
    "tests/test_w4_why_enrichment.py::_seed_e2e1_corpus "
    "(W4-a, issue #2101) — the shared epic E2E-1/E2E-7 planted-conflict corpus"
)

# Per-family topic key templates (deterministic; the jointly-pinned topic
# lists).  W4-a plants: p9-topic-0..9, decision-topic-0..4,
# superseded-topic-0..4, plain-topic-0..9, clean-topic-0..9.
_FAMILY_COUNTS: dict[str, int] = {
    "p9": 10,
    "decision": 5,
    "superseded": 5,
    "plain": 10,
    "clean": 10,
}


def topic_keys() -> dict[str, list[str]]:
    return {
        family: [f"{family}-topic-{i}" for i in range(count)]
        for family, count in _FAMILY_COUNTS.items()
    }


# Per-family planted role templates: role name → point content template.
# Content strings mirror the W4-a plant helpers verbatim:
#   _plant_conflicted / _plant_decision / _plant_superseded / _plant_clean.
# ``clean``'s second support is deliberately planted at posterior (11, 1) —
# NOT (12, 1) like W4-a's clean plant — so the two clean supports do not tie
# on EP weight and the assembly's deterministic supports-pointer target
# (highest weight) is the FIRST record on every run (a tied pair would make
# the pointer target id-tie-break over runtime ULIDs — nondeterministic
# navigation grading; composition/content denominators are unaffected).
PLANT_ROLES: dict[str, dict[str, str]] = {
    "p9": {
        "claim": "{topic} belief statement",
        "support": "{topic} supporting record alpha",
        "counter": "{topic} counterargument gamma",
    },
    "plain": {
        "claim": "{topic} belief statement",
        "support": "{topic} supporting record alpha",
        "counter": "{topic} counterargument gamma",
    },
    "decision": {
        "decision": "{topic} decision point",
        "support": "{topic} decision support record",
        "option_a": "{topic} alternative one",
        "option_b": "{topic} alternative two",
        "counter": "{topic} decision counterargument",
    },
    "superseded": {
        "old": "{topic} belief statement",
        "support": "{topic} supporting record alpha",
        "counter": "{topic} counterargument gamma",
        "successor": "{topic} successor belief",
    },
    "clean": {
        "claim": "{topic} clean belief statement",
        "support_a": "{topic} clean record one",
        "support_b": "{topic} clean record two",
    },
}

# Family → seeded posterior (alpha, beta) on the GRADED point (the claim /
# decision / old predecessor): P9 at (1.5, 1.5) → variance ~0.0714 >> 0.04;
# plain + decision + superseded at (2.0, 2.0) → variance 0.05 > 0.04
# (contested); clean at (12.0, 1.0) → variance ~0.0051 (not contested).
# CONTESTED_VARIANCE_THRESHOLD = 0.04 (tortoise/search_engine.py).
GRADED_POSTERIOR: dict[str, tuple[float, float]] = {
    "p9": (1.5, 1.5),
    "plain": (2.0, 2.0),
    "decision": (2.0, 2.0),
    "superseded": (2.0, 2.0),
    "clean": (12.0, 1.0),
}

# The expected dig-deeper target kinds per family (the plant structures the
# W4 assembly surfaces): every conflicted topic = supports + nand; decision
# adds tradeoff (the EP-favored alternative = option_a by construction:
# posterior 4/1 mean 0.8 > option_b 5/2 mean 0.714); superseded adds the
# successor pointer; clean topics surface ONLY a supports pointer (one of
# their records — deterministic: support_a, the higher-weight record).
_EXPECTED_KINDS: dict[str, list[dict]] = {
    "p9": [
        {"kind": "supports", "target_role": "support"},
        {"kind": "nand", "target_role": "counter"},
    ],
    "plain": [
        {"kind": "supports", "target_role": "support"},
        {"kind": "nand", "target_role": "counter"},
    ],
    "decision": [
        {"kind": "supports", "target_role": "support"},
        {"kind": "nand", "target_role": "counter"},
        {"kind": "tradeoff", "target_role": "option_a"},
    ],
    "superseded": [
        {"kind": "supports", "target_role": "support"},
        {"kind": "nand", "target_role": "counter"},
        {"kind": "superseded", "target_role": "successor"},
    ],
    "clean": [{"kind": "supports", "target_role": "support_a"}],
}


def render_manifest() -> dict:
    """The jointly-pinned corpus manifest (fixture-side seeding spec)."""
    return {
        "corpus": "why_suite",
        "protocol": CORPUS_PROTOCOL,
        "schema_version": schema.SCHEMA_VERSION,
        "seed": CORPUS_SEED,
        "source": SOURCE_PIN,
        "composition": {
            "total": 40,
            "conflicted": 30,
            "clean": 10,
            "p9": 10,
            "decision": 5,
            "superseded": 5,
            "plain": 10,
        },
        "topics": topic_keys(),
        "plant_roles": PLANT_ROLES,
    }


def render_gold() -> dict:
    """The sealed gold: per-planted-point expectations, DERIVED from the
    family structure table (conflicted/clean + expected pointer kinds)."""
    manifest = render_manifest()
    topics = manifest["topics"]
    entries: list[dict] = []
    for family in ("p9", "decision", "superseded", "plain", "clean"):
        conflicted = schema.FAMILY_CONFLICTED[family]
        for topic in topics[family]:
            entries.append(
                {
                    "point_id": topic,
                    "family": family,
                    "clean": not conflicted,
                    "expected": {
                        "conflict_surfacing": conflicted,
                        "dig_deeper_targets": [dict(t) for t in _EXPECTED_KINDS[family]],
                        "support_chain_sufficient": True,
                        "tradeoff_sufficient": family == "decision",
                    },
                }
            )
    return {
        "corpus": "why_suite",
        "protocol": CORPUS_PROTOCOL,
        "schema_version": schema.SCHEMA_VERSION,
        "suite": "why_suite",
        "seed": CORPUS_SEED,
        "entries": entries,
    }


def render_outputs() -> dict[str, bytes]:
    """All generator-owned files (manifest + gold + _manifest.json + pending
    baselines).  Baselines are written only when missing/pending — a
    PUBLISHED baseline (non-empty metrics) is never clobbered.

    The _manifest.json fixtures_hash is computed FROM THE RENDERED BYTES
    (never from disk — a first-time write would hash stale/missing files),
    replicating corpus.compute_fixtures_hash's join over path-prefixed
    sorted digests."""
    docs: dict[str, dict] = {
        "corpus_manifest.json": render_manifest(),
        "gold/why_suite.gold.json": render_gold(),
    }
    data: dict[str, bytes] = {rel: _dump(doc) for rel, doc in docs.items()}
    digests: list[tuple[str, str]] = []
    for rel, payload in data.items():
        digests.append((rel, schema.sha256_bytes(payload)))
    joined = "\n".join(f"{rel}:{digest}" for rel, digest in sorted(digests)).encode("utf-8")
    doc_manifest: dict = {
        "corpus": "why_suite",
        "files": dict(digests),
        "fixtures_hash": schema.sha256_bytes(joined),
        "generator": "tests/eval/why_suite/generate_corpus.py",
        "schema_version": schema.SCHEMA_VERSION,
        "seed": CORPUS_SEED,
    }
    out: dict[str, bytes] = dict(data)
    out["_manifest.json"] = _dump(doc_manifest)
    # Pending baselines carry the RENDERED fixtures_hash (never a disk read —
    # the first-time write must not hash stale/missing files).  The pending
    # shape is corpus.first_run_pending_baseline minus its disk-based hash,
    # which is patched to the rendered digest here.
    for posture in ("llm", "m2"):
        pending = corpus.first_run_pending_baseline(
            corpus.WHY_DIR, posture=posture, fixtures_hash=doc_manifest["fixtures_hash"]
        )
        out[f"baselines/{'main' if posture == 'llm' else 'm2'}.json"] = _dump(pending)
    return out


def _dump(doc: dict) -> bytes:
    return (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_corpus(root: Path | None = None) -> list[str]:
    root = root or corpus.WHY_DIR
    outputs = render_outputs()
    written: list[str] = []
    for rel, data in outputs.items():
        path = root / rel
        if rel.startswith("baselines/") and path.exists():
            existing = schema.read_json(path)
            if existing.get("metrics") or existing.get("history"):
                continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        written.append(rel)
    return written


def _iter_committed(root: Path):
    """The frozen-corpus JSON files (manifest + gold + _manifest.json),
    EXCLUDING baselines/ (they change when the runner blesses)."""
    frozen = ("corpus_manifest.json", "_manifest.json")
    for rel in frozen:
        path = root / rel
        if path.is_file():
            yield rel, path
    for path in sorted((root / "gold").glob("*.json")):
        yield path.relative_to(root).as_posix(), path


def check_drift(root: Path | None = None) -> list[str]:
    """Compare a fresh in-memory render against committed files."""
    root = root or corpus.WHY_DIR
    committed = {rel: path for rel, path in _iter_committed(root)}
    fresh = render_outputs()
    drifted = []
    for rel, data in fresh.items():
        if rel.startswith("baselines/"):
            continue
        if rel not in committed:
            drifted.append(f"{rel} (missing on disk)")
        elif committed[rel].read_bytes() != data:
            drifted.append(f"{rel} (content differs)")
    for rel in committed:
        if rel not in fresh:
            drifted.append(f"{rel} (orphan on disk)")
    return drifted


def validate_committed(root: Path | None = None) -> list[str]:
    """Full schema validation of the committed corpus (manifest ↔ gold,
    hash, both posture baselines) — the generator's own gate."""
    root = root or corpus.WHY_DIR
    issues: list[str] = []
    manifest = corpus.load_manifest(root)
    issues += [f"manifest: {i}" for i in schema.validate_manifest(manifest)]
    gold = corpus.gold_doc(root)
    issues += [f"gold: {i}" for i in schema.validate_gold(gold, manifest)]
    committed_hash = corpus.compute_fixtures_hash(root)
    verification = corpus.verify_manifest(root)
    if not verification["ok"]:
        issues.append(
            f"_manifest.json verification failed (missing={verification['missing']}, "
            f"extra={verification['extra']}, mismatched={verification['mismatched']}, "
            f"malformed={verification['malformed']})"
        )
    for posture in ("llm", "m2"):
        rel = "baselines/main.json" if posture == "llm" else "baselines/m2.json"
        baseline = schema.read_json(root / rel)
        issues += [f"{rel}: {issue}" for issue in schema.validate_baseline(baseline)]
        if baseline.get("fixtures_hash") != committed_hash:
            issues.append(f"{rel} fixtures_hash != on-disk corpus hash")
        cfg_posture = (baseline.get("config") or {}).get("extractor_posture")
        if cfg_posture != posture:
            issues.append(f"{rel}: config.extractor_posture {cfg_posture!r} != {posture!r}")
    return issues


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--check" in argv:
        drifted = check_drift()
        if drifted:
            print("CORPUS DRIFT:\n  " + "\n  ".join(drifted))
            return 1
        print("why-suite corpus is byte-identical to a fresh deterministic render")
        return 0
    if "--validate" in argv:
        issues = validate_committed()
        if issues:
            print("VALIDATION ISSUES:\n  " + "\n  ".join(issues))
            return 1
        print("committed why-suite corpus valid (40 planted points)")
        return 0
    written = write_corpus()
    issues = validate_committed()
    if issues:
        print("GENERATOR OUTPUT FAILED VALIDATION:\n  " + "\n  ".join(issues))
        return 1
    print(f"wrote {len(written)} files (40 planted points)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
