---
title: "#7951 increment 1 — the outbox ordering on `create_point` (evidence)"
type: evidence
domain: platform
doc_status: final
created: 2026-10-14
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "#7951, #7950, STORAGE-ARCHITECTURE.md §17.1, tortoise/sdk.py create_point, FalkorDB cache"
---

# #7951 increment 1 — the outbox ordering on `create_point`

**Measured at:** branch `fix/7951-atomic-write-outbox` off `origin/main` `72e42deea`, 2026-10-14;
the probe output and the test count re-measured 2026-10-15 at the #8697 review-fix head. Embedded
lane (`TORTOISE_DB_URI` unset, `TORTOISE_TEST_CARVE_OUT=1`) — no server needed, so the demonstration
is reproducible by any lane.

**What this proves, and what it does not.** It proves the §17.1 **ordering** on the highest-traffic
write path (`create_point`): the journal record is appended before any Point cache write, and the
committed record is what a replay folds. It does **not** claim §17.1 is closed — the raw handle is
still open (§4 below), and the record projection + `last_applied_seq` watermark (mechanism 2) are not
built. The plan of record is `docs/plans/2026-10-14-7951-atomic-write-outbox.md`.

## The demonstration

```console
$ env -u TORTOISE_DB_URI TORTOISE_TEST_CARVE_OUT=1 \
    uv run python docs/evidence/7951-create-point-outbox/probes/order_probe.py
journal_backed (guard armed): True
create_point order:              ['journal-PointAdded', 'cache-Point-write']
ORDERING VERDICT: JOURNAL-FIRST
journal PointAdded records:      1
rebuild_all materialised Point:  1
live/replay property map match:  True
live/replay edge set match:      True (2 edges)
REPLAY VERDICT: RECORD REPRODUCES THE LIVE NODE
failed cache write raised:        RuntimeError
record committed despite failure: 1
replay healed the cache:         1
COMMIT VERDICT: COMMITTED + REPLAY-HEALABLE
RAW HANDLE LANE (proj.g.query):  SUCCEEDED, rows = [['via-raw-handle']]
RESIDUAL: the raw handle is NOT closed — recorded on #7951 (the OVERRIDES: line); increment 1 routes one door, not 288.
```

**Raw output:** `order-probe.log` (verbatim; the shipped live engine logs `#4997`/pack-registry
advisories which the headline block omits). Exit code `0`.

## What each line means

| line | it is the failing criterion of… | how it would fail |
|---|---|---|
| `order: ['journal-PointAdded', 'cache-Point-write']` | S1's ordering | on `origin/main` the order is reversed (`cache` first) — the capstone's `test_create_point_is_apply_first_a_recorded_deviation` pinned exactly that |
| `REPLAY VERDICT: RECORD REPRODUCES THE LIVE NODE` | `derived = replay(journal)` | the probe compares the live and replayed node as a full property MAP **and** the outgoing EDGE SET `(type, target, edge-properties)` (`live/replay property map match` / `live/replay edge set match`), mirroring `tests/test_7951_create_point_outbox.py` T3b — a presence COUNT alone cannot fail on a property or edge mismatch, so it is not this criterion |
| `COMMIT VERDICT: COMMITTED + REPLAY-HEALABLE` | §17.1's crash window | with the cache write forced to fail, the record still stands and a later `rebuild_all` materialises the Point. On `origin/main` the append came *after* the write, so a failed write left **no record** — the write was gone with no repair path |

## Tests (the gates those lines are drawn from)

```console
$ env -u TORTOISE_DB_URI TORTOISE_TEST_CARVE_OUT=1 uv run pytest \
    tests/test_7951_create_point_outbox.py tests/test_7209_write_landing.py \
    tests/test_7951_enforce_atomic_write.py -q
40 passed
```

`tests/test_7951_create_point_outbox.py` carries the threat rows declared in the plan (T1–T8; T3
splits into T3a payload completeness / T3b live-vs-replay maps + edge set): T1 ordering, T2
failed-append, T4 unknown form, T5 commit survives a failed cache write, T6 one record, T7
payload-is-the-record, T8 record-states-what-the-store-holds.

## The residual, shown rather than hidden

`RAW HANDLE LANE (proj.g.query): SUCCEEDED` — the raw handle still writes the cache directly. That is
the recorded `OVERRIDES:` departure on `#7951` (a blanket refusal is **rejected**: the handle is the
product's own live write path). Increment 1 routes **one** door; the remaining raw-handle sites are
the next increments (plan §7). The status counts also drift per measurement (387 → 420 → 441 → 442 →
288 raw-handle), so the census is a reading of a moving surface, not a constant.

## Residuals declared with this evidence

1. The `DEDUP-HIT` path of `create_point` routes to `update_point`, still apply-first (plan §6.3).
2. The cache is written by the `CREATE`, not by `rebuild_from_record`; mechanism 2 (the watermark) is
   not built (plan §6.2).
3. The pre-append control-plane writes (`:EpMeta`, `:GraphEvent`) and the `:Source` adopt-on-touch are
   named in plan §3.
4. A pre-existing replay-parity defect: the fold drops `ep_alpha`/`ep_beta`/`baseline_set`/
   `baseline_source` (`_POINT_DENY`, no SET clause), so a `rebuild_all` does not restore the `#2199`
   baseline. **Not introduced here** — it is why replacing the `CREATE` with the fold was rejected for
   this increment (plan §6.6, §8.2).
5. The append-failure arm leaves an orphan `:GraphEvent` (the graph event store is written before the
   JSONL append inside `_emit_event`), so `WriteNotLandedError(record_committed=False)` no longer
   claims "nothing was journaled" — it names the orphan event (plan §6.7; filed **#8696**).
