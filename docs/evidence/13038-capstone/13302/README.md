# Capstone #13038 — before / after #13302

**What this is:** the same six-question assessment, run twice on this branch, at the same
fixture (`../narrative.json`, frozen 2026-11-16) and the same instrument, scored each time by a
**fresh reader** (`pi -p --no-session --no-tools`) that saw the narrative as its key and the
product's payloads as its evidence — never this lane's summary of them.

| run | product | reader | scoreboard |
|---|---|---|---|
| **before** | `bf3a7b8e77bf` (the parent commit, worktree unmodified) | fresh, 81.0 s | **3 right · 3 wrong** — Q1, Q5, Q6 wrong |
| **after** | `c8ecf9b37e6f` (#13302's commit) | fresh, 84.5 s | **5 right · 1 wrong** — Q1 wrong |

**Q5** (*"what was planned and never happened?"*) and **Q6** (*"has anything we relied on been
withdrawn?"*) flip to `right`. **Q1–Q4 do not regress**: Q2–Q4 stay `right`, and Q1 stays `wrong`
for its own cause (Q1 is a *reach* gap — an `entity_type`-scoped read never names an Object
commitment unless the caller guesses its name — which is a different issue and is still open).

## How each artifact was produced

- `run-after.json` / `scoreboard-after.md` — **one complete run** end to end:
  `bash docs/evidence/13038-capstone/rerun.sh --judge fresh --out <scratch> --verdicts-out <file>`
  at `c8ecf9b37e6f`. Its header carries that run's own durations (110.2 s ingest + ask, 84.5 s
  reader).
- `run-before.json` / `scoreboard-before.md` — the ingest + ask half was produced by the SAME
  command against the unmodified tree at `bf3a7b8e77bf`; that run's own reader was refused by this
  box's spawn gate (`[pi-gate] refusal #5 … re-dispatch when the 1-min load falls below 100`,
  measured at load 129), so the answers were scored afterwards from **that run's own packet**:
  `tools/capstone_state_value.py judge --packet <that run's judge-packet.json> --out <scratch>`.
  Consequence, stated rather than hidden: the verdicts are this run's payloads judged by a fresh
  reader, but `header.duration_s` here is the **ingest + ask** portion (99.8 s), because `judge`
  rebuilds the header from the packet, which is frozen before the reader runs. This is the
  lower-fidelity mode `../README.md` refuses for the bundle itself — which is why these boards
  live in this subdirectory and the bundle's four files are untouched.
- `verdicts-before.json` / `verdicts-after.json` — what each reader returned, in the shape
  `--verdicts` reads (`--verdicts-out`). Re-scorable:
  `uv run python tools/capstone_state_value.py judge \
     --packet <a run's judge-packet.json> --verdicts verdicts-after.json --out /tmp/rerender`

Every run used a private throwaway FalkorDB (`tools/test_lane.py`), a fresh graph namespace, and
the product's own write doors for the ingest. Nothing here reaches into a graph a human relies on.
The store URI is recorded **redacted** (`docker://<redacted>@…`) in both headers.

## What the two rows that moved actually show

**Q6 before:** `search('has anything we relied on been withdrawn', point)` → 2 rows, **0 of 2**
sharing a word with the question, `score 0.0`, `match: 'tfidf'` — a degraded whole-corpus ranking
that looked like an answer. The retracted claim appeared only under `include_terminal`, i.e. only
for a caller who already knew to force terminal scope **and** already knew the by-name query.

**Q6 after:** the same question is answered by `ended_scope`, which enumerates the ended
population and serves `{status: "retracted", endedAt: …}` — no id, no name, no per-call flag.

**Q5 before:** the question-shaped search returned 0 rows, and the cancelled plan was reachable
only by its id (`read_entity` → `stage='cancelled' slot='terminal' isClosed=True`).

**Q5 after:** `ended_objects` serves the cancelled plan with `stage {slot: "terminal", outcome:
"withdrawn"}`, `isClosed: true` and **`linkedEvents: []`** — ended, and nothing was ever recorded
as having happened — while the still-pending launch plan is absent from the population entirely
(it has not ended). The stage *name* is never needed: the slot decides.
