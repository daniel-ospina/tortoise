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

Exits non-zero, and measures nothing, if the shell already holds any of the
three flags — a mislabelled arm is worse than no arm.
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
    census,
    graph_uri,
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
    base = os.environ.get("TORTOISE_DB_URI") or ""
    if not base.strip():
        raise SystemExit("REFUSED: TORTOISE_DB_URI is not set")

    sessions = session_ids()
    started = time.time()
    runs = run_corpus(args.graph, sessions)
    data = census(graph_uri(base, args.graph))
    names = data["object_names"]
    bare = [name for name in names if _is_bare_definite(name)]
    record = {
        "arm": arm,
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
