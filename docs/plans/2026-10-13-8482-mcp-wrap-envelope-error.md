---
title: "Plan — #8482 a wrap-envelope MCP tool must not ship a payload its own schema forbids"
type: engineering
domain: capability
doc_status: draft
created: 2026-10-13
subjects.team: organisation-design-team
aboutSubjects: organisation-design-team
ownedBy: organisation-design-team
governingAgreement: "#8482 (P0-blocker, owner-raised)"
aboutObjects: mcp-server, tool-registry, tortoise-search, tortoise-expand-relationships
---

<!-- research-path: none (justified-skip: zero new third-party deps; every third-party behaviour below was read from the INSTALLED, PINNED source — fastmcp 3.4.6 and the TS SDK bundled in playwright's utilsBundle.js — not from training data) -->

# #8482 — a failed wrap-envelope tool ships `{"result": {"error": …}}` and the client refuses the call

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** `tortoise_search` / `tortoise_expand_relationships` (and the 9 other array-envelope tools)
stop failing with `MCP error -32602: … data/result must be array` when the underlying SDK call
raises, and the SDK's real message reaches the caller.

**Team:** organisation-design-team

**Issue:** #8482. **Deliberately not in scope:** the `tortoise_search` SDK raise itself
(`Type mismatch: expected Null or VectorF32 but was List`) — that root was #8316, closed. This issue
is the *masking*: a real failure arrives as an unreadable schema complaint, so it reads as
"nothing relevant exists" rather than "the reader is broken". Also out of scope: the
`_safe`-swallowed-failure telemetry status for the *general* case (#8135) — see Task 4 for the one
part of it this seam must fix to stay self-consistent.

## The mechanism (settled, reproduced — not inferred from the error text)

| # | Step | Anchor |
|---|------|--------|
| 1 | `tortoise_search` is annotated `-> list[dict]` — a NON-object return | `tortoise/mcp_server.py` (`def tortoise_search`) |
| 2 | FastMCP turns a non-object annotation into a wrap envelope: `{result: {type: array}, required: [result], x-fastmcp-wrap-result: true}` | `fastmcp/tools/function_parsing.py:349-351` |
| 3 | `_safe` answers EVERY failure with a **dict** (`_SafeError`), never a list | `tortoise/mcp_server.py` (`_SafeError`, `_safe`) |
| 4 | `Tool._convert_result` wraps the serialized value **unconditionally** when the flag is set → `{"result": {"error": …}}`, and `is_error` stays **False** | `fastmcp/tools/base.py:363-369` |
| 5 | A conforming client validates `structuredContent` against `outputSchema` whenever it is PRESENT → `-32602 InvalidParams: data/result must be array`; the SDK's message is discarded | TS SDK `callTool`, `utilsBundle.js:86191-86203` |
| 6 | Second producer: the transport wait-bound refusal is built OUTSIDE FastMCP's envelope → `{"error": {…}}` with no `result`, while `result` is **required** | `tortoise/mcp_server.py` (`_await_under_mcp_wait_bound`) |

> Anchor convention: `tortoise/mcp_server.py` rows name the SYMBOL, not a line number. Two review
> rounds corrected four line anchors and a main merge invalidated all four again — the symbol
> locates the code and does not drift.

**The discriminator the issue could not find** is the RAISE, not the annotation. The four tools it
could not separate (`tortoise_check_structure`, `tortoise_packs_list`, `tortoise_list_batches`,
`tortoise_list_namespaces`) carry the SAME envelope and work — because their SDK calls succeed and
yield a real list.

**Which side is wrong:** the failure payload, not the declared schema — the schema is the
product's *success* contract (`tortoise_search` does return an array) and is not relaxed; widening
it to admit the error dict converts a loud failure into a silent false negative — the exact failure
mode the issue warns against. The wrong shape is the **failure delivered in the success channel**.

## Architecture

One seam: `_wrapped_call_tool` (`tortoise/mcp_server.py`), the documented single dispatch point
every tenant tool call crosses, which already inspects result shape (`_mcp_result_count`) and
classifies failures. When the tool's own advertised schema proves the payload unreadable, the result
is rewritten to the channel MCP has for failure — `content` kept (it carries the real message),
`structured_content=None`, `is_error=True`. That pair is ACCEPTED by rule (validated only when a
payload is present) and the schema is **not** relaxed.

Two costs decided the design:

- **The schema read is behind a cheap pre-filter, and memoised.** `mcp.get_tool` is measured
  p50 ≈ 0.13–0.4 ms / p95 ≈ 1.2 ms per call (FastMCP rebuilds the tool), so probing it
  unconditionally would tax the whole surface to protect a failure path.
  `_maybe_wrap_envelope_violation` recognises the two violation shapes from data already on the
  result; for an **array**-envelope success that is two dict lookups and **zero** schema reads
  (measured: 0/200 lookups on a successful `tortoise_search`). The two **union** tools have
  legitimate non-list successes (`tortoise_delete_entity` → bool, `tortoise_query` →
  `{results, total, hasMore}`), so their success DOES schedule a schema read — but
  `_wrap_envelope_schema` memoises the classification per stored tool
  (`_WRAP_ENVELOPE_SCHEMA_CACHE`, keyed `tool:<name>@<version>#<component id>` — the stored key
  plus the identity of the LIVE component, so a same-name re-registration cannot be served a stale
  schema), so the rebuild is paid once per tool, not once per dispatch (measured: 40 → 21 `get_tool`
  calls over 20 union dispatches; the 21st is FastMCP's own dispatch lookup).
- **Fail-open on an unreadable schema.** The repair only ever applies to a payload PROVEN to
  violate the declared schema, so a tool whose schema cannot be read keeps working. Fail-closed
  would break object-annotated tools, whose `_safe` dict is perfectly valid.

## Pattern Research

**Skipped — zero third-party dependencies change.** The two third-party behaviours the design rests
on were read from installed source, not assumed: fastmcp 3.4.6 (`tools/base.py:352-369`,
`tools/function_parsing.py:340-352`, `client/mixins/tools.py:428-446`) and the TS MCP SDK's client
validator (`playwright/driver/package/lib/utilsBundle.js:86191-86203`). The client's rule is the
load-bearing fact: absent payload + `isError` is accepted; present payload is always validated.

---

### Task 1: the seam — recognise and repair a schema-violating envelope

**Intent:** A failed wrap-envelope tool must deliver its failure legibly instead of a `-32602`
about a payload nobody can read.
**Acceptance:** for every array-envelope tool, a `_safe` failure and the wait-bound refusal both
produce `is_error=True` with no `structured_content` and the message intact; a union envelope and an
object tool are untouched; the array-envelope success path performs zero schema reads, and a union
tool's success pays at most one memoised classification.
**Files:**

- Modify: `tortoise/mcp_server.py` (`_WRAP_ENVELOPE_SCHEMA_CACHE`, `_local_tool_cache_key`,
  `_wrap_envelope_schema`, `_violates_wrap_envelope`, `_maybe_wrap_envelope_violation`,
  `_safe_failure_message`, `_schema_error_result`; the repair in `_wrapped_call_tool`). The
  typed-vs-union classifier lives in the guard as `_envelope_result_type` (a test helper — no
  production caller)

### Task 2: the red-first test

**Intent:** Prove the repair is what fixes it, and pin the class rather than the two named tools.
**Acceptance:** the file fails before the change and passes after; the sweep walks the REAL registry.
**Files:**

- Create: `tests/test_mcp_wrap_envelope_error_8482.py`

### Task 3: registration

**Intent:** Make the guard actually run when the seam it pins moves.
**Acceptance:** `tools/ci_manifest.py` exits 0; `tests/test_ci_selection.py` leg/time coverage passes.
**Files:**

- Modify: `config/ci-surfaces.yml` (register the new file on `api`, the `mcp_server` family surface;
  **no** `durations` weight — the collector is that map's sole writer and 86 registered files
  legitimately carry none)

### Task 4: the classification the same seam already owned

**Intent:** The stdio auth gate's refusal is *also* a failure inside a wrap envelope, so the
top-level probe missed it and the call was recorded as `ok` — the same "a failure that reads as a
success" class, and the reason this defect could go unnoticed (#8135).
**Acceptance:** a wrap-envelope tool under the stdio auth gate records
`status=auth_error, error_kind=stdio_auth_gate`, and the refusal is legible to the client.
**Files:**

- Modify: `tortoise/mcp_server.py` (`_safe_failure_message` read through the envelope)

---

## Integration surface

| Surface | Map | Why |
|---|---|---|
| MCP `tools/call` result shaping | `_wrapped_call_tool` (single dispatch point) | the only place both producers cross |
| FastMCP wrap envelope | `x-fastmcp-wrap-result` from the return annotation | the contract the payload must satisfy |
| `_safe` failure contract | `_SafeError` dict, unchanged | the repair is a WIRE repair, not an SDK-facing one |
| telemetry | `mcp_tool_call` status vocabulary | classification must agree with the wire |

No MCP tool is added, removed or renamed — the surface is owner-approved (82 rows; verified with
`tools/surface_manifest.py check` and `tools/surface-guard.py`, both exit 0).

## Evidence (cycle log)

- Reproduced: `tortoise_search` on an SDK raise → `structuredContent={"result":{"error":"Type
  mismatch: expected Null or VectorF32 but was List"}}`, `isError=False`, declared
  `result: array` → the client's own `-32602`. Confirmed end to end through a real MCP session.
- Red first, against `origin/main` (the 12 tests that existed then): `8 failed, 4 passed`;
  `12 passed` with the fix. Red first for the follow-up tests: `10 failed, 4 passed` with the repair
  neutralised at runtime (the working-tree-discard guard refuses an in-place revert, #709), `14
  passed` with it. The 4 that pass either way are the do-not-break guards. **Those counts are
  historical and belong to the commit that produced them** — the guard has grown since (the auth
  classification and envelope-memo cases were added later), so read the test file itself, not this
  line, for the current total.
- Repo-wide: 13 registry tools carry the envelope (11 typed + 2 union) — swept, not just the two
  named. The guard forces a refusal for ALL 82 registry tools; the result a client would receive is
  validated against the tool's OWN schema for the 13 wrap-envelope rows (the ones the helper can
  read a `result` contract from). The 69 object/`anyOf` rows are exercised for the refusal's shape
  and the retry signal only — `_schema_validates_against` is envelope-shaped by construction and
  does not validate an object row's payload.
- No regression: `test_mcp_server.py`, `test_mcp_telemetry.py`,
  `test_mcp_result_count_telemetry.py`, `test_3926_error_prop_guard.py`,
  `test_transport_wait_bound.py`, `test_surface_guard_pin_4606.py`, `test_markers.py` + the new file
  — **224 passed, 17 skipped**. `test_ci_selection.py` + `test_ci_timing.py` — **371 passed**.
  `tools/ci_manifest.py`, `tools/registry_integrity.py`, `tools/surface_manifest.py check` and
  `tools/surface-guard.py` all exit 0. The one intermittent failure seen earlier,
  `TestOverhead::test_p95_under_5ms`, fails identically on a clean tree (6.70 / 8.92 ms vs
  7.96 / 6.76 / 8.50 ms with the fix; the repair performs ZERO lookups on that path) and is the
  already-filed load-sensitive flake **#3678**.
- Independent review (fresh context) found four defects in the first commit — the wait-bound retry
  signal dropped, the wrapped-scalar escape, the repaired failure recorded `ok`, and a tautological
  class guard with an invalid success sample. All four are fixed and pinned in `aebabcb32`; the
  dropped retry payload is re-homed on `_meta.tortoise_error`, recorded in
  `config/surface-manifest.yml`'s `response_fields` (13 entries, the gate cannot see it by design).
- The repair never ships a message-less error: a `-> list[dict]` tool that returns `None` produces
  `{"result": None}` with no text block, so `_schema_error_result` SYNTHESISES a text block naming
  the tool and the violated schema when the kept `content` is empty. No typed registry tool has
  that return path today (AST-checked across all 11), but a repaired error with no message is
  exactly the unreadable degradation this issue is about, so the channel is closed rather than
  documented as a limit.
- Known limit, deliberately not closed: the repair recognises the ENVELOPE, not the payload's
  contents, so array ITEMS violating the declared `items` schema are not caught — no producer in the
  registry today, and a partial JSON-Schema re-implementation would imply a completeness it lacks.
