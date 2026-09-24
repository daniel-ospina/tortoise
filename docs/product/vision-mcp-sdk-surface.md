---
title: Vision — the MCP and SDK surface
status: approved-scope
products: [tortoise]
aboutObjects: [tortoise-mcp, tortoise-sdk]
---

# Vision — the MCP and SDK surface

## The vision, in one paragraph

**A developer should be able to read our entire surface in a sitting and know what to call.**
Today they cannot: the MCP server exposes **98 tools** and the SDK **150 methods**, with five
overlapping ways to search, four ways to revise, seven ways to create a node, and a
lifecycle nobody owns. The target is a surface small enough to hold in your head, where every
name says what it does, every operation says what it will do to your data, and

> **anything that changes what happens to the caller's data is legible at the call site.**

That is the single principle the whole list is derived from — not a size target. Size is the
measurement, not the goal.

**Two audiences, two halves:**

| | Who | What it is |
|---|---|---|
| **The memory API** | An agent, or code acting for one | Read and write **one memory graph**. This is the MCP surface, mirrored in the SDK. |
| **The tenancy API** | A **builder** — code running an app where every end-customer has private memory | Provision a memory graph per end-customer, scope a key to it, destroy it on churn. **Not on the MCP.** |

**Canonical terms:** an **organisation account** (the billing and plan boundary) owns many
**memory graphs** (the unit of memory). An end-customer is a memory graph inside the builder's
account — the builder pays; an end-customer never has an organisation account of their own.

## The two canonical documents

| Document | What it is |
|---|---|
| **`docs/product/canonical-mcp-tools.md`** | **The approved MCP list — 23 tools**, 9 READ / 14 WRITE. Merged as `8375c7921`. This is the **naming authority** for the SDK. |
| **`docs/product/beta-sdk-surface.md`** | **The recommended SDK surface — 40 methods** over 150, from the builder's and the agent's actual work. Includes the discarded-name ledger and the rationale for each. |
| `docs/product/canonical-sdk-methods.md` | The full inventory of all 150 with descriptions, grouped into 32 groups. **It is an inventory and an earlier target sketch, not the target.** Where it disagrees with `beta-sdk-surface.md`, `beta-sdk-surface.md` governs. |

**These documents are the vision.** This file is the bridge from them to implementation.

**Which target governs, stated once so no lane has to guess:** `canonical-mcp-tools.md` is the
authority for **MCP names** (23, owner-approved and merged), and **`beta-sdk-surface.md` is the
authority for the SDK target (40)**. `canonical-sdk-methods.md` is the *inventory of what exists
today* plus an earlier 32-group target sketch; its names are superseded wherever the two disagree
— e.g. it says `write_knowledge` and `stabilize_beliefs` where the current target says
`write_knowledge_batch` and `refresh_confidence`. Read it for coverage, not for names.

## Scope of the freeze

The freeze is on **tools and endpoints** — nothing is added, removed, renamed or deprecated on
either surface without explicit human approval. A **response field** that is off by default and
leaves the response unchanged is **not** a gate failure, but must be recorded.

## The reconciliation — MCP 98 → 26

**GENERATED — see `docs/product/bridge-table.md`**, produced by
`uv run python tools/bridge_table.py`.

An earlier version of this section carried a hand-built bucket map. It has been **deleted**, not
corrected: it drifted from the registry in three separate ways (it named `run_onboarding` as a
destination after the same document dropped it; it listed a target that is not in the 25; and it
could not be checked). The generator replaces it, and unlike the map it **fails the build** when
the map and the registry disagree — a mismatch is a finding, not something to reconcile by hand.

**What it establishes, from the registry at build time:**

| | |
|---|---|
| Registry tools | **98** |
| Absorbed into the 26 MCP targets | **81** |
| Absorbed into a builder-only SDK method (not on the MCP) | **1** |
| Tenancy (SDK/REST only) | **2** |
| Retired | **14** |
| MCP target tools | **26** |

**The four bucket rows sum to 98.** (The last row is the target *surface*, not a bucket —
26 targets received those 81 absorbed tools many-to-one, so it is not part of the sum.)

