---
title: "Research Brief — Tortoise Hosted Platform"
type: synthesis
domain: capability
doc_status: live
created: 2026-08-13
ownedBy: epistemic-team
---

# Research Brief — Tortoise Hosted Platform

Context for issues sliced from epic #7618 (hosted architecture, multi-tenancy, pack isolation).

## Raw Notes

### 2026-08-13T15:10:00Z — [canonical] Issue #318 Phase 1.5 — Axis Research (Architecture — per-tenant pack isolation)

### 1. Canonical — shared catalog + tenant context, not copies
- Multi-tenant extensibility (dev.to / WorkOS / knowledgelib.io): shared app instance, tenant identity flows through call chain, extension registry merges GLOBAL + TENANT-SPECIFIC hooks at dispatch time; trusted tenant_id through middleware.
- AWS SaaS Lens: isolation is a LOGICAL construct enforced by runtime policy, not siloed resources; tier-based isolation packages isolation flavors per tenant; pool model + premium silo tier; silo environments must run the same product version as pooled.

### 2. Competitor-precedent — per-tenant INSTALL-STATE, never copies
- Crystallize: Plugin Store (public catalog, active state only) + Plugin Registry (which tenant, which plugin, which revision, config, scopes, secrets); lifecycle pending→active→inactive; revision locking.
- Stella Ops: Postgres plugin registry; plugin_instances table (plugin_id FK, tenant_id, config JSONB, enabled, status) — shared catalog + per-tenant install state; 9 lifecycle states.
- DuploCloud: plugin_data.{tenant_id}.{plugin_id}.{collection} namespace; token-scoped isolation; migrations scoped per plugin.
- Spree Commerce: "no per-tenant installation", no version drift in global rollouts. Kong: tenant-specific plugin must be installed on ALL data planes or loading fails — install-state parity problem.

