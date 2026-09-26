"""G5's EXISTING half — the pre-existing bare-definite-description Objects (#5059).

**What this module is.** A **pure, report-only planner** over a read-only census
of the ``:Object`` layer. For every row it records a *disposition* with a rule
id and a recoverable counterfactual — the ``#4899`` safeguard pattern (a flag,
a recorded rule id, a recoverable counterfactual) applied to rows that are
already in the store rather than to candidates about to be written.

**Why it is a PLANNER and not a migration.** ``EXTRACTOR-V4-ARCHITECTURE.md``
§4.3 ``G5`` records the gap as *"no migration or backfill for the ~25k existing
nodes … no decision on the ~4,900 ``the <X>`` Objects"*. The decision the issue
asks for is **not** available by name pattern, and this lane MEASURED that at
population scale (live ``org_3326a01ea34ae595d84de5d8f9``, 2026-09-25):

* ``7,870`` Objects, ``27,324`` reference edges.
* Objects matching a drop-shaped pattern (``the `` prefix / contains ``/`` /
  contains ``#`` / contains ``.py`` / contains ``_``): **6,533 — 83% of ALL
  Objects.** Of those **3,517 are load-bearing (≥2 referencing points)**, 2,614
  single-use, 402 unused.
* ``the `` alone: **4,901, of which 2,570 are load-bearing.** That is ≈48%
  precision on an irreversible delete.
* **11 of the graph's 12 most-referenced Objects match a drop pattern**, and the
  #1 is a file path (``config/ci-surfaces.yml``, 123 references).
* **Reference count does NOT separate keepers from junk.** Six rows a sample
  named must-keep (``guard 2``, ``_TOOL_BY_NAME``, ``NON_SDK_READ_OPERATIONS``,
  ``prune_backups``, ``daniel-ospina/agent-infra``, ``_capture_cost_props``) are
  **all refs = 1**, inside a 2,614-row single-use class that also holds obvious
  noise (``the T2 guard``, ``the T3 rule``).
* **The structural falsification:** a mint-time rule fires when the name cannot
  yet resolve, so "resolve-or-drop" is a drop for every first mention — and
  **every one of those 2,570 hubs was a first mention once.** The hubs exist
  *because* the rule was absent.

Both comparables say the same thing and neither is ours to overturn: **mem0 v3
is ADD-only** (it walked *back* from a reconciliation pass; memories accumulate
rather than being consolidated away), and **Hindsight** states it plainly —
*"A retain path that dropped a fact because it resembled one already stored
would be a retain path you could not trust to have kept what you sent it."*
Hindsight invalidates by **relocation** (archive the superseded row), never by
deletion; ``EXTRACTOR-V4-ARCHITECTURE.md`` §2.4.2 records that as adoptable. And
the pattern form is already rejected in writing in this repo — see
``tortoise/value_gate``'s docstring: *"a definite description is left to the
arbiter, and is reported, not dropped."*

**⇒ The backfill is a RECLASSIFY / MERGE / RELOCATE problem, not a delete
problem.** This module therefore has **no destructive disposition at all**
(:data:`DESTRUCTIVE_DISPOSITIONS` is empty by construction, and every record
carries ``destructive=False``). There is no flag value, no row shape and no
resolver answer that removes or overwrites anything.

The safety ladder
-----------------
``EXTRACTOR-V4-ARCHITECTURE.md`` §16.4 (owner ruling ``O4``) admits
``KEEP`` / ``NOOP`` / ``DISCARD`` / ``MERGE`` for the *write* path. A backfill
runs after the rows exist, so it has one rung the write path cannot have — a
**lookup** (§4.2: *"Against-priors consolidation … needs the S3 lookup"*; S3 is
*"the single authority on whether a candidate is new or an existing node"*).
The rungs are tried **most-preserving first**, and a lower rung is never taken
when a higher one applies:

1. **RESOLVE** — identify what the description denotes, keep the row, record the
   referent. Zero loss; it *adds* information.
2. **RECLASSIFY** — the row is positively a definite description (a reference,
   not an entity). **Additive**: it labels the row and overwrites **no** field.
3. **MERGE_CANDIDATE** — the row is a spelling-duplicate of another row. Only
   *recorded*; the merge machinery is ``#5006``'s (union-of-attachments, and the
   never-across-a-difference bar).
4. **Destructive** — removal/relocation. **OUT OF SCOPE.** Not implemented here;
   it is the owner's ruling to make (see the Context · Options · Analysis ·
   Recommendation recorded on ``#5059``). ``STORAGE-ARCHITECTURE.md`` §2/§191
   names the mechanism *if* it is ever authorised: **relocation** via
   ``Object.status``, journaled, never a row delete.

The two structural invariants
-----------------------------
**I1 — a referenced Object is never a candidate for anything.**
:data:`RULE_LOAD_BEARING` fires on ``ref_count >= 1`` and sits **before every
rule that can emit a non-``KEEP`` disposition**, and the first matching rule
wins. So ``ref_count >= 1 ⇒ KEEP``, by dispatch order alone. This is strictly
stronger than the requested "cannot delete a ≥2-reference Object": it also
protects the 2,614 single-use refs=1 rows, which is where the must-keep sample
actually lives.

**I2 — an unreadable or unclassifiable row is never actionable.** ``ref_count``
that is absent or unparseable is **not** read as zero (absence of a
measurement is not a measurement of zero) — it is :data:`RULE_REF_COUNT_UNREADABLE`
and a ``REPORT``. Anything the positive classifier does not recognise falls to
:data:`RULE_UNCLASSIFIED` and a ``REPORT``, never to a lower rung.

Bounded, and idempotent
-----------------------
*Bounded*: one pass over the census, one disposition per row, no loop and no
recursion; the census read is a single frozen read-only query with an optional
completeness cap that is **reported** when it bites rather than silently
truncating.

*Idempotent*: :func:`plan_backfill` is a **pure function** of
``(census, resolver)`` — no clock, no environment read beyond the flag, no
graph I/O — so two runs over the same census are byte-identical (pinned by
:func:`plan_fingerprint`). Every record carries ``from_state``/``to_state``, so
a future applier is a no-op when a row is already at ``to_state``; and a census
row that already carries ``backfill_applied`` is answered
:data:`RULE_ALREADY_APPLIED` → ``KEEP``, which is what makes a second run after
an application produce an empty actionable set.

Failure policy (§4.2): FAIL-OPEN
--------------------------------
Unknown ⇒ ``KEEP`` or ``REPORT``; never a lower rung, never a removal. A
resolver that raises is treated as *no answer*. The public entry point is
**total** on a malformed census (``None``, a bare dict, non-mapping rows,
non-string names, missing ids) and yields dispositions rather than an exception.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from enum import StrEnum
from typing import Any

from .env_truthy import is_truthy

# ── The flag (#4899 safeguard 1) ────────────────────────────────────────────

#: The call-time toggle. Unset/0 ⇒ the planner does not run and returns an empty
#: plan with a zeroed ``enabled`` stats key, so the report's shape and count are
#: measurable before the plan is trusted — the ``TORTOISE_VALUE_GATE`` (#4899),
#: ``TORTOISE_VET`` (#5005) and ``TORTOISE_CLASSIFY_LATER`` (#1695) precedent.
#:
#: ⚠️ **The flag can never enable a destructive action** — there is none to
#: enable. It only decides whether the ledger is produced.
ENV_FLAG = "TORTOISE_DD_BACKFILL"


def backfill_enabled() -> bool:
    """Whether the planner runs. Off unless the flag says otherwise."""
    return is_truthy(os.environ.get(ENV_FLAG))


# ── The disposition vocabulary ──────────────────────────────────────────────


class Disposition(StrEnum):
    """The four rungs plus the two inert outcomes, as a closed enum.

    Deliberately an enum rather than a bare ``str``: a caller cannot pass an
    out-of-vocabulary outcome, which is the same structural argument
    ``EXTRACTOR-V4-ARCHITECTURE.md`` §4.2 makes for a decision-only model. The
    module's constants and its :data:`DISPOSITIONS` set are both derived from
    this member list, so an outcome cannot be declared in one place and not the
    others.
    """

    KEEP = "KEEP"
    RESOLVE = "RESOLVE"
    RECLASSIFY = "RECLASSIFY"
    MERGE_CANDIDATE = "MERGE_CANDIDATE"
    REPORT = "REPORT"


KEEP = Disposition.KEEP.value
RESOLVE = Disposition.RESOLVE.value
RECLASSIFY = Disposition.RECLASSIFY.value
MERGE_CANDIDATE = Disposition.MERGE_CANDIDATE.value
REPORT = Disposition.REPORT.value

#: Every disposition this planner can emit — the single source, derived from
#: :class:`Disposition` so the enum and the set cannot drift apart.
DISPOSITIONS: frozenset[str] = frozenset(d.value for d in Disposition)

#: ⛔ **EMPTY BY DESIGN, AND IT MUST STAY EMPTY.** The v4 write-path vocabulary
#: admits ``DISCARD`` (§16.4 O4); a *backfill* does not, because dropping a row
#: that already exists is memory loss with no write path to re-derive it from —
#: mem0 and Hindsight both refuse it (§1/§191), and this lane measured the
#: precision at ≈48% on the ``the `` class. A test asserts this set is empty and
#: that it is disjoint from :data:`DISPOSITIONS`, so adding a destructive
#: rung is a deliberate, reviewable act rather than a quiet addition.
DESTRUCTIVE_DISPOSITIONS: frozenset[str] = frozenset()

#: Dispositions that change nothing at all.
INERT_DISPOSITIONS: frozenset[str] = frozenset({KEEP, REPORT})

# ── The rule vocabulary (each id is recorded on every disposition —
#    #4899 safeguard 2: nothing happens silently) ──────────────────────────

RULE_NOT_DESCRIPTION = "dd.not_description"
RULE_LOAD_BEARING = "dd.load_bearing"
RULE_ALREADY_APPLIED = "dd.idempotent.already_applied"
RULE_REF_COUNT_UNREADABLE = "dd.ref_count.unreadable"
RULE_RESOLVE_ARBITER = "dd.resolve.arbiter"
RULE_RESOLVE_ARTICLE = "dd.resolve.stripped_article"
RULE_RECLASSIFY = "dd.reclassify.bare_definite"
RULE_MERGE = "dd.merge.normalized_duplicate"
RULE_UNCLASSIFIED = "dd.unclassified.report"

#: The evaluation order — **the first match wins**, and the order IS the safety
#: property. :data:`RULE_LOAD_BEARING` must precede every rule that can emit a
#: non-``KEEP`` disposition (I1); :data:`RULE_REF_COUNT_UNREADABLE` and
#: :data:`RULE_UNCLASSIFIED` are the fail-closed floors (I2).
RULES: tuple[str, ...] = (
    RULE_NOT_DESCRIPTION,
    RULE_LOAD_BEARING,
    RULE_ALREADY_APPLIED,
    RULE_REF_COUNT_UNREADABLE,
    RULE_RESOLVE_ARBITER,
    RULE_RESOLVE_ARTICLE,
    RULE_RECLASSIFY,
    RULE_MERGE,
    RULE_UNCLASSIFIED,
)

#: The disposition each rule emits. Pinned by
#: ``tests/test_definite_description_backfill_5059.py`` so a rule cannot be
#: re-pointed at a different disposition without the plan's semantics changing
#: visibly in review.
RULE_DISPOSITION: dict[str, str] = {
    RULE_NOT_DESCRIPTION: KEEP,
    RULE_LOAD_BEARING: KEEP,
    RULE_ALREADY_APPLIED: KEEP,
    RULE_REF_COUNT_UNREADABLE: REPORT,
    RULE_RESOLVE_ARBITER: RESOLVE,
    RULE_RESOLVE_ARTICLE: RESOLVE,
    RULE_RECLASSIFY: RECLASSIFY,
    RULE_MERGE: MERGE_CANDIDATE,
    RULE_UNCLASSIFIED: REPORT,
}

#: Where each rule's authority is declared. The source stays the source — the
#: design document or the landed decision — and the planner only implements it.
#: ``declares`` must appear verbatim on the cited line (``anchor`` additionally
#: requires the string to sit on the *same line*), so a rule that stops being
#: authorised stops being justified and the drift pin fails rather than the
#: drift going silent. This is ``#4899``'s ``DECLARED_CLASS`` pattern.
DECLARED_BY: dict[str, dict[str, str]] = {
    RULE_NOT_DESCRIPTION: {
        "source": "docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md",
        "declares": "62.3% of Objects are",
        "anchor": "definite descriptions",
        "label": "out of scope — not a definite description",
    },
    RULE_LOAD_BEARING: {
        "source": "docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md",
        "declares": "a wrong keep is noise; a wrong drop is memory loss",
        "label": "a referent — load-bearing",
    },
    RULE_ALREADY_APPLIED: {
        "source": "tortoise/migrate_kinds.py",
        "declares": "Idempotent — safe to run multiple times",
        "label": "already dispositioned — no-op",
    },
    RULE_REF_COUNT_UNREADABLE: {
        "source": "docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md",
        "declares": "a wrong keep is noise; a wrong drop is memory loss",
        "label": "reference count unreadable — cannot prove it is not a referent",
    },
    RULE_RESOLVE_ARBITER: {
        "source": "docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md",
        "declares": "the single authority on whether a candidate is new or an existing",
        "label": "resolved by the lookup",
    },
    RULE_RESOLVE_ARTICLE: {
        "source": "docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md",
        "declares": "find what exists",
        "label": "resolved by article-stripped name",
    },
    RULE_RECLASSIFY: {
        "source": "docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md",
        "declares": "is this actually an entity, or a reference",
        "label": "a definite description — reclassify (additive)",
    },
    RULE_MERGE: {
        "source": "docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md",
        "declares": "The admissible outcomes are `KEEP` / `NOOP` / `DISCARD` / `MERGE`",
        "label": "spelling-duplicate — merge candidate (recorded only)",
    },
    RULE_UNCLASSIFIED: {
        "source": "tortoise/value_gate.py",
        "declares": "is left to the arbiter, and is reported, not dropped",
        "label": "unclassified — reported, not dropped",
    },
}


# ── The definite-description shape ──────────────────────────────────────────

#: ``the <body>`` — the article must be followed by whitespace and a non-empty
#: body, so a bare ``the`` is not a description and ``there``/``theme`` are not
#: read as article + body.
_ARTICLE = re.compile(r"^the[ \t]+(?P<body>\S(?:.*\S)?)$", re.IGNORECASE)

#: A common-noun token: lowercase letters, hyphens and apostrophes only. The
#: positive classifier requires **every** body token to be one of these, which
#: is what makes "positively classified" a real gate rather than a keyword test.
_COMMON_NOUN = re.compile(r"^[a-z][a-z'’\-]*$")

#: Identifier / proper-noun evidence. Any single hit means the row is NOT
#: positively classifiable and therefore cannot reach a non-``KEEP`` rung on the
#: strength of the classifier alone.
_IDENTIFIER_CHARS = frozenset("#/\\_@:|=+*()[]{}<>\"'")
_EXTENSION = re.compile(r"\.[A-Za-z]{2,}")

_DISPOSITION_ORDER = (KEEP, RESOLVE, RECLASSIFY, MERGE_CANDIDATE, REPORT)


def normalize_name(name: object) -> str:
    """Case- and whitespace-insensitive form, for identity comparison only.

    This is a *comparison* key, never a rewrite: nothing here is written back to
    a row.
    """
    return " ".join(str(name or "").split()).casefold()


def is_bare_definite_description(name: object) -> bool:
    """Whether ``name`` has the ``the <body>`` SHAPE.

    ⚠️ **Shape is not a verdict.** ``the owner`` and ``the admin-merge rail``
    (70 references) both match, and both may be real entities. The shape only
    decides whether the row is *in scope* for this planner; the rung is chosen
    by :func:`plan_backfill`'s ordered rules, and I1 removes every referenced row
    before the shape is ever consulted for a non-``KEEP`` outcome.
    """
    raw = str(name or "").strip()
    if not raw:
        return False
    return _ARTICLE.match(raw) is not None


def _article_body(name: object) -> str:
    """The text after ``the ``, or ``""`` when there is no article."""
    m = _ARTICLE.match(str(name or "").strip())
    return m.group("body") if m else ""


def positive_evidence(name: object) -> str | None:
    """Why ``name`` is NOT positively a bare definite description, else ``None``.

    The rule is deliberately conservative: a body token is common-noun-shaped iff
    it is ``^[a-z][a-z'’\\-]*$`` after trimming sentence punctuation. Any digit,
    any identifier character, a dotted extension, or **any capital letter** is
    evidence that the name may be a proper noun or an identifier, and the row
    then falls to :data:`RULE_UNCLASSIFIED` (**``REPORT``**) rather than to a
    rung. Fail-closed: the cost is an unclassified ledger row the owner can rule
    on, not a mislabelled memory.
    """
    body = _article_body(name)
    if not body:
        return "no definite-article body"
    for token in body.split():
        bare = token.strip(".,;:!?")
        if not bare:
            return f"empty token in {token!r}"
        if any(ch.isdigit() for ch in bare):
            return f"digit in {bare!r}"
        if any(ch in _IDENTIFIER_CHARS for ch in bare):
            return f"identifier character in {bare!r}"
        if _EXTENSION.search(bare):
            return f"dotted extension in {bare!r}"
        if any(ch.isupper() for ch in bare):
            return f"capital letter in {bare!r}"
        if not _COMMON_NOUN.match(bare):
            return f"non-common-noun token {bare!r}"
    return None


def is_positively_bare_definite_description(name: object) -> bool:
    """Whether ``name`` is a definite description past the conservative gate."""
    return is_bare_definite_description(name) and positive_evidence(name) is None


def definite_article_stripped(name: object) -> str | None:
    """The body of ``the <body>`` in its normalized form, else ``None``."""
    body = _article_body(name)
    return normalize_name(body) if body else None


# ── The read-only census ────────────────────────────────────────────────────

#: ⛔ **READ-ONLY. No write verb appears, and none may be added.**
#: :func:`assert_read_only` is applied to this string at every call site, and a
#: test asserts the guard rejects each mutation verb — so the claim "the census
#: cannot mutate production" is executed, not asserted in prose.
#:
#: Inbound edges are counted **unlabelled**, deliberately: an Object is a
#: referent if *anything* points at it — ``aboutObject`` from Point/Event/Session
#: (``ONTOLOGY.md`` §3.2), ``references`` from Source (§3.4), ``produces`` and
#: ``uses`` from Event, ``wasDerivedFrom`` from Object, ``TAGGED`` from Point
#: (§3.5). Naming a subset would under-count and let a hub through the guard;
#: over-counting only ever *keeps* more, which is the safe direction.
#:
#: ``o.status`` and ``o.objectKind`` are read so a future relocation rung has the
#: field it would need, and so the ledger can show what a reclassification would
#: leave untouched.
CENSUS_QUERY = """
MATCH (o:Object)
OPTIONAL MATCH (o)<-[r]-()
WITH o, count(r) AS ref_count, collect(DISTINCT type(r)) AS ref_edges
RETURN o.id            AS id,
       o.name          AS name,
       ref_count       AS ref_count,
       ref_edges       AS ref_edges,
       o.status        AS status,
       o.objectKind    AS object_kind
ORDER BY ref_count DESC, id
"""

#: Cypher verbs that can mutate. ``MERGE`` is included even though the census
#: query does not use it: the guard exists so a *future* edit to
#: :data:`CENSUS_QUERY` cannot smuggle one in.
_WRITE_VERB = re.compile(
    r"\b(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|FOREACH)\b", re.IGNORECASE
)


class ReadOnlyViolation(ValueError):
    """A query offered to the census contains a mutating verb."""


def assert_read_only(query: str) -> None:
    """Raise :class:`ReadOnlyViolation` if ``query`` contains a mutating verb."""
    m = _WRITE_VERB.search(str(query or ""))
    if m:
        raise ReadOnlyViolation(
            f"census query contains the mutating verb {m.group(0)!r}; the "
            "backfill census is read-only by contract (#5059)"
        )


def census_rows(
    run: Callable[[str, Mapping[str, Any]], Sequence[Mapping[str, Any]]],
    *,
    limit: int | None = None,
) -> tuple[list[dict], list[str]]:
    """Read the Object census with a caller-supplied **read-only** executor.

    ``run`` is injected — this module performs no I/O of its own, which is what
    keeps :func:`plan_backfill` pure and the tests hermetic. Returns
    ``(rows, warnings)``.

    ⚠️ **A cap that bites is REPORTED, never silent.** When ``limit`` is given
    and the result comes back at exactly that size, the census may be truncated
    and the warning says so; a truncated census must never be read as a complete
    one (the same rule the dispatch pre-flight applies to a partial PR list).
    """
    assert_read_only(CENSUS_QUERY)
    params: dict[str, Any] = {}
    query = CENSUS_QUERY
    warnings: list[str] = []
    if limit is not None:
        params["limit"] = int(limit)
        query = query if query.rstrip().endswith("LIMIT $limit") else (
            query.rstrip() + "\nLIMIT $limit"
        )
    raw = run(query, params)
    rows = [dict(r) for r in (raw or []) if isinstance(r, Mapping)]
    if limit is not None and len(rows) >= int(limit):
        warnings.append(
            f"census returned {len(rows)} rows at the cap of {int(limit)} — the "
            "census may be TRUNCATED; the plan is not a complete enumeration"
        )
    return rows, warnings


# ── The plan ────────────────────────────────────────────────────────────────

def _as_int(value: object) -> int | None:
    """``value`` as a non-negative int, or ``None`` when it is not one.

    ``None`` is a real answer here and is NOT zero: absence of a measurement is
    not a measurement of zero, and treating it as zero would let an unmeasured
    row fall through to a rung (I2).
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 and float(value).is_integer() else None
    if isinstance(value, str) and value.strip():
        try:
            n = int(value.strip())
        except ValueError:
            return None
        return n if n >= 0 else None
    return None


def _row_id(row: Mapping[str, Any], index: int) -> str:
    """A stable identity for a census row.

    Falls back to the name, then to a positional key — but a positional key is
    *recorded as positional* so a reader can see the identity was synthesized
    rather than read.
    """
    for key in ("id", "object_id", "elementId"):
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    name = str(row.get("name") or "").strip()
    if name:
        return f"name:{name}"
    return f"row:{index}"


def _state(payload: Mapping[str, Any] | None) -> str | dict:
    return dict(payload) if payload else "unchanged"


def _record(
    *,
    row_id: str,
    name: str,
    rule_id: str,
    disposition: str,
    reason: str,
    counterfactual: str,
    from_state: Mapping[str, Any] | None = None,
    to_state: Mapping[str, Any] | None = None,
    preserves: Sequence[str] = (),
) -> dict:
    """Build one disposition record.

    Every record carries the ``#4899`` trio — the rule id, the reason, and a
    counterfactual stated so the change (or the decision not to make one) is
    readable without the original census — plus ``destructive: False`` as a
    literal field, so the no-removal property is visible on the artifact itself
    and not only in this module's control flow.
    """
    return {
        "id": row_id,
        "name": name,
        "rule_id": rule_id,
        "disposition": disposition,
        "reason": reason,
        "counterfactual": counterfactual,
        "from_state": _state(from_state),
        "to_state": _state(to_state),
        "preserves": list(preserves),
        "destructive": bool(disposition in DESTRUCTIVE_DISPOSITIONS),
        "disposition_key": _key(row_id, rule_id, to_state),
    }


def _key(row_id: str, rule_id: str, to_state: Mapping[str, Any] | None) -> str:
    """The stable identity of a disposition — row, rule, and target state.

    An applier is a no-op when a row is already at ``to_state``; this key is how
    that comparison is recorded on the artifact rather than recomputed.
    """
    return (f"{row_id}|{rule_id}|"
            f"{json.dumps(_state(to_state), sort_keys=True)}")


def _empty_plan(enabled: bool, warnings: Sequence[str] = ()) -> dict:
    """The flag-off / empty-census plan: no records, and a zeroed stats key."""
    return {
        "enabled": enabled,
        "records": {},
        "order": [],
        "stats": {
            "enabled": enabled,
            "rows": 0,
            "actionable": 0,
            "by_disposition": {d: 0 for d in _DISPOSITION_ORDER},
            "by_rule": {r: 0 for r in RULES},
            "destructive": 0,
            "fingerprint": "",
        },
        "warnings": list(warnings),
    }


def _classify_row(
    row: Mapping[str, Any],
    row_id: str,
    name: str,
    *,
    ref_count: int | None,
    by_norm: Mapping[str, list[tuple[str, str]]],
    names: set[str],
    ids: set[str],
    resolver: Callable[[str], str | None] | None,
    warnings: list[str],
) -> dict:
    """Apply the ordered rules to one row and return its disposition record.

    The order is the whole safety argument — see the module docstring's I1/I2.
    """
    norm = normalize_name(name)

    # 1. Not a definite description ⇒ out of scope. KEEP.
    if not is_bare_definite_description(name):
        return _record(
            row_id=row_id, name=name, rule_id=RULE_NOT_DESCRIPTION,
            disposition=KEEP,
            reason="name is not a bare definite description (`the <X>`) — out of scope",
            counterfactual="nothing would change; the row is not in this migration's scope",
        )

    # 2. THE HUB GUARD (I1). Any inbound edge makes the row a referent, and a
    #    referent is never a candidate — this must precede every non-KEEP rule.
    if ref_count is not None and ref_count >= 1:
        return _record(
            row_id=row_id, name=name, rule_id=RULE_LOAD_BEARING,
            disposition=KEEP,
            reason=(
                f"load-bearing: {ref_count} inbound reference(s) — a referent, "
                "never a candidate for reclassification, merge or removal"
            ),
            counterfactual=(
                "nothing would change; removing a referenced Object is memory "
                "loss and would recreate exactly the hubs this layer needs"
            ),
            from_state={"ref_count": ref_count},
            to_state={"ref_count": ref_count},
        )

    # 3. Already dispositioned ⇒ no-op. This is the idempotence rule.
    if row.get("backfill_applied"):
        return _record(
            row_id=row_id, name=name, rule_id=RULE_ALREADY_APPLIED,
            disposition=KEEP,
            reason=(
                "a disposition is already recorded for this row "
                f"({row.get('backfill_class') or 'recorded'}) — re-running is a no-op"
            ),
            counterfactual="nothing would change; the disposition is already applied",
        )

    # 4. Fail-closed floor (I2): an unreadable reference count cannot prove the
    #    row is unreferenced, so it is reported rather than advanced.
    if ref_count is None:
        return _record(
            row_id=row_id, name=name, rule_id=RULE_REF_COUNT_UNREADABLE,
            disposition=REPORT,
            reason=(
                "reference count is absent or unparseable — absence of a "
                "measurement is not a measurement of zero"
            ),
            counterfactual=(
                "nothing would change; the row is reported so it can be measured "
                "before any rung is considered"
            ),
        )

    # 5. RESOLVE via the injected lookup (the S3-style seam; the backfill runs
    #    after the rows exist, so a lookup is available to it).
    if resolver is not None:
        referent = None
        try:
            referent = resolver(name)
        except Exception as e:  # fail-open: never fail-lowered
            warnings.append(
                f"resolver failed for {name!r} ({type(e).__name__}: {e}) — "
                "treated as unresolved (fail-open)"
            )
        if referent is not None and str(referent).strip():
            target = str(referent).strip()
            validated = target in ids or target in names
            if not validated:
                warnings.append(
                    f"resolver returned {target!r} for {name!r}, which is not in "
                    "the census — recorded unvalidated"
                )
            return _record(
                row_id=row_id, name=name, rule_id=RULE_RESOLVE_ARBITER,
                disposition=RESOLVE,
                reason=f"resolved by the lookup to {target!r}",
                counterfactual=(
                    f"would record that this object denotes {target!r}; the row is "
                    "kept and no field is overwritten"
                ),
                from_state={"denotes": None},
                to_state={"denotes": target, "referent_validated": validated},
            )

    # 6. RESOLVE by the article-stripped name — a deterministic, no-model
    #    resolution: `the plan doc` denotes a row named `plan doc`.
    stripped = definite_article_stripped(name)
    if stripped and stripped in names:
        others = [rid for rid, _n in by_norm.get(stripped, []) if rid != row_id]
        if others:
            target = others[0]
            return _record(
                row_id=row_id, name=name, rule_id=RULE_RESOLVE_ARTICLE,
                disposition=RESOLVE,
                reason=(
                    "resolved by the article-stripped name: "
                    f"{stripped!r} is object {target!r}"
                ),
                counterfactual=(
                    f"would record that this object denotes {target!r}; the row is "
                    "kept and no field is overwritten"
                ),
                from_state={"denotes": None},
                to_state={"denotes": target, "referent_validated": True},
            )

    # 7. RECLASSIFY — positive, conservative classification. ADDITIVE: it labels
    #    the row and overwrites nothing, which is why it outranks a merge.
    evidence = positive_evidence(name)
    if evidence is None:
        return _record(
            row_id=row_id, name=name, rule_id=RULE_RECLASSIFY,
            disposition=RECLASSIFY,
            reason=(
                "positively a bare definite description (every body token is a "
                "common noun) and unreferenced"
            ),
            counterfactual=(
                f"would label {name!r} as a definite description and leave "
                "objectKind untouched; undo: clear the label"
            ),
            from_state={"backfill_class": None},
            to_state={"backfill_class": "definite_description",
                      "overwrites": [], "kind_untouched": True},
            preserves=("objectKind", "name", "status", "id"),
        )

    # 8. MERGE_CANDIDATE — a spelling-duplicate of another row. RECORDED ONLY;
    #    the merge machinery is #5006's (union-of-attachments + the bar).
    others = [
        rid for rid, _n in by_norm.get(norm, []) if rid != row_id
    ]
    if others:
        target = others[0]
        return _record(
            row_id=row_id, name=name, rule_id=RULE_MERGE,
            disposition=MERGE_CANDIDATE,
            reason=(
                f"normalizes to the same denotation as object {target!r} despite "
                "the refused positive classification"
            ),
            counterfactual=(
                f"would propose folding into {target!r} with both sides' attachments "
                "unioned; kept as a recorded candidate only (#5006 applies the merge)"
            ),
            from_state={"merge_into": None},
            to_state={"merge_into": target, "applied": False},
        )

    # 9. Fail-closed floor (I2) — reported, never dropped.
    return _record(
        row_id=row_id, name=name, rule_id=RULE_UNCLASSIFIED,
        disposition=REPORT,
        reason=(
            "not positively a definite description and no referent found — "
            f"{evidence}"
        ),
        counterfactual=(
            "nothing would change; the row is reported to the arbiter and is not "
            "dropped (a correct keep is noise, a wrong drop is memory loss)"
        ),
    )


def plan_backfill(
    census: object,
    *,
    resolver: Callable[[str], str | None] | None = None,
    enabled: bool | None = None,
) -> dict:
    """Plan the backfill over ``census`` and return the recorded dispositions.

    ``census`` is a sequence of mapping rows (as :func:`census_rows` returns).
    **No graph I/O, no mutation** — the result is a ledger.

    Returns::

        {"enabled": bool,
         "records": {row_id: {id, name, rule_id, disposition, reason,
                              counterfactual, from_state, to_state, preserves,
                              destructive, disposition_key}},
         "order": [row_id, ...],
         "stats": {enabled, rows, actionable, by_disposition, by_rule,
                   destructive, fingerprint},
         "warnings": [...]}

    ``enabled`` defaults to :func:`backfill_enabled`; pass it explicitly in tests
    so the planner's behaviour does not depend on the ambient environment. With
    the flag off the plan is empty and only ``stats.enabled`` differs — the
    ``#4899`` flag contract.
    """
    on = backfill_enabled() if enabled is None else bool(enabled)
    if not on:
        return _empty_plan(False)

    warnings: list[str] = []

    # Normalize the census. Total on a malformed shape: a bare mapping is one
    # row, a non-iterable is no rows, a non-mapping row is skipped with a
    # warning — never an exception.
    if isinstance(census, Mapping):
        raw_rows: Iterable[Any] = [census]
    elif isinstance(census, (str, bytes)) or census is None:
        raw_rows = []
        if census is not None:
            warnings.append("census was a string, not a row sequence — ignored")
    else:
        try:
            raw_rows = list(census)
        except TypeError:
            raw_rows = []
            warnings.append("census was not iterable — treated as empty")

    rows: list[tuple[str, str, Mapping[str, Any]]] = []
    for index, row in enumerate(raw_rows):
        if not isinstance(row, Mapping):
            warnings.append(f"census row {index} is not a mapping — skipped")
            continue
        rows.append((_row_id(row, index), str(row.get("name") or ""), row))

    if not rows:
        return _empty_plan(True, warnings)

    # Duplicate names, for the deterministic no-model merge candidate.
    by_norm: dict[str, list[tuple[str, str]]] = {}
    for row_id, name, _row in rows:
        by_norm.setdefault(normalize_name(name), []).append((row_id, name))
    names = set(by_norm)
    ids = {row_id for row_id, _name, _row in rows}

    records: dict[str, dict] = {}
    order: list[str] = []
    for row_id, name, row in rows:
        # ⚠️ Two rows can share a synthesized id; last-write-wins would silently
        # drop a disposition, so a collision is reported and the row is reported
        # rather than mis-keyed.
        key = row_id
        if key in records:
            warnings.append(
                f"duplicate row id {row_id!r} in the census — the later row is "
                "reported under a positional key"
            )
            key = f"{row_id}#{len(order)}"
        record = _classify_row(
            row, row_id, name,
            ref_count=_as_int(row.get("ref_count")),
            by_norm=by_norm, names=names, ids=ids,
            resolver=resolver, warnings=warnings,
        )
        record["id"] = key
        record["disposition_key"] = _key(
            key, record["rule_id"], record["to_state"]
            if isinstance(record["to_state"], Mapping) else None
        )
        records[key] = record
        order.append(key)

    by_disposition = {d: 0 for d in _DISPOSITION_ORDER}
    by_rule = {r: 0 for r in RULES}
    actionable = 0
    for record in records.values():
        by_disposition[record["disposition"]] = (
            by_disposition.get(record["disposition"], 0) + 1
        )
        by_rule[record["rule_id"]] = by_rule.get(record["rule_id"], 0) + 1
        if record["disposition"] not in INERT_DISPOSITIONS:
            actionable += 1

    plan = {
        "enabled": True,
        "records": records,
        "order": order,
        "stats": {
            "enabled": True,
            "rows": len(records),
            "actionable": actionable,
            "by_disposition": by_disposition,
            "by_rule": by_rule,
            "destructive": sum(
                1 for r in records.values() if r["destructive"]
            ),
            "fingerprint": "",
        },
        "warnings": warnings,
    }
    plan["stats"]["fingerprint"] = plan_fingerprint(plan)
    return plan


def plan_fingerprint(plan: Mapping[str, Any]) -> str:
    """A stable digest of the plan's dispositions — the idempotence witness.

    :func:`plan_backfill` is a pure function of ``(census, resolver)``: no
    clock, no environment read beyond the flag, no graph I/O. So two runs over
    the same census produce the **same** fingerprint. A differing fingerprint
    for an unchanged census means the planner is no longer pure, which is the
    defect this digest exists to surface.
    """
    records = plan.get("records") or {}
    canonical = [
        {
            "id": r.get("id"),
            "rule_id": r.get("rule_id"),
            "disposition": r.get("disposition"),
            "from_state": r.get("from_state"),
            "to_state": r.get("to_state"),
        }
        for r in sorted(records.values(), key=lambda r: str(r.get("id")))
    ]
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
