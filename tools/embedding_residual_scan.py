#!/usr/bin/env python3
"""`python tools/embedding_residual_scan.py` — attest an erasure across BOTH stores.

#11642's acceptance asks for erasure that *"spans both stores and is attested by
a residual scan"*. After `#4051` V1 moved the vector index to Postgres, every
vector exists TWICE — the record's `embedding`/`embedding_vec` column and the
graph node's `embedding` property, because writes stay in the graph — so
"erased" is a claim about both, and a delete's return code attests neither.

The scan itself lives in `tortoise/embedding_residual_scan.py`; this is the
operator verb around it. Exit codes are the verdict:

    0  CLEAN        both stores READ, in scope, neither holds a vector
    3  RESIDUE      a store answered and still holds one — a FINDING
    2  UNAVAILABLE  NO VERDICT (a store could not be read, or no graph handle)

⛔ `2` is deliberately not `0`: a scan that could not read a store must never be
reported as "no residue found".

Usage:

    # the whole scope, both stores
    uv run python tools/embedding_residual_scan.py \
        --org o_123 --graph-id g_abc --graph-name org_acme \
        --graph-uri "$TORTOISE_DB_URI"

    # machine-readable
    uv run python tools/embedding_residual_scan.py --org ... --graph-id ... \
        --graph-name ... --json

⚠️ The GRAPH half needs a live FalkorDB. When `--graph-uri` is absent the graph
half is REFUSED (exit 2) rather than silently reported clean — "I did not look"
is the one answer this tool must never dress up as "nothing there".
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.embedding_residual_scan import (
    EXIT_CLEAN,
    EXIT_RESIDUE,
    EXIT_UNAVAILABLE,
    scan,
)


def _graph_handle(graph_uri: str | None, graph_name: str):
    """A read handle on the named graph, or ``None`` when it cannot be built.

    ⛔ Returning ``None`` (not raising) is deliberate: the caller turns it into
    the graph half's REFUSAL, so a missing URI is an explicit "not measured"
    rather than an exception an operator might read as a tool bug.

    ⛔ AND THE NAME IS VERIFIED AGAINST THE ENGINE'S OWN LISTING BEFORE ANY
    CYPHER RUNS (#11660 review round 6, P1-4). `select_graph` does not raise for
    a name the engine does not hold — and an aggregate over a graph that does
    not exist answers `[[0]]`, indistinguishable from a genuinely empty tenant —
    so `--graph-name org_typo` used to read nothing, record no refusal, and print
    **CLEAN**: a false erasure attestation from the one instrument whose contract
    says that is impossible, reachable by one typo in a flag the round-3 split
    introduced. Two further reasons the check is HERE rather than in
    `_count_graph`:

    * a Cypher read on a MISSING graph CREATES an empty one (verified on
      FalkorDB 4.20.4 — see `tortoise/hosted_backup.py::_await_restore_copy_settled`,
      which never queries a name `GRAPH.LIST` does not hold for exactly this
      reason), so a scan must not touch an unverified name at all;
    * "I read a graph with no nodes" and "I read nothing" are the two states
      this tool exists to keep apart, and the second one is not a CLEAN.

    A listing that cannot be read is treated the same way as an absent name —
    refused, never assumed present. Fail-closed is the only safe direction for an
    attestation.

    ⛔ THE URI GOES THROUGH THE REPO'S ONE URI → ENDPOINT DERIVATION, NEVER A
    RE-PARSE (#2974; #11660 review round 7, P1-1). This handle used to be
    ``FalkorDB.from_url(graph_uri)``, which is a DIFFERENT derivation from the
    product's ``tortoise.projection.resolve_db_endpoint`` on three axes at once,
    and one of them fails OPEN — the one outcome this module says is impossible:

    * ``docker://`` — the scheme in ``.env.example`` and the one
      ``tools/test_lane.py`` prints for a private lane — is not understood by the
      client at all (``FalkorDB.from_url`` rewrites only ``falkor(s)://`` and
      then defers to ``redis.from_url``, which accepts ``redis``/``rediss``/
      ``unix``). So the documented command could not attest in the documented
      deployment: the graph half was refused and the verdict was ALWAYS
      ``UNAVAILABLE`` on this repo's own default local URI.
    * ``falkor(s)://`` — a scheme the product REFUSES
      (``tests/test_engine_probe.py`` pins that refusal) — was ACCEPTED here.
    * a URI stating **no port** meant ``6379`` (redis-py's default) here and
      **``16379``** in the product. Dial a different FalkorDB that happens to
      hold a graph of the same name and the count is ``0`` for every label —
      ``MATCH … RETURN count(n)`` always answers one row, so the ``if not rows``
      refusal below does not fire — while the record half (another server
      entirely) also answers in scope: **CLEAN, exit 0, about an instance nobody
      asked about.** The ``GRAPH.LIST`` check does not cover it, because the
      wrong instance really does hold that name.

    So the endpoint is resolved, a ``ValueError`` from the resolver is a REFUSAL
    that carries the resolver's own message (which names the expected schemes
    and never echoes the URI — it carries a credential), and the client is built
    from the resolved fields with the product's bounded socket policy (#8118:
    ``falkordb.FalkorDB`` defaults ``socket_timeout=None``, so an unbounded
    client parks this scan in ``recv()`` forever against a stalled engine —
    #2969's defrag loop, #2838's MERGE lock — alive, 0% CPU, no error). The
    name-verification and the ``--graph-id``/``--graph-name`` split are
    untouched: they are about WHICH graph, this is about WHICH SERVER.
    """
    if not graph_uri:
        return None
    from tortoise.projection import (
        _bounded_server_socket_kwargs,
        resolve_db_endpoint,
    )

    try:
        endpoint = resolve_db_endpoint(graph_uri)
    except ValueError as exc:
        print(f"Warning: the graph URI was REFUSED ({exc}). The graph half of "
              "the scan is NOT measured; the verdict cannot be CLEAN.",
              file=sys.stderr)
        return None
    try:
        from falkordb import FalkorDB

        db = FalkorDB(
            host=endpoint.host,
            port=endpoint.port,
            username=endpoint.username,
            password=endpoint.password,
            ssl=endpoint.ssl,
            **_bounded_server_socket_kwargs(),
        )
        names = {str(n) for n in (db.list_graphs() or [])}
    except Exception as e:  # noqa: BLE001, RUF100
        print(f"Warning: no graph handle ({type(e).__name__}: {e}). The graph "
              "half of the scan is NOT measured; the verdict cannot be CLEAN.",
              file=sys.stderr)
        return None
    if graph_name not in names:
        print(
            f"Warning: the engine holds NO graph named {graph_name!r} (its "
            f"listing has {len(names)} graph(s)). The graph half of the scan is "
            "NOT measured — an aggregate over a name that does not exist "
            "answers 0, which is why this is a REFUSAL and not a clean count. "
            "A Cypher read on an unknown name would also CREATE an empty graph, "
            "so nothing is queried.",
            file=sys.stderr,
        )
        return None
    try:
        return db.select_graph(graph_name)
    except Exception as e:  # noqa: BLE001, RUF100
        print(f"Warning: no graph handle ({type(e).__name__}: {e}). The graph "
              "half of the scan is NOT measured; the verdict cannot be CLEAN.",
              file=sys.stderr)
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Residual-scan BOTH vector stores for one scope after an "
                    "erasure (#11642). Exit 0 CLEAN / 3 RESIDUE / 2 no verdict.")
    ap.add_argument("--org", required=True, help="Record org_id (the scope)")
    # ⛔ TWO DIFFERENT IDENTIFIERS, NEVER ONE (#11660 review round 3, P1).
    # The record's `graph_id` is a `public.graphs.id` (`g_<hex>`, FK-enforced);
    # the FalkorDB graph NAME is that row's `namespace` (`org_<ns>` /
    # `org_<tid>_<gid>`). An earlier draft took ONE value for both, so whichever
    # was supplied, one store was read under a wrong scope — and worse, a graph
    # name the engine does not hold answers `MATCH … RETURN count(n)` with **0**
    # rather than raising, so `_count_graph` recorded NO refusal and the tool
    # printed CLEAN. That is a false erasure attestation from the one instrument
    # whose contract says a false CLEAN is impossible.
    ap.add_argument("--graph-id", required=True,
                    help="The RECORD scope's graph_id (public.graphs.id, "
                         "`g_<hex>`) — never the FalkorDB graph name")
    ap.add_argument("--graph-name", required=True,
                    help="The FalkorDB graph NAME to scan (public.graphs."
                         "namespace, `org_<ns>` / `org_<tid>_<gid>`) — never "
                         "the record's graph_id")
    ap.add_argument("--record-dsn", default=None,
                    help="Postgres record DSN (default: $TORTOISE_RECORD_DSN)")
    ap.add_argument("--graph-uri", default=None,
                    help="FalkorDB URI for the graph half "
                         "(default: $TORTOISE_DB_URI). Absent → the graph half "
                         "is refused and the verdict is not CLEAN")
    ap.add_argument("--json", action="store_true", help="Machine-readable output")
    args = ap.parse_args(argv)

    graph_uri = args.graph_uri or os.environ.get("TORTOISE_DB_URI")
    graph = _graph_handle(graph_uri, args.graph_name)

    outcome = scan(
        org_id=args.org,
        graph_id=args.graph_id,
        record_dsn=args.record_dsn,
        graph=graph,
    )

    if args.json:
        print(json.dumps(outcome.as_dict(), indent=2, sort_keys=True,
                         default=str))
        return outcome.exit_code

    print(f"scope: org={outcome.org_id} record graph_id={outcome.graph_id} "
          f"falkor graph name={args.graph_name}")
    print(f"  record: {outcome.record_vectors!r}"
          + ("" if outcome.record_vectors is None
             else f"  {outcome.record_by_table}"))
    # ⛔ The split the dense leg cannot serve (#11660 review round 6, P2-6): rows
    # the record HOLDS whose `embedding_vec` is NULL (non-384, an object, `[]`, a
    # `null` element). They are not residue and they are not indexed, and the
    # dense read's `WHERE embedding_vec IS NOT NULL` excludes them silently — so
    # print the number even when it is zero, because "N vectors, M indexed" is
    # the question an operator actually has.
    if outcome.record_unindexed_by_table:
        print(f"  record held but NOT ANN-indexed: "
              f"{sum(outcome.record_unindexed_by_table.values())} "
              f"{outcome.record_unindexed_by_table}")
    print(f"  graph : {outcome.graph_vectors!r}"
          + ("" if outcome.graph_vectors is None
             else f"  {outcome.graph_by_label}"))
    for refusal in outcome.refusals:
        print(f"  REFUSED — {refusal}", file=sys.stderr)

    verdict = outcome.verdict
    print(f"\nverdict: {verdict}")
    if verdict == "CLEAN":
        print("Both stores were read in scope and neither holds a vector.")
        return EXIT_CLEAN
    if verdict == "RESIDUE":
        print("A store still holds a vector for an erased scope — the erasure "
              "did not reach it. This is a FINDING, not a retry.")
        return EXIT_RESIDUE
    print("NO VERDICT: a store could not be read. This is NOT 'no residue'.",
          file=sys.stderr)
    return EXIT_UNAVAILABLE


if __name__ == "__main__":
    raise SystemExit(main())
