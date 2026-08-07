# Tortoise License Notes (#338 D3)

**Date:** 2026-08-07
**Status:** draft — pending owner/legal approval (G3 human gate)
**Decision:** [Supersedes DEC-002 AGPLv3-dual for public positioning — owner decision 2026-08-07]

---

## 1. Decision

Tortoise is licensed under **Business Source License 1.1 (BUSL-1.1)**:

- **Self-hosted:** free production use for organizations under **US $5,000,000** annual revenue (trailing 12 months); above threshold requires a commercial license.
- **Hosted (api.premiselabs.co):** commercial subscription with a free tier — **NOT covered** by the BSL grant.
- **Change Date:** 4 years from publication of each version → converts to **Apache License 2.0**.
- **Anti-resale:** the grant never permits offering Tortoise (or a substantially similar product) to third parties as a hosted/managed service.

The service model is the enabler: adopters **connect** to Tortoise over MCP/REST and never import it — so the BSL boundary sits at the network and MIT-licensed products (e.g. David Waring's) are never bound by Tortoise's license.

## 2. Clause → Precedent Mapping (owner mandate: borrow from precedent)

| Clause in our LICENSE | Precedent (language borrowed from) | Source |
|---|---|---|
| SPDX identifier + canonical BSL text (verbatim) | SPDX `BUSL-1.1` canonical text | https://spdx.org/licenses/BUSL-1.1.html |
| Parameters block structure (Licensor / Licensed Work / AUG / Change Date / Change License) | HashiCorp BSL 1.1 parameter-block formatting | https://www.hashicorp.com/en/bsl |
| AUG paragraph structure ("make production use ... provided that") | Couchbase BSL 1.1 Additional Use Grant pattern | https://www.couchbase.com/blog/couchbase-adopts-bsl-license/ |
| Quantitative threshold — "$5,000,000 annual revenue, trailing 12 months" | Sentry Functional Source License $5M revenue grant; MariaDB MaxScale quantitative AUG (≤3 instances); published €5M BSL AUG example | https://blog.sentry.io/introducing-the-functional-source-license-freedom-without-free-riding/ · https://mariadb.com/bsl-faq-adopting/ |
| Grant exclusions — hosted-as-a-service excluded, anti-resale | Couchbase BSL (no commercial DBaaS/SaaS derivative); HashiCorp (no competing hosted/embedded offering) | https://www.couchbase.com/blog/couchbase-adopts-bsl-license/ · https://www.hashicorp.com/en/bsl |
| Change Date → Change License (Apache 2.0) | Redis BSL→Apache 2.0 conversion; Elasticsearch BSL→Apache 2.0 mechanism; BUSL-1.1 §"Effective on the Change Date" | https://web.archive.org/web/2024*/redis.io/legal/bsl/ (Redis relicensed to RSALv2/AGPLv3 in 2024 — original BSL page archived) |

## 3. Copyright / CLA Audit (P0.1)

**Method:** `git log --format='%an' | sort | uniq -c` across the full history.

**Result (2026-08-07):**

| Author | Commits | Notes |
|---|---|---|
| `daniel-ospina` | 511 | Primary author |
| `Daniel Ospina` | 1 | Case-variant of the same human (git config drift) — reconciled as one contributor |
| `fly-io[bot]` / `Fly.io` | 2 | Deployment bot — no copyrightable contribution |

**Total:** 514 commits, **single human copyright holder** (Daniel Ospina). No third-party contributions exist, so no CLA-reconciliation risk for the AGPL→BSL relicense. The prior LICENSE's "CLA available" note is superseded — CLA remains available for future contributors (Apache 2.0 re-licensing path preserved via the Change License).

## 4. FAQ Drafting Notes (for README T5.1)

- **"Is BSL open source?"** BSL is source-available, not OSI-approved. Code is public, modifiable, and non-production-use free; production use is free under the $5M AUG. Every version converts to Apache 2.0 four years after publication.
- **"Why not AGPL/MIT?"** AGPL on an imported library blocks MIT products the same way BSL does — the *service model* is what moves the license boundary to the network (the fix for David Waring's objection). BSL + revenue threshold protects the self-host/trust segment while enabling monetization; enterprise procurement generally treats BSL/AGPL/SSPL similarly (source-available copyleft), so BSL loses nothing on that axis while keeping a permissive conversion path.
- **"When do I need a commercial license?"** Self-hosted production use by an organization whose trailing-12-month revenue exceeds $5M USD; or offering Tortoise to third parties as a hosted/managed service.
- **"Is the hosted service covered?"** No — hosted (api.premiselabs.co) is a separate commercial product with a free tier. The BSL grant governs self-hosted copies only.
- **"What happens in 4 years?"** Each version converts to Apache 2.0 on its Change Date. Old versions stay under original terms until their own Change Date (version-specific, per BUSL-1.1).

## 5. Known Trade-offs (from research; accepted by owner decision)

- BSL is source-available, not OSI-approved → distro-inclusion blocked during restricted period; some enterprise allowlists exclude it (same class as AGPL/SSPL).
- Fork risk (Valkey/OpenTofu pattern) when value appears gated — mitigated by the $5M AUG granting real free production use and the 4-year Apache 2.0 conversion.
- Threshold-crossing confusion ("when do I pay?") — addressed by the FAQ + clear revenue definition in the AUG.
- Enforcement is manual (no technical license checks).
