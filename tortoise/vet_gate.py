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
The **mechanical** rule set (file paths, ``the <X>`` definite descriptions,
``#123`` references, CI vocabulary, test counts, hex hashes) belongs to
**#4899**, which adds a 5th ``DISCARD(reason=...)`` outcome to
``classify_consolidation``. That is the mechanism **owner decision #1509 §2.4**
mandates — *"never create a parallel layer doing the same thing with redundant
machinery"*. **This module deliberately does NOT reimplement those rules.**
VET is the **adversarial half** the design names:

    "#4899 specifies the mechanical DISCARD. Necessary, not sufficient: it
    cannot answer 'is this an entity or a reference?'"

The two are complementary — the same policy source (the pack), different
machinery (a regex predicate vs. a decision-only model).

⚠️ **#4899 is the mechanical rule *source*, not the S2.2 mechanical *site*.** It
places its DISCARD in ``classify_consolidation`` (E7), which runs inside
``execute_embed`` — i.e. *after* both classify passes and after RESOLVE, the
exact inverted position §4.2 diagnoses as the bug. Nothing in either change yet
puts the mechanical predicate *before* S3; that placement is still unowned, and
saying otherwise would let a recorded gap disappear.

The arbiter seam is the point (§4.2)
------------------------------------
A **decision-only** model structurally enforces the vocabulary: a ``Choice``
over supplied options cannot return an out-of-vocabulary answer, whereas an
extraction LLM emitting kinds/verdicts inline can only be *hoped* to comply.
``arbiter=None`` ⇒ the step performs only its mechanical Level-2 checks and
returns all-``KEEP``. **No production caller is wired yet** (``vet_arbiter`` is
an injected seam), so with the flag on and no arbiter the step provably decides
nothing — that is the current, honest state.

Vocabulary (owner ruling **O4**, `EXTRACTOR-V4-ARCHITECTURE.md` §16.3 / §16.4)
-----------------------------------------------------------------------------
- ``KEEP`` — ship the candidate.
- ``NOOP`` — a decision was made and it changes nothing (distinct from ``KEEP``
  only in *who* decided; both leave the item in place, so ``apply_vet`` treats
  them identically — a consumer that needs to tell them apart must read the
  recorded outcome, not the list).
- ``DISCARD`` — do not ship; the candidate is removed and its counterfactual
  recorded.
- ``MERGE`` — fold into another candidate.
- ``RENARRATE`` — **batch-level only**: re-extract the batch.

This is VET's **selection** axis and is deliberately distinct from
``extractor_v2.DecisionRecord``'s consolidation axis (``ADD``/``UPDATE``/
``NOOP``/``DELETE``). The token ``NOOP`` is shared, but the two sets are
decisions at different steps and neither module consumes the other's set.

⚠️ ``MERGE-INTO-EXISTING`` is deliberately **NOT** emitted. §16.2 records it as
the **uncorrected A4 defect**: *"A4 is not corrected — ``MERGE-INTO-EXISTING``
is still in VET's outputs, so the VET/S3 circularity stands"*. VET runs before
S3 RESOLVE, and S3 is what *finds* what exists — so "merge into existing" is
circular at this position. A merge candidate is flagged ``MERGE`` and
**recorded in the VET evidence surface only — nothing consumes it yet**: no
code routes a ``MERGE`` verdict to S3, and the merge *itself* is #5006's (it
needs the union-of-attachments machinery and the high bar).

Failure policy (§4.2): FAIL-OPEN
--------------------------------
A wrong keep is noise; a wrong drop is memory loss. Unknown ⇒ ``KEEP``. An
arbiter that raises ⇒ all ``KEEP`` + a warning. The only path to ``DISCARD`` is
an explicit arbiter verdict. The public functions are **total** on a malformed
section shape — a non-sequence section, a non-mapping item, a non-iterable
``about_entities``, ``None`` — and on a malformed ``prior``/``decisions`` map,
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
naming an entity the S2 pass removed) is invisible to a per-pass check and was
verified to produce exactly that 422.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, MutableMapping, Sequence
from typing import Any

