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

SCHEDULED USE (#14559)
----------------------
This tool is the mechanism; it is driven unattended by
``.github/workflows/test-graph-reaper.yml`` — every 15 minutes, three passes:
plan (dry run, artifact), execute (``--execute``), verify (dry run again, which
must report 0 ``reapable``). The cadence, the resident band, and the durable log
are stated there, in one place; nothing in this file is schedule-specific.

``--execute`` also reports ``used_memory`` BEFORE and AFTER the reap, in bytes
and in MB freed (``memory`` in the ``--json`` document). Read it the way the
docstring above measures cost — the delta is the only figure that reflects what
the allocator actually released, and it is an INSTANCE-WIDE reading, so it
carries concurrent tenant writes in the window (see :func:`_used_memory`).
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter BEFORE every import — `test_entry_point_python_guard`
# requires the guard to precede the import block, because a module-level 3.11+-only
# import raises ImportError before a later-placed guard could run. Landing this guard
# BELOW `argparse`/`json`/`re` reds that census for every PR (#10754's class).
#
# #4053/#11342: the refusal must not wear a VERDICT's status. This tool's answers are
# 0 (clean / dry run) and 1 (a deletion FAILED, or the store named a failure), so
# `raise SystemExit(<message>)` — which exits 1 — would report a failed reap on a box
# whose interpreter is simply old. 2 is this tool's own REFUSED (it returns it when the
# report cannot be built), which is what an old interpreter IS. `SystemExit(<int>)`
# carries no message, so the refusal is printed explicitly.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    print(
        f"tools/reap_empty_test_graphs.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        "`uv run python tools/reap_empty_test_graphs.py`",
        file=sys.stderr,
    )
    raise SystemExit(2)

