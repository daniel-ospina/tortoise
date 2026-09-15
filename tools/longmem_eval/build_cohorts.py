"""#2517 (C4): build the source-session re-injection measurement cohorts.

The plan pins the SELECTOR (not the data): the built files are written
OUTSIDE the repo (the eval's existing cache root, ``dataset.cache_dir()`` —
which honours ``TORTOISE_LME_CACHE_DIR``) because a 49 MB slice is not
committed; the committed builder plus the pinned selector is what makes the
measurement reproducible.

Cohorts (both materialized from the same source file, in source order):

  * ``tail`` — questions at index range ``s[150:250]`` (100 questions; the
    #2519 doc's tail slice — the cross-session-heavy stretch).
  * ``head`` — the first 50 ``single-session-user`` questions (a real
    ``question_type`` field value; the reader-item-cap / token-budget
    cohort).

The source path resolves through ``tools.longmem_eval.dataset`` (the single
source of truth: ``cache_dir()`` + ``SPLIT_FILES[DEFAULT_SPLIT]``), and a
known split file is verified against ``SPLIT_DIGESTS`` before slicing — a
tampered or truncated corpus must never be served as a silently different
denominator. The source sha256 is printed so a receipt can pin it.

Usage::

    python -m tools.longmem_eval.build_cohorts            # both cohorts
    python -m tools.longmem_eval.build_cohorts --cohort tail

Writes ``longmemeval_2517_<cohort>.json`` (the same list-of-question shape
``--data`` consumes) and prints the absolute paths.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from tools.longmem_eval.dataset import (
    DEFAULT_SPLIT,
    SPLIT_DIGESTS,
    SPLIT_FILES,
    cache_dir,
)

#: the pinned selectors (the receipt records them verbatim).
TAIL_SLICE = (150, 250)
HEAD_TYPE = "single-session-user"
HEAD_N = 50


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_source(path: Path) -> str:
    """Verify a KNOWN split file against its pinned digest (an unknown
    ``--source`` is used as-is, with its digest printed for the receipt)."""
    digest = _sha256(path)
    pinned = {name: SPLIT_DIGESTS[split]
              for split, name in SPLIT_FILES.items()
              if split in SPLIT_DIGESTS}
    expected = pinned.get(path.name)
    if expected is not None and expected != digest:
        raise SystemExit(
            f"{path} sha256 {digest} does not match the pinned digest "
            f"{expected} for {path.name} — the corpus is truncated or "
            "tampered; refusing to build a cohort from a wrong denominator")
    return digest


def build_cohorts(source: Path) -> dict[str, list[dict]]:
    """Materialize both cohorts from ``source`` (in-memory, source order)."""
    instances = json.loads(source.read_text(encoding="utf-8"))
    tail = instances[TAIL_SLICE[0]:TAIL_SLICE[1]]
    head = [q for q in instances if q.get("question_type") == HEAD_TYPE][
        :HEAD_N]
    return {"tail": tail, "head": head}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, default=None,
                    help="LongMemEval source file "
                         "(default: dataset.cache_dir()/"
                         "SPLIT_FILES[DEFAULT_SPLIT])")
    ap.add_argument("--out-dir", type=Path, default=None,
                    help="output directory (default: dataset.cache_dir())")
    ap.add_argument("--cohort", choices=("tail", "head", "both"),
                    default="both")
    args = ap.parse_args(argv)

    source = args.source or (cache_dir() / SPLIT_FILES[DEFAULT_SPLIT])
    if not source.exists():
        raise SystemExit(
            f"source corpus {source} not found — pass --source or set "
            "TORTOISE_LME_CACHE_DIR")
    out_dir = args.out_dir or cache_dir()
    digest = _verify_source(source)

    cohorts = build_cohorts(source)
    wanted = (("tail", "head") if args.cohort == "both" else (args.cohort,))
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in wanted:
        out = out_dir / f"longmemeval_2517_{name}.json"
        out.write_text(json.dumps(cohorts[name]), encoding="utf-8")
        print(f"{name}: {len(cohorts[name])} questions -> {out}")
    print(f"source: {source} sha256={digest}")
    print(f"selectors: tail={TAIL_SLICE} head=({HEAD_TYPE}, first {HEAD_N})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
