# Contributing to Tortoise

Thanks for wanting to contribute. Tortoise is the epistemic graph engine from
Premise Labs — a small team. Before you open a PR, please read this page and
the license note below.

## What this project is

Tortoise is licensed under the **Business Source License 1.1** (BSL) — see
[LICENSE](LICENSE). BSL is **not** an OSI open-source license: you may read,
use (within the Additional Use Grant), and modify the code, but the license
terms differ from MIT/Apache/BSD. Four years after publication the project
converts to **MPL 2.0**.

This matters for contributions: because the project is BSL today (and MPL 2.0
later), we need a clear license grant from every outside contributor. See the
[contribution license note](#contribution-license-note) below.

## Ways to contribute

- **Bugs & feedback** — file a bug report via the [bug template](https://github.com/daniel-ospina/tortoise/issues/new?template=bug_report.yml), or ask/chat in [Discussions](https://github.com/daniel-ospina/tortoise/discussions).
- **Questions** — Discussions, not issues.
- **Code** — see below. Please open an issue or a Discussion first for anything
  non-trivial: this project is small and deliberately scoped, and we route
  inbound through a triage queue — a PR that arrives without a tracked issue
  may be closed and queued rather than reviewed in place.

## Opening a pull request

1. **Start from a tracked issue.** If your change isn't already an issue,
   open one first and reference it in the PR.
2. **Fork + branch.** Work on a descriptive branch; keep the change small and
   focused.
3. **Add the contribution license note.** Every PR must include the donation
   statement below (the PR template has a checkbox for it). PRs without it
   cannot be accepted.
4. **Tests.** Tortoise runs `uv run pytest tests/ -v` (Python 3.12+; Docker
   FalkorDB lane by default — see [AGENTS.md](AGENTS.md) for the embedded
   carve-out). Add/adjust tests for your change and make sure the suite passes
   locally.
5. **CI must be green.** The project's checks (lint, tests, drift gates) run
   on every PR.

## The MCP tool surface and public SDK methods cannot grow by accident

> ⛔ **THE RULE.** You may not add or remove a tool from the MCP surface, or a method from the
> SDK surface, without **human approval from Daniel**. This is a mandated rule, not a
> suggestion: the procedure below is the *mechanism*; Daniel's approval is the *authority*.
>
> **WHY IT EXISTS.** The surface is the contract every agent and every customer integration is
> built on. Changing it changes what every agent can see and do, so it materially affects
> customer outcomes — it is not an implementation detail.
>
> **WHAT TO DO.** Get Daniel's approval **first**, before you write the change or re-cut
> anything, through the repo `AGENTS.md` "USER QUESTIONS" / "DECISION RELAY" path: say which
> tool or method, what it does, and why it is needed. Then follow the steps below, which record
> that approval on the row. A green gate is **not** the approval — see *What the gates do and
> do not prove* below.

Adding an entry to `TOOL_REGISTRY` in [`tortoise/tool_registry.py`](tortoise/tool_registry.py) does not
just register a tool — it **expands what every agent can see**. That surface grew to 98 MCP tools and
150 public SDK methods without anyone deciding it should, so it is now gated. (The #3863 curation then
retired 16 names; the live surface is 82 MCP tools, and the gate's baseline in
`config/surface-manifest.yml` records every one of them.)

The gate: **`tools/surface-guard.py`** (CI job `surface-guard`, part of the required `python-ci-gate`)
compares the live declaration against the approved baseline in
[`config/surface-manifest.yml`](config/surface-manifest.yml) and **fails closed** on any added tool,
any added public SDK method, any removal, any **changed SDK binding** (which method a tool fronts), any
change to how a tool is served (HTTP vs stdio-only), a registry whose entry count no longer matches the
baseline (a duplicate name a name comparison cannot see), or an exemption that has become reachable. A
missing, unreadable, or malformed baseline is also a failure — including a **duplicate row name**, which
a name-keyed comparison silently drops and so cannot verify, and a row whose `name` is not a non-empty
string (which used to crash the check with a traceback rather than fail closed) — as is an unreadable
served-surface declaration: the guard must never skip a check and still report success. An SDK row is
identified by its name: it must be `sdk:<its method>`, so a row that renames a real method or fronts an
existing method under a new name is a failure rather than an invisible addition.

**What the gate does *not* fail on: an added field on an existing response.** A field that is off
by default, and leaves the response unchanged when it is off, is neither a new tool nor a new
endpoint — so it does not gate **as an addition**. The precedent is the W4 why-layer key on the
`tortoise_analyze` response: written only when `TORTOISE_W4_ENRICHMENT` is truthy
(1/true/yes/on; unset or `0` means off), with every other
field byte-identical when it is absent. That is a different `why` key from the one on
`volunteer_context`, which is present by default.

**One qualification: the gate also fingerprints implementations.** It records a digest of each
registered tool's own code object, so a field added *inside a tool's handler* changes that tool's
fingerprint — and, since the fingerprint covers the function's source position, the fingerprint of every
tool defined after it — and reds the gate, correctly, as a changed implementation rather than a new
tool. Add response fields in the SDK or assembly layer, not inside a tool function.

**And it must still be recorded.** Every such addition goes in the `response_fields` table in
[`docs/product/mcp-sdk-surface.md`](docs/product/mcp-sdk-surface.md), which is rendered from
`config/surface-manifest.yml`. Two of `check`'s properties defend that record: an empty or missing
`response_fields` block is a failure, and every entry must name a tool or endpoint that exists in
the manifest — so the record can be neither deleted nor left unanchored.

**Be precise about the half the machine cannot check.** Nothing inspects response bodies at
runtime, so a field that nobody recorded at all is **not** caught by any check. That half is a
reviewing obligation, and it is stated as one rather than implied to be automatic. The carve-out is
about what the guard *fails* on — not about what goes *unrecorded*. An unrecorded field has no
approval behind it and the carve-out does not cover it.

The baseline cannot be *edited* either. `tools/surface_manifest.py check` (the same required job) also
**re-derives the whole baseline from the declaration** and reds on any difference outside the columns that
are not a function of the code. Those columns are exactly `NON_DERIVABLE_ROW_KEYS` and
`NON_DERIVABLE_DOC_KEYS` in the tool (the authority — read them there): `used_by`, `recommendation`,
`basis` and `reason` (they read a machine-local call log), the human `approval` reference and `exemption`
flag, and the doc-level `cut_at_commit` / `approval_status` / `approval_principal` / `approval_pr` /
`response_fields`. One
consequence worth stating plainly: those columns **are** editable in a PR, so the review evidence a reader
sees in the generated document is not gate-verified — the served surface is. So hand-editing the
baseline's `counts:`, a row's description, its `class`, `served` or `served_from`, or any other derived
cell is a red build, not a shortcut: change the registry, then re-cut.

Two things have no automatic path and are updated by hand as part of a re-cut: the `approval` fields (a
re-cut resets them to `null` and marks the baseline `pending-owner-approval`, so the owner re-records
them — see "To propose an addition"), and `baseline_counts` in
[`config/surface-order.yml`](config/surface-order.yml), the frozen keyword distribution the derived
`counts:` is checked against. The check reads that table for `keywords`, `tokens`, `family_rank` **and**
`baseline_counts`, and refuses a table whose shapes or ranks are wrong or whose `baseline_counts` is
empty — an empty table cannot turn the distribution check off. If an added tool moves the distribution,
the check reds until the order table records the new expectation. Note what the approval reference does
and does not do: the gate verifies its **shape** (a PR number and an `@handle`), never that the review it
names exists. The carrier for the approval is the **#4282 mandate above and Daniel's review** — not a
machine control. (The repository ruleset the #3863 scope doc proposed as that carrier was **rejected by
the owner on #4282** as over-engineering; do not re-introduce one.)

**"Added tool" means the advertised surface, not just the registry.** A tool can reach agents without
ever entering `TOOL_REGISTRY`, by three routes the guard checks separately, because each is invisible
to the others:

1. **Registered on the server** — a `@mcp.tool()`-decorated function in `tortoise/mcp_server.py`, or a
   direct `mcp.add_tool(...)`. Caught by enumerating the served set through **both** the pre-transform
   aggregate and the protocol `tools/list` path and reding on anything undeclared in **either**; each
   view alone has a blind spot, since the pre-transform view misses transforms and the protocol view
   applies auth/visibility filtering.
2. **Injected by a server-level transform** (`mcp.add_transform`, whose `list_tools` appends a tool —
   `mcp_server.py` already uses that API for `_HTTPToolFilter`). Caught by the **`allowed_transforms`**
   check, which is derived from the `add_transform` call sites in `mcp_server.py` and reds on any
   transform beyond the approved set.
3. **Replacing an approved tool's implementation** — `mcp.add_tool(fn)` where `fn` reuses an approved
   name silently REPLACES it while leaving the name set and the entry count identical. Caught by the
   per-row **`served_from`** fingerprint, which records the **code object** behind each tool
   (`co_filename:sha256` over the code object's bytecode, names and constants) — deliberately not
   `__module__`/`__qualname__`, which are writable strings a shadow implementation can simply copy
   from the tool it replaces. `co_firstlineno` is deliberately **not** part of the identity: a pure
   code move is not a change to the served implementation.

**Scope, stated plainly.** The gate constrains the *registration routes* — what `TOOL_REGISTRY`
declares, what the server registers, and what the transforms do. It is **not** a security boundary
against someone who edits `mcp_server.py` and `tools/surface-guard.py` together; a party who can edit
the guard can defeat any gate, and the control for that class is Daniel's review — the #4282 mandate
— not this check. What the gate guarantees is that the surface cannot grow as a **side effect** —
silently, in a diff nobody reads, through a route nobody chose.

**What the gates do and do not prove.** `tools/surface-guard.py` and
`tools/surface_manifest.py check` are **drift / consistency controls**. They compare the live
declaration against the frozen baseline, so they catch a change that is *unrecorded* — a new tool or
method that entered the code without the baseline being re-cut. They **cannot** prove that Daniel
approved an expansion: a change that updates the registry **and** the baseline together satisfies
every property and passes both. The approval is carried by the mandated rule above and by Daniel's
review, never by a machine check. A lane that reads a green gate as "approved" will not seek
approval — which is the one failure this section exists to prevent.

**To propose an addition**, in the same PR:

1. Change the registry.
2. `uv run python tools/surface_manifest.py cut` — folds the change into the baseline and marks
   `approval_status: pending-owner-approval`.
3. `uv run python tools/surface_manifest.py render` — regenerates
   [`docs/product/mcp-sdk-surface.md`](docs/product/mcp-sdk-surface.md).
4. Get the owner's approval and record it **per row** in `approval:` — a PR number and a principal,
   never a bare `yes` or a date. This applies to **every** row, MCP tools and SDK methods alike; once
   `approval_status: approved` is set, a row without a recorded approval is a red build. The
   `exemption: true` flag is **not** an approval carve-out — it records a method deliberately outside
   the reachable set, and the guard reds if such a method later becomes reachable.

**A red `surface-guard` is the gate working, not a bug.** Do not resolve it by editing the baseline to
match the registry — that is precisely the unapproved expansion the check exists to catch. The curated
list, including what each tool does, what uses it, and the recommendation for it, lives in
[`docs/product/mcp-sdk-surface.md`](docs/product/mcp-sdk-surface.md).

## A finding must carry the tree it was measured against

A defect report is **true of the tree it was measured on** and may be **false of the product**. Three
lanes once reported a defect that was already fixed on `origin/main` — each measured against a stale
worktree or an un-rebased branch. One follow-up was about to be closed as *moot* for a file that
exists on main; one epic phase was sequenced as *blocking* to re-implement a defect that commit
`65b26f6c2` had already fixed (#4290). Worktrees do not self-update, so a six-day-old checkout reads
exactly like a live defect unless the report says which tree it came from.

Every finding therefore carries **one machine-produced line**:

```
Measured at: <ref-label>@<40-hex-sha> on <YYYY-MM-DD>
```

Do not hand-type the SHA. Run the emitter in the checkout you actually measured against and paste its
output into the issue or comment:

```bash
python3 tools/finding_provenance.py --emit
```

Before reporting, ask the cheap question — *is my checkout behind, and by how many commits?*:

```bash
python3 tools/finding_provenance.py --checkout
```

The gate is mechanical, not a convention. [`tools/finding_provenance.py`](tools/finding_provenance.py)
answers whether the finding was measured against a tree that **contains** the fix — using an ancestry
test (`git merge-base --is-ancestor`), never SHA equality — and **fails** on a stale measurement:

```bash
python3 tools/finding_provenance.py --validate finding.md            # contains current origin/main?
python3 tools/finding_provenance.py --validate finding.md --fix 65b26f6c2   # contains the claimed fix?
gh issue view 4009 --json body -q .body | python3 tools/finding_provenance.py --validate -
```

Exit `0` = current, `1` = stale / undated / predates the fix, `2` = environment error. **A missing
`Measured at:` line is a failure, never a pass** — a free-text field nothing validates is the defect
the gate exists to remove. The `.github/workflows/finding-provenance.yml` job runs the same check when a
finding issue is opened (and on demand via `workflow_dispatch`), annotating an undated or stale finding
with the `provenance-stale` label instead of letting it be acted on silently. It exempts
`beta-feedback` reports — an external report is about a released version, not a commit.

## Contribution license note

This project follows the pattern used by MariaDB (the BSL originators) for
receiving outside contributions into a BSL-licensed codebase.

By submitting a pull request or patch, you agree that your contribution is
donated under one of:

1. the **BSD 3-Clause License** (the "New BSD" license), with the following
   statement in the PR description:

   > I donate this contribution to the Tortoise project under the BSD
   > 3-Clause License.

   (BSD-3 is compatible with both the current BSL terms and the future MPL 2.0
   conversion, so the project can continue to use your code after the Change
   Date), or

2. dedicated to the **public domain** (CC0 / "no rights reserved"), with the
   same statement style, or

3. a **Contributor Agreement** with Premise Labs (for regular/large
   contributors — ask first).

## Review & merge policy

- Outside contributions are **untrusted code**: every PR goes through normal
  review + CI and is merged only by a maintainer. Nothing is auto-merged.
- Be patient — this is a small team and inbound flows through a triage queue.
- If your PR arrives without a linked issue, expect it to be closed with a
  pointer and queued rather than reviewed immediately.

## Code of conduct

Be constructive and respectful. This is a research-grade engine — questions
about design decisions are welcome in Discussions.
