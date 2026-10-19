"""Build the E1+E2 measurement corpus (#9076) from two in-repo, provenance-bearing sources.

**Why a derived corpus and not a hand-written one.** #9075 (the pre-registration)
is what is supposed to freeze the corpus, and it has not landed. This module does
not pretend to replace it: it derives the candidate population from the two
artifacts the repo already versions, stamps every row with its origin, and the
probe refuses to SCORE anything until a frozen ``preregistration.json`` exists in
the commit named by ``--prereg``. So the corpus is reproducible and auditable now,
and it is still replaceable by #9075's corpus without touching the probe.

**The two sources, and what each one is FOR.**

1. ``tests/eval/write_path/salience_labels.jsonl`` — 25 real production Object
   names the OWNER ruled are not entities (``origin:
   prod-graph-audit-2026-09-23``, ``owner_confirmed: 2026-09-23``). This is the
   only owner-labelled Object population in the repo, so it is the **E1
   population**. ⚠️ It is a **detection specification, NEVER a drop list**
   (#4917's own header): four of the 25 names carry a LATER ``keep-object``
   ruling (``docs/engineering/1026-calibration-set.jsonl``, owner 2026-09-26) and
   deleting them would reverse that ruling. See ``keep_object_ruling`` on each
   row and the probe's drop table — no arm here discards any of the four.

   Each labelled name arrives with the ``statement_excerpt`` it was minted from.
   That statement becomes a ``Point`` carrying ``about_entities: [name]``, and it
   is why ``vet_gate.apply_vet`` Rule 2 has something to defend. The audit's
   ``reason`` field reads "referenced exactly once" on 15 of the 25 rows (and on
   7 of the 9 names the probe's mint-skip arm drops); the wiring of
   ``about_entities`` for all 25 is a **corpus-construction decision** the source
   supports — every row carries the excerpt — not a fact the reason field attests
   for every row. A corpus that emitted bare names with no referencing statement
   would measure a pipeline nobody runs.

2. ``tests/eval/write_path/gold/*.gold.json`` — the versioned write-path corpus's
   90 planted units across 7 sessions. These are the repo's own must-keep set
   (``planted_units``), and they are the **anti-gaming control**: the E1 share may
   not fall "only because fewer real facts are extracted", so every one of these
   must survive every arm. They also seed the **E2 state-value** class — the
   anchors carry the values the carve-out exists for ("stuck at 62 percent since
   3am", "ten-minute TTL", "it hit 4.2 seconds").

**What this corpus is NOT, stated so it cannot be misread.** The Object
population is the owner-labelled audit set, so the E1 *level* here (60.0%) is not
comparable to the architecture document's 62.3% production figure (that one is
over ALL Objects, real entities included; §1:85, reconstructed as 4,901/7,870 =
62.27% from the G5 census). Only the **delta** between arms is the measurement.

Run ``python docs/evidence/7949-capstone/corpus/build_e1e2_corpus.py`` to rewrite
``e1e2_corpus.json``; ``tests/test_e1_e2_measurement.py`` asserts the committed
file equals a fresh build, so a silent corpus edit fails a test instead of moving
a number.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
LABELS = ROOT / "tests/eval/write_path/salience_labels.jsonl"
GOLD_GLOB = "tests/eval/write_path/gold/*.gold.json"
CALIBRATION = ROOT / "docs/engineering/1026-calibration-set.jsonl"
OUT = Path(__file__).resolve().parent / "e1e2_corpus.json"

SCHEMA = "e1e2-corpus/1"

#: The later owner ruling (#1026, 2026-09-26) that rules four of the 25 audited
#: names MUST-KEEP. Keyed by the name as it appears in the calibration set.
KEEP_OBJECT_NAMES = (
    "guard 2",
    "_TOOL_BY_NAME",
    "daniel-ospina/agent-infra",
    "_capture_cost_props",
)


def _keep_object_names() -> set[str]:
    """Read the ``keep-object`` ruling from its committed home, not from a literal.

    The four-name tuple above is the *expectation*; this reads the ruling itself so
    that a change to the calibration set is visible here rather than silently
    un-defended. A calibration row is matched by its ``input`` field
    (``Object `<name>` — refs=…``).
    """
    import re

    names: set[str] = set()
    if not CALIBRATION.exists():
        return set(KEEP_OBJECT_NAMES)
    for line in CALIBRATION.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if row.get("label") != "keep-object":
            continue
        m = re.search(r"Object `([^`]+)`", str(row.get("input") or ""))
        if m:
            names.add(m.group(1).strip())
    return names


def _labels_points_and_objects() -> tuple[list[dict], list[dict]]:
    keep = _keep_object_names()
    objects: list[dict] = []
    points: list[dict] = []
    for i, line in enumerate(LABELS.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        name = row["object_name"].strip()
        oid = f"obj-audit-{i:02d}"
        objects.append({
            "id": oid,
            "name": name,
            "class": row["class"],
            "provenance": row.get("origin") or "prod-graph-audit-2026-09-23",
            "source": "tests/eval/write_path/salience_labels.jsonl",
            "owner_confirmed": row.get("owner_confirmed") or "",
            # #4917 + #1026: a row is a detection spec, and four names are
            # must-keep by a LATER owner ruling. Recorded per row, not in prose.
            "keep_object_ruling": name in keep,
        })
        points.append({
            "id": f"pt-audit-{i:02d}",
            "content": row["statement_excerpt"].strip(),
            "kind": "statement",
            "about": [name],
            "provenance": row.get("origin") or "prod-graph-audit-2026-09-23",
            "source": "tests/eval/write_path/salience_labels.jsonl",
            # The anti-gaming control is the PLANTED must-keep set, so the audit's
            # own statements are not counted as real facts here (some are
            # decision-ish; counting them would inflate the control).
            "must_keep": False,
        })
    return objects, points


def _planted_points() -> list[dict]:
    points: list[dict] = []
    for path in sorted(ROOT.glob(GOLD_GLOB)):
        gold = json.loads(path.read_text(encoding="utf-8"))
        session = gold["session_id"]
        for u in gold["planted_units"]:
            points.append({
                "id": f"pt-{u['id']}",
                "content": u["verbatim_anchor"].strip(),
                "kind": u["kind"],
                "about": [],
                "provenance": f"write_path_gold:{session}",
                "source": f"tests/eval/write_path/gold/{path.name}",
                "must_keep": True,
                "notability": u.get("notability") or "",
            })
    return points


def build() -> dict:
    objects, audit_points = _labels_points_and_objects()
    planted = _planted_points()

    contents = [p["content"] for p in audit_points + planted]
    dupes = sorted({c for c in contents if contents.count(c) > 1})
    if dupes:
        # A duplicate content collides on the content-addressed point id in
        # execute_embed and one of the two silently disappears — which would
        # corrupt the denominator before any gate ran.
        raise SystemExit(f"duplicate point contents in corpus: {dupes!r}")

    return {
        "schema": SCHEMA,
        "generated_by": "docs/evidence/7949-capstone/corpus/build_e1e2_corpus.py",
        "object_population": {
            "what": "the 25 owner-confirmed non-entity Object NAMES from the production audit",
            "why_this_population": (
                "it is the only owner-labelled Object population in the repo, so it is "
                "the only denominator that is not a guess; its LEVEL is not comparable "
                "to the architecture doc's 62.3% (that figure is over ALL Objects)"
            ),
            "count": len(objects),
        },
        "control_population": {
            "what": "the versioned write-path corpus's planted must-keep units (7 sessions)",
            "why_this_population": (
                "the E1 share may not fall only because fewer real facts are extracted, "
                "so these must survive every arm — they are the anti-gaming control"
            ),
            "count": len(planted),
        },
        "sources": [
            "tests/eval/write_path/salience_labels.jsonl",
            "tests/eval/write_path/gold/*.gold.json",
        ],
        "rulings_this_corpus_respects": {
            "labels_are_a_detection_specification_not_a_drop_list": "#4917 header",
            "keep_object_names": sorted(_keep_object_names()),
            "keep_object_source": "docs/engineering/1026-calibration-set.jsonl (#1026 owner ruling 2026-09-26)",
        },
        "objects": objects,
        "points": audit_points + planted,
    }


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    corpus = build()
    text = json.dumps(corpus, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    if "--check" in argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            print("COMMITTED CORPUS DIFFERS FROM A FRESH BUILD", file=sys.stderr)
            return 1
        print(f"corpus OK: {OUT.name} matches a fresh build "
              f"({len(corpus['objects'])} objects, {len(corpus['points'])} points)")
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT} — {len(corpus['objects'])} objects, {len(corpus['points'])} points")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