† **`run_onboarding` is in the approved 23-tool list — it is tool #23, absorbing the seven
`onboarding_*` tools — and the beta 26 drops it in favour of `check_connection`.** That is not a
contradiction with the bridge table, which describes the **26-tool target**: `run_onboarding` was
a proposed merge of seven registry tools, the 26 drops it, so those seven have no destination and
are correctly `REMOVED`. **They are one of the genuine retirements** — `check_connection` replaces
the proposed entry point and absorbs none of them. Two of the seven were **tenant-visible reads**
(`onboarding_state`, `onboarding_github_status` — both `_ro()`, `http_policy=True`); they are
**rehomed to the tenancy block (SDK/REST)**, so the capability survives while the MCP surface
loses both reads.

**`98 − 26 = 72 retired` is wrong** and circulated in earlier drafts: 14 retire, 2 are tenancy-only,
1 is absorbed into a builder-only SDK method that is not on the MCP, and 81 are
absorbed into the 26 targets. Many current tools map onto one target, so the two numbers are
not complements.

## The reconciliation — SDK 150 → 40

The ledger of the departing names — grouped, with the rationale for each group — is the
**"Discarded — and why"** section of `docs/product/beta-sdk-surface.md`.

**Exactly four target names are reused verbatim from the current SDK:** `create_entity`,
`get_entity`, `approve_merge` and `close`. Everything else on the 40 is a new name, a renamed
name, or a merge — which is why the per-name old→new mapping matters and is a **Phase 0.3b
deliverable that does not exist yet**. (`list_sources` is *not* one of the four: it is a current
name that folds into the target `list_knowledge(kind='source')`, so the question it asks survives
but the name does not.) The ledger is written per group, not per name — a group-level ledger is the
honest form, and the per-name completeness check is deferred to Phase 0.3b.

**40 rows**, of which the table's rows 1–2 are the `Tortoise(...)` constructor and
`close()`.

**The SDK count is 40 and the MCP count is 26.** Start from the approved canonical MCP list of
**23**. The owner's 2026-09-21 ruling added `graph_set_recording` — an unplaced tool — bringing the
approved surface to **24**. The beta target then adds four, drops three and splits one:

| | |
|---|---|
| **+4** | `get_historical_knowledge` · `mine_knowledge_from_directory` · `write_question` · `check_connection` |
| **−3** | `inspect_batch` → `list_knowledge(kind='batch')` · `manage_deployment` (tenancy is not on the MCP) · `run_onboarding` → `check_connection` † |
| **±1** | `revise_knowledge` splits into `update_knowledge` + `supersede_knowledge` |
| **renames** | `recall_beliefs`→`check_confidence` · `stabilize_beliefs`→`refresh_confidence` · `capture_knowledge`→`mine_knowledge_from_session` · `index_files`→`index_sources_from_directory` · `manage_source_trust` unchanged |

`23 + 1 (owner ruling) + 4 − 3 + 1 = 26`. ✓

