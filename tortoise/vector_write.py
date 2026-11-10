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
    "statement_label_mutations",
    "statement_node_labels",
    "statement_pattern_labels",
    "statement_rebound_aliases",
    "statement_unattributed_set_items",
    "statement_uses_unmodelled_scope",
    "statement_write_aliases",
    "statement_write_target_problem",
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
#: A Cypher identifier in its BARE spelling. ⛔ This is NOT the whole spelling surface — see
#: :data:`_IDENT_FRAGMENT` and :func:`_identifier_at`; keeping ONE definition of the bare form is
#: what stops the readers below disagreeing about what an identifier is.
_IDENTIFIER_RE = re.compile(r"[A-Za-z_]\w*")

#: A Cypher identifier, EITHER spelling: bare (``e``) or backtick-quoted (`` `b` ``). One token to
#: the engine — FalkorDB's lexer returns ``Token::IdentifierOrKeyword`` for a backticked span
#: (`graph/src/parser/lexer.rs` @ `0f45a86`), it does NOT read it as a string — so a reader that
#: accepted only the bare spelling was BLIND to an identifier the engine accepts. That blindness is
#: the fail-open direction and it was live on this very guard (#11641, measured at `9d389cc`).
_IDENT_FRAGMENT = rf"(?:{_IDENTIFIER_RE.pattern}|`[^`]+`)"

_STATEMENT_LABEL_RE = re.compile(
    rf"\(\s*(?:{_IDENT_FRAGMENT}\s*)?:\s*({_IDENT_FRAGMENT})")

#: A node label rendered by INTERPOLATION inside a node pattern — `(n:{safe_labels})`. It is the
#: EXACT complement of :data:`_STATEMENT_LABEL_RE`: same prefix (so a nested map literal
#: `{meta: {a: 1}}` cannot trip it), but a `{` where a label identifier would be. Kept beside that
#: pattern because the two together say the whole truth about what a statement names — the reader
#: sees the LITERAL labels, this sees the ones no reader can read off.
_INTERPOLATED_LABEL_RE = re.compile(
    rf"\(\s*(?:{_IDENT_FRAGMENT}\s*)?:\s*\{{")


def _blank_string_literals(statement: str) -> str:
    """``statement`` with every quoted STRING literal blanked to spaces — same length, same indices.

    ⛔ The NAMING readers need this and the WRITE-TARGET readers get it from :func:`_depths`, which
    marks a quoted span ``-1``. Without it the two halves of one module read the grammar differently:
    measured at `1f82113dd`, ``MATCH (e:Event {eventId:$eid}) SET e.note = '(b:Batch)'`` made
    :func:`statement_node_labels` return ``{'Batch', 'Event'}`` and ``SET e.note = '(n:{x})'`` made
    :func:`statement_interpolates_label` return True — so the door REFUSED a legitimate statement
    while naming a label the statement never binds. Fail-closed, but a false positive whose message
    is not even true, which is the one error class this seam's own docstrings promise it does not
    produce.

    BACKTICKED spans are NOT blanked: a backtick is Cypher's spelling of an IDENTIFIER (the engine's
    lexer returns ``Token::IdentifierOrKeyword`` for it), so `` (`b`:Batch) `` must keep reading as
    the alias ``b``. A single or double quote opens a STRING, which can never be a node label.

    ⛔ A BACKTICK IS TRACKED, exactly as :func:`_blank_comments` tracks it, because a ``'`` INSIDE a
    backticked identifier is part of that identifier and not the start of a string. Without the
    state, ``MATCH (`a'b`:Event …) SET `a'b`.embedding = …`` opened a phantom string at the ``'``
    and blanked the REST of the statement: the door refused a legal statement with "names NO node
    label", and — the fail-open half — a stray pattern placed after such an identifier became
    invisible while leg 3 (which never blanks) still proved a target from the part before it
    (measured at `66da0c05`). One module, one reading of the grammar.
    """
    out = list(statement)
    index = 0
    length = len(statement)
    quote: str | None = None
    escaped = False
    while index < length:
        char = statement[index]
        if quote is not None:
            if quote != "`":
                out[index] = " "
            if escaped:
                escaped = False
            elif char == "\\" and quote != "`":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in ("'", '"', "`"):
            quote = char
            if char != "`":
                out[index] = " "
        index += 1
    return "".join(out)


