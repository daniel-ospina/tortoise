---
title: "#3027 — OAuth code redemption state: making a failed redemption distinguishable from a replay"
type: engineering
domain: platform
doc_status: live
subjects.team: organisation-design-team
aboutSubjects: oauth-redemption-state, authorization-code
aboutObjects: oauth-codes, oauth-access-tokens, oauth-refresh-tokens, mcp-oauth
created: 2026-09-25
---

# Scoping / design — #3027: `oauth_codes` redemption state

**Issue:** [#3027](https://github.com/daniel-ospina/tortoise/issues/3027) — *`oauth_codes` has no
redemption state — a failed redemption is indistinguishable from a replay* · **Level:** task ·
**Complexity:** complex · **Lane:** `fix/3027-oauth-code-redemption-state`

**Search tool (research PREFLIGHT):** none used. This design is grounded in the repository — the
schema, `tortoise/oauth.py`, `tortoise/supabase_control.py`, the #2863 fault suite, and the issue
threads. No external/SOTA convergence claim is made, so no research rung was needed; the
contradiction test below ran against **recorded decisions**, which is the required first step.

---

## 0. Contradiction test — run FIRST, before any candidate was formed

| Recorded decision | Where it is recorded | What it governs | Contradiction result |
|---|---|---|---|
| **Secrets are stored hashed only** (SHA-256 hexdigest) | `supabase/migrations/0016_oauth.sql` header; `docs/oauth-mcp.md:205-206`; `tortoise/oauth.py` module docstring | What may be persisted for codes/tokens | **HARD STOP for one candidate.** Any design that answers a lost-response retry by **re-serving the same plaintext credential pair** requires storing plaintext — it contradicts this. Not adoptable. The route would be a reopen (evidence in front of the owner), never a quiet exception. This settles the issue's open sub-question *"may a retry ever re-serve the same credential pair?"* → **no**. |
| **OAuth provenance FKs are `ON DELETE SET NULL`**, deliberately — *not* `CASCADE` | #3036 owner-ratified scoping comment, `OVERRIDES:` line: *"`ON DELETE CASCADE` … is deliberately NOT used for the two OAuth FKs: an access token is an independent bearer credential that `CASCADE` would silently revoke, and `rotated_from` is a rotation chain that `CASCADE` would delete through"*; migration `20260925000001_oauth_referential_integrity.sql` | FK action for any new OAuth cross-table link | **ALIGNS.** The new `code_id` link is provenance, exactly the shape #3036 ruled on. It must be `ON DELETE SET NULL`. `CASCADE` here would delete live token rows when a dead code row is reaped by the #3036 retention sweep — the same hazard #3036 removed. |
| **An unobservable write state is never advertised as retryable** | #2863 body; `OAuthTemporarilyUnavailable` docstring (`tortoise/oauth.py`) — *"Never on an unobserved write state"*; `docs/plans/2026-09-11-2863-oauth-code-atomicity.md` | Which failures may return 503 vs terminal `invalid_grant` | **CONSTRAINS, at full width.** The rule's test is whether *this request's own write* may have landed: a claim PATCH that **raised** may have committed → **terminal** `invalid_grant` (`_consume_state` → `'unknown'`). A claim PATCH **observed** to match zero rows wrote nothing → a later failure to *classify* that row is safely **retryable** 503, and `claimed` observed by another request is **terminal** (the retry can terminate). An earlier revision of this lane answered the *observed* `claimed` case 503, narrowing the rule; **rejected on review**. The docstring now names both halves explicitly. See §3.2/§3.4. |
| **D5 — rotating refresh tokens per (user, org), family revoked on org suspension** | `docs/scoping/2026-08-15-524-oauth-mcp-scoping.md` decision table, D5 | Refresh rotation + family semantics | **ALIGNS, with a requirement.** The "did this code mint a family?" link must survive rotation, so `code_id` is **inherited** by each rotated refresh row (`rotation preserves the family's origin`). A link that died at the first rotation would make the reconciler blind to the live descendant. |
| **#3025 transactional mint (one-RPC atomicity) is out of scope**; **#3026 sibling transient boundary is out of scope** | #3027 *Out of scope* section | How atomicity may be achieved | **ALIGNS.** This design uses the existing per-call seam (no new RPC, no new transaction), so it composes with #3025 rather than pre-empting it. |
| **#2863's in-process confirmation is to be replaced by a state transition** | #3027 *Target* checklist, final item | Whether #2863's `_restore_code` may be deleted | **No contradiction — sequencing only.** The Target authorizes the removal; this lane **introduces the durable path alongside** it and does **not** delete the workaround in the same change (an explicit lane constraint). The deletion is a follow-up step once the state machine is proven. |

**Searched:** `~/.pi/agent/state/DECISION-LEDGER.md` (no OAuth entries); `docs/scoping/**` (#524
D1–D6 only); `docs/oauth-mcp.md`; #2863 + #3036 + #3027 threads; the two OAuth migrations.

**Contradiction-test verdict:** *no plaintext storage (0016) · `code_id` FK is `ON DELETE SET NULL`
(#3036) · unknown outcome is never a success (#2863) · `code_id` survives rotation (D5) · keep
#2863's workaround in place for now.*

---

## 1. Confirmed problem

`oauth_codes` carries exactly one consumption signal: `used_at`, a binary null-or-set flag
(`0016_oauth.sql:38-53`). Three facts follow, all verified in the code:

1. **A redemption attempt is not durable.** `_consume_code` (`oauth.py:1229-1285`) writes `used_at`
   as an atomic CAS — that proves *the request still owns its claim*, and nothing more. The
   in-process confirmation #2863 added (`_rollback_minted` soft-revokes, `_mint_observably_clean`
   confirms by SELECT, `_prev_refresh_unclaimed` confirms the rotation claim, and only then
   `_restore_code` re-arms) **does** separate *"the redemption never committed"* (`recovered=True`,
   observed clean) from *"it committed and the compensating write also failed"* (`recovered=False`)
   — for as long as it can read the tables, in that request. What it cannot do is **record** the
   answer: the observation dies with the request, so no later attempt can replay it, and inside the
   read-failure window the confirmation itself is blind. That un-recorded, un-replayable observation
   is the gap — not an inability to tell the two apart while reading. (`docs/plans/2026-09-11-2863-oauth-code-atomicity.md`)
2. **No provenance link.** `oauth_access_tokens` / `oauth_refresh_tokens` carry no reference to the
   code that authorized them (`0016_oauth.sql:55-78`). "Did this code mint?" is unanswerable from
   the grant side.
3. **Some windows cannot be compensated at all.** The control plane commits and the response is
   lost (`supabase_control.py` raises `RuntimeError` on a transport error; the client timeout is
   5.0 s). If the compensating soft-revoke *also* fails, a live orphan family survives — and
   because nothing records *which* code minted it, no **later** request can find it: the residue is
   only ever resolvable from inside the attempt that just failed, and in this window that
   attempt has already lost its evidence. The correct current posture is fail-safe (leave the code
   burned); a durable record lets a later attempt find and revoke the orphan.

**Root cause, stated as what is missing:** the code row cannot **record** *"claimed, outcome
unknown"* — so an observation made in one request cannot resolve a claim met by another — and no
token row points back at its authorizing code, so the grants cannot be asked *"did this code
mint?"* at all.

---

## 2. Problem diamond — alternative framings (evaluated, rejected)

- **"Make the mint one transaction (#3025) and the gap disappears."** Rejected here: #3025 is
  explicitly out of scope, and a transaction does not fix the *lost-response* window — the commit
  still succeeds while the response is lost, so the caller still needs durable state to learn that.
- **"Keep it in process and just retry harder."** Rejected: that is the status quo, whose defect is
  that the answer is derived from a read that can itself fail, leaving an unobservable state.
- **"Return `invalid_grant` for everything non-clean."** Rejected: it makes a genuinely retryable
  transient (a failed mint that provably left nothing) indistinguishable from a replay, which is
  the regression #2863 fixed.
- **"Store the plaintext pair so a retry can be served the same pair."** **HARD STOP** — contradicts
  the hash-only decision (0016). Not a candidate; see §0.

---

## 3. Solution diamond

### 3.1 Where the state lives — `oauth_codes` columns, not a separate ledger

| Option | Outcome | Verdict |
|---|---|---|
| **A. A separate redemption ledger keyed by `(code_hash, request_id)`** | The key does no work: OAuth's token request carries **no idempotency key**, so a retry cannot present a `request_id`. The only thing a retry presents is the **code bytes**, and `oauth_codes.code_hash` already identifies that. A ledger would add an attempt history no decision reads, and a second place for the code's state to disagree with the code row. | Rejected |
| **B. State columns on `oauth_codes`** (`redemption_state`, `redemption_id`, `redemption_settled_at`, `redemption_note`) + `code_id` provenance links on the two token tables | The row a retry already addresses **is** the redemption record; single-use means one authoritative state per code; the terminal transition is an UPDATE on the row the claim CAS already touched; backfill is trivial. | **Chosen** |
| **C. Ledger *and* columns** | Two sources of truth for one single-use credential. | Rejected |

**On the option the issue itself names ("a redemption ledger keyed by `(code_hash, request_id)`"):**
it is not wrong, it is *unnecessary* — the identity the protocol lets a retry present is the code,
not a request id. Recording the attempt id as a **column** (`redemption_id`) keeps the audit value
and pays no schema/consistency cost.

### 3.2 The state machine

| State | Meaning | Redeemable? |
|---|---|---|
| `unclaimed` | not claimed (mirrors `used_at IS NULL`) | yes, if unexpired |
| `claimed` | a redemption attempt owns the code; **outcome not yet recorded** | no — the claim is consumed; a second request answers terminal `invalid_grant` and settles the residue (§3.4) |
| `minted` | the attempt completed and the pair was handed to the response | no — replay-safe terminal |
| `burned` | terminal failure: the code must never mint again | no — terminal |

**The claim CAS and the settle CAS are two different fences, and both are needed:**

- `unclaimed → claimed` is a CAS on **`code_hash` + `used_at IS NULL` +
  `redemption_state='unclaimed'` + `expires_at > now()`** (the claim), and it writes
  `redemption_state`, `redemption_id` and `used_at` **in the same statement**, so the durable state
  can never be half-written relative to the claim.
- Every **settle** is a CAS on **the claim identity** — `id` + `redemption_state='claimed'` + (when
  the settling view carries it) `redemption_id` — and reports whether it won. Exactly one of *{the
  owning request, a reconciler that took the claim over}* can settle a claim.
- `_restore_code`'s in-process **re-arm** is a third CAS, on **`code_hash` + `used_at` +
  `redemption_state='claimed'`** — so it cannot resurrect a claim a reconciler has already settled.

- `claimed → minted` — after `_issue_tokens` returns the pair, immediately before it is returned,
  **and delivery is GATED on this settle winning**. If it loses, a reconciler has already taken the
  claim over (so it may be revoking the family this request just minted): the pair is compensated and
  the exchange surfaces as an aborted mint, which is **terminal** (`invalid_grant`) — the row is
  `burned` by then, so `_restore_code`'s `redemption_state='claimed'` fence cannot match and there is
  **no re-arm**. (The re-arm applies only to the *verified-clean, still-owned* failure, where this
  request's claim is intact.) A delivered family is therefore never revocable by a later reconcile,
  and a late attempt can never resurrect a settled claim.
- `claimed → unclaimed` — the **verified-clean** failure, `_restore_code`'s CAS re-arm, extended to
  clear the state columns in the same PATCH. This is #2863's re-arm, now durably recorded. It is the
  **only** re-arm: there is no cross-request one.
- `claimed → burned` — an **intentional terminal signal after the claim** (bad PKCE, client /
  redirect / resource mismatch, suspended org), CAS-fenced. The expiry path is *not* normally in this
  list: an already-expired code fails the claim filter and is refused from the `expired`
  observation with the row still `unclaimed`; the `note='expired'` burn covers only the sub-second
  race between the filter's `now()` and the post-claim re-check.
- `claimed → burned` (reconciler) — the claim is taken over past the grace and the residue is
  terminal: an orphan family was found and revoked (`note='orphan-revoked'`), or (past the grace)
  no family exists at all (`note='unresolved'`; §3.4).
- `claimed → claimed` — a failure whose compensation is **unverified** (`OAuthMintAborted.recovered
  is False`). This is deliberately *not* burned: the outcome is unknown, which is exactly what the
  durable record exists to represent, and burning it would hide the orphan from the only mechanism
  that can revoke it. It is resolved by the reconciler once it ages past the grace.

**Invariant, enforced at the schema — DIRECTIONAL:**
`used_at IS NULL ⇒ redemption_state = 'unclaimed'` (written
`used_at IS NOT NULL OR redemption_state = 'unclaimed'`, constraint
`chk_oauth_codes_redemption_used_at`).

**⛔ Deliberately NOT the biconditional, and this was a review finding.** The
biconditional `(state='unclaimed') = (used_at IS NULL)` was the first form and it
**breaks the deployed writer on its normal path**: the pre-#3027 `_consume_code`
PATCHes `used_at` alone, leaving the `'unclaimed'` default → `23514` on every
authorization-code exchange. And the rollout makes that window MANDATORY, not
theoretical: `deploy-hosted`'s fail-closed migration-drift gate requires prod to
hold this migration **before** the new image ships, so the old writer serves
traffic against this schema (and any rollback re-breaks it permanently).
`ADD CONSTRAINT ... NOT VALID` does not rescue it — Postgres still enforces a
NOT VALID check on INSERT/UPDATE (verified in PGlite). So the enforced direction
is the one the state machine depends on (**no settled row without a claim
timestamp**, which is what makes `_settle_redemption`'s `state='claimed'` filter
imply `used_at IS NOT NULL`), and the relaxed one is the shape `_observe_code`
already tolerates — it classifies a `used_at`-set, state-less row as `claimed`, the
reconcilable state; and such a row is inert for the reconciler, whose settle CAS
matches nothing and returns `lost-race` without revoking.

`used_at` stays the claim timestamp (all #2863/#3036 code and tests keep
working). Every write this codebase makes sets both columns in ONE statement, so
the biconditional holds for everything we write. Corollaries the reconciler
relies on: **a live family linked to a `claimed` code is never a delivered
family** (delivery is what writes `minted`, and it is gated on winning the
settle), and **a claim is never re-armed across requests** — an outcome-unknown
claim that is not reconciled clean stays consumed.

### 3.3 The provenance link — `code_id`, and it survives rotation

`oauth_access_tokens.code_id` **and** `oauth_refresh_tokens.code_id`, both
`bigint REFERENCES public.oauth_codes(id) ON DELETE SET NULL` (per §0, the #3036 policy), each with
its own index for the reconciler's `code_id = C AND revoked_at IS NULL` probe.

- **Both tables**, because the access row is the delivered credential and the refresh row is the
  family root; either alone leaves a question unanswerable.
- **Rotation inherits it.** `_issue_tokens` is called with the previous refresh row's `code_id`, so
  a rotated descendant still points at the authorizing code. Without this the reconciler would look
  straight past a live descendant and re-arm a code whose family is live — the exact
  double-grant the issue exists to prevent.
- `ON DELETE SET NULL` (not CASCADE): #3036's retention sweep reaps dead code rows; CASCADE would
  take live tokens with them.

### 3.4 The reconciler — lazy, on the next redemption attempt

| Option | Outcome | Verdict |
|---|---|---|
| A periodic sweep (folded into #3036's hourly runner) | No consumer exists between retries: nobody asks a code's outcome except a redemption attempt, and an undelivered family's plaintext is held by nobody, so it is inert until its own TTL. A scheduler would run forever to resolve rows no one reads. | Rejected (recorded as a follow-up if a consumer appears) |
| **Lazy reconcile when a redemption attempt meets a `claimed` code** | The attempt is the only consumer, so resolving it there is exactly enough; needs no scheduler and no new background loop; and it is the only path that can *do* anything with the answer. | **Chosen** |
| A terminal state only (never resolve) | Strands a recoverable transient as permanently burned. | Rejected |

**Rule** (`_reconcile_claimed_redemption`, grace `REDEMPTION_CLAIM_GRACE_S`, env
`TORTOISE_OAUTH_REDEMPTION_GRACE_S`, default 60 s). It is reached only from a **terminal** `claimed`
observation — nothing here is ever answered 503:

1. `now - used_at < grace` → the attempt may still be in flight. Touch **nothing**; return
   `'inflight'`. The caller still answers terminal `invalid_grant`. This is #2863's posture: the
   outcome is unknown, so it is not re-armed **and not advertised as retryable** — the retry can
   terminate (the sibling may settle `minted`).
2. `now - used_at ≥ grace` → **take ownership with the settle CAS first, then act** (ordering is the
   whole safety argument; see the fence note below).
   - CAS lost → `'lost-race'`: the owner settled `minted` first, so its family is delivered and
     **nothing is touched**.
   - CAS won, **a live family is linked** (`code_id = C`, `revoked_at IS NULL`, on either token
     table) → the mint committed and the response was lost (or the compensation failed). These rows
     were never delivered, so they are **soft-revoked** (best-effort: a failed
     revoke is captured and the row survives inert under the `burned` code until
     the TTL sweep) and the code is `burned`
     (`note='orphan-revoked'`). This is the branch the issue's *"committed but compensation
     failed"* Target item names, and the one #2863's read-based confirmation could not reach.
   - CAS won, **no live family** → at probe time the claim left no live credential, so the residue is
     **burned** (`note='unresolved'`) — fail safe. `'burned-clean'`. (True *at probe time*: the probe
     and the settle are not one transaction, so a family minted between them is missed — one of the
     documented residuals above.)

**⛔ What the grace window does NOT bound.** It does not prove the owner is dead. The mutating grant
is awaited with `timeout=float("inf")` in `hosted_api`, so a live sibling can outlive any window;
what the window bounds is *when a later request starts taking the claim over*. A live sibling that
outlives it loses the settle CAS and compensates its pair — an aborted grant, not a double grant.
The window is generous so that does not happen for nothing.

**⛔ Why there is no cross-request re-arm.** An earlier revision re-armed the code in case 2's
no-family branch ("the owner is dead"). Review reproduced the failure: the mutating grant is awaited
with `timeout=float("inf")` in `hosted_api`, so **no wall-clock bound proves the owner is gone** — a
stalled-but-live owner wakes, mints, and clears the re-armed row, leaving **two live families for
one single-use code**. Re-arming on that guess is the exact defect the issue exists to fix. A residue
with no family is therefore burned; the client re-runs authorization. The common *verified-clean*
failure still re-arms — **in process**, where the observation and the write are the same request, via
`_restore_code`.

**⛔ The fence, and why the CAS must come first.** Both parties settle through the same
claim-identity CAS, so it can be lost by either. If the reconciler revoked first and asked
permission later, an owner that settled `minted` in the gap would have its just-delivered family
revoked underneath the client (reproduced on review). Taking ownership first makes the two orderings
exclusive: **owner wins** → the reconciler gets `'lost-race'` and touches nothing; **reconciler
wins** → the owner's `minted` settle loses, so `exchange_auth_code` compensates instead of
delivering. Either way at most one live family is ever produced for one code.
4. **The reconciler's own read fails** → `'unobservable'`: never settle, never revoke. Nothing has
   been written, and the caller still answers terminal `invalid_grant`; a later attempt re-attempts
   reconciliation.

**The inherent residuals, stated honestly.** Two windows cannot be closed without storing plaintext,
which §0 forbids:

- An attempt that reaches the `minted` write and *then* loses its response is indistinguishable from
  a delivered one — the client did not receive the pair but the server cannot know. It is answered
  terminally (`invalid_grant`); the family it left is inert (nobody holds its plaintext) and is reaped
  by **#3036**'s retention sweep after its TTL.
- **TOCTOU against the family probe, and a best-effort revoke.** The reconciler probes for a live
  family and *then* settles. If the probe runs before the owner mints, the reconciler burns the claim
  with no family; the late owner then mints, loses the settle CAS, and if its compensation *also*
  fails a live row survives under a now-`burned` code — which the reconciler will never revisit. The
  same escape applies to the reconciler's own revoke: `_rollback_minted` is best-effort (it captures
  on failure and never raises), so a failed revoke leaves the row live under a `burned` code with
  `redemption_note='orphan-revoked'`. Both escapees are inert (their plaintext was never delivered)
  and are reaped by the retention sweep at TTL, but §3.4's no-family branch is not universally true:
  it is true at probe time, and the revoke is an attempt.
- **The zero-row observation rests on the control-plane seam.** `_observe_code`'s retryable 503 is
  safe because the claim PATCH was *observed* to match zero rows — and a select-bearing PATCH asks
  for `return=representation`, whose genuine zero-match answer is a content-bearing `[]`.
  `supabase_control.query` ALSO reads a 2xx with an **empty** body as `[]`, so an intermediary that
  stripped a committed PATCH's body would look like a zero-row claim. The consequence is bounded and
  is **not** a double-issue (the retry re-runs the same claim CAS, which is what decides), but the
  signal is then retryable for a code that is in fact consumed — #2863's untruthful retry, reinstated
  by the seam. Pinned by `test_empty_body_patch_reads_as_zero_rows`.

The invariant the issue asks for holds regardless: a retry **never creates a second live family**.

**A residue is only ever resolved by a later redemption attempt.** The reconciler is lazy by design
(§3.4), so a code that is claimed and never presented again stays `claimed` — and any live orphan
linked to it stays live until #3036's TTL sweep. No scheduler exists; that trade was chosen
deliberately above.

### 3.5 Migration + backfill

Additive, mirroring the #3036 migration's shape and its honesty about lock footprint:

- 4 columns on `oauth_codes` (`redemption_state text NOT NULL DEFAULT 'unclaimed'`; `redemption_id
  text`; `redemption_settled_at timestamptz`; `redemption_note text`), the CHECK on the state
  vocabulary, and the DIRECTIONAL `used_at`-agreement CHECK (§3.2 — not the biconditional; the
  biconditional rejected the deployed writer during the mandatory migration-before-image rollout).
- 2 `bigint` FK columns + 2 indexes on the token tables. **No dangling-pointer repair is needed**:
  the columns are new and nullable, so no existing row can carry a dangling value.
- **Backfill:** `used_at IS NOT NULL` → `burned` with `redemption_settled_at = used_at` and
  `redemption_note='backfill'`. Rationale: those rows were consumed before the state existed and we
  cannot prove they minted, so the fail-safe terminal is `burned` — never re-arm a historical code.
  `used_at IS NULL` rows keep the `'unclaimed'` default.

### 3.6 `redemption_note` — diagnostic only

A short, **never-read-by-control-flow** record of why a row became terminal: `'backfill'`,
`'terminal'`, `'expired'`, `'orphan-revoked'`, `'unresolved'`. All five are the literals actually
written (`oauth.py` + the migration). An outcome-unknown claim deliberately carries **no** note —
state `'claimed'` is the signal, because nothing has been settled. Without the note, a burned row
cannot be told apart from a burned-by-backfill row in an incident. It is not an authorization input,
and no decision branches on it.

---

## 4. Open for the owner (ratification, not a blocker)

**One item is genuinely the owner's, and this lane proceeds on the recorded decision rather than
guessing:** *may a retry ever re-serve the same credential pair?* → **No, and it is not open in the
adopt-over-a-decision sense**: re-serving requires persisting plaintext, which contradicts the
hash-only decision in `0016_oauth.sql` and `docs/oauth-mcp.md`. Per the contradiction test the route
to a different answer is a **reopen** with evidence, not a quiet exception. The decision request on
#3027 asks the owner to confirm the recorded reading; implementation carries the no-re-serve answer.

Everything else in §3 follows from the issue's own Target list and is presented for ratification in
the #3027 decision comment.

---

## 5. Implementation plan (this lane)

1. `supabase/migrations/20260925000002_oauth_redemption_state.sql` — columns, CHECKs, FKs, indexes,
   backfill (§3.5).
2. `tortoise/oauth.py`:
   - claim writes `redemption_state='claimed'` + `redemption_id` (§3.2), selecting `id` and
     `redemption_state`;
   - `_settle_redemption(cp, code_row, state, note)` — the claim-identity CAS; its **return value
     gates delivery** on the `minted` settle and fences the reconciler's takeover;
   - `_restore_code` clears the state columns in its existing CAS PATCH, fenced on
     `redemption_state='claimed'` so an in-process re-arm cannot undo a reconciler's burn;
   - `_issue_tokens(..., code_id=None)` stamps `code_id` on both rows; `exchange_auth_code` passes
     the code's `id`; `refresh_grant` passes the previous row's `code_id` through rotation;
   - `_reconcile_claimed_redemption` + the grace constant, wired into `_consume_code`'s
     already-claimed branch — which stays **terminal** (`invalid_grant`), never 503.
   - **#2863's `_restore_code` / `_consume_state` / `_mint_observably_clean` stay in place.**
3. Tests:
   - `tests/test_oauth_redemption_state.py` (fake control plane, the issue's own instrument):
     state transitions; **lost response after a committed mint** (CP applies the write then raises)
     → terminal signal, no second live family; the #2863 **double-fault** pinned by the reconciler
     (orphan revoked, code burned); replayed code after success → no second family; stale `claimed`
     with no family → **burned** (never re-armed) and never redeemable; fresh `claimed` → **400** and
     untouched; reconciler read failure → 400 and untouched; and the **CAS fence in both
     directions** (a late `minted` cannot resurrect a reconciled claim; a reconciler cannot revoke a
     family whose owner settled `minted` first).
   - `supabase/tests/20260925000002_oauth_redemption_state.sql` — PGlite assertions: state
     vocabulary CHECK, the DIRECTIONAL `used_at` agreement CHECK (**including that the legacy
     `used_at`-only writer is ACCEPTED and that a settled row without a claim timestamp is
     rejected, on INSERT *and* UPDATE**), the backfill (a pre-existing `used_at` row
     becomes `burned`), the `code_id` FK enforcement and `SET NULL` on code delete, and rotation
     inheritance is asserted at the app layer.
   - The pre-seed for the backfill is placed **before** the migration in `validate.mjs`, mirroring
     #3036's "#4216 pattern": put the dirty row before the migration so the migration's own UPDATE
     is the thing under test.
4. `docs/oauth-mcp.md` — document the redemption state machine and the no-re-serve rule.

**Explicitly not in this lane:** removing #2863's in-process confirmation (the Target's last item) —
the durable path must be proven first. Noted as the follow-up step.

---

## 6. Verification plan (and its limits)

| Suite | Command | Status in this lane |
|---|---|---|
| PGlite schema + SQL assertions | `npm --prefix supabase/tests/pglite run validate` | **runs** — where the migration is proven |
| OAuth fake-CP unit + fault suites | `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_oauth_redemption_state.py tests/test_oauth_token_fault.py tests/test_oauth_mcp.py -q` | **runs** (embedded carve-out; fake control plane) |
| `ast.parse` / `ruff` / typecheck | via `commit-workflow` pre-flight | **runs** |
| `tools/surface_manifest.py check` | as required | **runs** |
| Docker-lane DB-backed pytest | `TORTOISE_DB_URI=… uv run pytest` | **CANNOT RUN** — local FalkorDB is wedged; an infrastructure outage, not a code result. No backend fallback is taken (HARD RULE). |
