"""E1's BEFORE arm — the same corpus with the v4 selection passes OFF.

Why this exists. `capstone_measure.py` FORCES the measured arm ON (and reads it
back), so every table it can produce has an "after" arm and no "before" arm, and
E1 (`after_share <= 0.50 x before_share`) is then scored off a *different*
artifact whose corpus is not the frozen one. This driver closes that gap: it
runs the SAME corpus through the SAME entry point with `TORTOISE_VET`,
`TORTOISE_CLASSIFY_LATER` and `TORTOISE_VALUE_GATE` OFF, and reports the same
two numbers the frozen E1 metric names — `:Object` count and the `^the <X>`
share — off the run's own graph.

What it does NOT do. It does not monkeypatch, wrap or re-implement any part of
the pipelined path: `run_corpus` and `census` are the shipped probe's own
functions, one pass over the pre-registered corpus, one census over the graph
the run wrote. The ONLY difference from `capstone_measure.py` is the arm.

⚠️ The honest limit, stated here so a reader does not have to find it: the OFF
and ON arms are separate LLM realizations of the same corpus, so a difference
between them is NOT attributable to the flag alone. The attribution is carried
by the deterministic gate-drop count (`e1_gate_drops.py`); the paired numbers
are the population E1's bar is written over.

Usage::

    export TORTOISE_DB_URI='docker://:<pw>@127.0.0.1:<port>/<graph>'
    python docs/evidence/7949-capstone/probes/e1_paired_arm.py \\
        --graph capstone_off_<head8>_<epoch> --out /tmp/off-arm.json

Exit status: **0** when the arm was measured and every session ran; **1** when
the arm was measured but a session failed; **2** when the run wrote fewer
`:Object` nodes than the frozen `denominator_floors.e1_objects` — the pre-
registration calls a reading below that floor `REFUSED, which is neither a pass
nor a fail`, so a share over it is not a before-share (and a zero denominator is
the same refusal at its limit: issue #9889 acceptance item 2). A refusal writes
**no record**, because an artifact is what a later reader scores and a refused
number is not one.

Exits non-zero, and measures nothing, if the shell already holds any of the
three flags — a mislabelled arm is worse than no arm — and before any model call
if the backend cannot extract at all (`backend_ready`: a keyless capture
extracts nothing, so it would be a measurement of the ABSENCE of a key, #3892).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[4]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_PROBES = Path(__file__).resolve().parent
if str(_PROBES) not in sys.path:
    sys.path.insert(0, str(_PROBES))

from capstone_measure import (  # noqa: E402
    _is_bare_definite,
    backend_ready,
    census,
    graph_uri,
    parse_bars,
    read_preregistration,
    resolve_head,
    run_corpus,
)

#: The three flags the measured arm pins. OFF means: absent, empty, or "0".
ARM_KEYS = ("TORTOISE_VET", "TORTOISE_CLASSIFY_LATER", "TORTOISE_VALUE_GATE")

#: A FOURTH switch for the same pass, and the reason it is checked beside the
#: three: `TORTOISE_S2_DECISION_SEAM` arms S2.2 *through the arbiter*, so
#: `_vet_enabled` reads True even against an explicit `TORTOISE_VET=0`
#: (`capstone_measure.ARM_OFF` pins it UNSET for exactly this, and #5087 is the
#: flag). Leaving it out would let this driver print `OFF` while the seam ran
#: the very pass the label denies — the mislabelled arm the refusal above exists
#: to prevent, arriving through the one key the acceptance list does not name.
SEAM_KEY = "TORTOISE_S2_DECISION_SEAM"


def off_arm_readback() -> dict[str, str | None]:
    """The arm actually in force, READ BACK — never a literal this file types."""
    held = {key: os.environ.get(key) for key in (*ARM_KEYS, SEAM_KEY)}
    on = {k: v for k, v in held.items() if v not in (None, "", "0")}
    if on:
        raise SystemExit(
            f"REFUSED: {on!r} is set — this driver measures the OFF arm; a run "
            "that prints OFF while the shell holds the passes ON measures the "
            "wrong population under the right label")
    return held


def object_floor() -> int:
    """E1's frozen Objects floor, read out of the pre-registration — never typed.

    E1 scores a SHARE, and the frozen record puts a floor under that share's
    DENOMINATOR (`denominator_floors.e1_objects`, criterion E1: "**all**
    `:Object` nodes written by the run, same arm, same commit (floor >= 30
    Objects)"). The floor is why `capstone_measure.e1_outcome` returns
    `below_floor=True` -> `REFUSED` instead of a verdict: with a handful of
    Objects a single delete moves the share by double digits, so a share over
    such a run is noise wearing a decimal point. This driver produces the very
    number that bar is written over, so it reads the same floor out of the same
    artifact — a floor typed into this file would be a floor nobody froze.
    """
    return parse_bars(read_preregistration(resolve_head()))["floors"][
        "e1_objects"]


def denominator_refusal(objects: int, floor: int) -> str | None:
    """The refusal for a denominator E1's own record refuses — message or None."""
    if objects == 0:
        return (f"REFUSED: the run wrote no :Object nodes — a share over a zero "
                f"denominator is not a before-share (#9889 acceptance item 2), "
                f"so this arm is not a measurement "
                f"(denominator_floors.e1_objects = {floor})")
    if objects < floor:
        return (f"REFUSED: the run wrote {objects} :Object nodes, under the "
                f"frozen denominator_floors.e1_objects = {floor} — E1's record "
                f"calls a run below that floor 'REFUSED, which is neither a "
                f"pass nor a fail', so a share over it is not a before-share")
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--graph", required=True,
                        help="the disposable graph on TORTOISE_DB_URI's container")
    parser.add_argument("--out", required=True,
                        help="where the arm record (JSON) is written")
    args = parser.parse_args(argv)

    from tests.eval.write_path.corpus import session_ids

    arm = off_arm_readback()
    print(f"arm read-back: {arm}", flush=True)

    ready, why = backend_ready()
    if not ready:
        raise SystemExit(f"REFUSED: {why}")

    floor = object_floor()
    base = os.environ["TORTOISE_DB_URI"]

    sessions = session_ids()
    started = time.time()
    runs = run_corpus(args.graph, sessions)
    data = census(graph_uri(base, args.graph))
    names = data["object_names"]
    print(f"objects={len(names)} floor={floor}", flush=True)
    refusal = denominator_refusal(len(names), floor)
    if refusal:
        print(refusal, flush=True)
        return 2

    bare = [name for name in names if _is_bare_definite(name)]
    record = {
        "arm": arm,
        "object_floor": floor,
        "graph": args.graph,
        "sessions": [{k: run[k] for k in
                      ("session_id", "ok", "extracted", "elapsed_s")}
                     for run in runs],
        "objects": len(names),
        "bare_definite": len(bare),
        "share": (len(bare) / len(names)) if names else None,
        "object_names": names,
        "label_counts": data["label_counts"],
        "elapsed_s": round(time.time() - started, 1),
    }
    Path(args.out).write_text(json.dumps(record, indent=1))
    failed = [run["session_id"] for run in runs if not run["ok"]]
    print(f"OFF arm: objects={len(names)} bare_definite={len(bare)} "
          f"share={record['share']} failed={failed} "
          f"elapsed={record['elapsed_s']}s")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
