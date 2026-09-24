"""S2.2 VET — the adversarial selection gate (extractor v4).

**The one question this step asks:** *"should this candidate exist at all?"*
(`EXTRACTOR-V4-ARCHITECTURE.md` §4.0 — the purpose test). S2.3 CLASSIFY asks
*"what IS it?"* — a different question with a different failure mode (S2.2 fails
as *"we kept junk"*; S2.3 fails as *"we mislabelled it"*).

**Why it must exist at all.** The only rejection in the live pipeline is
``classify_consolidation`` (``extractor_v2.py:3994``), which runs **dead last**
— inside ``execute_embed`` — against *graph priors*, and therefore answers
*"is this a duplicate of something already stored?"*. That question structurally
cannot answer *"should this candidate exist at all?"*, which needs no priors.
This step is that gate, and it runs **before** the classify/resolve/embed tail
so a discarded candidate can never be classified, resolved or embedded.

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

The arbiter seam is the point (§4.2)
------------------------------------
A **decision-only** model structurally enforces the vocabulary: a ``Choice``
over supplied options cannot return an out-of-vocabulary answer, whereas an
extraction LLM emitting kinds/verdicts inline can only be *hoped* to comply.
``arbiter=None`` ⇒ the step performs only its mechanical Level-2 checks and
returns all-``KEEP``.

Vocabulary (owner ruling **O4**, `EXTRACTOR-V4-ARCHITECTURE.md` §16.3 / §16.4)
-----------------------------------------------------------------------------
``KEEP`` / ``NOOP`` / ``DISCARD`` / ``MERGE``, plus the batch-level
``RENARRATE``.

⚠️ ``MERGE-INTO-EXISTING`` is deliberately **NOT** emitted. §16.2 records it as
the **uncorrected A4 defect**: *"A4 is not corrected — ``MERGE-INTO-EXISTING``
is still in VET's outputs, so the VET/S3 circularity stands"*. VET runs before
S3 RESOLVE, and S3 is what *finds* what exists — so "merge into existing" is
circular at this position. A merge candidate is flagged ``MERGE`` and **S3
arbitrates the target**; the merge *itself* is #5006's (it needs the
union-of-attachments machinery and the high bar).

Failure policy (§4.2): FAIL-OPEN
--------------------------------
A wrong keep is noise; a wrong drop is memory loss. Unknown ⇒ ``KEEP``. An
arbiter that raises ⇒ all ``KEEP`` + a warning. The only path to ``DISCARD`` is
an explicit arbiter verdict.

⚠️ **A referenced entity is never removed** (the Layer-1 guard). See
``apply_vet``: an entity that any surviving candidate references **is a
referent**, and removing it would fail ``commit_schema.validate_layer1``
(``about_entities ⊆ entities``) and 422 the whole session — the exact
"a wrong drop is memory loss" outcome the failure policy forbids.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
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

#: The sections VET inspects, with the field that carries the item's text.
#: (section, text-field, family) — ``entities`` carry ``name``; events and
#: points carry ``content``.
SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("entities", "name", "entity"),
    ("events", "content", "event"),
    ("points", "content", "point"),
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
    """Case-folded, whitespace-collapsed text (the identity used for ids)."""
    return " ".join(str(text or "").split()).casefold()


def _item_text(section: str, item: Mapping[str, Any]) -> str:
    for sec, field, _family in SECTIONS:
        if sec == section:
            return str(item.get(field) or "").strip()
    return ""


def _item_id(section: str, index: int, item: Mapping[str, Any]) -> str:
    return f"{section}:{index}:{_norm(_item_text(section, item))}"


def _iter_items(embed_list: Mapping[str, Any]
                ) -> Iterable[tuple[str, int, Mapping[str, Any]]]:
    for section, _field, _family in SECTIONS:
        for i, item in enumerate(embed_list.get(section) or []):
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

def check_batch(embed_list: Mapping[str, Any], narrative: str) -> dict:
    """Level 2 — *did this batch come out right?*

    Two signals, both **reported, never acted on** in v1:

    - **coverage** — candidates emitted vs. statements the narrative carried.
      ``coverage_suspect`` fires only on the unambiguous case (a non-empty
      narrative produced **zero** candidates — a definite loss, no threshold).
    - **abstraction** — the candidate count per family, so a session that spent
      itself on one family is visible to the hand review.

    Returns a plain dict (JSON-safe) that rides ``stats["batch"]``.
    """
    statements = _narrative_statements(narrative)
    counts = {section: len(embed_list.get(section) or [])
              for section, _field, _family in SECTIONS}
    total = sum(counts.values())
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


def vet_candidates(embed_list: Mapping[str, Any], *,
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
                           "kind": str(item.get("kind")
                                       or item.get("eventKind")
                                       or item.get("pointKind") or "")})
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

def _referenced_entity_names(embed_list: Mapping[str, Any],
                             discarded_ids: set[str]) -> set[str]:
    """Entity names referenced by any candidate that is NOT being discarded.

    This is the P0 guard's input. Only *surviving* items are scanned: an
    entity referenced solely by a discarded point may safely go with it.
    Scans ``about_entities`` and the subject/object ``slots`` of every
    surviving candidate. Operator endpoints are NOT scanned — they name
    points/events, never entities (``commit_schema``), so they cannot create
    an entity reference. A shape we do not recognise contributes nothing,
    which is safe because the guard's only failure mode is keeping too much.
    """
    names: set[str] = set()
    for section, index, item in _iter_items(embed_list):
        if _item_id(section, index, item) in discarded_ids:
            continue
        for a in (item.get("about_entities") or []):
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


def _operator_endpoint_text(op: Mapping[str, Any]) -> set[str]:
    out: set[str] = set()
    for key in ("src", "dst"):
        v = op.get(key)
        if isinstance(v, str) and v.strip():
            out.add(_norm(v))
    target = op.get("target")
    if isinstance(target, Mapping):
        for key in ("src", "dst"):
            v = target.get(key)
            if isinstance(v, str) and v.strip():
                out.add(_norm(v))
    return out


def apply_vet(embed_list: Mapping[str, Any],
              decisions: Mapping[str, Mapping[str, Any]]
              ) -> tuple[dict, list[str]]:
    """Apply VET verdicts, returning ``(new_embed_list, warnings)``.

    Rules, in priority order:

    1. **Only an explicit ``DISCARD`` removes anything.** A missing decision, a
       ``MERGE``, a ``NOOP`` or a ``KEEP`` all leave the item in place. (``MERGE``
       is *recorded* by the caller but not applied here — merging is #5006's.)
    2. **A referenced entity is never removed.** An entity that a surviving
       candidate references **is a referent**; dropping it would fail
       ``validate_layer1`` (``about_entities ⊆ entities``) and 422 the whole
       session. The discard is downgraded to ``KEEP`` with a warning.
    3. **Operators orphaned by a removed point/event are pruned** and counted —
       an operator whose endpoint no longer exists cannot be wired, and
       ``execute_embed`` would drop it anyway (this makes the drop explicit and
       visible rather than silent).
    """
    warnings: list[str] = []
    discarded_ids = {iid for iid, d in (decisions or {}).items()
                     if str(d.get("outcome") or "").upper() == DISCARD}
    if not discarded_ids:
        # Nothing to do — return a shallow copy whose contents are identical
        # to the input, so the flag-off / nothing-discarded paths are
        # byte-identical downstream (the same object is never mutated).
        return dict(embed_list), warnings

    referenced = _referenced_entity_names(embed_list, discarded_ids)
    removed_entity_names: set[str] = set()
    removed_context: set[str] = set()      # norm texts of removed events/points
    # Start from a full copy so non-VET keys (operators, chain_notes,
    # link_before_create, …) are preserved — only the three candidate sections
    # are rewritten.
    out: dict[str, Any] = dict(embed_list)
    downgraded = 0

    for section, _field, family in SECTIONS:
        items = embed_list.get(section) or []
        kept: list[Any] = []
        for i, item in enumerate(items):
            if not isinstance(item, Mapping):
                kept.append(item)
                continue
            iid = _item_id(section, i, item)
            if iid not in discarded_ids:
                kept.append(item)
                continue
            if family == "entity":
                name = _norm(item.get("name"))
                if name and name in referenced:
                    downgraded += 1
                    warnings.append(
                        f"vet: entity {str(item.get('name'))!r} was DISCARDed "
                        "but is referenced by a surviving candidate — kept "
                        "(Layer-1 referential integrity)")
                    kept.append(item)
                    continue
                if name:
                    removed_entity_names.add(name)
            else:
                txt = _norm(item.get("content"))
                if txt:
                    removed_context.add(txt)
            warnings.append(
                f"vet: discarded {section} "
                f"{_item_text(section, item)[:80]!r}")
        out[section] = kept

    # Operators: drop any whose endpoint referenced a removed point/event.
    if removed_context:
        ops = out.get("operators") or []
        kept_ops: list[Any] = []
        pruned = 0
        for op in ops:
            if isinstance(op, Mapping) and (
                    _operator_endpoint_text(op) & removed_context):
                pruned += 1
                continue
            kept_ops.append(op)
        if pruned:
            out["operators"] = kept_ops
            warnings.append(
                f"vet: pruned {pruned} operator(s) whose endpoint was "
                "discarded")

    # Defence-in-depth assertion, unreachable by construction: a removed
    # entity was unreferenced by every non-discarded item going in, and the
    # output's non-discarded set is a subset of that — so no surviving item
    # can name it. The check stays because a future shape change that breaks
    # the invariant should surface as a WARNING here, not as a 422 at commit
    # time. It only reports; it never re-adds anything.
    if removed_entity_names:
        surviving = _referenced_entity_names(out, set())
        leaked = removed_entity_names & surviving
        if leaked:
            warnings.append(
                "vet: ⚠️ internal invariant broken — removed entity name(s) "
                f"still referenced: {sorted(leaked)} (a candidate shape "
                "changed under the guard; no entity was re-added)")
    if downgraded:
        warnings.append(f"vet: {downgraded} discard(s) downgraded to KEEP")
    return out, warnings


# ── The draft-and-run instrument (D13/O5) ─────────────────────────────────

def audit_candidates(embed_list: Mapping[str, Any],
                     decisions: Mapping[str, Mapping[str, Any]]) -> str:
    """Render one line per candidate for the tens-of-real-items hand review.

    This is the **report**, not a gate. Its output is what the owner reviews to
    set the thresholds the design refuses to invent (D13/O5: draft → run →
    look → refine). One line per candidate, stable order, tab-separated:

        <section>\\t<outcome>\\t<rule_id>\\t<text>\\t<reason>
    """
    lines = ["section\toutcome\trule_id\ttext\treason"]
    for section, index, item in _iter_items(embed_list):
        iid = _item_id(section, index, item)
        d = decisions.get(iid) or {}
        lines.append("\t".join([
            section,
            str(d.get("outcome") or KEEP),
            str(d.get("rule_id") or ""),
            _item_text(section, item)[:160],
            str(d.get("reason") or ""),
        ]))
    return "\n".join(lines)