### 3. Pitfalls — cross-tenant leakage & drift
- Payload CMS multi-tenant plugin: version/upgrade problems reported by real users (payloadcms/payload#11071).
- Autonoma multi-tenancy testing: cross-tenant reads, missing tenant filters, ASYNC CONTEXT LOSS, cache-related leakage. safeguard.sh: client-supplied tenant context, context loss in async paths, leakage at edges.
- Directly validates pre-mortem "singleton leak": process-global caches keyed by tenant = async context loss class bug.

### 4. Adversarial — when per-tenant isolation of shared config is actually required
- Per-tenant SILO isolation is a PAID TIER driven by compliance/residency (FedRAMP, HIPAA, EU residency): "shared template, isolated execution"; silo as premium tier, pool for everyone else (AWS SaaS Lens tier-based isolation, aws public-sector sovereignty, iancloud.ai datacenter-per-customer).
- Implication: pack isolation tiers are a PRICING/GTM decision tied to a future enterprise tier — not something to pre-build now; governance hooks without an enterprise tier are phantom work.

### 2026-08-13T15:35:00Z — [competitor] Issue #318 Phase 2.5 fix — counter-evidence sources (converge citations)
- Salesforce metadata-driven multitenancy: one shared kernel; tenant customization is a metadata overlay partitioned by OrgID — platform objects evolve centrally, tenants customize in isolation (Gearset/Flosum ecosystem docs, dev.to multi-tenant extensibility survey).
- Shopify: app versions/extensions versioned centrally and propagated to installed stores; tenants hold install-state, not copies.
- VS Code Marketplace: centrally signed, immutable, versioned catalog; governance = publisher identity + signatures; staleness solved by central auto-update, never by per-client copies.

### 2026-08-13T15:35:00Z — [canonical] Issue #318 Phase 2.5 — Ontology axis dedup note
- Ontology axis (shared-vs-tenant kind semantics, namespacing) for THIS slice rated LOW: no vocabulary change (manifest v3 + kind partitioning already landed in #949/#950/#951); kind lifecycle/schema-versioning governance deferred per D4. Deduplicated against the Architecture registry-install-state findings above (Crystallize/Stella Ops lifecycle states cover the registry-semantics ground) — no separate ontology queries fired. Re-open when custom-pack authoring is scoped.
- **2026-08-13T10:10:49** [canonical] AXIS RESEARCH — Issue #307 Email Integration (invites + key recovery). Date 2026-08-13.

[canonical] Resend API surface (resend.com/docs): REST base https://api.resend.com; auth Bearer re_<key>; User-Agent header REQUIRED (403 without); rate limit 10 req/s per TEAM (all keys); idempotency keys via headers (24h expiry, 256 chars); send email params: from, to[] (max 50), subject, html, text, reply_to, bcc, cc, tags, template{id,variables}; response 200 {id}. Test addresses delivered/bounced/complained/suppressed@resend.dev simulate events w/o reputation damage; 422 on @example.com/@test.com. Webhooks: email.bounced/delivered/delivery_delayed/complained; at-least-once with svix-id dedup; retry 5s/5m/30m/2h/5h/10h; order not guaranteed. Domain verification: DNS TXT/SPF/DKIM, subdomain recommended, verify in ~15min-72h. Python SDK 'resend' v2.35.0 (PyPI, MIT, py>=3.7; async via httpx extra). Source: resend.com/docs (add-a-domain, api-reference/emails/send-email, create-an-api-key, webhooks, testing KB).
- **2026-08-13T10:11:03** [competitor] [competitor-precedent] Team invitation flows: (1) securepatterns.dev 'Designing a Safe Team Invitation Flow' — token = proof of link possession, NOT email verification; accept requires independently verified identity (signin/signup w/ email verification); POST-only accept, GET is side-effect-free; unauthenticated accept → 401; session email-match guard; 7-day member / 24-48h admin TTL; CSPRNG >=256-bit; SHA-256(token) stored; rate-limit accept per-token/IP/global; Referrer-Policy no-referrer on landing; link host from config never Host header; revoke supersedes pending. (2) viprasol.com SaaS team invitations — Resend sendInvitationEmail with token-in-URL (/invite/accept?token=), separate accept flows new/existing users, revoke-old-on-new, seat-limit at creation. (3) skycloak.io invitation lifecycle — manual accept required for all auth types, 24h expiry, email verification for new users.
- **2026-08-13T10:11:05** [pitfalls] [pitfalls] Transactional email production failures: photonconsole.com — 200 from provider != delivered; retry 4xx transient vs 5xx permanent (retrying 5xx burns reputation); spam-folder routing is invisible to SMTP metrics (needs inbox-placement/seed tests); delivery latency vs token expiry (greylisting 15min can exceed OTP validity); fixed-interval retry storms under throttle (use exponential backoff). courier.com — healthy: delivered 98-99%, hard bounce <0.5%, spam complaints <0.3%; inbox placement is the real metric; idempotency keys + durable queue prevent duplicates; suppression list wired to bounce/complaint webhooks. Webhook processing must be idempotent (provider at-least-once), ack fast, decouple ingestion from processing, DLQ exhausted retries. DVARA flightdeck — thin internal send layer: durable delivery record, idempotency, exp backoff (30s..120s x5), DLQ 30d, log transport for dev/CI.
- **2026-08-13T10:11:08** [adversarial] [adversarial] Never email secrets: OWASP Forgot-Password Cheat Sheet — never send password/key in email; use URL token (single-use, time-limited, hashed at rest, CSPRNG >=128 bits); don't trust Host header (Host-header injection); HTTPS; noreferrer; rate limit per-account+per-IP (3-5/email/hr, 5-10/IP verify attempts); identical response for known/unknown accounts (anti-enumeration) incl. timing; notify out-of-band on recovery completion. guptadeepak.com CIAM compass — recovery flows probed before login; 5/hr/account + 50/hr/IP; unverified-email recovery = ATO vector. ttl.space/LinkPilot/PrivateNote — email creates durable searchable copies; one-time burn-after-read link + passphrase via second channel is the strongest simple pattern; scoped+expiring keys limit blast radius. Magic links follow same rules (single-use, time-limited, stored hashed).
