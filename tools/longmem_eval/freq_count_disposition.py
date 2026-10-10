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
12). The ``conversion_undetermined`` flag reports whether the rows being
summarized observed a gold-admitted class member at all, so a ``conversion``
of 0 in those rows is undetermined, not measured. The UNION scan over the
two committed outcome files declared in ``OUTCOME_SOURCES`` (every arm in
each) is reported separately as ``conversion_reachable_any_arm``, so it
establishes "no class member ever had gold admitted in the committed data"
without being conflated with the loaded arm.
Resolving it needs the 12 re-run under ``applied-rerank``; that is the
reported remainder (Refs #2886).
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/longmem_eval/freq_count_disposition.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python -m tools.longmem_eval.freq_count_disposition`"
    )

import json
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
#: ``cap3-only``). Scanned — every arm — for the union reachability fact
#: (``conversion_reachable_any_arm``).
OUTCOMES_8ARM = "docs/runbook/2578-measured-outcomes.jsonl"
OUT_DEFAULT = "docs/runbook/2886-frequency-count-outcomes.jsonl"

#: Every committed per-question outcome file that could carry one of the 12
#: class members. The UNION over these (all arms) feeds
#: ``conversion_reachable_any_arm`` — a run present anywhere in the committed
#: data counts. ``conversion_undetermined`` itself tracks the rows being
#: summarized, so an admission in another arm never makes that 0 measured.
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


def gold_admitted_scan(
    census: Mapping,
    sources: Iterable[str | Path] = OUTCOME_SOURCES,
) -> tuple[set[str], list[str], list[str]]:
    """``(admitted_qids, scanned, missing)`` over the declared sources.

    Scans every row — every arm — of every committed outcome file in
    ``sources`` (default: the two files in :data:`OUTCOME_SOURCES`), so the
    union fact it returns (``conversion_reachable_any_arm``) establishes
    what it claims: whether any class member was ever observed with gold
    admitted in the committed data, not merely in the one default arm. It
    does NOT decide ``conversion_undetermined``, which tracks the rows being
    summarized.

    ``missing`` names every declared source that was NOT read. Absence is
    not evidence: an empty ``admitted`` must not be read as "no class member
    was ever admitted" without knowing which files were actually scanned,
    so the summary carries this list beside the flag.
    """
    class_qids = {str(r["qid"]) for r in census.get("rows", [])
                  if r.get("cls") == CLASS}
    admitted: set[str] = set()
    scanned: list[str] = []
    missing: list[str] = []
    for path in sources:
        p = _resolve(path)
        if not p.exists():
            missing.append(str(path))
            continue
        scanned.append(str(path))
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                qid = str(row.get("qid"))
                if qid in class_qids and row.get("gold_admitted"):
                    admitted.add(qid)
    return admitted, scanned, missing


def gold_admitted_qids(
    census: Mapping,
    sources: Iterable[str | Path] = OUTCOME_SOURCES,
) -> set[str]:
    """The class qids that had gold admitted under ANY committed run.

    Thin wrapper over :func:`gold_admitted_scan` for callers that want only
    the qid set; see it for the missing-source reporting.
    """
    return gold_admitted_scan(census, sources)[0]


def disposition_for(outcome: Mapping) -> str:
    """The structural-vs-conversion disposition of one committed outcome.

    * gold never admitted → ``structural`` (answerable) or
      ``abstention-control`` (an abstention-design row that RECORDS the
      reader refusing — the row's ``reader_refusal`` must be True; without
      that evidence the disposition is ``unmeasured``);
    * gold admitted and answered right → ``fixed-by-admission``;
    * gold admitted and answered wrong → ``conversion``;
    * a row that OMITS ``gold_admitted`` (or an admitted row that omits
      ``label``) → ``unmeasured`` — an absent field is not a finding, and
      reading it as one invents the disposition the tool exists to report.
    """
    qid = str(outcome.get("qid"))
    admitted = outcome.get("gold_admitted")
    if admitted is None:
        return "unmeasured"
    if not admitted:
        if is_answerable(qid):
            return "structural"
        # An abstention-DESIGN row is a correct refusal only when the row
        # itself records the refusal: without that evidence, claiming
        # "abstention-control" would credit a reader that answered instead.
        return ("abstention-control"
                if outcome.get("reader_refusal") is True else "unmeasured")
    label = outcome.get("label")
    if label is None:
        # Admitted with no recorded answer outcome: this row cannot be
        # counted as ``conversion`` (the acceptance metric) or as fixed.
        return "unmeasured"
    return "fixed-by-admission" if label else "conversion"


def resolution_for(outcome: Mapping) -> str:
    """How the #2886 deterministic temporal-aggregation arm resolved the
    question, read off the per-outcome verdict the arm records.

    * ``resolved`` — the owner (``resolve_temporal_aggregate``) published a
      MEASURED value (the deterministic path closed it);
    * ``abstained:<reason>`` — the owner declined (no anchors / no events /
      no unit / a TOTAL that rode zero spans — never a guess);
    * ``unmeasured`` — the committed outcome carries NO verdict (the arm was
      OFF, the default, or the row predates the wiring). An absent field is
      never read as a resolution.

    This is the field that turns the acceptance's "how many of the 12 were
    fixed" into a read-out once a gold-admitting run records the arm.
    """
    verdict = outcome.get("temporal_aggregate_verdict")
    if not isinstance(verdict, Mapping):
        return "unmeasured"
    # A TOTAL over span-less hits PUBLISHES the module's documented zero-span
    # sum (a non-None ``value`` whose span-days sum is 0) — the adapter's own
    # "span honesty" contract says that is NOT a measured sum, so crediting it
    # as a resolution would overstate what the deterministic path closed. The
    # signal (``span_days``) is the SAME core tally the resolver rode, so the
    # two cannot disagree. A TOTAL that abstained for another reason
    # (``no_events`` / ``no_unit``) keeps the owner's own reason below.
    if (verdict.get("kind") == "total" and verdict.get("value") is not None
            and not verdict.get("span_days")):
        return "abstained:no_span_bounds"
    if verdict.get("value") is not None:
        return "resolved"
    return f"abstained:{verdict.get('reason') or 'unknown'}"


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
            # Carried through unchanged so an emitted row is a strict
            # superset of the 2578 row shape: a joiner reading the pool
            # geometry (which every 2578 row carries) must not KeyError.
            "pool_limit": outcome.get("pool_limit") if outcome else None,
            "pool_depth": outcome.get("pool_depth") if outcome else None,
            "gold_admitted": (
                outcome.get("gold_admitted") if outcome else None),
            "reader_refusal": (
                outcome.get("reader_refusal") if outcome else None),
            "disposition": disposition_for(outcome) if outcome else "unmeasured",
            # #2886: how the deterministic arm resolved the question when it
            # ran (the wiring's read-out); ``unmeasured`` when the committed
            # outcome carries no verdict (arm OFF — the default).
            "deterministic_resolution": (
                resolution_for(outcome) if outcome else "unmeasured"),
            "answer": outcome.get("answer") if outcome else None,
            "issue": 2886,
        }
        rows.append(base)
    return rows


def summarize(rows: Iterable[Mapping], *,
              reachable_qids: set[str] | None = None,
              union_sources_missing: list[str] | None = None) -> dict:
    """The acceptance summary: counts per disposition + the conversion
    reachability caveat.

    ``reachable_qids`` is the set of class qids observed with gold admitted
    under ANY committed run (see :func:`gold_admitted_qids`). When omitted,
    it is derived from ``rows`` (the loaded arm only) — callers that want the
    union-scan claim must pass the union set explicitly.

    ``conversion_undetermined`` is always derived from the rows being
    summarized (the loaded arm): an admission in some OTHER arm does not
    test these rows, so it cannot make a ``conversion`` of 0 measured.
    ``reachable_qids`` feeds only ``conversion_reachable_any_arm``.
    ``union_sources_missing`` names the declared sources the union scan did
    NOT read (None = no scan was performed by this caller), so the flag can
    never be read as a finding about files that were never opened.
    """
    rows = list(rows)
    counts = {"structural": 0, "conversion": 0, "fixed-by-admission": 0,
              "abstention-control": 0, "unmeasured": 0}
    for r in rows:
        counts[r["disposition"]] = counts.get(r["disposition"], 0) + 1
    n_answerable = sum(1 for r in rows if is_answerable(str(r["qid"])))
    loaded_admitted = {str(r["qid"]) for r in rows if r.get("gold_admitted")}
    resolution = {"resolved": 0, "abstained": 0, "unmeasured": 0}
    for r in rows:
        value = str(r.get("deterministic_resolution") or "unmeasured")
        if value == "resolved":
            resolution["resolved"] += 1
        elif value.startswith("abstained:"):
            resolution["abstained"] += 1
        else:
            resolution["unmeasured"] += 1
    if reachable_qids is None:
        reachable_qids = loaded_admitted
    return {
        "n": len(rows),
        "n_answerable": n_answerable,
        "structural": counts["structural"],
        "conversion": counts["conversion"],
        "fixed_by_admission": counts["fixed-by-admission"],
        "abstention_controls": counts["abstention-control"],
        "unmeasured": counts["unmeasured"],
        # #2886: the deterministic arm's read-out over these rows — resolved
        # (a value published) vs abstained (never a guess, with reason).
        # ``unmeasured`` is the default (arm OFF / pre-wiring outcome).
        "deterministic_resolution": resolution,
        # ``conversion == 0`` is a MEASUREMENT only when the rows being
        # summed actually observed gold admitted for a class member; an
        # admission in some OTHER arm (the union scan) never tested these
        # rows, so it cannot make this zero measured.
        "conversion_undetermined": not loaded_admitted,
        # The union fact is kept, separately, so it is neither lost nor
        # conflated with the loaded-arm measurement above.
        "conversion_reachable_any_arm": bool(reachable_qids),
        # Which declared sources the union scan actually READ: None means this
        # caller performed no scan, [] means all were present. A missing
        # source makes `false` unreadable as "nothing was ever admitted".
        "union_sources_missing": (None if union_sources_missing is None
                                  else sorted(union_sources_missing)),
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
    admitted, _scanned, missing = gold_admitted_scan(census)
    print(json.dumps(
        summarize(rows, reachable_qids=admitted,
                  union_sources_missing=missing),
        indent=2))
    p = write_rows(rows, args.out)
    print(f"wrote {len(rows)} rows → {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
