<!-- research-path: docs/plans/2026-09-25-4021-inverted-predecessor-window.md -->

# Plan — #4021: refuse a retroactive successor that would persist an inverted predecessor window

**Issue:** daniel-ospina/tortoise#4021 (P0-blocker, lane `c4-answer-quality`)
**Branch:** `fix/4021-supersede-successor-start` (base `294d5847e`; re-based from `c5d5afea5` after main advanced 10 commits)
**Tier:** standard

---

## 1. Confirmed problem

`TortoiseSDK.supersede_point` (`tortoise/sdk.py`, def 5930) resolves the successor's window
start `succ_vf` by the documented order (`valid_from` kwarg → successor stored `validFrom` →
successor `createdAt` → `now`; lines 6086-6093) and stamps it as the predecessor's `validTo`
(stamp query 6381-6388) **without ever reading the predecessor's own `validFrom`**. A retroactive
successor therefore persists an interval whose end precedes its start:

```
old.validFrom = '2026-06-10'
old.validTo   = '2026-06-01'      ← INVERTED  (validTo < validFrom)
```

Reproduced at `c5d5afea5` and re-confirmed at `294d5847e` (embedded scratch SDK) on **both** write paths — the issue's two
required paths:

| path | input | result |
|---|---|---|
| no kwarg | `old.validFrom='2026-06-10'`, `new.validFrom='2026-06-01'` | ACCEPTED → `old.validTo='2026-06-01'` (inverted) |
| agreeing kwarg | `valid_from='2026-06-01'` (== successor's stored start, so the #3980/#3984 guard passes) | ACCEPTED → inverted |
| forward control | `old.validFrom='2026-06-01'`, `new.validFrom='2026-06-10'` | correct |

**The invariant actually violated is interval well-formedness on the persisted predecessor
window** — `_created_sort_key(validTo) >= _created_sort_key(validFrom)` — the ordering assumed by
`restore_point_at`'s `_covers` (the sole coverage predicate; `sdk.py:15524-15529`, measure
`tortoise/search_engine.py:1664`). An inverted predecessor window satisfies `_covers` for **no**
instant, so the read path renders it as honest absence (`found=False` + `nearest`).

### The issue body's stated harm is overstated (corrected here, not repeated)

> "because the chain walk anchors on the predecessor's `_covers`, an inverted predecessor window
> makes the whole chain unreachable from the predecessor's id"

