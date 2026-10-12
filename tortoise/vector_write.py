"""The ONE clear-then-set seam for stored embeddings (#4520).

Why this module exists
----------------------
On the embedded engine (``falkordblite`` / ``redislite``) a
``SET x.embedding = vecf32($v)`` **overwrite** of a property that already holds a
vector is **silently discarded** unless some component moves by ~1.0 — and real
embedder output never does. There is no error and no warning; ``typeof`` still
reads ``Vectorf32`` while the node keeps its **stale** value, so the dense
retrieval leg can answer from a vector that no longer describes the stored
content. The server lane lands the identical write. Measured: ``Δ = 0.02`` on
one component drops on redislite and lands on FalkorDB; ``Δ = 1.0`` (a one-hot
probe) lands on both, which is why a synthetic probe cannot detect this.

The invariant
-------------
**Before an embedding is overwritten, the property must be CLEARED first.**
``REMOVE``/``FOREACH … REMOVE`` ahead of the write is the only shape that lands
on both engines; ``vecf32()`` alone is not enough.

The clear is ATOMIC with the write wherever the dialect allows it
(:func:`clear_then_set_prefix`). For the ``MERGE … ON CREATE SET … ON MATCH
SET …`` shape the dialect REJECTS an interleaved guard (measured —
``FOREACH`` cannot sit between the ``MERGE`` pattern and ``ON CREATE``, nor
between the two arms), so :func:`clear_embedding` issues the clear as its OWN
statement — the pre-existing #4524 shape. **That form is NOT atomic and has a
crash window**: if the process dies (or the set query fails) between the clear
and the write, the node is left with ``embedding = NULL``. Prefer the atomic
form whenever the query shape allows it, and reach for :func:`clear_embedding`
only for the ON CREATE/ON MATCH arms.

This module is the single place that expresses the invariant, so a new writer
cannot silently re-inherit the defect — which has already landed three separate
times (#4457 ``_upsert_point_props``, #4524 the Subject/Object/Document/Event
seams, #4521 ``_revise_point``) because each writer re-spelled the clause
locally. **Every #4520 writer that OVERWRITES an existing vector goes through
one of the helpers below** — the issue's own sites plus the further overwrite
writers this change found (``graph-scripts/backfill_embeddings.py``'s
``_backfill_graph``, ``tortoise/hosted_backup.py``'s restore, the snapshot
derived-restore path in ``projection/__init__.py``,
``battery/runner/setup.py``, ``tools/ask_spotcheck.py``). The claim is narrowed
to OVERWRITES on purpose — the defect is a small-Δ overwrite being silently
dropped, and two kinds of write are not overwrites, so they are deliberately
OUT of this seam's scope and must NOT be routed into it:

* **Δ = 0 self-recast** — ``SET x.embedding = vecf32(x.embedding)`` reads and
  rewrites the SAME property, so no value is replaced and none can be dropped.
  Routing it through :func:`clear_then_set_prefix` would **destroy the
  vector**: the guard REMOVES the property and the ``SET`` then reads the
  property the clear just removed. ``graph-scripts/backfill_embeddings.py``
  holds one, ``_repair_legacy_event_embeddings`` (the pre-#244 plain-``List``
  → ``VectorF32`` repair), named as out of scope at its definition.
* **NULL wipe** — a write whose incoming vector is NULL sets the property to
  NULL rather than replacing its value; the seam's guarantee (that a new vector
  LANDS) does not govern it. Writers that ALSO overwrite still route their
  non-null branch through these helpers.

⚠️ **A KNOWN REMAINING GAP (reported on #4520, NOT closed here):**
``TortoiseSDK.update_point`` and the ``update_entity`` Point branch write props
with a bare ``SET n += $props`` — no ``vecf32``, no clear — so an embedding
reaches the store as a plain Cypher ``List``. Two branches, with different
routes to the fix:

* **Derived** — ``update_point(content=…)`` re-encodes the content and the bare
  SET stores the new embedding as a ``List``. Measured: ``create_point`` stores
  ``Vectorf32``, the content edit flips ``typeof`` to ``List``, and the failure
  surfaces on the next dense READ, where ``vec.euclideanDistance`` raises
  ``Type mismatch: expected Null or VectorF32 but was List`` — so ONE content
  edit degrades the whole Point dense leg. This branch is FIXABLE with
  ``vecf32``/the seam exactly as ``_revise_point`` now is.
* **Caller-supplied** — the verbatim ``embedding`` prop (and ``update_entity``)
  pass the caller's vector through; that branch's write-verbatim #5046
  semantics must be preserved when it is migrated.

Both are the #4521 class (a plain-list write, not the small-Δ overwrite this
seam fixes), so they need their own scoped change; they must NOT be assumed
covered by this seam until their writers are migrated too.

Two shapes, one invariant
-------------------------
* :func:`clear_then_set_prefix` — the in-query, atomic form. Splice it between
  the ``MERGE``/``MATCH`` pattern and the ``SET`` list. It is a Cypher-side
  ``FOREACH (_ IN CASE WHEN <expr> IS NOT NULL THEN [1] ELSE [] END | REMOVE
  <alias>.embedding)`` guard, so the *clear happens only when a real vector is
  being written* — a null vector preserves the stored value, exactly as the
  callers' ``CASE … ELSE x.embedding`` branches require. It is safe on a fresh
  ``MERGE`` (nothing to remove) and needs no Python-side gating.
* :func:`clear_embedding` — the separate-query form, for the
  ``MERGE … ON CREATE SET … ON MATCH SET …`` shape whose arms the dialect will
  not let an interleaved ``REMOVE``/``FOREACH`` sit inside. It carries the SAME
  no-op-when-absent contract (it returns without touching the store when the
  incoming vector is ``None``), so it can be called unconditionally — but it is
  NOT atomic (see above).

:func:`set_embedding_clause` is the canonical ``… = CASE WHEN … THEN
vecf32(…) …`` SET clause, so the clear and the set are written once, together.

This is an interim correctness fix on the path that exists between now and the
recorded ``STORE-DEC`` architecture (the vector leg leaves the graph — Postgres
holds the record, FalkorDB a derived hot cache). It is not a competing
architecture choice: it stops the defect harming users today and keeps the class
from regrowing while the vector leg is retired.
"""
from __future__ import annotations

