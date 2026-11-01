"""#10137 — build the PRE-REGISTERED stratified sample for the corpus census.

N is fixed HERE, before any run, and the selection is the COMMITTED selector
(``run_protocol.stratified_slice_indices`` via ``_build_stratified_slice``) —
never a head slice, which is the #2516 defect (the split's first 50 rows are
~100% single-session-user).

    PYTHONPATH=. TORTOISE_LME_CACHE_DIR=~/.cache/tortoise-longmemeval \
      python docs/evidence/10137-corpus-ep-census/probes/build_slice.py 24

Writes ``<out>/pilot_stratified_<N>.json`` + ``<out>/slice-meta.json`` (the
selected indices, question ids and types) outside the repo, so the run never
touches the repo's cache. ``--out`` defaults to ``/tmp/10137/run``.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

# repo root, resolved from this file (…/docs/evidence/<dir>/probes/<this>)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))

# No suppression directive here on purpose: ruff's E402 deliberately EXEMPTS an
# import that follows a `sys.path` modification (ruff PR #9047, v0.2.0), so a
# no-suppression-needed `E402` marker would be UNUSED and RUF100 reds the lint job.
from tools.longmem_eval.run_protocol import (
    ProtocolState,
    _build_stratified_slice,
    _load_cached_instances,
    stratified_slice_indices,
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("n", nargs="?", type=int, default=24)
    ap.add_argument("--out", default="/tmp/10137/run")
    args = ap.parse_args()
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    instances = _load_cached_instances("s")
    indices = stratified_slice_indices(
        [str(q.get("question_type") or "") for q in instances], n=args.n)
    rows = [{
        "index": int(i),
        "question_id": instances[i].get("question_id"),
        "question_type": instances[i].get("question_type"),
        "n_sessions": len(instances[i].get("haystack_sessions") or []),
    } for i in indices]

    state = ProtocolState(out / "state.json")
    state.data["run_dir"] = str(out)
    state.save()
    path, composition = _build_stratified_slice(
        state, args.n, materialize=True, instances=instances)

    meta = {
        "n_requested": args.n,
        "n_selected": len(indices),
        "selector": "tools/longmem_eval/run_protocol.py::stratified_slice_indices",
        "corpus_population": len(instances),
        "slice_path": str(path),
        "composition": composition,
        "rows": rows,
    }
    (out / "slice-meta.json").write_text(json.dumps(meta, indent=2))
    print(json.dumps({k: v for k, v in meta.items() if k != "rows"}, indent=2))


if __name__ == "__main__":
    main()
