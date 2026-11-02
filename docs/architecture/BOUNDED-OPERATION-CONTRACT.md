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

## 2. The census — every bounded surface, classified by whether the caller can TELL

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
| MCP `tortoise_search` (`tool_registry.py:474`) · `GET /search` (selfhost, `selfhost_api.py:365`) | `limit` | **SILENT** | both return a bare list (`response_model=list[PointResponse]` on the route); named by PR #10500's own body as **deliberately left** list-shaped, because a marker here is a return-shape change |
| `TortoiseSDK.list_batches` (MCP: `tortoise_list_batches`) | `limit=20` | **SILENT** | `tortoise/sdk.py:26725` — `return items[:limit]`; the method's contract is `-> list[dict]` (`:26676`), so there is no field in which to say "there are more" |
| `TortoiseSDK.suggest_entry_points` (MCP: `tortoise_suggest_entry_points`) | `limit=5` | **SILENT** | `tortoise/sdk.py:33753` — `results = results[:limit]`, returning `results` at `:33801`; declared `-> list[dict]` (`:33699`) |

The **SILENT** rows are the same defect as the ones PR #10500 and PR #10518 fixed, and they are the
reason this document exists rather than a follow-up issue per surface.

### 2a. Why the remaining SILENT surfaces are parked, not fixed

Every one of them returns a **bare list**, and each sits behind a published surface, so there is no
non-breaking place to put an incompleteness marker:

- adding a key to *each row* is not a marker (a caller counting rows cannot see it);
- returning a wrapper object (`{"items": …, "truncated": …}`) changes the declared return type, and
  therefore **changes what every agent reading that MCP tool — or that HTTP route — sees**.

For the two SDK-implemented ones, that second option is an MCP/SDK **surface** change, and the
repo's hard rule requires Daniel's approval before any surface change (`AGENTS.md` → *MCP/SDK
Surface Approval*). So they are recorded here as:

> **NEEDS-APPROVAL(`tortoise_list_batches`, `tortoise_suggest_entry_points`)** — the fix for each is
> a return-shape change from a bare list to a marked result; the contract cannot be honoured on a
> bare `list[dict]` without one.

`tortoise_search` and the selfhost `GET /search` are the same shape and need the same decision, but
they are a published **HTTP response model** rather than an SDK method, so they are recorded as
**SILENT (deliberately left by #10500)** with the same return-shape dependency.

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

1. **It says how many there are, or that more exist** — `truncated: true` alone is weaker than
   `total_available`; either is acceptable, silence is not.
2. **It is in the same object as the rows** — not a side channel, not a log line, not a metric.
3. **It is present at exactly-at-limit and absent when complete** — a marker that is always `true`
   carries no information, and one that is always `false` is worse than none.
4. **It is applied at the bound, not at the deserialisation** — a marker computed after a second
   truncation silently reports the first cut only.

## 5. Related

- #8447 — the issuing contract (C1 this document, C2 boundary tests, C3 the four doors).
- #5388 — the closed instance: `_iter_registered_orgs` could not distinguish a *complete* 1000-org
  fleet from a PostgREST-truncated page. We fixed the instance, not the class; this is the class.
- PR #10500 — merged: `GET /v1/search` marks its result incomplete (`truncated` + `total_available`
  via `truncation.py`); the list-shaped search surfaces stay silent by that PR's own scope note.
- PR #10518 — `read_after` refuses an out-of-domain `limit` instead of rewriting it.
- `docs/architecture/STORAGE-ARCHITECTURE.md` — the storage seams these bounds sit on.