import argparse
import json
import re

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

    The same clause `hosted_api._org_graph_names` uses, and it is the safety
    property of the ``graphs`` WALK below: the control plane supplies CANDIDATES,
    ownership decides. ⛔ It does NOT govern the ``organizations.graph_name`` claim
    (which is indexed verbatim, by design — #2023), so that path can still name
    another org's namespace; #13306 tracks that gap and is pre-existing. Without the
    walk's check a malformed (or hostile) ``public.graphs`` row
    ``{org_id: <a test org>, namespace: "org_<a customer>_g_x"}`` would attribute
    a customer's graph to a test org, and an EMPTY customer graph would land in
    ``reapable`` and be deleted — exactly what this tool promises never to do.
    """
    if not org_id or not namespace:
        return False
    if namespace in _own_spellings(org_id):
        return True
    return namespace.startswith((f"org_{org_id}_", f"team_{org_id}_"))


def _own_spellings(org_id: str) -> tuple[str, ...]:
    """The spellings an org's DEFAULT graph is known by — no gid among them."""
    return (canonical_org_graph_name(org_id), f"team_{org_id}", f"org_{org_id}")


def _gid_from_namespace(namespace: str, org_id: str) -> str | None:
    """The per-graph gid a CUSTOM namespace embeds, or ``None``.

    ``_provision_graph`` mints ``org_{org_id}_{gid}`` with ``graphs.id == gid`` and
    the ACL user is ``tenant_{gid}`` (`acl_graph_users`), so for a custom namespace
    the credential is NAMEABLE even when its row is gone, tombstoned or partly
    unreadable.

    ⛔ This is a SHAPE test, never a source of gids: a missing gid never authorises
    a deletion (see `classify_graph`), so nothing here feeds ``entry["gid"]``. It
    does three jobs: it decides which refusal MESSAGE fits, it supplies the gid a
    row's ``id`` must AGREE with (refusal 6(b), in BOTH the live pass and the
    deferred one), and it selects the tombstone wording.

    Two tests, in this order, and they fail for different reasons:

    * the PREFIX is ``org_{org_id}_`` / ``team_{org_id}_``, matched against the
      ROW's org id — so a name is only a candidate when it begins ``{id}_``. A
      registry-lane org's ``graph_name`` is ``org_{name}``, and when ``name`` does
      not begin with the org's own ``{id}_`` no prefix matches at all;
    * the SHAPE then requires the remainder to be ``g_[0-9a-z]+``. A name UNDER the
      org's own prefix but with any other tail — ``org_{org_id}_backup`` — matches
      the prefix and is kept gid-less by THIS test.

    A name that passes both is read as per-graph, and there the row's ``id`` must
    agree with the gid the name spells or the graph is refused (fail-closed; it is
    never reaped on a name). A self-referentially prefixed registry-lane
    ``graph_name`` (``org_{id}_g_x``) passes both, so it is attributed and, when its
    row id disagrees, over-refused — the declared fail-closed residual.
    """
    for prefix in (f"org_{org_id}_", f"team_{org_id}_"):
        if namespace.startswith(prefix):
            tail = namespace[len(prefix):]
            if re.fullmatch(r"g_[0-9a-z]+", tail):
                return tail
    return None


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

    ⛔ SIX refusals, all fail-closed:

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
       decision was nondeterministic on an estate with registry-era rows;
    4. a ``status`` that is not ``'active'`` never lets a row DECIDE which gid a
       namespace lends during the live pass, and a namespace whose rows are all
       non-live is indexed as REFUSED (item 6) — except ``kind='default'`` rows,
       which are DEFERRED and do lend their own id afterwards (see the ⛔ below).
       ``status`` is free text, so the gate is "anything but ``'active'``"
       rather than a vocabulary of the two values the product writes; a row that
       arrives WITHOUT one is not live either (the column is
       ``NOT NULL DEFAULT 'active'`` and is always selected, so an absent one is a
       control plane whose liveness answer cannot be read).

       ⛔ WHERE A GID IS A CREDENTIAL, AND WHERE IT IS NOT. No per-graph ACL user
       is MINTED for a ``kind='default'`` graph — protection rides the org-scoped
       tenant ACL, and ``create_acl_user`` is minted only for CUSTOM graphs
       (``org_{tid}_{gid}`` ⇒ ``tenant_{gid}``). So on a bare ``org_{id}`` /
       ``team_{id}`` namespace the gid is at most this row's OWN credential (a
       graph demoted from custom keeps ``tenant_<id>``), never another graph's. On
       a CUSTOM namespace the gid IS the credential, and that is where the gates in
       items 5 and 6 earn their place.

       ⛔ A NON-LIVE ``kind='default'`` ROW IS NOT REFUSED, it is DEFERRED: claimed
       only after the live pass, when no live row has spoken for the namespace, so
       a live row always decides and the graph stays reapable. Refusing it would
       strand an empty leaked graph for ever — nothing else reclaims one
       (`restore_graph` refuses the kind; the purge sweep enumerates CUSTOM
       tombstones only), which is the waste this tool exists to remove. A
       tombstoned CUSTOM row IS refused (item 6(b)); and
    5. two LIVE rows carrying DISTINCT gids for one namespace are refused, for
       the same reason — never resolved by row order; and
    6. a claim that must not be reaped is refused, with a REASON (what the row
       looked like) and a MESSAGE FORM decided by whether the NAMESPACE names a
       gid at all:

       (a) the namespace names NO gid (a bare ``org_{id}`` / ``team_{id}``, or any
           prefixed-but-not-gid-shaped name such as ``org_{id}_backup``) and its row
           is not live, carries an unrecognised status, or carried no ``id``. No
           credential can be attributed, so the ACL drop cannot be verified — a
           gid-less reap would leave ``tenant_<gid>`` live forever;
       (b) the namespace NAMES its gid (``org_{id}_{gid}`` / ``team_{id}_{gid}``)
           and its row is a tombstone of a ``kind`` other than ``'default'``,
           carries an unrecognised status, or its ``id`` disagrees with that gid. Here the credential is SPELLED BY THE NAME, so
           the refusal is a judgement about DESTROYING the graph, not about
           attributing a drop: a tombstone is restorable within its grace window and
           erasing it is the purge sweep's job, and a row whose ``id`` disagrees must
           not be trusted to hand :func:`reap` an arbitrary gid.

       The REGISTRY-LANE case, scoped precisely: for ``graph_name = org_{name}``
       where ``name`` does NOT begin with the org's own ``{id}_``,
       :func:`_owns_namespace` does not attribute a ``graphs`` row of that shape, so
       its tombstone is invisible to this gate and the name is claimed gid-less from
       ``graph_name`` — a PRE-EXISTING asymmetry on the restorability axis (no
       per-graph credential is involved), tracked separately, not fixed here. A name
       that IS self-referentially prefixed (``org_{id}_g_x``) is attributed like any
       other per-graph name and takes item 6(b)'s agreement check, so it is refused
       rather than left invisible — the declared fail-closed over-refusal.

    What this index does NOT do: derive a gid from the namespace NAME. The name
    does spell the credential (``org_{org_id}_{gid}`` ⇒ ``tenant_{gid}``), but
    `_gid_from_namespace` never supplies a gid of its own: it decides which refusal
    MESSAGE fits, and it supplies the value a live row's ``id`` must agree with
    (item 6(b)). A missing gid never authorises a deletion, so a per-graph
    namespace whose row is gone is refused by `classify_graph`, not reaped on a
    name. One limitation it
    does not claim away: a name refused because it was AMBIGUOUS reads as "no
    organizations row owns this graph name" in the report — the reason is coarse
    (the stderr warning is exact), because the entry leaves the index entirely.

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
        # REFUSAL, never a shorter index. `status` decides whose gid a namespace
        # may lend (refusal 4); `kind` decides whether a TOMBSTONED row is
        # restorable at all — a default graph is not, so refusing it would strand
        # an empty leaked graph for ever.
        graph_rows, complete = cp.query_all(
            "graphs",
            select=["id", "org_id", "namespace", "status", "kind"], cursor="id")
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
    # namespace -> {"org", "reason"}: WHY this namespace must not be reaped. The
    # reason describes the ROW (a tombstone, an unreadable status, no id, an id that
    # disagrees with the namespace); `_refuse` turns it into one of TWO message forms
    # depending on whether the NAMESPACE names a gid, so the reason and the message
    # are not the same statement.
    dormant: dict[str, dict] = {}

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
            if not gid:
                return
            if not held.get("gid"):
                held["gid"] = gid
            elif held["gid"] != gid:
                # Two LIVE rows, two DIFFERENT credentials, one namespace: which
                # one `reap()` deletes would depend on row order, so refuse.
                ambiguous.add(namespace)
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
    unknown_status = 0
    # (namespace, org, row id) for every NON-LIVE `kind='default'` row: claimed
    # only AFTER the live pass, so a live row always wins and a bare namespace
    # nobody reclaims is still reaped (see the branch below).
    deferred_default: list[tuple[str, dict, str]] = []
    for row in graph_rows:
        org = by_id.get(str(row.get("org_id")))
        namespace = row.get("namespace")
        status = row.get("status")
        org_id = str(row.get("org_id") or "")
        owned = bool(org) and _owns_namespace(org_id, str(namespace or ""))
        if not owned or not isinstance(namespace, str) or not namespace:
            continue
        derived = _gid_from_namespace(namespace, org_id)
        row_id = row.get("id")
        # Only a LIVE row may DECIDE a namespace in THIS pass. A tombstoned row can
        # share the bare default namespace with the live one, and taking its id as
        # the gid would make the drop depend on row order — the credential dropped
        # need not be the live graph's (on a bare namespace it is at most the
        # tombstone's own; on a promoted/custom shape it is a real
        # `tenant_<gid>`). A row with NO status is not live either —
        # `graphs.status` is `NOT NULL DEFAULT 'active'` and always selected, so an
        # absent one is a control plane whose liveness answer we cannot read.
        if status != "active":
            if status is None:
                unknown_status += 1
            if row.get("kind") == "default":
                # ⛔ A DEFAULT graph has no minted per-graph ACL user, and nothing
                # reclaims it once it is not live: `restore_graph` refuses the
                # kind, and the purge sweep enumerates CUSTOM tombstones only
                # (#13262's neighbour). Refusing here would strand an empty leaked
                # graph for ever, which is the waste this tool exists to remove.
                # Its gid is at most THIS row's own credential (a graph demoted
                # from custom keeps `tenant_<id>`), so it is claimed after the
                # live pass, when no live row has spoken for the namespace.
                if isinstance(row.get("id"), str) and row.get("id"):
                    deferred_default.append((namespace, org, str(row["id"])))
                continue
            if status == "deleted" and derived:
                # The namespace NAMES the credential, and it is a CUSTOM graph, so
                # a TOMBSTONE is restorable within its grace window and erasing it
                # is the purge sweep's job — not a cleaner's.
                reason = ("its `graphs` row is a TOMBSTONE (status='deleted'), and "
                          "a trashed CUSTOM graph is restorable until the purge "
                          "sweep owns its erasure")
            else:
                reason = (f"its `graphs` row is not live (status={status!r}), which "
                          f"this tool cannot read as an active graph")
            dormant[namespace] = {"org": org, "reason": reason}
            continue
        if not row_id:
            # A live row whose PRIMARY KEY did not arrive is as unreadable as a
            # missing status: the gid decides a DELUSER, and a gid is NEVER
            # reconstructed from the namespace name — a missing one never
            # authorises a deletion (see `classify_graph`).
            dormant[namespace] = {
                "org": org,
                "reason": "its `graphs` row is live but carried no id"}
            continue
        if derived and derived != row_id:
            # A malformed (or hostile) row must not lend an arbitrary gid: that is
            # a wrong-credential DELUSER on the tool's most dangerous axis. The
            # namespace is the authority; a disagreeing id is refused, never
            # trusted (round-5 review).
            dormant[namespace] = {
                "org": org,
                "reason": (f"its `graphs` row's id {row_id!r} disagrees with the "
                           f"gid its namespace embeds ({derived!r})")}
            continue
        _claim(namespace, org, row_id)

    for namespace, org, row_id in deferred_default:
        entry = claims.get(namespace)
        if entry is not None and entry.get("gid") is not None:
            if entry.get("deferred") and entry["gid"] != row_id:
                # TWO non-live default rows, two ids, one namespace: whichever
                # credential is real is not decidable from the rows, so the name is
                # refused rather than resolved by query order (the doctrine the
                # multi-org case already follows).
                ambiguous.add(namespace)
            continue
        # ⛔ The SAME agreement check the live pass makes, because this pass lends a
        # gid too: a drift row whose `id` disagrees with the gid its namespace
        # embeds must not hand `reap()` an arbitrary DELUSER target, and it must
        # not defeat a custom tombstone's refusal when both rows coexist.
        derived = _gid_from_namespace(namespace, str(org.get("id") or ""))
        if derived and derived != row_id:
            dormant[namespace] = {
                "org": org,
                "reason": (f"its non-live `graphs` row's id {row_id!r} disagrees "
                           f"with the gid its namespace embeds ({derived!r})")}
            continue
        _claim(namespace, org, row_id)
        claimed = claims.get(namespace)
        if claimed is not None and claimed.get("gid") == row_id:
            # Remember that this gid came from a NON-LIVE row: `_refuse` may not
            # treat it as the live verdict it stands in for.
            claimed["deferred"] = True

    def _refuse(name: str, org: dict, reason: str) -> None:
        entry = claims.get(name)
        if (entry is not None and entry.get("gid") is not None
                and not entry.get("deferred")):
            # A LIVE row attributed the credential after this complaint was
            # recorded (the tombstone+live default shape, in either order). The gid
            # is known, so the refusal would be false — and a reason claiming "no
            # gid can be attributed" beside a published gid is the kind of record
            # a later lane reads and deletes the gate over (round-6 review).
            #
            # ⛔ A DEFERRED gid is not that verdict: it came from a NON-LIVE
            # default row, so it must not rescue a custom tombstone into a reap
            # (that would destroy a restorable graph, which refusal 6(b) exists to
            # prevent).
            return
        entry = claims.setdefault(name, {"org": org, "gid": None})
        if _gid_from_namespace(name, str(org.get("id") or "")):
            entry["refused"] = (
                f"REFUSED rather than deleted: {reason}. The namespace does "
                f"spell the credential, so this is a judgement about destroying "
                f"the graph, not about attributing its ACL drop")
        else:
            entry["refused"] = (
                f"no gid can be attributed to it ({reason}), so the ACL drop "
                f"cannot be verified and the graph is REFUSED rather than deleted")
        if is_test_org(org):
            # A customer graph is ruled out by the test marker, so its credential
            # verdict is moot — do not warn once per customer org (round-5 review).
            print(f"[warn] {name!r} refused: {entry['refused']}", file=sys.stderr)

    # A gid-less claim that NAMES a credential but cannot attribute it is not
    # reapable: `reap()` would delete the graph and skip the ACL drop, orphaning
    # `tenant_<gid>` forever. A bare default spelling is different — it never had
    # a per-graph credential, so gid-less is its normal shape.
    for name in list(dormant):
        info = dormant.pop(name)
        _refuse(name, info["org"], info["reason"])
    if unknown_status:
        print(f"[warn] {unknown_status} graphs row(s) carried no `status` — "
              f"treated as NOT live: their namespaces are refused, or reaped "
              f"behind a live row's gid, or (for a `kind='default'` row that "
              f"carries an id) reaped with that row's own id deferred", file=sys.stderr)
    for name in ambiguous:
        claims.pop(name, None)
        print(f"[warn] {name!r} is claimed ambiguously (several orgs, several "
              f"live gids, or two non-live default rows) — REFUSED (ambiguity is "
              f"never resolved by row order)", file=sys.stderr)
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
    ownership → test marker → credential attribution → emptiness. Ownership comes
    before the marker so an unresolvable graph is reported as unresolved rather
    than being mis-filed as "some org's, not test" (which would read like a
    decision about an owner); the credential verdict comes AFTER the marker
    because a graph this tool would never touch needs none.
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
    if entry.get("refused"):
        # The org row DOES own it; what cannot be attributed is its credential.
        # This gate comes AFTER the test marker: a graph this tool would never
        # touch needs no credential verdict, and reporting a customer graph as an
        # unattributable orphan would be false (round-5 review).
        return BUCKET_REFUSED_UNRESOLVED, str(entry["refused"])
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


