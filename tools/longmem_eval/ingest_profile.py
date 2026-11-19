#!/usr/bin/env python3
"""Where the deterministic ingest's wall clock goes — the #8486 finding-3
diagnosis, as a re-runnable measurement instead of a paragraph.

The 10-question smoke measured **ingest: mean 870 s per question** (p50 757 s,
p95 1,365 s) against **retrieval: 2.0 s mean**. Ingest dominates by ~400×, so
it is the term that decides whether the escalation ladder (10 → 50 → 500) is
viable at all. This module answers the two questions that decide what to do
about it:

1. **Per-question work, or a fixed setup cost paid once?** — measured by
   ingesting synthetic haystacks of increasing size into a FRESH graph each
   and reading the marginal cost per point: a fixed cost shows as a constant
   intercept, per-question work as a constant slope.
2. **Which term dominates that per-question work?** — measured by a
   ``cProfile`` attribution over the real ``ingest_haystack``, so the answer
   is a profile rather than a guess.

⛔ It does NOT download the dataset (a box that cannot reach HuggingFace still
needs to be able to ask this question): the haystack is SYNTHETIC. Its size is
calibrated to the real split by the one number the repo has measured —
``haystack_overlap``'s census that the 500-Q S split emits **371,095 encode
units** (turn leg 246,750 + chunk leg 124,345), i.e. **742 points per
question** — 371,095 / 500 = 742.19. The synthetic shape below is built to
that TARGET; the headline ladder's last rung (500 turns + 250 chunks) builds
**750** points, 1.1 % over it, and the module reports the shape it actually
built so the two are never conflated.

Usage::

    uv run python -m tools.longmem_eval.ingest_profile            # embedded tempdir (or $TORTOISE_DB_URI, as run.py does)
    uv run python -m tools.longmem_eval.ingest_profile --json
    uv run python -m tools.longmem_eval.ingest_profile --db docker://:falkordb@localhost:6379/tortoise

The embed term needs the pinned embedder (``uv sync --extra embeddings`` and
the cached bge-small weights). Without it the profile still runs and says so:
the write/query term is measured, the embed term is reported as UNMEASURED —
never as zero.

⛔ This is a DIAGNOSTIC. It writes throwaway graphs and never a report; it is
not part of a run's fingerprint and no published number depends on it.
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/longmem_eval/ingest_profile.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]})"
    )

import argparse
import cProfile
import io
import json
import os
import pstats
import tempfile
import time
from pathlib import Path
from typing import Any

from tortoise.sdk import TortoiseSDK

from .ingest import DEFAULT_CHUNK_TURNS, ingest_haystack
from .stall_guard import (
    DEFAULT_EVAL_SOCKET_CONNECT_TIMEOUT_S,
    DEFAULT_EVAL_SOCKET_TIMEOUT_S,
    ENV_SOCKET_CONNECT_TIMEOUT,
    ENV_SOCKET_TIMEOUT,
)

#: Points per question on the real 500-Q S split (``haystack_overlap``'s
#: census: 371,095 encode units / 500 questions) — the calibration target the
#: synthetic shape below is built to hit. 371,095 encode units / 500 questions
#: = 742.19; the headline rung builds 750 (500 turns + 250 chunks), which is
#: the shape actually profiled — the module reports it rather than asserting
#: the target.
DATASET_POINTS_PER_QUESTION = 742

#: Turn length (chars) for the synthetic haystack. LongMemEval-S is ~115k
#: tokens over ~500 turns, i.e. ~230 tokens/turn ≈ 900 chars — the value the
#: profile uses as its headline shape. A shorter 200-char shape is also
#: measured so the embed term's LENGTH sensitivity is visible rather than
#: assumed.
HEADLINE_TURN_CHARS = 900
SHORT_TURN_CHARS = 200

#: The scaling ladder: (sessions, turns-per-session). The last is the real
#: per-question shape (25 × 20 = 500 turns → 750 points at chunk_turns=2,
#: against the 742 the dataset averages — see DATASET_POINTS_PER_QUESTION).
SCALING_SHAPES = ((1, 4), (5, 20), (10, 20), (25, 20))


def synthetic_question(qid: str, sessions: int, turns: int,
                       turn_chars: int) -> dict:
    """A LongMemEval-SHAPED question (session/turn/date/evidence fields only —
    no dataset bytes). Content is prose, not padding: a real embedder's cost
    is dominated by token count, and a repeated single word would tokenise
    differently from conversation text."""
    body = (
        "I went to the pharmacy on Tuesday and picked up the prescription, "
        "then drove to the hardware store for paint and primer because the "
        "kitchen ceiling had water damage after the storm last weekend. "
    ) * 16
    return {
        "question_id": qid,
        "question_type": "single-session-user",
        "haystack_session_ids": [f"{qid}-s{i}" for i in range(sessions)],
        "haystack_dates": [f"2023/05/{i + 1:02d} (Mon) 10:00"
                           for i in range(sessions)],
        "haystack_sessions": [
            [{"role": "user" if t % 2 == 0 else "assistant",
              "content": body[:turn_chars],
              "has_answer": (s == 0 and t == 0)}
             for t in range(turns)]
            for s in range(sessions)
        ],
        "question": "What did I pick up?",
        "answer": "a prescription",
    }


def _sdk(*, db_uri: str | None, namespace: str) -> tuple[TortoiseSDK, Any]:
    """Per-question SDK — the SAME construction ``run.py``'s
    ``_make_question_sdk`` performs, so the measurement describes the run's
    path (a server → one graph per question on it; else a fresh embedded
    tempdir).

    ⛔ The ARM is selected the way ``run_main`` selects it, not by ``--db``
    alone: ``run_main`` promotes an ambient ``TORTOISE_DB_URI`` into the server
    path when no ``--db`` was given, so a lane that exports that variable (the
    likeliest state — ``tools/test_lane.py`` exports exactly it) runs on the
    shared FalkorDB server. Selecting the embedded arm there would measure a
    different ENGINE, not just a different timeout, which is the one thing this
    module's parity claim cannot survive.
    """
    if db_uri is None:
        db_uri = os.environ.get("TORTOISE_DB_URI")
    # Validate what was promoted, not just what was passed: ``run_main`` runs
    # this check on the PROMOTED value, so an ambient ``TORTOISE_DB_URI=notauri``
    # is a usage error there and would otherwise surface as a ``TortoiseSDK``
    # failure here — naming the SDK, not the mistake.
    if db_uri is not None and "://" not in db_uri:
        raise ValueError(
            f"--db (or TORTOISE_DB_URI) must be a FalkorDB URI "
            f"(docker://|redis://|rediss://|bolt://), got {db_uri!r} — the "
            f"per-question isolated graphs derive from the URI's server")
    if db_uri:
        prev = os.environ.get("TORTOISE_DB_URI")
        # ASSIGN, not setdefault: an already-exported TORTOISE_DB_URI would
        # otherwise win over the --db the operator named, so the measurement
        # (and the five throwaway graphs it leaves) would land on a DIFFERENT
        # server than the one asked for while report["db"] still said True.
        # That is the likeliest state on a smoke lane — tools/test_lane.py
        # exports exactly this variable.
        os.environ["TORTOISE_DB_URI"] = db_uri
        # Socket-bound parity (review round 6). ``run_main`` wraps EVERY
        # ``--db`` run in ``_temporary_env_var(ENV_SOCKET_TIMEOUT, ... or
        # DEFAULT_EVAL_SOCKET_TIMEOUT_S)`` with the connect timeout beside it,
        # and the multi-tenant read bound is otherwise the product default.
        # On a smoke lane that exports only ``TORTOISE_DB_URI``
        # (``tools/test_lane.py`` exports exactly that), omitting these leaves
        # the profiler on a 12x tighter read bound than the run it claims to
        # describe: one query past it raises ``redis TimeoutError`` and aborts
        # a rung the real run tolerates — on precisely the contended host this
        # module exists to measure. Same rule as the URI above: the lane's
        # value wins if it set one, else the run's own default.
        borrowed = {
            "TORTOISE_DB_URI": prev,
            ENV_SOCKET_TIMEOUT: os.environ.get(ENV_SOCKET_TIMEOUT),
            ENV_SOCKET_CONNECT_TIMEOUT: os.environ.get(ENV_SOCKET_CONNECT_TIMEOUT),
        }
        os.environ[ENV_SOCKET_TIMEOUT] = (
            borrowed[ENV_SOCKET_TIMEOUT] or str(DEFAULT_EVAL_SOCKET_TIMEOUT_S))
        os.environ[ENV_SOCKET_CONNECT_TIMEOUT] = (
            borrowed[ENV_SOCKET_CONNECT_TIMEOUT]
            or str(DEFAULT_EVAL_SOCKET_CONNECT_TIMEOUT_S))

        def _cleanup():
            for name, val in borrowed.items():
                if val is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = val

        return TortoiseSDK(namespace=namespace), _cleanup
    td = tempfile.TemporaryDirectory(prefix="lme-profile-")
    # #1944 parity, not decoration. ``run.py``'s ``_make_question_sdk`` raises
    # the vendored redislite start budget to 60 s because the 10 s default
    # RACES on a contended box (the falkordb.so module load + socket bind can
    # exceed it), and ``embedded_lifecycle`` reads that budget from the same
    # class attribute. This profiler exists to be run on exactly that box, so
    # without this it can die on the first rung with a
    # ``RedisLiteServerStartError`` that says nothing about ingest cost.
    import redislite.client as _rc
    if _rc.Redis.start_timeout < 60:
        _rc.Redis.start_timeout = 60
    return TortoiseSDK(os.path.join(td.name, "lme.db")), td.cleanup


def measure_shape(*, db_uri: str | None, tag: str, sessions: int, turns: int,
                  turn_chars: int) -> dict:
    """Time one ingest on a FRESH graph, splitting the fixed setup (SDK +
    graph + first touch) from the ingest itself.

    ``tag`` MUST be unique per invocation (the caller derives it from the
    pid): the measured ingest is idempotent by design, so a namespace reused
    from an earlier invocation reports ``chunks: 0`` and a near-zero ingest
    time — a re-ingest of an already-populated graph, not a fresh write.
    """
    t0 = time.monotonic()
    sdk, cleanup = _sdk(db_uri=db_uri, namespace=f"ingestprof_{tag}")
    try:
        sdk._get_proj().g.query("RETURN 1")  # fixed setup: connect + graph
        t_touch = time.monotonic()
        stats = ingest_haystack(
            sdk, synthetic_question(tag, sessions, turns, turn_chars),
            chunk_turns=DEFAULT_CHUNK_TURNS)
        t_ingest = time.monotonic()
        n_points = sdk._get_proj().g.query(
            "MATCH (p:Point) RETURN count(p)").result_set[0][0]
    finally:
        sdk.close()
        cleanup()
    ingest_s = t_ingest - t_touch
    return {
        "tag": tag,
        "sessions": sessions,
        "turns": stats["turns"],
        "chunks": stats["chunks"],
        "points": n_points,
        "turn_chars": turn_chars,
        "fixed_setup_s": round(t_touch - t0, 3),
        "ingest_s": round(ingest_s, 3),
        "s_per_point": round(ingest_s / max(n_points, 1), 4),
    }


def profile_attribution(*, db_uri: str | None, sessions: int, turns: int,
                        turn_chars: int, tag: str, top: int = 25) -> dict:
    """cProfile the same ingest and return the attribution that NAMES the
    dominant term (plus the Cypher query count — the N+1 shape)."""
    sdk, cleanup = _sdk(db_uri=db_uri, namespace=f"ingestprof_{tag}")
    try:
        sdk._get_proj().g.query("RETURN 1")
        q = synthetic_question(tag, sessions, turns, turn_chars)
        prof = cProfile.Profile()
        prof.enable()
        ingest_haystack(sdk, q, chunk_turns=DEFAULT_CHUNK_TURNS)
        prof.disable()
    finally:
        sdk.close()
        cleanup()
    buf = io.StringIO()
    stats = pstats.Stats(prof, stream=buf).sort_stats("cumulative")
    stats.print_stats(top)
    # pstats' own per-function table (``func_profiles`` maps the function NAME
    # to a ``FunctionProfile`` carrying ``file_name``/``line_number``); the raw
    # ``stats.stats`` value shape is not stable across Python versions, and
    # this module must not crash on the one call it exists to make.
    rows = [
        {"ncalls": entry.ncalls, "cumtime": round(entry.cumtime, 3),
         "tottime": round(entry.tottime, 3),
         "name": f"{Path(entry.file_name).name}:{entry.line_number}"
                 f"({func})"}
        for func, entry in stats.get_stats_profile().func_profiles.items()
    ]
    rows.sort(key=lambda r: r["cumtime"], reverse=True)
    return {
        "cprofile_total_s": round(stats.total_tt, 3),
        "top": rows[:top],
        "text": buf.getvalue(),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="#8486 finding 3: profile the deterministic ingest path")
    p.add_argument("--db", default=None,
                   help="FalkorDB URI (docker://|redis://|rediss://|bolt://). "
                        "Default: an embedded tempdir graph — UNLESS "
                        "TORTOISE_DB_URI is exported, which run.py promotes "
                        "into the server path and this module mirrors, so "
                        "the arm matches the run's")
    p.add_argument("--json", action="store_true",
                   help="emit one JSON object instead of the human render")
    p.add_argument("--no-profile", action="store_true",
                   help="times only; skip the cProfile attribution (faster)")
    args = p.parse_args(argv)
    # ``--db ""`` must not silently fall back to the embedded arm: it is the
    # ordinary shape of an unset-variable mistake (``--db "$TORTOISE_DB_URI"``),
    # and ``run_main`` refuses it rather than measuring something else. Same
    # check, same message.
    if args.db is not None and "://" not in args.db:
        p.error(
            f"--db must be a FalkorDB URI (docker://|redis://|rediss://|"
            f"bolt://), got {args.db!r} — the per-question isolated graphs "
            f"derive from the URI's server")

    # The arm actually SELECTED, not merely the flag that was passed: with an
    # ambient TORTOISE_DB_URI and no --db the profiler takes the server arm
    # (``_sdk`` promotes it, as ``run_main`` does), so reporting
    # ``bool(args.db)`` would print ``db: false`` about a server run — the
    # record disagreeing with the run, which is the defect class #8486 is about.
    selected_db = args.db if args.db is not None else os.environ.get(
        "TORTOISE_DB_URI")
    report: dict[str, Any] = {"db": bool(selected_db), "shapes": [],
                              "embedder": None, "encode": {}}
    # A PID-scoped tag: every measurement below writes a FRESH graph, and the
    # ingest is idempotent — a reused namespace measures a no-op re-ingest.
    run_tag = f"{os.getpid()}"
    from tortoise.embeddings import EmbeddingModel

    t0 = time.monotonic()
    model = EmbeddingModel.get(load_timeout=600)
    report["embedder_load_s"] = round(time.monotonic() - t0, 2)
    report["embedder"] = type(model).__name__ if model is not None else None
    if model is not None:
        for chars in (SHORT_TURN_CHARS, HEADLINE_TURN_CHARS):
            t = time.monotonic()
            model.encode(["x" * chars] * 16)
            report["encode"][str(chars)] = {
                "texts": 16,
                "s": round(time.monotonic() - t, 3),
                "s_per_text": round((time.monotonic() - t) / 16, 4),
            }

    for i, (sessions, turns) in enumerate(SCALING_SHAPES):
        shape = measure_shape(db_uri=args.db, tag=f"{run_tag}_sc{i}",
                              sessions=sessions, turns=turns,
                              turn_chars=HEADLINE_TURN_CHARS)
        report["shapes"].append(shape)
        print(json.dumps(shape), file=sys.stderr, flush=True)

    if not args.no_profile:
        sessions, turns = SCALING_SHAPES[-1]
        report["attribution"] = profile_attribution(
            db_uri=args.db, sessions=sessions, turns=turns,
            turn_chars=HEADLINE_TURN_CHARS, tag=f"{run_tag}_profile")

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    print("\n# Ingest profile (#8486 finding 3)\n")
    print(f"embedder: {report['embedder']} "
          f"(load {report['embedder_load_s']} s)")
    if report["embedder"] is None:
        print("⛔ NO EMBEDDER — the write-time embed term is UNMEASURED here "
              "(provision `--extra embeddings` + the cached weights); the "
              "query/write term below is still measured.")
    print("\n| shape | turns | chunks | points | fixed setup | ingest | "
          "s/point |\n|---|---|---|---|---|---|---|")
    for s in report["shapes"]:
        print(f"| {s['sessions']}x{s['turns']} | {s['turns']} | "
              f"{s['chunks']} | {s['points']} | {s['fixed_setup_s']} s | "
              f"{s['ingest_s']} s | {s['s_per_point']} |")
    last = report["shapes"][-1]
    print(f"\nPer-question projection: {last['points']} points × "
          f"{last['s_per_point']} s = "
          f"{round(last['points'] * last['s_per_point'], 1)} s/question "
          f"→ 500 questions = "
          f"{round(last['points'] * last['s_per_point'] * 500 / 3600, 1)} h")
    if "attribution" in report:
        print("\n## cProfile attribution (top terms)\n")
        print("| cumtime s | ncalls | term |\n|---|---|---|")
        for row in report["attribution"]["top"][:12]:
            print(f"| {row['cumtime']} | {row['ncalls']} | {row['name']} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
