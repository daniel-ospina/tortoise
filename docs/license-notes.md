# Tortoise License Notes (#338 D3)

**Date:** 2026-08-07
**Status:** draft — pending owner/legal approval (G3 human gate)
**Decision:** [Supersedes DEC-002 AGPLv3-dual for public positioning — owner decision 2026-08-07]

---

## 1. Decision

Tortoise is licensed under **Business Source License 1.1 (BUSL-1.1)**:

- **Self-hosted:** free production use for organizations under **US $5,000,000** annual revenue (trailing 12 months); above threshold requires a commercial license.
- **Hosted (api.premiselabs.co):** commercial subscription with a free tier — **NOT covered** by the BSL grant.
- **Change Date:** 4 years from publication of each version → converts to **Mozilla Public License 2.0**.
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
| Change Date → Change License (MPL 2.0) | HashiCorp BSL→MPL 2.0 (enterprise-safe conversion); BUSL-1.1 §"Effective on the Change Date" | https://github.com/hashicorp/terraform/blob/main/LICENSE · https://web.archive.org/web/2024*/redis.io/legal/bsl/ (Redis original BSL page archived) |

## 3. Copyright / CLA Audit (P0.1)

**Method:** `git log --format='%an' | sort | uniq -c` across the full history.

**Result (2026-08-07):**

| Author | Commits | Notes |
|---|---|---|
| `daniel-ospina` | 536 | Primary author |
| `Daniel Ospina` | 1 | Case-variant of the same human (git config drift) — reconciled as one contributor |
| `fly-io[bot]` / `Fly.io` | 2 | Deployment bot — no copyrightable contribution |

**Total:** 539 commits (as of PR #554 head, 2026-08-07), **single human copyright holder** (Daniel Ospina). No third-party contributions exist, so no CLA-reconciliation risk for the AGPL→BSL relicense. The prior LICENSE's "CLA available" note is superseded — CLA remains available for future contributors (MPL 2.0 re-licensing path preserved via the Change License).

## 4. FAQ Drafting Notes (for README T5.1)

- **"Is BSL open source?"** BSL is source-available, not OSI-approved. Code is public, modifiable, and non-production-use free; production use is free under the $5M AUG. Every version converts to Mozilla Public License 2.0 four years after publication.
- **"Why not AGPL/MIT?"** AGPL on an imported library blocks MIT products the same way BSL does — the *service model* is what moves the license boundary to the network (the fix for David Waring's objection). BSL + revenue threshold protects the self-host/trust segment while enabling monetization. **MPL 2.0 as the Change License** is the enterprise-safe conversion: file-level copyleft, OSI-approved, not on AGPL/SSPL ban lists, and embeddable in proprietary products post-conversion (HashiCorp precedent).
- **"When do I need a commercial license?"** Self-hosted production use by an organization whose trailing-12-month revenue exceeds $5M USD; or offering Tortoise to third parties as a hosted/managed service.
- **"Is the hosted service covered?"** No — hosted (api.premiselabs.co) is a separate commercial product with a free tier. The BSL grant governs self-hosted copies only.
- **"What happens in 4 years?"** Each version converts to **Mozilla Public License 2.0** (file-level copyleft — enterprise-safe) on its Change Date. Old versions stay under original terms until their own Change Date (version-specific, per BUSL-1.1).

## 6. Template Fidelity (owner mandate: "template as much as possible, just fill in the name")

The LICENSE file mirrors the **standard BSL adopter template** (MariaDB MaxScale
`licenses/LICENSE2408.TXT` structure; HashiCorp Terraform formatting) — plain text,
no Markdown, only the Parameters block customized:

| File element | Source (template, not innovation) |
|---|---|
| `License text copyright (c) 2020 MariaDB Corporation Ab...` header | HashiCorp Terraform LICENSE / MariaDB MaxScale LICENSE |
| `Parameters` block (Licensor / Licensed Work / AUG / Change Date / Change License) | HashiCorp + MariaDB template (fill-in-the-blank) |
| "For information about alternative licensing arrangements..." contact line | HashiCorp pattern; points to the GitHub repo issues (no company email exists yet — the repo URL cannot go stale) |
| `Notice` — "not an Open Source license... eventually made available under an Open Source License" | MariaDB MaxScale LICENSE Notice (verbatim standard text) |
| `Business Source License 1.1` / `Terms` headings + canonical terms | SPDX BUSL-1.1 canonical text (verbatim) |

**Covenants of Licensor — included (full canonical text).** The SPDX BUSL-1.1
text includes a "Covenants of Licensor" section whose covenant #1 requires a
**GPL-2.0-compatible Change License**. Our Change License is **Mozilla Public
License 2.0**, which IS GPL-2.0-compatible (file-level copyleft) — so the full
canonical text ships verbatim, no trimming (MariaDB MaxScale ships the same full
text). *Historical note: an earlier Apache 2.0 choice was rejected because Apache
2.0 is GPLv3-compatible but not GPLv2-compatible (ASF, FSF, FOSSA) — Apache would
have forced trimming the Covenants and offered no post-conversion service moat.*

**GitHub templates:** GitHub's license picker does NOT offer Business Source License
(`bsl-1.0` in the picker = Boost Software License 1.0). The template source is the
MariaDB steward text + adopter files (linked in §2), not GitHub.

## 5. Known Trade-offs (from research; accepted by owner decision)

- BSL is source-available, not OSI-approved → distro-inclusion blocked during restricted period; some enterprise allowlists exclude it (same class as AGPL/SSPL).
- Fork risk (Valkey/OpenTofu pattern) when value appears gated — mitigated by the $5M AUG granting real free production use and the 4-year MPL 2.0 conversion.
- Threshold-crossing confusion ("when do I pay?") — addressed by the FAQ + clear revenue definition in the AUG.
- Enforcement is manual (no technical license checks).
