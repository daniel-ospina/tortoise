"""S2.2 VET — the adversarial selection gate (extractor v4).

**The one question this step asks:** *"should this candidate exist at all?"*
(`EXTRACTOR-V4-ARCHITECTURE.md` §4.0 — the purpose test). S2.3 CLASSIFY asks
*"what IS it?"* — a different question with a different failure mode (S2.2 fails
as *"we kept junk"*; S2.3 fails as *"we mislabelled it"*).

**Why it must exist at all.** The only rejection in the live pipeline is
``classify_consolidation`` (``tortoise/extractor_v2.py``, called from
``execute_embed``), which runs **dead last** — inside ``execute_embed`` —
against *graph priors*, and therefore answers
*"is this a duplicate of something already stored?"*. That question structurally
cannot answer *"should this candidate exist at all?"*, which needs no priors.
This step is that seam.

**Ordering, stated exactly (not implied).** VET runs **before the resolve/embed
tail on every arm**, and **before the classify pass only under
``TORTOISE_CLASSIFY_LATER=1``** — with the flag off (the default) kinds are
emitted inline by S2.1/S4, so a candidate can reach VET already labelled. The
unconditional guarantee is therefore *"a discarded candidate is never resolved
or embedded"*; the never-classified half holds only on the classify-later arm.

⚠️ **Atomicity / ``SPLIT`` is NOT implemented here.** §S2.2 Level-1 q2 asks
*"fused? split it"*, but ``SPLIT`` is absent from the O4 vocabulary below and
from :data:`OUTCOMES`, so even a compliant arbiter cannot ask for a split. v1
records nothing for q2 — a disclosed omission, not an oversight.

Scope boundary — a RECORDED-DECISION boundary, not a preference
----------------------------------------------------------------
The **mechanical** rule set belongs to **#4899**, and **#1509 §2.4** mandates
the mechanism it uses — *"never create a parallel layer doing the same thing
with redundant machinery"*. So the predicate is not written here: it lives in
the leaf module :mod:`tortoise.value_gate` (stdlib-only, no pipeline
vocabulary), and this module **calls** it. VET remains the **adversarial half**
the design names:

    "#4899 specifies the mechanical DISCARD. Necessary, not sufficient: it
    cannot answer 'is this an entity or a reference?'"

The two are complementary — the same policy source (the pack), different
machinery (a regex predicate vs. a decision-only model).

⚠️ **#4899's placement was DECIDED 2026-09-25: S2.2b, i.e. HERE.** The issue as
first written put its ``DISCARD`` in ``classify_consolidation`` (E7), which runs
inside ``execute_embed`` — *after* both classify passes and after RESOLVE, the
exact inverted position §4.2 diagnoses as the bug; §4.3 names the step
``S2.2b VET — the gate (identifier-only DISCARD) + adversarial``. The
contradiction test settled it against the later record, and the predicate now
runs at :func:`vet_candidates` Level-2 **before** S3.

⚠️ **The predicate is IDENTIFIER-ONLY** (``#2453``: *"only INCIDENTAL process
logistics remain droppable: ids, hashes, and ephemeral counters not central to a
decision"*). It fires only when a candidate is *nothing but* an identifier. It
does **not** discard definite descriptions, CI vocabulary, test counts or hex
hashes — that is the semantic half, owned by ``#4894``. **The ``the <X>``
question is NOT mechanical** and is left to the arbiter: ``the owner`` is a real
entity, while ``the cycle-4 ruling`` is a definite description — telling them
apart is exactly the question below.

The arbiter seam is the point (§4.2)
------------------------------------
A **decision-only** model structurally enforces the vocabulary: a ``Choice``
over supplied options cannot return an out-of-vocabulary answer, whereas an
extraction LLM emitting kinds/verdicts inline can only be *hoped* to comply.
``arbiter=None`` ⇒ no model decides; the mechanical Level-2 checks still run,
including the flag-gated ``#4899`` identifier-only ``DISCARD`` — not all-KEEP. No caller injects an arbiter *directly* — the seam is
injected — but ``extractor_v2._s2_seam_arbiter`` now supplies
``s2_decision_seam.vet_arbiter()`` whenever ``TORTOISE_S2_DECISION_SEAM`` is
armed (`#5087`), so an arbiter-only gate DOES decide then. ``TORTOISE_VET``
alone supplies no arbiter and still decides nothing.

Vocabulary (owner ruling **O4**, `EXTRACTOR-V4-ARCHITECTURE.md` §16.3 / §16.4)
-----------------------------------------------------------------------------
- ``KEEP`` — ship the candidate.
- ``NOOP`` — a decision was made and it changes nothing (distinct from ``KEEP``
  only in *who* decided; both leave the item in place, so ``apply_vet`` treats
  them identically — a consumer that needs to tell them apart must read the
  recorded outcome, not the list). **Precedence, because "changes nothing" is
  load-bearing rather than decorative:** a ``NOOP`` never overrides a standing
  ``DISCARD``, while ``KEEP``/``DISCARD``/``MERGE`` do. The seam emits ``NOOP``
  for its uncertain and fused bands, so without that rule an armed seam would
  silently keep what a mechanical ``#4899`` predicate dropped and erase the rule
  id that explains it (:func:`vet_candidates` enforces it).
- ``DISCARD`` — do not ship; the candidate is removed and its counterfactual
  recorded.
- ``MERGE`` — fold into another candidate.
- ``RENARRATE`` — **batch-level only**: re-extract the batch.

This is VET's **selection** axis and is deliberately distinct from
``extractor_v2.DecisionRecord``'s consolidation axis (``ADD``/``UPDATE``/
``NOOP``/``DELETE``/``MERGE``). The tokens ``NOOP`` and ``MERGE`` are shared by
name, but the two sets are decisions at different steps: VET **records** a
``MERGE`` verdict, and ``extractor_v2`` reads that recorded verdict and takes
the ``MERGE`` **action** at S3 — neither module's outcome set is the other's.

⚠️ ``MERGE-INTO-EXISTING`` is deliberately **NOT** emitted — and that removal IS
the A4 correction §16.2 records (*"✅ NOW FIXED — ``MERGE-INTO-EXISTING`` is
removed from VET's outputs (§S2.2)"*). VET runs before S3 RESOLVE, and S3 is
what *finds* what exists — so "merge into existing" is circular at this
position. ⚠️ **The verdict and the action are not the same token** (§S2.2,
#5092): this gate may record ``MERGE`` for a flagged pair, it never performs the
fusion.

✅ **The verdict HAS a consumer (#5006).** A ``MERGE`` outcome in
``stats["decisions"]`` is read by ``extractor_v2._merge_flagged_contents`` and
routed into S3's consolidation pass, where ``tortoise.near_dup_merge`` executes
the fusion (union of both attachment sets onto ONE survivor). This module still
performs no fusion itself and must not: the split is §4.2's, and moving the
write here would put the fusion BEFORE the gate — the one thing that section
forbids. With ``arbiter=None`` there are no ``MERGE`` verdicts, so the routing
is inert on every arbiter-less run.

Failure policy (§4.2): FAIL-OPEN
--------------------------------
A wrong keep is noise; a wrong drop is memory loss. Unknown ⇒ ``KEEP``. An
arbiter that raises ⇒ **no verdict is applied** + a warning: every decision the
gate had ALREADY taken stands, including the ``#4899`` mechanical ``DISCARD``
(nothing here is an all-KEEP path — see the ``vet_candidates`` docstring, whose
"every candidate KEEP" wording was false and is corrected there for the same
reason it was corrected in the module prose). ``DISCARD`` has exactly **two**
paths: an explicit arbiter verdict, or the ``#4899`` mechanical predicate when
its flag is on — and every one records its ``rule_id`` and its counterfactual.
The public functions are **total** on a malformed
section shape — a non-sequence section, a non-sequence ``operators``, a
non-mapping item, a non-iterable ``about_entities``, ``None`` — and on a
malformed ``prior``/``decisions`` map,
yielding no candidates rather than an exception, so a direct caller outside
``extractor_v2._run_vet_pass`` is fail-open too. (The arbiter is NOT called
for an EMPTY candidate list, so a batch-level ``RENARRATE`` cannot be requested
for a session that extracted nothing — ``check_batch``'s ``coverage_suspect``
is the signal there. Disclosed, not hidden.)

⚠️ **A referenced entity is never removed** (the Layer-1 guard). See
``apply_vet``: an entity that any surviving candidate references **is a
referent**, and removing it would fail ``commit_schema.validate_layer1``
(``about_entities ⊆ entities``) and 422 the whole session — the exact
"a wrong drop is memory loss" outcome the failure policy forbids. Because the
gate runs **twice** (before S3, then on the post-S4 union), the guard is
carried across passes via :func:`removal_pool` — a cross-pass reference (S4
naming an entity the S2 pass removed) is invisible to a per-pass check and is
exactly what produces that 422.
"""
from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Mapping, MutableMapping, Sequence
from typing import Any

from . import value_gate

# ── The outcome vocabulary (O4) ──────────────────────────────────────────────

KEEP = "KEEP"
NOOP = "NOOP"
DISCARD = "DISCARD"
MERGE = "MERGE"
RENARRATE = "RENARRATE"          # batch-level only

#: Per-candidate outcomes. ``RENARRATE`` is NOT here — it is a batch verdict.
OUTCOMES = frozenset({KEEP, NOOP, DISCARD, MERGE})

#: The batch-level outcome set.
BATCH_OUTCOMES = frozenset({RENARRATE})

#: The sections VET inspects: ``(section, text-field, kind-field, family)``.
#: The text-field column documents the field each section normally carries;
#: ``_item_text`` follows the extractor's actual rule (``name or content``).
#:
#: ⚠️ This is a SECOND copy of ``extractor_v2._CLASSIFY_SECTIONS`` — this module
#: cannot import it, because ``extractor_v2`` imports *this* module (a
#: module-level import here would cycle and silently fall back). The copy is
#: kept non-silent by ``tests/test_vet_gate_5005.py::
#: test_section_table_matches_extractor``, which fails the moment the two
#: drift. The text rule itself is pinned SEPARATELY by
#: ``test_text_field_matches_extractor_item_identity``: the column above cannot
#: be pinned to ``_CLASSIFY_SECTIONS`` because that table carries no text field.
SECTIONS: tuple[tuple[str, str, str, str], ...] = (
    ("entities", "name", "kind", "entity"),
    ("events", "content", "eventKind", "event"),
    ("points", "content", "pointKind", "point"),
)

