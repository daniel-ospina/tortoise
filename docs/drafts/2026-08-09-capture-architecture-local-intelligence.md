---
title: "Capture Architecture — Local Intelligence, Remote Graph (BYOK default)"
type: design
domain: product
doc_status: draft
created: 2026-08-09
ownedBy: epistemic-team
governingAgreement: "#312, #753 (design review)"
---

> **Product-owner decision (2026-08-09):** conversation extraction runs LOCALLY on the
> user's machine with THEIR LLM key and model choice. The hosted product stores only the
> DERIVED knowledge (points, entities, operators, source summaries) the user chooses to
> write to the graph. We never see the raw conversation and never handle the user's LLM
> key. Raw-transcript upload and managed-key extraction are explicit OPT-INS, not the
> default.

# Capture Architecture — Local Intelligence, Remote Graph

## The model

```text
User's machine                                    Hosted product
─────────────────                                 ─────────────
session happens  → local extraction (user's key,  → receives ONLY the
raw conversation   user's model, user's compute)    derived result:
stays here         → structured derived commit      points, entities,
user's LLM key     → POST derived content           operators, Source
stays here, never                                   summary/metadata
sent
```

- **We never see the conversation.** The raw tape stays on the user's machine; provenance
  references it locally (source id + span), and extracted claim text rides along in the Point.
- **We never touch the user's LLM key.** No key storage surface, no key rotation
  obligations, no "test connection" approval flow. The user's provider bills them directly.
- **Break-even by construction.** The LLM cost is the user's (local). We meter graph ops
  (writes/reads — the existing usage model). No margin question on the intelligence layer.

## Why this fits the target customer

- **Devs are finicky about models** — they configure exactly their provider + model locally.
  No platform approval, no lock-in.
- **"We never see your conversations"** is a strong trust story for devs and enterprises.
- **Self-hosted and hosted unify** into one story: *local extraction + hosted graph
  persistence/sharing/collaboration*. The hosted product's value is the shared derived
  layer, not the compute.
- The local-compute pattern already exists: the capture extension spawns local python for
  the self-hosted ingest path; the SDK `capture_session` (merged #721) already does local
  extraction. BYOK on hosted = same local path, POST the derived result instead of the raw.

## The flow (default capture path)

1. Session ends on the user's machine → the capture extension runs the **value-first
   extraction locally** (value gate → extraction → relations; warrants are an opt-in
   toggle, reconstructed premises always tagged low-confidence/derived).
2. The local pipeline produces a **derived commit**:
   `{session_id, summary, story_arc, entities[], points[], operators[], provenance_refs[]}`
   — no raw text.
3. POST to the hosted API (new "session commit" endpoint) → server validates (schema,
   quota, budget) → persists to the team graph.
4. Hosted graph holds: derived Points/Entities/Operators + a **Source node with summary +
   story-arc** (metadata only, per the Event/Source/Points three-layer model).
5. Raw conversation + evidence stay local; provenance points reference them.

## The two explicit opt-in paths

| Path | What it is | Who it's for | Notes |
| --- | --- | --- | --- |
| **Raw-transcript upload** | Upload the full conversation to the hosted graph (Source holds content, enables cross-device full-conversation recall/search) | Users who want hosted full-transcript recall | Opt-in toggle; the "conversation never leaves" guarantee is the default |
| **Managed-key extraction** | Server-side extraction using our DeepSeek V4 Flash key, metered (~$0.02–0.03/session or bundled) | Non-devs who don't want local setup | The existing server-side extraction path (TORTOISE_SESSION_EXTRACTION auto/required/regex); the "simplicity affordance" |

## Trade-offs (honest)

- **Hosted recall is of the derived layer + summaries**, not the literal transcript. "What
  did we decide" yes; "what did we say in turn 42" is a local query unless raw upload is
  opted in.
- **The merged cloud-mode (#131) posts raw conversation** — under this architecture it
  becomes the managed-key path, or is reworked to POST derived content. The
  conversation-never-leaves default takes precedence before #131 flips on.
- **Implicit-premise (warrant) generation is dangerous** (invented reasoning, research-
  grade, no ground truth) — deferred out of the default path; at most a user-opted toggle
  using their own model, always tagged low-confidence/derived.

## What needs to change (when implemented)

1. **New hosted endpoint**: "session commit" accepting derived content (schema-validated,
   quota-checked) — the derived write path.
2. **Capture extension rework**: local value-first extraction → POST derived commit
   (default); raw-conversation POST becomes the opt-in; managed-key stays server-side.
3. **SDK**: expose the derived-commit producer (the local extraction already exists in
   `capture_session`); add a `commit_session` cloud-write helper.
4. **Warrants**: opt-in toggle, off by default; reconstructed premises tagged
   `derived: true`, confidence-capped.
5. **Onboarding**: configure local extraction (provider + model + key, local-only);
   optional toggles for raw upload and managed key.

## Decisions recorded

- ✅ LLM provider for the managed option: **DeepSeek V4 Flash** ($0.14/M in, $0.28/M out).
- ✅ Default capture = **local extraction, derived-only writes**.
- ✅ Warrants/implicit premises = **deferred or user-opted toggle**, never default.
- ⏳ Open: whether #131 (raw-conversation cloud mode) is kept as the managed path or
  reworked to derived — decide before it becomes default-on.
- ⏳ Open: Source-node content on hosted = summary+story-arc only (lean) vs opt-in raw.

## Related docs

- docs/drafts/2026-08-09-value-first-extraction-pipeline.md (the extraction pipeline)
- docs/drafts/2026-08-09-state-epistemic-separation.md (three-layer model)
- docs/drafts/2026-08-09-mvp-scope-economics.md (pricing/cost framing)
- Issue #312 (hosted capture), #753 (design review gate)
