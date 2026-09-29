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
**abandons, never cancels**, on its 10 s transport bound (`hosted_api.py:2515-2517` —
`asyncio.wait_for(asyncio.shield(task), …)`; the 504 is
emitted by middleware at 2581-2596), so the handler keeps running after the client is told it
failed — a 504 routinely arrives **after** the commit.

Measured today against `https://api.premiselabs.co`:

| session | `GET /v1/sessions/<id>` | turns | extracted | `turn_points` ids | receipt |
|---|---|---|---|---|---|
| `h4-cursor-seam-20260923T082102Z` | **200** | 3 | 4 | `…_t0`, `…_t1`, `…_t2` | `session_capture_receipt_cursor = 2026-09-23T08:21:06Z` |

**The served turn shape, from that same live response** (this is what the confirmation must compare
against):

```
turn_points[0] = {"id": "h4-cursor-seam-20260923T082102Z_t0", "role": "user",
                  "content": "<timestamp>Friday, Sep 18, 2026 …"}
```

The role is served as its own field and `content` has the writer's `"[user] "` prefix **removed**
(`get_session_detail`). The writer's stored string is `"[user] <content>"`, so comparing the two
directly never matches — the first revision of this PR did exactly that and was inert against the
live API (the fakes echoed the writer, so the unit tests stayed green). Verified against the live
response: `(role, content)` → `_capture_turn_texts` → `_capture_turn_role_text` round-trips exactly
for all three served rows.

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
path only. The proof is the deterministic turn-id→``(role, body)`` map, which the server writes
**before** extraction (`hosted_api.py:9623-9629`, one batched `UNWIND` through the shared
`_write_capture_turns`), so it is unaffected by the extraction the bound abandons:

```
GET /v1/sessions/<sid>  →  200 with turn_points [{id: {sid}_t{i}, role: R, content: <body>}]
```