def _used_memory(proj) -> int | None:
    """Instance ``used_memory`` in BYTES, or ``None`` when it cannot be read.

    This is the "how much did the sweep actually reclaim" figure, and it is only
    honest read the way the module docstring measures it: **by deleting**. A
    create-probe reports ~346 KiB per graph because the allocator already holds
    those pages; the resident cost only appears on DELETE (1.46 MB/graph measured,
    216.4 MB freed for 148 graphs). So the tool takes this reading BEFORE and
    AFTER a reap and reports the delta; nothing here probes a graph.

    ⛔ It is an INSTANCE-WIDE reading, so the delta carries whatever else the
    instance did in the window (a tenant write, a peer sweep). That is the
    measurement's residual, stated rather than corrected away: a per-graph memory
    figure does not exist on FalkorDB, and inventing one from the name or the node
    count would be the same false-negative the create-probe produces.

    Returns ``None`` — never ``0`` — when the reading is unavailable: a 0 would be
    minted downstream as "freed nothing", which is the opposite of "not measured"
    (the same over-claim rule `FalkorProjection._memory_pressure` follows).
    """
    info = None
    try:
        # Two client shapes, in the order `_memory_pressure` uses: the raw redis
        # connection (falkordb >= 1.x) and the client's own `.info()` (older /
        # duck-typed handles).
        conn = getattr(proj.db, "connection", None)
        if conn is not None:
            info = conn.info("memory")
        else:
            info = proj.db.info("memory")
    except Exception:
        return None
    if not isinstance(info, dict):
        return None
    try:
        value = int(info["used_memory"])
    except Exception:
        return None
    return value if value >= 0 else None