def _naming_text(statement: str) -> str:
    """The text the NAMING readers read: comments AND string literals blanked, indices preserved."""
    return _blank_string_literals(_blank_comments(statement))


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

    ⛔ THIS IS THE *NAMING* QUESTION, NOT THE *STORING* ONE. It answers "which labels does the
    statement mention"; :func:`statement_write_target_problem` answers "where does the vector land",
    and a bound that asks only the first is a false PASS — `MATCH (e:Event …) CREATE (p) SET p += …`
    returns `{'Event'}` here while storing on the label-less node `[]` (measured, #11641). A door
    must ask both; the two live here so the answers cannot drift apart.

    Labels are returned UNQUOTED, so `` (`b`:Batch) `` and `` (b:Batch) `` both read ``Batch`` —
    the engine strips the quotes too, and reporting them would make a served label look unserved.
    Cypher comments AND quoted string literals are blanked first (see :func:`_naming_text`), so a
    label named inside a comment — or inside a string, as in ``SET e.note = '(b:Batch)'`` — is not a
    label this statement binds. A BACKTICKED identifier is syntax, not a string, and is kept.
    """
    return frozenset(label.strip("`") for label in
                     _STATEMENT_LABEL_RE.findall(_naming_text(statement)))


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
    return bool(_INTERPOLATED_LABEL_RE.search(_naming_text(statement)))


#: ── the WRITE TARGET: the node the statement writes TO (#11641) ──────────────────────────────
#: The three readers above answer *what does the statement NAME*. That is NOT the question a bound
#: on ``stores=(...)`` has to answer: a statement can name a served label in a ``MATCH`` and store
#: the vector on a DIFFERENT, partly or wholly unlabelled alias — measured on this PR, where
#: ``MATCH (e:Event …) CREATE (p) SET p += $props, <embedding clause on p>`` passed every reader
#: above and landed a ``Vectorf32`` on the label-less node ``[]``, which no ``entity_type`` reaches.
#: The readers below answer the question the register's declaration actually makes: **where does the
#: vector land**, at the same seam, so the answer has one home rather than one per writer.
#:
#: ⛔ WHY THE ANSWER IS TEXT AND NOT A BINDABLE PARAMETER. A Cypher label is query STRUCTURE, not a
#: value: Neo4j's Cypher Manual (Parameters — *"Parameters cannot be used for the following
#: constructs, as these form part of the query structure that is compiled into a query plan: … Node
#: labels; ``MATCH (n:$param)`` is invalid"*), and this engine is the same family. A door that
#: EXECUTES a caller's statement therefore has no parameter to ask and no engine API that reports
#: "which label did that statement write to". The only two options are (a) a reader over the
#: statement TEXT, or (b) stop executing caller text for the store; this module is the one home of
#: (a), and it is written to fail CLOSED.
#:
#: ⚠️ A TEXT READER'S COMPLETENESS IS NOT KNOWABLE A PRIORI — so breadth is spent in the REFUSING
#: direction, exactly as :func:`statement_node_labels` does, and the shapes it still cannot see are
#: named at :func:`statement_write_target_problem` rather than left implied.

#: A Cypher statement's clause keywords. Used only to find where a clause BODY ends, so a keyword
#: inside a quoted string, inside a bracket, or as a ``.property`` / ``:Label`` component is not one.
#: ``ON`` is included so ``MERGE (…) ON CREATE SET …`` ends its pattern list at ``ON``.
_CLAUSE_KEYWORD_RE = re.compile(
    r"(?<![\w$.:])(MATCH|MERGE|CREATE|SET|REMOVE|DELETE|DETACH|RETURN|WITH|UNWIND|WHERE|"
    r"FOREACH|CALL|ORDER\s+BY|LIMIT|SKIP|UNION|ON)(?![\w$:])",
    re.IGNORECASE,
)

#: The characters that, IMMEDIATELY before a ``(`` with no space between them, make it a function
#: CALL rather than a node pattern. ``vecf32($embedding)``, ``coalesce(…)`` and every other call are
#: excluded by this rule alone, so the reader needs no list of function names. A space before the
#: ``(`` is not a call — that is what ``MATCH (e:Event …)`` depends on — so only the IMMEDIATE
#: predecessor is tested (see :func:`_scan_node_patterns`).
_NOT_A_PATTERN_PREFIX = re.compile(r"[A-Za-z0-9_$)\]}]", re.UNICODE)

def _blank_comments(statement: str) -> str:
    """``statement`` with every Cypher COMMENT blanked to spaces — same length, same indices.

    ⛔ A comment is not syntax, and reading one as syntax is a FAIL-OPEN. ``_depths`` decrements on
    every ``)`` outside a quote, so a ``)`` inside ``/* … */`` drove the depth negative and silently
    removed EVERY FOLLOWING clause from the write-target reader — measured at `9d389cc`: a ``CREATE
    (p)`` and its embedding write placed AFTER such a comment in an otherwise-valid statement were
    invisible, the reader's only target was ``e``, and the door ACCEPTED while the vector landed on
    the label-less node ``p``. FalkorDB's lexer skips ``// …`` and ``/* … */`` between tokens, so
    blanking is what the engine does too.

    An UNTERMINATED ``/*`` is NOT a comment — the lexer leaves that ``/`` for the parser to reject —
    so no span is opened for it here. Quoted spans (strings and backticked identifiers) are copied
    through untouched: a ``//`` inside ``'a//b'`` is text, not a comment.
    """
    out = list(statement)
    index = 0
    length = len(statement)
    quote: str | None = None
    escaped = False
    while index < length:
        char = statement[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\" and quote != "`":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in ("'", '"', "`"):
            quote = char
            index += 1
            continue
        if char == "/" and index + 1 < length:
            if statement[index + 1] == "/":
                end = statement.find("\n", index)
                end = length if end < 0 else end
            elif statement[index + 1] == "*":
                close = statement.find("*/", index + 2)
                if close < 0:                 # unterminated — NOT a comment
                    index += 2
                    continue
                end = close + 2
            else:
                index += 1
                continue
            for position in range(index, end):
                out[position] = " "
            index = end
            continue
        index += 1
    return "".join(out)