#: A stable rule id for the one v1 producer (the arbiter). #4899's mechanical
#: rules carry their own ids; this module never emits them.
RULE_ARBITER = "vet.arbiter"

#: How a verdict is recorded when the arbiter did not supply one.
RULE_FAIL_OPEN = "vet.fail_open"

_SENTENCE_SPLIT = re.compile(r"[.!?;]+(?:\s|$)|[\n\r]+")


# ── Small helpers (stdlib-only — this module must not import extractor_v2,
#    because extractor_v2 imports this module) ──────────────────────────────

def _norm(text: object) -> str:
    """Whitespace-collapsed, lower-cased text — the identity used for ids and
    for matching operator endpoints to removed items.

    ⚠️ Deliberately the SAME transform as ``extractor_v2._norm`` (``.lower()``,
    **not** ``.casefold()``): the two meet on operator endpoints and on
    removed-item text, so a normaliser that disagreed on a single codepoint
    (``"Straße"``) would make a prune miss — or hit — silently. Pinned by
    ``test_norm_matches_extractor_norm``.
    """
    return " ".join(str(text or "").split()).lower()


def _section_items(embed_list: object, section: str) -> Sequence[Any]:
    """The item list for ``section``, or ``()`` for any malformed shape.

    Defensive by design: the module's contract is that a bad section shape is
    a *no candidate*, never an exception (fail-open). A string is a
    ``Sequence`` and must not be iterated as items.
    """
    if not isinstance(embed_list, Mapping):
        return ()
    return _as_items(embed_list.get(section))


def _as_items(value: object) -> Sequence[Any]:
    """``value`` as an iterable item list, or ``()`` when it is not one."""
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return value
    return ()


#: The embedder truncates point/event content AND minted endpoint refs to this
#: many characters before keying them (``extractor_v2._MAX_CONTENT``, the
#: ``str(...).strip()[:1000]`` sites in ``execute_embed``) — cited symbolically,
#: not by line number, because line numbers move as the module is edited.
_MAX_CONTENT = 1000


def _norm_variants(text: object) -> set[str]:
    """Normalized keys for an ENTITY-name identity — the mint's guard SUPERSET.

    The mint transform ``M(x) = _norm(x.strip()[:_MAX_CONTENT])`` applied to the
    raw string and to its whitespace-collapsed form — ``_endpoint_keys(x) = {M(x),
    M(_norm(x))}``, the entity guard's own set ("the two SPELLINGS, not every
    truncation") — PLUS the untruncated ``_norm(x)`` alias the mint registers for
    a minted endpoint (``_full``; ``_resolve`` probes that spelling). So for
    non-empty ``x``, ``_norm_variants(x) == _endpoint_keys(x) | {_norm(x)}`` — a
    SUPERSET that is STRICT exactly when ``_norm(x)`` is not already one of the
    guard's two arms; both helpers return the empty set for empty/whitespace-only
    input.

    Use this for the **entity-name key sets** (``removed_entity_names``,
    ``present_entity_names``, ``prior_entity_keys``) AND for the **ENTITY half**
    of the operator-endpoint surface (:func:`_operator_endpoint_key_sets`),
    which ``apply_vet`` matches against the removed-ENTITY keys alone
    (``entity_gone``) — so it must cover the entity-GUARD ref keys, not only the
    resolution keys. Use :func:`_resolution_variants` (``{_norm(x), M(x)}``) for
    point/event CONTENT and for the CONTENT half of the endpoint surface:
    relative to it, the extra arm here is the entity guard's collapsed-closure
    ``M(_norm(x))``, which a regular point never registers. Carrying it on a
    content SURVIVOR surface lets a surviving long point shield an operator
    endpoint the mint cannot resolve, and carrying it against a MERGED ``gone``
    lets it collide with a removed CONTENT key — each re-opening a ``#2552``
    fabrication or dropping a legitimate edge (#5069 review, P1).

    For non-empty ``raw`` the two sets are equal exactly while the untruncated
    alias ``_norm(raw)`` is ALREADY one of the guard's two arms; otherwise the
    difference is exactly ``{_norm(raw)}``. For empty/whitespace-only input both
    helpers return the empty set, so the difference is empty too. Do NOT decide
    equality by length: ``.lower()`` can LENGTHEN (``"İ"`` lowercases to two
    code points), so a raw string at or under the cap can still normalise past
    it — compare the SETS, not the lengths.
    ``test_endpoint_key_set_matches_extractor`` pins the containment and the
    exact difference.
    """
    raw = str(text or "").strip()
    if not raw:
        return set()
    return {_norm(raw),
            _norm(raw[:_MAX_CONTENT]),
            _norm(_norm(raw)[:_MAX_CONTENT])}


def _resolution_variants(text: object) -> set[str]:
    """The keys ``execute_embed`` RESOLVES point/event/operator CONTENT on.

    ``_norm(x.strip()[:_MAX_CONTENT])`` — the mint's resolution key — plus the
    UNTRUNCATED ``_norm(x)`` alias a minted endpoint registers (``_full`` in
    ``_mint_endpoint``; ``_resolve`` probes that spelling). Deliberately NOT
    :func:`_norm_variants`: that set additionally carries the ENTITY guard's
    collapsed-closure arm, which a regular point/event never registers. On a
    SURVIVOR surface that extra arm lets a surviving >cap point shield an
    operator endpoint the mint cannot resolve, and the mint then fabricates a
    claim Point from the DISCARDED text — the ``#2552`` failure the prune
    exists to prevent (#5069 review, P1).
    """
    raw = str(text or "").strip()
    if not raw:
        return set()
    return {_norm(raw), _norm(raw[:_MAX_CONTENT])}


def _item_text(section: str, item: Mapping[str, Any]) -> str:
    """The item's text — the extractor's OWN rule: ``name`` or ``content``.

    Deliberately not a per-section field lookup: ``extractor_v2`` builds every
    item key (``_classify_item_id``) and the classification surface
    (``_collect_classify_items``) from ``item.get("name") or
    item.get("content")`` regardless of section, so a section-specific rule
    would disagree on an item carrying both keys — and VET would then gate on a
    different string than the pipeline labels. The ``SECTIONS`` text-field
    column documents the field each section *normally* uses; this function
    follows the extractor, which is the authority.
    """
    if section not in {sec for sec, _f, _k, _fam in SECTIONS}:
        return ""
    return str(item.get("name") or item.get("content") or "").strip()


def _item_kind(section: str, item: Mapping[str, Any]) -> str:
    for sec, _field, kind_field, _family in SECTIONS:
        if sec == section:
            return str(item.get(kind_field) or "").strip()
    return ""


def _item_id(section: str, index: int, item: Mapping[str, Any]) -> str:
    """The per-item key a verdict is addressed to.

    Deliberately **index-dependent** (``section:index:norm(text)``), unlike
    ``extractor_v2._classify_item_id`` (which drops the index so its key is
    stable across reorders). VET's two calls — ``vet_candidates`` then
    ``apply_vet`` — run back-to-back on the *same* list, so the index is
    stable, and keeping it is what lets two identical-content items be judged
    independently (one discarded, the twin kept). Making the key
    index-independent would silently give both twins one id.
    """
    return f"{section}:{index}:{_norm(_item_text(section, item))}"


def _iter_items(embed_list: object
                ) -> Iterable[tuple[str, int, Mapping[str, Any]]]:
    for section, _field, _kf, _family in SECTIONS:
        for i, item in enumerate(_section_items(embed_list, section)):
            if isinstance(item, Mapping):
                yield section, i, item


def _narrative_statements(narrative: str) -> int:
    """A deterministic proxy for "how many things did the narrative state?".

    Deliberately crude and **unmeasured** — it exists to be *reported*, not to
    trigger an action. The design's `RENARRATE` trigger threshold is explicitly
    something the draft-and-run loop must set on real sessions (§4.2), so this
    function never decides anything by itself.
    """
    return sum(1 for part in _SENTENCE_SPLIT.split(str(narrative or ""))
               if part and part.strip())


# ── #11534 — the PARTIAL-gap detector (statement + clause spans) ──────────
#
# ``coverage_suspect`` above answers ONE question — "did prose go in and
# nothing come out?" (``total == 0``) — and that is the only coverage failure
# it can see. A narrative that yielded 44 candidates while missing one
# statement's claim is INVISIBLE to every instrument in the tree, which is what
# #11534 exists to fix: an ADD-capable stage cannot be built on a detector that
# can only count.
#
# ⛔ WHAT THIS DETECTOR IS, AND WHAT IT IS NOT. It is a **lexical, deterministic
# coverage proxy**: a segment is *covered* when the content words it carries are
# found in the candidate set, weighted by how DISCRIMINATING each word is in
# this batch (idf over the batch's own item texts). It decides nothing — it
# reports segments, an address for each, and what it thinks is missing — and it
# never triggers a write by itself (`check_batch`'s contract is "reported, never
# acted on", and #11534's own indicator 3 requires the detector to be SCORED
# before it drives anything).
#
# ⚠️ Its measured limits, from the F3 population (#11534's evidence record,
# ``docs/evidence/11534-partial-gap/``): on the 7 recorded sessions × the 2
# recorded runs in ``docs/evidence/1789-s4-reeemit-tax/`` it flags **70 of 844
# clause segments (8.3%)** and overlaps **9 of the 21 items S4 added — 8 of 12
# `points` but 1 of 9 `operators`**. It catches clause-level elisions (a claim
# the narrative made in a clause no candidate carries) and it MISSES the two
# shapes this proxy cannot see by construction: (a) a **generalisation** — an
# operator or point that restates a covered statement at a higher abstraction,
# so no narrative span is uncovered even though the item is new — most of the 8
# of 9 operators it does NOT reach (the record names 3), and (b) a **re-shaped**
# item whose content the candidates already lexically carry. Read a low
# ``coverage`` as evidence of missing prose, never as a claim a model should emit.
#
# ⚠️ The ADDRESS is a char span into the NARRATIVE — not into a source turn, and
# not a graph span. #11534's body worried that the target needed `#2684`'s span
# layer (point→source, `state: not-implemented`); it does not: a re-extraction
# that reads the flagged span needs the span in the text it was given, which is
# computed from the narrative string in hand. `#2684` remains a separate,
# unbuilt mechanism and is not a precondition for this one.