__all__ = [
    "EMBEDDING_PROPERTY",
    "clear_embedding",
    "clear_then_set_prefix",
    "set_embedding_clause",
]

#: The node property every embedding writer targets.
EMBEDDING_PROPERTY = "embedding"

#: The default Cypher expression holding the incoming vector.
DEFAULT_VECTOR_EXPR = "$embedding"


def clear_then_set_prefix(alias: str,
                          vector_expr: str = DEFAULT_VECTOR_EXPR) -> str:
    """The in-query clear guard — splice before the ``SET`` list.

    ``alias`` is the node binding in the caller's ``MERGE``/``MATCH`` (``n``,
    ``s``, ``e``, ``t``, …). ``vector_expr`` is the Cypher expression holding
    the incoming vector (``$embedding``, ``turn.emb``, ``row.emb``, ``r.v``);
    the clear fires only when it is non-null, so a null vector preserves the
    stored value.

    The returned prefix is spliced immediately after the ``MERGE``/``MATCH``
    pattern::

        self.g.query(
            "MERGE (n:Point {id:$id}) " + clear_then_set_prefix("n")
            + "SET " + ", ".join(set_clauses),
            params=params,
        )
    """
    return (
        f"FOREACH (_ IN CASE WHEN {vector_expr} IS NOT NULL THEN [1] ELSE [] END | "
        f"REMOVE {alias}.{EMBEDDING_PROPERTY}) "
    )


def clear_embedding(store, match_pattern: str, alias: str, params: dict,
                    vector) -> None:
    """Execute the separate-query clear guard against ``store``.

    The pre-overwrite clear for the ``MERGE … ON CREATE SET … ON MATCH SET …``
    shape, whose arms the dialect will not let an interleaved guard sit inside
    — so it is its OWN statement, exactly as the #4524 fix had it.

    ``store`` is any graph handle with ``.query(cypher, params=…)``
    (``FalkorProjection.g`` / a ``_GuardedGraph``). ``match_pattern`` is the
    full node pattern including its alias, e.g. ``"(s:Subject {name:$name})"``.
    ``vector`` is the incoming vector; when it is ``None`` the call is a NO-OP,
    so the caller cannot forget a gate and an absent vector can never clear a
    stored one. ``params`` is the caller's own parameter map for the match.
    """
    if vector is None:
        return
    store.query(
        f"MATCH {match_pattern} REMOVE {alias}.{EMBEDDING_PROPERTY}",
        params=params)


def set_embedding_clause(alias: str,
                         vector_expr: str = DEFAULT_VECTOR_EXPR,
                         *,
                         preserve_on_null: bool = True) -> str:
    """The canonical ``<alias>.embedding = …`` SET clause.

    ``preserve_on_null=True`` (the overwrite writers): keep the stored value
    when the incoming vector is null. ``preserve_on_null=False`` (the
    wipe-on-null writers — a re-encode whose content became empty, or a CREATE
    with no vector): write NULL. ``vecf32`` is applied only to a real vector,
    never to NULL.
    """
    else_branch = f"{alias}.{EMBEDDING_PROPERTY}" if preserve_on_null else "NULL"
    return (
        f"{alias}.{EMBEDDING_PROPERTY} = CASE WHEN {vector_expr} IS NOT NULL "
        f"THEN vecf32({vector_expr}) ELSE {else_branch} END"
    )
