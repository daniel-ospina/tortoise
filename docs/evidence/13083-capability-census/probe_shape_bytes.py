#!/usr/bin/env python3
"""#13083 — what each STORED SHAPE costs the graph, per property family, on a fixed corpus.

This is the measuring half of the capability audit. The census doc
(`docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md`) states which readers touch
each shape; this instrument states what a shape is WORTH in bytes, so a
`record_only` verdict carries a number instead of an adjective.

WHAT IT MEASURES

  * Bytes are the SERVER's own resident accounting (``INFO memory`` →
    ``used_memory``) taken around the corpus, the same instrument
    ``docs/evidence/10000-turns-bytes/probe_turn_bytes.py`` uses and for the same
    reasons (#5331: ``GRAPH.MEMORY USAGE`` is blind to the HNSW ``vectorset``
    module and reports whole MB).
  * Per-property figures are **RENDERED** sizes — ``len(str(value).encode())`` summed
    per label, emitted as ``repr_bytes_by_label`` because that is what it is. ⚠️
    This is NOT ``sum(size(n.<prop>))``: ``size()`` refuses a scalar and returns
    an ELEMENT COUNT for a list, so no single Cypher spelling yields stored bytes
    for every property type. For a STRING the two coincide and the figure is
    exact; for a numeric ARRAY the rendered size over-reports by ~4.7x (a 384-dim
    float vector renders as ~7,187 characters where its stored ``float32`` payload
    is 384 x 4 = 1,536 bytes), so an array's stored footprint must be DERIVED FROM
    THE WIDTH, never read off this column.
  * The CORPUS is SYNTHETIC and FIXED: the same ids, the same texts and the same
    deterministic vectors in every arm, so a re-run on any machine reproduces it
    and no figure is a snapshot of tenant data.

⚠️ LIMITS, stated rather than discovered later:

  * ``used_memory`` includes block/attribute/index matrices, so a small corpus
    over-states the marginal per-node cost (FalkorDB allocates in blocks). The
    corpus size is a flag and is printed WITH the figure.
  * The per-property column is RENDERED (``len(str(v).encode())``), not stored:
    exact for a string, an ~4.7x OVER-report for a numeric array (a 384-dim
    float32 vector is 1536 stored B but ~7187 rendered characters). Derive an
    array's footprint from its width; never read it off that column. It also
    excludes the attribute-block entry per property per node, so it is a
    payload figure and never a residency figure.

RUN IT (a PRIVATE lane — never the shared 6379, and never the one-line
``eval "$(…)"`` form: a failed run prints nothing on stdout and ``eval ""``
returns 0, leaving a previously exported URI silently measured):

    uri="$(uv run python tools/test_lane.py uri)" || exit 1
    eval "$uri"
    # ⛔ BOTH arms carry `--legacy-prose`, and the corpus is the one the
    # committed JSONs used (6 x 10, not the 10 x 20 this block used to show —
    # a different corpus cannot reproduce them).
    uv run python docs/evidence/13083-capability-census/probe_shape_bytes.py \
        --arm before --sessions 6 --turns 10 --legacy-prose > measurement-before.json
    uv run python docs/evidence/13083-capability-census/probe_shape_bytes.py \
        --arm after  --sessions 6 --turns 10 --legacy-prose > measurement-after.json
    # `--arm after` WITHOUT the flag exits 2 by design: it would seed no prose,
    # differing from `before` by the SEEDING rather than the declaration.

    uv run python tools/test_lane.py down
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

MARKER = "SHAPE13083"
DIM = 384
ARMS = ("before", "after")


def _vector(seed: int) -> list[float]:
    """A deterministic 384-dim float32 vector — the production embedding width."""
    import math

    return [math.sin(seed * 0.017 + i * 0.001) for i in range(DIM)]


def _text(seed: int, chars: int) -> str:
    return (f"{MARKER} s{seed} " + "topic prose about storage placement. " * 200)[:chars]


def _used_memory(g) -> int:
    """The server's own resident accounting — the same instrument as §9.1's probe."""
    info = g.execute_command("INFO", "memory")
    if isinstance(info, (bytes, str)):
        text = info.decode() if isinstance(info, bytes) else info
        for line in text.splitlines():
            if line.startswith("used_memory:"):
                return int(line.split(":", 1)[1])
    if isinstance(info, dict):
        return int(info["used_memory"])
    raise RuntimeError(f"unreadable INFO memory: {type(info)!r}")


def _nodes_by_label(g) -> dict:
    return {str(r[0]): int(r[1]) for r in g.query(
        "MATCH (n) RETURN labels(n)[0], count(n) ORDER BY labels(n)[0]").result_set}


def _property_bytes(g) -> dict:
    """per label → {prop: RENDERED payload bytes}, from ``len(str(value).encode())``.

    ⚠️ This is a RENDERED size, NOT the engine's stored size, and ``size()`` cannot
    supply the stored figure (it refuses a scalar and returns an ELEMENT COUNT for a
    list). Exact for a string; an order-of-magnitude OVER-report for a numeric array
    (a 384-dim float vector renders as ~7,187 characters; its stored ``float32``
    payload is 1,536 B) — derive an array's footprint from its width, never from this
    column. The output field is named ``repr_bytes_by_label`` for the same reason.

    The property name is taken from the node's own key set (``keys(n)``) rather
    than from a declaration, because the question is what the WRITER put there —
    a declaration would answer what the ontology wishes were there.
    """
    out: dict[str, dict[str, int]] = {}
    labels = [str(r[0]) for r in g.query(
        "MATCH (n) RETURN DISTINCT labels(n)[0] ORDER BY labels(n)[0]").result_set]
    for label in labels:
        keys = [str(r[0]) for r in g.query(
            f"MATCH (n:{label}) UNWIND keys(n) AS k RETURN DISTINCT k").result_set]
        per: dict[str, int] = {}
        for key in keys:
            # ⚠️ THIS IS A **RENDERED** SIZE, NOT THE ENGINE'S STORED SIZE, and
            # the name below says so (`repr_bytes`) because conflating the two
            # inflates every numeric ARRAY by an order of magnitude: `str()` of a
            # 384-dim float vector is ~7,187 characters of digits, commas and
            # spaces, while the stored `float32` payload is 384 × 4 = 1,536 bytes.
            # A review caught the first cut of this instrument publishing the
            # rendered figure AS bytes. `size()` is not a substitute either: it
            # refuses a scalar and returns an ELEMENT COUNT for a list, so no
            # single Cypher spelling yields stored bytes for every property type.
            # String properties (the ones that dominate prose) are exact; array
            # and numeric properties are OVER-REPORTED here and their stored
            # footprint must be derived from the width, not read off this column.
            rows = g.query(f"MATCH (n:{label}) RETURN n.`{key}`").result_set
            total = 0
            for (value,) in rows:
                if value is None:
                    continue
                total += len(str(value).encode())
            per[key] = total  # rendered bytes — see the note above
        out[label] = dict(sorted(per.items(), key=lambda kv: -kv[1]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=ARMS)
    ap.add_argument("--sessions", type=int, default=10)
    ap.add_argument("--turns", type=int, default=20)
    ap.add_argument("--turn-chars", type=int, default=1500)
    ap.add_argument("--namespace", default="test_r13083_shapes")
    ap.add_argument(
        "--legacy-prose", action="store_true",
        help="REQUIRED for both arms of the prose A/B. Seeds each turn's :Point "
             "with §9.1's narrative in its row props TAIL — the shape a pre-move "
             "record->cache projection produced. `before` POPS the declaration "
             "and keeps the key; `after` SETS it and drops the key. Both arms "
             "must pass this flag: an `after` run without it produces a corpus "
             "with no prose at all, which differs from `before` by the SEEDING "
             "rather than by the declaration, and looks like a valid A/B.")
    args = ap.parse_args()

    os.environ["TORTOISE_SESSION_LLM_MOCK"] = "1"
    repo = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(repo))

    import tortoise.sdk as sdk_mod
    from tortoise.sdk import TortoiseSDK

    # Every arm is graph-only: the record is not armed, so a capture's turns
    # land as NODES. That is deliberate — this instrument measures what the
    # graph holds on the documented capability FALLBACK, which is the
    # population a `record_only` move would remove from.
    # ⛔ The prose A/B's invariant is asserted OUTSIDE the flag: running `after`
    # without `--legacy-prose` is exactly the confounded arm a review caught
    # (the arms would differ by SEEDING, not by the declaration), so it is
    # refused rather than silently produced.
    if args.arm == "after" and not args.legacy_prose:
        print("refusing: the `after` arm is the declaration arm of the prose "
              "A/B and must be run WITH --legacy-prose, or the two arms differ "
              "by whether prose was seeded rather than by the declaration.",
              file=sys.stderr)
        return 2

    os.environ.pop("TORTOISE_RECORD_DSN", None)
    journal_dir = tempfile.mkdtemp(prefix="r13083-")
    sdk = TortoiseSDK(namespace=args.namespace,
                      event_log_path=str(Path(journal_dir) / "journal.jsonl"))
    try:
        proj = sdk._get_proj()
        g = proj.g
        g.query("MATCH (n) DETACH DELETE n")

        before = _used_memory(g)

        # ── the corpus: captures (turns as nodes) ─────────────────────────────
        for s in range(args.sessions):
            sid = f"13083-s{s}"
            turns = [{"role": "user" if i % 2 == 0 else "assistant",
                      "content": _text(s * 100 + i, args.turn_chars)}
                     for i in range(args.turns)]
            sdk_mod._write_session_and_turns(
                proj, sdk, sid, turns, now="2026-11-17T00:00:00+00:00",
                turn_embs=[_vector(i) for i in range(args.turns)],
                session_existed=False)

        # ── the §9.1 prose arm: the ONE shape #13083 moves ───────────────────
        # A legacy turn snapshot carried the narrative in its `props` tail, and
        # `public.point` has no `story_arc` COLUMN, so the projection's tail door
        # was the only thing standing between that prose and a node. `before`
        # seeds the node the OLD projection produced; `after` seeds the node the
        # declaration makes it produce. Nothing else in the corpus differs.
        if args.legacy_prose:
            # ⛔ NOT through `create_point`: that door REFUSES the key (§9.1/D1,
            # `_refuse_unpaired_narrative`), which is the live half of the same
            # rule. The route that was still open is the RECORD -> CACHE one, so
            # the arm goes through the projection's OWN two calls —
            # `node_from_row` then `record_plan` — exactly what
            # `rebuild_from_record` executes. The declaration is the only
            # difference between the arms.
            from tortoise import cache_projection as cp

            # ⛔ THE ARM VARIES THE DECLARATION, and nothing else. `before` is
            # the tree as it stood before #13083's entry existed — removed IN
            # PROCESS here rather than by checking out an older commit, so the
            # two arms run the same code and the same corpus and the declared
            # placement is the single difference between them.
            # ⛔ `after` INJECTS the entry — it does not rely on the shipped
            # tree carrying it. The move is not landed (#10429 owns it), so a
            # probe that read the shipped declaration would produce two arms
            # that differ by whether the prose was SEEDED AT ALL rather than by
            # the declaration — the defect a review caught in the first cut of
            # this instrument. Both arms are therefore never-prose-less: `before`
            # REMOVES the entry, `after` SETS it, and the corpus is identical.
            if args.arm == "before":
                cp.CACHE_FORBIDDEN_COLUMNS.pop("point", None)
            else:
                cp.CACHE_FORBIDDEN_COLUMNS["point"] = frozenset({"story_arc"})
            assert ("point" in cp.CACHE_FORBIDDEN_COLUMNS) == (args.arm == "after"), (
                "the arm did not set up the declaration it names")

            planned = []
            for s_i in range(args.sessions):
                for i in range(args.turns):
                    key = f"13083-s{s_i}_t{i}"
                    row = {
                        "id": key,
                        "node_key": key,
                        "cache_tier": "graph",
                        "is_episodic": True,
                        "content": _text(s_i * 100 + i, args.turn_chars),
                        "point_kind": "event",
                        "props": {"story_arc": _text(5000 + s_i * 100 + i, args.turn_chars)},
                    }
                    planned.append(cp.node_from_row("Point", row))
            for stmt in cp.record_plan(planned).statements:
                g.query(stmt.cypher, params=dict(stmt.params))

        # ── the corpus: ONE public capture (the real capture chokepoint) ──────
        # The census must be taken over what the PRODUCTION writer emits, not
        # only over the turn-store chokepoint above: `_capture_session` is the
        # door the CLI/MCP capture uses, and its node set is a different shape.
        public_turns = [{"role": "user" if i % 2 == 0 else "assistant",
                         "content": _text(900 + i, 800)} for i in range(4)]
        sdk._capture_session(public_turns, session_id="13083-public")

        # ── the corpus: the other five shapes, with their property families ───
        sdk._create_source(
            f"https://example.test/13083/{args.namespace}", "document",
            tier="high", title="Shape census source",
            topics=["storage", "placement"], summary="A summary of the source.",
            domain="platform", documentKind="report", project="kept-13083",
            provenance_spans=[{"start": 0, "end": 120}], about_entities=["13083-object-1"])
        sdk._create_event("13083-event-1", "Deployment",
                         participants=["alice", "bob"],
                         startedAt="2026-11-17T00:00:00+00:00",
                         aboutObject="13083-object-1", note="event note",
                         classification_level="internal",
                         keywords=["deploy", "storage"])
        sdk._create_object("13083-object-1", objectKind="artifact",
                          title="Object title")
        sdk._create_subject("13083-subject-1", subjectKind="person")
        sdk._create_point("statement", "the shape census claim", project="kept-13083")

        after = _used_memory(g)
        census = _nodes_by_label(g)
        report = {
            "arm": args.arm,
            "tree": {"git_head": _git_head(repo), "module": sdk_mod.__file__},
            "corpus": {"sessions": args.sessions, "turns_per_session": args.turns,
                       "turn_chars": args.turn_chars, "dim": DIM},
            "graph": {"graph_name": proj.graph_name, "nodes_by_label": census},
            "used_memory_bytes": {"before": before, "after": after, "delta": after - before},
            "repr_bytes_by_label": _property_bytes(g),
            "legacy_prose_arm": bool(args.legacy_prose),
            "limits": [
                "repr_bytes_by_label is a RENDERED size, not the engine's stored "
                "size: it is exact for a STRING and over-reports a numeric ARRAY by "
                "~4.7x (a 384-dim float vector renders as ~7,187 characters; its "
                "stored float32 payload is 1536 B). Derive an array's footprint from "
                "its width; never read it off this column.",
                "used_memory includes block/index matrices; small corpora over-state "
                "the marginal per-node cost, and its baseline drifts between runs.",
            ],
        }
        print(json.dumps(report, indent=2, default=str))
    finally:
        sdk.close()
    return 0


def _git_head(repo: Path) -> str:
    import subprocess
    try:
        return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return "unknown"


if __name__ == "__main__":
    sys.exit(main())
