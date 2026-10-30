"""E1's mechanism, measured: does the v4 value gate drop ANY `^the <X>` Object?

Why this exists. E1's bar is written over a class (`^the <X>` `:Object` nodes)
that one pass either acts on or does not. `capstone_measure.py` can report the
SHARE a run produced, but a share cannot say whether the v4 selection path
touched it: two runs of the same arm differ by more than the bar's whole width
(measured 12.0% and 54.0% on the same corpus). This driver answers the
deterministic question instead, with ZERO model calls: apply the EXACT predicate
the S2.2 pass applies (`tortoise.value_gate.mechanical_verdict`, called from
`tortoise/vet_gate.py` at the `TORTOISE_VALUE_GATE` seam) to the Object names a
run actually wrote, and count the drops.

The positive controls are not decoration. A probe that reports "0 drops" without
showing that the predicate CAN drop would be indistinguishable from one whose
predicate is dead code, so two known identifier-only names (`#4118`, a path) and
the owner's must-keep (`the admin-merge rail`) are run through the same call and
printed beside the count.

Usage::

    export TORTOISE_DB_URI='docker://:<pw>@127.0.0.1:<port>/<graph>'
    python docs/evidence/7949-capstone/probes/e1_gate_drops.py \\
        --graph capstone_measure_<head8>_<epoch> --out /tmp/e1-gate.json

Exit status: 0 when the measurement was taken (whatever the number is — a gate
that drops nothing is a RESULT, not a failure); 2 when the graph yields no
Object names, because a zero-denominator reading of "0 drops" is not a result.

⚠️ What a 0-drop RESULT does NOT say. `vet_gate.apply_vet` Rule 2 downgrades a
discard whose Object is still REFERENCED by a statement, and every audited Object
in the frozen corpus is wired to the statement it was minted from — so this
driver measures the PREDICATE, not the pass's net effect (the control that
separates the two is `e1_e2_probe.discriminating_control`, `E1-E2.md` limit 6).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[4]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_PROBES = Path(__file__).resolve().parent
if str(_PROBES) not in sys.path:
    sys.path.insert(0, str(_PROBES))

from capstone_measure import census, graph_uri  # noqa: E402

#: Two names the predicate MUST drop and one it MUST keep. If the first pair
#: stops dropping, the predicate is dead and the zero below means nothing.
CONTROLS = ("#4118", "a/b/c.py", "the admin-merge rail")


def measure(graph: str) -> dict:
    from tortoise import value_gate
    from tortoise.definite_description_backfill import (
        is_bare_definite_description,
    )

    base = os.environ.get("TORTOISE_DB_URI") or ""
    if not base.strip():
        raise SystemExit("REFUSED: TORTOISE_DB_URI is not set")
    names = census(graph_uri(base, graph))["object_names"]
    bare = [name for name in names if is_bare_definite_description(name)]
    drops = {}
    for name in names:
        verdict = value_gate.mechanical_verdict(name)
        if verdict is not None:
            drops[name] = verdict["rule_id"]
    controls = {name: value_gate.mechanical_verdict(name) for name in CONTROLS}
    alive = (controls["#4118"] is not None
             and controls["a/b/c.py"] is not None
             and controls["the admin-merge rail"] is None)
    return {
        "graph": graph,
        "objects": len(names),
        "bare_definite": len(bare),
        "bare_definite_share": (len(bare) / len(names)) if names else None,
        "value_gate_drops": drops,
        "drops_total": len(drops),
        "drops_among_bare_definite": [n for n in bare if n in drops],
        "controls": controls,
        "predicate_alive": alive,
        "object_names": names,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--graph", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    record = measure(args.graph)
    Path(args.out).write_text(json.dumps(record, indent=1))
    print(f"objects={record['objects']} bare_definite={record['bare_definite']} "
          f"share={record['bare_definite_share']}")
    print(f"value_gate drops: {record['drops_total']} "
          f"(among bare_definite: {len(record['drops_among_bare_definite'])}) "
          f"controls alive={record['predicate_alive']}")
    if not record["objects"]:
        print("REFUSED: the graph yields no Object names — 0 drops would be a "
              "zero-denominator reading, not a result")
        return 2
    if not record["predicate_alive"]:
        print("REFUSED: the positive controls no longer drop — the predicate is "
              "dead, so the drop count measures nothing")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