#: Marks a segment as *not accounted for* when its idf-weighted coverage falls
#: below this floor. Measured, not chosen in the dark — and RE-DERIVED by
#: ``tests/test_11534_partial_gap.py``::
#: ``test_the_coverage_floor_is_justified_by_the_measured_distribution``,
#: because this comment carried PRE-correction percentiles through two review
#: rounds (both were unreproducible at the shipped settings, and quoting them
#: here only gives the next reader a number to re-quote).
#: Over the 7 committed sessions the population is a full-coverage MASS with a
#: low TAIL: of the 210 segments that clear ``GAP_MIN_CONTENT_TOKENS``, **147 sit
#: at exactly 1.0** and **35 fall below this floor** (median 1.0, p10 0.355, p25
#: 0.736).
#:
#: ⛔ WHERE THE FLOOR SITS, SAID SO THAT IT IS TRUE (review round 4, P2). An
#: earlier version of this comment called ``[0.5, 0.6)`` "the THINNEST band
#: between the two", and that is FALSE on the population it cites: measured in
#: 0.1-wide bands, ``[0.2, 0.3)`` holds 3 segments and ``[0.8, 0.9)`` holds 4, so
#: the band the floor sits in is neither the thinnest nor uniquely thin. What the
#: data does support is narrower and is what the pin asserts: across the three
#: bands straddling the floor (``[0.4, 0.5)`` 6 · ``[0.5, 0.6)`` 4 · ``[0.6, 0.7)``
#: 14) this one is the LOCAL MINIMUM, and it is a shallow one — of its four values
#: (0.508 · 0.511 · 0.528 · 0.586) ONE sits above the floor, so 0.55 cuts three of
#: the four. The floor's defensible justification is the mass/tail split above;
#: the local minimum is corroboration, not a separation. Pinned by
#: ``test_the_coverage_floor_is_justified_by_the_measured_distribution`` (which
#: re-derives the bands), so the next reader cannot re-quote a claim nothing
#: measures.
GAP_COVERAGE_FLOOR = 0.55

#: Segments carrying fewer content words than this are not claims — the class it
#: removes is `**bold label**` and date-fragment segments; headings are masked
#: BEFORE segmentation, so they are never flags. Re-derived by
#: ``test_the_content_floor_s_removal_class_is_measured_not_asserted``.
GAP_MIN_CONTENT_TOKENS = 4

#: How many flagged segments ride ``stats["batch"]``. The full count is always
#: reported in ``flagged_total``; the cap keeps a session's stats JSON bounded.
GAP_MAX_FLAGGED = 20

#: A word is a content word when it is at least this long and not in the stop
#: set. Kept LOW on purpose — the vocabulary here is technical prose, and the
#: idf weighting (not this length) is what keeps common words quiet.
_GAP_MIN_WORD = 3

_GAP_WORD = re.compile(r"[a-z0-9][a-z0-9\-]{1,}")

#: Clause boundaries. ``,``/``:``/``—`` are the narrative's own separators;
#: the conjunctions are included because the population writes a claim as
#: "<covered clause>, though <the elided one>" — the elision is a CLAUSE, and a
#: sentence-level detector never reaches it.
_GAP_CLAUSE = re.compile(
    r",|—|--|:|\bbut\b|\bso\b|\bthough\b|\bwhile\b|\bwhich\b|\bbecause\b"
    r"|\band\b")

_GAP_STOP = frozenset("""
 a an and are as at be been being both but by can could did do does doesn done
 each even ever every few for from had has have he her here his how if in into
 is it its just may me might more most much must never no nor not now of off on
 once one only or other our out over own per same she should since so some such
 than that the their them then there these they this those through to too two
 under until up us very was we were what when where which while who whom why
 will with within without would yet you your also although because though still
 three four five six seven eight nine ten does doesn nothing else what's dont
 isn't""".split())  # noqa: SIM905 — one word per line is the readable form here

#: Suffix stripping — deliberately the CRUDEST possible stemmer (longest match
#: wins, one pass). It exists so "shares"/"shared"/"sharing" meet; a real
#: stemmer would need a dependency this module may not take (it is stdlib-only by
#: contract — see the module docstring).
#:
#: ⛔ BASE-FORM SYMMETRY IS THE PROPERTY THAT MATTERS, AND THE FIRST VERSION OF
#: THIS FUNCTION DID NOT HAVE IT (review round 2, P2, measured). A bare
#: "strip the suffix if the remainder is long enough" rule is ASYMMETRIC: the
#: plural loses ``es`` *and* the ``e`` (``shares`` → ``shar``) while the singular
#: keeps its ``e`` (``share`` → ``share``), so the pair the docstring named did
#: not meet — and the failure is in the direction that INVENTS a gap (lower
#: coverage), not the direction that hides one. Measured on the recorded
#: population before the fix: 2 of the 70 flagged spans were this class (both
#: ``stated`` vs the batch's ``state``). The fix is two normalisations that run on
#: BOTH sides: ``-ies`` → ``-y`` (so ``policies``/``policy`` meet) and a trailing
#: ``e`` dropped from the stem (so every one of
#: stage/lease/cache/queue/service/share/state meets its plural).
#:
#: What remains asymmetric is disclosed rather than claimed away, and the class is
#: WIDER than the one named here before: on the SHIPPED function ``status``/``statuses``,
#: ``process``/``processes`` and ``class``/``classes`` stem differently too, so it
#: is not only the non-``-s`` plurals; it can only lower coverage, never raise it.
_GAP_SUFFIXES = sorted(
    ("ations", "ation", "ments", "ment", "ings", "ing", "ied",
     "ers", "er", "ed", "es", "s", "ly", "al"),
    key=len, reverse=True)


def _gap_stem(word: str) -> str:
    if len(word) > 5 and word.endswith("ies"):
        return word[:-3] + "y"                 # policies → policy, both sides
    for suffix in _GAP_SUFFIXES:
        if len(word) - len(suffix) >= 3 and word.endswith(suffix):
            word = word[:-len(suffix)]
            break
    if len(word) > 3 and word.endswith("e"):
        word = word[:-1]                        # stage/stages → stag, both sides
    return word


def _gap_tokens(text: object) -> set[str]:
    """The content tokens of `text` — markdown punctuation stripped, stemmed.

    Deliberately NOT :func:`_norm`: that one is the *identity* transform shared
    with ``extractor_v2`` (operators, removals), and widening it here would
    change what the gate means by "the same item". This is a coverage lens, not
    an identity, and the two must stay separable.
    """
    cleaned = re.sub(r"[`*#_>|]+", " ", str(text or "").lower())
    return {_gap_stem(w) for w in _GAP_WORD.findall(cleaned)
            if len(w) >= _GAP_MIN_WORD and w not in _GAP_STOP}


def _gap_mask(narrative: str) -> str:
    """`narrative` with markdown HEADINGS blanked, SAME LENGTH.

    Same length is the contract: every offset the detector reports is an offset
    into the ORIGINAL narrative, so a caller can slice it back out.

    ⚠️ WHAT IT IS FOR, MEASURED (review round 2, P2). An earlier version of this
    docstring claimed the mask removes *"the population's dominant
    false-positive class"*. It does not: removing the blanking entirely on the
    recorded population changes **zero flags** (70 → 70, only the segment count
    844 → 876), because the heading class is already suppressed by
    :data:`GAP_MIN_CONTENT_TOKENS` — a heading that survives the split into a
    colon-free clause is short. What the mask actually buys is **stable statement
    NUMBERING**: a heading's own ``.`` (``## Root cause (preserved exactly).``)
    would otherwise open a phantom statement and shift every subsequent index, so
    the address :func:`narrative_segments` reports would drift with editorial
    edits to the titles. That is the property it is kept for; the sentence above
    used to attribute an effect to the wrong guard.
    """
    chars = list(narrative)
    for match in re.finditer(r"(?m)^[ \t]*#{1,6}(?:[ \t][^\n]*)?$", narrative):
        for i in range(match.start(), match.end()):
            chars[i] = " "
    return "".join(chars)


def narrative_segments(narrative: str) -> list[dict]:
    """The narrative's CLAUSE spans, each with a statement AND clause index.

    Every entry is ``{"statement": i, "clause": j, "start": s, "end": e,
    "text": t}`` where ``s``/``e`` index the ORIGINAL narrative, so the caller
    can re-read exactly the bytes the detector judged. ``statement`` is a
    SENTENCE ORDINAL in this function's own segmentation; ``clause`` orders the
    segments within that statement.

    ⛔ ``statement`` IS AN ADDRESS ONLY — it is **not** comparable to the
    ``narrative_statements`` count :func:`_narrative_statements` reports, and an
    earlier version of this docstring was wrong about the ONLY reason why
    (review, P2). It claimed the two agree "up to the headings"; they do not.
    Two independent divergences:

    * headings — this split runs on the heading-blanked mask
      (:func:`_gap_mask`), so a heading's own ``.`` opens no statement here,
      while ``_narrative_statements`` counts the raw prose;
    * **in-sentence terminators**, the larger one. ``_SENTENCE_SPLIT``
      (``[.!?;]+(?:\\s|$)|[\\n\\r]+``) requires the terminator to be followed by
      whitespace or end-of-text, and this scan does not — any ``.``/``;`` opens
      a new statement here. Measured: ``"The p95 hit 4.2 seconds under load.
      Then it recovered."`` reports ``narrative_statements == 2`` while the
      segments carry statement indices ``0, 1, 2`` (the decimal's ``.`` opened
      one). A version number, a domain name or a URL does the same.

    So read ``statement`` as a grouping key for the segments in THIS result —
    stable for a given narrative, and useful because two flagged spans sharing
    an index came from one sentence of this segmentation — and never as an
    index into the reported statement count. Changing this splitter to match
    ``_SENTENCE_SPLIT`` would reconcile them and would also move the flag count,
    which the recorded measurement (844 segments / 70 flags) is pinned on; the
    address is what a re-extraction reads, and the count was never the contract.
    """
    text = str(narrative or "")
    if not text:
        return []
    masked = _gap_mask(text)
    out: list[dict] = []
    statement = -1
    for sentence in re.finditer(r"[^.!?;\n\r]+", masked):
        chunk = sentence.group(0)
        if not chunk.strip():
            continue
        statement += 1
        base = sentence.start()
        separators = list(_GAP_CLAUSE.finditer(chunk))
        starts = [0] + [m.end() for m in separators]
        ends = [m.start() for m in separators] + [len(chunk)]
        for clause, (a, b) in enumerate(zip(starts, ends, strict=True)):
            if b <= a:
                continue                        # adjacent separators
            piece = chunk[a:b]
            if not piece.strip():
                continue
            out.append({"statement": statement, "clause": clause,
                        "start": base + a, "end": base + b,
                        "text": text[base + a:base + b].strip()})
    return out


