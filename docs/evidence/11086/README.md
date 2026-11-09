---
title: "#11086 — the measured write-rate effect of the two gates, per gate"
type: evidence
domain: platform
doc_status: live
created: 2026-11-09
subjects.team: epistemic-team
aboutSubjects: Tortoise vector layer, EMBED gate, VALUE gate, S2.2b VET
aboutObjects: "#11086, #5008, #4899, #11445, STORAGE-ARCHITECTURE.md §12.2a, EXTRACTOR-V4-ARCHITECTURE.md §4.3 G2"
---

# `#11086` — the measured write-rate effect of the two gates, per gate

## ⛔ This measurement is POST-ARMING, and the ordering `#5008` chose has already been inverted

`#5008` decided the ordering explicitly: *"the rate effect is measurable before it is
trusted."* `STORAGE-ARCHITECTURE.md` recorded the gate as *"flag-gated **off by default**
(`TORTOISE_EMBED_GATE`), so the graph is unchanged until the rate effect is measured."*

**PR #11445 turned `TORTOISE_EMBED_GATE` ON in `.env.example`, and the measurement was never taken.**
So the graph was no longer unchanged, and the measurement that was supposed to precede the change did
not precede it. This record is therefore **after the fact**. That is worse than the ordered
measurement and it is what was still possible; it is stated here rather than presented as if the
order had held. The same inversion is now written at the gate's own flag (`tortoise/embed_gate.py`),
in `config/v4-mechanisms.yml`, and at `STORAGE-ARCHITECTURE.md` §12.2a's state note, so no reader of
those three surfaces is told the graph is still unchanged.

## The commands, verbatim

```bash
uv run python tools/measure_11086_gate_rate.py            # human table
uv run python tools/measure_11086_gate_rate.py --out docs/evidence/11086/gate-rate.json
```

Both are deterministic: the embedder is stubbed with a content-addressed vector
(`sha256` prefix), so the tool makes **zero model calls and zero network calls**, and
`tests/test_embed_decision_step_11086.py` re-runs it and compares against `gate-rate.json`. A
refusal (exit `2`) writes **no** artifact, so a file left at `--out` after a refusal is the
previous run's.

### The record binds to a tree, and each arm witnesses its own gate

`gate-rate.json` carries a `provenance` block — `head_sha`, `measured_at`, `instrument`.
`measure()` itself is deliberately **pure** (no sha, no clock) so the numbers stay reproducible; the
write path stamps provenance on top, and the reproduction test compares everything **except** that
block while asserting the block is present and well-formed. A quoted "5/9, −4" can therefore be
checked out at a revision.

Each arm also records **its own** arming witness, so neither reading needs an inference:
`predicate_enabled_at_the_enforcement_point` (read from the same predicate the enforcement point
reads, while the arm's flag value is in the environment), `predicate_fired`, and — for the EMBED
arm — `predicate_calls`. In this record: EMBED ON `armed=true fired=4/9`, OFF `armed=false fired=0`;
VALUE ON `enabled=true fired=4`, OFF `enabled=false fired=0`. A gate that was never armed records
`false`/`0`, which is **not** readable as "the gate let everything through" — and an ON arm cannot
report `rows_without_a_vector: 4` with `predicate_fired: 0`.

## What was measured — the two gates, SEPARATELY

§12.2a decides **one rule set, two enforcement points**, each asking its own question:

| gate | question | enforcement point | counted as |
|---|---|---|---|
| `TORTOISE_EMBED_GATE` | does the **written row** carry a vector? | `tortoise/embeddings.py::encode_for_row` | rows for which `encode_for_row` returns a vector |
| `TORTOISE_VALUE_GATE` | does the row **exist** at all? | `tortoise/vet_gate.py::vet_candidates` + `apply_vet` (what `extractor_v2._run_vet_pass` calls) | candidates that survive the S2.2b pass |

They are **not summed**. A single combined number would answer a question neither gate asks.

### The corpus — both directions, on purpose

Nine rows: four **identifier-only** (a UUID, a commit hash, a file path, an `#<n>` reference) and five
prose rows. The corpus is **synthetic and stub-scored** — it is a fixed, hand-written list, and every
vector comes from a content-addressed stub rather than a model — which is what makes the delta
reproducible; it is not a sample of real traffic. Two of the prose rows are the **semantic** members
of the declared ephemeral class —
`"tool workarounds"` and `"sprint mechanics"` — which §12.2a requires the gate to **KEEP**; one is a
bare definite description (`"the admin-merge rail"`), which is VET's question and not this predicate's;
one has an issue number merely **present** inside a claim. A corpus of only identifiers would make any
gate look effective; a corpus of only prose would make any gate look safe.

### Measured effect

| | gate ON | gate OFF | delta |
|---|---|---|---|
| **EMBED** — rows carrying a vector | **5 / 9** | 9 / 9 | **−4** |
| **VALUE** — candidates still existing | **5 / 9** | 9 / 9 | **−4** |

Both gates fire on **exactly** the four identifier-only rows and on nothing else: the prose rows, the
two semantic ephemeral members, the bare definite description and the identifier-inside-a-claim row
all survive both arms. The two gates agree here **because the corpus's identifier-only class is the
intersection** of the two questions — they diverge on a row that is worth KEEPING but not worth
EMBEDDING, and this corpus does not contain one, which is stated rather than glossed over.

### ⚠️ What "gate OFF" means for each arm — the two arms are not the same kind of counterfactual

- **EMBED / OFF is the CODE default, not the shipped one.** `embed_gate.embed_gate_enabled()` is
  `False` unless `TORTOISE_EMBED_GATE` is truthy, so OFF is what a bare checkout does. But a
  deployment built from `.env.example` — or an existing install re-run through
  `graph-scripts/setup.py` step 9, which merges missing keys from the template — is **armed**.
  The shipped state is the **ON** column.
- **VALUE / OFF is not a state any deployment is in.** Since `#10996`,
  `value_gate.value_gate_enabled()` is `is_truthy(os.environ.get(ENV_FLAG, "1"))` — **unset means
  ON**. The OFF arm is a counterfactual produced by setting the documented kill-switch
  (`TORTOISE_VALUE_GATE=0`), not a reachable default. Stating the delta as "-4" without this
  sentence would imply the shipped product loses four of nine rows *today* from this gate alone,
  which is false: it lost them from `#10996`, and this measurement re-confirms the size.

## What this does **not** cover

- **No production rows.** The population is a fixed nine-row corpus, not a graph. The nine-row corpus
  is what makes the delta legible and reproducible; it is not a rate over real traffic. A rate over
  real rows is `#4997`/the capstone's, and no committed artifact carries one for these two gates.
- **No reap.** A row that already holds a vector keeps it (the write clauses PRESERVE on a null
  incoming vector). The gate stops the *next* vector being paid for; reclaiming what is already
  stored is `#4997` and needs a surface approval.
- **No vector index for any label.** That is V1 (`STORAGE-ARCHITECTURE.md` §14.3), DECIDED by
  PR #11481 and owned by `#4333`. Nothing here re-opens it.
