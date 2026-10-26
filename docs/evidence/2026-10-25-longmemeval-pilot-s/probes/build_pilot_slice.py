"""Build the run protocol's step-3 TYPE-STRATIFIED pilot slice with the
COMMITTED selection code — never a head slice (the #2516 defect: the split's
first 50 rows are ~100% single-session-user).

Writes into a scratch directory outside the repo, so the run does not touch
the repo's `.longmemeval_cache`:

    PYTHONPATH=. python docs/evidence/2026-10-25-longmemeval-pilot-s/probes/build_pilot_slice.py

Output: <scratch>/pilot_stratified_50.json + the composition it printed.
"""
from __future__ import annotations

import json
import pathlib
import sys

# repo root, resolved from this file (…/docs/evidence/<dir>/probes/<this>)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))

from tools.longmem_eval.run_protocol import (
    PILOT_SLICE_N,
    ProtocolState,
    _build_stratified_slice,
)

RUN = pathlib.Path("/tmp/8486/run")
RUN.mkdir(parents=True, exist_ok=True)
state = ProtocolState(RUN / "state.json")
state.data["run_dir"] = str(RUN)
state.save()

path, composition = _build_stratified_slice(state, PILOT_SLICE_N, materialize=True)
print(json.dumps({"slice": str(path), "composition": composition}, indent=2))
