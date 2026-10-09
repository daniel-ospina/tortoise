"""#2886 — census `frequency/count` disposition (structural vs conversion).

Reads the committed artifacts and emits the per-question disposition the
issue's acceptance asks for:

* ``tests/_assembly_census.json`` — the 133-row temporal taxonomy (the 12
  rows labelled ``frequency/count``);
* ``docs/runbook/2578-measured-outcomes-133.jsonl`` — the 133-question,
  single-arm (``A-default-133q``) per-question graded outcomes.

For each of the 12 rows the tool:

1. classifies the question's ACTUAL temporal-aggregation shape with the
   deterministic core (``tortoise.temporal_aggregation``) — the measured
   finding is that NONE is a count/frequency surface;
2. reads the committed outcome and assigns a disposition:

   * ``structural`` — gold was never admitted (write-side / admission
     problem); the deterministic core has no anchors to resolve;
   * ``conversion`` — gold admitted and the reader still answered wrong
     (reader-model problem, the #2013 leg);
   * ``fixed-by-admission`` — gold admitted and the reader answered right;
   * ``abstention-control`` — an ``_abs`` row whose correct answer is to
     refuse (a correct refusal is not a capability fix).

Output: ``docs/runbook/2886-frequency-count-outcomes.jsonl`` in the same row
shape as ``2578-measured-outcomes.jsonl`` (the 133 arm), plus the
``aggregate_kind``/``aggregate_unit`` classification and the ``disposition``
fields. Deterministic: same committed inputs → byte-identical output.

Honesty caveat emitted in the summary: for this class NO gold-admitted class
member was ever observed (the three arms in the 8-arm file that carry
``gold_admitted`` rows — ``applied-rerank`` 21, ``cap3-only`` 21,
``tr_top_k24`` 1 (qid ``8c18457d``) — run the 55-Q subset, which excludes all
12). The ``conversion_undetermined`` flag is derived from a UNION scan over
the two committed outcome files declared in ``OUTCOME_SOURCES`` (every arm in
each), not just the default arm — so it establishes "no class member ever had
gold admitted in the committed data", not merely "in the loaded arm".
Resolving it needs the 12 re-run under ``applied-rerank``; that is the
reported remainder (Refs #2886).
"""
from __future__ import annotations

import json
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from tortoise.temporal_aggregation import (  # noqa: E402
    classify_temporal_aggregate,
)

CENSUS_DEFAULT = "tests/_assembly_census.json"
OUTCOMES_DEFAULT = "docs/runbook/2578-measured-outcomes-133.jsonl"
#: The 8-arm, 55-Q committed run (``A-default`` …, ``applied-rerank``,
#: ``cap3-only``). Scanned — every arm — for the reachability flag.
OUTCOMES_8ARM = "docs/runbook/2578-measured-outcomes.jsonl"
OUT_DEFAULT = "docs/runbook/2886-frequency-count-outcomes.jsonl"

#: Every committed per-question outcome file that could carry one of the 12
#: class members. ``conversion_undetermined`` is derived from the UNION over
#: these (all arms) — a run present anywhere in the committed data counts.
OUTCOME_SOURCES: tuple[str, ...] = (OUTCOMES_DEFAULT, OUTCOMES_8ARM)

#: The issue's own class label in the census.
CLASS = "frequency/count"