# ── The outcome vocabulary (O4) ─────────────────────────────────────────────

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
#: many characters before keying them (the ``str(...).strip()[:1000]`` sites in
#: ``execute_embed`` — event content, point content, minted refs) — cited
#: symbolically, not by line number, because `#5005`'s own edits to that module
#: already invalidated one such citation.
_MAX_CONTENT = 1000


def _norm_variants(text: object) -> set[str]:
    """Normalized forms the embedder can key an endpoint on.

    Both the FULL text and its :data:`_MAX_CONTENT`-char prefix: ``execute_embed``
    keys a point/event id on the truncated content, while an operator's ref is
    also probed untruncated (a minted endpoint registers the untruncated key
    only when the truncated form did not already resolve). A removed item must
    therefore be recognised under EITHER form, or an operator on its content
    escapes the prune and the text is re-materialised as a Point (verified).
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


# ── Level 2 — the batch checks (mechanical, unique to VET) ─────────────────

def check_batch(embed_list: object, narrative: str) -> dict:
    """Level 2 — *did this batch come out right?*

    Two signals, both **reported, never acted on** in v1:

    - **coverage** — candidates emitted vs. statements the narrative carried.
      ``coverage_suspect`` fires only on the unambiguous case (a non-empty
      narrative produced **zero** candidates — a definite loss, no threshold).
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
                                 "rule_id", "reason", "counterfactual"}},
         "batch": {...},            # check_batch() + any arbiter batch verdict
         "stats": {...},            # counts, for the session roll-up
         "warnings": [...]}

    **Fail-open everywhere.** ``arbiter=None`` ⇒ every candidate ``KEEP``.
    An arbiter that raises ⇒ every candidate ``KEEP`` + a warning. A candidate
    the arbiter did not mention ⇒ ``KEEP``.
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

    if arbiter is not None and candidates:
        try:
            verdicts, arbiter_batch = _parse_verdicts(
                arbiter(candidates, str(narrative or "")))
        except Exception as e:
            # capture. Fail-open: every candidate keeps, and the failure is
            # recorded rather than swallowed.
            verdicts, arbiter_batch = {}, {}
            warnings.append(
                f"vet arbiter failed ({type(e).__name__}: {e}) — all "
                f"{len(candidates)} candidate(s) kept (fail-open)")
        for iid, verdict in verdicts.items():
            if iid in decisions:
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

    by_outcome: dict[str, int] = {}
    for d in decisions.values():
        by_outcome[d["outcome"]] = by_outcome.get(d["outcome"], 0) + 1
    stats = {
        "candidates": len(decisions),
        "by_outcome": by_outcome,
        "discarded": by_outcome.get(DISCARD, 0),
        "arbiter": "present" if arbiter is not None else "none",
    }
    return {"decisions": decisions, "batch": batch, "stats": stats,
            "warnings": warnings}


# ── Applying the verdicts — the removal, and the Layer-1 guard ─────────────

