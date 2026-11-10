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
locally. **Every #4520 writer this seam MIGRATED that OVERWRITES an existing
vector goes through one of the helpers below** — the issue's own sites plus the
further overwrite writers this change found
(``graph-scripts/backfill_embeddings.py``'s ``_backfill_graph``,
``tortoise/hosted_backup.py``'s restore, the snapshot derived-restore path in
``projection/__init__.py``, ``battery/runner/setup.py``,
``tools/ask_spotcheck.py``, and the DERIVED branch of
``TortoiseSDK.update_point``). The claim is narrowed to OVERWRITES on purpose —
the defect is a small-Δ overwrite being silently dropped, and two kinds of
write are not overwrites, so they are deliberately OUT of this seam's scope and
must NOT be routed into it:

* **Δ = 0 self-recast** — ``SET x.embedding = vecf32(x.embedding)`` reads and
  rewrites the SAME property, so no value is replaced and none can be dropped.
  Routing it through :func:`clear_then_set_prefix` would **destroy the
  vector**: the guard REMOVES the property and the ``SET`` then reads the
  property the clear just removed. ``graph-scripts/backfill_embeddings.py``
  holds one, ``_repair_legacy_event_embeddings`` (the pre-#244 plain-``List``
  → ``Vectorf32`` repair), named as out of scope at its definition.
* **NULL wipe** — a write whose incoming vector is NULL sets the property to
  NULL rather than replacing its value; the seam's guarantee (that a new vector
  LANDS) does not govern it. Writers that ALSO overwrite still route their
  non-null branch through these helpers.

