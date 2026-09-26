"""#2517 (C4): build the source-session re-injection measurement cohorts.

The plan pins the SELECTOR (not the data): the built files are written
OUTSIDE the repo (the eval's existing cache root, ``dataset.cache_dir()`` —
which honours ``TORTOISE_LME_CACHE_DIR``) because a 49 MB slice is not
committed; the committed builder plus the pinned selector is what makes the
measurement reproducible.

Cohorts (all three materialized from the same source file, in source order):

  * ``tail`` — questions at index range ``s[150:250]`` (100 questions; the
    #2519 doc's tail slice — the cross-session-heavy stretch).
  * ``head`` — the first 50 ``single-session-user`` questions (a real
    ``question_type`` field value; the reader-item-cap / token-budget
    cohort).
  * ``ms_tail`` — the WHOLE ``multi-session`` class inside the tail slice
    (the #2513 C4 measurement cohort; 71 of the 100 tail questions) — the
    class the re-injection arm claims to close, at its largest available n.

The source path resolves through ``tools.longmem_eval.dataset`` (the single
source of truth: ``cache_dir()`` + ``SPLIT_FILES[DEFAULT_SPLIT]``), and a
known split file is verified against ``SPLIT_DIGESTS`` before slicing — a
tampered or truncated corpus must never be served as a silently different
denominator. The source sha256 is printed so a receipt can pin it.

Usage::

    python -m tools.longmem_eval.build_cohorts            # all cohorts
    python -m tools.longmem_eval.build_cohorts --cohort tail

Writes ``longmemeval_2517_<cohort>.json`` (the same list-of-question shape
``--data`` consumes), a per-cohort provenance sidecar
``longmemeval_2517_<cohort>.provenance.json`` (``cohort``, ``questions``,
``source``, ``source_sha256``, ``verified``, ``selector`` — this cohort's
own —, ``selectors_pinned`` — the full pinned map —, and
``cohort_sha256`` — the cohort payload's own digest), and prints the
absolute paths.
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
    _read_instances,
    cache_dir,
)

#: the pinned selectors (the receipt records them verbatim).
TAIL_SLICE = (150, 250)
HEAD_TYPE = "single-session-user"
HEAD_N = 50

#: #2513 (C4) multi-session measurement cohort: the WHOLE multi-session
#: class inside the tail slice — the class the re-injection arm claims to
#: close, at its largest available n (71 of the 100 tail questions; the
#: tail's other 29 are temporal-reasoning / single-session-preference, which
#: the arm's turn-grain fetch does not address). The prior campaign measured
#: items [0:10] and [10:20] of this class as two independent samples; this
#: selector materializes the same 71 in source order, so that n=20 sample is
#: a strict SUBSET of this cohort and the two are directly comparable.
#: Selected by ``question_type`` (a real dataset field), in source order —
#: never re-sorted, so index identity is stable.
MS_TAIL_TYPE = "multi-session"

#: The per-cohort selector, keyed by cohort name — a sidecar records only
#: ITS cohort's entry, so it can never advertise a sibling's.
SELECTORS: dict[str, list] = {
    "tail": list(TAIL_SLICE),
    "head": [HEAD_TYPE, HEAD_N],
    "ms_tail": [list(TAIL_SLICE), MS_TAIL_TYPE],
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_source(path: Path, *, allow_unpinned: bool = False) -> tuple[str, bool]:
    """Verify the source against its pinned digest.

    A basename that matches a known split MUST match its ``SPLIT_DIGESTS``
    pin (a truncated or tampered corpus must never be sliced into a cohort
    that the receipt then pins a selector for). A basename that matches NO
    known split is refused unless ``allow_unpinned`` is set — on the path
    the cohorts actually travel (``--data`` →
    ``dataset.load_dataset(data_path=…)``) no digest check runs at all, so
    this is the only guard there. (``load_dataset``'s cached-split branch
    and the download path DO verify via ``_verify_digest``; the ``--data``
    branch does not.)

    Returns ``(sha256, verified)``.
    """
    digest = _sha256(path)
    pinned = {name: SPLIT_DIGESTS[split]
              for split, name in SPLIT_FILES.items()
              if split in SPLIT_DIGESTS}
    expected = pinned.get(path.name)
    if expected is None:
        if not allow_unpinned:
            raise SystemExit(
                f"{path} is not a pinned split corpus (known: "
                f"{sorted(pinned)}) — refusing to slice an unverified "
                "denominator; pass --allow-unpinned-source to override "
                "(the override is written to the provenance sidecar and "
                "printed)")
        return digest, False
    if expected != digest:
        raise SystemExit(
            f"{path} sha256 {digest} does not match the pinned digest "
            f"{expected} for {path.name} — the corpus is truncated or "
            "tampered; refusing to build a cohort from a wrong denominator")
    return digest, True


def build_cohorts(source: Path) -> dict[str, list[dict]]:
    """Materialize all cohorts from ``source`` (in-memory, source order).

    Parses through :func:`dataset._read_instances` — the eval lane's own
    reader for the ``--data`` path — so a JSONL source (one instance per
    line) is accepted alongside the documented JSON-list form instead of
    raising a bare ``json.JSONDecodeError``. The digest check stays on the
    RAW BYTES in :func:`_verify_source`.
    """
    instances = _read_instances(source)
    tail = instances[TAIL_SLICE[0]:TAIL_SLICE[1]]
    head = [q for q in instances if q.get("question_type") == HEAD_TYPE][
        :HEAD_N]
    ms_tail = [q for q in tail if q.get("question_type") == MS_TAIL_TYPE]
    return {"tail": tail, "head": head, "ms_tail": ms_tail}


def _write_provenance(out_dir: Path, *, name: str, source: Path,
                      source_sha256: str, verified: bool,
                      cohort_sha256: str, questions: int,
                      selector: list,
                      all_selectors: dict[str, list]) -> Path:
    """Persist how ONE cohort was built, next to that cohort file.

    The cohort payload stays the plain list-of-questions shape ``--data``
    consumes; provenance lives in a PER-COHORT sidecar so a cohort sliced
    from an UNVERIFIED corpus (``--allow-unpinned-source`` →
    ``verified=false``) can never be mistaken for a digest-verified one.

    Per-cohort (not one sidecar per directory) is load-bearing: the
    builder runs once per ``--cohort`` invocation, so a single shared file
    would be overwritten by the next build and leave an earlier cohort
    beside it reading as digest-verified — exactly the confusion the
    sidecar exists to prevent. ``cohort_sha256`` lets a reader validate
    the cohort payload independently of the invocation that wrote it.
    """
    prov = out_dir / f"longmemeval_2517_{name}.provenance.json"
    prov.write_text(json.dumps({
        "cohort": name,
        "cohort_sha256": cohort_sha256,
        "questions": questions,
        "source": str(source),
        "source_sha256": source_sha256,
        "verified": verified,
        # THIS sidecar's own selector (the one that produced the payload
        # whose ``cohort_sha256`` is pinned above). The full pinned map is
        # reported separately, so a reader never attributes a sibling
        # cohort's selector to this payload.
        "selector": selector,
        "selectors_pinned": all_selectors,
    }, indent=1) + "\n", encoding="utf-8")
    return prov


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, default=None,
                    help="LongMemEval source file "
                         "(default: dataset.cache_dir()/"
                         "SPLIT_FILES[DEFAULT_SPLIT])")
    ap.add_argument("--out-dir", type=Path, default=None,
                    help="output directory (default: dataset.cache_dir())")
    ap.add_argument("--cohort", choices=("tail", "head", "ms_tail", "both"),
                    default="both")
    ap.add_argument("--allow-unpinned-source", action="store_true",
                    help="slice a corpus whose basename matches no known "
                         "split (unverified; the override is recorded in "
                         "the provenance sidecar as verified=false)")
    args = ap.parse_args(argv)

    source = args.source or (cache_dir() / SPLIT_FILES[DEFAULT_SPLIT])
    if not source.exists():
        raise SystemExit(
            f"source corpus {source} not found — pass --source or set "
            "TORTOISE_LME_CACHE_DIR")
    out_dir = args.out_dir or cache_dir()
    digest, verified = _verify_source(
        source, allow_unpinned=args.allow_unpinned_source)

    cohorts = build_cohorts(source)
    want_all = args.cohort == "both"
    wanted = (("tail", "head", "ms_tail") if want_all else (args.cohort,))
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in wanted:
        out = out_dir / f"longmemeval_2517_{name}.json"
        payload = json.dumps(cohorts[name])
        out.write_text(payload, encoding="utf-8")
        print(f"{name}: {len(cohorts[name])} questions -> {out}")
        prov = _write_provenance(
            out_dir, name=name, source=source, source_sha256=digest,
            verified=verified,
            cohort_sha256=hashlib.sha256(
                payload.encode("utf-8")).hexdigest(),
            questions=len(cohorts[name]),
            selector=SELECTORS[name],
            all_selectors={k: list(v) for k, v in SELECTORS.items()})
        print(f"provenance: {prov}")
    print(f"source: {source} sha256={digest} "
          f"verified={str(verified).lower()}")
    print(f"selectors: tail={TAIL_SLICE} head=({HEAD_TYPE}, first {HEAD_N}) "
          f"ms_tail=({TAIL_SLICE}, {MS_TAIL_TYPE})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
