# Plan — #4675: a post-commit capture must terminalise, and the verdict must match the server

**Issue:** daniel-ospina/tortoise#4675 (impact: blocks the four-harness capture exit criterion at the
`captured` link, and blocks daniel-ospina/tortoise#4714's T1 for codex)
**Branch:** `fix/4675-post-commit-timeout-terminalises`
**Tier:** standard

---

## 1. Confirmed problem

The issue frames one defect. Scoping found **two**, which produce the same user-visible outcome
(*"a capture that landed is reported as unfiled / failed"*) and cannot be fixed independently.

### D1 — CLIENT: a retryable POST failure is deferred without ever asking whether it committed

`tortoise/__main__.py::_cmd_sessions_import` (4149-4168) and `capture_spool.py::_flush_one`
(1010-1029) both treat *every* retryable status as "not committed" and defer. The deployed server
**abandons, never cancels**, on its 10 s transport bound (`hosted_api.py:2518-2533`; the 504 is
emitted by middleware at 2581-2596), so the handler keeps running after the client is told it
failed — a 504 routinely arrives **after** the commit.

Measured today against `https://api.premiselabs.co`:

| session | `GET /v1/sessions/<id>` | turns | extracted | `turn_points` ids | receipt |
|---|---|---|---|---|---|
| `h4-cursor-seam-20260923T082102Z` | **200** | 3 | 4 | `…_t0`, `…_t1`, `…_t2` | `session_capture_receipt_cursor = 2026-09-23T08:21:06Z` |

…while the client recorded that same capture as a failure and parked it unfiled:

```
~/.tortoise/capture-errors/cursor.json   recorded_at 2026-09-23T08:21:16Z
  "capture failed: import failed (HTTP 504): … Spooled session: h4-cursor-seam-20260923T082102Z"
```

