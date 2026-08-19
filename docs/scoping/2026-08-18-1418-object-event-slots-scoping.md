---
title: "Scoping — Issue #1418 (object + event participant slots)"
type: engineering
domain: capability
doc_status: live
created: 2026-08-18
subjects.team: epistemic-team
---

# Scoping — Issue #1418 (object + event participant slots)

## Confirmed Problem Definition

**The v2 extractor (#1350) emits only `about_entities` name strings — no
typed participant slots, no per-slot confidence.** The #1370 entity-type-
agnostic binding (`EntityBound {point, entity_id, entity_type, confidence}`,
D6) is designed for exactly three participant roles — `subject` / `object` /
`event` — with per-slot confidence, and the #1370 S1 re-validation (2026-08-18)
fixed the emission anchor: `OUTPUT_CONTRACT` (extractor_v2.py:336) → `execute_embed`
(extractor_v2.py:997) → `commit_schema.py` (Point/Event models are
`extra="forbid"` — a new slot field 422s unless explicitly added).

**#1418 is the SECOND wire-up: object + event-as-content slots ride D6.**
This issue owns the extractor-side + schema half that stands alone; the
binding/decoration half (threshold-gated `EntityBound` journaling, search
decoration, #1417 provenance-orthogonality) stays gated on #1370 (write-path
implementation, other agent).

## Boundary (what ships here vs what stays gated on #1370)

| Ships in #1418 | Stays gated on #1370 |
|---|---|
| `OUTPUT_CONTRACT` emits per-point/per-event `slots: {subject/object/event: [{name, kind, confidence}]}` (S2+S4 share the constant) | `EntityBound` journaled events, threshold-gated binding (`≥ τ_hi` / band / NIL) |
| `execute_embed` (S5) sanitizes + carries slots into Layer-1 `payload_points` / `payload_events` | Search decoration surfacing object/event aboutness |
| `commit_schema.py`: `SlotRef` + `ParticipantSlots` models; `slots` field on `Point` + `CommitEvent` (additive-optional — old payloads validate unchanged) | `aboutEvent` content-edge writes / provenance untangle (#1417) |
| Layer-1 referential check: subject/object slots must resolve to an emitted `(name, bare kind)` entity pair (D6 merge key — the "inherited" fail-closed direction the #1370 re-validation names; `core:plan` ≡ `plan`) | Event-slot resolution (event-as-content vs entity ref) — the write-path resolver owns it; **no** Layer-1 check on event slots (content-match would be wrong: a slot name like "the Aug 3 meeting" ≠ event content "The Aug 3 meeting concluded X") |
| Canonical hash includes slot **names/kinds only** (confidence EXCLUDED — LLM artifact, consistent with the `confidence/c_cal` exclusion rule) | — |

## Design Decisions

- **D1 Slot shape (S1 anchor, verbatim):** `slots: {subject: [{name, kind, confidence}], object: [...], event: [...]}` — same schema for all three roles; `name`+`kind` is D6's `entity_id` (the (name, kind) merge key — no client entity ids). Kind vocabulary from the master list (SUBJECTS/OBJECTS/EVENTS sections) — closed; **S5 applies the same `master_kind_forms` minted-kind gate to slot kinds as to entities/events** (repair to the family fallback with a warning — never silently divergent).
- **D2 Event-as-content (#1417 B2):** the `event` slot binds CONTENT aboutness (a claim *about* an event — "the Aug 3 meeting concluded X" → `event: [{name: "the Aug 3 meeting", kind: "core:meeting", confidence}]`), NEVER provenance. Prompt guidance states this explicitly so the LLM never puts capture-path provenance in an event slot.
- **D3 Sanitization (S5, deterministic):** slots are LLM output → sanitized exactly like entities/events: non-dict entries skipped, non-list role values warned + dropped, blank names dropped, `confidence` coerced to float clamped to [0,1] (non-numeric → warning + 0.0), unknown role keys dropped with warning. **Resolution filter (fail-closed, never sink the commit):** subject/object refs that do not resolve to an emitted `(name, bare kind)` entity are DROPPED with a warning (operator-drop pattern) — the write path binds emitted entities only; event refs pass through untouched (write-path resolved). No binding decisions here — S5 carries, never gates (threshold gating is #1370's).
- **D4 Schema:** `SlotRef {name, kind, confidence}` and `ParticipantSlots {subject, object, event}` — all `extra="forbid"`; `Point.slots: ParticipantSlots | None = None`, `CommitEvent.slots` same (additive-optional per the `SupersessionRecord` precedent — no migration, old clients validate unchanged).
- **D5 Canonicalization:** slots enter the canonical hash as sorted `{name, kind}` pairs, keyed `slots`, ONLY when present on the item (the supersessions pattern — an additive field must not change the id of a payload that never had it). Per-slot `confidence` excluded (same rule as `confidence`/`c_cal`).

## Test Plan

- **test_extractor_v2.py (`TestParticipantSlots`):** OUTPUT_CONTRACT carries subject/object/event slot shape; S2_TMPL + S4_TMPL carry slot guidance; `execute_embed` carries point AND event slots through to the payload with per-slot confidence; sanitization (bad confidence, blank names, unknown roles, non-list role values); minted slot-kind repair; unresolved subject/object refs dropped with warning (event refs untouched); slots payload passes Layer-1.
- **test_commit_schema.py (`TestParticipantSlots`):** Point + CommitEvent accept slots (no 422); additive-optional (no slots → valid, same hash as pre-#1418); referential (subject/object slot must resolve to an emitted (name, kind) pair — unknown name OR kind mismatch → 422; bare kind ≡ namespaced kind); shape (confidence out of [0,1] → 422; unknown role key → 422); canonical (slot-name change → id change; confidence change → id unchanged).

## Risks / Dependencies

- **#1370 (write-path binding)** — this issue's extractor/schema half is additive-optional and payload-valid without it; the binding just won't be journaled until #1370 lands. No mechanism change here (D6 proof of type-agnosticism happens there).
- **#1417 (aboutEvent untangle)** — event slots must never ride the provenance edge; this issue only *emits* the slot (content semantics), the write side stays gated.
- **#1353 decoration** — untouched; no change to the subject field.

## Verification (issue checklist, extractor/schema half)

1. Extractor emits `object` and `event` slots with per-slot confidence (same schema as `subject`) — ✅ (this issue).
2. `EntityBound` binds object/event with zero mechanism changes — gated on #1370.
3. Search decoration surfaces object/event aboutness — gated on #1370.
4. `aboutEvent` content edges never collide with provenance — gated on #1417.
