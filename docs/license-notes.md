---
title: "Tortoise License Notes (#338 D3)"
type: decisions
domain: legal
doc_status: draft
created: 2026-08-07
ownedBy: epistemic-team
aboutSubjects: tortoise
aboutObjects: tortoise-graph, tortoise-client, tortoise-skills-and-integrations
---

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

## 6. Client/Server Split — thin client license boundary (#526, 2026-08-15)

The #526 package split ships the engine as a server-only distribution
(`tortoise-graph`, BSL-1.1 — unchanged) and a **thin driver** distribution
(`tortoise-client`) under **Apache-2.0**. Full mechanics:
[docs/client-server-split.md](docs/client-server-split.md).

**Why Apache-2.0 for the client (vs MPL-2.0):**

- **Driver-industry norm:** MongoDB ships every driver under Apache-2.0
  explicitly so an application using the driver is *"a separate work"* that
  never inherits server obligations; Redis keeps client libraries
  open-source under its RSAL/SSPL server licenses. Apache-2.0 matches the
  MongoDB-driver analogy exactly.
- **Maximal permissiveness:** Apache-2.0 allows consumers to vendor, modify,
  and relicense the driver with no file-level copyleft obligations — the
  right shape for a thin network driver whose only job is to connect.
  MPL-2.0 (HashiCorp's SDK precedent) would impose file-level copyleft on a
  ~4-module package with no offsetting benefit for the consumer.
- **Boundary is physical, not behavioral:** the client dist contains ONLY
  the client modules (mcp_client, config, exceptions, status_vocabulary)
  re-licensed under Apache-2.0; engine code (sdk, projection, EP) never ships
  in the client wheel. A client-only install cannot contain BSL code.
- **Backstop:** `validation/check-license-surface.py` now asserts the client
  surfaces (client/LICENSE, client/pyproject.toml, client/README.md) declare
  Apache-2.0, and `client/verify_client.sh` (CI) proves no engine module or
  engine dependency is importable/installed from a clean client install.

Pending legal sign-off on the Apache-2.0 choice (issue #526 Q1) — owner
pick, documented here for review.

---

## 7. Consumer Skills Surface — MIT at the skills root (#4366, 2026-09-20)

**OVERRIDES:** the repo-default inheritance of BSL 1.1 over every file that
has no licence of its own — the artifacts a customer copies/adapts are MIT by
owner decision, so the boundary sits at the **network** (§1) and never at an
individual file.

The skills the dashboard **serves** at `app.premiselabs.co/skills/**` are the
same class of artifact as the #526 client dist (the "Client/Server Split"
section): the thing a third party
vendors and adapts, not the engine. They carried **no licence of their own**,
so they inherited the repo's BSL 1.1 by default — which puts the BSL boundary
at the *file* instead of at the *network*, the exact outcome §1 exists to
avoid. Owner decision: *"For integrations and skills/workflows with tortoise we
want that MIT licensed."*

**Mechanism — a per-directory `LICENSE` at the skills root**, following #526's
`client/LICENSE` pattern:

| Path | Licence |
|---|---|
| `website/apps/dashboard/public/skills/LICENSE` | MIT (© 2026 Premise Labs) |
| everything under `website/apps/dashboard/public/skills/` | covered by that file |

**Why not a header on each file?** The served skills are *mirrored* content
(the intended source of truth is the public MIT repo
`daniel-ospina/tortoise-skills-and-integrations`, which itself carries one root
`LICENSE` and no per-file notices). A per-file notice added here would make the
tortoise copy diverge from the copy it mirrors — the same drift the one-source
shape exists to prevent — and `tortoise-onboarding/SKILL.md` is byte-identical
-by-test to `tortoise/onboarding/SKILL.md`, so touching it would force a
licence notice into the product tree as well. The `LICENSE` file is emitted to
`dist/` by the vite build exactly as the note in `public/_headers` describes,
so it is served at `https://app.premiselabs.co/skills/LICENSE`.

**Residual — the notice does not travel inside an installed copy (partially closed, see below).**
`install-tortoise-skills.sh` fetches only `$SKILLS_BASE/<name>/SKILL.md`; it
never fetches `LICENSE`. A customer who runs the installer gets three skills
with no licence notice on disk, so MIT's "included in all copies" condition is
met for the *served tree* the directory licence covers, but not for the
installed copy — the artifact most consumers actually receive. The installer
half of this is **closed below (#4398)**; the installed-copy half remains open.
The remaining mechanisms are: have the installer also fetch `LICENSE` into the
harness dir (a write-logic change, owned by #4327's lane); add the notice once
**upstream** in the MIT repo's per-file content; or inject the notice into the
**served output only** (a build-time rewrite of `dist/skills/*/SKILL.md`, which
leaves the in-tree bytes — and so the
onboarding byte-identity contract — untouched and reaches every installed copy
— under #4365 that is the three reusable capabilities: `tortoise-onboarding` is
now delivered as INSTRUCTIONS and is not installed by the current
`SKILLS_VERSION=v3` installer. A copy an earlier v2 installer already put on
disk is deliberately left in place by v3 (#4327 preservation), so that copy
stays outside this notice-injection reach). The
mechanism adopted for the *installer* half — the notice in the script's own
header — does **not** close this residual: an installed `SKILL.md` still holds
no notice of its own. Tracked on **#4398**, not absorbed here; the first
upstream-side move is the notice in the MIT
repo, because a per-file notice added *here* would diverge this tree from the
tree it mirrors.

**How the mechanism is checked (and its limits).** Equality with the MIT repo
is asserted on three declarations only — `MIT License`,
`Copyright (c) 2026 Premise Labs`, `Permission is hereby granted, free of
charge` — and the check **never fetches upstream**, so drift *relative to the
MIT repo* is silent: a rewritten body passes as long as those three markers
survive. The BSL scan has two marker classes, because a licence *declaration*
and a prose *mention* look different and only one of them is a regression:

- a **`BUSL` token** (with or without `-1.1`, case-insensitive) or the
  versioned short form **`BSL 1.1`** — including the `BSL-1.1` / `BSL v1.1`
  spellings vendored notices and `mariadb.com/bsl11` use — counts **anywhere**,
  prose included: an artifact that names BSL anywhere is claiming it. The
  *unversioned* acronym `BSL` is deliberately not a token: the served
  `how-to-use-tortoise` skill writes `BSL` in prose, and the version is what
  separates a claim from a mention.
- the **canonical name** (`business source license [1.1]`) counts in a file's
  first 20 lines (frontmatter + header comment, where a licence header lives)
  **or anywhere** in a licence/notice file. A file is a licence/notice file
  when its NAME carries a licence/notice token followed by a non-alphanumeric
  or the end: `LICENSE`, `LICENSE.md`, `LICENSE-BSL`, `LICENSE 2.txt`,
  `LICENSE (copy).txt`, `license copy.txt`, `third_party_licenses.txt`,
  `THIRD-PARTY-NOTICES.txt`, `MIT.license`, `COPYING.LESSER`, `COPYRIGHT`,
  `NOTICES.txt`, or any file under `LICENSES/`/`LICENCES/`/`NOTICES/`. A name
  whose token continues as a word (`licensee-notes.md`, `licensing-notes.md`)
  is prose about licensing, not a licence, and is scanned in the window only.
  The whole-file rule exists because the engine's own BSL text carries the name
  at line 2 *and* line 28 and never carries the `BUSL` token, so a composite
  file that keeps the MIT markers and appends the BSL terms would otherwise
  pass — and a stray `Business *Source* License` or British `Licence` spelling
  in prose is not a declared marker of ours.

Symlinked directories **are** followed (a skill dir symlinked into the surface
is still served by the Pages copy, so it is still asserted). Reads — and the
summary stream — are UTF-8-pinned, so a C/POSIX locale can neither silently skip
every non-ASCII file (all four served skills are heavily non-ASCII) nor turn a
diagnosis into a traceback. **Reach limits, stated rather than implicit:** a canonical-name declaration
buried past the first 20 lines of a file that is *not* named as a licence/notice
file is out of reach (including a name with no separator at all, `LICENSEBSL`);
and a file that is not valid UTF-8 carries no assertion (the licence file itself
is reported as an explicit error in that case, not a traceback). One deliberate
exception to the window: the SERVED SCRIPT (#4398) is scanned **whole-body**
even though its name is not a licence token — the whole content IS the notice the
consumer receives, so a canonical-name claim cannot hide past the window there
(the `whole_body` argument of `bsl_declaration`).

**OVERRIDES:** the repo-default inheritance of BSL 1.1 over every file with
no licence header of its own — the served installer is MIT with its notice
**in-band**, so the boundary stays at the network for the script a
`curl | bash` customer runs, exactly as for the served skills tree. (The same
line is posted on **#4398** as its issue-side marker, per AGENTS.md.)

**Backstop (enforced in CI):** `validation/check-license-surface.py` asserts
(1) the per-directory licence exists and declares MIT, and (2) **no file under
the surface declares BSL** — so a new file dropped into `public/skills/` cannot
silently re-import the engine licence. The `license-surface` job is a REQUIRED
branch-protection check with no path gate. Two further surfaces joined it:
`SERVED_SCRIPTS` (the installer's in-band MIT notice, and no BSL there) and
`LICENCE_DISCLOSURE_SURFACES` (the `/license` page must name every permissive
surface). Both are mutation-tested in `tests/test_license_surface_consumer.py`.

**Closed since the mechanism above was recorded:**

- **`public/install-tortoise-skills.sh` — MIT, in-band (#4398).** The installer
  no longer inherits the engine licence: it carries `SPDX-License-Identifier:
  MIT`, the copyright, and the **full permission notice** in its own header.
  This is the mechanism a *served script* needs, and it is deliberately NOT a
  served sidecar: a `curl … | bash` user receives the script's bytes and nothing
  else, so a licence file served next to it never travels with a piped
  download. It is the single-file analogue of #526's mechanism, where
  `client/LICENSE` ships **inside** the wheel — there the notice is packaged
  with the artifact it covers; a bare script has no package to carry a sidecar.
  Proven non-vacuous by the `SERVED_SCRIPTS` assertion: deleting the notice, or
  reducing it to a bare SPDX id (metadata, not the notice MIT requires), REDs;
  so does introducing a BSL declaration into the script.
- **The onboarding instructions are settled MIT, not a forward declaration.**
  The owner's #4366 adoption decision (2026-09-21) rules that the served
  `public/skills/tortoise-onboarding/SKILL.md` is a **consumer artifact** — it
  is what the customer's agent reads — so it is covered by the MIT directory
  licence like the other three skills. Its source of truth
  (`tortoise/onboarding/SKILL.md`) stays product-BSL: the decision scopes the
  grant to the artifact the consumer receives, not to the product tree. No
  per-file notice is added, because a notice in the served file would have to
  land in the product tree too (they are byte-identical-by-test) and would
  diverge this tree from the MIT repo it mirrors.
- **`website/license.html` — the page now discloses every surface (#4399).** It
  no longer states BSL-only: it scopes the engine's BSL, and names the thin
  client (Apache-2.0, #526) and the served skills + installer (MIT,
  #4366/#4398), with a revision row recording the change. The
  `LICENCE_DISCLOSURE_SURFACES` assertion keeps it from re-drifting back to a
  BSL-only page.

**Still open (disclosed, not absorbed): the notice does not travel inside an
installed copy.** The `SKILL.md` files the installer writes still carry no
notice *of their own* on disk: they are MIT by the directory licence, and the
installer that delivers them carries the notice, but MIT's "included in all
copies" condition is met for the served tree and for the installer, not for an
installed `SKILL.md` in isolation. Closing that needs either a `LICENSE` fetch
inside the installer (a write-logic change, owned by #4327's lane) or a
served-output notice injection (a deploy change) — both outside this lane's
authorised surface. **#4398 stays open for that residual**; the durable
post-deploy probe for `/skills/LICENSE` is the same residual's second half.

**Not a residual — assumption 11** ("what should an installer do when it finds
a same-named artifact it did not create?") is answered elsewhere. The owner
moved it into #4366 as a research task; the `fix/4327-installer-overwrite` lane
researched and implemented it — back up once, then merge, preserving foreign
frontmatter keys and foreign banner/trailer seam blocks
(`tests/test_installer_preserves_foreign_skill_content.py` records the owner
ruling). This licence change does not re-open it.

**Found while closing #4399, filed rather than absorbed — `website/tos.html`
§15.3 ("License Boundary") still states the whole-project claim** ("Tortoise is
a source-available project. The self-hosted version … is licensed under the
Business Source License 1.1") with no mention of the Apache-2.0 client or the
MIT skills/installer. It is a *contractually binding* page, so it is the
highest-consequence version of the defect #4399 fixed on `/license`, and no
issue covered it. Filed as **#4442**; any edit there must keep the `legal-e2e`
constraints (`business source license` present; exactly one `$N` figure).
