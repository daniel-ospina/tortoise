---
title: "T11 — Live-corpus double-index smoke over the real session corpus (NO-NETWORK)"
type: operations
domain: capability
doc_status: live
subjects.team: epistemic-team
created: 2026-10-11
aboutSubjects: tortoise
aboutObjects: tortoise
---

# T11 — Live-corpus double-index smoke (epic #900 §8.6, issue #1037)

Date: 2026-10-11 · Host: macOS 26.6.2, 8 CPU, load 18.6/22.4/23.0 (fleet busy —
14 concurrent `pytest` processes from other lanes) · Engine: `falkordb/falkordb-server:v4.20.6`
(graph module `ver=42006` — the CI pin) · Corpus: **5,411 real session files**
(`~/.tortoise/docs/conversations`, 39 MB, frozen for the run)

**Verdict: idempotency proven at production scale. All four SC3 indicators PASS.**
Two defects surfaced by the run are filed separately (#8083, #8084) — neither is
an idempotency failure.

## 1 — Indicators

| # | Indicator | Measured | Verdict |
|---|---|---|---|
| 1 | run-2 adds **zero new nodes/edges** | post-run-1 == post-run-2: **10,934 nodes / 5,610 edges**, identical | **PASS** |
| 2 | final draining run `skipped + updated + failed == file_count` with `updated == 0`, ≤3 attempts | drain: `5411 + 0 + 0 == 5411`, `updated == 0`; **2 attempts** (cold + drain) | **PASS** |
| 3 | coherence checkpoints: per-eventId uniqueness, duplicate-url empty, hash-pair sweep == 0, health monotone + post-drain stable | forks `0`, duplicate urls `0`, hash-pair `0`, REQUIRED `0`; health `up_to_date 0 → 2364`, `unindexed 5404 → 3040`, `stale 0`, stable across runs | **PASS** |
| 4 | `failed` set + repaired-unit list + duration + conditions recorded | §4/§5 below | **PASS** |

## 2 — Conditions

- **Mode:** `TORTOISE_INDEX_NO_NETWORK=1`, no `--metadata` (`extract_metadata=False`);
  the canonical operator path `python -m tortoise index directory <corpus> --corpus-name <n>`
  (the same entry point epic #900 T8/#1044 migrated the session-end hook onto).
- **The real corpus is present on this machine now.** The capstone report for
  #1031 (2026-08-14) recorded T11 as "BLOCKED (environment) — the real 4,190-file
  session corpus is NOT present on this machine". Re-measured 2026-10-11:
  `~/.tortoise/docs/conversations` holds **5,411** `*.md` files (39 MB) — the
  blocker is gone. (The venue is still growing at ~10–24 files/hour.)
- **Quiescent window by snapshot:** a growing corpus cannot be quiescent, so the
  corpus was copied byte-for-byte (`cp -a`) and the smoke ran against the frozen
  copy; `file_count` is therefore constant across runs, which is what makes
  "run-2 zero new nodes/edges" a measurement rather than a race.
  - manifest: `find . -type f -name '*.md' | sort | xargs shasum | shasum` →
    **`e8cb42169900323d909767bac34ba04c8ab04ea5`** (5,411 files)
- **Engine:** a private lane container (`tools/test_lane.py up`), pinned with
  `TORTOISE_TEST_LANE_IMAGE=falkordb/falkordb-server:v4.20.6` — the
  `.github/actions/falkordb-provision` default, i.e. the engine the epic's E2E
  suite runs on. A **fresh empty graph** per leg was used as the baseline so the
  node/edge deltas are exact; the production embedded store
  (`~/.tortoise/tortoise.db`) is single-writer and was held by a live process
  (`EmbeddedStoreBusyError`, holder pid 42801), so it cannot host the run.
- **Measurement queries** are the epic's own harness pins, copied verbatim:
  `_hash_pair_sweep` and the D10-aware `_required_sweep` from
  `tests/test_index_directory.py:785`, per-eventId uniqueness / duplicate-url from
  the §7 checkpoint set (CYCLE-21 corrected fork detector: per-eventId uniqueness,
  not global equality): `MATCH (e:Event) WITH e.eventId AS eid, count(*) AS c WHERE c > 1 RETURN count(*)`.

## 3 — Leg A — clean double-index (graph `t11_clean`, 07:47–07:56Z)

| step | duration | report |
|---|---|---|
| checkpoint pre | — | nodes 1, edges 0, Sources 0, Events 0 |
| **run 1 (cold)** | **315 s** | `file_count 5411, indexed 5411, updated 0, skipped 0, failed 0, aborted 0, ignored 0`; `by_kind {document 3040, agentSession 2371}` |
| checkpoint post-run-1 | — | **10,934 nodes / 5,610 edges** |
| **run 2 (drain)** | **174 s** | `file_count 5411, indexed 0, updated 0, skipped 5411, failed 0, aborted 0, ignored 0` |
| checkpoint post-run-2 | — | **10,934 nodes / 5,610 edges — zero delta** |

Graph accounting (measured, fully closed):

```text
nodes 10,934 = Source 8,451 + Event 2,364 + Object 118 + Meta 1
edges  5,610 = Source→Source 3,040 + Source→Event 2,364 + Event→Object 206
Sources 8,451 = 5,411 corpus (one per file) + 3,040 document (documentKind: conversation 3,038, brief 2)
```

`by_kind.agentSession (2,371) − Events (2,364) = 7` = the non-primary copies of the
6 duplicated sessionIds the corpus carries — surfaced by the harness and skipped by
the pinned primary-election, never silently re-indexed (`MATCH (s:Source) WHERE
s.documentKind IS NULL AND NOT (s)-[:references]->() RETURN count(*)` → **7**).

Throughput at this scale: **0.058 s/file** cold (315 s for 5,411 files), 0.032 s/file
draining.

## 4 — Leg B — the convergence loop (graph `t11_smoke`, 07:24–07:41Z)

Run independently against a second fresh graph, to exercise the bounded convergence
loop the issue calls for — and it produced the journal's most useful datapoint: a
**transient per-run failure that the loop repaired**, exactly the case the
"≤3 convergence attempts" and "repaired-unit list" indicators exist to capture.

| step | duration | report | nodes / edges |
|---|---|---|---|
| checkpoint pre | — | — | 1 / 0 |
| run 1 (cold) | 435 s | `indexed 5410, updated 0, skipped 0, **failed 1**` | 10,933 / 5,609 |
| run 2 (drain) | 225 s | `updated 1, skipped 5410, failed 0` | 10,934 / 5,610 |
| run 3 (stability) | 126 s | `updated 0, skipped 5411, failed 0` | 10,934 / 5,610 |
| run 4 (confirmation; journal key `4-repair`) | 143 s | `updated 0, skipped 5411, failed 0` | 10,934 / 5,610 |

**Failed set (run 1):**

| file | error | retryable | cause |
|---|---|---|---|
| `2026-07-30-15715f36.md` | `db write failure (db): Query timed out` | `true` | `db` |

**Repaired-unit list:** run 2's `updated == 1` is that file; the `+1 node / +1 edge`
delta between post-run-1 and post-run-2 is that file's `Source` (it is
`document`-classified) and its corpus→document `references` edge. Per-file isolation
held: the failing file never aborted the run (`aborted 0`) and did not perturb any
other file's outcome. It is the **stability and confirmation runs** (3 and 4 — journal
keys `3-stability`, `4-repair`) that satisfy the arithmetic
(`skipped + updated + failed == 5411`, `updated == 0`); run 2 is the repair run
(`updated 1`) and is what makes the next two **delta-zero** — convergence reached
inside **3 attempts** (cold, drain, stability).