def _identifier_at(text: str, index: int) -> tuple[str, int] | None:
    """Read the Cypher IDENTIFIER at ``index`` — bare (``e``) or backtick-quoted (`` `b` ``).

    Returns ``(name, end)`` with the quotes STRIPPED (the engine strips them too), or ``None`` when
    no identifier starts here.

    ⛔ WHY BOTH SPELLINGS, AND WHY THIS IS THE ONLY READER. A backticked span is an identifier to the
    engine, not a string (:data:`_IDENT_FRAGMENT`). Every reader in this module used to match the
    bare form directly, so a quoted alias or label was invisible to all of them at once — and
    ``_depths`` marks the span ``-1`` (it must: a comma or paren INSIDE an identifier is not
    syntax), which is why the quoting could not simply be dropped. The measured fail-open at
    `9d389cc` (#11641):

        MATCH (e:Event {eventId:'seed'}), (`b`:Batch {id:'b1'}) SET e += $props, `b`.embedding = …

    ``statement_node_labels`` read only ``Event`` (its alias group could not match a backtick) and
    ``statement_write_aliases`` found no ``SET`` head in `` `b`.embedding ``, so the door accepted it
    and stored a ``Vectorf32`` on ``:Batch`` — reachable by no ``entity_type``, with
    ``stores=("Event",)`` and DoD row 2 both still green.

    The repo's own class readers already accept the quoted spelling for exactly this reason
    (``tests/test_9079_onboarding_declared_lane.py::_labels_written``).
    """
    if index >= len(text):
        return None
    if text[index] == "`":
        close = text.find("`", index + 1)
        if close <= index + 1:                # unterminated, or an EMPTY name — no identifier
            return None
        return text[index + 1:close], close + 1
    match = _IDENTIFIER_RE.match(text, index)
    if match is None:
        return None
    return match.group(0), match.end()


def _item_head(item: str) -> tuple[str, str] | None:
    """Split one ``SET``/``REMOVE`` item into ``(alias, rest)`` — ``e`` and ``.embedding = …``.

    ``rest`` is what tells a PROPERTY write from a LABEL mutation (``:Label``), so the head reader
    has to accept both identifier spellings (:func:`_identifier_at`) — otherwise `` `b`.embedding ``
    yields no head and the alias `b` never appears as a write target at all.
    """
    cursor = 0
    while cursor < len(item) and item[cursor].isspace():
        cursor += 1
    identifier = _identifier_at(item, cursor)
    if identifier is None:
        return None
    name, end = identifier
    return name, item[end:].strip()


def _depths(statement: str) -> list[int]:
    """The bracket nesting depth BEFORE each character; ``-1`` marks a QUOTED span.

    ``-1`` is what keeps a comma, a paren or a keyword inside a string literal out of every reader
    below: a quote opens a span that runs to its matching quote (with ``\\`` escapes), and every
    character in it — the quotes included — is marked ``-1``. A comma inside ``'a, b'`` therefore
    cannot split a ``SET`` list, and ``'{not: a label}'`` cannot be read as a map literal.
    """
    depth = 0
    quote: str | None = None
    escaped = False
    depths: list[int] = [0] * len(statement)
    for index, char in enumerate(statement):
        if quote is not None:
            depths[index] = -1
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in ("'", '"', "`"):
            quote = char
            depths[index] = -1
        elif char in "([{":
            depths[index] = depth
            depth += 1
        elif char in ")]}":
            depth -= 1
            depths[index] = depth
        else:
            depths[index] = depth
    return depths


