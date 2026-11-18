# Capstone #13038 — before / after #13302

**What this is:** the same six-question assessment run against the same frozen fixture
(`../narrative.json`, frozen 2026-11-16) and the same instrument, scored each time by a **fresh
reader** (`pi -p --no-session --no-tools`) that was given the narrative as its key and the
product's own payloads as its evidence — never this lane's summary of them.

| run | product | reader | scoreboard |
|---|---|---|---|
| **before** | `bf3a7b8e77bf` (the parent commit, worktree unmodified) | fresh, 81.0 s | **3 right · 3 wrong** — Q1, Q5, Q6 wrong |
| **after** (`scoreboard-after.md`) | `353c225703af` (#13302's code, after the review round) | fresh, 92.9 s | **5 right · 1 wrong** — Q1 wrong |
| **after, second run** (`scoreboard-after-run2.md`) | the same commit | fresh, 97.2 s | **6 right · 0 wrong** |

**Q5** (*"what was planned and never happened?"*) and **Q6** (*"has anything we relied on been
withdrawn?"*) are `right` in **both** post-change runs and `wrong` in the before run: that flip is
deterministic across the runs taken. **Q2–Q4 are `right` throughout.**

⚠️ **Q1 is JUDGE-VARIABLE, and the two after boards are here because of it — do not read the pair as
a discrepancy.** Q1's recipe is *untouched* by #13302 (it asks the same `search` calls; its payload
differs between the two runs only in ids and timestamps), and the two runs scored it `wrong` and
`right` respectively. Its cause is *reach*, not lifecycle — an `entity_type`-scoped read never names
an Object commitment unless the caller guesses its name (Q2's own call, which names the plan, does
return it) — and that is a different gap with no ticket of its own yet. Both boards are committed so
a reader can see the variance rather than a single flattering sample; the claim this change supports
is **"Q5 and Q6 flip, Q1–Q4 do not regress"**, not "everything is right now".

## How each artifact was produced

- `scoreboard-after.md` / `run-after.json` and `scoreboard-after-run2.md` / `run-after-run2.json` —
  **one complete run each**, end to end:
  `bash docs/evidence/13038-capstone/rerun.sh --judge fresh --out <scratch> --verdicts-out <file>`
  at `353c225703af`. Each header carries that run's own durations (131.8 s / 148.8 s ingest+ask,
  92.9 s / 97.2 s reader).
- `scoreboard-before.md` / `run-before.json` — the ingest + ask half was produced by the SAME
  command against the unmodified tree at `bf3a7b8e77bf`; that run's own reader was refused by this
  box's spawn gate (`[pi-gate] refusal #5 … re-dispatch when the 1-min load falls below 100`,
  measured at load 129), so its answers were scored afterwards from **that run's own packet**
  (`tools/capstone_state_value.py judge --packet <that packet> --out <scratch>`). Consequence,
  stated rather than hidden: the verdicts are that run's own payloads judged by a fresh reader, but
  `header.duration_s` there is the **ingest + ask** portion (99.8 s), because `judge` rebuilds the
  header from the packet, which is frozen before the reader runs. That is the lower-fidelity mode
  `../README.md` refuses for the bundle itself — which is why these boards live in this
  subdirectory and the bundle's own four files are untouched.
- `verdicts-*.json` — what each reader returned, in the shape `--verdicts` reads
  (`--verdicts-out`), so any board can be re-scored without a model:

  ```bash
  uv run python tools/capstone_state_value.py judge \
      --packet <a run's judge-packet.json> --verdicts verdicts-after.json --out /tmp/rerender
  ```

Every run used a private throwaway FalkorDB (`tools/test_lane.py`), a fresh graph namespace, and the
product's own write doors for the ingest; nothing here reaches into a graph a human relies on, and
the store URI is recorded **redacted** (`docker://<redacted>@…`) in every header.

## What the rows that moved actually show

**Q6 before:** `search('has anything we relied on been withdrawn', point)` → 2 rows, **0 of 2**
sharing a word with the question, `score 0.0`, `match: 'tfidf'` — a degraded whole-corpus ranking
that *looked* like an answer. The retracted claim appeared only under `include_terminal`, i.e. only
for a caller who already knew to force terminal scope **and** already knew the by-name query.

**Q6 after:** the same question is answered by `ended_scope`, which enumerates the ended population
and serves `{status: "retracted", endedAt: …}` — no id, no name, no per-call flag. The board renders
that call as `search(None, point, ended_scope)`, so it cannot be mistaken for a plain default read.

**Q5 before:** the question-shaped search returned 0 rows, and the cancelled plan was reachable only
by its id (`read_entity` → `stage='cancelled' slot='terminal' isClosed=True`).

**Q5 after:** `ended_objects` serves the cancelled plan with
`[stage=terminal/withdrawn, no linked event, endedAt=…]` — ended, and nothing was ever recorded as
having happened — while the still-pending launch plan is absent from the population entirely (it
has not ended). The stage *name* is never needed: the slot decides.
