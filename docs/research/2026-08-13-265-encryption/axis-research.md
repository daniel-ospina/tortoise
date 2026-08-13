---
title: "Research: #265 — Client-Side Content Encryption (Axis Research + Integration Docs)"
type: synthesis
domain: capability
doc_status: live
created: 2026-08-13
ownedBy: epistemic-team
---

# Research: #265 — Client-Side Content Encryption (Axis Research + Integration Docs)

**Date:** 2026-08-13 · **Issue:** daniel-ospina/tortoise#265 · **Method:** issue-scoping Phase 1.5 (codebase-first precedent scan → 6 external queries post-dedup, cap 10; exa MCP used after Perplexity 429)

## Axis ratings
Architecture **high** · Ontology/metadata **high** · UX low-medium · Library-deps **low** (cryptography==50.0.0 pinned).

## Dedup (PRIOR_RESEARCH)
- ADR-008 (docs/adr/ADR-008-client-side-content-encryption.md) covers the option analysis (A/B/C/D), clear-metadata choice, _searchText extension — deduped.
- #316 scoping (docs/scoping/2026-08-13-316-vector-benchmark-scoping.md) covers C4 latency methodology — deduped.

## Architecture axis

### canonical
- **CipherSweet** (Paragon Initiative, ciphersweet.paragonie.com/security): non-deterministic AEAD (AES-GCM) for encryption; deterministic *truncated keyed hash* (HMAC-SHA384 / keyed BLAKE2b, HKDF-distinct keys) as blind index for SELECT-able lookup; Bloom-filter semantics bound leakage; guidance: coincidence count C satisfies 2 ≤ C < sqrt(R). → source for the keyed-HMAC content_hash + leak calculus.
- **WorkOS** (workos.com/blog/cryptographic-key-isolation-multi-tenant-saas): DEK/KEK envelope — KEK rotation without re-encryption; per-tenant KEK; "do not build a key registry in your own database". → source for the rotation-without-reencryption upgrade path (v1 direct KEK accepted; envelope = documented upgrade).