def _mb(value: int | None) -> str:
    """A byte figure for a report line. ``None`` stays UNMEASURED, never 0."""
    return "unreadable" if value is None else f"{value / 1048576:.1f} MB"


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
                       and not (entry or {}).get("refused")
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
    #
    # One handle for the reap AND both memory readings, on purpose: the delta is
    # only a measurement of THIS sweep if both ends of it are the same store
    # (see `_used_memory`).
    proj = _read_handle()
    used_before = _used_memory(proj)
    outcome = reap(report, proj=proj)
    used_after = _used_memory(proj)
    freed = (None if used_before is None or used_after is None
             else used_before - used_after)
    memory = {"used_before": used_before, "used_after": used_after,
              "bytes_freed": freed}
    failed = [o for o in outcome
              if not o["deleted"] or o["error"] not in (None, "already absent")]
    if args.json:
        print(json.dumps({"report": report, "outcome": outcome,
                          "failed": len(failed), "memory": memory},
                         indent=2, sort_keys=True))
        for o in outcome:
            print(f"  {'deleted' if o['deleted'] else 'FAILED '} {o['graph']}"
                  + (f" — {o['error']}" if o["error"] else ""), file=sys.stderr)
        print(f"\n{len(outcome) - len(failed)} deleted, {len(failed)} failed, "
              f"used_memory {_mb(used_before)} -> {_mb(used_after)} "
              f"(freed {_mb(freed)})", file=sys.stderr)
        return 1 if failed else 0
    for o in outcome:
        print(f"  {'deleted' if o['deleted'] else 'FAILED '} {o['graph']}"
              + (f" — {o['error']}" if o["error"] else ""))
    print(f"\n{len(outcome) - len(failed)} deleted, {len(failed)} failed")
    print(f"used_memory {_mb(used_before)} -> {_mb(used_after)} "
          f"(freed {_mb(freed)})")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
