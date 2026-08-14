---
title: "Scoping: #265 — Epic: Client-side content encryption — provider cannot read customer graph content"
type: decisions
domain: capability
doc_status: live
created: 2026-08-13
ownedBy: epistemic-team
---

# Scoping: #265 — Epic: Client-side content encryption — provider cannot read customer graph content

**Date:** 2026-08-13 · **Tier:** complex · **Skill:** issue-scoping v5.1 (double diamond + verify gates, streamlined inline mode)
**Issue:** daniel-ospina/tortoise#265 · **Team:** organisation-design-team
**Research artifact:** `docs/research/2026-08-13-265-encryption/`
**Status:** scoped — [QWEN-GATE] substitute reviewer used (qwen3.8-max blocked 401)

---

## Confirmed Problem

Give the hosted Tortoise product a **cryptographically-enforced provider-cannot-read guarantee over customer graph content** — covering not just `Point.content`/`Document.content`/transcript Documents but **every content-derived field** the v3.7 state-centric record requires protected, resolved per the field-classification rule below — with:

- **Per-write-path customer-side encryption execution points**: SDK + REST paths guaranteed at v1 (encryption executes in the customer's process); hosted MCP path (the primary agent surface — `claude mcp add tortoise https://api.premiselabs.co/mcp/`, README:55) **hard-gated on a local MCP encrypt-proxy workstream** with a drop-dead date, dated customer-facing disclosure, and non-vacuous slip consequences (acceptance criterion 12).
- **A key lifecycle whose KEK is generated client-side, never derivable from any credential the provider receives, and never unwrappable by the provider at any lifecycle stage including mint** — rejecting the issue's own former "HKDF from API key + team pepper" design (#1137/ADR-008, provider-derivable). **Two-key split**: client-held confidentiality key (content KEK) + server-held integrity key (TORTOISE_SECRET_PEPPER precedent) for HMAC verification and dedup. Distribution via **team passphrase** (per-session entry or OS-keychain cache); recovery **WITHOUT email and WITHOUT provider key possession** (passphrase re-entry → re-wrap in registry); rotation **without data loss** (bulk re-encrypt job allocated, P5); **accepted + disclosed data-loss mode** on passphrase+keychain loss.
- **Field-classification rule (R3) — no field in both the protected and accepted-leak sets**, with stated search/dedup consequence per field:

| Field | Resolution | Consequence |
|---|---|---|
| Point.content, Document.content, Event content payloads, operator content | **encrypt** (AES-256-GCM, AAD = node-id+field) | C1 core |
| content_hash | **keyed HMAC** (server-held integrity key) | intra-team dedup preserved; cross-team equality + dictionary attack removed; quantified equality-leak bound |
| Document.summary/topics | **encrypt** (content-derived) | `_searchText` rebuilt from title/tags/pointKind + client-derived snippet field |
| Point._searchText (BUILD — does not exist today) | **clear, accepted verbatim-term leak** with pre-registered bound (chars, abstractive-only provenance, overlap%) | Point FTS re-pointed from raw `content` index to `_searchText` (projection/__init__.py:929) |
| embeddings | **clear, accepted semantic-fingerprint leak** — attack-contingent bound (SOTA inversion attack named), customer disclosure | vector search preserved; #160 pipeline bypassed for v1 teams, client-computed + model-version pinned |
| structural lifecycle (status, CORRECTS topology, timestamps, confidence) | **clear** | lifecycle stays queryable per 08-12 CRITICAL; lifecycle *content* (the superseding/deprecated points' text) IS encrypted |

- **Leak-quantification with pre-registered thresholds + go/no-go consequence** (embedding inversion, HMAC-dedup equality, FTS term-distribution, snippet-overlap); "bounded" without measurement = guarantee not shipped.
- **<5% read-latency regression** (per-op p50/p95, search/EP/traversal, #316 methodology) + a defined write-path budget (AEAD + keyed-hash + client embedding).
- **Per-consumer processing classifications** (R5): topic summarization → metadata-only at v1; ranking display → clear-derived fallback; audit → redacted/metadata-only; snippets → client-side post-decrypt; dashboard → non-guaranteed metadata-only view + disclosure; analytics/metering verified content-free; no reveal endpoints for encrypted teams.
- **Migration product-gated** (enablement gate: new teams flagged at provision; existing teams opt-in client-side re-encrypt sweep or frozen-v0 decision; old-SDK writes 4xx fail-closed).
- **Artifact updates mandated**: ADR-008 addendum (HKDF rejection, two-key model, #160 re-scope, C6 re-scope), #265 body sever via #1137, research artifact committed.

### Why This Framing

Root cause of "provider can read content" is not the three content fields: it is **every derived field** (plain-SHA256 `content_hash` at sdk.py:159, operator content mirroring inputs, `_searchText`/summary/topics, embeddings) **plus key custody** plus **the execution boundary** (hosted MCP runs provider-side — `hosted_api.py:5943` mounts `/mcp`; `mcp_auth.py` builds a per-request in-process SDK; there is no customer code on that path today). External precedent converges (Tresorit/Proton: true ZK = no server-side content search; Zep BYOK server-decryptable; Acontext key-in-credential + dedup-off; CipherSweet keyed blind indexes; amgres/Bitwarden recovery without email). Rejected: narrow field-encryption framing (fails root cause), key-lifecycle-only framing (partial), self-host-only / BYOK / TEE / client-side-search alternatives (fail on evidence, ADR-008).

### Falsification Check

This definition is WRONG if any of:
1. A labeled retrieval set or real query logs exist making server-side content search unnecessary (verified: none — #316 defers P/R@K to a future labeled-set issue).
2. The hosted deployment never stores content-derived fields server-side (contradicted: search/EP/topic-summarization are server-side).
3. An attacker with DB exfiltration (no keys) can recover customer content through any clear field not classified as a documented accepted leak — the classification matrix must be exhaustive (review-gated against the v3.7 record spec).
4. Provider ops staff can decrypt via the recovery path, or an adversary holding {API key + traffic + DB + registry + mint endpoint} can decrypt (automated harness, go/no-go).
5. The provider process observes plaintext on any guaranteed path (execution-point falsification).
6. Latency regression on search/EP/traversal exceeds 5%, or the write-path budget is missed.

### Confidence: 82/100

High on root-cause framing, execution-boundary finding, #307 staleness (primary-source verified), recovery-without-email necessity, two-key model. Residual: accepted-leak boundary is a product decision (embeddings/_searchText/snippet field); dashboard/web-client scope; #317 reranker depth; customer adoption of the local proxy; migration scope decision.

---

## Verification Gates

### problem-verify — 2 cycles, PASSED
- **Cycle 1:** Verifier A P0=0 P1=4 P2=3 P3=3; Verifier B P0=0 P1=3 P2=6 P3=2. Controller: FIXED all 5 P1 groups — (1) execution boundary mis-stated (code-verified: hosted MCP is provider-side, A9 re-tagged FALSE, per-write-path execution points added); (2) key-derivation contradiction (HKDF-from-API-key rejected — provider sees the API key; client-generated KEK + passphrase-wrapped registry copy); (3) stakeholder taxonomy (per-surface classify all 10 + Document.summary/topics); (4) dashboard web client (non-guaranteed surface, documented); (5) leak bound (quantification deliverable + pre-registered thresholds). Re-dispatched.
- **Cycle 2:** Verifier A P0=0 P1=6 P2=3 P3=0; Verifier B P0=0 P1=5 P2=4 P3=2. Controller: INCORPORATED all via R1–R6 — key wrapping pinned (R1), multi-agent distribution restored as team passphrase (R2), per-field resolution rule (R3), MCP hard-gate reframed (R4), processing execution points (R5), proxy magnitude surfaced (R6). No re-dispatch left (max 1 used). Residual: none structural — remaining items are solution decisions + human Clarifications.
- **Verdict:** PASSED. Both gates: all P0/P1 resolved or bound into the confirmed problem; no 3-cycle escalation trigger.

### solution-verify — 2 cycles, PASSED
- **Cycle 1:** Verifier A P0=0 P1=4 P2=6 P3=0 P4=2; Verifier B P0=0 P1=4 P2=5 P3=2 P4=2. Controller: incorporated S-Fix 1–7 + P2/P3/P4 batch — (1) JSONL event-log/rebuild plaintext path (P0 → encrypted-verbatim snapshots + version-dependent replay); (2) gate teeth → acceptance criterion 12; (3) encrypted-payload contract + fail-closed write gate (server never recomputes embedding); (4) Point search surface (no title exists; Point._searchText built, parity = latency + quantified recall@k); (5) interim hosted-MCP behavior (content-bearing tools frozen for encrypted teams + metadata-only reads); (6) topic summarization v1 = metadata-only; (7) export/portability decrypt-export. Re-dispatched.
- **Cycle 2:** Verifier A P0=0 P1=4 P2=8 P3=1; Verifier B P0=0 P1=3 P2=11 P3=0. Controller: incorporated C2-1..C2-6 — criterion-12 loophole closed (hard drop-dead date + expiry + non-vacuous slip consequences + O/I/T scoped per-surface); rebuild derived-field parity (snapshot embedding verbatim, dedup identity preserved); **two-key model** (client confidentiality key + server integrity key); #160 resolved (landed as "pending merge", client-side embedding + model-version pin); rotation bulk re-encrypt job allocated in P5; artifact persistence (this doc + research artifact). P2s: event-log surface corrected (hosted never sets event_log_path; gate SDK-level emission on encryptionVersion>=1), content_hash site enumeration, snippet bound pre-registered, C6 re-scope, dashboard carve-out (main.jsx:1332/1349 verified), migration enablement gate, topic-summary snippet source, Point FTS index + backfill.
- **Verdict:** PASSED. Both gates: no P0 remained after incorporation; residual = product decisions + execution-phase detail, not structural.

### Phase 5.6 — Qwen Coherence Check
**[QWEN-GATE] substitute reviewer used** — qwen3.8-max is blocked (401) on this session; one substitute fresh-context reviewer dispatched per the skill's coherence prompt.
- Findings: P1×3 — (a) artifacts not persisted on disk (fixed by this doc + research artifact + #1137 comment + extra issues); (b) #1137 body + 307 scoping doc still encode the rejected HKDF-from-API-key design (addressed: #1137 comment + extra issue E1); (c) target wording "zero plaintext reachable" vs clear derived fields (reworded to "zero raw-content plaintext" + leak table names _searchText/topic-metadata/embeddings). P2×4 — #317 reranker disposition pinned per mode (skip at v1, disclosed impact); wrapped-key corruption + rotation-skew edge cases added to P3; P4 restructured as transport-level matrix-driven transform (79-tool enumeration = conformance test matrix only); #160 sequencing pinned in ADR addendum + zero-new-deps qualified to crypto core (client embedding dep noted).
- Coherence verdict: strong on execution boundary, two-key split, scrypt-passphrase rejection, JSONL parity, MCP hard-gate, migration enablement, edge-case coverage. No problem-dimension dropped between diamonds.

---

## Plan

**Selected approach: Hybrid C — one crypto core, two deployment modes (SDK-embedded + local MCP encrypt-proxy), phased.** Rejected alternatives documented below with "when it WOULD have been better".

### Implementation steps

**P0 — Design & pre-registration (review-gated):** threat model (adversary = provider process + DB exfiltration + subpoena; NOT customer device compromise); field-classification matrix as a **review-gated deliverable** (fresh-context adversarial pass over the v3.7 state-centric record spec; every Point/Document/Event/operator/Subject field → encrypt | keyed-blind | clear-with-quantified-bound | structural); leak-quantification probes with **pre-registered numeric thresholds + consequence rule** (embedding inversion, HMAC-dedup equality + content churn, FTS term-distribution, snippet-overlap; exceed ⇒ guarantee not shipped / surface descoped); latency baselines (read: #316 methodology; write: current clear path p50/p95 per-op).

**P1 — Crypto core (`tortoise/crypto.py` rewrite):** AES-256-GCM envelope (pyca `AESGCM`, 96-bit nonce, 128-bit tag, AAD = node-id+field, encrypt-then-authenticate); keyed `content_hash` (HMAC-SHA256 under server-held integrity key — preserves intra-team dedup, removes cross-team equality + dictionary attack); passphrase KDF (**pyca scrypt, n=2^17, versioned in envelope** — zero new third-party deps; FIPS-provider scrypt limitation noted in ADR addendum); client-side KEK generation; versioned envelope header (encryptionVersion 0|1, KDF params, key id); fail-loud on missing/wrong key (crypto.py precedent). Unit tests: round-trip, tamper (GCM auth tag), wrong-key, rotation, KDF vectors, HMAC dedup equality, envelope-version mismatch.

**P2 — SDK-embedded encryption (`tortoise/sdk.py` + projection) + encrypted-payload contract:** client derives and ships (content_ct, client embedding, keyed content_hash, `_searchText`); **server never recomputes embedding/_searchText for encryptionVersion>=1 teams** (sdk.py:734-745 recompute path bypassed); server verifies HMAC (integrity key); **fail-closed write gate** (encryptionVersion>=1 team rejects plaintext content writes — REST /v1/points + MCP args, old-SDK writes 4xx); JSONL snapshot parity (sdk.py:568-590 `_emit_event`): **encrypted-verbatim snapshots only**, emit-side strip version-dependent (v1 snapshots preserve client embedding + keyed content_hash), v1 replay uses snapshot embedding verbatim (never recomputes from ciphertext), encrypted-tenant rebuild via verbatim apply() path; Point._searchText build (tags + pointKind + label + client-derived snippet/summary field with pre-registered bound) + FTS index create + idempotent backfill (projection precedent :986); read-path decrypt in get_point/search/EP/related/audit/reveal consumers; snippet construction client-side post-decrypt (parity test); summary/topics encrypted; topic summarization → **metadata-only at v1** (snippet source switched to client-derived field incl. /v1/topics/{topic}/summary); client embedding model + dimension + version pinned + write-time model-version check. Integration tests: "content never stored in clear" DB assertion (incl. post-rebuild), HMAC dedup hits, EP confidence/rankings identical, stored embedding == client vector, plaintext→4xx, rebuild-no-double-encryption, concurrent writers (multi-agent, same KEK, random nonces).

**P3 — Key lifecycle (hosted_api.py + control-plane registry + client ceremony):** client-side KEK generation at team setup (SDK/CLI command); passphrase-wrapped copy (scrypt KEK) → registry Team node (`wrapped_key`, `key_version`, `kdf_params` — reserve in 7714 data-model plan); recovery WITHOUT email (passphrase re-entry → re-wrap → agent unwrap; disclosure: "if you lose passphrase AND all keychain copies, data is permanently inaccessible"); per-agent distribution: team passphrase per-session entry (v1) or OS-keychain cache (documented weaker); rotation = passphrase re-wrap + **KEK re-encrypt via the P5 bulk re-encrypt job** (worker, per-team queues, idempotent resume, rollback, latency budget, key-online); **API-key mint never touches key material** (hosted_api.py:1643/3953 unchanged in that respect); wrapped-copy corruption handling (detect at unwrap, alert, preserve encrypted blobs); rotation-skew per-write key-version check (fail-loud on mismatch). Tests: recovery end-to-end; rotation re-wrap + re-encrypt; **adversary harness = {API key + traffic + DB + registry + mint endpoint} cannot decrypt** (go/no-go); corrupted-wrapper + KDF-mismatch cases; no-key-material-in-mint assertion.

**P4 — MCP encrypt-proxy (NEW `tortoise/proxy.py` + CLI; hard-gated):** local gateway terminating MCP (+REST) connections; **transport-level matrix-driven transform** at the Streamable-HTTP message boundary keyed off the field-classification matrix (one generic envelope transform; per-tool exception list only for non-mechanical consumers — topic summarization, ranking, dedup hashing); 79-tool enumeration retained as a **conformance test matrix**; write-transform (embed+hash+encrypt), read-transform (decrypt across content-bearing tools), metadata-only for the rest; absorbs topic summarization (full mode) + ranking display; provider /mcp receives only ciphertext + clear derived fields. **Guarantee claim for the MCP surface gated on proxy GA + disclosure (criterion 12).**

**P5 — Latency, leaks, migration, export, artifacts:** read-latency regression < 5% per-op p50/p95 vs #316 baseline (FTS/vector/hybrid strategies + EP + traversal); write-path budget (AEAD + keyed-hash + client embedding) met; leak probes vs pre-registered thresholds; migration cutover per tenant class (backfill _searchText → enable client encryption → re-encrypt in place; SDK / MCP / dashboard classes; rollback criteria; legacy-search degradation window with bound) + **enablement gate** (new teams flagged at provision; existing teams opt-in client-side re-encrypt sweep or frozen-v0 decision); bulk re-encrypt job (rotation + migration machinery); export-decrypt (`tortoise export --decrypt` — ciphertext + envelope + recovery instructions); ADR-008 addendum (HKDF rejection, two-key model, #160 "pending merge" re-scope, C6 re-scope, FIPS note) at P2 time; #265 body sever via #1137; research artifact committed.

### Testing strategy
Unit (crypto envelope: round-trip/tamper/rotation/KDF vectors) → integration (SDK write→DB ciphertext assertion incl. rebuild; HMAC dedup; EP/search parity; fail-closed gate; embedding==client vector) → adversarial (key-derivation harness go/no-go; tamper; wrong-key; corrupted wrapper; rotation skew) → leak probes (pre-registered thresholds) → latency (read p50/p95 vs #316 methodology; write-path budget) → E2E (proxy round-trip; MCP tool contract conformance; export-decrypt; backup→restore→decrypt; concurrent writers; degraded no-key read).

### Acceptance criteria
1. No plaintext raw content at rest/transit on guaranteed paths (SDK/REST v1; MCP post-proxy) — DB-inspection incl. post-rebuild.
2. content_hash = keyed HMAC everywhere on guaranteed paths; intra-team dedup + idempotent writes work; no plain-SHA256 content fingerprint reachable.
3. Adversary harness ({API key + traffic + DB + registry + mint endpoint}) cannot decrypt content — automated go/no-go.
4. Key recovery works end-to-end without email (passphrase re-entry → re-wrap → unwrap).
5. Read-latency regression < 5% per-op p50/p95 (search/EP/traversal, #316 methodology); write-path budget met.
6. EP confidence values identical with/without encryption; search = latency parity + quantified recall@k regression within pre-registered threshold (fixture set, plaintext baseline).
7. Leak-quantification probes pass pre-registered thresholds; exceed ⇒ guarantee not shipped (documented).
8. Field-classification matrix implemented exhaustively and review-gated (no unclassified content-derived field; Point FTS resolved with index + backfill).
9. Key rotation without data loss (re-wrap + bulk re-encrypt resume/rollback test).
10. Migration enablement gate executed per tenant class with rollback criteria; legacy-search degradation disclosed + bounded.
11. Artifact updates: ADR-008 addendum, #265 body severed (via #1137), research artifact committed.
12. **Gate (teeth):** epic close requires (a) MCP encrypt-proxy shipped + GA with read/write parity proven, OR (b) dated customer-facing disclosure with hard drop-dead date + expiry mechanism (auto-expire → re-ratify or re-classify by ADR) + non-vacuous slip consequences (content-tool freeze persists indefinitely + indefinite-state public disclosure re-issued + SDK/REST encryption GA withheld) + named accountable owner; guarantee claim scoped to SDK/REST until the proxy lands; disclosure enumerates exactly which surfaces remain plaintext for encrypted teams.

### Runtime prerequisites
- `cryptography==50.0.0` (pinned; AESGCM/HKDF/HMAC/scrypt) — **zero new third-party deps for the crypto core**; client-side embedding needs the `[embeddings]` extra/ONNX on the client (noted — "zero new deps" qualified).
- #316 vector-benchmark methodology (read baseline); new write-path baseline defined in P0.
- #160 hosted embeddings: "implementation complete, verified — **pending merge**" — server-side pipeline bypassed for v1 teams (client-side embedding, model-version pinned); sequencing window pinned in ADR addendum.
- #317 cross-encoder reranker: consumes content (max_length 512 contract, 317 scoping:98) — **v1 disposition: skip rerank for encryptionVersion>=1 teams with disclosed result-parity impact**, or route via proxy; carve-out note filed (extra issue E2).
- Registry schema extension (7714 plan): `wrapped_key`/`key_version`/`kdf_params` on Team node.
- No dependency on #307 (severed; #1137 tracks the #265 body edit — bodies untouched by scoping).

---

## Clarifications (human decisions)

1. **Accepted-leak boundary (embeddings / _searchText / client-derived snippet field):** ADR-008 accepts clear embeddings + _searchText; the 08-12 CRITICALs push further (encrypt content-derived). The plan ships clear-with-quantified-bound + disclosure. Human sign-off needed on: clear-and-bounded (plan default) vs tighter (Tresorit-level: encrypt embeddings, lose vector search) vs looser (no snippet field).
2. **Migration of pre-existing plaintext:** backfill re-encrypt (client-side sweep) vs documented legacy-clear vs export+reingest. Human sign-off needed (data volume unknown; product risk).
3. **Hosted MCP interim:** v1 guarantee = SDK/REST; MCP content-bearing tools frozen for encrypted teams until the proxy ships (criterion 12). Human sign-off on the interim state + disclosure wording.
4. **Dashboard:** non-guaranteed surface for encrypted teams (metadata-only content views); NO browser key holding in v1. Human sign-off on the carve-out.
5. **Data-loss acceptance:** losing passphrase + all agent keychain copies = permanent data loss (provider cannot help by design). Human sign-off on the disclosure.
6. **Team passphrase UX:** per-session passphrase entry vs OS-keychain cache (documented weaker). Human sign-off on v1 default.
7. **#160 sequencing:** client-side embedding for v1 encrypted teams bypasses the pending-merge #160 server pipeline — sign-off that client-side embedding (already the SDK behavior, sdk.py:737) is the v1 path.

---

### Axis Research (findings-date 2026-08-13; 6 queries post-dedup, cap 10)

> **Dedup:** ADR-008 covers the option analysis (A/B/C/D) + clear-metadata choice — deduped; #316 scoping covers C4 latency methodology — deduped. Fresh queries fired for ZK posture reality, competitor E2EE agent memory, key lifecycle/recovery, blind-index content-hash, team onboarding.

- **Architecture (high):** canonical — CipherSweet (ciphersweet.paragonie.com/security): non-deterministic AEAD (AES-GCM) + deterministic *truncated keyed hash* blind index, HKDF-distinct keys, Bloom-filter semantics, coincidence bound 2 ≤ C < sqrt(R); WorkOS (workos.com/blog/cryptographic-key-isolation-multi-tenant-saas): DEK/KEK envelope, KEK rotation without re-encryption, per-tenant KEK. competitor-precedent — Acontext (docs.acontext.io/security/encryption): E2EE knowledge base, API key = auth + embedded KEK, rotation preserves key, dedup disabled, no recovery by design; memlawb (github.com/Gitlawb/memlawb): client-side E2EE memory, server sees namespace/entry-key/ciphertext/hash/timestamps, recall client-side; Mnemosyne (github.com/mnemosyne-oss/mnemosyne docs/security.md): client-side E2EE sync, encrypts content+importance+type+embeddings, server sees routing metadata only, comparison table confirms Zep = "BYOK only (server-managed)", Mem0 = no client-side encryption; Zep/Mem0/Graphiti (majorlabs.co/reports/state-of-agent-memory): no incumbent ships client-side E2EE with server-side search; Tresorit (tresorit.com/security, /features/zero-knowledge-encryption, support search article): true ZK = server cannot index content, search limited to filenames/extensions local-only; Proton Mail (proton.me/blog/engineering-message-content-search): SSE "mostly academic", chose client-side local index (AES-GCM) + metadata search. pitfalls — DSN'17 Information Leakage in Encrypted Deduplication: MLE/deterministic dedup leaks via frequency analysis; Bellare et al. MLE (iacr 2012/631): convergent encryption brute-force on predictable content; Cornell WhatsApp E2E backup injection attack: dedup + FTS structure exploited cross-user; ADHDecode field-level CSE case study: password-derived keys break forgot-password ("data remains encrypted. Forever.").
- **Ontology/metadata (high):** canonical — CipherSweet leak calculus (above); ORF paper (eprint.iacr.org/2022/1044): keyed PRF for identifiers prevents metadata leakage from low-entropy values, multi-device key sharing + revocation; security.SE 187883: HMAC(plaintext) under protected secret = secure lookup index. competitor-precedent — memlawb metadata-minimization table; Mnemosyne routing-metadata-only; Tresorit non-convergent cryptography (no cross-user equality); Acontext no-dedup (no equality oracle). pitfalls — frequency analysis on equality oracles (DSN'17); FTS-index structural leakage (WhatsApp attack) — accepted for _searchText under C2 with documented bound.
- **Key lifecycle (high, within Architecture):** canonical — amgres.com/blog/zero-knowledge-key-recovery: 4 recovery patterns (Shamir K-of-N; passphrase-derived KEK — offline-attackable, hard KDF + two-key refinement; escrow = NOT pure ZK); "email-based recovery = only as private as the user's email"; Bitwarden (blog + help): ZK personal vaults unrecoverable, ORG recovery via recovery-key wrapping, Invite→Accept→Confirm + fingerprint verification, SSO Key Connector. competitor-precedent — Acontext: lost key = data lost by design; memlawb: passphrase (Argon2id)/keychain/Shamir options. pitfalls — ADHDecode: password-derived key breaks forgot-password; Acontext key-in-API-key → key loss = data loss; WorkOS: "do not build a key registry in your own database" (v1 accepts minimal registry copy — we are the SDK, not a KMS vendor).
- **UX (low-medium):** Bitwarden org onboarding + recovery rehearsal guidance (amgres: force rehearsal at setup, plain-language data-loss disclosure, offline-first); Acontext "copy and store your API key somewhere safe". Implication: team-passphrase setup ceremony + explicit data-loss disclosure; zero per-agent ceremony in v1 beyond passphrase entry.
- **Library-deps (LOW):** `cryptography==50.0.0` pinned; AESGCM/HKDF/HMAC/scrypt verified present in 50.0.0 (scrypt n=2^17 derives with default maxmem; FIPS-provider scrypt limitation noted). Codebase-first precedent: crypto.py Fernet + auth.py pepper/PBKDF2 + _searchText pattern. **Zero new deps.**

### Integration Docs

| Dep | Version | Status | API surface / findings |
|---|---|---|---|
| cryptography (pyca) | 50.0.0 | already pinned (requirements.txt:244) | `hazmat.primitives.ciphers.aead.AESGCM` (encrypt/decrypt, 96-bit nonce, 128-bit tag, AAD); `hazmat.primitives.kdf.hkdf.HKDF`; `hazmat.primitives.hashes.HMAC/SHA256`; `hazmat.primitives.kdf.scrypt.Scrypt` (n=2^17/r=8/p=1 verified; 1GiB maxmem; not in FIPS provider — noted). No in-repo AESGCM precedent (only Fernet). |
| sentence-transformers / ONNX (`[embeddings]` extra) | — | existing extra | Client-side embedding (all-MiniLM-L6-v2/384, embeddings.py:148) — qualified dep: "zero new deps" applies to crypto core; client embedding weight already required for SDK embedding today. |
| #316 | — | scoping + research committed | Read-latency baseline methodology (p50/p95/p99, warm-up, ≥100 samples, degradation chain, E2E-8 p95≤300ms). |
| #160 | — | "implementation complete, verified — pending merge" | Server-side embedding pipeline — bypassed for v1 encrypted teams (client-side embedding, model-version pinned); sequencing pin in ADR addendum. |
| #317 | — | scoping + research committed | Cross-encoder reranker consumes content (max_length 512) — v1: skip for encrypted teams, disclosed impact (E2). |
| #307 / #1137 | — | #307 scoped (email integration, NOT encryption keys); #1137 open chore | Dependency severed; #1137 tracks #265 body edit; HKDF text in #1137/307-doc superseded (E1). |

---

## Rejected Alternatives

- **Approach A (SDK-embedded only, MCP excluded):** would have been better if the product explicitly accepted an SDK/REST-only guarantee at v1 with MCP exclusion for a release window. Rejected: primary surface (Claude Desktop → hosted /mcp) unguaranteed weakens the epic's reason to exist; equal-status exclusion = the "contractual" guarantee the issue rejects.
- **Approach B (proxy-first, all paths at v1):** would have been better if enterprise MCP-only usage were the launch cohort and single-mode ops preferred. Rejected: no sequencing benefit; higher risk; C's Phase-2 proxy is B's work.
- **Approach D (server-side KMS isolation):** would have been better if the product later adopted a weaker BYOK/compliance posture (separate ADR). Rejected: KMS compromise/subpoena decrypts — violates "cryptographic guarantee, not contractual" (ADR-008 Option B already rejected).
- **HKDF-from-API-key key derivation:** would have been better if "API key leak = content compromise" were acceptable and no recovery needed. Rejected: provider receives the API key on every request → provider-derivable; contradicts "customer key never possessed" (R1, verified by both gate cycles).
- **Argon2id passphrase KDF:** would have been better if passphrase-strength protection were critical-path and a new dep acceptable. Rejected for v1: pyca scrypt (zero-new-dep) with n=2^17; Argon2id = documented follow-up.
- **Direct KEK encryption (no DEK/KEK):** v1 keeps direct (matches ADR encryptionVersion bump); DEK/KEK envelope (WorkOS) = the documented rotation-without-reencryption upgrade path; second bulk re-encrypt under the new hierarchy documented.
- **Tresorit-level (encrypt embeddings, disable server search):** would have been better if C2 search parity were not a product requirement. Rejected: breaks vector search; quantified-bound + disclosure is the v1 compromise; Proton-style client-side search index = v2 extensibility.
- **Searchable encryption (SSE/homomorphic) and TEE:** pre-rejected by ADR-008 (SSE: academic, no vector support; TEE: multi-month, fragile) — the crypto-technique axis is closed by ADR-008; the topology/sequencing axis is what this scoping diverged.

---

## Wiring Check

| Touch Point | Type | Covered By | Status |
|---|---|---|---|
| Tenant graph content fields (Point/Document/Event/operator) | Data store | P2 (encrypt) + field matrix (P0) | ✅ |
| content_hash sites (sdk.py:685, :928, GraphEvent payload, projection :872) | Data store | P2 keyed HMAC + probe threshold | ✅ |
| Control-plane registry Team node (wrapped_key/key_version/kdf_params) | Data store | P3 + 7714 plan reservation | ✅ |
| JSONL event log / rebuild (sdk.py:568-590) | Data store | P2 encrypted-verbatim snapshots + version-dependent replay | ✅ |
| hosted_backup blobs + restore | Data store | P2/P5 ciphertext-inherits + backup→restore→decrypt test | ✅ |
| POST /v1/points + /v1/teams/{id}/export + /v1/topics/{topic}/summary | API | P2 payload contract + fail-closed gate; P5 export-decrypt; P2 metadata-only summary | ✅ |
| MCP tools (79, TOOL_REGISTRY) | API | P4 transport-level matrix transform + conformance matrix; interim freeze (criterion 12) | ✅ |
| Reveal endpoints | API | none for encrypted teams (explicit) | ✅ |
| API-key mint (hosted_api.py:1643/3953) | Auth | P3: never touches key material | ✅ |
| Team passphrase (never transmitted) | Auth | P3 ceremony + recovery | ✅ |
| Two-key split (integrity key server-side) | Auth | P0/P1 design + HMAC verification | ✅ |
| External services | — | none new; client LLM summarization = follow-up (not v1) | ✅ |
| Dashboard (main.jsx:1332 turn.content / :1349 ep.content) | UI | carve-out: non-guaranteed metadata-only view + disclosure (C4) | ✅ |
| SDK/CLI setup + recovery UX | UI | P3 ceremony + data-loss disclosure | ✅ |
| Logging/analytics/metering | Cross-cutting | analytics verified content-free (endpoint/method/team); no plaintext logging rule; metering counts | ✅ |
| Enablement gate / feature flag (encryptionVersion) | Cross-cutting | P5 migration + old-SDK 4xx | ✅ |
| #307 / #1137 | External dep | severed; #1137 tracks body edit; E1 corrects HKDF text | ✅ |
| #317 reranker | External dep | v1 skip for encrypted teams + disclosed impact (E2) | ✅ |
| #316 baseline / #160 embeddings | External dep | P0/P5 + ADR addendum pin | ✅ |

Coverage verified via REST search (no parallel encryption implementation in flight; #1137 open tracking the sever; no open encryptionVersion/_searchText work). **HARD-GATE: no wiring gaps — all touch points covered.**

---

## Review Cycle Log

- problem-verify: Cycle 1 (A P0=0 P1=4 P2=3 P3=3; B P0=0 P1=3 P2=6 P3=2) → fixes applied → Cycle 2 (A P0=0 P1=6 P2=3 P3=0; B P0=0 P1=5 P2=4 P3=2) → incorporated (R1–R6) → **PASSED** (2 cycles, max re-dispatch used).
- solution-verify: Cycle 1 (A P0=0 P1=4 P2=6; B P0=0 P1=4 P2=5) → S-Fix 1–7 → Cycle 2 (A P0=0 P1=4 P2=8; B P0=0 P1=3 P2=11) → incorporated (C2-1..C2-6) → **PASSED** (2 cycles, max re-dispatch used).
- Phase 5.6 coherence: **[QWEN-GATE] substitute reviewer** — P1×3 (artifacts persisted; #1137/307-doc HKDF text; target rewording) + P2×4 incorporated → coherent, no re-run needed (substitute, single pass per instructions).
- No stuckness/escalation triggers fired (no issue re-flagged 3 consecutive cycles; no P0 survived a cycle).
- Missing infra (parallel_work_check, approval router, ui_prototype): **skipped with note** — not present on this machine; UX_RATING low (key-setup UX only), no prototype gate needed.

---

## Complexity

| Domain | Rating | Basis |
|---|---|---|
| Architecture | high | Encryption envelope, key lifecycle, two-key model, execution-point redesign (MCP proxy), latency preservation |
| Ontology | high | v3.7 state-centric metadata classification (C3), field matrix vs record spec |
| Security | high | Threat model, adversary harness, key custody, no-plaintext invariant |
| UX | low | SDK/CLI key-setup + recovery ceremony; dashboard carve-out (non-guaranteed) |
| Performance | medium | <5% read-latency + write-path budget; #316 methodology; proxy latency |
| Data/migration | medium | Enablement gate, per-tenant cutover, bulk re-encrypt, backfill |
| Dependencies | low | Zero new deps (pyca cryptography pinned); #316/#317/#160 consumers; no #307 |
| **Overall** | **complex** | Matches issue's expected tier |
