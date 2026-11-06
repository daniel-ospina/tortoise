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
**Census measured at:** `origin/main = 62e3f2f61` (2026-11-02).
**Status:** C1 of #8447 (this document). C2 (boundary tests per surface) and C3 (the four doors)
are tracked on the issue.

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

Measured on this tree:

| surface | bound | state | evidence |
|---|---|---|---|
| `TortoiseSDK.get_cross_lens_candidates` (MCP: `tortoise_find_cross_lens_candidates`) | `limit` | **MARKS** | `tortoise/sdk.py:28131-28135` — returns `truncated`, `cap` and `count` alongside the rows (`truncated = len(all_candidates) > limit`) |
| `event_store.read_after` | `limit` | **REFUSES** | `tortoise/event_store.py:974` — `if not 1 <= limit <= MAX_PAGE: raise ValueError(...)`, with the message stating *why* coercion would be indistinguishable from a drained stream |
| `GET /v1/search` (hosted) | `limit` | **MARKS** | `tortoise/hosted_api.py:10681-10690` — `response.update(truncation_field(trunc_out))`, i.e. `truncated` + `total_available` (PR #10500, merged). ⚠️ `count` is still rows **delivered**, not matched — the marker, not `count`, is the instrument |
| MCP `tortoise_search` (`tool_registry.py:474`) · `GET /search` (selfhost, `selfhost_api.py:365`) | `limit` | **SILENT** | both return a bare list (`response_model=list[PointResponse]` on the route); recorded as a **deliberate limit** on #8447's 2026-11-01 comment (§6) and restated in `config/surface-manifest.yml:139-141` (*"returns a bare list, so it does not carry the field; wrapping a list in an object is a declared return-type change"*), because a marker here IS a return-shape change |
| `TortoiseSDK.list_batches` (MCP: `tortoise_list_batches`) | `limit=20` | **SILENT** | `tortoise/sdk.py:26725` — `return items[:limit]`; the method's contract is `-> list[dict]` (`:26676`), so there is no field in which to say "there are more" |
| `TortoiseSDK.suggest_entry_points` (MCP: `tortoise_suggest_entry_points`) | `limit=5` | **SILENT** | `tortoise/sdk.py:33753` — `results = results[:limit]`, returning `results` at `:33801`; declared `-> list[dict]` (`:33699`) |
| `GET /v1/points` (hosted, `hosted_api.py:10142`) | `limit=50` (`Query(50, ge=1, le=1000)`) | **SILENT** | `return {"points": results, "count": len(results)}` at `hosted_api.py:10201` — `count` is rows delivered; no marker |
| `GET /points` (selfhost, `selfhost_api.py:315`) | `limit=50` | **SILENT** | `response_model=list[PointResponse]` — a bare list on the route |
| `GET /v1/events` (hosted, `hosted_api.py:10094-10098`) | `limit=100` (`le=_MAX_EVENT_PAGE`) | **REFUSES at the edge** | `limit: int = Query(100, ge=1, le=_MAX_EVENT_PAGE)` (`:10098`) refuses 0 at the route; the seam below it (`read_after`) refuses both ends — see §3 |

⚠️ **The census is a population, and this table is the part of it that has been measured** — it is
NOT yet exhaustive. §1 also names *body/log caps* and *ingest chunking*, and no row here covers
either. Read the absence of a surface from this table as **UNMEASURED**, never as compliant: the
failure mode this document exists to prevent is exactly "we audited the instance, not the class".

### 2a. Why the remaining SILENT surfaces are parked, not fixed

Five of the six return a **bare list**, and each sits behind a published surface, so there is no
non-breaking place to put an incompleteness marker:

- adding a key to *each row* is not a marker (a caller counting rows cannot see it);
- returning a wrapper object (`{"items": …, "truncated": …}`) changes the declared return type, and
  therefore **changes what every agent reading that MCP tool — or that HTTP route — sees**.

For the two SDK-implemented ones, that second option is an MCP/SDK **surface** change, and the
repo's hard rule requires Daniel's approval before any surface change (`AGENTS.md` → *MCP/SDK
Surface Approval*). `tortoise_search` is the third of the same class — an MCP tool
(`tortoise/tool_registry.py:474`), not a route — and the manifest's own note calls its list→object
change *"an MCP Surface Approval decision"* (`config/surface-manifest.yml:140-141`). So the three
are recorded here as:

> **NEEDS-APPROVAL(`tortoise_list_batches`, `tortoise_suggest_entry_points`, `tortoise_search`)** —
> the fix for each is a return-shape change from a bare list to a marked result; the contract
> cannot be honoured on a bare `list[dict]` without one.

`selfhost GET /search` and selfhost `GET /points` are published **HTTP response models**
(`response_model=list[PointResponse]`) rather than SDK methods or MCP tools, so they are recorded as
**SILENT** with the same return-shape dependency. The deferral record names **exactly two**
surfaces: `tortoise_search` and selfhost `GET /search` — **#8447's 2026-11-01 comment** §6, verbatim:
*"Deliberate limit: `tortoise_search` (MCP) and `selfhost_api`'s `/search` are list-shaped, so they
still cannot say it"*, with §1 rows #13/#14 as the same pair and `config/surface-manifest.yml:139-141`
restating the MCP half. It is **not** a scope note on any PR body.

⚠️ **`selfhost GET /points` is NOT in that record.** It is the same shape and needs the same
decision, but no artifact has decided it yet — so it is recorded here as **SILENT and undecided**,
not as deferred. An undecided surface is not a deferred one; conflating them is how a surface stops
being tracked.

⚠️ **hosted `GET /v1/points` is NOT one of these, and is not parked on an approval.** Its body is a
**dict**, not a list — `return {"points": results, "count": len(results)}` (`hosted_api.py:10201`) —
and the route declares no `response_model` (`:10141-10142`), so a marker rides it **additively**,
exactly as it rides `/v1/search`: build the body, then `response.update(truncation_field(…))`
(`:10681`, `:10690`). #10500 recorded that shape as a `response_fields` row in
`config/surface-manifest.yml` (`:121-143`), **not** as an MCP/SDK surface approval — and its own
note draws the line there: wrapping a *list* in an object is the return-type change; adding a field
to an object body is not. So this one is **SILENT and fixable without an approval**: recorded here
as **not yet done**, never as blocked.

⛔ Do not "fix" any of these by adding a sentinel row, a magic string, or a warning log — a sentinel
is a row the caller asked not to receive, and a log is exactly the *"recorded somewhere the caller
does not read"* that the refusal clause forbids.

---

## 3. `OVERRIDES:` — the deliberate departures from AIP-158

This contract departs from **Google AIP-158 (Pagination, Approved)** on **two** points, and both
departures are deliberate. Recorded on the issue (#8447) **and** beside the constant they govern
(`tortoise/event_store.py:893-919`), because both are artifacts a lane reads:

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
have a true end-of-collection instrument, coerce-down remains available — departures (1) and (2) are
scoped to a seam that has none.

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
   to ignore the field (`sdk.py:33996-33999`) — the opposite failure, and just as damaging.
4. **It is applied at the bound, not at the deserialisation** — a marker computed after a second
   truncation silently reports the first cut only.

## 5. Related

- #8447 — the issuing contract (C1 this document, C2 boundary tests, C3 the four doors).
- #5388 — the closed instance: `_iter_registered_orgs` could not distinguish a *complete* 1000-org
  fleet from a PostgREST-truncated page. We fixed the instance, not the class; this is the class.
- PR #10500 — merged: `GET /v1/search` marks its result incomplete (`truncated` + `total_available`
  via `truncation.py`); the list-shaped surfaces named there are a recorded deferral on #8447's
  2026-11-01 comment (§1 census rows #13/#14 — `tortoise_search` and selfhost `GET /search` only),
  not a scope note on that PR. ⚠️ selfhost `GET /points` is the same shape and is **undecided**, not
  deferred.
- PR #10518 — `read_after` refuses an out-of-domain `limit` instead of rewriting it.
- `docs/architecture/STORAGE-ARCHITECTURE.md` — the storage seams these bounds sit on.