def partial_gaps(embed_list: object, narrative: str, *,
                 coverage_floor: float = GAP_COVERAGE_FLOOR,
                 min_content_tokens: int = GAP_MIN_CONTENT_TOKENS,
                 max_flagged: int = GAP_MAX_FLAGGED) -> dict:
    """Which narrative SEGMENTS does this batch not account for? (#11534)

    For every clause span, the share of its content tokens (idf-weighted, over
    this batch's own item texts) that no candidate carries. A segment below
    ``coverage_floor`` with at least ``min_content_tokens`` content words is
    **flagged**: it is prose the batch did not account for.

    Returns::

        {"segments": N, "coverage_floor": f, "min_content_tokens": k,
         "flagged_total": K, "flagged": [ {...segment, "coverage": c,
         "missing": [tokens]} ], "truncated": bool}

    ``flagged_total`` is the TRUE count; ``flagged`` holds at most
    ``max_flagged`` entries so a session's stats stay bounded. Entries are
    ordered by ASCENDING coverage — the least accounted-for segment first, which
    is the order a re-extraction wants.

    **Total on every malformed shape** (fail-open): a non-str narrative yields
    no segments, a malformed ``embed_list`` yields the empty candidate set (so
    every content-bearing segment is flagged, which is the honest answer — with
    no candidates nothing is accounted for), and no exception escapes.

    ⚠️ idf is computed over the ITEM TEXTS, not a corpus: a word the batch uses
    everywhere ("stage", "lease") is near-worthless as evidence of coverage,
    and one it does not use at all scores highest — which is exactly the
    "nothing here accounts for this word" signal. ``log((N+1)/(df+0.5))`` never
    divides by zero and never returns a non-positive weight for a token in the
    union.
    """
    segments = narrative_segments(narrative)
    empty = {"segments": 0, "coverage_floor": coverage_floor,
             "min_content_tokens": min_content_tokens,
             "flagged_total": 0, "flagged": [], "truncated": False}
    if not segments:
        return empty

    candidate_tokens = [_gap_tokens(_item_text(section, item))
                        for section, _i, item in _iter_items(embed_list)]
    candidate_tokens = [toks for toks in candidate_tokens if toks]
    df: dict[str, int] = {}
    for toks in candidate_tokens:
        for token in toks:
            df[token] = df.get(token, 0) + 1
    n_items = len(candidate_tokens)

    def _idf(token: str) -> float:
        return math.log((n_items + 1) / (df.get(token, 0) + 0.5))

    union = set(df)
    flagged: list[dict] = []
    for segment in segments:
        toks = _gap_tokens(segment["text"])
        if len(toks) < min_content_tokens:
            continue
        total = sum(_idf(t) for t in toks)
        if total <= 0:                          # impossible for a non-empty set
            continue
        covered = sum(_idf(t) for t in toks if t in union)
        coverage = covered / total
        if coverage >= coverage_floor:
            continue
        flagged.append({**segment, "coverage": round(coverage, 3),
                        "missing": sorted(t for t in toks if t not in union)})
    flagged.sort(key=lambda entry: entry["coverage"])
    return {"segments": len(segments),
            "coverage_floor": coverage_floor,
            "min_content_tokens": min_content_tokens,
            "flagged_total": len(flagged),
            "flagged": flagged[:max_flagged],
            "truncated": len(flagged) > max_flagged}


# ── Level 2 — the batch checks (mechanical, unique to VET) ─────────────────

def check_batch(embed_list: object, narrative: str) -> dict:
    """Level 2 — *did this batch come out right?*

    Three signals, all **reported, never acted on** in v1:

    - **coverage** — candidates emitted vs. statements the narrative carried.
      ``coverage_suspect`` fires only on the unambiguous case (a non-empty
      narrative produced **zero** candidates — a definite loss, no threshold).
    - **partial coverage** (#11534) — ``partial_gaps``: WHICH clause span no
      candidate accounts for, with a char span into the narrative. The zero-case
      signal above cannot see a narrative that yielded 44 candidates and still
      left a statement uncovered; this one can, and it still only reports.
    - **abstraction** — the candidate count per family, so a session that spent
      itself on one family is visible to the hand review.

    Counts use the **same predicate** as :func:`vet_candidates` (a Mapping item
    with non-empty text), so ``candidates`` here always equals
    ``stats["candidates"]`` there — an instrument that disagreed with what the
    gate actually saw would mislead the hand review it exists to serve.

    Returns a plain dict (JSON-safe) that rides ``stats["batch"]``.
    """
    statements = _narrative_statements(narrative)
    counts: dict[str, int] = {section: 0 for section, _f, _k, _fam in SECTIONS}
    total = 0
    for section, _index, item in _iter_items(embed_list):
        if _item_text(section, item):
            counts[section] += 1
            total += 1
    ratio = (total / statements) if statements else None
    return {
        "narrative_statements": statements,
        "candidates": total,
        "by_section": counts,
        "coverage_ratio": (round(ratio, 3) if ratio is not None else None),
        # The ONE unambiguous coverage failure: prose in, nothing out.
        "coverage_suspect": bool(statements and total == 0),
        # #11534: WHICH prose. ``coverage_suspect`` above can only fire on a
        # zero-candidate batch, so a partial gap — a narrative that yielded
        # candidates and still left a statement uncovered — is invisible to it.
        # Additive key, same "reported, never acted on" contract.
        "partial_gaps": partial_gaps(embed_list, narrative),
    }


# ── Level 1 — the arbiter seam ─────────────────────────────────────────────

#: The arbiter contract: given the candidate list and the narrative, return
#: either ``{"verdicts": [...], "batch": {...}}`` or a bare list of verdicts.
#: Each verdict is ``{"id": str, "outcome": str, "rule_id"?: str,
#: "reason"?: str}``. Anything else — a missing id, an unknown outcome, a
#: missing verdict — is fail-open (KEEP).
Arbiter = Callable[[list[dict], str], Any]


def _parse_verdicts(raw: Any) -> tuple[dict[str, dict], dict]:
    """Normalise an arbiter response into ``({id: verdict}, batch)``.

    Tolerant by design: the arbiter is a model, and the response shape is the
    one thing we can never fully trust. An unparseable response yields no
    verdicts → every candidate keeps (fail-open).
    """
    batch: dict = {}
    verdicts: dict[str, dict] = {}
    if isinstance(raw, Mapping):
        batch = dict(raw.get("batch") or {}) if isinstance(
            raw.get("batch"), Mapping) else {}
        raw = raw.get("verdicts")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return {}, batch
    for v in raw:
        if not isinstance(v, Mapping):
            continue
        vid = str(v.get("id") or "").strip()
        if not vid:
            continue
        outcome = str(v.get("outcome") or "").strip().upper()
        if outcome not in OUTCOMES:
            continue                      # fail-open: unknown ⇒ no verdict
        verdicts[vid] = {
            "outcome": outcome,
            "rule_id": str(v.get("rule_id") or RULE_ARBITER),
            "reason": str(v.get("reason") or ""),
        }
    return verdicts, batch