where `n` is the number of turns actually posted. **The served `content` is NOT the stored text:**
the writer stores `f"[{role}] {content[:5000]}"` (`tortoise.sdk._capture_turn_texts`) and
`get_session_detail` serves that string SPLIT — `role` as its own field, `content` with the
`[role] ` prefix stripped. So the comparison is on the served `(role, body)` pair, and both sides go
through the one shared inverse `tortoise.sdk._capture_turn_role_text` (used by the reader and the
client) rather than a mirrored regex. Comparing the writer's raw string against a served row never
matches: a confirmation built that way is **inert in production** while any test whose fake echoes
the writer stays green — measured on the live API (see §2's evidence). Confirmed durable ⇒ the entry is filed
(`filed_key` stamped by `_flush_one`'s existing compare-and-swap) and, on the import path, the local
receipt is written and the breadcrumb cleared. **Not** confirmed ⇒ behave exactly as today (defer).

Failure modes the helper must treat as **UNKNOWN ⇒ defer** (never as filed, never as permanent):
the confirming GET 504/429/5xx; a 404 (the abandoned handler may not have MERGEd yet); a
transport error; an unparseable body; more server rows than we posted (a concurrent grown
transcript — the CAS in `_flush_one` owns that case).

Placement: `tortoise/session_confirm.py`, imported by `tortoise/__main__.py` (which holds
`api_key`/`api_url`, and is reached by **both** the drain and `session capture`) and by
`tortoise/session_verify.py`. A module rather than a closure because the Pi/TypeScript leg needs the
same mechanism and must not re-implement the comparison — the two classifiers already diverged once
(#4895); the residual is filed as #4924. `capture_spool.py` gains no credentials and no new API —
`_flush_one` already stamps `filed_key` on any `ok` outcome.

### D2 — `captured` reads its evidence BEFORE the evidence exists

`session_verify`'s `captured` predicate is **unchanged** — it is a recorded decision (#3809
Scope §2: *"a `session_capture_receipt_<harness>` advanced AND the session is retrievable by id
with the expected turns"*, restated in PR #4182's guard table). The defect is the **observation
window**, not the predicate: the receipt is written by the same abandoned handler (D1), so it
lands *after* the session row is visible — and on the `session verify` path the read happened once,
before the loop, which is why `session_capture_receipt_codex` was `None` while the codex probe
session sat in the graph.

The fix polls **both** legs to the same deadline. An early break remains for the case that can
never converge — a settled turn count that is *not* the expected one — but the expected count must
keep the window open, because the receipt is still to come. `receipt_advanced` is reported in the
link payload as before, so the evidence is not lost.

---

## 3. Implementation steps

1. `tortoise/session_confirm.py` (new): `confirm_capture(reader, api_url, api_key, session_id,
   turns, *, attempts, delay_s, read_timeout_s, sleep)` returning `FILED` / `UNEXTRACTED` /
   `UNKNOWN` / `TURNS_MISSING`. Bounded: `DEFAULT_ATTEMPTS=2`, `DEFAULT_DELAY_S=1.0`,
   `DEFAULT_READ_TIMEOUT_S=5.0` — the POST may already have spent the transport bound's 30 s and
the Claude `SessionEnd` hook cancels at 60 s, so the confirmation is a *suffix*, not a second
   budget. The comparison is `{turn_id: (role, body)}` — the writer's own string SPLIT by the
   reader's own inverse (`tortoise.sdk._capture_turn_role_text`, shared with
   `get_session_detail`) against the served `role`/`content` fields — because the turn ids are
   positional (`f"{session_id}_t{i}"`), so an id-set match is satisfied by an earlier capture of
the same session id with a different transcript (compaction, a branch, the
`MAX_SESSION_TURNS` window shift at 500), and because a raw-string comparison never matches a
served row at all (§2's D1).
2. `tortoise/__main__.py::_session_post.handle`: on a retryable `PostOutcome`, call the helper; on
   `FILED`/`UNEXTRACTED` return `PostOutcome(ok=True, status=200, …)` carrying `confirmed` and, for
   the unextracted case, `extraction_mode = _CAPTURE_NO_PROVIDER_MODE` so the client's existing
   #4188 disclosure fires (no unqualified "Captured session").
3. `tortoise/__main__.py::_confirm_already_captured`: gated on
   `status == 503 or classify_failure(status, detail) != "retry"` — the recorded rule is a
   **2xx-only** local receipt (403/402/503 ⇒ exit 1, honest error, no receipt) and this must not mint
   a receipt for a refusal the plan is refused on. 403/402 fall out of the classifier (`permanent`);
   **503 does not** (`status >= 500 → "retry"`), so it is excluded by name — see #4925 for the
   standing question. Cost of the exclusion: a 503 whose commit landed defers, never loses (503 is
   `retry`, so `_spool_if_retryable` keeps the entry). The drain's `_refused` carries the same
   exclusion so the two paths cannot drift. Writes the receipt, clears the breadcrumb via
   `capture_spool._clear_breadcrumb_for(harness, session_id)` (not the harness-wide
   `_clear_capture_error`), prints the truthful "already filed" line.
4. `tortoise/session_verify.py`: poll both legs of the UNCHANGED #3809 predicate to one deadline;
   keep `receipt_before`/`receipt_after`/`receipt_advanced` in the link payload.
5. Tests: `tests/test_session_confirm.py` (new), `tests/test_capture_spool.py`,
   `tests/test_session_import_codex.py`, `tests/test_session_verify.py` — each new guard
   mutation-verified RED; `tests/test_session_confirm.py` registered in `config/ci-surfaces.yml`
   (both halves).

## 4. Acceptance criteria

- [ ] A retryable POST whose session is provably committed terminalises: the spool entry gains
      `filed_key`/`filed_at` and the drain reports it filed, not deferred.
- [ ] A permanent refusal, and a **503**, whose session happens to be durable still mints NO
      receipt (rc=1, honest error) and keeps the session spooled — the recorded 2xx-only rule.
- [ ] A confirmation whose session carries the right ids but different TEXT is NOT a confirmation
      (ids are positional — see §3.1), and one whose served `role` differs is NOT a confirmation
      either.
- [ ] The comparison matches a row in the shape the SERVER returns (role separate, `[role] `
      prefix stripped) — a raw-stored-string comparison would be inert in production.
- [ ] A confirming-read failure of any kind (404, 504, 429, transport, unparseable) defers — it never
      files and never discards.
- [ ] An UNEXTRACTED confirmation is reported as such (`extraction_mode`), never as an unqualified
      success (#4188/#1529).
- [ ] `session verify`'s `captured` link is PROVEN when the receipt advances only AFTER the session
      row is visible (the real ordering), and still FAILs when it never advances.
- [ ] `session verify` still FAILs `captured` when the session is absent, or short of its expected
      turns.
- [ ] `memory` is unchanged: `extracted >= 1` and the Source node still gate it.
- [ ] Existing suites green; `ruff` clean; `ci_selection.py --integrity` clean.

## 5. Out of scope (filed, not absorbed)

- codex/cursor have **no automatic drain** (`claude-hooks/session-start.sh` is the only one), so a
  spooled codex/cursor session is only retried when a user runs `tortoise session drain`.
- The `verify-<harness>-…` probe spool entry is held back by the drain's probe guard forever and is
  removed only by verify's own cleanup.
- The 10 s transport bound itself (#3834, #4580) and the re-extraction cost of a `capture_ok=false`
  session (#3556).
- The codex/cursor seams not passing `session_id` in their breadcrumb (#4799).