`restore_point_at`'s CORRECTS walk is **backwards-only** (`MATCH (p)-[:CORRECTS]->(old)`,
`sdk.py:15452-15460`), so entering from the chain **root** yields a single-element chain — verified
on a well-formed **forward** chain (`a(06-01)←b(06-10)←c(06-20)`): `from a, t=06-15` → `found=False`.
(A *middle* node yields its own backwards suffix, e.g. `from b, t=06-15` → `[b, a]`, which is why
"unreachable" is the root's property.) Unreachability from the root is therefore universal, not
inversion-specific. What the inversion **uniquely** adds is narrower and still P0: the predecessor's
own interval becomes empty, so it cannot answer for any instant, and the read path cannot
distinguish that from honest absence.

The body's "forward control works correctly" is also overstated: at the shared-boundary instant
(`t == validTo == successor.validFrom`) a closed-interval chain reads `ambiguous=True` even
forward. That is a pre-existing closed-interval property, **out of scope** (see §6).

### Root cause and its home

One root: the write boundary has **no interval-well-formedness guard**, and the SDK's
terminal-transition writer *derives* `validTo` from an independent fact. The same omission exists in
`invalidate_point` (`validTo = now`, `sdk.py:5880-5885`), in the caller-supplied
`create_point`/`update_point` prop pair (`sdk.py:3459-3483`, `5653-5677`), in
`mining._temporal_wire` (`mining.py:468`) and in the replay folds
(`projection/entities.py:747-748, 1341, 1443`) — **eight writers/derivers of one window state, none
enforcing the invariant.** Those are **different roots** (see §6) and are NOT absorbed.

**What is one-homed here is the *successor-window resolution*, not the whole interval invariant.**
`_supersede_window_end` (module level in `tortoise/sdk.py`) is called by both the writer and the MCP
preview — so those two cannot drift. The interval invariant itself is only enforced at this one
writer in this PR; the **shared contract** that would bind all eight writers
(`validate_validity_window(valid_from, valid_to)`, the direct analogue of the existing
`validate_span`) is the intended end state, tracked by #5358/#5359/#5360. This plan must NOT be read
as "the window concern is handled everywhere" — it is handled at one writer, with the rest
enumerated and filed.

The in-repo precedent for that end state is `validate_span` (`tortoise/commit_schema.py:332-366`):
"the ONE HOME for the rule, called by every write path". `validate_span` itself covers the
**span-offset** axis (integer character offsets; strict `end > start`) and does NOT enforce the
validity window — so its existence is not evidence that the interval invariant is already homed. Its
ONE-HOME *architecture* is what is adopted, not its predicate.

A residual the plan does NOT close (pre-existing, not introduced here): a **duplicate id** makes the
`[0]` node reads order-nondeterministic (the writer's own `MATCH` fans out), so the guard's verdict
can vary per query plan. That applies to **both** the pre-existing successor read (`vf_rows[0]`) and
the **new** predecessor read added by §3.2 (`old_vf_rows[0]`) — both are inherited from the existing
fan-out, not created by this change; both are recorded here and in the PR's "not covered" list.

> **Also corrected by this change:** the `supersede_point` docstring's "fall back to now — monotone,
> never a gap" and ONTOLOGY §4.1's `validTo` wording are **false as written** (#4021's inversion and
> #5360's unorderable end both defeat them). The docs edit in §3.4 corrects the overstatement rather
> than extending it.

### Decision: REFUSE (fail-closed), derived — not inherited from the body

The body says the A/B fork (refuse vs normalise) is a product decision. The re-derivation:

- **Contradiction test (first).** Is there a recorded decision this contradicts? #3980/#3984
  (recorded: `docs/ONTOLOGY.md` Changelog v3.13 + §4.7) already REFUSES a `valid_from` kwarg that
  disagrees with the successor's stored start — fail-closed, before any mutation. Refusing an
  inverted window *extends the same principle*; it contradicts no recorded decision. ONTOLOGY §4.7
  records no retroactive-correction ruling. **No contradiction → live candidate.**
- **External convergence.** IBM DB2 `BUSINESS_TIME` "automatically creates an implicit check
  constraint ensuring the period end is greater than the period start"; `WITHOUT OVERLAPS`
  forbids overlapping periods; SQL:2011/Postgres interval constraints reject inverted periods.
  Every comparable rejects; none repairs-and-persists.
- **Normalisation is unwarranted.** Clamping `old.validTo` up to `old.validFrom` invents a
  retroactive semantics with no definition of what the clamped value means (and produces a
  spurious `ambiguous` at the shared instant), and needs an owner decision it does not have.
- **The capability cost is recorded, not hidden.** Refusal forecloses *backdating a successor*
  under this verb. A first-class retroactive correction is a separate capability (bitemporal
  practice — Fowler's *RetroactiveEvent* — treats it as a distinct operation); filed as its own
  issue (§6). The owner may overrule refuse→normalise; that is recorded as the owner question on
  #4021 (§7) and does not block this fail-closed fix (reversible, default-safe).

## 2. Scope

**In scope**
- `supersede_point`: refuse (before any mutation/emit) when the resolved predecessor end is
  strictly before the predecessor's own `validFrom`.
- The shared helper owning the successor-window resolution **and** the new refusal.
- MCP dry-run parity: `_preview_supersede` (`tortoise/mcp_server.py:4356+`) must refuse identically
  (otherwise `dry_run=True` reports "would succeed" for an operation the writer refuses).
- `docs/ONTOLOGY.md` §4.1/§4.7 `validTo` rows + a changelog entry (with the interval-integrity
  rationale and an `OVERRIDES:`-style statement of the deliberate refusal).
- Tests in the already-CI-subscribed `tests/test_validity_windows.py` + `tests/test_dry_run_preview.py`.

**Out of scope (separate roots → separate issues, §6)**
- `invalidate_point`'s `validTo = now` (documented in ONTOLOGY §4.7; changing it is an unrequested
  semantics decision — refusing could be *worse* than the current corruption for a future-dated
  claim, and `retract_point` is the window-agnostic alternative).
- `create_point`/`update_point` caller-supplied `validFrom`/`validTo` pairs, and the
  `mining._temporal_wire` window-start post-pass (same root: an unchecked window writer).
- The no-kwarg **and undated-successor-kwarg** unparseable/unorderable end (#5360 — the unparseable
  kwarg against an *undated* successor also resolves to an unorderable end, because the #3980 guard
  cannot fire when `stored_vf is None`).
- `commit_ops.apply_supersessions`' warn-and-skip: the refusal turns a previously-succeeding (but
  corrupting) retroactive ingest supersession into a **silent fail-open skip** (#5365).
- Read-path/audit signal for an **already-persisted** inverted window (#5361).
- A first-class retroactive-correction operation (#5362).
- The hosted commit path's 4xx mapping, the partial-write consequence, and its misleading retry
  advice (#5363).
- Duplicate-successor-id read nondeterminism (pre-existing; recorded in §1, not filed).

### Boundary: equality is ALLOWED

`succ_vf == old.validFrom` yields the closed zero-length window `[vf, vf]` — not inverted. Only
strict `<` is refused. (Refusing equality would reject a legitimate same-instant replacement and is
beyond the defect.)

## 3. Design

### 3.1 The helper (one home, two callers)

Module level in `tortoise/sdk.py`, immediately above `class TortoiseSDK:` (2342):

```python
def _supersede_window_end(*, old_id, new_id, old_vf, valid_from,
                         stored_vf, successor_created_at, now):
    """Resolve the predecessor's window END and refuse an inverted window (#4021).

    Resolution order is unchanged and is the ONE home for it:
        str(valid_from) → stored_vf (truthiness) → successor_created_at → now

    Returns the value AS PERSISTED — the stored branch stays RAW (no `str()`).
    A numeric stored value must keep keying as `(0, float)`: passing it through
    `str()` makes it unparseable `(1, text)` and REINTRODUCES the unbounded
    predecessor window the #3980 guard exists to prevent. Pinned by the new tests
    `test_window_end_numeric_stored_value_stays_raw` and
    `test_supersede_numeric_stored_start_no_kwarg_raw_end` (NOT by the #3980
    numeric test, which passes an ISO kwarg and takes the `str(valid_from)` branch).

    Refuses (ValueError) when `old_vf is not None` and
    `_created_sort_key(succ_vf) < _created_sort_key(old_vf)` — the same measure
    and the same `is not None` presence predicate `restore_point_at`'s `_covers`
    uses. Strictly-before only: equality (`[vf, vf]`) is well-formed.

    Scope: this refuses the **inverted** direction. An **unparseable** resolved
    end (a truthy-but-unparseable stored successor `validFrom` with no kwarg) is
    a SEPARATE residual — the #3980 orderability gap on the no-kwarg path —
    tracked as #5360 and deliberately NOT absorbed here.

    The #3980 kwarg-vs-successor agreement guard is NOT here — it is reachable
    only when a kwarg is passed, and it stays inline (byte-equivalent) at its
    reviewed call site.
    """
    from .search_engine import _created_sort_key   # lazy — avoids the import cycle
    if valid_from is not None:
        succ_vf = str(valid_from)
    elif stored_vf:
        succ_vf = stored_vf
    elif successor_created_at:
        succ_vf = successor_created_at
    else:
        succ_vf = now
    if old_vf is not None and _created_sort_key(succ_vf) < _created_sort_key(old_vf):
        raise ValueError(<message below>)
    return succ_vf
```

**Error message** (stable substring `"inverted window"`; names only existing operations; **written to survive `_scrub_error`**, whose regex `(host=|at |to )[\w.-]+(:\d+)?` rewrites any word ending in `at`/`to` followed by a space — e.g. `that `/`into ` — so the wording avoids those):

```
supersede_point: refusing supersede {old_id!r} - {new_id!r} - the successor's
window start {succ_vf!r} precedes the predecessor's validFrom {old_vf!r};
persisting it would leave an inverted window (validTo < validFrom), which no
query instant resolves. Give the successor a validFrom on-or-after {old_vf!r},
or use `retract_point()` (window-agnostic) for withdrawal of the predecessor
```

Verified mechanically: `"inverted window" in msg` and `_scrub_error(msg) == msg` (zero regex hits).
The **template** is scrub-stable; an interpolated id that itself contains `" at x"`/`" to x"` would
still be rewritten, which is correct scrub behaviour (ids are not reworded to evade the scrubber) and
is why AC6 is scoped to the template.

### 3.2 `supersede_point` insertion (`sdk.py:6067-6093`)

- Keep the successor read (6067-6070) and `stored_vf` (6071).
- Keep the #3980 guard **byte-for-byte** (6072-6085).
- Add one read of the predecessor's start; pair it with `vf_rows[0][1]` and call the helper:

```python
old_vf_rows = proj.g.query(
    "MATCH (n:Point {id:$id}) RETURN n.validFrom", params={"id": old_id},
).result_set
old_vf = old_vf_rows[0][0] if old_vf_rows else None
succ_vf = _supersede_window_end(
    old_id=old_id, new_id=new_id, old_vf=old_vf, valid_from=valid_from,
    stored_vf=stored_vf,
    successor_created_at=(vf_rows[0][1] if vf_rows else None), now=now)
```

**Fail-closed ordering:** this is strictly before `_emit_event("PointSuperseded", …)`
(`sdk.py:6102-6106`) and before every mutation (status stamp 6381). No phantom event is journaled
on refusal — the pattern is already pinned by `tests/test_validity_windows.py:194-201`.

### 3.3 MCP preview parity (`mcp_server.py`)

- Add `_supersede_window_end` **and `_now_iso`** to the `from tortoise.sdk import (...)` list (23-26).
  Do **not** add a `datetime` import and do **not** inline `datetime.now(timezone.utc).isoformat()` in
  `mcp_server.py`: `tortoise/sdk.py:2041` already defines the canonical module-level
  `_now_iso() -> str`, `mcp_server.py` already imports private names from that module, and the inline
  form would (a) add a 30th clock site and (b) trip `ruff` UP017 (the SDK's inline site carries an
  explicit `# noqa: UP017`; `mcp_server.py` has none and currently passes ruff).
- In `_preview_supersede`, on the `transfer_edges=True` path only, **after** the 2a
  operator-edge loop (i.e. immediately before `succ_rows = proj.g.query(...)` at `mcp_server.py:4451`)
  — NOT before the loop. The writer validates relationship types (`validate_rel_type`,
  `sdk.py:5998-6001`) **before** its window guard (`6072-6093`); inserting the preview guard before
  the preview's `validate_rel_type` loop would reverse error precedence on a graph carrying both an
  undeclared rel type and an inverted window. The parity contract is **the refuse/allow verdict
  plus the message when the window is the only fault** (a rel-type fault legitimately wins in both).
- Read `old_vf` and the successor's `validFrom`/`createdAt` with the preview's **own** net-new
  queries — `succ_rows` (`:4451`) returns `ID(n)` only and its `[0]` indexing is relied on by
  downstream passes, so it must not be perturbed. Then call the helper with `valid_from=None` (the
  MCP surface exposes no kwarg) and `now=_now_iso()` (the writer's clock; only reached when the
  successor is undated, where preview↔writer message equality is not asserted — test 14).
  The helper raises → `_safe` returns `{"error": "...inverted window..."}`.
- `transfer_edges=False` delegates to `_preview_invalidate` and is NOT guarded (invalidate is out
  of scope).

### 3.4 ONTOLOGY.md

- §4.1 `validTo` row and §4.7 `validTo` row: append the new precondition (refusal on a successor
  start strictly before the predecessor's `validFrom`; equality allowed; the `_created_sort_key` /
  `is not None` measure; the explicit scope boundary), plus the interval-integrity rationale
  (SQL:2011 `PERIOD` / DB2 `BUSINESS_TIME` / Postgres exclusion-constraint convergence) and a
  deliberate-refusal marker.
- **Correct the existing overstatement**: §4.1's/monotone language promises an end that is never a
  gap; #4021 (inversion) and #5360 (unorderable end) both defeat it. The row must state the actual
  contract (well-formedness enforced at `supersede_point`; the other writers listed as residuals with
  their issue ids) rather than extend the false claim.
- New changelog entry `v3.18 (2026-09-25, issue #4021)` above v3.17, stating the deliberate
  refusal, the recorded capability cost (retroactive correction is not this verb), and the scope
  boundary — **and bump the frontmatter `title:` (line 2) and the H1 (line 14) from `v3.17` to
  `v3.18`**, since the doc's convention pairs the title with the newest changelog entry.

### 3.5 Adversarial Threat Surface

**Declared (gate/enforcement code: "a caller cannot make the write path fail open").** Untrusted
input surface = the `valid_from` kwarg plus point temporal properties (`old.validFrom`, successor
`validFrom`/`createdAt`), reachable via SDK and MCP.

| # | Adversarial input | Required behaviour | Test |
|---|---|---|---|
| B1 | successor's stored `validFrom` strictly earlier than the predecessor's, no kwarg | refuse before any mutation/emit | 5 |
| B2 | `valid_from` equal to the successor's stored start (passes #3980) but earlier than the predecessor's | refuse — must not be a restatement of #3980 | 6 |
| B3 | non-string `valid_from` (numeric epoch) on an **undated** successor, where #3980 cannot fire — the writer persists `str(valid_from)`, which `_created_sort_key` cannot order | the helper **resolves the successor window first** (`str(valid_from)`, byte-identical to `sdk.py:6087`) and measures the RESOLVED value — a raw-object comparison would key `(0, float)` while the artifact is `(1, text)`, diverging from both the persisted value and #3980's own measure | 16 |
| B4 | predecessor `validFrom` falsey-but-present (`0`, `""`) | presence predicate `is not None` — a real start | 3 |
| B5 | predecessor `validFrom` unparseable — falsey (`""`) **and** truthy (`"not-a-date"`) — against a parseable end | refuse (degenerate under the read measure) | 3 |
| B6 | undated successor whose fallback end precedes a future-dated predecessor start | refuse | 8 |
| B7 | `dry_run=True` on any B1–B6 input | refuse identically (no fail-open preview). Test **13** asserts the refusal message substring + unchanged graph counts; test **14** is the differential (exact scrubbed-message parity) on a **dated** successor, where `succ_vf` is deterministic; test **17** is the **undated** B6 case, where parity is the **verdict** only. No equality is asserted across the clock boundary: preview `_now_iso()` and writer `now` are different instants, and because the refusal predicate (`resolved end < validFrom`) is monotone in `now`, a preview taken earlier can only be *conservatively* wrong — never fail-open — on a monotone clock | 13, 14, 17 |
| B8 | `old.validFrom` mutated between preview and apply (TOCTOU) | **OUT of scope** — single-writer assumption; duplicate-id read nondeterminism recorded in §1 | — |

**Out of scope (different roots, filed):** `invalidate_point`'s `validTo=now` (#5358); caller-supplied
`create`/`update` pairs + `mining._temporal_wire` (#5359); the unparseable/unorderable end on the
no-kwarg **and undated-kwarg** paths (#5360); read-path/audit **detection** of already-corrupted
windows (#5361); a first-class backdate operation (#5362); the hosted 422 mapping (#5363);
`commit_ops`/`promote_point` fail-open skips (#5365); the missing shared
`validate_validity_window` contract (#5374); the duplicated `_now_iso` + its false "shared by write
paths" claim (#5375); #3982 (date-only → LOCAL midnight) and #3985 (falsey-but-present no-kwarg
start). Also still distinct and **unaddressed** here, per the issue's "do not collapse" list:
**#3991** (kwarg-vs-successor disagreement constraint) and **#3945** (undated successor → open start).

## 4. Downstream surfaces (named, with the decision)

| Surface | file:line | Effect of the refusal | Decision |
|---|---|---|---|
| MCP `tortoise_supersede` (apply) | `mcp_server.py:2107→2123` | ValueError → `_safe` → `{"error": …}` (no corruption) | in scope (correct) |
| MCP `tortoise_supersede(dry_run=True)` | `mcp_server.py:4356+` | must refuse identically | **in scope** — parity |
| hosted commit §5 write | `hosted_api.py:11708` (successor created with `validFrom=pr.point.when`, 11681-11683) | ValueError → the documented fail-closed **500** (`11964-11972`) | **ACCEPTED as fail-closed, with a disclosed consequence**: the successor was already created (`11680-11705`) and is **not** rolled back ("the record stays partial"), and a retry cannot succeed because `supersede_point` re-raises deterministically — so the payload is permanently un-committable and the 500's "retry with the same `client_commit_id`" advice is **wrong for this class**. `hosted_api.py` is shared by four lanes; the 422 mapping + partial-write handling is filed as **#5363**. This behaviour is disclosed in the PR body's "not covered" section. |
| `commit_ops.apply_supersessions` | `commit_ops.py:537-541` | `except Exception → warn()` → **fail-open skip** | **CHANGED BEHAVIOUR, disclosed**: a retroactive `pt_` supersession previously *succeeded* (while corrupting the window); it is now warned+skipped, leaving the point live with no CORRECTS and only a callback warning — fail-open, unlike the SDK/MCP paths. Pre-existing posture, new input class → filed as **#5365**; a test pins the warn+skip. |
| `promote_point` | `sdk.py:6681-6686` | `except ValueError → warning` → **fail-open skip** | **CHANGED BEHAVIOUR, disclosed** (same class as `commit_ops`): a promoted candidate whose resolved start precedes the live target's `validFrom` now raises and is swallowed, so the promotion proceeds **without** superseding. Folded into **#5365**. |
| `mining._temporal_wire` | `mining.py:468` | writes `validFrom` on a bulk post-pass | **unaccounted writer**, found by the D2 family sweep — folded into **#5359** |
| `indexer/github_indexer._upsert_statement` | `tortoise/indexer/github_indexer.py:671` | passes `valid_from = n["updated_at"]` (an agreeing kwarg); GitHub `updated_at` is non-decreasing ⇒ the guard cannot realistically fire | dormant since #1844; no change. If re-wired (#1843), decide warn-and-skip vs propagate. |
| `tools/gen_ask_transcripts.py:118` | script | fixture-only; no dated predecessor ⇒ unreachable | no change |
| rank-time freshness (`time_aware.py`, #2520 — landed on main *after* the plan's first draft, `294d5847e`) | `tortoise/time_aware.py:is_stale_entry` / `prefer_latest_order` | none harmful — this reader closes a window with `valid_to`/`expired_at` cast to a date and **never compares `valid_to` to `valid_from`**, so an inverted window is not mis-read here; it would merely have marked the corrupted predecessor stale (which is what the writer intended anyway). Disclosed because the plan's §1 claim that "the read measure is `_created_sort_key`" does **not** extend to *every* reader | no change; the refusal strictly reduces the set of windows this reader sees. Any reader that *does* assume well-formedness is covered by the `validate_validity_window` contract (#5374) and the audit (#5361) |
| rebuild / replay | folds replay `valid_to` verbatim (`projection/entities.py:1338-1345`) | guard precedes the emit ⇒ nothing inverted is journaled ⇒ no fold mirroring needed for live/rebuilt parity | verified. **Residual:** a pre-fix journal replayed by the fold re-materialises an inverted `valid_to` verbatim — detection/repair is **#5361**. |

## 5. Tests (tests FIRST)

`tests/test_validity_windows.py` is already in `config/ci-surfaces.yml:1058` — **no CI registration
change needed**. Class-B question answered per test: **(1) what value makes it fail? (2) does the
fixture make that value reachable?**

Helper tests (import `_supersede_window_end`):
1. `test_window_end_refuses_inverted_start` — table `(old_vf, succ_vf)`; `('2026-06-10','2026-06-01')` refused.
2. `test_window_end_equal_start_allowed` — equal + format-only-equal (`…Z` vs `…+00:00`) allowed.
3. `test_window_end_falsey_or_unparseable_start_refused` — presence/orderability predicate: `old_vf=""` + a parseable end (`'2026-06-01'`) is refused (an unparseable start sorts AFTER the end); `old_vf=0` + a **pre-epoch** end (`'1969-12-31T00:00:00+00:00'`) is refused; and a **truthy** unparseable start (`old_vf="not-a-date"`) against a parseable end is refused (B5's truthy half — distinct from the falsey `""` half). (A post-epoch end with `old_vf=0` is NOT an inversion — `(0, 0.0) < (0, <epoch>)` — so the `0` half must use the pre-epoch end to exercise the predicate.)
4. `test_window_end_numeric_stored_value_stays_raw` — stored `1781049600.0` no kwarg → **returns the raw float** (pins the `-> str` regression).
16. `test_window_end_numeric_kwarg_resolved_before_measure` — **B3's discriminator** (the undated-successor sibling of the existing `test_supersede_numeric_epoch_kwarg_refused`, `tests/test_validity_windows.py:363`; that one covers the *dated* successor, where #3980 already refuses). Setup: `old_vf='2026-06-10'`, **undated** successor, `valid_from=1780000000.0` — a real instant ~12 days earlier. The RAW float keys `(0, 1780000000.0)` → strictly less → would REFUSE; the RESOLVED string keys `(1, '1780000000.0')` → ALLOW. Assertions: (a) the call does **not** raise, and (b) it returns exactly the string `'1780000000.0'` (`isinstance(result, str)`), not the float. Dropping the `str()` — the natural `-> str` return-type regression the plan's §3.1 pins — makes (a) fail; returning `valid_from` unresolved makes (b) fail.
    **The chosen epoch is a HOST-DEPENDENCE guard, and the test asserts it as a self-check.** `_created_sort_key` parses an ISO *date-only* string with `datetime.fromisoformat(...).timestamp()` on a NAIVE datetime, i.e. **host-LOCAL midnight** — so `'2026-06-10'` keys as `(0, 1781067600.0)` in UTC−5 but `(0, 1780999200.0)` in UTC+14. `.. == 1781000000.0` (the value first drafted here) would therefore key LESS than the predecessor's start on a UTC+14 host and the discriminator would silently vanish. The test must assert the premise explicitly — `assert _created_sort_key(raw_epoch) < _created_sort_key(old_vf)` — so the discriminator is self-verifying on any host (and the chosen epoch is ~1.2e7 s below the UTC+14 reading, far beyond any ±14 h offset). Do NOT hardcode `1781049600.0` as "the" key of `old_vf`: that number is the UTC midnight, and the plan's earlier draft stated it as if it were host-independent (reviewer #1, cycle 3).
    **Residual (out of scope, #5360):** because the persisted end is an unorderable string, the inversion is genuinely undetectable for this input — the test pins the CONSISTENCY of the measure with the artifact, not a repair; an undated successor + numeric kwarg still lands an unorderable `validTo`.

Writer tests:
5. `test_supersede_retroactive_successor_refused` (no kwarg) — refuses; assert **fail-closed**: no `validTo`/`expiredAt`, `status != 'superseded'`, no CORRECTS, no `PointSuperseded` event.
6. `test_supersede_retroactive_successor_agreeing_kwarg_refused` — `valid_from` == successor's stored start (the #3980 guard ACCEPTS) → new guard refuses.
7. `test_supersede_equal_start_allowed` — accepted; `old.validTo == old.validFrom`.
8. `test_supersede_future_dated_predecessor_refused` — undated successor whose fallback end precedes `old.validFrom`.
9. `test_supersede_numeric_stored_start_no_kwarg_raw_end` — dated predecessor, numeric-stored successor, no kwarg → accepted, `validTo` raw and orderable.
10. `test_restore_point_at_forward_supersede_window_not_inverted` — **read-path assertion**: after a legitimate forward supersede, the predecessor's window read through `restore_point_at` (chain entry) is not inverted and in-window instants resolve.
11. `test_restore_point_at_after_refusal_predecessor_window_intact` — **the read-path assertion that actually REDs on the reverted branch**: attempt the retroactive supersede → expect the refusal → then `restore_point_at(old_id, t)` for an in-window `t` still returns `found=True` with `chain[0]["valid_to"]` **not inverted** (and an out-of-window `t` is honest absence). Pre-fix this test fails because the write is accepted and the window inverts; it is added to the mutation-proof list.
12. `test_supersede_refusal_message_survives_scrub` — `_scrub_error(str(exc)) == str(exc)` and `"inverted window" in str(exc)` (the MCP surface must show the full actionable hint, not a `***`-redacted one).

MCP parity tests (`tests/test_dry_run_preview.py`):
13. `test_supersede_preview_refuses_a_retroactive_successor` — `{"error": … "inverted window"}`; `_graph_counts` unchanged.
14. `test_supersede_preview_verdict_matches_writer` — differential on the **scrubbed** strings: `result["error"] == _scrub_error(str(writer_exc))` for a **dated** successor (deterministic `succ_vf`; the undated `createdAt`/`now` fallback can differ by clock). Compare through `_scrub_error` — raw equality is impossible because `_safe` scrubs (`mcp_server.py:1055-1063`: `(host=|at |to )[\w.-]+`).
17. `test_supersede_preview_refuses_undated_successor_case` — B6's preview parity: a future-dated predecessor + an **undated** successor → `dry_run=True` refuses (verdict parity only; the preview's `_now_iso()` and the writer's `now` are taken at different instants, so the message is not compared).

`commit_ops` / `promote_point` regression tests — name an existing, CI-subscribed file (`tests/test_lme_ingest_v2_supersession.py`, registered at `config/ci-surfaces.yml:1466`) or `tests/test_commit_supersession_parity.py` (`:283`) — **not** a new unregistered file:
15. `test_ingest_retroactive_supersession_warns_and_skips` — pins the new fail-open warn+skip on the ingest path (#5365), so the behaviour change is recorded, not silent. (`promote_point`'s sibling swallow is deliberately **not** pinned here — its `temporal_replacement` path is unpinned in the existing suite, and the plan records that in §4/§5 row 5 and in #5365 rather than claiming "existing lifecycle tests" cover it.)

**Mutation proof** (recorded in the PR): re-comment the helper's inversion branch → tests 1, 3, 5, 6, 8, 11, **12**, **13**, **14**, **15**, **17** fail; restore → pass. (Test 15 REDs because a reverted branch makes the ingest supersede *succeed*, so no warn+skip occurs. Tests 2, 4, 7, 9, 10, 16 stay green — they do not depend on the refusal; 16 pins the `str()`-measure, not the refusal.)

**Regression sweep (REQUIRED, not assumed):** the refusal fires on a previously-accepted input, and ~40 test files call `supersede`/`supersede_point`. Before merge: grep the suite for fixtures with a dated predecessor + an earlier/undated successor start, then run the **full selected lane** (`tools/ci_selection.py` / the repo's standard docker test command) green — not just the two touched files. AC5 requires that, not the single file.

### Integration Surface Map

| # | Surface | Type | Flow | Test layer | Contract | Key failure modes |
|---|---|---|---|---|---|---|
| 1 | `supersede_point` write boundary | DB/state mutation | Write | pytest integration (real graph) | `validTo`/`validFrom` on `:Point` | (a) inverted window persisted; (b) `PointSuperseded` journaled before the refusal |
| 2 | MCP `_preview_supersede` | state mutation (read-only) | Read | pytest integration | preview verdict == writer verdict (message equal when the window is the only fault) | (a) fail-open preview (`dry_run` says success); (b) error precedence reversed vs the writer's rel-type validation — **no test; deliberate** (the parity contract is the verdict when the window is the ONLY fault, and crafting an undeclared rel-type fault is a different surface; §3.3's placement pins it by construction — after the 2a loop, so the preview's `validate_rel_type` still wins) |
| 3 | hosted commit §5 | HTTP/DB | Write | (existing hosted lane; #5363 adds a targeted test) | ValueError → fail-closed 500; partial write disclosed | (a) partial write (orphan successor); (b) deterministic retry failure with misleading retry advice |
| 4 | `commit_ops.apply_supersessions` (ingest) | DB | Write | pytest integration (test 15) | warn+skip (fail-open, documented) | (a) silent skip of a previously-succeeding supersession; (b) predecessor left live with no CORRECTS |
| 5 | `promote_point` internal supersede | DB | Write | **no test here** — the swallow is unpinned; recorded in #5365 | ValueError → warning | (a) silent skip of a promotion supersede; (b) orphaned successor left live |
| 6 | rebuild fold | DB replay | Read | pytest integration (existing) | replay copies `valid_to` verbatim | (a) a pre-fix journal re-materialises an inverted window (#5361); (b) no live-vs-rebuilt audit signal exists for the replayed inversion |
| 7 | `mining._temporal_wire` (bulk window-start post-pass) | DB | Write | (non-SDK producer; no test here — #5359) | `validFrom` stamped from session date / ingest clock | (a) a start stamped after an already-written `validTo`; (b) wall-clock fallback when the session carries no date |

### Pattern Research

> **Findings date:** 2026-09-25
> **Search tool:** `web_search` with `model="sonar"` — **rung 2**; rung 1 (`mcp_load
> seo-intelligence`) is UNAVAILABLE on this machine ("Unknown MCP server 'seo-intelligence'"). The
> controller also used `web_search` directly instead of routing through the `research` skill — a
> process deviation recorded here rather than hidden; the queries were run with the cheapest model
> named explicitly.
>
> - **canonical** — IBM DB2 `BUSINESS_TIME`: defining the period "automatically creates an implicit
>   check constraint ensuring the period end is greater than the period start"; `BUSINESS_TIME
>   WITHOUT OVERLAPS` forbids overlapping periods (ibm.com/docs/db2). SQL:2011 / Postgres exclusion
>   constraints reject inverted periods (`PERIOD` requires `start <= end`).
> - **competitor-precedent** — bitemporal products (Progress, `pg_bitemporal`) require
>   non-overlapping valid-time ranges; a period's end never precedes its start.
> - **pitfalls** — bitemporal practice models a *retroactive* correction as its own operation
>   (Fowler, *RetroactiveEvent*; branch point + recompute), NOT as an ordinary forward supersede —
>   which is precisely why a retroactive successor does not fit `supersede_point` and must be
>   refused rather than silently stamped.

## 6. Deferred findings — filed as separate issues (with evidence)

| Issue | Root (distinct) | Evidence |
|---|---|---|
| **#5358** | `invalidate_point` stamps `validTo = now` unguarded → same inversion for a future-dated predecessor | `sdk.py:5880-5885`; ONTOLOGY §4.7 documents the behaviour → needs an owner ruling (refuse vs clamp vs `retract_point`) |
| **#5359** | `create_point`/`update_point` accept a caller-supplied inverted `validFrom`/`validTo` pair, and `mining._temporal_wire` stamps `validFrom` unchecked | `sdk.py:3459-3483`, `5653-5677`; `mining.py:468` |
| **#5360** | an unparseable/unorderable resolved end is persisted on the **no-kwarg** path **and** on the undated-successor-kwarg path (the #3980 guard cannot fire when `stored_vf is None`) | `sdk.py:6088-6089`, `6086-6087`; `_created_sort_key("not-a-date")` / `str(1781049600.0)` |
| **#5361** | read path / audit cannot distinguish an already-persisted inverted window from honest absence | `sdk.py:15524-15529`; `audit_graph`/`check_structure` have zero window-ordering checks; folds replay verbatim |
| **#5362** | retroactive/backdated correction as a first-class operation (foreclosed by the refusal) | external precedent (Fowler, *RetroactiveEvent*) |
| **#5363** | hosted commit maps the refusal to a generic 500 (no 422; misleading retry advice) **and** leaves a partial write (orphan successor) that a retry cannot complete | `hosted_api.py:11680-11708`, `11964-11972` |
| **#5365** | `commit_ops.apply_supersessions` **and** `promote_point` turn the refusal into a silent fail-open skip of a previously-succeeding (corrupting) retroactive supersession | `commit_ops.py:537-541`; `sdk.py:6681-6686` |
| **#5374** | no shared `validate_validity_window(valid_from, valid_to)` declaration binds the eight window writers — the per-writer fixes are drivers without a contract | `tortoise/commit_schema.py:332-366` (`validate_span`) is the ONE-HOME precedent; no `validate_validity_window` exists |
| **#5361 owner** | the retrofit audit for already-corrupted windows has no owner — an owner note is posted on #5361 and the plan requires the lane assignment before the fix lands | `#5361` |
| **#5375** | `_now_iso` is defined twice in `sdk.py` (2041 and 21906) with **no** consistency assertion, and its "shared by write paths" docstring is false (≈4 of ~30 now-sites route through it) | AST check of `tortoise/sdk.py`; `grep -c 'datetime.now(timezone.utc).isoformat()' tortoise/sdk.py` → 29 |

## 7. Owner question (recorded on #4021, protocol shape — does not block)

- **Context:** #4021's body names an A/B product fork (refuse vs normalise-and-document).
- **Options:** (A) refuse a successor starting before the predecessor (chosen); (B) normalise the
  predecessor window (clamp) and document retroactive semantics in ONTOLOGY §4.7.
- **Analysis:** (A) extends the recorded #3980 fail-closed decision and matches interval-integrity
  convergence (DB2/SQL:2011/Postgres reject inverted periods); it forecloses backdating under this
  verb (capability D filed separately). (B) invents a clamped-window semantics with no evidence,
  and creates a spurious `ambiguous` at the shared instant. (A) does not contradict any recorded
  decision (contradiction test run first).
- **Recommendation:** (A) refuse — shipped fail-closed; (B) remains available as a follow-up if the
  owner rules for it.

**Recorded on #4021** (not merely intended): the protocol block + the `OVERRIDES:` line are posted at
https://github.com/daniel-ospina/tortoise/issues/4021#issuecomment-5839985638, with the reproduction
and the corrected-harm note at
https://github.com/daniel-ospina/tortoise/issues/4021#issuecomment-5839986966.

## 8. Acceptance Criteria

1. `supersede_point` raises `ValueError` containing `"inverted window"` when the resolved end is
   strictly before the predecessor's `validFrom`, on both required write paths.
2. The refusal is fail-closed: zero graph mutation, zero `PointSuperseded` event, zero CORRECTS.
3. Equality and format-only-equal values are allowed; only strict `<` is refused.
4. Presence predicate `old_vf is not None` (falsey-but-present `0`/`""` are real starts).
5. **The full selected test lane is green** (not just `tests/test_validity_windows.py`) — the refusal fires on a previously-accepted input and the writer is called from ~40 test files; the #3980 guard and resolution order remain byte-equivalent for every previously accepted/refused input.
6. MCP `dry_run=True` refuses identically (verdict equal; message equal when the window is the only fault) and changes nothing; the refusal message **template** is scrub-stable (`_scrub_error(msg) == msg`).
7. Tests fail at the pre-fix head, pass after; the mutation proof names the exact tests that RED on the reverted branch (`1, 3, 5, 6, 8, 11, 12, 13, 14, 15, 17`); test 16 pins the `str()`-measure (not the refusal).
12. Every declared adversarial threat class B1–B7 is covered by a specific named test that REDs when the guard is absent (B3 → 16; B7 → 13/14/17); B8 is out of scope with its residual recorded.
8. A read-path assertion pins that a **refused** retroactive attempt leaves the predecessor window intact and non-inverted (test 11), and that a legitimate forward supersede is non-inverted (test 10).
9. ONTOLOGY §4.1/§4.7 + changelog v3.18 state the precondition, the rationale, the scope boundary, and **correct** the existing "never a gap" overstatement.
10. `tools/surface-guard.py` and `tools/surface_manifest.py check` green (no MCP/SDK surface change).
11. Every declared adversarial threat class B1–B7 is covered by a named test; B8 is stated out of scope with its residual recorded.