def _clause_bodies(statement: str, depths: list[int]) -> list[tuple[str, int, int]]:
    """``(KEYWORD, body_start, body_end)`` for every clause, at ANY nesting depth.

    Depth-relative on purpose: a store nested in ``FOREACH`` or in a ``CALL { … }`` subquery is
    still a store, and a reader that only looked at depth 0 would let it through while the outer
    statement's own target looked fine.
    """
    bodies: list[tuple[str, int, int]] = []
    for match in _CLAUSE_KEYWORD_RE.finditer(statement):
        start = match.start()
        if depths[start] < 0:  # a keyword inside a quoted string is text, not a clause
            continue
        base = depths[start]
        end = len(statement)
        for index in range(match.end(), len(statement)):
            inner = depths[index]
            if inner < 0:
                continue
            if inner < base:
                end = index
                break
            if inner == base and _CLAUSE_KEYWORD_RE.match(statement, index):
                end = index
                break
        bodies.append((match.group(1).upper(), match.end(), end))
    return bodies


def _split_items(statement: str, start: int, end: int, base: int,
                 depths: list[int]) -> list[str]:
    """Split a clause body on the commas at ITS OWN depth — never on a comma inside a map, a
    list, a function call or a string (``SET e.meta = {a: {b: 1}}`` is ONE item)."""
    items: list[str] = []
    current = start
    for index in range(start, end):
        if depths[index] == base and statement[index] == ",":
            item = statement[current:index].strip()
            if item:
                items.append(item)
            current = index + 1
    tail = statement[current:end].strip()
    if tail:
        items.append(tail)
    return items


def _scan_node_patterns(statement: str, depths: list[int],
                        start: int = 0, end: int | None = None
                        ) -> list[tuple[str | None, frozenset[str], int]]:
    """Every node PATTERN beginning in ``[start, end)``, as ``(alias, labels, pattern_start)``.

    A pattern is a ``(`` that is not a function call (see :data:`_NOT_A_PATTERN_PREFIX`) and whose
    body parses as ``(:Label…)``, ``(alias)``, ``(alias:Label…)`` or ``(alias{…})``. Once a pattern
    is recognised its whole span is SKIPPED, so a ``(`` inside its property map cannot be read as a
    second pattern.

    ``labels`` is EMPTY when the pattern binds no label — and that absence is the fact the door
    needs: an alias bound with no label is a node no ``entity_type`` reaches.
    """
    patterns: list[tuple[str | None, frozenset[str], int]] = []
    length = len(statement) if end is None else min(end, len(statement))
    index = start
    while index < length:
        if statement[index] != "(" or depths[index] < 0:
            index += 1
            continue
        previous = index - 1
        # ⛔ Only an IMMEDIATE identifier/bracket predecessor makes this a function call: a space
        # before the ``(`` is a pattern (`MATCH (e:Event …)`), no space is a call
        # (`vecf32($embedding)`). Skipping whitespace here would read every `MATCH (` as a call.
        if (previous >= 0 and not statement[previous].isspace()
                and _NOT_A_PATTERN_PREFIX.match(statement[previous])):
            index += 1
            continue
        base = depths[index]
        close = None
        for candidate in range(index + 1, length):
            if depths[candidate] == base and statement[candidate] == ")":
                close = candidate
                break
        if close is None:
            index += 1
            continue
        cursor = index + 1
        while cursor < close and statement[cursor].isspace():
            cursor += 1
        alias: str | None = None
        labels: set[str] = set()
        recognised = True
        if cursor >= close:                       # `()` — an anonymous empty pattern
            pass
        elif statement[cursor] == ":":           # `(:Label …)` — anonymous, labelled
            labels = _read_labels(statement, cursor + 1, close)
        elif statement[cursor] == "{":           # `({…})` — anonymous, properties only
            pass
        else:
            identifier = _identifier_at(statement, cursor)
            if identifier is None:
                recognised = False
            else:
                name, after = identifier
                while after < close and statement[after].isspace():
                    after += 1
                tail = statement[after] if after < close else ")"
                if tail == "(":               # `name(` — a function call, not a pattern
                    recognised = False
                elif tail == ":":
                    alias = name
                    labels = _read_labels(statement, after + 1, close)
                elif tail in "){-<|" or tail == "]":
                    alias = name
                else:                          # `(x IN …)`, `(x WHERE …)` — not a pattern
                    recognised = False
        if recognised:
            patterns.append((alias, frozenset(labels), index))
            index = close + 1
        else:
            index += 1
    return patterns


def _read_labels(statement: str, cursor: int, close: int) -> set[str]:
    """Read ``Label`` / ``Label:Other`` starting at ``cursor``, stopping at anything else.

    A rendered label (``:{expr}`` or ``:$(expr)``) yields NO label here and leaves the alias
    UNLABELLED — the fail-closed reading, since an unlabelled binding is refused by the door. The
    interpolation shapes are also refused on their own by :func:`statement_interpolates_label`.
    """
    labels: set[str] = set()
    while cursor < close:
        while cursor < close and statement[cursor].isspace():
            cursor += 1
        if cursor >= close:
            break
        if statement[cursor] in "{$":       # a rendered label — unreadable, so no label recorded
            break
        identifier = _identifier_at(statement, cursor)
        if identifier is None:
            break
        label, cursor = identifier
        labels.add(label)
        while cursor < close and statement[cursor].isspace():
            cursor += 1
        if cursor < close and statement[cursor] == ":":
            cursor += 1
            continue
        break
    return labels