**The one ruling this rests on** (owner, 2026-09-21 — `graph_set_recording` was an unplaced tool, so it is
not one of the 23's rejects but a separate decision): **KEPT.** It is an agent's only in-MCP recovery
from the capture 409, because REST is unreachable from an MCP client. It is the target's one deliberate
exception to "tenancy is not on the MCP" — its SDK method is the builder-only `update_memory_graph`,
and it needs a `team:manage`-scoped key, so it is **not** a universal self-heal path.

## ⛔ Cross-artifact tensions — an owner-open item is closed by the owner, not by a draft

`docs/product/canonical-mcp-tools.md` is **owner-approved and merged**, and it carried five items
left **open**. **All five are now settled by an owner ruling:** `graph_set_recording` on
2026-09-21, and the remaining four on **2026-09-22** (both recorded on **#4282**). The beta target
had already made a call on the four; the ruling is what made it the surface, and the approved doc
is updated to match. **A draft making a call does not resolve an owner-open item — an owner ruling
does.**

The rule the four applied: **containers may be on the MCP; account tenancy is not.** A container
is fine on the agent surface when it is operator-only and never handed to a tenant
(`index_files`). What is not fine is an operator-only container whose members live on the
customer-grantable surface — merging those either loses a tenant-visible capability or smuggles
an exemption into the read/write guarantee.

| Owner-open item (in the approved doc) | What the beta target does | Status |
|---|---|---|
| `graph_set_recording` — *keep it as a 24th tool, or accept the loss?* Dropping it leaves an agent that hits a 409 with **no recovery path inside MCP** (REST is unreachable from an MCP client). | **RESOLVED by owner ruling 2026-09-21: KEPT.** Now a 26th MCP tool; the SDK's own per-field `graph_set_recording` stays discarded and the override folds into `update_memory_graph`, consistent with the ruling that deleted `set_memory_graph_name`/`set_memory_graph_backend`. | **✅ resolved — the count is 26** |
| `manage_deployment` — *placement OPEN* (`org_create`, `packs_list`, `pack_install`). | **RESOLVED 2026-09-22: OFF the MCP.** `org_create` is account tenancy (SDK/REST/console); the two pack members are SDK/REST. Putting account tenancy on the MCP would contradict the recorded tenancy ruling, so no MCP tool carries it. | **✅ resolved — not on the MCP** |
| `packs_list` — *does a tenant need to list its own packs?* Folding it into an operator-only tool **removes a tenant-visible read**. | **RESOLVED 2026-09-22: SDK/REST only, POST-BETA.** The tenant read is preserved on the SDK/REST pack surface; no competitor exposes pack installation on its agent API, so the MCP does not carry it. Per-graph curation is tracked by **#4663**. | **✅ resolved — SDK/REST post-beta (#4663)** |
| `pack_install` — same question for a **write** (`hosted_only`, `http_policy=True`). | **RESOLVED 2026-09-22: SDK/REST only, POST-BETA** — the tenant-visible write is preserved there, never lost to an operator-only tool. Per-graph curation is tracked by **#4663**. | **✅ resolved — SDK/REST post-beta (#4663)** |
| `run_onboarding` — it absorbs two read-only tenant-served tools, so it is both read and write on a customer-grantable surface, which the read/write principle forbids. | **RESOLVED 2026-09-22: dropped for `check_connection`.** Its two tenant-visible reads (`onboarding_state`, `onboarding_github_status`) are **rehomed to the tenancy block (SDK/REST)**; the MCP surface therefore loses both reads **by design**, not by accident. | **✅ resolved — dropped for `check_connection`; two reads rehomed** |

**None of this blocks the docs.** It blocks Phase 1.2 (cutting the surface manifest) and Phase 3.2
(removing anything). With all five settled, those phases are unblocked on this axis.

## Implementation plan

Phases are ordered so nothing is written twice. **The SDK is frozen while the MCP list is
built**, because the MCP handlers call the SDK — building them in the other order writes each
handler twice.

### Phase 0 — the pre-flight (blocks everything)

| | Deliverable | Why |
|---|---|---|
| **0.1** | **The bridge table.** Every MCP `type=` / `mode=` / `section=` discriminator → the exact SDK method and argument it resolves to. | The merged tools dispatch internally. Until that mapping is written down, nobody knows whether the 26 names can be implemented on the frozen SDK — or whether a `type=` value has no SDK method to call. **This is the check that prevents a rewrite.** |
| **0.2** | **Name the target.** A short note in both canonical docs stating that **every name is a target, not a description of today.** | The review found the MCP column reads as present tense. A developer following it today calls tools that do not exist. |
| **0.3** | **The MCP rename table.** old name → new name, per tool. | **Only 4 of the 26 is live verbatim** — every registered MCP tool carries a `tortoise_` prefix, and exactly 4 (create_entity, get_entity, approve_merge, graph_set_recording) have a prefixed equivalent. Without this, implementation silently renames the whole MCP surface with no migration note. |
| **0.3b** | **The SDK rename table.** old name → new name, per method — the SDK pair to 0.3. This exists because 0.3 is the *MCP* table; the SDK half would otherwise have had no owner. `beta-sdk-surface.md` and the SDK reconciliation above both delegate their per-name check here. | 0.1 |
| **0.4** | **`__all__` in `tortoise/__init__.py`.** | The root cause of 150. The public surface is declared by *convention*; every design test presupposes a declaration. Without this the surface drifts back. |

### Phase 1 — the declaration and the gate

| | Deliverable | Depends on |
|---|---|---|
| **1.1** | `tortoise/__all__` listing the 40 approved methods | 0.4 |
| **1.2** | The approved-surface manifest, cut **once** from that declaration, frozen | 1.1 |
| **1.3** | The gate: unfiltered `pull_request` check, **fail-closed** on any add/remove/rename | 1.2 |
| **1.4** | **#3883** — a retired **MCP tool** name must **WARN** when called, naming the replacement. This is the MCP half only; the SDK retires to a failing name (2.5). | — |

**1.4 gates every MCP removal.** Nothing is removed from the MCP surface until a caller of a
retired tool name gets a warning that names its replacement. The SDK does **not** inherit this
mechanism — see 2.5.

### Phase 2 — the SDK

| | Deliverable | Depends on |
|---|---|---|
| **2.1** | The 40 methods under their canonical names | 1.1, 1.4 |
| **2.2** | The 4 merges (`create_entity`, `link_entities`, `delete_knowledge`, `update_knowledge`) dispatching internally | 2.1 |
| **2.3** | `update_memory_graph` — **the rename path that is currently missing** | 2.1 |
| **2.4** | The Contracts section enforced: pagination cursors, truncation notice, typed errors | 2.1 |
| **2.5** | The retired **SDK** names → a **failing name that names its replacement**. **No alias layer, no warning shim, no telemetry** (#3836 (c) ruling): `tortoise-graph` is public on PyPI with no users, so there is no caller to protect and no call telemetry to collect. The earlier “146 → warning aliases” plan is WITHDRAWN. | 2.1 |
| **2.6** | `check_connection` (the `check_key` + `verify_connection` collapse) | — |

### Phase 3 — the MCP server

| | Deliverable | Depends on |
|---|---|---|
| **3.1** | The 26 tools implemented **on the frozen SDK** | Phase 2 |
| **3.2** | The **14 retired** tools removed. **2** are tenancy-only and **1** is absorbed into a builder-only SDK method that is not on the MCP, so **81 are absorbed** into the 26 targets — many-to-one. Writing "98 − 26 = 72 retired" conflates the two and is wrong. | 3.1 |
| **3.3** | The `co_firstlineno` guard defect fixed — see *Coordination* | — |

### Phase 4 — verification

| | Deliverable |
|---|---|
| **4.1** | Every MCP tool exercised end-to-end against a live graph |
| **4.2** | The isolation proof: `create_key` → `check_connection` → a graph it cannot reach |
| **4.3** | The retired-name warning proven by execution, not grep |

## Resolved — applied

Both items that were `Unsure` are now **settled and applied**: the revise rule is stated in the
rows (U1), and `check_key` + `verify_connection` are collapsed into **`check_connection(key_id=None)`**
(U2). Each was a non-decision under our own rule — documentation of existing semantics, and a naming
fix inside an unbuilt surface. Neither is live; both are reversible.

**U1 — the revise triangle.** Applied. The three rows now carry the decision rule: **retract** when
nothing replaced the claim, **supersede** when a specific successor did, **delete** when it must not
be retained. Decidable because the first two differ by exactly one thing — whether a successor exists.

**U2 — `check_key` vs `verify_connection`.** Applied. Both answered "what does this credential
reach"; they are now one **`check_connection(key_id=None)`** — omitting the id checks your own
connection, passing one inspects a specific credential. `key_id` selects *the thing asked about*,
not the operation, so this was one question with one answer all along.

**Neither needed the owner.** U1 documents semantics that already exist; U2 fixes a name on a surface
nobody has built. Both are off by default and reversible, which under our own rule makes them next
steps rather than gates. Applying them cost less than asking.

## Coordination — four lanes are blocked on this

| Lane | Blocked on | Unblocks with |
|---|---|---|
| **B2c** (`01a07161`) | *"I'm out of executable work."* The whole lane waits on the approved list. #3898 is **red by design**, its `xfail` bound to this work. | The approved list (now available) + Phase 1 |
| **B3** (`01a093d5`) | *"The list can only be re-approved all at once"* — it cannot re-cut the surface manifest per-PR without rubber-stamping. | Phase 1.2 — the manifest cut once |
| **B4** (`01a0a7f1`) | Told by the owner to build *under* the redesign, not alongside it. PR #4020 superseded twice. | Phase 0.1, so the route work binds to the new surface |
| **B5** (`01a0b558`) | Found a **defect in `tools/surface-guard.py`** — `_fingerprint` uses `co_firstlineno`, which is *positional, not identity*: 3 comment lines near line 713 shift every baseline row while the digest is byte-identical. | Phase 3.3 |

**B5's finding is a defect in a merged artifact from this lane and is treated as blocking.**

## Not in beta

- **The eval harness.** No vendor exposes its proprietary scenario suite, and the category has
  **no independently reproduced results** — vendor numbers diverge by 8–45 points. Revisit
  post-beta; it is cheap (every harness in the field makes the customer supply their model key).
- **The journal capability** (`checkpoint`, `diary_write`, `diary_read`). Live on the MCP today,
  from the initial commit, with a `wing`/`room` vocabulary that appears nowhere in
  `docs/ONTOLOGY.md`. Filed separately; unlisted until then.