def vet_candidates(embed_list: object, *,
                   narrative: str = "",
                   arbiter: Arbiter | None = None) -> dict:
    """Run S2.2 VET over one candidate list.

    Returns::

        {"decisions": {item_id: {"section", "index", "text", "outcome",
                                 "rule_id", "reason", "counterfactual",
                                 "agreed_by"}},
         "batch": {...},            # check_batch() + any arbiter batch verdict
         "stats": {...},            # counts, for the session roll-up
         "warnings": [...]}

    ``stats`` may carry a ``seam`` key: an arbiter that reports its own record
    (spend, model, prompt version, bands — #5087's typed-decision seam) has it
    folded in here, additively and under that one key, so the counters above stay
    this gate's own. An arbiter that reports nothing simply has no such key — the
    gate's FAIL-OPEN path must not require one.

    **Fail-open everywhere.** ``arbiter=None`` ⇒ no model decides: every
    candidate the mechanical pass did not discard is ``KEEP``, and with
    ``TORTOISE_VALUE_GATE`` on that pass can still ``DISCARD`` an identifier-only
    candidate (the Level-2 predicate runs BEFORE the arbiter and does not require
    one) — so "no arbiter" is not "all candidates kept".
    An arbiter that raises ⇒ **no verdict is applied** + a warning: everything
    the gate had ALREADY decided stands, so the mechanical ``#4899`` ``DISCARD``
    is not reverted, and the warning says so rather than claiming "all kept"
    (this docstring and that warning both said "every candidate KEEP" until
    #10072's review measured the composition: the surviving DISCARD is the
    fail-closed direction this gate exists for, and a record that said otherwise
    was a false claim about it). A candidate the arbiter did not mention ⇒
    ``KEEP``.
    """
    warnings: list[str] = []
    candidates: list[dict] = []
    decisions: dict[str, dict] = {}
    for section, index, item in _iter_items(embed_list):
        text = _item_text(section, item)
        if not text:
            continue
        iid = _item_id(section, index, item)
        candidates.append({"id": iid, "section": section, "text": text,
                           "kind": _item_kind(section, item)})
        decisions[iid] = {
            "section": section, "index": index, "text": text,
            "outcome": KEEP, "rule_id": RULE_FAIL_OPEN, "reason": "no verdict",
        }

    batch = check_batch(embed_list, narrative)
    arbiter_batch: dict = {}
    seam_stats: dict = {}

    # #4899 — the mechanical Level-2 predicate: identifier-only DISCARD.
    #
    # ⛔ This guard is WHY the default lives in ``value_gate.value_gate_enabled``
    # and not in the pipeline wrapper (`#10996` Claim 4): a default flipped one
    # file over would arm ``extractor_v2``'s pass while this line still read
    # ``False``, giving ``enabled: False, fired: 0`` — the pass running with the
    # predicate inert. The leaf reader is the effecting read, so the default sits
    # there. ``TORTOISE_VALUE_GATE=0`` (or blank/garbage) is the kill-switch.
    #
    # It runs BEFORE the arbiter, deliberately: with ``TORTOISE_VET`` alone no
    # arbiter is supplied (``extractor_v2._s2_seam_arbiter`` supplies one only
    # when ``TORTOISE_S2_DECISION_SEAM`` is armed, #5087), so an arbiter-only
    # gate decides nothing. Mechanical-first also makes the composition honest — the
    # predicate is *necessary, not sufficient*, and an explicit arbiter verdict
    # still overrides it below (``decisions[iid].update(...)``).
    #
    # Fail-open: a predicate that raises keeps everything. The rewrite back to
    # ``KEEP`` is not decoration — a partial pass would otherwise leave a
    # DISCARD with no recorded provenance, and a discard nobody can explain is
    # indistinguishable from memory loss.
    mechanical: dict[str, dict] = {}
    if value_gate.value_gate_enabled() and candidates:
        try:
            for iid, decision in decisions.items():
                verdict = value_gate.mechanical_verdict(decision["text"])
                if verdict:
                    decision.update(outcome=DISCARD, **verdict)
                    mechanical[iid] = verdict
        except Exception as e:
            warnings.append(
                f"value gate failed ({type(e).__name__}: {e}) — "
                f"{len(candidates)} candidate(s) kept (fail-open)")
            for iid in list(mechanical):
                decisions[iid].update(outcome=KEEP, rule_id=RULE_FAIL_OPEN,
                                      reason="value gate failed")
            mechanical.clear()

    if arbiter is not None and candidates:
        try:
            payload = arbiter(candidates, str(narrative or ""))
            verdicts, arbiter_batch = _parse_verdicts(payload)
            if isinstance(payload, Mapping):
                seam_record = payload.get("seam")
                if isinstance(seam_record, Mapping) and seam_record:
                    seam_stats = dict(seam_record)
        except Exception as e:
            # capture. Fail-open: NO verdict is applied, and the failure is
            # recorded rather than swallowed. `verdicts = {}` leaves every
            # decision the gate already took in place — including a mechanical
            # `#4899` DISCARD — so the warning must name what SURVIVED, not
            # claim a keep that did not happen (#10072 review).
            verdicts, arbiter_batch = {}, {}
            survived = sum(
                1 for d in decisions.values() if d["outcome"] == DISCARD)
            warnings.append(
                f"vet arbiter failed ({type(e).__name__}: {e}) — no verdict "
                f"applied (fail-open); the gate's own decisions stand "
                f"({survived} DISCARD of {len(candidates)} candidate(s))")
        for iid, verdict in verdicts.items():
            if iid not in decisions:
                continue
            # ⛔ ``NOOP`` IS DEFINED AS "IT CHANGES NOTHING" — see the module
            # vocabulary above — so it must never FLIP an outcome, and in
            # particular must never rescind a mechanical ``#4899`` DISCARD.
            # Applying it unconditionally did exactly that: an armed seam's
            # uncertain/fused band emits ``NOOP``, which turned
            # ``DISCARD/value.identifier_only.hash`` into ``NOOP/seam.arbiter``
            # — the item surviving and the predicate's rule id erased. That is
            # the SAME harm the degraded path is guarded against (no usable
            # answer must not protect what a working predicate dropped); an
            # "outage" is not the only way to emit no opinion (#10072 review).
            # ``KEEP``/``DISCARD``/``MERGE`` still override — those are
            # decisions, and overriding the heuristic is the seam's job.
            incoming = str(verdict.get("outcome") or "").upper()
            standing = str(decisions[iid].get("outcome") or "").upper()
            if incoming == NOOP and standing == DISCARD:
                continue
            # ``RULE_FAIL_OPEN`` is the ABSENCE of a decision (the gate's default
            # row), not a standing one — so agreement applies only against a
            # decision some rule actually made. Scoping this to ``incoming ==
            # standing`` alone recorded a REAL ``KEEP`` verdict as
            # ``rule_id: vet.fail_open / reason: "no verdict"`` (the default row
            # it happened to match), so the artifact claimed no verdict was given
            # when one was and "why was this kept?" became unanswerable per item.
            # Verified: KEEP at noul 0.93 must keep ``seam.arbiter`` and its band
            # reason, and only a genuine AGREEMENT over a real decision takes the
            # branch below (#10072 review).
            standing_rule = str(decisions[iid].get("rule_id") or "")
            if incoming == standing and standing_rule != RULE_FAIL_OPEN:
                # AGREEMENT, not a new decision. Replacing ``rule_id`` here made
                # the decision record contradict the mechanical census on the
                # SAME drop (record: ``DISCARD/seam.arbiter``; census:
                # ``by_rule {value.identifier_only.hash: 1}, overridden: 0``) —
                # so "which rule dropped this?" got two answers. The predicate's
                # rule id is the evidence for why it went; the seam's agreement
                # is recorded beside it, additively.
                decisions[iid]["agreed_by"] = str(
                    verdict.get("rule_id") or RULE_ARBITER)
                continue
            decisions[iid].update(verdict)

    if arbiter_batch:
        outcome = str(arbiter_batch.get("outcome") or "").strip().upper()
        if outcome in BATCH_OUTCOMES:
            batch["outcome"] = outcome
            batch["outcome_reason"] = str(arbiter_batch.get("reason") or "")

    # The counterfactual: what a DISCARD removes, stated so it is recoverable.
    for d in decisions.values():
        if d["outcome"] == DISCARD:
            d["counterfactual"] = (
                f"would have shipped as a {d['section']} "
                f"({d['rule_id']}): {d['text'][:120]!r}")
        else:
            d["counterfactual"] = ""

    # An arbiter that overrode a mechanical hit means the predicate did NOT
    # decide this candidate. Counting it as ``fired`` would attribute a discard
    # to a rule that produced none (``fired`` > ``discarded``, unexplained) —
    # the opposite of what the evidence key exists for. Prune, and report the
    # overrides separately so the rescue is visible rather than hidden.
    overridden = [iid for iid in mechanical
                  if decisions[iid]["outcome"] != DISCARD]
    for iid in overridden:
        mechanical.pop(iid)

    by_outcome: dict[str, int] = {}
    for d in decisions.values():
        by_outcome[d["outcome"]] = by_outcome.get(d["outcome"], 0) + 1
    stats = {
        "candidates": len(decisions),
        "by_outcome": by_outcome,
        "discarded": by_outcome.get(DISCARD, 0),
        "arbiter": "present" if arbiter is not None else "none",
        # #4899: additive. ``enabled`` reports the RESOLVED value, so the
        # evidence surface says whether the predicate ran — it is ``True`` on a
        # default run since #10996 (the mechanical half defaults ON) and ``False``
        # only under the explicit ``TORTOISE_VALUE_GATE=0`` kill-switch.
        "mechanical": {
            "enabled": value_gate.value_gate_enabled(),
            "fired": len(mechanical),
            "overridden": len(overridden),
            "by_rule": {
                r: sum(1 for v in mechanical.values() if v["rule_id"] == r)
                for r in value_gate.RULES
            },
        },
    }
    if seam_stats:
        # #5087: the arbiter's own record (spend, model, prompt version, bands,
        # per-question refusals) — present only when the arbiter reports one, so
        # an arbiter-less run is byte-identical to before.
        stats["seam"] = seam_stats
    return {"decisions": decisions, "batch": batch, "stats": stats,
            "warnings": warnings}


# ── Applying the verdicts — the removal, and the Layer-1 guard ─────────────

def _referenced_entity_names(embed_list: object,
                             discarded_ids: set[str], *,
                             relation_endpoints: bool = True) -> set[str]:
    """Entity names referenced by any candidate that is NOT being discarded.

    This is the P0 guard's input. Only *surviving* items are scanned: an
    entity referenced solely by a discarded point may safely go with it.
    Scans ``about_entities`` and the subject/object ``slots`` of every
    surviving candidate. Operator endpoints are NOT scanned *here* — they name
    points/events, never entities (``commit_schema``); the operator prune in
    :func:`apply_vet` handles the removed-entity-endpoint case separately. A
    shape we do not recognise contributes nothing, which is safe because the
    guard's only failure mode is keeping too much.

    ``relation_endpoints`` (#10999) decides WHICH WARNING is truthful about the
    entity↔entity channel's endpoints — never whether an endpoint protects its
    entity. Both the DISCARD guard and the Rule-4 RESTORE leg count relation
    endpoints as references (the default ``True``); the channel is **result-level**
    — an endpoint naming an entity no pass emitted leaves ``execute_embed``'s
    ``relations`` unresolved, dropped and named (the declared fail-open), and the
    Layer-1 payload never carries it, so it cannot 422. ``False`` therefore asks
    the narrower question "is this entity referenced by something Layer-1 CAN
    reject?", which only the DOWNGRADE message may answer with "Layer-1
    referential integrity"; passing it into the restore computation is the
    defect #10999 fixed — the entity stayed gone and S5 then dropped the edge
    as unresolvable, with nothing in the record saying why.
    """
    names: set[str] = set()
    for section, index, item in _iter_items(embed_list):
        if _item_id(section, index, item) in discarded_ids:
            continue
        refs = item.get("about_entities")
        if isinstance(refs, Sequence) and not isinstance(refs, (str, bytes)):
            for a in refs:
                if isinstance(a, str) and a.strip():
                    names.add(_norm(a))
        slots = item.get("slots")
        if isinstance(slots, Mapping):
            for role, refs in slots.items():
                if role == "event" or not isinstance(refs, Sequence):
                    continue
                for r in refs:
                    if isinstance(r, Mapping) and r.get("name"):
                        names.add(_norm(r["name"]))
    # #10999: an entity an entity↔entity relation NAMES is referenced. The
    # per-item scan above reads `about_entities`/`slots` only, so an entity
    # whose sole tie to the emission is a relation's endpoint looked
    # unreferenced and could be DISCARDed — and S5 would then drop the
    # relation as unresolvable, one stage after this guard authorised it.
    #
    # ⚠️ `_norm(ref)` ALONE leaves this arm half-closed. S5 resolves the
    # endpoint on EITHER fold (``extractor_v2._endpoint_fold_keys`` — `_norm`,
    # which collapses a whitespace RUN, and `.casefold()`, which folds ß/İ;
    # neither is a superset of the other), while this module's key is in
    # ``_norm`` space and its probe adds ``.casefold()`` — the very pair the
    # ``_norm`` docstring above names as DIVERGENT on ('Straße', 'İ'). For a
    # spelling variant the two folds disagree on, S5 resolves "STRASSE" →
    # "Straße" and ``_norm("STRASSE")`` is "strasse" ≠ "straße", so the
    # entity whose ONLY tie is that relation is still DISCARDed — the gap this
    # arm exists to close, one spelling variant in. Carry every emitted entity
    # the endpoint could resolve to instead; keeping too much is this guard's
    # only safe failure mode.
    #
    # ⛔ THIS IS A THIRD SHAPE OF THE FOLD, and a COPY by construction: this
    # module is stdlib-only and may not import `extractor_v2`, so
    # `_endpoint_fold_keys`' pair cannot be called here. The index below is
    # `_norm(name).casefold()` with two probes — NOT the emitter's two-key pair —
    # so "the identity rule has one home" holds for the two RESOLVER stages and
    # NOT for this guard. Measured over a 14x20 spelling matrix: 0 S5-resolvable
    # pairs are left unprotected, 2 are over-protected ('Straße  X' /
    # 'STRASSE X'), and over-protecting is this guard's declared safe direction —
    # it can keep an entity whose relation S5 then drops, never mint a wrong edge.
    if not relation_endpoints:
        return names
    emitted: dict[str, set[str]] = {}
    for entity in _section_items(embed_list, "entities"):
        if isinstance(entity, Mapping):
            key = _norm(entity.get("name"))
            if key:
                emitted.setdefault(key.casefold(), set()).add(key)
    for rel in _section_items(embed_list, "relations"):
        if not isinstance(rel, Mapping):
            continue
        for side in ("src", "dst"):
            ref = rel.get(side)
            if not (isinstance(ref, str) and ref.strip()):
                continue
            names.add(_norm(ref))
            # both folds, because the emitter's own key may carry either
            for probe in (ref.strip().casefold(), _norm(ref).casefold()):
                names |= emitted.get(probe, set())
    return names