The seam fired 08:21:02; the server wrote the receipt at 08:21:06. The session is durable, its turns
are durable, its memory was extracted — and the client's copy of it was then **discarded** by the
drain (`~/.tortoise/capture-spool/discarded.jsonl`: `permanent_http_402`, 08:22:24Z, pre-#4888).
So the client's verdict and the server's state disagreed, and the client destroyed its only copy of
a capture that had in fact succeeded.

Consequence for the spool: `session drain` can report `filed 0, deferred N` indefinitely against a
session that is already durable, and every such entry keeps a full conversation on disk forever.

### D2 — VERIFIER: `captured` asserts a per-session fact using a per-harness scalar

`session_verify.py:724-770` proves the `captured` link from `session_capture_receipt_<harness>` —
a **per-harness** scalar written by the server at `hosted_api.py:10192-10197`, i.e. **after all
extraction**, and only `if _session_alive()`.

verify (a) polls for **session existence**, (b) reads the receipt **once**, and (c) in its `finally`
**deletes its own probe session**. So for any capture whose extraction outlasts the existence poll,
the receipt cannot have been written when it is read, and after the delete it can never be written
(it is gated on `_session_alive()`). The link then FAILs **by construction**, for a probe the
verifier itself deleted.

Measured: `session_capture_receipt_codex` is `None` right now, and the codex probe session was
observed to exist at verify time (the issue's own trace: *"session … exists but
`session_capture_receipt_codex` did not advance"*). The `captured` link is therefore not a
measurement of codex's capture at all.

**This falsifies the issue's stated causal chain** — `session_verify` never reads the spool's
`filed_key` (`grep -n filed_key tortoise/session_verify.py` → 0 hits), so D1 is *not* what makes
verify report FAIL. Both defects are real; they are simply not the same defect.

### What was rejected and why

- **Server-side receipt at the durable-commit point** (write it before extraction) — contradicts three
  recorded decisions: `docs/plans/2026-08-25-1714-memory-capture-onboarding.md:211,218` (*"set only
  on 2xx"*, *"receipt 2xx-only"*), `docs/plans/2026-09-02-2002-W6-onboarding-plan.md:13` (receipt
  written after all durable writes, receipt↔Session invariant T1-P12), and the restatement at
  `hosted_api.py:10163-10166`. Not adopted; would need those decisions reopened.
- **Exempt `POST /v1/sessions` from the transport bound** — contradicts the recorded `OVERRIDES:`
  ruling on #3834 (`hosted_api.py:2207-2222`, *"do not 'fix' it by making the bounds uniform"*).
  Route: reopen #3834, not a quiet adoption.
- **Treat every 504 as filed** — explicitly warned against in the issue; it converts the false
  negative into a false positive. Rejected.
- **Server 202/ack on the timeout path** — a wire-protocol widening of the same bound ruling, plus
  a REST/MCP parity obligation and a possible SDK/MCP surface change (owner approval required).
  Highest cost, lowest marginal value over D1's read, since the read is already available and the
  turn ids are deterministic.

---

## 2. Chosen approach

### D1 — terminalise on a **proven** commit (not on the status code)

Add one helper that answers *"are the turns I posted durable?"*, and consult it on the retryable
path only. The proof is the deterministic turn-id set, which the server writes **before** extraction
(`hosted_api.py:9623-9629`, one batched `UNWIND` through the shared `_write_capture_turns`), so it is
unaffected by the extraction the bound abandons:

```
GET /v1/sessions/<sid>  →  200 with turn_points ids {sid}_t0 … {sid}_t{n-1}
```

where `n` is the number of turns actually posted. Confirmed durable ⇒ the entry is filed
(`filed_key` stamped by `_flush_one`'s existing compare-and-swap) and, on the import path, the local
receipt is written and the breadcrumb cleared. **Not** confirmed ⇒ behave exactly as today (defer).

Failure modes the helper must treat as **UNKNOWN ⇒ defer** (never as filed, never as permanent):
the confirming GET 504/429/5xx; a 404 (the abandoned handler may not have MERGEd yet); a
transport error; an unparseable body; more server rows than we posted (a concurrent grown
transcript — the CAS in `_flush_one` owns that case).

Placement: a single closure in `tortoise/__main__.py` next to `_session_post` (3423), because that is
the only place holding `api_key`/`api_url`, and it is reached by **both** the drain and
`session capture`. `capture_spool.py` gains no credentials and no new API — `_flush_one` already
stamps `filed_key` on any `ok` outcome.

### D2 — `captured` measures the session, not the harness

`session_verify`'s `captured` link becomes PROVEN on the per-session facts it already retrieves —
the session exists **and** its `turn_points` cover the probe's expected turns — and reports the
receipt as **corroboration**, surfaced in the link's detail (and as a warning when it did not
advance) instead of as the pass condition. The `memory` link (Source node + `extracted >= 1`) is
unchanged, so a capture that stored turns but never extracted is still caught by the chain.

This is a *correction of the predicate to what the link means*, not a relaxation of the gate: today
the gate requires evidence the verifier makes impossible to produce.

---

## 3. Implementation steps

1. `tortoise/__main__.py`: add `_session_confirm_committed(api_url, api_key, session_id, expected_turns) -> bool`
   (bounded: a small number of GET attempts, short sleeps, total ≪ the caller's own budget).
   Turn ids compared as a set against `{sid}_t{i}` for `i in range(expected_turns)`.
2. `tortoise/__main__.py::_session_post.handle`: on a retryable `PostOutcome`, call the helper; on
   confirmation return `PostOutcome(ok=True, status=200, detail=…)` carrying the confirmation.
3. `tortoise/__main__.py::_cmd_sessions_import`: in the `HTTPError`, `URLError` and response-phase
   branches, call the helper before spooling; on confirmation write the local receipt (2xx-equivalent
   path), clear the breadcrumb, print a truthful "already filed" line and return 0.
4. `tortoise/session_verify.py`: restructure the `captured` link predicate as in D2, keeping
   `receipt_before`/`receipt_after` in the link payload and adding `receipt_advanced`; the
   non-advance case becomes a warning carried on a PROVEN link, not a FAIL.
5. Tests: `tests/test_capture_spool.py`, `tests/test_session_import_codex.py`,
   `tests/test_session_verify.py` — each new guard mutation-verified RED.

## 4. Acceptance criteria

- [ ] A retryable POST whose session is provably committed terminalises: the spool entry gains
      `filed_key`/`filed_at` and the drain reports it filed, not deferred.
- [ ] A retryable POST whose session is **not** committed still defers (unchanged), and a genuine
      permanent failure is still discarded.
- [ ] A confirming-read failure of any kind (404, 504, 429, transport, unparseable) defers — it never
      files and never discards.
- [ ] `session verify`'s `captured` link is PROVEN when the probe session exists with its expected
      turns, even when the per-harness receipt never advanced; the non-advance is still reported.
- [ ] `session verify` still FAILs `captured` when the session is absent, or short of its expected
      turns.
- [ ] `memory` is unchanged: `extracted >= 1` and the Source node still gate it.
- [ ] Existing suites green; `ruff` clean.

## 5. Out of scope (filed, not absorbed)

- codex/cursor have **no automatic drain** (`claude-hooks/session-start.sh` is the only one), so a
  spooled codex/cursor session is only retried when a user runs `tortoise session drain`.
- The `verify-<harness>-…` probe spool entry is held back by the drain's probe guard forever and is
  removed only by verify's own cleanup.
- The 10 s transport bound itself (#3834, #4580) and the re-extraction cost of a `capture_ok=false`
  session (#3556).
- The codex/cursor seams not passing `session_id` in their breadcrumb (#4799).
