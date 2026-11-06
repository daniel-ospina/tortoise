---
title: "The bounded-operation contract (#8447, C1)"
type: engineering
domain: platform
doc_status: live
created: 2026-11-02
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: bounded operation, truncation marker, #8447, AIP-158
---

# The bounded-operation contract

**Issue:** #8447 (`P1-severe`) — *"contract: fail explicitly, never return a silently-truncated result"*.
**Census measured on this tree.** ⛔ There is deliberately **no commit pin on this line**: a head named
here is a claim about a *different* tree the moment any PR lands, and this census now cites
`<module>::<name>` symbols rather than line numbers, so a re-derivation against some other head is
neither possible nor useful. Each row is read from the code on the branch that carries the row, and
`tests/test_8447_truncation_marker.py::TestTheCensusNamesItsEvidenceHome` is what holds it true — it
reds on a line-number citation in §2 and on a symbol the tree does not bind. The hosted
`GET /v1/points` row is the exception worth naming: it was measured **here**, by PR #10622, which is
the change that made it **MARKS** — it read SILENT on every earlier head, and recording this document
at an older commit while carrying the new row is exactly the stale-map shape (#6134) it exists to
prevent.
**Status:** C1 of #8447 (this document). C2 (boundary tests per surface) is in the tree. C3 (the four
doors): **record write** and **session read** landed on `main`; the **ingest door** lands with PR #11067
(row below); **capture** is verified compliant and carries its own evidence in §6.

---

## 1. The rule

> **A bounded operation must either return complete results, or mark the result as incomplete and
> say why. It must never return a silently-truncated result that is indistinguishable from a
> complete one.**

A *bounded* operation is any operation whose result size is a function of a caller-supplied or
internally-fixed bound: a `limit=`/`page_size=`/`top_k=` parameter, a result cap, a pagination
cursor, a body/log cap, or an ingest chunk.

The failure this prevents is not "the result was short". It is **"the caller cannot tell a short
result from a complete one"** — which turns a bound into silent data loss for every consumer that
does not already know the population size.

### 1a. The refusal clause (the same shape, one step earlier)

> **A refused operation must either refuse visibly (an error, or a reply field that says so) or
> succeed. It must never return a value indistinguishable from success — `None`, `[]`, or a `200`
> with an empty body — while recording the failure somewhere the caller does not read.**

`0`, `"empty"`, and *"I could not read it"* must never collapse into one answer.

---

## 2. The census — bounded surfaces classified by whether the caller can TELL

Three states, and only three:

| state | meaning |
|---|---|
| **MARKS** | the result carries a machine-readable incompleteness signal (a `truncated` flag, an `incomplete` marker, a `total_available`, a cursor that is empty exactly at end-of-collection) |
| **REFUSES** | an ask outside the surface's domain raises, rather than being quietly rewritten |
| **SILENT** | **the defect.** The caller cannot tell. |

