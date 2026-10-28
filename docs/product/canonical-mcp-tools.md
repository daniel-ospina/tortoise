---
title: "Canonical MCP tool list — the owner-approved target surface"
type: synthesis
domain: capability
doc_status: live
created: 2026-09-18
ownedBy: epistemic-team
aboutSubjects: tortoise-memory
aboutObjects: mcp-tool-surface
approval_status: approved
approved: 2026-09-18
approvedBy: daniel-ospina
approval_pr: 4120
issue: 3863
related:
  - "#3994"
  - "#3883"
  - "#4113"
  - "#4114"
---

# Canonical MCP tool list — 23 proposed tools (the beta target is 27 as of the 2026-10-17 #3540 amendment; it was 26)

**Owner-approved 2026-09-18.** This is the target MCP surface. Everything not on this
list is retired, folded into a member below, or archived. The surface is frozen in both
directions until this list is executed — see `docs/product/mcp-sdk-surface.md` (the
generated view of the current baseline) and the gate in `tools/surface-guard.py`.

**Amended 2026-09-22** (owner ruling, recorded on #4282): the four placement items this
document left **OPEN** are settled — `manage_deployment` is **off the MCP**,
`run_onboarding` is **dropped for `check_connection`**, and `packs_list`/`pack_install` are
**SDK/REST, post-beta** (#4663). The **23** is this list's own proposal count; the **MCP
target was 26 at that ruling** (27 since #3540, 2026-10-17). `approved: 2026-09-18` above stays the original ruling date; this paragraph
records the amendment.

**Amended 2026-10-12** (owner ruling, recorded on **#7852**): `create_operator`
(the SDK method) and `tortoise_create_operator` (the MCP tool) gain a
**`semantics`** input — the Layer-2 meaning vocabulary §8 already records. The
parameter is **additive and defaulted** (`semantics=None`), so every existing
caller keeps its behaviour; when it is supplied the direction the edge records
follows the declared meaning (a one-way meaning recorded `unidirectional`, a
mutual one `bidirectional` — the runtime's interim pair at W1; since **v3.25**
the declared meaning's own model value is recorded verbatim, see the correction
below), and the basis the resolution used is recorded on
the operator as **`direction_basis`** (`explicit` / `semantics` / `default`) — the token
names the layer that produced the value (`semantics` = the declared Layer-2 meaning
resolved it), never the Layer-3 `label` predicate, which is a different input. An
out-of-vocabulary word **warns and the write proceeds** — it never refuses
(warn-not-block). This is **W1** of #7852's three-layer relation model, which
the owner approved verbatim: *"w1 yes approved"* (2026-10-09), relayed into
this document by **PR #8283**.
`approved: 2026-09-18` / `approval_pr: 4120` above stay the original ruling's;
this paragraph records the amendment.

> **Correction, 2026-10-12 (v3.25 — same issue #7852).** The W1 sentence above
> describes W1's interim behaviour, when the runtime could store only the pair
> `unidirectional` / `bidirectional`. The **four-value direction storage**
> vocabulary that change named has since landed: the runtime records the
> declared meaning's own model direction verbatim — `->` (one-way), `<-`
> (reverse one-way), `<->` (mutual), `-` (none: the connection exists and no
> confidence transfers) — and interprets it on every path (`create_operator`,
> `create_direct_edge`, the ingest contract, EP and the analyze BFS).
> `bidirectional` / `unidirectional` remain **accepted and readable aliases**
> (`bidirectional` ≡ `<->`, `unidirectional` ≡ `->`); storage is verbatim, so no
> data migration was required. The `w1 yes approved` ruling above is unchanged.
>
> This document is the **approval record**, not the generated baseline. The generated
> list of what exists *today* is `docs/product/mcp-sdk-surface.md`, rendered from
> `config/surface-manifest.yml`. This file records what the surface is *approved to become*.

**Amended 2026-10-14** (owner-directed on the **#2104** write-path lane, **PR #8563**): the
frozen baseline is re-cut for `tortoise_session_capture` because its docstring was corrected
— the stale gate-order list that named a removed pre-#3892 provider 503 as a pre-write gate
now records the actual order plus the separate inner extractor-provider 503. **No tool is
added, removed or renamed; the served schema, parameters and bindings are unchanged** — only
the description text, and hence the handler digest, changes. status: approved · principal:
daniel-ospina · PR #8563 · date: 2026-10-14.

**Amended 2026-10-17** (owner ruling on **#3540**, **PR #8863**): **one tool is added and two
are retired — a net REDUCTION of one**, which is the ruling's own requirement. The TARGET count
moves the other way: the **MCP target count is 27** (it was 26), because the two retired
switches were never ON this target list: the
merged list gains `tortoise_manage_sources` while the SERVED surface loses one net (**82 → 81**).
Both pairs are stated because they move in opposite directions, and a generated table that prints
"27" must not be read as contradicting this record. Owner's words:
*"b sounds good because we enable agents to configure the system (assuming they have
permission) and that's better UX than clicking on the UI"* (2026-10-12).

`tortoise_manage_sources(action=list|enable|disable,
source=github_issues|github_documents|agent_sessions)` is ONE consolidated source-management
operation. It replaces the two per-source switches `tortoise_onboarding_github_index` and
`tortoise_onboarding_session_recording`, which are **retired through `RETIRED_USE_INSTEAD`**
(#3883), not deleted — a caller still resolves them and is handed the replacement. It also
closes the tracked `github_documents` gap, which had no tool at all.

`tortoise_onboarding_github_connect` is deliberately **not** folded in: it is the OAuth
handshake, and the ruling's own *"credentials never pass through the model"* condition is
what keeps it out of an agent-callable list/enable/disable operation.

Permission is the condition the owner attached to the ruling and is enforced at the tool
boundary: the entry declares `risk="admin"` (#4122 — `writes` is derived from it), so
`_enforce_mcp_tool_scope` requires `graphs:write` and denies an unresolvable name. Agent
session recording keeps its recorded `#1927` default (ON — the optional off-switch, ToS-covered);
the ruling's "deny-by-default"
is taken to govern what the assistant may DO, not to reverse what the product ALREADY does.

> **OVERRIDES:** the "reads and writes never share a tool" principle stated immediately below,
> and `beta-sdk-surface.md`'s "account tenancy is not on the MCP" — the owner ruled for **one**
> consolidated operation that both lists and switches sources, over per-source tools
> (2026-10-12, on #3540). The permission condition is what makes the departure safe rather than
> the split: `risk="admin"` (and the `writes` permission derived from it, #4122) means a
> read-only key is refused even for `action="list"`, so the
> read inside a write-gated tool can never be reached by a read-scoped caller. A future lane
> that "fixes" this by splitting `list` into its own read tool would restore the principle and
> **undo the ruling's single-tool arithmetic** — reopen #3540 instead of editing this tool.

status: approved · principal: daniel-ospina · PR #8863 · date: 2026-10-17.

**Amended 2026-10-22** (owner-filed and owner-scoped on **#9575**, **PR #9582**): the
`tortoise_packs_list` description no longer claims *"Read-only;"*. The handler reaches
`pack_state.get_tenant_packs`, whose `ensure_tenant_packs` MERGEs a missing starter
`:PackInstall` activation, so a `graphs:read` caller can cause a write — which is why **#4122**
re-tiered that entry to **`maintenance`** and stopped it declaring `readOnlyHint: true` (PR #9573
landed the tier; PR #9546 then rewrote the axis so every hint is *derived* from `risk`), and why it
deliberately left the description — the other string an agent
reads before deciding to call — asserting the opposite (#9575). The new text states what the call
does instead of asserting a read-only guarantee: a
graph carrying no `PackInstall` record at all gets its starter set re-seeded before the answer,
skipped when the graph's pack set was deliberately configured (#8648). The rule is enforced by a
test, not by prose: `tests/test_tool_registry.py` holds **every** entry whose `risk` is not `read`
to the description it publishes, so a future non-read tool cannot claim *"Read-only;"* either.
**No tool is added,
removed or renamed; the served schema, parameters and bindings are unchanged** — only the
description text, and hence that row's `job` and the baseline's `cut_at_commit`, change.
status: approved · principal: daniel-ospina · PR #9582 · date: 2026-10-22.

**Amended 2026-10-25** (owner ruling, recorded on **#9660**; the owner's words in the thread were
*"confirmation step"*, and on fragmentation: *"every agent that ever connects the mcp has to read the list of
tools on every single session = multiplying cache costs"*): **permanent deletion gains a preview-then-confirm
protocol, and it stays ONE tool.** No tool is added, removed, or renamed; `tortoise_delete` (registry id
`surface.delete`) keeps `sdk_method="delete"` and gains one optional parameter:

- **The FIRST call changes nothing.** With `dry_run=True` — or with no `confirmation` at all — the call
  resolves the target, reports every edge that would go with it, and returns a short-lived `confirmation`
  token (`tortoise/deletion.py`, ten-minute default) plus the plain-language summary. `deleted` is `False`.
- **The second call carries the token** (`confirmation=<token>`) and performs the delete, then calls
  `TortoiseSDK.delete`, so every kind keeps its own path: a Point, a relation (operators are Points), a
  Source, an Object, a Subject, an Event. **Whole graphs are not covered** — the dashboard deletes those.
- **A token is bound to one id and expires.** An invalid, foreign, or expired token RAISES and destroys
  nothing; it never degrades into a bare delete, which is what makes the protocol more than advice. Binding
  it to the id is what stops consent from drifting onto a different object between the preview and the write.
- **Why a token, not a `confirm=True` flag** (the owner's reason for the ruling): an assistant can set a flag
  in the same breath as the call, so a flag leaves nothing between a misread instruction and a destroyed
  claim.
- **Why not a family of per-kind delete tools** (the owner's reason, quoted above): the tool list is re-read
  by every connecting agent every session, so each extra tool is a cost every customer pays forever. This
  document's `delete_knowledge` row (18) is therefore **executed by keeping the single entry and widening
  what it deletes** — the absorbed names stay retired-forward entries, not new siblings.
- **Permission compatibility, inert today:** `tortoise.deletion.CAPABILITY = "knowledge:delete"` names the
  capability a later permission system can key on, and the entry keeps the single `risk="destructive"`
  declaration from which every client hint is derived. Nothing enforces the capability yet, by design — it
  exists so "deletion requires an administrator" has a stable key to attach to later.
- **The old description had drifted:** it promised a `dry_run` preview that the tool did have, but described
  the tool as deleting by default with no confirmation at all — so the promise was in prose only. The
  behaviour now matches the warning, and `tests/test_deletion_confirmation.py` pins the protocol while
  `tests/test_dry_run_preview.py` re-pins the one tool whose DEFAULT path is now a preview.
status: approved · principal: daniel-ospina · issue #9660 · date: 2026-10-25.

**Amended 2026-10-27** (owner ruling in answer to the two questions posted on **#4282**, **PR #10187**):
**what may be deleted, and that a retiring tool does not need a no-replacement warning.** Both
answers are **relayed verbatim by the lane that asked them, not posted by the owner** — the relay is
comment `6451…` on #4282 (search the issue for *"cna re-use"*). Neither answer is a word in the
owner's own post; treat the relay comment as the source of record for the quotation.

**(a) The surplus SDK methods that nothing calls may be DELETED — not merely made private — with the
code archived.** Owner: *"1 yes (archive the code so we cna re-use it later if needed)"*. Measured on
`origin/main` @ `17f604b054`: of the 138 public `TortoiseSDK` methods off the approved 40, **111 are
referenced somewhere under `tortoise/**`**, **27 have no caller anywhere** under `tortoise/**`, and
of those **1 is `RELOCATED`, not deleted** — leaving **26 deletable**. The 111 become **private**
(`_name`) with their call sites re-pointed — the `graph_count` → `_graph_count` precedent of
2026-10-25 — and keep their capability. The **26** are deleted, and their code is archived at tag
**`archive/4282-surplus-sdk-methods`** (points at `17f604b054`; **pushed**, verified with
`git ls-remote --tags origin`) with a per-method index, the recorded disposition of each, the work
order for the deleting PR, and a restore recipe in
[`archived-sdk-methods-2026-10.md`](archived-sdk-methods-2026-10.md). Deleting them is *not* a
licence to re-add them: a restored method returning to the public surface is a new surface change
and needs this document amended again.

⛔ **`test_guard` is the one the scan finds dead and a ruling forbids deleting.**
`beta-sdk-surface.md:390` (owner-approved) says it is *"Kept and relocated … it guards the
production-wipe incident, so the code must survive"*, and `tools/sdk_rename_table.py:709` records
`RELOCATED`. It leaves the public surface; it stays in the tree. A scan that finds no caller is
not licence to delete code a ruling says must survive — which is why the count is **26, not 27**.

**The 111 include six methods that look unreferenced to any scan that skips `sdk.py`** —
`delete_point`, `create_direct_edge`, `create_derivation`, `compute_reputation`,
`dream_health_state`, `resolve_id` — because the only caller is the class itself
(`self.<name>(...)`). They are load-bearing: deleting them breaks six live tools (`tortoise_delete`,
`tortoise_delete_point`, `tortoise_ingest`, `tortoise_assess_source`, `tortoise_dream_health`,
`tortoise_review_connections`) and the approved target method **`link_entities`**. The selection
rule therefore reads `sdk.py` as well; see the archive doc's correction table for the three wrong
counts that preceded **26**.

**Callers and artifacts outside the scanned population.** `tools/purge_test_residue.py:133` calls
`store.delete_point(...)` — that method is in the **111**, so the call site re-points to the private
name. And **all 26 are referenced from `tests/`**, while **17** are per-method keys in
`tools/sdk_rename_table.py` and all 26 are members of the canonical partition in
`docs/product/canonical-sdk-methods.md` — and that file's **rows are quoted verbatim by the rename
table's citation anchors**, so editing a row breaks them too. Leaving any of it stale reds
`tests/test_sdk_rename_table.py::test_check_mode_is_clean`. The deletion lane follows the archive
doc's work order; `tortoise/**` is not a safe list of unreferenced names.

**(b) A retiring tool does not need a no-replacement warning.** Owner: *"2 we dont have customers
other than 2 guys using it self-hosted so let's not worry about migration and rather focus on
delievrign the end result propelry and cleanly"*. This settles the question actually asked — whether
the **9** tools that leave the MCP with **no destination** need a warning naming a replacement. They
do not: a warning that names nothing serves a caller who does not exist. The **9** are
`tortoise_analyze`, `tortoise_backfill_v25`, `tortoise_onboarding_state`,
`tortoise_onboarding_seed`, `tortoise_onboarding_demo_create`,
`tortoise_onboarding_github_connect`, `tortoise_onboarding_github_status`, `tortoise_packs_list`,
`tortoise_pack_install` (all nine live in `TOOL_REGISTRY`, measured 2026-10-27).

**What this does NOT authorise — stated because the answer was broader than the question.** The
**18** existing `RETIRED_USE_INSTEAD` forward entries are a *different population*: every one of them
names a **live** replacement (`tortoise_get` → `tortoise_get_entity(id, type=...)`,
`tortoise_health` → `tortoise_overview(section="health")`, … — 18 of 18 verified). They are neither
"machinery serving a caller who does not exist" nor covered by the question that was asked, and they
carry two recorded decisions **in this document**: the 2026-10-17 amendment (retired *"through
`RETIRED_USE_INSTEAD` (#3883), not deleted — a caller still resolves them and is handed the
replacement"*) and the 2026-10-25 one (*"the absorbed names stay retired-forward entries"*).
**Removing them is a separate decision** and would need its own argument naming #3540 and #9660; it
is not done here. The end state of the epic is still **27 advertised tools**, but that is reached by
the full 81 → 27 consolidation, **not** by this amendment — these two answers remove 0 tools and 0
methods on their own.

> **OVERRIDES:** the plan item `1.4` (`#3883`) requirement that *nothing is removed from the MCP
> surface until a caller of a retired tool name gets a warning naming its replacement* — for the
> **9 tools that have no replacement to name**, the warning is dropped. The owner removed that
> premise on 2026-10-27 (2 self-hosted users, no customer callers), so a warning that can only say
> "gone" is optional machinery rather than a precondition. **Scope of this override:** the 9
> no-destination retirements only. It does **not** touch the 18 replacement-naming forward entries
> (#3540, #9660), and it does **not** touch the SDK half (`2.5`, the #3836 (c) ruling), which already
> ships a clean hard failure that names the replacement. The 18 keep answering exactly as recorded.

**Correction carried by this amendment — the count was wrong three times, and each error is a
different trap.** The number first published on #4282 was **26** (a non-recursive **regex** over
`tortoise/*.py` — it never read the subpackages, and a regex counts prose like
`` `sdk.capture_session` `` in a docstring as usage). It then read **34** (AST, but with **`sdk.py`
excluded** from the readers, which hid the seven methods the class calls on itself — the six
load-bearing ones listed above, plus `membership_get`). It then read **27** (AST, `sdk.py` included)
— and the owner-approved `RELOCATED` disposition of `test_guard` brings it to **26**. The archive doc
carries the table of how each reading was produced plus the single command that reproduces 27→26.
Any deletion PR must use **26 / 111**.

status: approved · principal: daniel-ospina · PR #10187 · date: 2026-10-27.

## Principle: reads and writes never share a tool

On the customer-grantable surface, a tool is either **read** or **write** — never both, so
read permission can be granted generously with no path to a destructive action.

> **OVERRIDES:** the read-or-write binary stated just above, and NIST SP 800-53 AC-6's
> read-low / write-high default — the owner ruled (2026-10-21, on #4122) that a tool is
> classified by **user-perceived risk**, not by whether its handler writes, so a confined,
> idempotent, **derived-value write is permissioned as read** (`maintenance`). The precedent
> is PostgreSQL **17**'s `MAINTAIN` privilege (`GRANT MAINTAIN ON <table>` plus the
> `pg_maintain` role, added in 17 — a SEPARATE grant from SELECT/INSERT/UPDATE/DELETE).
> PostgreSQL mints its own grant; we deliberately do not, because a new scope would be a
> surface change, so the read key stays and only the read-only CLAIM is dropped.
> `tortoise_get_confidence`, `tortoise_compute_confidence` and
> `tortoise_packs_list` are `maintenance`: a `graphs:read` key may call them, they never
> require `graphs:write`, and they do NOT declare `readOnlyHint: true` (that literal hint is
> what clients auto-approve on). The declaration is `risk` on the registry entry; `writes`,
> `readOnlyHint`, `destructiveHint` and `idempotentHint` are **derived** from it
> (`tortoise/tool_registry.py`) and pinned by `tests/test_tool_risk_tiers.py`, so a hint
> cannot be "fixed" in one place and left wrong in the other. A future lane that wants these
> to be writes must reopen #4122 — `writes=True` would also stop the expensive recompute
> being metered as a read, which is the cost lever OWASP API4 asks us to watch.

**Operator-only tools are exempt.** They are never handed to a tenant, so they may mix
reads and writes — `index_files` is the clean example: both of its members (`index_files`,
`mine_conversations`) are excluded from tenant HTTP.

**The general rule: containers may be on the MCP; account tenancy is not.** A container that
mixes reads and writes is fine when it is operator-only and never handed to a tenant
(`index_files`). What is *not* fine is a container whose members live on the customer-grantable
surface: a tenant-served read or write inside an operator-only tool either loses that capability
or smuggles an exemption into the read/write guarantee.

**`manage_deployment` was the worked case, and it is now OFF the MCP** (settled 2026-09-22).
`org_create` is account tenancy — putting it on the MCP would contradict the recorded tenancy
ruling — and its two tenant-visible members are rehomed rather than lost: the packs operations
move to the SDK/REST pack surface. See **Open items**.

## READ — 9 tools

Grantable freely. The one item that used to block this — `recall_beliefs` absorbing
`get_confidence`, which is read-labelled but writes — is **resolved by the #4122 ruling**
(the trio is `risk="maintenance"`: a `graphs:read` key may call it and it no longer claims
`readOnlyHint: true`). See **Open items**.

| # | Tool | Absorbs |
|---|---|---|
| 1 | `search_knowledge` | search, suggest_entry_points, search_sessions |
| 2 | `list_knowledge` | query |
| 3 | `recall_beliefs` | recall, belief_timeline, provenance, session_context, get_confidence, calibrate_summary |
| 4 | `get_entity` | get, get_session, get_entity |
| 5 | `explore_connections` | expand_relationships, entity_profile, traverse |
| 6 | `graph_overview` | overview, check_structure, validate_domain, audit, issue_insight, dream_health, summarize_structure |
| 7 | `review_link_candidates` | review_connections, find_cross_lens_candidates, list_dedup_candidates |
| 8 | `poll_events` | events_poll |
| 9 | `inspect_batch` | list_batch, list_batches |

## WRITE — 14 tools

| # | Tool | Absorbs |
|---|---|---|
| 10 | `create_entity` | create_point, create_entity, create_event, create_object, create_subject, create_document, diary_write |
| 11 | `register_source` | create_source |
| 12 | `index_files` | index_files, mine_conversations *(operator-only)* |
| 13 | `capture_knowledge` | session_capture, ingest, checkpoint |
| 14 | `manage_source_trust` | assess_source, get_source_reliability, set_source_tier |
| 15 | `link_entities` | create_edge, create_operator |
| 16 | `record_decision` | file_decision, file_human_approval |
| 17 | `revise_knowledge` | update, update_point, update_entity, supersede, invalidate, retract_point, promote_point, set_point_baseline — the beta target **splits this into `update_knowledge` + `supersede_knowledge`**; promote/baseline become **fields on `update_knowledge`** |
| 18 | `delete_knowledge` | delete, delete_point, delete_entity |
| 19 | `stabilize_beliefs` | dream, compute_confidence |
| 20 | `approve_merge` | approve_merge |
| 21 | `adjust_relationship` | operator_action, annotate_operator, mitigate_operator |
| 22 | `manage_deployment` | org_create, packs_list, pack_install — **OFF the MCP (settled 2026-09-22)**; account tenancy is not on the MCP. See Open items |
| 23 | `run_onboarding` | the seven `onboarding_*` — **OFF the MCP (settled 2026-09-22)**, dropped for `check_connection`. See Open items |

**Rows 22 and 23 are settled OFF the MCP (2026-09-22).** `manage_deployment` is account tenancy
(`org_create`) plus two tenant-served pack operations; `run_onboarding` mixed a write with two
tenant-served reads. Neither container ships on the MCP target. The rehomed members and the
dropped capability are recorded in **Open items**; the **MCP target count stayed at 26 for
that ruling** (27 since #3540, 2026-10-17 — see the amendment above) —
the beta list already carries `check_connection` in `run_onboarding`'s place and the tenancy
surface outside the MCP. Nothing below is a silent deletion: every member is either rehomed or
listed as retired.

## Removed entirely

| Name | Why |
|---|---|
| `analyze` | spends LLM tokens at read time. Write-time LLM cost is ours; read-time LLM is not offered |
| `diary_read` | dissolves into `list_knowledge(kind="diary")` |
| `backfill_v25` | proposed for archive — a one-shot migration to ONTOLOGY **v2.5**; the ontology is now **v3.14** (`docs/ONTOLOGY.md:2`, which itself supersedes v2.5). Zero customers, pre-beta: no legacy graphs exist to migrate. The baseline records it `lifecycle: active` with `recommendation: review`, so archiving is a proposal here, not a recorded status. Rebuild against the then-current schema if ever needed |

**Already gone before this list:** `ask` — eval-only, removed by issue #3849 / PR #3929.
It is not a row above because this table lists tools *this change* removes.

**Reading the "Absorbs" columns:** some baseline tools are not named individually
because a container already absorbs them transitively — `get_point`, `get_events`,
`get_operator`, `get_governance` sit inside `get`; `health`, `status`, `stale`,
`taxonomy`, `list_pointkinds`, `list_sources`, `list_tags`, `list_namespaces`,
`list_graphs`, `list_topics` sit inside `overview`; `paginated_query` and
`query_points_by_tag` sit inside `query`; `index_sessions` and `ingest_corpus` sit
inside `index_files`.

Two names that appear in earlier drafts of this list — `index_source` and
`manage_confidence` — were **proposed container names that never shipped**. Neither has
ever existed as a tool in this repo, so neither is a row above. `index_source`'s job is
done by `register_source`; the confidence tools are split across `recall_beliefs`,
`stabilize_beliefs` and `revise_knowledge`.

## Design decisions

1. **Cost policy:** write-time LLM cost is ours; **read-time LLM is not offered**. If a
   question needs an LLM, it is answered from data (search / list / recall), never by
   spending tokens at the customer's request.
2. **Operator-only tools stay separate** where merging would force a choice between
   exposing a privileged capability and losing a customer-visible one.
3. **A writer never shares a read-only name.** `approve_merge` is split out of
   `review_link_candidates` for exactly this reason.
4. **Points, Events, Sources, Subjects, Objects and Documents are all entities**
   (ontology §1), so one `create_entity` with `type=` covers the whole creator family
   with the right fields per type. `create_source` is the exception: a Source is a
   provenance anchor whose **URL is its node identity**, with tier canonicalisation,
   upsert-by-content-hash and its own journaling — so it stays `register_source`.
5. **Naming:** verb + object, 2–3 words, no internal jargon. `get_entity` not `get`;
   `explore_connections` not `explore_graph`; `adjust_relationship` not `operator_action`.
6. **Two shapes for ingesting files, deliberately:** `index_files(dir)` for a
   self-hoster with files on the server's disk; `capture_knowledge` / `ingest` for a
   hosted customer sending content. Both are needed; neither is redundant.

## Open items

| Item | Status |
|---|---|
| **Confidence reads currently WRITE.** `get_confidence` refreshes and stores the score (for a dirty root it calls `self.dream(dirty_only=True, ...)` inside `TortoiseSDK.get_confidence`, `tortoise/sdk.py`, which writes `SET n.confidence = p.c` in `tortoise/dream.py`). | **✅ RESOLVED 2026-10-21 (#4122) — the LABEL half.** The entry is `risk="maintenance"`: it keeps `graphs:read` (so `recall_beliefs` may absorb it without breaking the read guarantee for KEY SCOPE) and it declares `readOnlyHint: false`, so no client auto-approves it as a read. The read path still stores a derived score — that is the tier's defining behaviour, recorded in the `OVERRIDES:` note under *reads and writes never share a tool*. *(Cited by symbol, not line: line numbers go stale on every rebase.)* |
| **`tortoise_get_entity` must NOT be retired** by the #3883 warning shim. The map must retire `tortoise_get` → `tortoise_get_entity(id, type=...)` instead, because `list_tools` strips retired names *by string* — so retiring `get_entity` while re-declaring it would hide it from agents entirely. | **✅ RESOLVED — PR #4031 retires `tortoise_get` into `tortoise_get_entity` and keeps `get_entity` live; the tool gained the `type=` dispatch (`point`\|`entity`\|`operator`\|`events`\|`governance`), so the named replacement call resolves, and `TortoiseSDK.get_entity` is untouched. The earlier inverse (retiring `get_entity` into `tortoise_get`) was the must-fix this row raised.** |
| **`compute_confidence` is mislabelled in the same way.** It persists: `TortoiseSDK.compute_confidence` (`tortoise/sdk.py`) runs `MATCH (n:Point {id: p.id}) SET n.confidence = p.c`. | **✅ RESOLVED 2026-10-21 (#4122) — `risk="maintenance"`** (keeps `graphs:read`, drops `readOnlyHint`; explicitly `idempotent=False` because call 2 starts from call 1's stored value). The earlier plan to move it to `stabilize_beliefs` is superseded by the ruling. |
| **`packs_list` is tenant-facing and NOT on the MCP.** Described as *"List this team's active packs … another tenant's packs are never observable"* (`tortoise/tool_registry.py`). Its read classification is `risk="maintenance"` since #4122 (the `get_tenant_packs` self-heal MERGE) — it keeps `graphs:read` and no longer claims `readOnlyHint`. **Settled 2026-09-22: it is SDK/REST only and POST-BETA** — the tenant read is preserved there, and no competitor exposes pack installation on its agent API, so the agent-facing MCP does not carry it. Per-graph curation is tracked by **#4663**. | **✅ RESOLVED 2026-09-22 — SDK/REST post-beta (#4663)** |
| **`pack_install` is tenant-facing too — an unflagged write.** It has `http_policy=True` and is described as *"Install a custom expansion pack on the **HOSTED surface** … stores the manifest in the tenant graph and activates it"* (`tortoise/tool_registry.py`); the baseline records it `served: http`. **Settled 2026-09-22: SDK/REST only and POST-BETA**, so the tenant-visible write survives instead of being destroyed by folding it into an operator-only tool. (Its `hosted_only=True` flag is separately dead — see #4114.) Per-graph curation is tracked by **#4663**. | **✅ RESOLVED 2026-09-22 — SDK/REST post-beta (#4663)** |
| **`run_onboarding` absorbs two read-only tenant-served tools** — `onboarding_state` and `onboarding_github_status` are `risk="read"`/`readOnlyHint` and `http_policy=True`. So it is both read and write on the customer-grantable surface, which the principle above forbids. **Settled 2026-09-22: the container is DROPPED** for `check_connection` (*programmatic, returns a result — not a wizard*), and its two tenant-visible reads are **rehomed to the tenancy block (SDK/REST)**. The MCP surface therefore **loses both reads by design** — the capability is reachable, just not through the agent API. The other five setup members are not on the MCP target. | **✅ RESOLVED 2026-09-22 — dropped for `check_connection`; two reads rehomed to SDK/REST** |
| **`graph_set_recording` — dropping it removes an agent's self-heal path.** The registry says the capture `409` (*"Session recording is disabled for this graph"*) *"routes agents here; call this tool to turn recording back on, then retry the capture"* (the `tortoise_graph_set_recording` entry in `tortoise/tool_registry.py`). REST exposes the same operation at `PATCH /v1/graphs/{graph_id}`, but **an MCP client cannot reach REST** — so dropping it would leave an agent that hits a 409 with no recovery path inside MCP. **Owner ruling 2026-09-21: KEEP IT.** It becomes a **24th approved tool** — this list's own count, not the beta target's, which becomes **26** after its other deltas (a write; needs a `team:manage`-scoped key, so it is not a universal self-heal path). Its SDK method is the builder-only `update_memory_graph`; the SDK's own per-field `graph_set_recording` stays discarded, consistent with the ruling that deleted `set_memory_graph_name`/`set_memory_graph_backend`. | **✅ RESOLVED — kept; the MCP target became 26 at that ruling (27 since #3540, 2026-10-17)** |
| **SDK method `TortoiseSDK.get_entity`** keeps the narrow meaning while this tool takes the broad one — same name, two meanings, one layer apart. | resolve in the SDK pass |
| **Bridge table** — every `type=`/`mode=` value in these 23 mapped to the SDK method behind it, before implementation starts. | pre-flight |

## Sequencing

1. Approve this list. *(done)*
2. **Publish the bridge table** (MCP `type=`/`mode=` → SDK method).
3. Implement the MCP list with the **SDK frozen** (method names + capabilities unchanged)
   so the handlers are written once.
4. Then the **SDK canonical list** (`config/surface-manifest.yml` declares **150** public
   methods — a number DERIVED from the class, never typed; it moved 147 → 150 at #4282
   phase 2.2 because `link_entities`, `update_knowledge` and `delete_knowledge` were
   approved rows with no `def` yet, so the declaration stays at 40 — and
   `docs/product/canonical-sdk-methods.md` agrees; reconcile the per-name mapping when
   that pass starts). Renames there are mechanical for the
   MCP handlers; only a *capability* removal can force an MCP change — which the bridge
   table will have flagged.

## Verification that produced this list

Checked against `origin/main`: **every baseline tool but one is absorbed by exactly one target
or explicitly removed**, **zero double-absorption**, **no collisions with REST routes**,
and **17 of the 23 names appear nowhere else in the repo** — the six that do are
`get_entity`, `create_entity`, `register_source`, `index_files`, `approve_merge` and
`run_onboarding`, and in every case the match is a different identifier or the existing
tool of the same name. The one unplaced tool is `graph_set_recording`, whose disposition is
**RESOLVED by owner ruling 2026-09-21: kept** — see the row above. The unplaced tool is now the
**24th** approved tool (the beta target became 26 at that ruling; 27 since #3540, 2026-10-17), so "zero orphans" holds.
Six breakages were found and
folded into the list above; two were filed as issues: **#4113** (surface guards assert tool
names by string, so they go vacuous on any rename) and **#4114** (`hosted_only` is declared
in the registry and never read).
