#!/usr/bin/env python3
"""Reap EMPTY TEST-ORG graphs — the cleaner #13205's leak needed (DRY-RUN by default).

WHY THIS EXISTS
---------------
A tenant graph is not free. Measured on ``instance-yxwgk64it`` (2026-11-16):
deleting 148 graphs that held 0.0 MB of data dropped ``used_memory`` from 841.4 MB
to 625.0 MB — **216.4 MB freed ≈ 1.46 MB per graph**, i.e. 26 % of a 1 GB
instance, spent on graphs that never held a single node.

⛔ **MEASURE PER-GRAPH COST BY DELETING, NOT BY CREATING.** A probe that *creates*
a graph and reads its footprint shows ~346 KiB, because the allocation lands in
pages the allocator already holds. The full resident cost only appears when the
graph is deleted. The 346 KiB reading is a FALSE NEGATIVE and must never be used
as a gate (`--self-check` prints both figures for exactly this reason).

Where they came from: test/agent signups provisioned a REAL graph on the
production instance (every ``agent-*`` / ``e2e-*`` org got one), and the tests'
teardown deleted the control-plane row without deprovisioning the data plane —
``public.graphs`` cascades, a FalkorDB graph does not. Deleting the row is not
deleting the graph; that is the leak.

WHAT THIS TOOL IS ALLOWED TO TOUCH  (safe by construction, four independent gates)
---------------------------------------------------------------------------------
1. **DRY-RUN unless ``--execute``.** Nothing is deleted without the flag, and the
   flag changes ONLY the last step: the classification below is identical in both
   modes, so a dry run is a real rehearsal and not a different code path.
2. **The owning org must RESOLVE.** A graph whose name maps to no
   ``organizations`` row is **REFUSED, never reaped** (bucket
   ``refused-unresolved``). An orphan is a question for a human — ``x``,
   ``tortoise``, ``registry_control_plane``, ``g_0bcdb2…`` and the ``team_*``
   names with no org row all land here. A cleaner that treats "nobody owns it" as
   "mine to delete" is how a cleaner becomes an outage.
3. **The owner must be TEST/AGENT.** Default markers are narrow and name/email
   based (``agent-*``, ``agent_*``, ``e2e*``, ``vgate-*``, ``e2e-live-*@…``).
   Anything else is ``kept-not-test-org`` — **there is deliberately NO flag that
   widens this to "any org"**, because that flag would be the whole safety
   property traded for convenience. An org whose name carries no marker can still
   be reaped by naming it EXPLICITLY with ``--test-org <org_id>`` (repeatable;
   never a wildcard).
4. **The graph must be EMPTY.** Emptiness is 0 NODES (and therefore 0 edges),
   read per graph. A graph that holds anything at all — one Point, one ``Meta``
   row — is ``kept-not-empty`` and is a data question, not a cleaning one. If the
   emptiness read FAILS the graph is ``refused-unreadable``, never assumed empty.

A reserved-name deny list (``registry_*``, ``tortoise``) is a belt-and-braces
extra gate on top of (2): control-plane state needs adjudication by whoever owns
it, not by a cleaner (the issue says so explicitly).

USAGE
-----
    uv run python tools/reap_empty_test_graphs.py                  # DRY RUN (report)
    uv run python tools/reap_empty_test_graphs.py --json           # the same, machine-readable
    uv run python tools/reap_empty_test_graphs.py --execute        # GRAPH.DELETE
    uv run python tools/reap_empty_test_graphs.py --execute \\
        --test-org 01J8ZZK4M7HQ3X9Z8R2K6VWPB                       # one extra explicit org

Exit codes: 0 = complete report (dry run or executed, nothing failed);
1 = at least one delete failed; 2 = the report could not be produced (control
plane unreadable, graph list unreadable, or no control plane configured) — a
partial run is never reported as clean.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

# #5128: refuse a <3.12 interpreter before the imports below (see the sibling
# graph-scripts for the same guard).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/reap_empty_test_graphs.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/reap_empty_test_graphs.py`"
    )

from tortoise.sdk import canonical_org_graph_name

#: Org NAMES that mark a test/agent signup. Anchored on a `-`/`_` SEPARATOR, so
#: only machine-shaped names match: ``agent-<id>``, ``e2e-live-<hex>``,
#: ``vgate-<x>``. A customer called ``E2E Logistics Ltd`` or ``Agent Test`` is
#: NOT matched (both have a space where the separator is), and neither is
#: ``agentic systems``.
#:
#: ⛔ UNDER-INCLUSIVE ON PURPOSE, and that is the fail-closed direction. A name
#: marker that over-includes deletes a customer's graph; one that under-includes
#: lists the org under ``kept-not-test-org`` and the operator adds it with
#: ``--test-org <id>`` after READING THE DRY RUN. `Agent Test` — one of the orgs
#: the issue counted — lands in exactly that bucket: it is a test org by intent
#: and not by shape, so it is named explicitly rather than matched by a regex
#: that would also match a customer. Widening the marker is therefore
#: impossible by accident and always a reviewed edit.
TEST_ORG_NAME_RE = re.compile(r"^(?:agent|e2e|vgate)[-_]", re.IGNORECASE)

#: Test-org EMAIL shapes. ``e2e-live-<hex>@premise-labs.dev`` is the welcome-e2e
#: monitor's own signup (graph-scripts/2146_e2e_live_orphan_cleanup.py documents
#: the red window that minted 222 of them). Anchored to the whole local part so
#: a real person at that domain is not swept.
TEST_ORG_EMAIL_RE = re.compile(
    r"^e2e-live-[0-9a-f]+@premise-labs\.dev$", re.IGNORECASE)

#: Names that are NEVER reapable however empty they look, even if some row
#: happens to resolve them. The control plane's own state graphs need
#: adjudication by their owner (issue #13205 says exactly this about
#: ``registry_control_plane``/``registry_tortoise``), and a cleaner is not an
#: adjudicator.
RESERVED_GRAPH_NAMES = frozenset({
    "registry_control_plane",
    "registry_tortoise",
    "tortoise",
})

#: The bucket names. Exported so the tests assert on the VOCABULARY rather than
#: on substrings, and so a new bucket has to be added in one place.
BUCKET_REAPABLE = "reapable"
BUCKET_KEPT_NOT_TEST_ORG = "kept-not-test-org"
BUCKET_KEPT_NOT_EMPTY = "kept-not-empty"
BUCKET_REFUSED_UNRESOLVED = "refused-unresolved"
BUCKET_REFUSED_UNREADABLE = "refused-unreadable"


class Refusal(Exception):
    """The report cannot be produced honestly — exit 2, never a clean report."""


def _owns_namespace(org_id: str, namespace: str) -> bool:
    """Does ``namespace`` PROVABLY belong to ``org_id``?

    The same clause `hosted_api._org_graph_names` uses, and it is the whole safety
    property of the index below: the control plane supplies CANDIDATES, ownership
    decides. Without it a malformed (or hostile) ``public.graphs`` row
    ``{org_id: <a test org>, namespace: "org_<a customer>_g_x"}`` would attribute
    a customer's graph to a test org, and an EMPTY customer graph would land in
    ``reapable`` and be deleted — exactly what this tool promises never to do.
    """
    if not org_id or not namespace:
        return False
    own = (canonical_org_graph_name(org_id), f"team_{org_id}", f"org_{org_id}",
           f"team_{org_id}")
    if namespace in own:
        return True
    return namespace.startswith((f"org_{org_id}_", f"team_{org_id}_"))


def _namespace_index(cp) -> dict[str, dict]:
    """Every accepted graph-name spelling → ``{"org": row, "gid": str | None}``.

    The spellings a tenant graph is stored under, all of which must resolve here
    or the resolver would refuse graphs the product itself considers owned:

    * ``organizations.graph_name`` **verbatim** — the canonical pointer, and the
      ONLY spelling that is authoritative for an SDK-lane org (``org_{name}``,
      #2023) or a legacy org (``team_{id}``). Its type is not checked against the
      org id: ``graph_name`` IS the pointer, by definition.
    * ``canonical_org_graph_name(org_id)`` — what every resolver derives from a
      bare id;
    * ``team_{org_id}`` — the pre-#3543 physical spelling, still live on disk;
    * every ``public.graphs.namespace`` row — one per CUSTOM graph
      (``org_{org_id}_{gid}``), carrying its ``id`` so the reaper can also drop
      that graph's per-graph ACL user (:func:`reap`).

    ⛔ THREE refusals, all fail-closed:

    1. **An unreadable ``graphs`` read is a REFUSAL, not a degradation.** That
       read is the ONLY source of ``gid``s, and a gid is what makes a custom
       graph's ACL user droppable — so degrading to "default graphs only" would
       let a PROMOTED org's default (whose ``graph_name`` IS a
       ``org_{id}_{gid}`` namespace) resolve from the organizations row with
       ``gid: None``, classify as reapable, and be deleted with its global
       ``tenant_<gid>`` credential left live (review round 4). The same applies
       to a read truncated at the deployment's ``max_rows``: `query` never
       reports truncation, so an incomplete read cannot be told from a complete
       one — and a missing gid must never authorise a deletion. `classify_graph`
       adds an independent gate for the same shape (see its docstring).
    2. a ``graphs`` row whose namespace is not PROVABLY the row org's
       (:func:`_owns_namespace`) is DISCARDED — never indexed, so the graph it
       names reads as unresolved and is refused; and
    3. a name claimed by more than one org is dropped from the index entirely
       and reported as ambiguous. The old `setdefault` over an unordered query
       made the keeper depend on PostgREST's row order, i.e. the keep/reap
       decision was nondeterministic on an estate with registry-era rows.

    Raises :class:`Refusal` on any ``organizations`` read failure: a resolver that
    degrades to "unknown" is indistinguishable from "no owner", and this tool
    REFUSES on no owner — so a silent degradation would read as a clean run.
    """
    try:
        orgs = cp.query("organizations", select=["id", "name", "email", "graph_name"])
    except Exception as e:
        raise Refusal(f"organizations read failed: {e}") from e
    try:
        # ⛔ `query_all`, NOT `query` (review round 5). A single-page read is
        # silently capped at the deployment's `max_rows` (1000) and `query`
        # never reports it — so a promoted org's `graphs` row could be missing
        # from the page while its `organizations.graph_name` still names that
        # custom namespace, and `classify_graph` would lose the gid.
        # `query_all` returns `(rows, complete)`; an uncertified walk is a
        # REFUSAL, never a shorter index.
        graph_rows, complete = cp.query_all(
            "graphs", select=["id", "org_id", "namespace"], cursor="id")
        if not complete:
            raise Refusal(
                "the graphs walk could not be certified complete — an "
                "incomplete read can lose the gid of a CUSTOM graph, and a "
                "custom graph without its gid cannot have its ACL user "
                "dropped; refusing rather than reaping on a partial view")
    except Refusal:
        raise
    except Exception as e:
        raise Refusal(
            f"graphs read failed ({e}) — the gid of every CUSTOM graph comes "
            f"from this table, and a custom graph without its gid cannot have "
            f"its ACL user dropped; refusing rather than reaping on a partial "
            f"view") from e
    claims: dict[str, dict] = {}
    ambiguous: set[str] = set()

    def _claim(namespace: object, org: dict, gid: str | None) -> None:
        if not isinstance(namespace, str) or not namespace:
            return
        held = claims.get(namespace)
        if held is not None:
            if held["org"].get("id") != org.get("id"):
                ambiguous.add(namespace)
                return
            # SAME org, claimed twice: the org row's `graph_name` claims first
            # with no gid, so a later `graphs` row for that SAME namespace must
            # still contribute its gid — otherwise a PROMOTED default (whose
            # `graph_name` IS a custom `org_{id}_{gid}` namespace) would reap the
            # graph while `reap()` skipped its ACL drop for want of a gid, leaving
            # the global `tenant_<gid>` credential live forever (round-3 review).
            if gid and not held.get("gid"):
                held["gid"] = gid
            return
        claims[namespace] = {"org": org, "gid": gid}

    by_id: dict[str, dict] = {}
    for row in orgs:
        org_id = row.get("id")
        if not org_id:
            continue
        by_id[str(org_id)] = row
        for name in (row.get("graph_name"),
                     canonical_org_graph_name(str(org_id)),
                     f"team_{org_id}"):
            _claim(name, row, None)
    for row in graph_rows:
        org = by_id.get(str(row.get("org_id")))
        namespace = row.get("namespace")
        if org and _owns_namespace(str(row.get("org_id")), str(namespace or "")):
            _claim(namespace, org, row.get("id"))
    for name in ambiguous:
        claims.pop(name, None)
        print(f"[warn] {name!r} is claimed by more than one organization — "
              f"REFUSED (ambiguity is never resolved by row order)",
              file=sys.stderr)
    return claims


def is_test_org(org: dict, explicitly_named: frozenset[str] = frozenset()) -> bool:
    """Is this org a test/agent signup — i.e. may its empty graphs be reaped?

    Positive markers ONLY (see the module docstring). ``explicitly_named`` is the
    ``--test-org`` override: an exact org id an operator named, never a pattern.
    """
    org_id = str(org.get("id") or "")
    if org_id and org_id in explicitly_named:
        return True
    name = str(org.get("name") or "")
    email = str(org.get("email") or "")
    return bool(TEST_ORG_NAME_RE.match(name) or TEST_ORG_EMAIL_RE.match(email))


def _looks_custom(graph_name: str, org: dict) -> bool:
    """Is this a PER-GRAPH namespace (``org_{id}_{gid}``) rather than a default?

    A custom graph is minted with a global ``tenant_<gid>`` ACL user, so it can
    only be reaped safely when its gid is known.

    ⛔ The exemption list is the BARE default spellings ONLY — never the org's
    stored ``graph_name`` (review round 5). A promoted/repaired org's
    ``graph_name`` IS a custom ``org_{id}_{gid}`` namespace, so exempting it made
    the gate unreachable for exactly the graph it was added to protect: with that
    row missing from the ``graphs`` read, the stored pointer claimed the
    namespace with ``gid: None``, `_looks_custom` said "default", and the graph
    was deleted with its live ``tenant_<gid>`` credential behind it. A bare
    default is ``org_{id}``/``team_{id}``, and those stay exempt.
    """
    org_id = str(org.get("id") or "")
    if not org_id:
        return False
    if graph_name in (canonical_org_graph_name(org_id), f"team_{org_id}",
                      f"org_{org_id}"):
        return False
    return graph_name.startswith((f"org_{org_id}_", f"team_{org_id}_"))


def classify_graph(graph_name: str, *, org_index: dict[str, dict],
                   node_count: int | None,
                   explicitly_named: frozenset[str] = frozenset()
                   ) -> tuple[str, str]:
    """``(bucket, reason)`` for ONE graph — the whole decision, pure and testable.

    ``node_count`` is ``None`` when the emptiness read failed; that is a REFUSAL,
    never an assumption of emptiness.

    The gates are applied in the order that fails CLOSED first: reserved name →
    ownership → test marker → emptiness. Ownership comes before the marker so an
    unresolvable graph is reported as unresolved rather than being mis-filed as
    "some org's, not test" (which would read like a decision about an owner).
    """
    if graph_name in RESERVED_GRAPH_NAMES or graph_name.startswith("registry_"):
        return (BUCKET_REFUSED_UNRESOLVED,
                "reserved control-plane graph — needs adjudication, not a cleaner")
    entry = org_index.get(graph_name)
    if entry is None:
        return (BUCKET_REFUSED_UNRESOLVED,
                "no organizations row owns this graph name — an orphan needs "
                "adjudication, never a cleaner")
    org = entry["org"]
    if not is_test_org(org, explicitly_named):
        return (BUCKET_KEPT_NOT_TEST_ORG,
                f"owner {org.get('id')!r} ({org.get('name')!r}) is not marked "
                f"test/agent")
    if node_count is None:
        return (BUCKET_REFUSED_UNREADABLE,
                "node-count read failed — emptiness is never assumed")
    if entry.get("gid") is None and _looks_custom(graph_name, org):
        # ⛔ The independent gate for the round-4 finding: a CUSTOM namespace
        # (`org_{id}_{gid}`/`team_{id}_{gid}`) reached the index with no gid, so
        # its per-graph ACL user is unnameable — deleting it would orphan a live
        # credential. That can only happen on an incomplete/truncated `graphs`
        # read, which `_namespace_index` now refuses outright; this refuses the
        # single graph anyway, so the safety does not depend on that read's
        # completeness.
        return (BUCKET_REFUSED_UNRESOLVED,
                "custom-shaped graph with no gid — its per-graph ACL user "
                "cannot be dropped, so deleting it would orphan a live "
                "credential")
    if node_count != 0:
        return (BUCKET_KEPT_NOT_EMPTY, f"holds {node_count} node(s)")
    return (BUCKET_REAPABLE, "empty graph owned by a test/agent org")


def _node_count(proj, graph_name: str) -> int | None:
    """Node count for one graph, or ``None`` when it cannot be read."""
    try:
        rows = proj.db.select_graph(graph_name).query(
            "MATCH (n) RETURN count(n)").result_set
    except Exception:
        return None
    try:
        return int(rows[0][0])
    except Exception:
        return None


def _read_handle():
    """THE ONE store handle: a projection that CANNOT materialise a graph.

    Used for BOTH phases (classify and delete) on purpose. Two handles with two
    path policies would be a fail-OPEN: the tool would read emptiness from one
    database and issue ``GRAPH.DELETE`` against another, so a graph could be
    deleted on the strength of a DIFFERENT store's emptiness. `resolve_db_path`
    (the CLI/dev default) and `hosted_api._resolve_embedded_db_path` (the server
    process's policy) genuinely disagree — measured ``/home/cmux/.tortoise/
    tortoise.db`` vs ``/tmp/tortoise.db`` — so the embedded branch addresses the
    store THE HOSTED APP serves from, not the CLI's.

    ⛔ `_make_sdk(graph_name="tortoise")._get_proj()` is NOT read-only: the
    open-time health probe is a `GRAPH.QUERY`, and on a graph the store does not
    hold the FIRST ONE CREATES IT (measured #11113). A "dry run" that mints the
    `tortoise` graph would spend exactly the resource this tool exists to reclaim
    — and `tortoise` is in `RESERVED_GRAPH_NAMES`, so the tool could never
    reclaim what it just created. Write-freedom is the PAIR
    (`skip_health_check=True, ensure_indexes=False`); `ensure_indexes=False`
    ALONE still creates a missing graph through the health probe
    (`test_write_freedom_needs_the_flag_PAIR_not_ensure_indexes_alone`).
    """
    import os

    from tortoise.projection import FalkorProjection

    db_uri = os.environ.get("TORTOISE_DB_URI")
    if db_uri:
        return FalkorProjection.from_uri(
            db_uri, graph_name="tortoise",
            skip_health_check=True, ensure_indexes=False)
    from tortoise.hosted_api import _resolve_embedded_db_path

    return FalkorProjection(
        path=_resolve_embedded_db_path(), graph_name="tortoise",
        skip_health_check=True, ensure_indexes=False)


def build_report(*, explicitly_named: frozenset[str] = frozenset(),
                 proj=None, cp=None) -> dict:
    """The full verdict for every graph on the instance. NEVER deletes anything."""
    from tortoise.supabase_control import get_control_plane, is_supabase_enabled

    if cp is None:
        if not is_supabase_enabled():
            raise Refusal(
                "no control plane configured (is_supabase_enabled() is False) — "
                "ownership cannot be resolved, so NOTHING can be reaped; a "
                "self-host registry lane has no cross-tenant leak to clean")
        cp = get_control_plane()
    org_index = _namespace_index(cp)
    if proj is None:
        proj = _read_handle()
    try:
        names = sorted(proj.db.list_graphs() or [])
    except Exception as e:
        raise Refusal(f"graph listing failed: {e}") from e
    rows = []
    for name in names:
        # `node_count` is only read for names that could possibly be reaped —
        # a read per graph is cheap, but a read per CUSTOMER graph is a
        # needless scan of live tenants' data.
        entry = org_index.get(name)
        org = (entry or {}).get("org")
        needs_count = (name not in RESERVED_GRAPH_NAMES
                       and not name.startswith("registry_")
                       and org is not None
                       and is_test_org(org, explicitly_named))
        count = _node_count(proj, name) if needs_count else None
        bucket, reason = classify_graph(
            name, org_index=org_index, node_count=count,
            explicitly_named=explicitly_named)
        rows.append({
            "graph": name,
            "bucket": bucket,
            "reason": reason,
            "org_id": (org or {}).get("id"),
            "org_name": (org or {}).get("name"),
            "gid": (entry or {}).get("gid"),
            "node_count": count,
        })
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["bucket"]] = counts.get(row["bucket"], 0) + 1
    return {"graphs": rows, "counts": counts, "total": len(rows)}


def reap(report: dict, *, proj) -> list[dict]:
    """Delete the ``reapable`` graphs. The ONLY function that mutates anything.

    Re-derives nothing: it acts on the report it was handed, so the dry run the
    operator read IS the plan. Absent-graph raises are success (the end state is
    the graph being gone), matching ``hosted_api._drop_org_graph_impl``.

    ⛔ Each CUSTOM graph's per-graph ACL user is dropped FIRST, from the ``gid``
    the report carries (#13205 review). Custom graphs are minted with a
    GLOBAL ``tenant_<gid>`` FalkorDB user; deleting the graph alone leaves that
    credential live forever — the exact reason the org purge drops ACL users
    before the graphs (#2113 C4). The reaper keeps the org row, so nothing else
    will ever revisit it.

    ⛔ AND THE DROP IS VERIFIED, not trusted. Both `drop_acl_user` and
    `hosted_api._acl_user_drop_hook` are BEST-EFFORT by design (they log and
    swallow), so a refused `ACL DELUSER` looks exactly like a success from the
    caller's side — measured: with the real hook and a raising `drop_acl_user`
    the graph was deleted and the outcome read clean. `acl_user_exists` is the
    check that can fail: the deletion is SKIPPED when the user is still there
    (a live credential must never outlive the graph that explains it), and
    `acl_user_exists` answers False when the ACL layer is absent, which is the
    correct semantics — a layer that is declared off has no user to orphan.
    """
    from tortoise.acl_graph_users import acl_user_exists, drop_acl_user
    from tortoise.projection import is_missing_graph_error

    outcome = []
    for row in report["graphs"]:
        if row["bucket"] != BUCKET_REAPABLE:
            continue
        entry = {"graph": row["graph"], "org_id": row.get("org_id"),
                 "deleted": False, "error": None}
        if row.get("gid"):
            try:
                drop_acl_user(str(row["gid"]))
                still_live = acl_user_exists(str(row["gid"]))
            except Exception as e:
                entry["error"] = f"acl-user drop failed: {type(e).__name__}: {e}"
                outcome.append(entry)
                continue
            if still_live:
                entry["error"] = (
                    f"acl user for {row['gid']!r} survived its drop — refusing "
                    f"to orphan a live credential behind a deleted graph")
                outcome.append(entry)
                continue
        try:
            proj.db.select_graph(row["graph"]).delete()
            entry["deleted"] = True
        except Exception as e:
            if is_missing_graph_error(e):
                entry["deleted"] = True
                entry["error"] = "already absent"
            else:
                entry["error"] = f"{type(e).__name__}: {e}"
        outcome.append(entry)
    return outcome


def _print_report(report: dict, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    print(f"graphs enumerated: {report['total']}")
    for bucket in sorted(report["counts"]):
        print(f"  {bucket:<24} {report['counts'][bucket]}")
    print()
    for row in report["graphs"]:
        if row["bucket"] == BUCKET_REAPABLE:
            print(f"  [{BUCKET_REAPABLE}] {row['graph']}  "
                  f"(org {row['org_id']} {row['org_name']!r})")
    for row in report["graphs"]:
        if row["bucket"].startswith("refused"):
            print(f"  [{row['bucket']}] {row['graph']} — {row['reason']}")


def _note(msg: str, *, as_json: bool) -> None:
    """A human line. Under ``--json`` it goes to STDERR so stdout stays one
    parseable document — the old code appended it to the JSON, so any caller
    piping to ``jq`` got a parse error (PR #13218 review)."""
    print(msg, file=sys.stderr if as_json else sys.stdout)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Reap EMPTY graphs owned by test/agent orgs (dry-run default).")
    ap.add_argument("--execute", action="store_true",
                    help="actually GRAPH.DELETE the reapable set (default: dry run)")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--test-org", action="append", default=[], metavar="ORG_ID",
                    help="treat this exact org id as test/agent (repeatable; "
                         "never a wildcard)")
    args = ap.parse_args(argv)
    try:
        report = build_report(explicitly_named=frozenset(args.test_org))
    except Refusal as e:
        print(f"REFUSED — {e}", file=sys.stderr)
        return 2
    if not args.json:
        _print_report(report, as_json=False)
    elif not args.execute:
        _print_report(report, as_json=True)
    if not args.execute:
        _note("\nDRY RUN — nothing deleted. Re-run with --execute to delete the "
              f"{report['counts'].get(BUCKET_REAPABLE, 0)} reapable graph(s).",
              as_json=args.json)
        return 0
    # The SAME write-free handle the report was built on — never `_make_sdk`,
    # which would MINT the reserved `tortoise` graph on the deletion path (the
    # very write `_read_handle` exists to avoid).
    outcome = reap(report, proj=_read_handle())
    failed = [o for o in outcome
              if not o["deleted"] or o["error"] not in (None, "already absent")]
    if args.json:
        print(json.dumps({"report": report, "outcome": outcome,
                          "failed": len(failed)}, indent=2, sort_keys=True))
        for o in outcome:
            print(f"  {'deleted' if o['deleted'] else 'FAILED '} {o['graph']}"
                  + (f" — {o['error']}" if o["error"] else ""), file=sys.stderr)
        print(f"\n{len(outcome) - len(failed)} deleted, {len(failed)} failed",
              file=sys.stderr)
        return 1 if failed else 0
    for o in outcome:
        print(f"  {'deleted' if o['deleted'] else 'FAILED '} {o['graph']}"
              + (f" — {o['error']}" if o["error"] else ""))
    print(f"\n{len(outcome) - len(failed)} deleted, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
