# ADR-008: Client-Side Content Encryption for Tortoise Cloud

**Status:** Accepted (decision recorded; implementation gated post-GA)
**Date:** 2026-08-07
**Issue:** #28
**Owner:** epistemic-team

## Decision

Tortoise Cloud shall use **client-side content encryption with clear metadata**
(Option A from issue #28) for customer content: `Point.content`, `Document.body`,
and `Session.transcript` are AES-256-GCM encrypted with a **customer-held key**
before reaching our servers. All metadata required for search, EP propagation,
and graph traversal stays **clear**: titles, kinds, tags/keywords/topics,
embeddings, confidence scores, timestamps, edge labels, and a derived
`_searchText` field used for full-text search.

We **reject** searchable encryption (SSE/homomorphic): it fails tractability
(C4) and zero-UX-degradation (C6) criteria, has no vector-index support, and
carries research risk unjustified by current demand.

## Context

Tortoise Cloud is moving to hosted infrastructure (fly.toml deploy, MCP over
Streamable HTTP with tenant Bearer auth — #236, control-plane registry #7714).
Privacy-conscious customers (regulated industries, legal, healthcare,
enterprise) need assurance their knowledge base content is unreadable by us.
Self-hosted provides this by definition; the cloud product needs an equivalent
guarantee to convert these customers.

The graph model has four layers:

| Layer | Entities | Content sensitivity |
|-------|----------|---------------------|
| Semantic | Objects, Subjects | names, descriptions |
| Epistemic | Points, IMPL/NAND, confidence | **content** — the core asset |
| Episodic | Events | **transcripts** |
| Procedural | Actions | step bodies |

C2 (search performance parity) is re-scoped honestly: **latency parity holds;
result parity does not**. Keyword search becomes metadata-bound (clear
`_searchText`, titles, topics, embeddings). This matches how the codebase
already treats Documents: `Document._searchText` is the FTS-indexed derived
field while the body stays opaque (#125).

## Precedent in the codebase

The encryption envelope pattern is **already partially built**:

- `tortoise/projection/entities.py:253,273` — `Document._searchText` derived
  clear-text field is FTS-indexed (`createNodeIndex('Document','_searchText')`,
  projection/__init__.py) while the body is not.
- `tortoise/auth.py` — `hash_api_key()` with pepper (`TORTOISE_SECRET_PEPPER`)
  establishes the key-handling conventions (never store plaintext; pepper from
  env).

ADR-008 extends this: `_searchText` becomes the FTS surface for `Point` too,
and the raw content fields become ciphertext blobs.

## Schema implications (reserve now, before #235/#454 bake content-in-clear)

The hosted data-model plan (`docs/epics/2026-08-03-tortoise-hosted-platform/plans/7714-data-model.md`)
defines the registry/control-plane graph only — it does not fix the tenant
graph's content schema. But the onboarding epic (#235) and tool-registry epic
(#454) will wire tenant writes. **To avoid a live multi-tenant migration
later, reserve the following now:**

```
Point { content: <ciphertext blob | plaintext>, _searchText: <clear string>, encryptionVersion: 0|1 }
Document { body: <ciphertext blob | plaintext>, _searchText: <clear string>, encryptionVersion: 0|1 }
Event { transcript: <ciphertext blob | plaintext>, name/summary/topics: <clear>, encryptionVersion: 0|1 }
```

- `encryptionVersion: 0` = plaintext (current data, pre-enablement),
  `1` = AES-256-GCM client-key envelope.
- Search/EP/traversal read ONLY clear fields: `_searchText`, titles, kinds,
  topics, embedding, confidence, timestamps, edge labels.
- No stored flag for content state on reads — encryption is a write-path
  concern; readers never need the key.

## Key management

- **Customer-held key**: AES-256-GCM key generated client-side (e.g., in the
  MCP host / SDK), never transmitted to our servers. Wrapped per-tenant in the
  control-plane registry (metadata only — we hold a wrapped copy for recovery
  UX, never the unwrapped key).
- **Multi-agent key distribution** (unaddressed in the original issue — design
  gap): agents in different sessions share team graphs. Key must be derivable
  from a team secret the agent already holds (e.g., derive from API key +
  team pepper via HKDF) so every agent can decrypt team content without a
  separate key-exchange protocol.
- **Rotation**: `encryptionVersion` bump + re-encrypt; document procedure in
  ops runbook before GA.

## Consequences

- **Positive**: cryptographically unreadable content (C1, C5) — a real
  differentiator for regulated customers; EP propagation and traversal
  unaffected (confidence + edges clear, C3); implementation reuses the
  `_searchText` pattern (~300 lines crypto module + write-path wiring, C4).
- **Negative**: keyword search is metadata-bound (topics/titles/summary still
  semantically rich because session topics are LLM-extracted); content edits
  must re-derive `_searchText`; multi-agent key derivation must be designed
  before GA.
- **Risks**: embeddings are clear-text-derived — an adversary with the model
  could reverse approximate semantic fingerprints (documented, accepted for
  v1; mitigation = content is still opaque, only the semantic hash leaks).

## Implementation plan (post-GA trigger)

**Phase 1 — now (decision recorded, schema reserved):** this ADR + a note in
the 7714 data-model plan reserving `encryptionVersion` + `_searchText` so
#235/#454 do not hardcode content-in-clear.

**Phase 2 — post-GA (trigger: first privacy-sensitive prospect or security
questionnaire):**
1. `tortoise/crypto.py` — AES-256-GCM envelope (~250 lines) + HKDF key
   derivation from team secret.
2. Write-path: SDK `create_point`/`ingest` encrypt content, derive
   `_searchText` (Point — extend the Document pattern), set
   `encryptionVersion=1`.
3. Search strategy: FTS reads `_searchText`; vector reads clear embedding;
   structural unchanged.
4. Backfill/migration path for `encryptionVersion=0` tenants (re-encrypt in
   place, key never leaves the client).
5. Pre-req: #160 (hosted embeddings) must land first — C2 search parity is a
   P0 prerequisite.

## Alternatives considered

| Option | Verdict |
|--------|---------|
| A: Client-side encryption + clear metadata | **Accepted** — meets C1/C3/C4/C5, near-zero UX impact |
| B: Searchable encryption (SSE/homomorphic) | Rejected — fails C4/C6, no vector support, research risk |
| C: Defer; self-host-only privacy story | Rejected for the product decision — forfeits the A1 "cryptographically can't read" differentiator during GA; schema would bake content-in-clear and retrofit becomes a live migration |

## Status of the original issue's criteria

| Criterion | Status |
|-----------|--------|
| C1 Provider cannot read content | Met by design (client-held key) |
| C2 Search performance parity | Latency parity yes; results parity re-scoped to metadata-bound (documented) |
| C3 EP propagation unaffected | Met (confidence + topology clear) |
| C4 Implementation tractability | Met (reuses `_searchText` precedent, ~1 dev cycle) |
| C5 Customer key control | Met (key never transmitted; wrapped copy for recovery) |
| C6 Zero UX degradation | Met at v1; key-derivation design needed for multi-agent sessions |