### competitor-precedent
- **Acontext** (docs.acontext.io/security/encryption): E2EE knowledge-base SaaS; API key = auth secret + embedded encrypted master key (KEK); rotation replaces auth secret only (no re-encryption); per-object DEK; server stores only wrapped DEKs + ciphertext; **dedup disabled**; lost key = data lost, no recovery, "by design". → closest architectural precedent; key-in-credential pattern evaluated and REJECTED for #265 (provider-issued token; issuance leakage; passphrase-wrapped registry copy chosen instead).
- **memlawb** (github.com/Gitlawb/memlawb): client-side E2EE agent memory; server sees namespace id, entry key, ciphertext, content hash, timestamps; recall client-side; metadata-minimization tier-2 hashes entry keys; key custody: passphrase (Argon2id) / OS keychain / Shamir. → metadata-minimization + key-custody options.
- **Mnemosyne** (github.com/mnemosyne-oss/mnemosyne docs/security.md): client-side E2EE sync; encrypts content + importance + type + **embeddings**; server sees routing metadata only; comparison table: Zep = "BYOK only (server-managed)", Mem0 = no client-side encryption, Letta/Honcho = none. → embedding-encryption precedent (tightening option; rejected for #265 v1 — breaks C2).
- **Zep / Mem0 / Graphiti** (majorlabs.co/reports/state-of-agent-memory; medium.com comparison): no incumbent ships client-side E2EE with server-side search; Zep BYOK = server can still decrypt; Mem0 hash = dedup content-hash, not signature. → validates #265's differentiation + the C2 tension.
- **Tresorit** (tresorit.com/security, /features/zero-knowledge-encryption, support "Search in Tresorit"): zero-knowledge E2EE; server cannot index content → search limited to filenames/extensions, index local; non-convergent cryptography (no cross-user content equality). → true-ZK posture = no server-side content search.
- **Proton Mail** (proton.me/blog/engineering-message-content-search): SSE "mostly limited to academic interest"; chose client-side local index (AES-GCM under user key) + server-side metadata search. → confirms SSE rejection + the client-side-search v2 extensibility path.

### pitfalls
- **DSN'17 "Information Leakage in Encrypted Deduplication"** (cse.cuhk.edu.hk): MLE/deterministic dedup leaks via frequency analysis.
- **Bellare et al., Message-Locked Encryption** (eprint.iacr.org/2012/631): convergent encryption brute-force on predictable content.
- **Cornell "Injection Attacks Against E2E Encrypted Applications"** (WhatsApp case): dedup + FTS index structure exploited for cross-user attacks.
- **ADHDecode client-side field-level encryption case study** (adhdecode.com): password-derived keys break forgot-password — "data remains encrypted. Forever."; KMS-managed keys shift trust to KMS; debugging encrypted systems is hard.

## Ontology/metadata axis (C3 — state-centric lifecycle metadata)

### canonical
- CipherSweet blind-index leak calculus (above).
- **ORF — Oblivious Revocable Functions** (eprint.iacr.org/2022/1044): keyed PRF for identifiers prevents metadata leakage from low-entropy values; multi-device key sharing + revocation.
- **security.stackexchange.com 187883**: HMAC(plaintext) under a well-protected secret = secure lookup index.

### competitor-precedent
- memlawb metadata table (server sees route data only); Mnemosyne routing-metadata-only; Tresorit non-convergent (no equality oracle); Acontext no-dedup (no equality oracle at all).

### pitfalls
- Frequency analysis on equality oracles (DSN'17); FTS term-distribution leakage (WhatsApp attack) — accepted for _searchText under C2 with documented bound; truncated-blind-index coincidence bounds (CipherSweet).

## Key lifecycle axis

### canonical
- **amgres.com/blog/zero-knowledge-key-recovery**: 4 recovery patterns — (1) Shamir K-of-N shares; (2) passphrase-derived KEK wrapping master key (offline-attackable → hard KDF (Argon2id/scrypt) + two-key refinement passphrase XOR device-secret); (3) guardians; (4) escrow/HSM = NOT pure ZK. "A recovery flow that ends in 'the cloud holds the key' has converted a ZK system into a regular cloud-key-managed system." "Email-based recovery = only as private as the user's email." Recovery-UX: force rehearsal at setup; plain-language permanent-data-loss disclosure; offline-first.
- **Bitwarden** (bitwarden.com/blog/end-to-end-encryption-and-zero-knowledge, help docs): ZK = personal vaults unrecoverable without master password; ORG recovery via recovery-key wrapping (admin reset without breaking org ZK); team onboarding Invite→Accept→Confirm + fingerprint-phrase verification; SSO Key Connector (customer-managed decryption key server).

### competitor-precedent
- Acontext: lost API key = permanent data loss, no recovery, by design.
- memlawb: passphrase (Argon2id) / OS keychain / Shamir split options.

### pitfalls
- ADHDecode: password-derived keys break forgot-password (data loss).
- Acontext key-in-API-key: key loss = data loss; key re-sent in Authorization header each request (leakage surface — rejected for #265).
- WorkOS: key registry in your own DB = high-value target (v1 accepts minimal wrapped-key registry copy; we are the SDK, not a KMS vendor).

## UX axis (low-medium)
- Bitwarden org onboarding (Invite→Accept→Confirm + recovery enrollment policy); amgres recovery-UX guidance (rehearsal at setup, plain-language disclosure, offline-first); Acontext "copy and store your API key somewhere safe".
- Implication for #265: team-passphrase setup ceremony + explicit data-loss disclosure at setup; zero per-agent ceremony in v1 beyond passphrase entry (or keychain cache, documented weaker).

## Library-deps axis (low)
- `cryptography==50.0.0` (pyca) pinned (requirements.txt:244). Verified present: `AESGCM`, `HKDF`, `HMAC`/`SHA256`, `Scrypt` (n=2^17/r=8/p=1 derives with default maxmem; 1GiB max_memory; n<2^(128r/8) constraint; **not available in pyca FIPS-mode provider** — noted for ADR addendum).
- Codebase-first precedent: crypto.py (Fernet), auth.py (PBKDF2 + TORTOISE_SECRET_PEPPER), _searchText Document pattern.
- **Zero new third-party deps for the crypto core.** Client-side embedding requires the existing `[embeddings]` extra / ONNX (sentence-transformers, all-MiniLM-L6-v2/384) — qualified: "zero new deps" applies to the crypto core; embedding weight already a client requirement today (embeddings.py:148).

## Integration Docs (drafted at solution-converge; verified by solution-verify + coherence)

| Dep | Version | Status | API surface / findings |
|---|---|---|---|
| cryptography (pyca) | 50.0.0 | already pinned (requirements.txt:244) | `hazmat.primitives.ciphers.aead.AESGCM` (96-bit nonce, 128-bit tag, AAD); `kdf.hkdf.HKDF`; `hashes.HMAC`/`SHA256`; `kdf.scrypt.Scrypt` (n=2^17, versioned; FIPS-provider limitation). No in-repo AESGCM precedent (Fernet only). |
| sentence-transformers / ONNX (`[embeddings]` extra) | — | existing extra | Client-side embedding (all-MiniLM-L6-v2/384). Model + dimension + version must be pinned + write-time model-version check (P2). |
| #316 | — | scoping + research committed | Read-latency baseline methodology (p50/p95/p99, warm-up, ≥100 samples, degradation chain, E2E-8 p95≤300ms). |
| #160 | — | "implementation complete, verified — pending merge" | Server-side embedding pipeline — bypassed for v1 encrypted teams (client-side embedding); sequencing window pinned in ADR addendum. |
| #317 | — | scoping + research committed | Cross-encoder reranker consumes content (max_length 512 contract, 317 scoping:98) — v1: skip for encrypted teams, disclosed impact (extra issue E2). |
| #307 / #1137 | — | #307 scoped (email integration, NOT encryption keys); #1137 open chore | #265 dependency severed; #1137 tracks #265 body edit; HKDF-from-API-key text in #1137 + 307 scoping doc superseded by scrypt-passphrase two-key model (extra issue E1). |

## Claims → source confidence

| Claim | Sources | Tier |
|---|---|---|
| True ZK requires giving up server-side content search (or accepting documented leaks) | Tresorit, Proton, memlawb, Mnemosyne (4 independent) | High |
| BYOK (Zep) = server-managed/decryptable; Mem0 = no client-side encryption | Mnemosyne comparison, MajorLabs report, Zep docs (3) | High |
| Plain content_hash/dedup leaks via dictionary/frequency attacks; keyed-HMAC blind index is the canonical fix | CipherSweet, security.SE, DSN'17, Bellare MLE, WhatsApp attack (5) | High |
| Recovery without email is solvable; email recovery = weakest link; data-loss on key loss is inherent to ZK | amgres, Bitwarden, ADHDecode, Acontext (4) | High |
| Key-in-API-key pattern has issuance/header leakage problems | Acontext, WorkOS, security.SE (3) | High |
| AES-GCM ~GB/s throughput → <5% latency feasible; the C2 risk is metadata-bound search, not the cipher | ADR-008, Proton (WebCrypto AES-GCM), domain | Medium |
| Embeddings are semantic fingerprints; inversion attacks exist (attack-contingent bound) | ADR-008 (accepted risk), embedding-inversion literature (via Exa) | Medium ⚠️ |

## Open items flagged to plan
- content_hash production-site enumeration (sdk.py:685, :928, GraphEvent payload, projection :872).
- Snippet field bound pre-registration (chars, abstractive-only, overlap%).
- Bulk re-encrypt job allocation (rotation + migration machinery).
- FIPS-mode scrypt limitation note in ADR addendum.