def statement_pattern_labels(statement: str) -> dict[str, frozenset[str]]:
    """Every ALIAS a node pattern binds, to the labels that statement binds it to.

    ⛔ ``frozenset()`` — an alias bound with **no label** — is the load-bearing value: it is the
    absence the door refuses, because a node carrying no label is reachable by no ``entity_type``
    at all (`#5090` DoD row 2's own defect).

    An alias bound by several patterns maps to the UNION of their labels (a repeated alias in one
    ``MATCH`` denotes the same node, so the union is what the node is known to carry). Anonymous
    patterns (``(:Label)``, ``()``, ``({…})``) bind no alias and are not reported — there is nothing
    for a following clause to write to.

    ⚠️ This is the BINDING reader, not the write reader: it does not say which of those aliases the
    statement writes. Pair it with :func:`statement_write_aliases`.
    """
    text = _blank_comments(statement)
    depths = _depths(text)
    bindings: dict[str, frozenset[str]] = {}
    for alias, labels, _start in _scan_node_patterns(text, depths):
        if alias is None:
            continue
        bindings[alias] = bindings.get(alias, frozenset()) | labels
    return bindings


def statement_write_aliases(statement: str) -> frozenset[str]:
    """Every alias the statement WRITES TO — the aliases a stored property can land on.

    * the head of every ``SET`` clause item (``SET a += …``, ``SET a = …``, ``SET a.prop = …``,
      ``SET a:Label``), and
    * every alias a ``CREATE`` / ``MERGE`` node pattern introduces (the created/merged node).

    ⛔ ``REMOVE a.prop`` is deliberately NOT here: removing the embedding property writes a
    property, not a node, and :func:`clear_then_set_prefix`'s own ``REMOVE e.embedding`` guard must
    stay accepted — the `#4520` clear-then-set invariant every overwrite writer depends on.
    ``REMOVE a:Label`` is a LABEL mutation and is reported separately by
    :func:`statement_label_mutations`.

    Scanned at ANY depth, so a store nested in ``FOREACH`` / ``CALL { … }`` contributes its alias
    instead of hiding behind a well-behaved outer clause.
    """
    text = _blank_comments(statement)
    depths = _depths(text)
    aliases: set[str] = set()
    for keyword, start, end in _clause_bodies(text, depths):
        base = depths[start] if start < len(depths) else 0
        if keyword in ("CREATE", "MERGE"):
            for alias, _labels, _pattern_start in _scan_node_patterns(
                    text, depths, start, end):
                if alias is not None:
                    aliases.add(alias)
        elif keyword == "SET":
            for item in _split_items(text, start, end, base, depths):
                head = _item_head(item)
                if head is not None:
                    aliases.add(head[0])
    return frozenset(aliases)


def statement_label_mutations(statement: str) -> frozenset[str]:
    """Aliases whose LABEL the statement mutates — ``SET a:Label`` / ``REMOVE a:Label``.

    A label mutation decides what the node IS at the moment the vector lands, so it is refused
    rather than reasoned about: ``REMOVE e:Event SET e:Foo`` moves a served store onto ``:Foo``
    AFTER the pattern reader has seen ``(e:Event …)``, which is why naming a served label cannot be
    the bound. ``REMOVE a.prop`` / ``REMOVE a.embedding`` is a PROPERTY removal and is NOT a
    mutation — it is how :func:`clear_then_set_prefix` clears a vector before rewriting it.
    """
    text = _blank_comments(statement)
    depths = _depths(text)
    aliases: set[str] = set()
    for keyword, start, end in _clause_bodies(text, depths):
        if keyword not in ("SET", "REMOVE"):
            continue
        base = depths[start] if start < len(depths) else 0
        for item in _split_items(text, start, end, base, depths):
            head = _item_head(item)
            if head is not None and head[1].startswith(":"):
                aliases.add(head[0])
    return frozenset(aliases)


def statement_unattributed_set_items(statement: str) -> tuple[str, ...]:
    """``SET`` items whose WRITE HEAD this reader cannot attribute to an alias.

    A ``SET`` item's head is a plain variable in every shape this product uses, but Cypher also
    permits a PropertyExpression whose atom is a PARENTHESISED expression — ``SET (CASE WHEN $c THEN
    p ELSE q END).embedding = $embedding``. Such an item names no alias, so a reader that skipped it
    silently would prove a target from the OTHER items (say a labelled ``CREATE (p:Event …)``) while
    the vector actually landed on ``q`` — the same false PASS as an invisible alias, one grammar
    level up. Refusing an item that cannot be attributed is the fail-CLOSED reading, and it costs
    nothing: no statement in this repo writes through an expression head.
    """
    text = _blank_comments(statement)
    depths = _depths(text)
    items: list[str] = []
    for keyword, start, end in _clause_bodies(text, depths):
        if keyword != "SET":
            continue
        base = depths[start] if start < len(depths) else 0
        for item in _split_items(text, start, end, base, depths):
            if _item_head(item) is None:
                items.append(item)
    return tuple(items)