def _item_content(section: str, item: Mapping[str, Any]) -> str:
    """The item's CONTENT — the surface ``execute_embed`` resolves operators on.

    Deliberately different from :func:`_item_text` (which is the extractor's
    candidate-identity rule, ``name or content``). A point/event id is
    ``_content_id(...)`` over its ``content`` and ``_resolve`` looks up
    ``point_ids[_norm(content)]``, so the operator prune MUST match on content:
    an item carrying BOTH keys would otherwise record its name as "removed"
    and leave an operator on its content unpruned, to be re-minted. Entities
    carry no content and contribute nothing here.
    """
    if section == "entities" or "content" not in item:
        return ""
    return str(item.get("content")).strip()


def _item_text_variants(section: str, item: Mapping[str, Any]) -> set[str]:
    """Normalized texts the embedder can key OR mint an endpoint from.

    The UNION of the candidate identity (``name or content``) and the content:
    `execute_embed` *resolves* an endpoint on content but its #2552 mint pre-pass
    materializes whatever text the operator wrote — so a point carrying both keys
    can be re-materialised from either. Only a survivor's CONTENT shields an
    endpoint; its name does not resolve in ``execute_embed``, so it must not.
    Keyed with :func:`_resolution_variants` (the mint's RESOLUTION keys), never
    :func:`_norm_variants` — see that helper for why the entity-closure arm must
    not leak onto a content surface.
    """
    return (_resolution_variants(_item_text(section, item))
            | _resolution_variants(_item_content(section, item)))


def _content_texts(embed_list: object) -> set[str]:
    """Normalized CONTENT of every point/event — the embedder's RESOLUTION
    surface (``_resolve`` keys ``point_ids``/``event_ids`` by content)."""
    out: set[str] = set()
    for section, _index, item in _iter_items(embed_list):
        out |= _resolution_variants(_item_content(section, item))
    return out


def _survives(section: str, item: Mapping[str, Any],
              after: object) -> bool:
    """Whether ``item`` is still present in ``after`` (value equality).

    A *removal* is an absent item, not a text that is merely absent from a
    surface — the distinction the carry-forward pool rests on. Equality rather
    than identity: a list may be rebuilt around equal dicts, and a value-equal
    item is the same item for every purpose here.

    Assumes JSON-shaped items (acyclic, from the extractor) — the shape both
    ends of the comparison in fact have (``_run_vet_pass`` compares a list to
    its own post-``apply_vet`` revision, which keeps survivors by identity). A
    comparison that RAISES is treated as *survived*, the fail-open direction: a
    wrong keep is noise, a wrong drop is memory loss. A non-reflexive value
    (``float('nan')`` in a rebuilt list) is the one case that compares unequal
    without raising and would read as a removal; it needs a hand-built list to
    reach, since the extractor's lists do not carry NaN.
    """
    for other in _section_items(after, section):
        if not isinstance(other, Mapping):
            continue
        try:
            if item == other:
                return True
        except Exception:  # an incomparable value is not a removal
            return True
    return False


def _entity_map(embed_list: object) -> dict[str, Mapping[str, Any]]:
    """Normalized entity name → the entity item, for every emitted entity."""
    out: dict[str, Mapping[str, Any]] = {}
    for item in _section_items(embed_list, "entities"):
        if not isinstance(item, Mapping):
            continue
        name = _norm(item.get("name"))
        if name:
            out.setdefault(name, item)
    return out