Transient DB timeouts at this scale: **1 per 10,822 cold-run file-indexings
(0.009 %)** — one `cause=db`, `retryable=true` failure, in Leg B run 1; it is surfaced
in the journal rather than swallowed, and the next run repaired it. Consistent with
the host load (18.6–23.0 on 8 CPUs, 14 concurrent pytest processes).

## 5 — Coherence checkpoints (indicator 3)

| checkpoint | pre-run | post-run-1 | post-drain | post-drain stability |
|---|---|---|---|---|
| nodes / edges | 1 / 0 | 10,934 / 5,610 | 10,934 / 5,610 | 10,934 / 5,610 |
| `Event` count / distinct `eventId` | 0 / 0 | 2,364 / 2,364 | 2,364 / 2,364 | 2,364 / 2,364 |
| **per-eventId forks** (`count > 1`) | 0 | **0** | **0** | **0** |
| Sources / distinct urls | 0 / 0 | 8,451 / 8,451 | 8,451 / 8,451 | 8,451 / 8,451 |
| **duplicate-url** (`count > 1`) | 0 | **0** | **0** | **0** |
| **hash-pair sweep** (`s.contentHash <> e.file_hash`) | 0 | **0** | **0** | **0** |
| **REQUIRED sweep** (D10-aware) | 0 | **0** | **0** | **0** |
| health `matched` / `up_to_date` | 0 / 0 | 2,364 / 2,364 | 2,364 / 2,364 | 2,364 / 2,364 |
| health `stale` | 0 | 0 | 0 | 0 |
| health `unindexed` | 5,404 | 3,040 | 3,040 | 3,040 |
| health `duplicates` (sessionIds) | 6 | 6 | 6 | 6 |

**Health buckets are monotone-improving and then stable**: `up_to_date 0 → 2,364`
(never decreases), `unindexed 5,404 → 3,040` (never increases), `stale 0` and
`duplicates 6` unchanged from pre-run to post-drain — the "buckets legitimately
change on run 1" behaviour CYCLE-21 warned about, then flat.

**One correction made during the run (recorded for honesty):** the first pass used
the pre-D10 `_required_sweep` clause (`test_backfill_sources.py:57`) and read
`3,040` violations. That is a **stale query, not a defect** — D10 (ONTOLOGY v3.15
§4.4) makes a document a `:Source` keyed by `url` that deliberately does *not*
carry `sourceKind`/`contentHash` (§4.6). The epic-owned D10-aware pin
(`tests/test_index_directory.py:785`, families split, never relaxed) reads **0**.
The pre-D10 clause is reported above as the raw `3,040` datapoint so the two are
never confused again.