⚠️ **One branch is CLOSED, and one is deliberately OPEN (both reported on
#4520):** the CALLER-supplied branch of ``TortoiseSDK.update_point`` and the
``update_entity`` Point branch write props with a bare ``SET n += $props`` — no
``vecf32``, no clear — so an embedding reaches the store as a plain Cypher
``List``. Two branches, with different dispositions:

* **Derived — CLOSED.** ``update_point(content=…)`` re-encodes the content
  (#4208) and the bare SET used to store the new embedding as a ``List``,
  which flipped ``typeof`` from ``Vectorf32`` to ``List`` on ONE content
  edit. The DERIVED vector is now routed through this seam: the ``embedding``
  key is stripped from the verbatim merge and written with
  :func:`clear_then_set_prefix` + :func:`set_embedding_clause`
  (``preserve_on_null=False``), exactly as ``_revise_point`` is, while every
  other key keeps the caller-supplied-patch ``SET n += $props`` semantics. Both
  halves are required: the ``vecf32()`` cast keeps a server-owned value in the
  typed shape every other SERVER-OWNED writer produces (the caller-supplied
  branch below deliberately stores a plain ``List`` — PR #3018) — a bare
  ``List`` node is absent from the Point HNSW index cohort (#8429) — and the
  clear lands the small-Δ
  overwrite the embedded engine would otherwise DISCARD. (Before #8316 that
  flip also made the next dense READ raise ``Type mismatch: expected Null or
  Vectorf32 but was List``; the read boundary now normalises a right-width
  ``List``, so only a wrong width or a non-numeric value still aborts the
  MATCH — #8430.)
* **Caller-supplied — WRITE verbatim, READ normalised.** The verbatim
  ``embedding`` prop (``update_point`` with an explicit vector, and the
  ``update_entity`` Point branch, which never derives) still passes the
  caller's vector through RAW. That is a **recorded decision** (PR #3018's
  TYPE CHANGE GUARD: a caller-owned float64 list must not be silently narrowed
  through ``vecf32``), not an oversight — reversing it is the #5046/#5048
  reopen, never a quiet re-route through :func:`set_embedding_clause`. It is
  the #4521 class (a plain-list write, not the small-Δ overwrite this seam
  fixes), so it must NOT be assumed covered by the WRITE helpers above.

  The defect that verbatim write used to cause is fixed at the READ boundary
  instead (:func:`stored_embedding_vector_expr`, #8316): the stored plain
  ``List`` made ``vec.euclideanDistance`` raise ``Type mismatch: expected Null
  or Vectorf32 but was List`` for the WHOLE ``MATCH``, so ONE caller-vectored
  Point degraded the entire dense leg — every other Point included — to
  FTS-only recall. The stored value is untouched (the decision stands); only
  the distance expression normalises the shape it reads.

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

:func:`stored_embedding_vector_expr` is the READ counterpart of the same shape
contract: the one Cypher expression a dense read must use for a stored
embedding, so BOTH shapes this seam permits — the ``Vectorf32`` every server
writer lands and the verbatim plain ``List`` a caller may own — are comparable.
A scan/brute-force read must never spell ``<alias>.embedding`` directly (see the
function). The HNSW index arm is served typed rows only — a ``List`` node is not
in the index at all, which is a separate cohort gap (#8429), not a second read
to normalise.

This is an interim correctness fix on the path that exists between now and the
recorded ``STORE-DEC`` architecture (the vector leg leaves the graph — Postgres
holds the record, FalkorDB a derived hot cache). It is not a competing
architecture choice: it stops the defect harming users today and keeps the class
from regrowing while the vector leg is retired.
"""
from __future__ import annotations

import re

__all__ = [
    "EMBEDDING_PROPERTY",
    "clear_embedding",
    "clear_then_set_prefix",
    "require_served_store_labels",
    "served_store_labels",
    "set_embedding_clause",
    "statement_interpolates_label",
    "statement_node_labels",
    "stored_embedding_vector_expr",
]

#: The node property every embedding writer targets.
EMBEDDING_PROPERTY = "embedding"

#: The default Cypher expression holding the incoming vector.
DEFAULT_VECTOR_EXPR = "$embedding"


#: ── WHERE a vector may be stored, at the SAME seam (#11641) ──────────────────────────────────
#: A Cypher label is not a bindable parameter — it is part of the statement text — so every writer
#: that stores a vector has TWO facts to get right, not one: the clause (above) and the LABEL the
#: clause's alias is bound to. This section is the one home of the second half, for the same reason
#: the section above is the one home of the first: a writer that spells its own label locally can
#: put a vector somewhere no read reaches, and `#5090` DoD row 2 exists to catch exactly that.
#: ⛔ Nothing here creates an index, and nothing here reads or writes a store — it is a bound, not
#: a migration. `STORAGE-ARCHITECTURE.md` §14.3 V1 (the vector INDEX lives in Postgres) is
#: untouched by it.

def served_store_labels() -> frozenset[str]:
    """The labels a stored vector can be READ BACK from: the query path's own vocabulary.

    ⛔ ONE owner, never a second copy. This reads `tortoise.security`'s vocabulary — the same one
    `run_vector_query` validates its ``entity_type`` against and derives its query label from — so
    the served set here cannot drift from the served set there. A literal list of the five labels
    in this module would be a second declaration of where a vector can be found, and the two could
    disagree with nothing going red: the failure mode row 2 is scored on.

    ``operator`` and ``document`` collapse onto ``Point`` and ``Source`` by ``entity_label``, which
    is why seven entity types are five labels.
    """
    from tortoise.security import VALID_ENTITY_TYPES, entity_label
    return frozenset(entity_label(t) for t in VALID_ENTITY_TYPES)


#: A node label as a Cypher PATTERN binds it — `(n:Point`, `MATCH (e:Event`, `, (b:Batch`. ⛔ Not
#: anchored to `MATCH|MERGE|CREATE`: a single statement binds SEVERAL patterns and only the first
#: follows the keyword (`MATCH (e:Event {…}), (b:Batch {…})` — measured, the keyword-anchored form
#: missed `Batch` entirely and let a stray label through the door's bound). ``\w+`` deliberately does
#: NOT match an INTERPOLATED label (`(n:{safe_labels})`), because the point of this pattern is to let
#: a writer prove the labels its statement NAMES; an interpolation is the shape that fact cannot be
#: read off, and a writer using one must declare a bound instead (see
#: `require_served_store_labels`).
_STATEMENT_LABEL_RE = re.compile(r"\(\s*(?:[A-Za-z_]\w*\s*)?:\s*([A-Za-z_]\w*)")

#: A node label rendered by INTERPOLATION inside a node pattern — `(n:{safe_labels})`. It is the
#: EXACT complement of :data:`_STATEMENT_LABEL_RE`: same prefix (so a nested map literal
#: `{meta: {a: 1}}` cannot trip it), but a `{` where a label identifier would be. Kept beside that
#: pattern because the two together say the whole truth about what a statement names — the reader
#: sees the LITERAL labels, this sees the ones no reader can read off.
_INTERPOLATED_LABEL_RE = re.compile(r"\(\s*(?:[A-Za-z_]\w*\s*)?:\s*\{")


def statement_node_labels(statement: str) -> frozenset[str]:
    """Every node label a Cypher statement binds in a node PATTERN (``(x:Label``).

    The reader a *door* uses: a writer that executes a caller-supplied statement cannot name its
    own label in its own body, so it names the labels the STATEMENT declares and refuses anything
    outside them (``tortoise.sdk::_event_raw_write_door``, #11641).

    ⚠️ It matches the pattern SHAPE, not the clause that introduces it, so it sees every comma- or
    ``WHERE``-separated pattern in one statement — not only the first one after ``MATCH``/``MERGE``/
    ``CREATE``. That breadth is deliberate (a keyword-anchored form missed a stray second pattern,
    measured) and it errs in the REFUSING direction: a false positive costs a writer a clear error,
    where a false negative stores a vector somewhere no read path reaches.
    """
    return frozenset(_STATEMENT_LABEL_RE.findall(statement))


def statement_interpolates_label(statement: str) -> bool:
    """True when ``statement`` renders a node label by INTERPOLATION — ``(n:{safe_labels})``.

    The companion of :func:`statement_node_labels`, and the reason a *door* needs one: a reader
    that returns only the labels a statement NAMES cannot see a pattern whose label is rendered at
    runtime, so a caller could satisfy the bound with one literal ``(e:Event …)`` pattern and store
    the vector on a SECOND, interpolated one — the label inherited from the caller again, which is
    exactly what a bound is supposed to end.

    So the two answer different questions, and a writer that cannot name its own label must ask
    both: *which labels does the statement name* (:func:`statement_node_labels`) and *does it name
    one by interpolation* (this). Nothing here inspects a graph — it is a text-level bound.
    """
    return bool(_INTERPOLATED_LABEL_RE.search(statement))


def require_served_store_labels(labels, *, writer: str) -> None:
    """Refuse a vector store onto a label ``run_vector_query`` cannot serve.

    ``labels`` is every label the write targets (the writer's declared set, or the labels a
    caller's statement names). Raises ``ValueError`` naming the writer, the offending labels and
    the served vocabulary — fail CLOSED, before the write, because the alternative is a vector that
    is paid for and unreachable: the defect `#5090` DoD row 2 is scored on.

    ⛔ This is the bound, not the read path's own validation. `run_vector_query` already refuses an
    unknown ``entity_type``; this refuses a WRITE that would land somewhere no ``entity_type``
    reaches, which is the half nothing checked before `#11641`.
    """
    served = served_store_labels()
    stray = sorted(set(labels) - served)
    if stray:
        raise ValueError(
            f"{writer} would store a vector on label(s) {stray}, which no read path serves — "
            f"`run_vector_query` can only reach {sorted(served)}. A vector stored on any other "
            "label is paid for and unreachable, so the write is refused (#11641)."
        )


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


def stored_embedding_vector_expr(alias: str) -> str:
    """The READ half of the shape contract — ``<alias>.embedding`` as a vector.

    ``alias`` is the node binding of the caller's ``MATCH`` (``n``). Returns a
    Cypher expression that is the stored embedding as a ``Vectorf32`` whichever
    of the two shapes this seam permits is on the node:

    * a plain ``List`` — the VERBATIM caller-owned value (PR #3018's recorded
      decision) — is cast once through ``vecf32()``; a read spelled
      ``<alias>.embedding`` would raise ``Type mismatch: expected Null or
      Vectorf32 but was List`` and abort the WHOLE ``MATCH``, so one such node
      would degrade the entire dense leg to FTS-only recall (#8316);
    * a ``Vectorf32`` — every server-computed embedding — is returned
      unchanged.

    Why an expression and not a write-side fix: narrowing the caller's value at
    the write boundary is the #5046/#5048 reopen, never a quiet re-route
    through :func:`set_embedding_clause` (see the module docstring).

    ⛔ Do NOT "simplify" this to ``CASE WHEN typeof(…) = 'Vectorf32' THEN …
    ELSE vecf32(…) END``. BOTH engines evaluate BOTH ``CASE`` arms, so the
    ``vecf32`` arm runs on every already-typed row and the whole query raises
    ``Type mismatch: expected List or Null but was Vectorf32`` (measured on
    embedded FalkorDBLite and on FalkorDB 6.x). The list-comprehension FILTER
    is lazy on both engines, which is why the shape test is expressed as one.

    Callers must filter ``WHERE <alias>.embedding IS NOT NULL`` (the dense read
    does): a NULL embedding yields NULL here, like any null Cypher argument.
    """
    prop = f"{alias}.{EMBEDDING_PROPERTY}"
    return (
        f"coalesce([_v IN [{prop}] WHERE typeof(_v) = 'List' | vecf32(_v)][0], "
        f"{prop})"
    )