def _referenced_entity_names(embed_list: object,
                             discarded_ids: set[str]) -> set[str]:
    """Entity names referenced by any candidate that is NOT being discarded.

    This is the P0 guard's input. Only *surviving* items are scanned: an
    entity referenced solely by a discarded point may safely go with it.
    Scans ``about_entities`` and the subject/object ``slots`` of every
    surviving candidate. Operator endpoints are NOT scanned *here* — they name
    points/events, never entities (``commit_schema``); the operator prune in
    :func:`apply_vet` handles the removed-entity-endpoint case separately. A
    shape we do not recognise contributes nothing, which is safe because the
    guard's only failure mode is keeping too much.
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
    if section == "entities" or item.get("content") is None:
        return ""
    return str(item.get("content")).strip()


def _item_text_variants(section: str, item: Mapping[str, Any]) -> set[str]:
    """Normalized texts the embedder can key OR mint an endpoint from.

    The UNION of the candidate identity (``name or content``) and the content:
    `execute_embed` *resolves* an endpoint on content but its #2552 mint pre-pass
    materializes whatever text the operator wrote — so a point carrying both keys
    can be re-materialised from either. Subtracting ``surviving_texts`` keeps the
    broader set safe (a survivor providing either form shields the operator).
    """
    return (_norm_variants(_item_text(section, item))
            | _norm_variants(_item_content(section, item)))


def _content_texts(embed_list: object) -> set[str]:
    """Normalized CONTENT of every point/event — the embedder's RESOLUTION
    surface (``_resolve`` keys ``point_ids``/``event_ids`` by content)."""
    out: set[str] = set()
    for section, _index, item in _iter_items(embed_list):
        out |= _norm_variants(_item_content(section, item))
    return out


def _identity_texts(embed_list: object) -> set[str]:
    """Normalized identity + content of every point/event — the embedder's MINT
    surface (its #2552 pre-pass materializes whatever text an operator wrote)."""
    out: set[str] = set()
    for section, _index, item in _iter_items(embed_list):
        out |= _item_text_variants(section, item)
    return out


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


def _operator_endpoint_text(op: Mapping[str, Any]) -> set[str]:
    """Normalized texts an operator endpoint names.

    Mirrors ``execute_embed``'s resolution surface: ``src``/``dst`` for any
    IMPL/NAND/MITIGATES, and the target — read as
    ``op.get("target") or op.get("target_edge")``, the **first present, not a
    union** — **only for a MITIGATES**, which is the only ``op_type`` the
    embedder reads a target for (``if _op_type == "MITIGATES"``). Reading it for
    every operator would drop a valid edge on a field the embedder never looks
    at (verified: an IMPL carrying a stray ``target`` that named a discarded
    point was pruned, losing the edge). Endpoints are coerced with the embedder's
    own ``str(v or "")`` so a NON-STRING endpoint (an LLM can emit ``42``) is not
    skipped and left to be re-minted, and a target is honoured only when it is a
    ``dict``, exactly as the embedder requires.
    """
    out: set[str] = set()
    for key in ("src", "dst"):
        v = op.get(key)
        if v and str(v).strip():
            out |= _norm_variants(v)
    if str(op.get("op_type", "")).upper() == "MITIGATES":
        target = op.get("target") or op.get("target_edge")
        if isinstance(target, dict):
            for key in ("src", "dst"):
                v = target.get(key)
                if v and str(v).strip():
                    out |= _norm_variants(v)
    return out


def removal_pool(before: object, after: object) -> dict:
    """The carry-forward record of one pass's removals.

    ``{"removed_texts": set[str], "removed_entities": {name: item}}``. The gate
    runs twice; without carrying the first pass's removals forward, the union
    pass cannot prune an operator that S4 re-added against an earlier-discarded
    item, nor restore an entity S4 started referencing — both verified to put
    the discarded content back into the payload (a resurrected Point, and a
    Layer-1 422).

    Derived by diff, not by re-reading the verdicts, so a *downgraded* discard
    (the Layer-1 guard) correctly contributes nothing to the pool.
    """
    before_entities = _entity_map(before)
    after_entities = _entity_map(after)
    return {
        # The REMOVAL side is the mint surface (identity + content): an operator
        # naming either form would be materialized by #2552. The SURVIVOR side
        # is the resolution surface (content only) — a survivor's *name* does
        # not resolve an endpoint in ``execute_embed``, so it must not shield
        # one (verified: shielding on a name left the endpoint to be minted).
        # Surviving ENTITY names are subtracted too: an entity contributes no
        # content, so without this an UNCHANGED entity would be carried forward
        # as "removed" and its name would prune an operator the embedder never
        # resolves against it (verified: `removal_pool(el, el)` returned the
        # name of an entity that was never touched).
        "removed_texts": (_identity_texts(before) - _content_texts(after)
                          - set(after_entities)),
        "removed_entities": {n: it for n, it in before_entities.items()
                             if n not in after_entities},
    }


def _rewrite_entity_references(embed_list: Mapping[str, Any],
                               restored: Mapping[str, str]) -> None:
    """Re-spell a reference to the emitted entity's exact name.

    ``validate_layer1`` compares ``about_entities`` and slot names by EXACT
    string, so an entity named ``pytest`` does not satisfy a point naming
    ``PyTest`` — the session still 422s (verified). Rewriting is a KEEP action
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
       discarded candidate — verified end-to-end. It reads an operator's
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
       cross-pass reference is invisible to a per-pass guard and was verified
       to produce a Layer-1 422. ``validate_layer1`` compares ``about_entities``
       and slot names by EXACT string, so an entity named ``pytest`` with a
       point naming ``PyTest`` 422s — whether the entity was *restored* (S4's
       reference arrived after the removal) or *downgraded to KEEP* by Rule 2
       (the same-pass case). Both go through the same reconciliation. Fail-open:
       keep, never drop.

    The function is **total** on a malformed section shape (a non-sequence
    section, a non-mapping item, a non-iterable ``about_entities``, an immutable
    ``Mapping`` slot ref) and on a malformed ``prior``/``decisions`` map: no
    exception.
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
                    # reconciliation exists to prevent (verified).
                    canonical[name] = str(item.get("name") or "").strip()
                    warnings.append(
                        f"vet: entity {str(item.get('name'))!r} was DISCARDed "
                        "but is referenced by a surviving candidate — kept "
                        "(Layer-1 referential integrity)")
                    kept.append(item)
                    continue
                if name:
                    removed_entity_names.add(name)
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
    present = set(_entity_map(out))
    to_restore = _referenced_entity_names(out, set()) - present
    for name in sorted(to_restore):
        original = prior_entities.get(name)
        if original is not None and str(original.get("name") or ""):
            # STRIP, as above: the emitted name is stripped (see the downgrade
            # branch for the verified failure this prevents).
            canonical[name] = str(original["name"]).strip()
            out.setdefault("entities", [])
            if isinstance(out["entities"], list):
                out["entities"].append(original)
            warnings.append(
                f"vet: entity {name!r} was discarded by an earlier pass but "
                "is referenced after S4 — restored (Layer-1 referential "
                "integrity)")
        else:
            warnings.append(
                "vet: ⚠️ a surviving candidate references entity "
                f"{name!r}, which no pass emitted — it cannot be restored "
                "here; the payload will fail Layer-1 unless the extractor "
                "emits it")
    if canonical:
        _rewrite_entity_references(out, canonical)

    # Operators: drop any whose endpoint referenced a removed point/event —
    # including items an EARLIER pass removed (they are absent from this list,
    # so the diff alone cannot see them). Only fire when no surviving item
    # provides that text: an identical-content survivor still resolves the
    # endpoint, and pruning would lose its edge.
    surviving_texts = _content_texts(out)
    gone = (removed_context | prior_texts | removed_entity_names
            | set(prior_entities)) - surviving_texts - set(canonical)
    if gone:
        ops = _as_items(out.get("operators"))
        kept_ops: list[Any] = []
        pruned = 0
        for op in ops:
            if isinstance(op, Mapping) and (
                    _operator_endpoint_text(op) & gone):
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
    look → refine). One line per candidate, stable order, tab-separated:

        <section>\\t<outcome>\\t<rule_id>\\t<text>\\t<reason>
    """
    lines = ["section\toutcome\trule_id\ttext\treason"]
    decision_map: Mapping[str, Any] = (decisions
                                       if isinstance(decisions, Mapping)
                                       else {})
    for section, index, item in _iter_items(embed_list):
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