def _resolve(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() or p.exists() else _REPO / p


def load_census(path: str | Path = CENSUS_DEFAULT) -> dict:
    with open(_resolve(path), encoding="utf-8") as fh:
        return json.load(fh)


def load_outcomes(path: str | Path = OUTCOMES_DEFAULT, *,
                  arm: str | None = "A-default-133q") -> dict[str, dict]:
    """Load the committed per-question outcomes keyed by qid (optionally
    filtered to one arm)."""
    out: dict[str, dict] = {}
    with open(_resolve(path), encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if arm is not None and row.get("arm") != arm:
                continue
            out[str(row.get("qid"))] = row
    return out


def is_answerable(qid: str) -> bool:
    """Abstention-DESIGN rows (``_abs``) are excluded from the answerable
    denominator — refusing is the correct behaviour."""
    return not str(qid or "").endswith("_abs")


def gold_admitted_qids(
    census: Mapping,
    sources: Iterable[str | Path] = OUTCOME_SOURCES,
) -> set[str]:
    """The class qids that had gold admitted under ANY committed run.

    Scans every row — every arm — of every committed outcome file in
    ``sources`` (default: the two files in :data:`OUTCOME_SOURCES`) — so
    ``conversion_undetermined`` establishes what it claims: no class member
    was ever observed with gold admitted in the committed data, not merely
    in the one default arm. Missing source files are skipped (the caller's
    committed tree may be partial); a present file contributes every arm.
    """
    class_qids = {str(r["qid"]) for r in census.get("rows", [])
                  if r.get("cls") == CLASS}
    admitted: set[str] = set()
    for path in sources:
        p = _resolve(path)
        if not p.exists():
            continue
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                qid = str(row.get("qid"))
                if qid in class_qids and row.get("gold_admitted"):
                    admitted.add(qid)
    return admitted


def disposition_for(outcome: Mapping) -> str:
    """The structural-vs-conversion disposition of one committed outcome.

    * gold never admitted → ``structural`` (answerable) or
      ``abstention-control`` (an abstention-design row whose correct answer
      is the refusal);
    * gold admitted and answered right → ``fixed-by-admission``;
    * gold admitted and answered wrong → ``conversion``.
    """
    qid = str(outcome.get("qid"))
    if not outcome.get("gold_admitted"):
        return "structural" if is_answerable(qid) else "abstention-control"
    return "fixed-by-admission" if outcome.get("label") else "conversion"


def build_rows(census: Mapping, outcomes: Mapping[str, dict]) -> list[dict]:
    """One disposition row per census class member, in census order."""
    rows: list[dict] = []
    for row in census.get("rows", []):
        if row.get("cls") != CLASS:
            continue
        qid = row["qid"]
        outcome = outcomes.get(qid)
        intent = classify_temporal_aggregate(row["question"])
        base = {
            "arm": outcome.get("arm") if outcome else None,
            "qid": qid,
            "cls": outcome.get("cls") if outcome else row.get("cls"),
            "reclassified_cls": (
                intent.kind.value if intent is not None else "unclassified"),
            "aggregate_kind": intent.kind.value if intent is not None else None,
            "aggregate_unit": intent.unit if intent is not None else None,
            "label": outcome.get("label") if outcome else None,
            "context_tokens": outcome.get("context_tokens") if outcome else None,
            "gold_admitted": (
                outcome.get("gold_admitted") if outcome else None),
            "reader_refusal": (
                outcome.get("reader_refusal") if outcome else None),
            "disposition": disposition_for(outcome) if outcome else "unmeasured",
            "answer": outcome.get("answer") if outcome else None,
            "issue": 2886,
        }
        rows.append(base)
    return rows


def summarize(rows: Iterable[Mapping], *,
              reachable_qids: set[str] | None = None) -> dict:
    """The acceptance summary: counts per disposition + the conversion
    reachability caveat.

    ``reachable_qids`` is the set of class qids observed with gold admitted
    under ANY committed run (see :func:`gold_admitted_qids`). When omitted,
    it is derived from ``rows`` (the loaded arm only) — callers that want the
    union-scan claim must pass the union set explicitly.
    """
    rows = list(rows)
    counts = {"structural": 0, "conversion": 0, "fixed-by-admission": 0,
              "abstention-control": 0, "unmeasured": 0}
    for r in rows:
        counts[r["disposition"]] = counts.get(r["disposition"], 0) + 1
    n_answerable = sum(1 for r in rows if is_answerable(str(r["qid"])))
    if reachable_qids is None:
        reachable_qids = {str(r["qid"]) for r in rows
                          if r.get("gold_admitted")}
    return {
        "n": len(rows),
        "n_answerable": n_answerable,
        "structural": counts["structural"],
        "conversion": counts["conversion"],
        "fixed_by_admission": counts["fixed-by-admission"],
        "abstention_controls": counts["abstention-control"],
        "unmeasured": counts["unmeasured"],
        # conversion is UNREACHABLE (not zero) when no class member ever had
        # gold admitted under ANY run present in the committed data (the
        # union scan), not merely in the loaded arm.
        "conversion_undetermined": not reachable_qids,
    }


def write_rows(rows: list[dict], path: str | Path = OUT_DEFAULT) -> Path:
    p = _resolve(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r, sort_keys=False, ensure_ascii=False) for r in rows]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--census", default=CENSUS_DEFAULT)
    ap.add_argument("--outcomes", default=OUTCOMES_DEFAULT)
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--print", action="store_true", dest="do_print")
    args = ap.parse_args(argv)
    census = load_census(args.census)
    outcomes = load_outcomes(args.outcomes)
    rows = build_rows(census, outcomes)
    if args.do_print:
        for r in rows:
            print(f"{r['qid']:18s} {r['aggregate_kind']!s:14s} "
                  f"{r['disposition']}")
    print(json.dumps(
        summarize(rows, reachable_qids=gold_admitted_qids(census)),
        indent=2))
    p = write_rows(rows, args.out)
    print(f"wrote {len(rows)} rows → {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