#: A ``WITH``/``RETURN`` projection item that RE-BINDS an alias by name: ``… AS e``.
#: ``WITH b AS e`` re-points ``e`` at a different node, so every pattern-derived binding for ``e``
#: stops describing what the later ``SET e.…`` touches. Reads both identifier spellings.
_REBIND_RE = re.compile(
    rf"\bAS\s+({_IDENT_FRAGMENT})", re.IGNORECASE)


def statement_rebound_aliases(statement: str) -> frozenset[str]:
    """Aliases the statement RE-BINDS with ``AS`` — ``WITH b AS e``.

    ⛔ This closes a fail-OPEN that lived INSIDE the grammar, so it is not part of the text reader's
    accepted residual (measured at `66da0c05`): a statement that matches ``(e:Event …)``, re-points
    ``e`` at an UNLABELLED node via ``WITH b AS e``, and then stores the vector through ``e``.
    It passes all three legs — ``e`` IS bound to ``Event`` by a pattern, and the store names ``e`` —
    while the vector lands on the label-less node, i.e. exactly the `#5090` row-2 defect leg 3 was
    added to refuse.

    ⚠️ The statement is DESCRIBED, not spelled: `tortoise.embed_writer_register` finds a store by
    scanning a writer's own source text, docstrings included (see :func:`statement_node_labels`), so
    a literal store clause here makes this reader look like a writer. The bindings are unioned per alias NAME across the whole text, so a later
    ``AS e`` re-point has to be read as "the binding this reader proved no longer describes the
    write". Refused rather than reasoned about: proving which of two bindings is live at the store
    needs real scope analysis, and no statement in this product does it.

    A plain ``WITH e, e.eventId AS note`` is NOT a rebind of ``e`` (the ``AS`` names ``note``), so
    only an ``AS`` whose TARGET is an alias the patterns also bind is a rebind — see
    :func:`statement_write_target_problem`, which joins the two.

    ⚠️ DEFENCE IN DEPTH, and after :func:`statement_uses_unmodelled_scope` it is mostly
    UNREACHABLE: a `WITH` is refused at the door before this reader runs, so the shape this reader
    was built for is already gone. It is kept because it is still CORRECT over what remains (an
    `AS` in a clause the grammar does not refuse, e.g. `RETURN x AS e`), and because deleting a
    proven-closed fail-open class so that a future widening of the accepted grammar re-opens it
    silently is the wrong direction to tidy.
    """
    text = _blank_comments(statement)
    text = _blank_string_literals(text)
    return frozenset(alias.strip("`")
                     for alias in _REBIND_RE.findall(text))


#: The constructs that MOVE Cypher's variable scope, or replace the scope a statement starts in.
#: A scope-respecting reader would have to be a PARSER, so these are refused instead of guessed —
#: see :func:`statement_uses_unmodelled_scope` for why the name-keyed binding map cannot survive them.
#:
#: ⛔ `OPTIONAL MATCH` is here for the SAME reason even though it opens no new scope: it makes a
#: binding OPTIONAL, so the alias may be NULL at the `SET` while the pattern still reads as labelled —
#: the same "the pattern is not the node" gap, reached without a `WITH`.
_SCOPE_CONSTRUCT_RE = re.compile(
    r"(?<![\w$.:])(OPTIONAL\s+MATCH|LOAD\s+CSV|IN\s+TRANSACTIONS|WITH|UNION|CALL)(?![\w$:])",
    re.IGNORECASE,
)

#: What may follow a scope keyword for it to still be a VARIABLE rather than a clause. `MATCH (with)`,
#: `SET with.x = 1` and `MATCH (with), (x)` are patterns and property writes, not clauses — a token
#: matcher that refused those would block a statement the engine accepts for no gain.
_SCOPE_TOKEN_TERMINATORS = ")]},;.:"