def _operator_endpoint_key_sets(
        op: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    """``(content_keys, entity_keys)`` an operator endpoint names.

    The prune matches each half against its OWN provenance of ``gone`` —
    ``content_keys`` (:func:`_resolution_variants`, the mint's RESOLUTION keys)
    against the removed point/event CONTENT keys, and ``entity_keys``
    (:func:`_norm_variants`, which ALSO covers the mint's ENTITY-GUARD ref keys)
    against the removed ENTITY keys. Matching one MERGED set against the union
    is wrong in both directions: drop the entity half and a ref naming a
    DISCARDED >cap entity through its collapsed-closure arm escapes the prune,
    so the mint fabricates a claim Point from the participant name (#5069
    re-review, P1a); keep it merged and the same closure arm can coincide with a
    removed CONTENT key, dropping an operator the mint would have kept, with a
    false "whose endpoint was discarded" warning (#5069 re-review, P1b).

    ``src``/``dst`` are read whichever ``op_type`` an operator carries (the
    embedder reads them unconditionally). The target — read as
    ``op.get("target") or op.get("target_edge")``, the **first present, not a
    union** — is read **only for a MITIGATES**, the only ``op_type`` the
    embedder reads a target for (``if _op_type == "MITIGATES"``). Reading it for
    every operator would drop a valid edge on a field the embedder never looks
    at — an IMPL carrying a stray ``target`` naming a discarded point would be
    pruned, losing the edge. Endpoints are coerced with the embedder's
    own ``str(v or "")`` so a NON-STRING endpoint (an LLM can emit ``42``) is not
    skipped and left to be re-minted, and a target is honoured only when it is a
    ``dict``, exactly as the embedder requires.
    """
    content: set[str] = set()
    entity: set[str] = set()

    def _add(v: object) -> None:
        content.update(_resolution_variants(v))
        entity.update(_norm_variants(v))

    for key in ("src", "dst"):
        v = op.get(key)
        if v and str(v).strip():
            _add(v)
    if str(op.get("op_type", "")).upper() == "MITIGATES":
        target = op.get("target") or op.get("target_edge")
        if isinstance(target, dict):
            for key in ("src", "dst"):
                v = target.get(key)
                if v and str(v).strip():
                    _add(v)
    return content, entity


def _operator_endpoint_text(op: Mapping[str, Any]) -> set[str]:
    """Every text an operator endpoint names, both provenances UNIONed.

    Convenience for callers that only need the union (and the drift test that
    pins the MITIGATES-target read). :func:`apply_vet` uses
    :func:`_operator_endpoint_key_sets` to match each provenance against its own
    ``gone`` half.
    """
    content, entity = _operator_endpoint_key_sets(op)
    return content | entity


def removal_pool(before: object, after: object) -> dict:
    """The carry-forward record of one pass's removals.

    ``{"removed_texts": set[str], "removed_entities": {name: item}}``. The gate
    runs twice; without carrying the first pass's removals forward, the union
    pass cannot prune an operator that S4 re-added against an earlier-discarded
    item, nor restore an entity S4 started referencing — both of which put the
    discarded content back into the payload (a resurrected Point, and a Layer-1
    422).

    Derived by diff, not by re-reading the verdicts, so a *downgraded* discard
    (the Layer-1 guard) correctly contributes nothing to the pool.

    **A removal is an absent ITEM, not a text missing from a surface.** Every
    collected text is the identity *and* content of an item with no value-equal
    counterpart in ``after``, because the #2552 mint materialises whichever form
    an operator wrote. Each text is keyed with :func:`_resolution_variants` (the
    mint's RESOLUTION keys); the ENTITY-closure arm belongs to
    :func:`_norm_variants` and must not leak onto this CONTENT surface. Deriving
    the set from a surface difference would invent
    removals for every surviving item that carries a ``name`` — a name is in the
    identity surface and not in the content surface — and prune operators naming
    a survivor.

    Whether a surviving item *shields* one of these texts is decided in
    :func:`apply_vet`, on the RESOLUTION surface — the ``content`` of surviving
    points/events (:func:`_content_texts`, which by construction carries no
    names), plus the names of entities present in the output *as the mint keys
    them*. A surviving point's or event's *name* does not shield: the embedder
    does not resolve an endpoint on it. A present ENTITY's name does, for a
    different reason — the embedder drops an entity-named endpoint rather than
    minting a Point for it, so pruning would only mis-attribute the drop. That
    shield is keyed with :func:`_norm_variants`, a SUPERSET of the mint's guard
    set (it also carries the mint's untruncated resolution alias), so it is
    never NARROWER than the guard — see :func:`apply_vet` for the exact
    criterion.
    """
    before_entities = _entity_map(before)
    after_entities = _entity_map(after)
    removed_texts: set[str] = set()
    for section, _field, _kind_field, _family in SECTIONS:
        if section == "entities":
            # Entities carry no content; a removed entity's name travels in
            # ``removed_entities`` and reaches the prune through its keys.
            continue
        for item in _section_items(before, section):
            if not isinstance(item, Mapping):
                continue
            if not _survives(section, item, after):
                removed_texts |= _item_text_variants(section, item)
    return {
        "removed_texts": removed_texts,
        "removed_entities": {n: it for n, it in before_entities.items()
                             if n not in after_entities},
    }


def _rewrite_entity_references(embed_list: Mapping[str, Any],
                               restored: Mapping[str, str]) -> None:
    """Re-spell a reference to the emitted entity's exact name.

    ``validate_layer1`` compares ``about_entities`` and slot names by EXACT
    string, so an entity named ``pytest`` does not satisfy a point naming
    ``PyTest`` — the session still 422s. Rewriting is a KEEP action
    (fail-open): the reference means the same entity; only its spelling is
    canonicalised. Immutable mappings are skipped rather than raising.
    """
    def _fix(value: Any) -> Any:
        return restored.get(_norm(value), value) if isinstance(value, str) \
            else value

    for _section, _index, item in _iter_items(embed_list):
        if not isinstance(item, MutableMapping):
            continue
        for key in ("about_entities",):
            refs = item.get(key)
            if isinstance(refs, Sequence) and not isinstance(refs,
                                                            (str, bytes)):
                item[key] = [_fix(r) for r in refs]
        slots = item.get("slots")
        if isinstance(slots, Mapping):
            for role, slot_refs in slots.items():
                if role == "event" or not isinstance(slot_refs, Sequence) \
                        or isinstance(slot_refs, (str, bytes)):
                    continue
                for r in slot_refs:
                    if isinstance(r, MutableMapping) \
                            and isinstance(r.get("name"), str):
                        r["name"] = _fix(r["name"])


def apply_vet(embed_list: Mapping[str, Any],
              decisions: Mapping[str, Mapping[str, Any]],
              *, prior: Mapping[str, Any] | None = None
              ) -> tuple[dict, list[str]]:
    """Apply VET verdicts, returning ``(new_embed_list, warnings)``.

    ``prior`` is the previous pass's :func:`removal_pool` (S2 → union).

    Rules, in priority order:

    1. **Only an explicit ``DISCARD`` removes anything.** A missing decision, a
       ``MERGE``, a ``NOOP`` or a ``KEEP`` all leave the item in place. (``MERGE``
       is *recorded* by the caller but not applied here — merging is #5006's.)
    2. **A referenced entity is never removed.** An entity that a surviving
       candidate references **is a referent**; dropping it would fail
       ``validate_layer1`` (``about_entities ⊆ entities``) and 422 the whole
       session. The discard is downgraded to ``KEEP`` with a warning.
    3. **Operators that reference a removed item are pruned.** The prune is
       **load-bearing, not cosmetic**: ``execute_embed`` has a MINT-BEFORE-WIRE
       pre-pass (#2552) that materializes an unresolved endpoint as a **new
       statement Point**, so an unpruned operator silently *resurrects* the
       discarded candidate. It reads an operator's
       endpoints exactly as the embedder does: ``src``/``dst`` with ``str(v or
       "")`` coercion, and — **only for a MITIGATES** — the target, as
       ``target or target_edge`` (the first present, not a union). It also
       includes the names of removed entities (an endpoint naming a removed
       entity would fabricate a claim Point out of a participant name, defeating
       #2552's own guard). It fires only when **no surviving item provides the
       endpoint** — discarding one of two identical-content items must not take
       the survivor's edge with it.
    4. **A referenced-but-missing entity is restored** from ``prior``, and the
       reference's spelling is reconciled to the emitted name. S4 runs between
       the two passes and can reference an entity the S2 pass removed; that
       cross-pass reference is invisible to a per-pass guard and produces a
       Layer-1 422. ``validate_layer1`` compares ``about_entities``
       and slot names by EXACT string, so an entity named ``pytest`` with a
       point naming ``PyTest`` 422s — whether the entity was *restored* (S4's
       reference arrived after the removal) or *downgraded to KEEP* by Rule 2
       (the same-pass case). Both go through the same reconciliation. Fail-open:
       keep, never drop.

    The function is **total** on a malformed section shape (a non-sequence
    section, a non-sequence ``operators``, a non-mapping item, a non-iterable
    ``about_entities``, an immutable ``Mapping`` slot ref) and on a malformed
    ``prior``/``decisions`` map: no exception.
    """
    warnings: list[str] = []
    base: Mapping[str, Any] = embed_list if isinstance(embed_list, Mapping) \
        else {}
    prior_map: Mapping[str, Any] = prior if isinstance(prior, Mapping) else {}
    raw_texts = prior_map.get("removed_texts")
    prior_texts: set[str] = (
        {_norm(t) for t in raw_texts}
        if isinstance(raw_texts, (list, tuple, set, frozenset)) else set())
    raw_entities = prior_map.get("removed_entities")
    prior_entities: dict[str, Mapping[str, Any]] = (
        {str(k): v for k, v in raw_entities.items() if isinstance(v, Mapping)}
        if isinstance(raw_entities, Mapping) else {})
    decision_map: Mapping[str, Any] = decisions if isinstance(decisions,
                                                             Mapping) else {}

    discarded_ids = {iid for iid, d in decision_map.items()
                     if isinstance(d, Mapping)
                     and str(d.get("outcome") or "").upper() == DISCARD}
    if not discarded_ids and not prior_texts and not prior_entities:
        # Nothing to do — return a shallow copy whose contents are identical
        # to the input, so the flag-off / nothing-discarded paths are
        # byte-identical downstream (the same object is never mutated).
        # ⚠️ ``prior_texts`` is part of the test: a prior that removed only
        # points/events has an EMPTY ``removed_entities``, and early-returning
        # on that alone skipped the operator prune — `prior_texts` must be part
        # of the test.
        return dict(base), warnings

    referenced = _referenced_entity_names(base, discarded_ids)
    # The Layer-1 half of the same set, and the ONLY half a downgrade may
    # truthfully cite as "Layer-1 referential integrity". A relation endpoint
    # protects its entity (the edge is the story's assertion and would vanish
    # with it) but the channel is RESULT-level: no Layer-1 reference names the
    # entity, so it cannot produce a 422 (#10999). ⛔ This narrower set is for
    # the WORDING only — the Rule-4 restore below still counts relation
    # endpoints, because that leg is the only one that can bring back an entity
    # an earlier pass removed, and S4 supplies the narrative's conclusion
    # between the two passes.
    layer1_referenced = _referenced_entity_names(
        base, discarded_ids, relation_endpoints=False)
    removed_entity_names: set[str] = set()
    removed_context: set[str] = set()      # norm CONTENT of removed points/events
    # norm entity name -> the spelling that must win in a reference. Populated
    # by BOTH a Layer-1 downgrade (the entity is kept) and a Rule-4 restore: in
    # either case the entity is in the output, so a reference spelled differently
    # must be reconciled or ``validate_layer1`` still 422s (see
    # ``_rewrite_entity_references``).
    canonical: dict[str, str] = {}
    # Start from a full copy so non-VET keys (operators, chain_notes,
    # link_before_create, …) are preserved — only the three candidate sections
    # are rewritten.
    out: dict[str, Any] = dict(base)
    downgraded = 0

    for section, _field, _kf, family in SECTIONS:
        kept: list[Any] = []
        for i, item in enumerate(_section_items(base, section)):
            if not isinstance(item, Mapping):
                kept.append(item)
                continue
            iid = _item_id(section, i, item)
            if iid not in discarded_ids:
                kept.append(item)
                continue
            if not _item_text(section, item):
                # An empty-text item is not a candidate, so no arbiter verdict
                # can legitimately address it: ``vet_candidates`` never emitted
                # its id. Refuse the removal (the id space must not be a way to
                # discard something the arbiter never saw).
                kept.append(item)
                continue
            if family == "entity":
                name = _norm(item.get("name"))
                if name and name in referenced:
                    downgraded += 1
                    # STRIP: execute_embed emits ``str(name).strip()`` and
                    # validate_layer1 matches that exact string, so a padded
                    # entity name rewritten verbatim would CREATE the 422 the
                    # reconciliation exists to prevent.
                    canonical[name] = str(item.get("name") or "").strip()
                    if name in layer1_referenced:
                        warnings.append(
                            f"vet: entity {str(item.get('name'))!r} was "
                            "DISCARDed but is referenced by a surviving "
                            "candidate — kept (Layer-1 referential integrity)")
                    else:
                        # A relation endpoint is the entity's only tie to the
                        # emission. The edge is kept, the 422 claim is not:
                        # `relations` never enters the Layer-1 payload.
                        warnings.append(
                            f"vet: entity {str(item.get('name'))!r} was "
                            "DISCARDed but is named by a surviving "
                            "entity↔entity relation — kept (the relation is "
                            "the story's own assertion; this is NOT a "
                            "Layer-1 reference)")
                    kept.append(item)
                    continue
                if name:
                    # #5069: THIS PASS's gone-set entry carries every form the
                    # mint keys a SPELLING of the name under, not just the full
                    # normalised name. A truncated spelling of a discarded
                    # >1000-char name otherwise escapes ``gone`` — and the mint
                    # cannot see a discarded entity (it is in neither the
                    # emitted set nor the S3 index), so it would fabricate a
                    # Point from it. (The pool stores the full name; the
                    # cross-pass leg expands it from the raw item below.)
                    removed_entity_names |= _norm_variants(item.get("name"))
            else:
                removed_context |= _item_text_variants(section, item)
            warnings.append(
                f"vet: discarded {section} "
                f"{_item_text(section, item)[:80]!r}")
        out[section] = kept

    # Rule 4 — restore an entity an earlier pass removed and a later reference
    # now needs, and reconcile the reference's SPELLING. Computed BEFORE the
    # prune: a restored name is present again, so it must not be pruned as if
    # it were discarded.
    present_map = _entity_map(out)
    present = set(present_map)
    # #10999: relation endpoints DO count for the restore — a relation naming an
    # entity an earlier pass removed is exactly the cross-pass case Rule 4
    # exists for (S4 supplies the conclusion between the two passes). The
    # `relation_endpoints` flag splits the WARNING, not the restore: it was
    # applied to this whole computation once, so the entity stayed gone and S5
    # then dropped the edge as unresolvable — with nothing in the record saying
    # why. `layer1_referenced` is what may truthfully be called a Layer-1
    # reference; a relation endpoint is result-level and cannot 422.
    layer1_referenced = _referenced_entity_names(
        out, set(), relation_endpoints=False)
    to_restore = _referenced_entity_names(out, set()) - present
    # The DISCARD arm above matches an endpoint on EITHER fold; the RESTORE
    # lookup must be fold-complete too, or the cross-pass case this arm exists
    # for fails one spelling variant in: `prior_entities` is keyed by `_norm`
    # (see `_entity_map`), so a casefold-only endpoint spelling ('STRASSE' vs
    # 'Straße', 'İ' vs 'i') restored nothing and S5 then dropped the edge as
    # unresolvable. Unique-or-nothing, and never a SECOND copy of an entity
    # this pass already holds under a different casefold.
    # Held spellings, by casefold, kept CURRENT as restores land (a restore
    # appends to `out["entities"]`, and a stale snapshot is how a later
    # reference in the same loop is left unreconciled).
    held_folded: dict[str, str] = {}
    for _key, _item in present_map.items():
        _spelling = str(_item.get("name") or "").strip()
        if _spelling:
            held_folded.setdefault(_key.casefold(), _spelling)
    restored_keys: set[str] = set()
    for ref_name in sorted(to_restore):
        name = ref_name
        # A RELATION endpoint whose entity this pass already holds under another
        # casefold is the same entity: restoring the prior copy would put two
        # Objects in the emission, make S5's fold index ambiguous, and drop the
        # very edge the arm exists to save. Layer-1 references are NOT skipped
        # — payload identity is EXACT (`(str(name).strip(), kind)`), so when
        # `prior` can supply the spelling a point/`slots` reference actually
        # names, restoring it is correct and re-pointing the reference at a
        # casefold sibling would silently attribute the statement to a
        # DIFFERENT entity (this module's own doctrine: guessing is worse than
        # a miss).
        if ref_name not in layer1_referenced \
                and name.casefold() in held_folded:
            # The append is skipped, and the reference is recorded so the Layer-1
            # surfaces `_rewrite_entity_references` actually rewrites
            # (`about_entities`, `slots`) name the held spelling. ⛔ It does NOT
            # rewrite a RELATION endpoint: `_iter_items` walks
            # `entities`/`events`/`points` only, so `relations[].src/dst` keep
            # the spelling as written and rely on S5's own endpoint fold — a
            # future consumer of `embed_list["relations"]` that matches EXACTLY
            # would see the unreconciled name.
            canonical[ref_name] = held_folded[name.casefold()]
            continue
        original = prior_entities.get(name)
        if original is None:
            folded = [key for key in prior_entities
                      if key.casefold() == name.casefold()]
            if len(folded) == 1 and folded[0].casefold() not in held_folded:
                name = folded[0]
                original = prior_entities[name]
        if original is not None and str(original.get("name") or ""):
            # STRIP, as above: the emitted name is stripped (see the downgrade
            # branch for why). Stamped on the REFERENCE's key: the folded prior
            # key is not what a reference's `_norm` form looks up.
            canonical[ref_name] = str(original["name"]).strip()
            if name in restored_keys:
                # Two divergent references can both resolve to ONE prior entry
                # (the direct hit and a folded sibling): reconcile, never append
                # a second copy or repeat the warning.
                continue
            restored_keys.add(name)
            out.setdefault("entities", [])
            if isinstance(out["entities"], list):
                out["entities"].append(original)
            # Re-stamp: two divergent spellings can both be in `to_restore`, and
            # the second must not restore the same entity again.
            held_folded.setdefault(name.casefold(), canonical[ref_name])
            if ref_name in layer1_referenced or name in layer1_referenced:
                warnings.append(
                    f"vet: entity {name!r} was discarded by an earlier pass "
                    "but is referenced after S4 — restored (Layer-1 "
                    "referential integrity)")
            else:
                warnings.append(
                    f"vet: entity {name!r} was discarded by an earlier pass "
                    "but an entity↔entity relation now names it — restored "
                    "(the relation is the story's own assertion; this is NOT "
                    "a Layer-1 reference)")
        elif ref_name in layer1_referenced:
            # `prior` cannot supply this spelling, but the emission may already
            # HOLD the entity under a casefold sibling — and payload identity
            # is EXACT, so leaving the reference as written makes
            # `validate_layer1` reject the whole session ("about_entities
            # reference unknown entities"), where re-spelling it onto the held
            # entity is both valid and what the reference names to every other
            # stage. Reconcile when a held spelling exists; warn only when
            # nothing can be reconciled (then the 422 is real and named).
            held = held_folded.get(name.casefold())
            if held:
                canonical[ref_name] = held
                # ⚠️ RESIDUAL (review round 10, P3, NOT fixed here): this
                # re-spelling emits no warning. It is deliberate — payload
                # identity is EXACT, so the reference as written would fail
                # Layer-1 — but the held spelling may be a DIFFERENT entity
                # (Layer-1 identity is `(name, kind)`), and the doctrine 50
                # lines above says "guessing is worse than a miss". The arm was
                # not reproduced by probe at 4ff6f2310 (to_restore carried the
                # reference and `held_folded` held the sibling, yet this branch
                # produced neither a rewrite nor a warning), so the disclosure
                # is left as a follow-up rather than claimed as fixed.
            else:
                warnings.append(
                    "vet: ⚠️ a surviving candidate references entity "
                    f"{name!r}, which no pass emitted — it cannot be restored "
                    "here; the payload will fail Layer-1 unless the extractor "
                    "emits it")
        # else: a relation endpoint naming an entity no pass emitted restores
        # nothing and warns nothing — it cannot 422 (result-level channel), and
        # S5 names the dropped relation itself.
    if canonical:
        _rewrite_entity_references(out, canonical)

    # Operators: drop any whose endpoint referenced a removed point/event —
    # including items an EARLIER pass removed (they are absent from this list,
    # so the diff alone cannot see them). Only fire when no surviving item
    # provides that text: an identical-content survivor still resolves the
    # endpoint, and pruning would lose its edge.
    surviving_texts = _content_texts(out)
    # An operator naming a SURVIVING entity is not pruned either: the name is
    # in the embedder's ``emitted_entity_names``, so #2552 cannot fabricate a
    # Point for it, and the embedder drops the operator itself (its OPERATOR
    # REFERENCING rule forbids entity endpoints). Without this, a name that
    # entered ``gone`` from an EARLIER pass's ``removed_entities`` — or from a
    # same-pass discard of a duplicate-name entity — pruned an operator whose
    # endpoint was present, with a warning claiming it "was discarded".
    #
    # The shield must key AT LEAST as broadly as the mint's refusal set: a
    # narrower shield prunes an entity-named endpoint and mis-reports it as
    # "whose endpoint was discarded". Keying broader is safe here, because every
    # extra alias the shield carries is matched by the mint's guard for that same
    # ref (the mint re-keys the ref through the truncated arms below), so nothing
    # it shields can be fabricated. #5069 canonicalised the guard key on BOTH
    # sides:
    # ``extractor_v2._endpoint_keys`` is the mint transform
    # ``_norm(x.strip()[:_MAX_CONTENT])`` applied to the raw and to the
    # whitespace-collapsed spelling of x, the mint's entity sets are built with
    # it, and the mint's guard is a set intersection against it — so
    # ``_norm_variants`` here (the same two forms PLUS the untruncated
    # resolution alias, i.e. a superset) covers the mint's own key set, and a
    # present entity shields its name in either spelling at ANY length.
    # The former ``_norm(raw[:1000]) == name`` test shielded only when
    # truncation left the normalised name unchanged, so it pruned operators
    # naming a present entity — the false "whose endpoint was discarded"
    # warning this shield exists to prevent.
    present_entity_names: set[str] = set()
    for _ent in _section_items(out, "entities"):
        if isinstance(_ent, Mapping):
            present_entity_names |= _norm_variants(_ent.get("name"))
    # #5069: the pool stores a removed entity under its FULL normalised name
    # (``_entity_map`` keys it that way for Rule 4's restore lookup), but the
    # ENTITY half of the prune is matched against the endpoint's
    # ``_norm_variants`` keys. Expand from the stored ITEM's raw name — expanding
    # the already-collapsed KEY would lose the raw arm and leave a cap-truncated
    # raw-prefix ref unpruned on this leg alone. That is what lets a
    # >1000-char entity discarded in an earlier pass still be pruned when a
    # later pass re-names it — the cross-pass leg of the guard.
    prior_entity_keys = {k for _item in prior_entities.values()
                         for k in _norm_variants(_item.get("name"))}
    # Match each endpoint provenance against its OWN ``gone`` half (#5069
    # re-review, P1b): a removed point/event contributes the mint's RESOLUTION
    # keys, a removed entity contributes its mint-GUARD keys. One MERGED set
    # over-prunes when an endpoint's entity-closure arm coincides with a removed
    # CONTENT key (an operator the mint would have kept, dropped with a false
    # "whose endpoint was discarded" warning). The shields subtract from BOTH
    # halves, exactly as the single `gone` did.
    _shield = surviving_texts | set(canonical) | present_entity_names
    content_gone = (removed_context | prior_texts) - _shield
    entity_gone = (removed_entity_names | prior_entity_keys) - _shield
    if content_gone or entity_gone:
        ops = _as_items(out.get("operators"))
        kept_ops: list[Any] = []
        pruned = 0
        for op in ops:
            if isinstance(op, Mapping):
                content_keys, entity_keys = _operator_endpoint_key_sets(op)
                if (content_keys & content_gone) or (entity_keys & entity_gone):
                    pruned += 1
                    continue
            kept_ops.append(op)
        if pruned:
            out["operators"] = kept_ops
            warnings.append(
                f"vet: pruned {pruned} operator(s) whose endpoint was "
                "discarded")

    if downgraded:
        warnings.append(f"vet: {downgraded} discard(s) downgraded to KEEP")
    return out, warnings


# ── The draft-and-run instrument (D13/O5) ─────────────────────────────────

def audit_candidates(embed_list: object,
                     decisions: Mapping[str, Mapping[str, Any]]) -> str:
    """Render one line per candidate for the tens-of-real-items hand review.

    This is the **report**, not a gate. Its output is what the owner reviews to
    set the thresholds the design refuses to invent (D13/O5: draft → run →
    look → refine). One line per candidate — the same predicate as
    :func:`vet_candidates`, so the report carries one row per candidate (plus
    a header) and an empty-text item — which no gate saw — is not a blank row.
    Stable order, tab-separated:

        <section>\\t<outcome>\\t<rule_id>\\t<text>\\t<reason>
    """
    lines = ["section\toutcome\trule_id\ttext\treason"]
    decision_map: Mapping[str, Any] = (decisions
                                       if isinstance(decisions, Mapping)
                                       else {})
    for section, index, item in _iter_items(embed_list):
        if not _item_text(section, item):
            continue
        iid = _item_id(section, index, item)
        d = decision_map.get(iid)
        if not isinstance(d, Mapping):
            d = {}
        lines.append("\t".join([
            section,
            str(d.get("outcome") or KEEP),
            str(d.get("rule_id") or ""),
            _item_text(section, item)[:160],
            str(d.get("reason") or ""),
        ]))
    return "\n".join(lines)
