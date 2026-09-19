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

Adding an entry to `TOOL_REGISTRY` in [`tortoise/tool_registry.py`](tortoise/tool_registry.py) does not
just register a tool — it **expands what every agent can see**. That surface grew to 99 MCP tools and
152 public SDK methods without anyone deciding it should, so it is now gated.

The gate: **`tools/surface-guard.py`** (CI job `surface-guard`, part of the required `python-ci-gate`)
compares the live declaration against the approved baseline in
[`config/surface-manifest.yml`](config/surface-manifest.yml) and **fails closed** on any added tool,
any added public SDK method, any removal, any **changed SDK binding** (which method a tool fronts), any
change to how a tool is served (HTTP vs stdio-only), a registry whose entry count no longer matches the
baseline (a duplicate name a name comparison cannot see), or an exemption that has become reachable. A
missing, unreadable, or malformed baseline is also a failure, as is an unreadable served-surface
declaration — the guard must never skip a check and still report success.

**What the gate does *not* fail on: an added field on an existing response.** A field that is off
by default, and leaves the response unchanged when it is off, is neither a new tool nor a new
endpoint — so it does not gate **as an addition**. The precedent is the W4 why-layer key on the
`ask` response: written only when `TORTOISE_W4_ENRICHMENT` is truthy (1/true/yes/on; unset or `0`
means off), with every other field byte-identical when it is absent. That is a different `why` key
from the one on `volunteer_context`, which is present by default.

**One qualification: the gate also fingerprints implementations.** It records a digest of each
registered tool's own code object, so a field added *inside a tool's handler* changes that tool's
fingerprint — and, since the fingerprint covers the function's source position, the fingerprint of
every tool defined after it — and reds the gate, correctly, as a changed implementation rather than
a new tool. Add response fields in the SDK or assembly layer, not inside a tool function.

**And it must still be recorded.** Every such addition goes in the `response_fields` block of
[`config/surface-manifest.yml`](config/surface-manifest.yml), rendered as a table in
[`docs/product/mcp-sdk-surface.md`](docs/product/mcp-sdk-surface.md). Two of `check`'s properties
defend that record: an empty or missing `response_fields` block is a failure, and every entry must
name a tool or endpoint that exists in the manifest — so the record can be neither deleted nor left
unanchored. Be precise about the half the machine cannot check: nothing inspects response bodies at
runtime, so a field that nobody recorded at all is **not** caught by any check. That half is a
reviewing obligation, and it is stated as one rather than implied to be automatic. The carve-out is
about what the guard *fails* on — not about what goes *unrecorded*.

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
   (`co_filename:co_firstlineno:sha256(co_code)`) — deliberately not `__module__`/`__qualname__`,
   which are writable strings a shadow implementation can simply copy from the tool it replaces.

**Scope, stated plainly.** The gate constrains the *registration routes* — what `TOOL_REGISTRY`
declares, what the server registers, and what the transforms do. It is **not** a security boundary
against someone who edits `mcp_server.py` and `tools/surface-guard.py` together; a party who can edit
the guard can defeat any gate, and the control for that class is required review, not this check. What
the gate guarantees is that the surface cannot grow as a **side effect** — silently, in a diff nobody
reads, through a route nobody chose.

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
