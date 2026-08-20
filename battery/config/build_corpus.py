"""Deterministic scenario-corpus builder (issue #1407).

Pipeline (pinned): validate-with-gold → recursive strip (delete every
``gold`` key at any depth) → post-strip proof (``assert_no_gold`` on the
emitted reader scenarios) → digest → emit.

Outputs:
* ``corpus.json`` (committed, pinned) — reader-facing: every scenario is
  gold-free and carries ``gold_sha256`` (sha256 of the canonical gold dict);
  manifest carries ``{corpus_version, content_sha256, golds_sha256,
  pack_counts, split_counts, family_coverage}``.
* ``.gold_store/golds.json`` (gitignored) — ``{scenario_id: gold}`` for the
  scorer path only.

Digest basis: ``canonical_json`` over DICTS (never file bytes); ``golds_sha256``
== ``GoldStore.digest()`` by construction. Emission uses sorted keys and sorted
scenario order; no sets, no timestamps, no absolute paths — two builds are
byte-identical across processes (determinism contract).

Run: ``uv run python -m battery.config.build_corpus [--out DIR] [--check]``
"""
from __future__ import annotations  # noqa: I001

import argparse
import hashlib
import json
import shutil  # noqa: F401
import sys
import tempfile
from pathlib import Path

from . import schema
from .corpus_loader import (
    GOLD_KEY,
    GOLD_HASH_KEY,
    assert_no_gold,
    canonical_json,
    _strip_gold,
)
from .validate import (
    load_yaml_dupreject,
    validate_attack_distribution,
    validate_controls,
    validate_held_out_family,
    validate_id_uniqueness,
    validate_pack_counts,
    validate_pack_splits,
    validate_scenario,
)

DEFAULT_SOURCE = Path(__file__).resolve().parent / "corpus.yaml"
DEFAULT_OUT_DIR = Path(__file__).resolve().parent
GOLD_STORE_REL = ".gold_store"
GOLD_STORE_FILE = "golds.json"


def _sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _validate(data: dict) -> list[str]:
    """Run every invariant. Raises ValueError listing all errors."""
    errors: list[str] = []
    meta = data.get("meta") or {}
    if meta.get("corpus_version") != schema.CORPUS_VERSION:
        errors.append(
            f"meta.corpus_version {meta.get('corpus_version')!r} != "
            f"schema.CORPUS_VERSION {schema.CORPUS_VERSION!r}")
    scenarios = data.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        errors.append("corpus is empty — refuse to build (mirrors harness E2E-1.4)")
        return errors
    ids = {str(s.get("id")) for s in scenarios}
    ct_ids = {str(s.get("id")) for s in scenarios if s.get("task_type") == "contradiction"}
    for sc in scenarios:
        errors.extend(validate_scenario(sc, ids, ct_ids))
    errors.extend(validate_id_uniqueness(scenarios))
    errors.extend(validate_controls(scenarios))
    errors.extend(validate_pack_splits(scenarios))
    errors.extend(validate_attack_distribution(scenarios))
    errors.extend(validate_pack_counts(scenarios))
    errors.extend(validate_held_out_family(scenarios))
    return errors


def _build_artifacts(data: dict, out_dir: Path) -> tuple[dict, dict]:
    """(corpus_json_dict, store_dict) — gold-free, hashed, sorted, sealed."""
    scenarios = list(data["scenarios"])
    scenarios.sort(key=lambda s: s["id"])

    store: dict[str, dict] = {}
    for sc in scenarios:
        gold = sc.get(GOLD_KEY)
        if not isinstance(gold, dict) or not gold.get("expected"):
            raise ValueError(f"scenario {sc['id']}: gold.expected missing")
        store[sc["id"]] = gold
        sc[GOLD_HASH_KEY] = _sha256_hex(canonical_json(gold))

    # Strip phase + post-strip proof over the EMITTED (reader) form.
    for sc in scenarios:
        _strip_gold(sc)
        assert_no_gold(sc)

    content_sha256 = _sha256_hex(canonical_json(scenarios))
    golds_sha256 = _sha256_hex(canonical_json(store))

    pack_counts = {t: 0 for t in schema.PACK_COUNTS}
    split_counts = {s: 0 for s in schema.SPLITS}
    families: set[str] = set()
    for sc in scenarios:
        pack_counts[sc["task_type"]] += 1
        split_counts[sc["split"]] += 1
        families.add(sc["family"])

    manifest = {
        "corpus_version": schema.CORPUS_VERSION,
        "content_sha256": content_sha256,
        "golds_sha256": golds_sha256,
        "pack_counts": dict(sorted(pack_counts.items())),
        "split_counts": dict(sorted(split_counts.items())),
        "family_coverage": sorted(families),
    }
    corpus = {"manifest": manifest, "scenarios": scenarios}
    return corpus, store


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")


def build_corpus(
    source: str | Path | None = None,
    out_dir: str | Path | None = None,
) -> Path:
    """Build corpus.json + the sealed gold store from the authored YAML.

    Returns the written corpus.json path. Raises ``ValueError`` (listing every
    violation) on any invalid input.
    """
    src = Path(source) if source else DEFAULT_SOURCE
    out = Path(out_dir) if out_dir else DEFAULT_OUT_DIR
    data = load_yaml_dupreject(src)
    errors = _validate(data)
    if errors:
        raise ValueError(
            f"corpus {src} failed validation with {len(errors)} error(s):\n  "
            + "\n  ".join(errors))
    corpus, store = _build_artifacts(data, out)
    corpus_path = out / "corpus.json"
    store_path = out / GOLD_STORE_REL / GOLD_STORE_FILE
    _write_json(corpus_path, corpus)
    _write_json(store_path, store)
    return corpus_path


def check_committed() -> int:
    """--check: rebuild into a temp dir; byte-diff corpus.json + store digest
    against the committed artifacts. Exit 0 clean / 1 drift."""
    committed = DEFAULT_OUT_DIR / "corpus.json"
    if not committed.is_file():
        print(f"--check: committed corpus.json not found at {committed}", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        rebuilt = Path(tmp) / "build"
        build_corpus(out_dir=rebuilt)
        fresh = (rebuilt / "corpus.json").read_bytes()
        if fresh != committed.read_bytes():
            print("--check: DRIFT — committed corpus.json differs from a fresh "
                  "build (rebuild and commit it)", file=sys.stderr)
            return 1
        committed_manifest = json.loads(committed.read_text(encoding="utf-8"))["manifest"]
        fresh_manifest = json.loads(fresh.decode("utf-8"))["manifest"]
        if committed_manifest.get("golds_sha256") != fresh_manifest.get("golds_sha256"):
            print("--check: DRIFT — golds_sha256 differs", file=sys.stderr)
            return 1
    print("--check: clean — committed corpus.json matches a fresh deterministic build")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build the sealed scenario corpus")
    ap.add_argument("--out", default=None, help="output directory (default: battery/config)")
    ap.add_argument("--source", default=None, help="corpus.yaml path (default: battery/config/corpus.yaml)")
    ap.add_argument("--check", action="store_true",
                    help="byte-diff a fresh build against the committed corpus.json")
    args = ap.parse_args(argv)
    if args.check:
        return check_committed()
    corpus_path = build_corpus(source=args.source, out_dir=args.out)
    print(f"built {corpus_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