def statement_uses_unmodelled_scope(statement: str) -> str | None:
    """The scope construct ``statement`` uses that this module cannot read, or ``None``.

    ⛔ WHY A REFUSAL AND NOT A SCOPE MODEL. :func:`statement_pattern_labels` maps an ALIAS to the
    labels the statement binds — keyed by NAME, over the whole statement, with no scope boundary.
    Cypher's ``WITH`` ends exactly that: a variable the clause does not carry *"is dropped from the
    scope of the query and cannot be referenced by subsequent clauses"* (Neo4j Cypher Manual,
    ``WITH`` → *Control variables in scope*), so a later ``MATCH (p)`` binds a **NEW, unlabelled**
    ``p`` while the name-keyed map still reads it as the served label the FIRST pattern gave it.
    Measured on this PR (#11641):

        MATCH (p:Event {eventId:'seed'}) WITH 1 AS x MATCH (p) SET p += $props, …embedding

    reads ``{p: {'Event'}}``, is ACCEPTED, and stores the vector on **every node the second
    ``MATCH`` reaches** — ``:Meta`` in the measurement, a label no ``entity_type`` reaches. (The
    control ``WITH p AS z MATCH (z)`` is refused only because the alias CHANGES; the hole needs
    the same NAME re-bound across the boundary.)

    Modelling that correctly means modelling scope — pattern expressions, subqueries, ``CALL``
    imports, ``UNION`` branches — which is a parser, and an under-modelled parser fails OPEN. So the
    bound is a DECLARED GRAMMAR instead: ``MATCH`` / ``MERGE`` / ``CREATE`` / ``SET`` / ``REMOVE`` /
    ``FOREACH``, which is every clause the product's own door statements use. A statement outside it
    is refused, loudly, rather than trusted by a reader that cannot prove it.

    Returns the offending construct (``"WITH"``, ``"UNION"``, ``"CALL"``, ``"CALL { … }``,
    ``"OPTIONAL MATCH"``, ``"LOAD CSV"``, ``"IN TRANSACTIONS"``) — the FIRST in statement order —
    or ``None``.

    ⛔ WHAT ``None`` MEANS, EXACTLY — this is the list of constructs REFUSED, not an allow-list of
    clauses, and the difference is deliberate rather than sloppy. A clause that binds only NEW names
    (``UNWIND … AS x``) or only READS (``WHERE`` / ``RETURN`` / ``ORDER BY`` / ``SKIP`` /
    ``LIMIT``) is not refused, because it cannot re-bind a name that is already bound — and a
    same-name re-binding across a boundary is the ONE shape that defeats a name-keyed map. Leaving
    them is safe: what they introduce is either harmless (measured — ``MATCH (e:Event {…}) UNWIND
    [1] AS x SET e += $props, …`` reads ``None`` and stores on ``e``) or caught by the write-target
    leg (measured — ``UNWIND $rows AS p SET …embedding`` writes to an alias NO node pattern binds,
    so :func:`statement_write_target_problem` refuses it). The list itself stays a DECLARATION
    rather than a proof of exhaustiveness; that residual is stated in
    :func:`statement_write_target_problem`.

    ⛔ ANTI-VACUITY, and it is the reason this reuses :func:`_blank_comments` + :func:`_depths`
    rather than a bare ``"WITH" in statement``: a construct inside a STRING LITERAL
    (``SET e.note = 'WITH 1 AS x'``), inside a backticked identifier, or inside a comment
    (``/* WITH */``) is not syntax, and a guard that refused it would block statements the engine
    accepts while proving nothing.
    """
    text = _blank_comments(statement)
    depths = _depths(text)
    for match in _SCOPE_CONSTRUCT_RE.finditer(text):
        start = match.start()
        if depths[start] < 0:      # inside a string literal or a backticked identifier — TEXT
            continue
        construct = " ".join(match.group(1).upper().split())
        previous = start - 1
        while previous >= 0 and text[previous].isspace():
            previous -= 1
        # `IN TRANSACTIONS` is only a clause after a subquery closes (`CALL { … } IN TRANSACTIONS`);
        # without the brace it is a variable named `transactions` after `IN`.
        if construct == "IN TRANSACTIONS" and not (
                previous >= 0 and text[previous] == "}"):
            continue
        following = match.end()
        while following < len(text) and text[following].isspace():
            following += 1
        if following < len(text) and text[following] in _SCOPE_TOKEN_TERMINATORS:
            continue               # a VARIABLE in a pattern / property write, not a clause
        if construct == "CALL" and following < len(text) and text[following] == "{":
            return "CALL { … } (a CALL subquery)"
        return construct
    return None


