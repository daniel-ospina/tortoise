"""Raw-vs-narrative falsifier — extractor v4, E5 (tortoise #7947).

Pre-registered: the corpus, split, scoring and decision rule are fixed in the
issue comment BEFORE any run. Nothing here may be tuned after a run starts.

**The question (§10's falsifier):** does the narrative lose the hedging / scope /
stance information a claim's confidence depends on?

**The two arms differ in exactly one value.** In the production pipeline
(``extractor_v2.extract_session_v2``) the S2 extraction call consumes ``story``::

    story = compile_stories(chunk_stories)
    embed_list = run_s2(model, story, master, session_date=..., edus=edus, ...)

* **arm B (narrative, as shipped):** ``story = compile_stories(chunk_stories)``
* **arm A (raw):** ``story = _edus_to_text(edus)`` — the raw transcript

Everything else is byte-identical: one pinned model, one ``run_s2`` (so one
prompt scaffolding), one chunking rule, the same ``edus``. That is what makes
the comparison mean something — if the two arms used different prompts, a
difference would say nothing about the input.

Arm B's S1 calls are REAL (the shipped ``run_s1``); they are simply not needed
for arm A, whose whole point is to reach S2 without a narrative.

Usage (keys come from the repo .env — always go through the wrapper, #2718/#4860)::

    PYTHONPATH=$PWD tools/run-with-eval-keys.sh \\
      .venv/bin/python -m tests.eval.write_path.raw_vs_narrative --sessions wp01_quarry_debug

Output: one JSON per session under ``--out`` (default ``~/.cache/pi-7947/arms``),
holding both arms' ``embed_list``, the story each arm fed to S2, and the run's
pinned parameters. Scoring is a separate step (``score_qualifiers.py``).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import tortoise.extractor_v2 as ev2  # noqa: E402

WRITE_PATH_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = WRITE_PATH_DIR / "fixtures"
GOLD_DIR = WRITE_PATH_DIR / "gold"

#: Frozen for this measurement. Changing any of these invalidates a run.
MODEL_ID = "deepseek/deepseek-v4-flash"
CHUNK_SIZE = 50
#: The date stamped INTO every receipt. It is a protocol constant, not "today":
#: it is part of the extraction cache key and it is what makes a repeat run
#: comparable, so it must not drift between runs of the same registration.
#: ⚠️ Consequence, and it bit run 2: a receipt produced on 2026-10-18 still
#: says 2026-10-10, so `session_date` is NOT a run timestamp — the run date is
#: `results/`' filename and this docstring. Do not read it as evidence of when
#: a receipt was produced.
SESSION_DATE = "2026-10-10"

#: The two pre-registered arms, in report order.
ARMS = ("raw", "narrative")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def build_inputs(model, conversation: list[dict], *, chunk_size: int = CHUNK_SIZE):
    """Return the S2 input for each arm, plus what produced it.

    Arm B calls the SHIPPED S1 (real generation). Arm A skips S1 entirely and
    hands S2 the raw transcript — the narrative is never produced, which is
    exactly what "extract without the narrative" means.

    ⛔ **An S1 failure is recorded, not swallowed silently.** A per-chunk S1 raise
    is non-fatal in production too, so the loop carries on — but the narrative
    story is then MISSING a chunk, and scoring it as if complete can drop claims
    (raising narrative loss, i.e. manufacturing a branch-1 fire) or move a cell
    across the band. That fact therefore goes into the receipt (`errors`, and
    `s1_stats` so a length-truncated S1 chunk — `extractor_v2.run_s1`'s documented
    one-shot escalation path — leaves a trace too), and `score_qualifiers`
    consumes it on the narrative arm. Before this, the list was written and read
    by nothing: the same "the flag was in the receipt, unread" defect this
    measurement had just fixed for S2, left open for S1.
    """
    edus = ev2._edus_from_conversation(conversation)
    if not edus:
        return {"edus": [], "chunks": 0, "stories": {}, "errors": ["no conversation content"],
                "s1_stats": []}

    raw_text = ev2._edus_to_text(edus)

    chunks = ev2.chunk_transcript(edus, target=chunk_size)
    chunk_stories: list[str] = []
    errors: list[str] = []
    s1_stats: list[dict] = []
    for chunk in chunks:
        stats: dict = {}
        ok = True
        try:
            chunk_stories.append(
                ev2.run_s1(model, ev2._edus_to_text(chunk), session_date=SESSION_DATE,
                           stats=stats)
            )
        except Exception as e:  # per-chunk failure is non-fatal in production too
            ok = False
            errors.append(f"S1 chunk failed: {type(e).__name__}: {e}")
        finally:
            # `finish_reason == "length"` is S1's documented tail-drop signal; it
            # is never an exception, so without these stats it is invisible.
            entry: dict = {"ok": ok}
            for key in ("finish_reason", "recovery"):
                if key in stats:
                    entry[key] = stats[key]
            s1_stats.append(entry)
    narrative = ev2.compile_stories(chunk_stories) if chunk_stories else ""

    return {
        "edus": edus,
        "chunks": len(chunks),
        "chunk_stories": chunk_stories,
        "stories": {"raw": raw_text, "narrative": narrative},
        "errors": errors,
        "s1_stats": s1_stats,
    }


def run_arms(model, conversation: list[dict], *, chunk_size: int = CHUNK_SIZE):
    """Run both arms. Returns per-arm embed lists + a common provenance block."""
    built = build_inputs(model, conversation, chunk_size=chunk_size)
    out: dict = {
        "model_id": MODEL_ID,
        "chunk_size": chunk_size,
        "session_date": SESSION_DATE,
        "chunks": built["chunks"],
        "errors": list(built["errors"]),
        # S1 produces the NARRATIVE arm's input (`raw` never consumes it) — kept in
        # the receipt so an S1 chunk raise or a length-truncated summary is
        # auditable, and consumed by the scorer for that arm only.
        "s1_stats": list(built["s1_stats"]),
        "stories": built["stories"],
        "story_sha": {k: _sha(v) for k, v in built["stories"].items()},
        "story_chars": {k: len(v) for k, v in built["stories"].items()},
        "embed_list": {},
        "s2_stats": {},
        "s2_errors": {},
    }
    for arm in ARMS:
        story = built["stories"].get(arm) or ""
        if not story:
            out["embed_list"][arm] = {}
            out["s2_errors"][arm] = "empty story — the arm produced no input"
            continue
        stats: dict = {}
        t0 = time.time()
        try:
            # Byte-identical call for both arms: only ``story`` differs.
            out["embed_list"][arm] = ev2.run_s2(
                model, story, None,
                session_date=SESSION_DATE,
                edus=built["edus"],
                stats=stats,
                core_only=False,
            )
        except Exception as e:
            out["embed_list"][arm] = {}
            out["s2_errors"][arm] = f"{type(e).__name__}: {e}"
        out["s2_stats"][arm] = stats
        out["s2_seconds"] = out.get("s2_seconds", {})
        out["s2_seconds"][arm] = round(time.time() - t0, 1)
    return out


def _load(session_id: str) -> list[dict]:
    p = FIXTURES_DIR / f"{session_id}.json"
    return json.loads(p.read_text())["conversation"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="raw-vs-narrative arms (#7947)")
    ap.add_argument("--sessions", nargs="+", required=True,
                    help="fixture session ids, e.g. wp01_quarry_debug")
    ap.add_argument("--out", default=str(Path.home() / ".cache" / "pi-7947" / "arms"))
    ap.add_argument("--chunk-size", type=int, default=CHUNK_SIZE)
    args = ap.parse_args(argv)

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    from tortoise.sdk import _model_adapter

    # PINNED: the same adapter construction the production extractor uses.
    model = _model_adapter(MODEL_ID, max_tokens=None)
    # ⚠️ The receipt pins the model, chunking and date, but the S1/S2 SCAFFOLDING
    # also reads the environment: TORTOISE_EXTRACTOR_PROMPT overrides the prompt,
    # TORTOISE_LABEL_ORDER / TORTOISE_LABEL_ORDER_SEED change the label ordering
    # (and so the cache key + what the model sees first), and under `compact` mode
    # `story` additionally drives the pack-kind injection — which would make the
    # arms differ in MORE than the one value the measurement rests on. Print them
    # so a receipt is self-describing; a non-default value must be recorded as a
    # deviation next to the result, not silently inherited.
    # NOTE: `TORTOISE_EXTRACTOR_COMPACT` is NOT read by the extractor — it was
    # listed here for two runs, which made a receipt look like it had pinned a
    # variable that does not exist. The list below is the one the S1/S2 code
    # actually reads (`extractor_v2._stage_cap` / `_extractor_escalation_tokens` for the
    # two caps, `extractor_v2` for the rest). Extending it means extending this tuple, not editing a print.
    env_pins = {k: os.environ.get(k) for k in (
        "TORTOISE_EXTRACTOR_PROMPT", "TORTOISE_LABEL_ORDER",
        "TORTOISE_LABEL_ORDER_SEED", "TORTOISE_EXTRACTOR_MAX_TOKENS",
        "TORTOISE_EXTRACTOR_ESCALATION_TOKENS")}
    print(f"[arms] env: {env_pins}")
    for k, v in env_pins.items():
        if v:
            print(f"[arms] !! {k} is SET — the arms are no longer 'one value differs'; "
                  "record this as a deviation in the write-up")
    print(f"[arms] model={MODEL_ID} chunk_size={args.chunk_size} date={SESSION_DATE}")

    for session_id in args.sessions:
        conversation = _load(session_id)
        t0 = time.time()
        res = run_arms(model, conversation, chunk_size=args.chunk_size)
        res["session_id"] = session_id
        res["turns"] = len(conversation)
        # The receipt must be self-describing OFFLINE: the write-up asserts which
        # environment produced these numbers, and a print to the console is not
        # an artifact. Recorded per session so a single receipt is auditable.
        res["env_pins"] = env_pins
        res["model_id"] = MODEL_ID
        res["chunk_size"] = args.chunk_size
        res["session_date"] = SESSION_DATE
        res["seconds"] = round(time.time() - t0, 1)
        dest = outdir / f"{session_id}.arms.json"
        dest.write_text(json.dumps(res, indent=2, default=str))
        print(f"[arms] {session_id}: turns={res['turns']} chunks={res['chunks']} "
              f"raw_chars={res['story_chars'].get('raw')} "
              f"narr_chars={res['story_chars'].get('narrative')} "
              f"in {res['seconds']}s -> {dest}")
        for arm in ARMS:
            err = res["s2_errors"].get(arm)
            if err:
                print(f"        !! {arm}: {err}")
            # ⛔ Production treats a rung-4 partial accept as a FAILURE of the arm
            # ("S2 output partial — truncated tail dropped (embed list
            # incomplete)"). This harness recorded it only as `s2_stats.partial` and
            # printed nothing, so run 2 carried a partial arm that nobody saw.
            # Surfaced here as an error on stderr so a human sees it, and left in
            # `s2_stats` so the scorer (and any audit) still reads the fact.
            elif (res["s2_stats"].get(arm) or {}).get("partial"):
                print(f"        !! {arm}: PARTIAL — the extractor dropped the tail of "
                      "the list (rung-4 partial accept); this arm's output is incomplete",
                      file=sys.stderr)
        # ⛔ S1 failures used to be written into the receipt and PRINTED NOWHERE —
        # the same "in the receipt, unread" defect as the S2 partial above, one
        # stage earlier. A dropped S1 chunk shortens the NARRATIVE story only.
        for err in res["errors"]:
            print(f"        !! S1: {err} — the narrative story is missing a chunk",
                  file=sys.stderr)
        truncated_chunks = [s for s in res["s1_stats"]
                            if s.get("finish_reason") == "length"]
        if truncated_chunks:
            print("        !! S1: "
                  f"{len(truncated_chunks)} chunk summary(ies) hit the length cap — "
                  "the chunk tail may be missing from the narrative story",
                  file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