## 6 — Findings (filed, not absorbed)

| finding | issue | one line |
|---|---|---|
| FalkorDB engine 6.x panics on a **read-only** aggregate whose `RETURN` names a property absent from the rows — the server exits and the container's graphs are lost | **#8083** | Cost this smoke its first full 5,411-file cold run (an ad-hoc checkpoint query killed the lane container). A/B: `v4.20.6` fine, `6.0.1` + `6.0.2` exit 1. `tools/test_lane.py:126` defaults to the panicking image; that site is outside #6386's fix scope. |
| `session_index_health` / `tortoise index sessions` report **3,040 of 5,411** corpus files `unindexed` forever, and the re-index remedy mints orphan `AgentSession` Events | **#8084** | The corpus is 56 % sessionId-less legacy conversations; the pinned classifier (`file_indexer.py:469` rule 5) makes them `document`s, which the `AgentSession`-only health surface can never match. Fix option 1 (classification-aware health) recommended; option 2 (classify `type: conversation` as a session) reopens the pinned §6.2 rule and needs an owner decision. |

**Cross-issue note:** the smoke did **not** reproduce a hash-idempotency failure of any
kind — `updated` never went non-zero except for the one retryable DB timeout, no
duplicate url, no forked eventId, no hash-pair drift, no growth after convergence.

## 7 — Residuals (not defects, recorded)

1. **Transient `db` timeouts are rare at this scale** (1 in 10,822 cold-run
   file-indexings). The one occurrence was reported per-file with `retryable: true`
   and repaired by the next run; a run-level retry inside a single invocation does
   not exist, so an operator needs a second run. The host load was 18.6–23.0 on
   8 CPUs with 14 concurrent `pytest` processes, so a load correlation remains
   plausible but is not established by a single observation.
2. **`session_index_health.unindexed` is not an idempotency signal** on a corpus that
   mixes sessions and documents — see #8084. Do not read a non-zero `unindexed` here
   as "the index did not converge".
3. The smoke ran against a **snapshot** (manifest above) because the live corpus grows
   hourly; a live-dir run cannot show "run-2 zero new nodes/edges" by construction.

## 8 — Reproduction

```bash
# frozen corpus (the real corpus, snapshot; 5,411 files in this run)
mkdir -p /tmp/t11-live
cp -a ~/.tortoise/docs/conversations /tmp/t11-live/corpus-frozen
( cd /tmp/t11-live/corpus-frozen && find . -type f -name '*.md' | sort | xargs shasum | shasum )

# private lane container on the CI-pinned engine
TORTOISE_TEST_LANE_IMAGE=falkordb/falkordb-server:v4.20.6 \
  uv run python tools/test_lane.py up --graph t11_clean      # -> export TORTOISE_DB_URI=...

# cold run + draining run (NO-NETWORK mode)
export TORTOISE_INDEX_NO_NETWORK=1
.venv/bin/python -m tortoise index directory /tmp/t11-live/corpus-frozen --corpus-name t11_clean
.venv/bin/python -m tortoise index directory /tmp/t11-live/corpus-frozen --corpus-name t11_clean

# coherence checkpoints (read-only; the epic's own pins)
TORTOISE_DB_URI='docker://:@127.0.0.1:<port>/t11_clean' .venv/bin/python - <<'EOF'
from tortoise.sdk import TortoiseSDK
g = TortoiseSDK()._get_proj().g
q = lambda c: g.query(c).result_set[0][0]
print('nodes', q('MATCH (n) RETURN count(n)'), 'edges', q('MATCH ()-[r]->() RETURN count(r)'))
print('event_id_forks', q('MATCH (e:Event) WITH e.eventId AS eid, count(*) AS c WHERE c > 1 RETURN count(*)'))
print('duplicate_urls', q('MATCH (s:Source) WITH s.url AS u, count(*) AS c WHERE c > 1 RETURN count(*)'))
print('hash_pair_sweep', q('MATCH (s:Source)-[:references]->(e:Event) WHERE s.contentHash <> e.file_hash RETURN count(*)'))
print('required_sweep(D10)', q("MATCH (s:Source) WHERE (s.documentKind IS NULL AND (s.url IS NULL OR s.url='' OR s.sourceKind IS NULL OR s.contentHash IS NULL OR s.contentHash='' OR s.ingestedAt IS NULL)) OR (s.documentKind IS NOT NULL AND (s.url IS NULL OR s.url='' OR s.ingestedAt IS NULL)) RETURN count(s)"))
print('health', TortoiseSDK().session_index_health('/tmp/t11-live/corpus-frozen'))
EOF
```

Raw journal committed next to this report as **`t11-live-corpus-smoke.json`** —
per-run report JSON, the interleaved checkpoint JSONs, and the durations for both
legs; every table above is transcribed from it verbatim. (The lane container was
brought down after the run; the frozen corpus copy lives only in `/tmp`.)