Measured on this tree. ⛔ **The quoted code is the citation's anchor; the line number is convenience.**
A bare `path:line` re-stales the moment a neighbouring PR inserts above it — this document has already
been through it twice (`sdk.py` moved 695 lines under these same numbers between `62e3f2f61` and
`d72983b99`, the exact class #10523's own commit records). So every row quotes the code it means, and
a reader who finds the number has drifted should search the quoted fragment: that is the claim.

| surface | bound | state | evidence |
|---|---|---|---|
| `TortoiseSDK.get_cross_lens_candidates` (MCP: `tortoise_find_cross_lens_candidates`) | `max_candidates` (200 on the MCP wrapper) — one of three bounds on this surface, see ⚠️ below | **MARKS** | `tortoise/sdk.py::get_cross_lens_candidates` — returns `truncated`, `cap` and `count` alongside the rows (`truncated = len(all_candidates) > limit`, where `limit = min(int(max_candidates), CROSS_LENS_CANDIDATE_CAP)` is derived inside that method; there is no `limit=` parameter on this surface) |
| `event_store.read_after` | `limit` | **REFUSES** | `tortoise/event_store.py::read_after` — `if not 1 <= limit <= MAX_PAGE: raise ValueError(...)`, with the message stating *why* coercion would be indistinguishable from a drained stream |
| `GET /v1/search` (hosted) | `limit` | **MARKS** | `tortoise/hosted_api.py::search` — `response.update(truncation_field(trunc_out))`, i.e. `truncated` + `total_available` (PR #10500, merged). ⚠️ `count` is still rows **delivered**, not matched — the marker, not `count`, is the instrument |
| MCP `tortoise_search` (`tortoise/tool_registry.py::TOOL_REGISTRY`) · `GET /search` (selfhost, `tortoise/selfhost_api.py::search`) | `limit` | **SILENT** | both return a bare list (`response_model=list[PointResponse]` on the route); recorded as a **deliberate limit** on #8447's 2026-11-01 comment (§6) and restated in `config/surface-manifest.yml` → `response_fields[tortoise_search].truncated` (*"returns a bare list, so it does not carry the field; wrapping a list in an object is a declared return-type change"*), because a marker here IS a return-shape change |
| `TortoiseSDK.list_batches` (MCP: `tortoise_list_batches`) | `limit=20` | **SILENT** | `tortoise/sdk.py::list_batches` — `return items[:limit]`; the method's contract is `-> list[dict]` (declared on the same `def`), so there is no field in which to say "there are more" |
| `TortoiseSDK.suggest_entry_points` (MCP: `tortoise_suggest_entry_points`) | `limit=5` | **SILENT** | `tortoise/sdk.py::suggest_entry_points` — `results = results[:limit]` on a plain `[:limit]` cut with no offset and no cursor; declared `-> list[dict]` on the same `def` |
| `tortoise_query(text=…)` (MCP, `tortoise/tool_registry.py::TOOL_REGISTRY`; the `text=` arm only) | `limit` (100 on that arm) | **SILENT** | `tortoise/mcp_server.py::tortoise_query` calls `tortoise_fts_query(…, limit=eff_limit)` **without** the private marker sink, and the SDK method (`tortoise/sdk.py::tortoise_fts_query`) is declared `-> list[dict]` — so even though it CAN compute the marker (its `_truncation_out` sink), a bare list has nowhere to carry it. Its structural arm (`text=None`) is a different surface, and not all of its branches are marked, see ⚠️ below |
| `tortoise_query(tag=…)` (MCP, `tortoise/tool_registry.py::TOOL_REGISTRY`; the `tag=` arm) | `limit` on the **response** (`eff_limit`); **no** bound on the READ | **SILENT** un-paginated, **MARKS** paged — and the walk is not lossless | `tortoise/mcp_server.py::tortoise_query` slices `rows[:eff_limit]` in the HANDLER, because `tortoise/sdk.py::query_points_by_tag` accepts no `skip`/`limit` and its own docstring says it *"returns the WHOLE tag population"* — so the RESPONSE is bounded and the READ is not, which is the shape the structural arm's fix rejects 60 lines away. The paged form returns `{results,total,hasMore}`, but that accessor orders `ORDER BY p.createdAt DESC` with **no `id(n)` tiebreak**: `createdAt` is not unique, so consecutive `offset=` windows are not adjacent ranges of ONE order (the #5346 defect) and a walk drops and repeats rows while `hasMore` — computed from that one call's own list — cannot see the loss. `tests/test_5346_paged_read_total_order.py` pins `paginated_query`, not this accessor. **Not bounded here: adding `skip`/`limit` to the public accessor is an MCP/SDK surface change and needs the owner's approval.** Follow-up and census owner: #11062 |
| `GET /v1/points` (hosted, `tortoise/hosted_api.py::list_points`) | `limit=50` (`Query(50, ge=1, le=1000)`) | **MARKS** | the same function asks the store for `limit+1` (an internal probe row, dropped before delivery) and its own `mark_truncation`/`truncation_field` calls (`tortoise/truncation.py::truncation_field`) publish `truncated`; `total_available` is deliberately WITHHELD (`exact=False` — a probe proves *more exist*, not *how many*), so a truncated page takes the third state. `count` is still rows **delivered**, and the cap VALUES are unchanged |
| `GET /points` (selfhost, `tortoise/selfhost_api.py::list_points`) | `limit=50` | **SILENT** | `response_model=list[PointResponse]` — a bare list on the route |
| `tortoise-ingest` / `python -m tortoise.ingest` (CLI, `tortoise/ingest.py`) — the **ingest door** of C3 | the whole ask: a target the graph does not hold as a `:Document` Source, an idempotency claim, and a sweep that leaves work pending | **MARKS** (exit status) | `tortoise/ingest.py::IngestOutcome` — `main` returns a named outcome and `.returncode` **is** the exit status (`DONE`/`SKIPPED_*` → 0, `REFUSED_*`/`PARTIAL` → 1). ⛔ The exit status reaches the shell through `tortoise/ingest.py::cli`, which `pyproject.toml` names — **never** through `main`: the generated console wrapper is `sys.exit(<entry>())` and `IngestOutcome` is a `StrEnum`, so `sys.exit(<member>)` prints it and exits **1** for every arm including `DONE` (measured). A refusal writes NO completion artifact — no `--resolution` Point, no `points : N statements` summary, no `--out` render. ⚠️ **`PARTIAL` is not a refusal**: the run DID most of the ask, so the exit status is what carries the shortfall rather than an error — but ⛔ **what it writes is per-arm, not uniform**: full ingest prints its `points :` summary and writes its `--out` render; `--upgrade` returns `_do_upgrade`'s outcome from `main` **before** the completion block, so a PARTIAL `--upgrade` writes **neither**; `--upgrade-all` prints its own `upgrade-all complete:` summary and never writes a render. Two things are `PARTIAL` on every path — a sweep that leaves a candidate pending, and **an extract that reports failed sections** (`stats['failed_sections']`): `--upgrade`, `--upgrade-all` and full ingest all answer `PARTIAL` (exit 1) now, where they used to print `warning: N sections failed extraction` and return `DONE`. ⚠️ That list is empty from `ingest` today — the stage fills it only under `skip_on_failure=True`, which this module does not pass (`tortoise/extractor.py::_run_document` re-raises), so the branch is insurance, not a live path; the arms that pin it inject the stats (`tests/test_8447_c3_ingest_door.py::TestAPartialExtractionIsNotDone`). The **reachable** section-level partial is the S7 entity stage (`_s7_document_entities`, which DOES pass `skip_on_failure=True`): its omitted Subjects print a `warning:` and the run still exits 0 — a live **SILENT** bound, named here rather than fixed. ⚠️ **A skip writes nothing either** — and ⛔ **that is a change THIS PR makes, not a standing property**: before it, the skip arm fell through to the completion block and *did* refresh the render (along with writing the bogus `--resolution` Point and the `points :` summary). It now returns before that block, so the render file is left exactly as it was, and a caller who re-runs `tortoise-ingest` to refresh `--out` gets the old file and exit 0 — the `skip:` line on stdout is the signal. The `--resolution` Point and the `points :` line are absent because they were *lies*; the render's absence is a deliberate consequence of returning before the block, and is pinned by `TestTheSkipArmWritesNothing`. ⚠️ A **skip is not a refusal**: `SKIPPED_ALREADY_PROCESSED` / `_EXTRACTED` / `_NOTHING_PENDING` stay `0`, and the sweep's own `already claimed` bucket is NOT `PARTIAL` — an idempotent re-run's answer *is* the ask, so reddening it would train callers to ignore the exit status. Pinned by `tests/test_8447_c3_ingest_door.py` in both directions — an exit-status table (`TestTheExitContractIsOneMapping`), the live refusal (`TestARefusalIsNotSuccess`), the skip-writes-nothing arms (`TestTheSkipArmWritesNothing`), the partial-extraction arms on all three paths (`TestAPartialExtractionIsNotDone`), the live sweep (`TestTheSweepReportsOutstandingWork`) and the sweep decision in isolation (`TestTheSweepDoesNotRedOnItsOwnIdempotency`, whose stubbed population is what pins `not_document` separately from `unreadable` — `--upgrade-all` reads the WHOLE graph, so a live assertion cannot). ⚠️ **Which arm actually discriminates is stated precisely**: the *absence* of the `--out` render and of the `points :` summary is KILLED by `test_a_capture_metadata_run_on_a_non_document_is_refused` (that arm fell through to the completion block until this PR). The same two assertions on the `--upgrade` arm are REGRESSION pins, not mutant-killers — `--upgrade` has been terminal in `main` since #133, so they pass against the un-fixed module too (measured in review round 8). A test COUNT is deliberately not stated: a number here re-stales the moment an arm is added, which is the class #10523 records. ⛔ `--capture-metadata` on content with no `##` sections is `REFUSED_NOT_A_DOCUMENT` (exit 1): the ask — emit the document Source + `sessionCaptured` — is unperformable there, and it used to fall through to the completion summary with zero sources written. ⚠️ **This door MARKS its refusal, NOT its every bound**: `--max-utterances N` still cuts `sections[:N]` (`tortoise/extractor.py::extract_from_document`) with no marker, and `_do_upgrade` then clears `needs_extraction`/`doc_status` — so the cut is recorded as a complete extraction and no sweep can revisit it. **Two** of the sweep's buckets are permanently red and no flag of this command clears either: `unreadable` (a pending Source whose file is gone, or which resolves outside `TORTOISE_INGEST_BASE_DIR` — widen the base, restore the file, or delete the Source) and `not_document` (a pending Source whose content has no `##` sections — add sections, or delete the Source; the bucket is decided BEFORE `begin_ingest`, so `--force` cannot help). Both are named in the command's own `still pending:` line. ⛔ **A third arm answers `DONE` while destroying the retry signal**: `--upgrade` on a Source the graph **does** hold as a `:Document` whose *content* has no `##` sections (`tortoise/ingest.py::_do_upgrade`, the `is_doc` false branch) prints `warning: … is not a Document (no ## headers) — extraction skipped (no claim made)` and then runs the completion transition **anyway** (`SET s.needs_extraction = false REMOVE s.doc_status`), returning `DONE` → exit **0**, because `failed_sections` stays empty and the `PARTIAL` arm cannot fire. Once cleared, that Source is invisible to every future sweep — `_discover_pending_documents` answers `false` at `WHEN s.needs_extraction IS NOT NULL THEN false`, ahead of the `ELSE $unmarked` arm, so neither `--upgrade-all` nor `--include-unstamped` selects it again — and ⛔ **this row's own remediation for the `not_document` bucket ("add sections") no longer reaches it through a SWEEP**: adding the sections back changes nothing for `--upgrade` or `--upgrade-all`, because discovery no longer selects the node. It is not a dead end, though, and the repair is an ordinary one: a plain `tortoise-ingest <file>` re-extracts it — `_document_is_pending` is consulted only on the `--upgrade` path, full ingest gates on `is_doc` alone, and this arm mints no `begin_ingest` claim, so no idempotency skip blocks the re-ingest — as does deleting and re-creating the Source. The three arms answer the same condition three ways — `--capture-metadata` → `REFUSED_NOT_A_DOCUMENT` (exit 1), `--upgrade-all` → `PARTIAL` (exit 1) **and it keeps the marker**, `--upgrade` → `DONE` (exit 0) **and it clears it** — and the split is deliberate: clearing is the pinned pre-D10 `--upgrade` parity decision (`tests/test_5140_pending_extraction.py::test_upgrade_makes_no_claim_for_a_document_with_nothing_to_extract`), so changing the arm would be a `#8447` reopen, not a fix. That, `--max-utterances` below, the S7 entity-stage partial above, and this `--upgrade` arm, are live **SILENT**/**stuck** bounds on a door this row declares MARKS, named here rather than fixed: it is `DONE`-shaped output over a truncated result, the same class as the list-shaped surfaces in §2a. |
| `GET /v1/events` (hosted, `tortoise/hosted_api.py::events_poll`) | `limit=100` (`le=_MAX_EVENT_PAGE`) | **REFUSES at the edge** | `limit: int = Query(100, ge=1, le=_MAX_EVENT_PAGE)` in `events_poll` refuses 0 at the route; the seam below it (`tortoise/event_store.py::read_after`) refuses both ends — see §3 |

⚠️ **`get_cross_lens_candidates` MARKS one of its three bounds.** The marker is
`truncated = len(all_candidates) > limit` (`tortoise/sdk.py::get_cross_lens_candidates`), where `limit`
derives from `max_candidates` inside that same method. Two further bounds on that same surface do not
reach it at all, and ⚠️ neither of those cuts is inside `get_cross_lens_candidates` — the per-point
ANN pull is `_cross_lens_pairs` and the pool cut is `_cross_lens_pool`, so both are cited by symbol
below rather than described as "same method": `top_k`, clamped to `CROSS_LENS_ANN_TOP_K_MAX`
(`tortoise/sdk.py::CROSS_LENS_ANN_TOP_K_MAX`), bounds the per-point ANN pull (`limit=top_k + 1`,
`tortoise/sdk.py::_cross_lens_pairs`), and `CROSS_LENS_POOL_CAP = 1000`
(`tortoise/sdk.py::CROSS_LENS_POOL_CAP`) cuts the eligible pool (`tortoise/sdk.py::_cross_lens_pool`).
A saturated ANN window
over a point with more above-threshold neighbours than `top_k` is precisely the *"a downstream leg
lost rows"* case §4 item 3 requires to flip the marker — and it stays `false`. Read
`truncated: false` on this surface as *"the candidate cut did not fire"*, never as *"nothing was
lost"*: the two SILENT bounds are named here rather than folded into the marker because folding them
in is a code change (the return is already a `dict`, so it is additive and needs no surface approval —
`issue_insight`'s `_with_truncation` helper performs exactly that additive fold
(`tortoise/sdk.py::issue_insight`, `tortoise/sdk.py::_with_truncation`), and
`tortoise/sdk.py::tortoise_fts_query` folds a *saturated window* into its marker —
`_trunc["incomplete"] = bool(_leg_peak >= str_limit or _lost_legs)`, which is the precedent for the
ANN-window case specifically), and this document is the census, not the fix.

⚠️ **`tortoise_query`'s structural arm (`text=None`) is a different surface, and not ONE surface.**
With `offset`/`page` given it returns a `hasMore`/`total` dict — the `tag` arm and the default arm
both inside `tortoise/mcp_server.py::tortoise_query`, the latter via
`tortoise/sdk.py::paginated_query`. Unpaginated it returns a bare list on the success path — `tag`, and
the default arm, which **since #10602 reaches that SAME `paginated_query` accessor with
`skip=0, limit=eff_limit` (100)** — except when that list is empty, a `kind` was given, **and**
`tortoise/query_suggestions.py::compute_suggestion` has a hint to offer, where the same handler
returns `{"results", "suggestion"}`, a dict carrying no `hasMore`/`total`. Boundary refusals
(`page < 1`, `offset < 0`, `limit < 1`) and an SDK failure surfacing from `_safe` return a structured
error dict instead. **This block is not an inventory**: none of these branches has a row in the census
above, and per the absence rule below they read as named-but-not-yet-censused, never as compliant — a
complete enumeration is a finding for a census row, not a parenthetical here.

⛔ **#10602 measured that SILENT arm, and it was not merely incomplete — it was the outage.** The
default arm had no LIMIT at all (`tortoise/sdk.py::query` is `MATCH (n:Point) WHERE … RETURN
properties(n)`), so `tortoise_query(kind="statement", limit=3)` returned the whole label: **15,708
rows, 15,417 ms, no data**, and a deterministic `-32009` "wait budget exceeded / retry_after: 10"
against a 10 s `mcp_auth._TRANSPORT_WAIT_BOUND_S`. Four concurrent readers of it drove `db.latency_ms`
to **6,291 ms** and left health unanswered for 9 s. The arm is now bounded at `eff_limit`; **the
truncation is still SILENT on the wire** (a bare list carries no `hasMore`/`total`), so this row
remains an OPEN census finding. What changed is only that the bound is now disclosed in the SERVED
tool description (`tortoise/tool_registry.py` — the text a model reads, not the handler docstring),
and that a paged read is advertised as the way to walk the population. Making the truncation visible
on the wire — returning the `hasMore`/`total` dict when `total > len(results)` — is a return-shape
change and is deliberately NOT done here.

⚠️ **The `tag=` arm is a third surface on the same tool, and it is the one the bound never reached.**
The structural READ is bounded at the accessor since **#10602**; on `tag=` only the RESPONSE is
bounded, in the handler, because `tortoise/sdk.py::query_points_by_tag` has no `skip`/`limit` to push
the bound into.
The read volume that produced the outage is therefore unchanged on that arm: a large tagged population
still costs the whole read and can still blow `mcp_auth._TRANSPORT_WAIT_BOUND_S` and return `-32009`,
while the served description now says the *response* is bounded (which is true, and is why the served
text names the distinction rather than calling the arm cheap). Its census row is in the table above;
the accessor bound, and the total order a lossless walk needs, are #11062 — both are one approval and
one signature away, and neither is a rider on #10602.

⚠️ **The census is a population, and this table is the part of it that has been measured** — it is
NOT yet exhaustive. §1 also names *body/log caps* and *ingest chunking*, and no row here covers
either. Read the absence of a surface from this table as **UNMEASURED**, never as compliant: the
failure mode this document exists to prevent is exactly "we audited the instance, not the class".

### 2a. Why the remaining SILENT surfaces are parked, not fixed

All six return a **bare list** *on the arm or the route the census measured* — the qualifier is
load-bearing for one of the six: `tortoise_query` is censused here on its **`text=` arm**, which returns
`tortoise_fts_query`'s bare list, while its *structural* arm (`text=None`) is a different surface that
does not always return a list (see the ⚠️ above), so the parked set is the arm, not the whole tool. Each
sits behind a published surface, so there is no non-breaking place to put an incompleteness marker:

- adding a key to *each row* is not a marker (a caller counting rows cannot see it);
- returning a wrapper object (`{"items": …, "truncated": …}`) changes the declared return type, and
  therefore **changes what every agent reading that MCP tool — or that HTTP route — sees**.

For the two SDK-implemented ones, that second option is an MCP/SDK **surface** change, and the
repo's hard rule requires Daniel's approval before any surface change (`AGENTS.md` → *MCP/SDK
Surface Approval*). `tortoise_search` is the third of the same class — an MCP tool
(`tortoise/tool_registry.py::TOOL_REGISTRY`), not a route — and the manifest's own note calls its
list→object change *"an MCP Surface Approval decision"* (`config/surface-manifest.yml` →
`response_fields[tortoise_search].truncated`). `tortoise_query` is the fourth: an MCP tool
(`tortoise/tool_registry.py::TOOL_REGISTRY`) whose `text=` arm returns `tortoise_fts_query`'s
bare list. So the four are recorded here as:

> **NEEDS-APPROVAL(`tortoise_list_batches`, `tortoise_suggest_entry_points`, `tortoise_search`,
> `tortoise_query`)** —
> the fix for each is a return-shape change from a bare list to a marked result; the contract
> cannot be honoured on a bare `list[dict]` without one.

`selfhost GET /search` and selfhost `GET /points` are published **HTTP response models**
(`response_model=list[PointResponse]`) rather than SDK methods or MCP tools, so they are recorded as
**SILENT** with the same return-shape dependency. The deferral record's own sentence names **two**
surfaces: `tortoise_search` and selfhost `GET /search` — **#8447's 2026-11-01 comment** §6, verbatim:
*"Deliberate limit: `tortoise_search` (MCP) and `selfhost_api`'s `/search` are list-shaped, so they
still cannot say it"*, with §1 rows #13/#14 as the same pair and `config/surface-manifest.yml` →
`response_fields[tortoise_search].truncated` restating the MCP half. It is **not** a scope note on
any PR body.

⚠️ **§1 of that same comment is not the same pair, and this is the trap the deferral sentence hides.**
§1 row #13 is selfhost `/search` (`tortoise/selfhost_api.py::search`), but row #14 carries **two**
surfaces — `tortoise_search` (`tortoise/mcp_server.py::tortoise_search`) *and*
`tortoise_query(text=…)` (`tortoise/mcp_server.py::tortoise_query`), the second of which §6's
sentence does not name. §1's own bullet list parks the whole `#13/#14` list-shaped
set (*"a field cannot ride a list, and wrapping one is a declared return-type change = an **MCP/SDK
surface decision (owner-only)**"*), so `tortoise_query` sits under the same class rule; it is censused
here as **SILENT** rather than left off the table on the strength of a sentence that names only two of
the three.

⚠️ **`selfhost GET /points` is NOT in that record.** It is the same shape and needs the same
decision, but no artifact has decided it yet — so it is recorded here as **SILENT and undecided**,
not as deferred. An undecided surface is not a deferred one; conflating them is how a surface stops
being tracked.

⚠️ **hosted `GET /v1/points` is NOT one of these, and is not parked on an approval.** Its body is a
**dict**, not a list — the `list_points` body build — and the route declares no `response_model`
(`@app.get("/v1/points")` carries none), so a marker rides it **additively**, exactly as it rides
`/v1/search`: build the body, then `response.update(truncation_field(…))`
(`tortoise/hosted_api.py::list_points`). #10500 recorded that shape as a `response_fields` row in
`config/surface-manifest.yml` → `response_fields[tortoise_search].truncated`, **not** as an MCP/SDK
surface approval — and its own
note draws the line there: wrapping a *list* in an object is the return-type change; adding a field
to an object body is not. So this one was **SILENT and fixable without an approval** — recorded here
as **not yet done**, never as blocked — and it is **now done**: the §2 row above reads **MARKS**.
What it never needed was the approval; what it needed was a marker site on a dict body, which is
exactly the `response.update(truncation_field(…))` shape `/v1/search` already uses.

⛔ Do not "fix" any of these by adding a sentinel row, a magic string, or a warning log — a sentinel
is a row the caller asked not to receive, and a log is exactly the *"recorded somewhere the caller
does not read"* that the refusal clause forbids.

---

## 3. `OVERRIDES:` — the deliberate departures from AIP-158

This contract departs from **Google AIP-158 (Pagination, Approved)** on **two** points, and both
departures are deliberate. Recorded on the issue (#8447) **and** beside the constant they govern
(`tortoise/event_store.py`, the `OVERRIDES:` block above `MAX_PAGE`), because both are artifacts a lane reads:

> **OVERRIDES:** AIP-158's `page_size` contract, on TWO counts, one instrument — an out-of-domain
> ask is REFUSED, never silently rewritten into a page the caller did not ask for:
> **(1)** AIP-158's **coerce-down** for a `page_size` above the declared maximum — AIP-158 can
> afford coerce-down because its completeness instrument is the `next_page_token`, which "must be
> empty" exactly at end-of-collection; `read_after` returns a bare `list[dict]` and `poll_events`'
> `next_cursor` is never empty, so a coercion here is **indistinguishable from a drained stream**.
> **(2)** A `page_size` of **0** is refused, although AIP-158 says *"The API must not return an
> error"* for it — serving a 1-row page for an ask of 0 is the same rewritten ask in the other
> direction, and `GET /v1/events` has always refused 0 at its own edge (`Query(ge=1)`).

AIP-158's own text for (1): *"If the user specifies page_size greater than the maximum permitted by
the API, the API should coerce down to the maximum permitted page size."* We do not, because on this
seam coerce-down *is* the silently-truncated result this contract forbids. Where a surface **does**
have a true end-of-collection instrument, coerce-down remains available — that scoping is departure
**(1)**'s, and only (1)'s. Departure **(2)** is instrument-independent: serving a 1-row page for an ask
of 0 is the same rewritten ask in the other direction whatever token the seam carries, which is why
AIP-158's unconditional 0-clause stays departed from here — the route's inherited `Query(ge=1)` is
not itself the recording of that choice.

---

## 4. What "marked" must mean (so the marker is worth reading)

A marker is only an instrument if a caller can act on it. Therefore:

1. **It says how many there are, or that more exist** — `total_available` when the total was
   *proved exact*, `truncated: false` when the result is complete. Either is an instrument; silence
   is not.
2. **It is in the same object as the rows** — not a side channel, not a log line, not a metric.
3. **`truncated` is ALWAYS present, and is `false` at exactly-at-limit** — the implemented rule
   (`tortoise/truncation.py::mark_truncation` writes it on every call **and pops any stale
   `total_available` before conditionally setting it**; `truncation_field` publishes the pair and
   *raises* when the marker is absent rather than defaulting it), pinned by
   `tests/test_8447_truncation_marker.py::TestMarkerVocabulary::test_exactly_at_limit_is_not_truncation` and
   `::TestMarkerVocabulary::test_complete_is_positively_marked_complete`. It flips only when the
   bound actually cut rows or a downstream leg lost rows. ⚠️ A marker that is `true` at
   exactly-at-limit is the *false* incompleteness that withholds an exact total and trains callers
   to ignore the field (`tortoise/sdk.py::issue_insight`) — the opposite failure, and just as
   damaging.
4. **It is applied at the bound, not at the deserialisation** — a marker computed after a second
   truncation silently reports the first cut only.

## 5. Related

- #8447 — the issuing contract (C1 this document, C2 boundary tests, C3 the four doors).
- #5388 — the closed instance: `_iter_registered_orgs` could not distinguish a *complete* 1000-org
  fleet from a PostgREST-truncated page. We fixed the instance, not the class; this is the class.
- PR #10500 — merged: `GET /v1/search` marks its result incomplete (`truncated` + `total_available`
  via `truncation.py`); the list-shaped surfaces named there are a recorded deferral on #8447's
  2026-11-01 comment (§6 names `tortoise_search` and selfhost `GET /search`; §1's bullet list parks
  the wider list-shaped set including row #14's `tortoise_query` — see §2a), not a scope note on that PR.
  ⚠️ selfhost `GET /points` is the same shape and is **undecided**, not deferred.
- PR #10518 — `read_after` refuses an out-of-domain `limit` instead of rewriting it.
- PR #10622 (C2) — hosted `GET /v1/points` now **MARKS**: an internal `limit+1` probe row
  proves the cut, the probe is dropped before delivery, and the body carries `truncated`
  (`total_available` withheld, `exact=False`). The four boundary cases are pinned per surface —
  hosted `/v1/points`, hosted `/v1/events` (cursor), and `paginated_query` (the control) — with the
  `MUTATION-MATRIX(2/2)` — two mutants, **4** tests red in all — recorded on **#8447's
  2026-11-02 mutation-evidence comment**, not in a PR body: the issue is the artifact a lane
  auditing C2 reads (§2's census is written from it), and a PR body is neither durable nor the
  pointer this census can hold. That comment's opening *structural* claim was later corrected by a
  fresh comment on **2026-11-04** (the arithmetic below; the original is left unedited so it stays
  auditable), so a lane reading the 2026-11-02 tables must read the correction beneath them. The
  nodes that evidence names are resolvable in the tree, and are
  pinned so a rename cannot leave the pointer naming something that does not exist:
  `tests/test_hosted_api.py::TestBoundedPointsListBoundaries` (M1 — marker dropped — kills all four;
  M2 — probe removed, the marker kept but LYING — kills 2 of those same four:
  `tests/test_hosted_api.py::TestBoundedPointsListBoundaries::test_limit_plus_one_is_marked_incomplete`
  and
  `tests/test_hosted_api.py::TestBoundedPointsListBoundaries::test_there_is_no_page_two_so_the_marker_is_the_only_instrument`)
  and the measured cursor departure
  `tests/test_hosted_api.py::TestBoundedEventCursorBoundaries::test_cursor_exhaustion_returns_an_empty_page_not_a_completion_signal`
  — asserted by
  `tests/test_8447_truncation_marker.py::TestTheCensusNamesItsEvidenceHome::test_every_test_the_contract_doc_names_exists`.
  Still **SILENT**: the list-shaped surfaces in §2a
  (`tortoise_search`, `tortoise_query(text=…)`, selfhost `GET /search`, selfhost `GET /points`,
  `list_batches`, `suggest_entry_points`) — all six of that section's SILENT surfaces, and every
  one of them needs a return-shape decision, not a marker. `tortoise_query` is the one this
  parenthetical used to drop, which re-opened the gap §2a exists to close.
  ⛔ **M2's kill set is a strict SUBSET of M1's — M2 is subsumed by M1 — so the union is 4, not 6.**
  That nesting is the arithmetic of the two mutants, not an accident: M2 publishes `truncated: False`
  over a cut page, which is the *correct* answer for the two complete-page cases, so those two pass
  under M2 (M1 reds them on a `KeyError`). M2's discriminating power is therefore exactly the two
  `truncated: True` assertions, and the two `truncated: False` assertions are pinned by M1 **alone**.
  A lane trimming this set on the belief that each mutant owns its own tests would drop one of M2's
  two reds and lose the only discrimination against a marker that is present but lying — so the
  nesting is the fact to carry, not a rounding detail.
- `docs/architecture/STORAGE-ARCHITECTURE.md` — the storage seams these bounds sit on.

## 6. Refusal-clause per door (C3)

§1a is enforced door by door, not once:

| door | mechanism | evidence |
|---|---|---|
| **record write** | `RecordWriteOutcome.REFUSED` + `RecordWrite.refusal`; `status` reads `degraded` | `tortoise/record_writer.py`; `_record_health` reads the counters from `/health` |
| **session read** | the door abstains visibly instead of `200` + an empty transcript | PR #10021 |
| **capture** | the 2xx receipt carries `errors` / `warnings` / `diagnostics`; the turn cap REFUSES at the handler (`MAX_SESSION_TURNS`), the per-turn cap MARKS (`_CAPTURE_TRUNCATION_SENTINEL`) | `tortoise/hosted_api.py::_capture_session_impl` (the REFUSE half); `tortoise/sdk.py::_CAPTURE_TRUNCATION_SENTINEL` (the MARK half's sentinel, which `hosted_api.py` never names) |
| **ingest** | `tortoise/ingest.py::IngestOutcome` — the exit status is the marker, and a refusal writes no completion artifact | `tests/test_8447_c3_ingest_door.py`; its live **SILENT**/**stuck** bounds are enumerated in §2, not here — `--max-utterances`, the S7 entity stage, and the `--upgrade` on `##`-less content that exits `DONE` after clearing the retry marker |

⛔ **A door's counters are part of the clause.** §1a forbids a value indistinguishable from success
*while* the failure is recorded somewhere the caller does not read — so a door must not make a counter
its instrument unless the caller can read it back. The record door's counters reach `/health` through
`_record_health`; the ingest door's SWEEP counters (`upgraded` / `skipped` / `unresolved` /
`not_document` / `already_claimed` / `partial_sections`) are read back by that sweep's own summary
lines — they are *not* nothing — while a single door run's instrument is the exit status
(`IngestOutcome`), not a counter.