def statement_write_target_problem(statement: str, allowed) -> str | None:
    """Report what is wrong with the WRITE TARGET of ``statement``, or ``None`` when nothing is.

    The one helper a door calls. It refuses — with a sentence a caller can act on — every alias the
    statement writes to that is

    * **ABSENT** — never bound by a node pattern in this statement;
    * **UNLABELLED** — bound, but to no label, so no ``entity_type`` reaches the node; or
    * **OUTSIDE** — bound to a label in ``allowed``'s complement;

    and it refuses a statement that MUTATES a label (``SET a:Label`` / ``REMOVE a:Label``). A
    statement that exposes NO write target the reader can prove is refused too — the fail-closed
    reading, because "I could not find where it writes" is not "it writes somewhere safe".

    ⛔ WHAT IT IS COMPLETE OVER (so the residual below can be sized): both identifier SPELLINGS —
    bare ``e`` and backtick-quoted `` `e` `` (:func:`_identifier_at`) — at every position it reads;
    Cypher COMMENTS (``// …``, ``/* … */``) are blanked before any reader sees them, so a ``)`` or a
    clause hidden in a comment cannot shift the depth out from under the scan; and an alias
    RE-BOUND by ``AS`` (:func:`statement_rebound_aliases`) is refused, because this reader unions
    bindings per alias NAME, so ``WITH b AS e`` would leave a proved label describing a different
    node. Each of those three was a live fail-open on this guard before #11641's review rounds, and
    every one was reached WITHOUT leaving the grammar.

    ⛔ WHAT IT IS NOT COMPLETE OVER, and why a DOOR must pair it with
    :func:`statement_uses_unmodelled_scope`: this reader keys bindings by NAME across the whole
    statement, so a `WITH` that drops an alias and re-binds the same name makes the map describe a
    node the statement never touches. That is refused at the door (a DECLARED GRAMMAR — `MATCH` /
    `MERGE` / `CREATE` / `SET` / `REMOVE` / `FOREACH`), not modelled here: a scope model without a
    parser fails open, which is the direction a bound must never fail in.

    ⛔ ACCEPTED RESIDUAL — what a text reader cannot see, named here rather than left implied. Two
    shapes, at their true size after the scope refusal (the earlier revision named a mutating
    procedure here, which is now INSIDE the bound — every procedure is a `CALL`, and `CALL` is
    refused):

    * **the declared list is a declaration, not a proof of exhaustiveness.** Every construct in
      :data:`_SCOPE_CONSTRUCT_RE` is refused, and a test pins one refusal per entry; a scope
      construct the list does not name is NOT refused. Widening the list is a review duty, so treat
      it as one — the same limit :func:`statement_node_labels` states for a label it cannot read.
    * **clause text a helper builds** is read as text like any other, so a scope construct inside it
      IS refused; what the reader still cannot see is a store the grammar does not phrase as
      `SET`/`REMOVE`/`CREATE`/`MERGE` at all. Inside the grammar it refuses what it cannot attribute
      (an unlabelled target, a label mutation, a `SET` item with a non-alias head).

    It is also why the door's own docstring says the bound is on the statement TEXT: a caller that
    never reaches this door writes outside every reader here, and that is the chokepoint's question
    (#9079), not this one.
    """
    allowed_labels = {str(label) for label in allowed}
    bindings = statement_pattern_labels(statement)
    targets = statement_write_aliases(statement)
    mutations = statement_label_mutations(statement)
    unattributed = statement_unattributed_set_items(statement)
    rebound = statement_rebound_aliases(statement)

    problems: list[str] = []
    shadowed = sorted(alias for alias in targets
                      if alias in rebound and alias in bindings)
    if shadowed:
        problems.append(
            f"it REBINDS alias(es) {shadowed} with `AS` (`WITH <other> AS {shadowed[0]}`), so the "
            "label its pattern proved no longer describes what the store touches — this reader "
            "unions bindings per alias NAME, so a rebind makes the proved label describe a "
            "different node")
    if mutations:
        problems.append(
            f"it MUTATES a label on alias(es) {sorted(mutations)} (`SET <a>:X` / `REMOVE <a>:X`), "
            "so the label at the moment the vector lands is decided by the statement's own "
            "effects rather than by any pattern")
    if not targets:
        problems.append(
            "it exposes NO write target this reader can prove (no `SET <alias> …` item and no "
            "node pattern introduced by `CREATE`/`MERGE`)")
    if unattributed:
        problems.append(
            f"it has a `SET` item whose write HEAD is not a plain alias {list(unattributed)}, so "
            "the node it writes cannot be proved — name the alias in the `SET` item instead of "
            "writing through an expression")
    unbound = sorted(alias for alias in targets if alias not in bindings)
    unlabelled = sorted(alias for alias in targets
                        if alias in bindings and not bindings[alias])
    outside = {alias: sorted(bindings[alias] - allowed_labels) for alias in sorted(targets)
               if alias in bindings and (bindings[alias] - allowed_labels)}
    if unbound:
        problems.append(
            f"it writes to alias(es) {unbound} that NO node pattern in the statement binds, so the "
            "label the vector lands on is not in the statement at all")
    if unlabelled:
        problems.append(
            f"it writes to alias(es) {unlabelled} bound by a node pattern with NO LABEL, so the "
            "vector lands on a node no `entity_type` reaches")
    if outside:
        rendered = ", ".join(f"{alias} bound to {labels}" for alias, labels in outside.items())
        problems.append(
            f"it writes to an alias bound to a label outside the allowed set ({rendered}; allowed "
            f"{sorted(allowed_labels)})")
    if not problems:
        return None
    return "; ".join(problems)


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
