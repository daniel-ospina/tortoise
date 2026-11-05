#!/usr/bin/env python3
"""#10000 — what ONE captured session costs the GRAPH, with and without the change.

The acceptance bullet this satisfies (§"What done means", bullet 4 — *"a cost
measurement taken after the change shows the drop: bytes per captured session in
the graph, before vs after, on the same corpus"*) had no artifact in the tree.
This is it, and it is the instrument that produced `README.md`'s numbers.

WHAT IT MEASURES, and why in this shape.

  * The corpus is SYNTHETIC and FIXED: ``--sessions`` sessions of ``--turns``
    turns of ``--turn-chars`` characters, the same ids and the same text in every
    arm, with a deterministic 384-dim vector per turn (the production embedding
    width). Nothing is read from a live tenant, so the figure is reproducible on
    any machine and cannot be a snapshot of production data.
  * The arms are the SAME CORPUS through the SAME public-ish capture chokepoint
    (``sdk._write_session_and_turns``), differing only in the one thing #10000
    changed — whether the record can hold the turns:

      before        the parent commit of the squash that landed the change
                    (``6b48f7cef^``), record unarmed — the pre-#10000 writer
      after-unarmed ``origin/main``, every turn still goes to the graph (the
                    documented capability fallback: no record DSN ⇒ unchanged)
      after-armed   ``origin/main`` with ``TORTOISE_RECORD_DSN`` set to a
                    Postgres carrying the record migrations — the arms of the
                    change itself

  * Bytes are the SERVER's own resident accounting (``INFO memory`` →
    ``used_memory``), taken before and after the corpus and divided by the
    session count. That instrument is used instead of ``GRAPH.MEMORY USAGE``
    because: it reports BYTES (``GRAPH.MEMORY USAGE`` reports whole MB, which is
    0 for a corpus this size), and it does not share #5331's blindness — the
    HNSW vector index lives in the separately loaded ``vectorset`` module, which
    ``GRAPH.MEMORY USAGE`` does not count at all while per-graph allocation is
    tracked here. ``GRAPH.MEMORY USAGE`` is reported beside it as the second
    witness §12.1b quotes, and it is expected to read ~0 at this scale.
    ⚠️ ``used_memory`` includes the graph's block/index/attribute matrices, so a
    SMALL corpus over-states the marginal per-turn cost (FalkorDB allocates in
    blocks). That is why the corpus size is a flag and is printed with the
    figure: the number is a per-session marginal at the stated corpus, never a
    per-node constant.

  * The CENSUS (nodes by label, edges by type, turn ids, and a graph-wide scan
    for each turn's own text) is the other half of the claim: bytes say "less",
    the census says "none", and the record row count says the transcript arrived
    somewhere.

RUN IT (a fresh, private FalkorDB — never the shared one). Two steps on purpose:
``test_lane.py``'s own USAGE forbids the one-line ``eval "$(…)"`` form, because a FAILED
run prints nothing on stdout, ``eval ""`` returns 0, and a PREVIOUSLY exported
``TORTOISE_DB_URI`` (commonly the SHARED lane) is then silently measured while the
command looks like it succeeded:

    uri="$(uv run python tools/test_lane.py uri)" || exit 1   # exports on stdout, status on ||
    eval "$uri"
    # armed arm only: a Postgres carrying the record migrations
    export TEST_RECORD_DB_URI=postgresql://record:record@127.0.0.1:5432/record
    uv run --extra postgres python \\
        docs/evidence/10000-turns-bytes/probe_turn_bytes.py --arm after-armed

The ``before`` arm must be run from a worktree at ``6b48f7cef^`` (or any
pre-#10000 commit) — this script is committed on the branch that adds it, so the
parent commit does not carry it: **COPY it into that worktree** and run it there.
⛔ Not ``PYTHONPATH``. The script puts the tree that CONTAINS IT first on
``sys.path`` (``repo = Path(__file__).resolve().parents[3]``, then
``sys.path.insert(0, str(repo))``), so a ``PYTHONPATH`` aimed at this worktree
LOSES to the worktree the copy sits in — the run would import the AFTER writer
while being labelled ``before``, silently, on the one arm the whole delta is
measured against. Every JSON therefore records ``git_head`` AND the resolved
``tortoise_module`` path, so an arm label and the code that actually ran cannot
disagree unnoticed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

#: The embedding width every capture lane stores (the dense leg's dimension).
DIM = 384
#: A corpus-wide marker token in every turn's text, so the graph-wide scan can
#: look for "this capture's own words" rather than for a shape.
MARKER = "r10000turns"

ARM_BEFORE = "before"
ARM_AFTER_UNARMED = "after-unarmed"
ARM_AFTER_ARMED = "after-armed"
ARMS = (ARM_BEFORE, ARM_AFTER_UNARMED, ARM_AFTER_ARMED)


def _trend_text(session_index: int, turn_index: int, chars: int) -> str:
    """The same text in every arm, keyed so the scan can attribute a hit."""
    head = f"{MARKER} s{session_index} t{turn_index} "
    return head + ("the quick brown fox jumps over the lazy dog. "
                   * ((chars // 44) + 1))[: max(chars - len(head), 0)]


def _vector(turn_index: int) -> list[float]:
    """A deterministic 384-dim unit-ish vector (no embedder, no drift)."""
    return [round(((i * 7 + turn_index * 13) % 1000) / 1000.0, 6) for i in range(DIM)]


def _used_memory(g) -> int:
    info = g.execute_command("INFO", "memory")
    if isinstance(info, (bytes, str)):
        text = info.decode() if isinstance(info, bytes) else info
        for line in text.splitlines():
            if line.startswith("used_memory:"):
                return int(line.split(":", 1)[1])
    if isinstance(info, dict):
        return int(info["used_memory"])
    raise RuntimeError(f"unreadable INFO memory: {type(info)!r}")


def _graph_memory_usage(g, graph_name: str) -> dict:
    raw = g.execute_command("GRAPH.MEMORY USAGE", graph_name)
    if not isinstance(raw, (list, tuple)):
        return {"raw": raw}
    return dict(zip(raw[::2], raw[1::2], strict=False))


def _census(g) -> dict:
    by_label = {str(row[0]): int(row[1]) for row in g.query(
        "MATCH (n) RETURN labels(n)[0], count(n) ORDER BY labels(n)[0]").result_set}
    by_edge = {str(row[0]): int(row[1]) for row in g.query(
        "MATCH ()-[r]->() RETURN type(r), count(r) ORDER BY type(r)").result_set}
    return {"nodes_by_label": by_label, "edges_by_type": by_edge}


def _text_hits(g, needles: list[str]) -> int:
    """How many graph nodes carry any of this corpus's own turn texts.

    The PROPERTY form of the acceptance bullet: a node is counted if it carries
    the text, whatever label, edge or `pointKind` a writer gave it — so a second
    write path that invents its own shape is caught here, which an id-space
    query (`sdk._capture_turn_ids`) would not see.
    """
    total = 0
    for needle in needles:
        rows = g.query(
            "MATCH (n) WHERE n.c CONTAINS $needle OR n.content CONTAINS $needle "
            "OR n.text CONTAINS $needle RETURN count(n)",
            params={"needle": needle}).result_set
        total += int(rows[0][0]) if rows else 0
    return total


def _git_head(repo: Path) -> str:
    """The commit the measured code came from — the artifact's provenance."""
    import subprocess

    try:
        return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True,
                              check=True).stdout.strip()
    except Exception:
        return "unknown"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=ARMS)
    ap.add_argument("--sessions", type=int, default=25)
    ap.add_argument("--turns", type=int, default=40)
    ap.add_argument("--turn-chars", type=int, default=1500)
    ap.add_argument("--namespace", default="test_r10000_bytes")
    args = ap.parse_args()

    os.environ["TORTOISE_SESSION_LLM_MOCK"] = "1"

    repo = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(repo))

    import tortoise.sdk as sdk_mod
    from tortoise.sdk import TortoiseSDK

    if args.arm == ARM_AFTER_ARMED:
        # Reuse the record leg's OWN provisioning helpers (#7970/#9078 own
        # "a Postgres carrying these migrations") rather than declaring a
        # second way to build the record.
        from tests.test_9078_record_written_by_production_path import (
            _app_role_dsn,
            _ensure_record_schema,
            _seed_tenant,
        )
        from tortoise import record_writer

        dsn = os.environ.get("TEST_RECORD_DB_URI")
        if not dsn:
            print("TEST_RECORD_DB_URI is not set — the armed arm needs the "
                  "record's Postgres", file=sys.stderr)
            return 2
        import psycopg2

        # One tenant per namespace, so repeated runs into one database do not
        # accumulate each other's rows (the `point_rows` figure is per run).
        tenant = (f"org_10000_bytes_{args.namespace}".replace("-", "_"),
                  f"g_10000_bytes_{args.namespace}".replace("-", "_"))
        admin = psycopg2.connect(dsn)
        admin.autocommit = True
        _ensure_record_schema(admin)
        _seed_tenant(admin, tenant)
        os.environ[record_writer.RECORD_DSN_ENV] = _app_role_dsn(dsn)
    else:
        os.environ.pop("TORTOISE_RECORD_DSN", None)

    # The journal is one half of the capability gate this measurement is ABOUT
    # (`record_writer.record_holds_turns` needs a configured journal AND a record
    # DSN, and it is the predicate `sdk._write_capture_turns` reads), so it
    # is provisioned in every arm — the arms differ only in the DSN.
    import tempfile

    journal_dir = tempfile.mkdtemp(prefix="r10000-bytes-")
    sdk = TortoiseSDK(namespace=args.namespace,
                      event_log_path=str(Path(journal_dir) / "journal.jsonl"))
    try:
        proj = sdk._get_proj()
        g = proj.g
        # A fresh graph per arm: the corpus delta must not include a previous
        # arm's rows (and a re-run must be repeatable).
        g.query("MATCH (n) DETACH DELETE n")

        if args.arm == ARM_AFTER_ARMED:
            sdk._bind_record_tenant(*tenant)
            from tortoise.record_writer import use_record_tenant

        public_report = None
        if args.arm == ARM_AFTER_ARMED:
            # ── the PUBLIC capture lane, once (#10000 bullet 2's census) ──────
            # The turn-store chokepoint below is where #10000 changes behaviour,
            # but the acceptance list also says what the graph must still HOLD:
            # the session's `:Source` anchor with NO prose payload, the extracted
            # claims, the Session/Event identity. One public capture (mock
            # extractor, so it is hermetic on every box) is the census for that
            # half. It runs BEFORE the byte baseline, so its rows are not part
            # of the per-session figure.
            public_turns = [{"role": "user" if i % 2 == 0 else "assistant",
                             "content": _trend_text(999, i, args.turn_chars)}
                            for i in range(min(args.turns, 6))]
            # ⛔ #4282 (owner ruling 2026-10-27) deleted the public capture lane
            # this census drives: ``TortoiseSDK.capture_session`` was the SDK's
            # only in-process caller of ``_extract_session_v2``. The census is
            # ONE block of this probe — the turn-store byte measurement below is
            # the probe's subject and does not depend on it — so the block is
            # reported as UNAVAILABLE rather than aborting the run, and no row
            # pretends a capture happened.
            if hasattr(sdk, "capture_session"):
                with use_record_tenant(*tenant):
                    receipt = sdk.capture_session(public_turns,
                                                  session_id="10000-bytes-public")
                public_report = {
                    "receipt_ok": bool(receipt.get("ok")),
                    "node_counts": _census(g)["nodes_by_label"],
                    "source_content_bytes": int(g.query(
                        "MATCH (s:Source) RETURN coalesce(sum(size(coalesce(s.content,''))), -1)"
                    ).result_set[0][0]),
                    "turn_text_hits": _text_hits(g, [f"{MARKER} s999 "]),
                }
            else:
                public_report = {
                    "unavailable": "TortoiseSDK.capture_session was deleted by "
                                   "#4282, so the public capture lane has no "
                                   "in-process door",
                }

        before = _used_memory(g)
        # One needle per session — every turn of that session shares the
        # prefix, so each session's text is scanned for in one query.
        needles = [f"{MARKER} s{s} " for s in range(args.sessions)]
        turn_ids: list[str] = []
        for s in range(args.sessions):
            sid = f"10000-bytes-s{s}"
            turns = [{"role": "user" if i % 2 == 0 else "assistant",
                      "content": _trend_text(s, i, args.turn_chars)}
                     for i in range(args.turns)]
            embs = [_vector(i) for i in range(args.turns)]
            ctx = use_record_tenant(*tenant) if args.arm == ARM_AFTER_ARMED else None
            if ctx is None:
                sdk_mod._write_session_and_turns(
                    proj, sdk, sid, turns, now="2026-10-26T00:00:00+00:00",
                    turn_embs=embs, session_existed=False)
            else:
                with ctx:
                    sdk_mod._write_session_and_turns(
                        proj, sdk, sid, turns, now="2026-10-26T00:00:00+00:00",
                        turn_embs=embs, session_existed=False)
            # `out["turn_ids"]` is ARITHMETIC (`_write_session_and_turns`
            # derives the id list it wrote); the question is what the GRAPH
            # holds, so the ids are read back through the graph-side reader.
            turn_ids += sorted(sdk_mod._capture_turn_ids(proj, sid))
        after = _used_memory(g)

        census = _census(g)
        report = {
            "arm": args.arm,
            "tree": {"tortoise_module": sdk_mod.__file__,
                     "git_head": _git_head(repo)},
            "corpus": {"sessions": args.sessions, "turns_per_session": args.turns,
                       "turn_chars": args.turn_chars, "dim": DIM},
            "graph": {"namespace": args.namespace, "graph_name": proj.graph_name,
                      "node_counts": census["nodes_by_label"],
                      "edge_counts": census["edges_by_type"]},
            "used_memory_bytes": {"before": before, "after": after,
                                  "delta": after - before,
                                  "delta_per_session": round(
                                      (after - before) / args.sessions, 1)},
            "graph_memory_usage_mb": _graph_memory_usage(g, proj.graph_name),
            "turn_nodes_in_graph": {
                "turn_ids_read_back_from_the_graph": len(turn_ids),
                "graphwide_text_hits_over_sessions": _text_hits(g, needles),
            },
        }
        if public_report is not None:
            report["public_capture_census"] = public_report
        if args.arm == ARM_AFTER_ARMED:
            with admin.cursor() as cur:
                cur.execute(
                    "SELECT count(*), coalesce(sum(octet_length(coalesce(content,''))), 0) "
                    "FROM public.point WHERE org_id=%s AND graph_id=%s",
                    tenant)
                rows, page_bytes = cur.fetchone()
            report["record_rows"] = {
                "point_rows": int(rows),
                "content_bytes": int(page_bytes),
                "expected_rows": args.sessions * args.turns,
            }
        print(json.dumps(report, indent=2, default=str))
    finally:
        sdk.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
