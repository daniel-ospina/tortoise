---
title: "#14343 increment 2 — the S4 embed-list reduction, re-measured on the same instrument, and the size of its gate"
type: evidence
domain: platform
doc_status: final
created: 2026-11-23
subjects.team: epistemic-team
aboutSubjects: extractor v2 S4 prompt embed-list serialization; the #7949 capstone acceptance arm
aboutObjects: "#14343, #14365, #14371, #14295, #14277, #14275, #7949"
---

# The reduction, measured — and why it is not armed

Increment 1 (`#14365`) decomposed the extractor's input constant and **shipped the one
information-neutral lever disarmed**: `TORTOISE_S4_EMBED_JSON=compact` switches S4's
embed-list region from `json.dumps(..., indent=1)` to compact separators. This file is
increment 2's record. It does four things and claims nothing more:

1. **re-measures the lever on the same instrument** the decomposition used, from the same
   real emissions — the number below is derived from this run, not inherited from the
   bundle's prose (§1–§2);
2. **measures two things the decomposition explicitly declared unmeasured** — the emission's
   per-field partition, and a keys-declared-once render of the same list (§3);
3. **sizes the second lever** the decomposition names (`S1's story` paid three times,
   `S2's embed list` paid twice) as a *filled-in* claim rather than a slogan (§4);
4. **states the gate, exactly** — what arming the lever requires, and why this lane did not
   arm it (§5).

⛔ **This lane shipped no production change, and that is the finding, not an omission.**
Arming the lever makes the compact render the layout every production S4 prompt carries;
`#14343`'s own rule is that a reduction which costs extraction quality is a *worse*
decision. The bars that would show the quality hold (`#7949` E1/E2/E3/E4) **cannot run on
this box** (§5.1), so the lever stays disarmed. A pricing decision is out of scope
(`#14275`, owner) and no pricing level is re-derived here.

---

## 1. The lever, re-measured from the real emissions

Instrument: `tools/extractor_cost_measure.py`, `serialization_lever()` — the same function
that produced the bundle's §4a table. Corpus: the **real S2 emissions** in
`tests/eval/write_path/receipts/7947-arms-2026-10-18/` (the frozen 7-session write-path
corpus, `raw` + `narrative` arm each) — **7 sessions / 14 emissions / 580 items**.

| layout | tokens | vs `indent=1` |
|---|---|---|
| `json.dumps(embed_list, indent=1)` — today | **77,561** | — |
| `json.dumps(embed_list, separators=(",", ":"))` — the lever | **52,346** | **−25,215 (−32.50 %)** |

**1,801 tokens per emission**, and the ratio is a property of the serialization rather than
of one session — the per-emission saving spans **29.6 %–35.0 %** across all 14.
This **reproduces the bundle's §4a figures exactly** (77,561 / 52,346 / 25,215 / 32.5 % /
1,801), which is the point of re-running it: the instrument and the record agree. The cells
are that function's own return value (`serialization_lever(receipts)` on the branch head),
not a re-implementation — its `saved_pct` is the rounded `32.5`, and the exact ratio
`25,215 / 77,561 = 32.5099 %` is what §2 applies to a per-document region.

`tests/test_14343_embed_list_serialization.py` pins the payload bytes the cells above were
measured on (278,874 → 224,109 characters) and pins the parsed-object equivalence on **every**
one of the 14 real emissions — so a receipts change that would move these numbers reds
instead of silently invalidating them.

---

## 2. Before → after, per median document, by stage

The embed region is not re-rendered offline (S4's graph read is `sdk=None` here), so its
**per-document** size comes from the decomposition's solved row — **12,412 tokens per S4
call** (provider `prompt_tokens` − `cl100k` static render, §1), at the measured **1.14 S4
calls/document** = **14,149 tokens/doc**. The serialization ratio is applied to that region;
nothing else in the prompt moves, by construction.

| | before | after (`compact`) | Δ |
|---|---|---|---|
| input tokens / median document | 48,461 | 43,861 | **−4,600** |
| output tokens / median document | 16,338 | 16,338 | 0 (not measured — see ⚠️) |
| **tokens / median document** | **64,799** | **60,199** | **−4,600 (−7.10 %)** |

⚠️ **Two estimators, both stated.** 4,600 is `32.5099 % × 14,149` (`4,599.8`); the bundle's
§4a prints **4,598** because it applies the rounded `32.5 %`. They are the same quantity and
the 2-token gap is rounding, not a disagreement — stated so a reader comparing the two
tables does not read one as rot. Per **call** rather than per document the same lever is
**4,035** (`32.5099 % × 12,412` = `4,035.1`) — a different row, not a different answer.

⚠️ The output column is unchanged **by construction** (the same prompt content, a smaller
render), and it has **not been measured**. What S4 *emits* given the two layouts is exactly
what the `#7949` capstone measures, and §5 says why it did not run here.

**What it buys, at the measured unit** (`floor`, the repo's one convention — a capacity cell
that cannot be bought at that tier must be wrong in the direction that flatters us):

| tier | allowance | docs/month before | after (`compact`) |
|---|---|---|---|
| solo | 3M | 46 | **49** |
| pro | 4M | 61 | **66** |
| team | 24M | 370 | **398** |

(`49 × 60,199 = 2,949,751 ≤ 3M`; `50 × 60,199 = 3,009,950` overruns. `66 × 60,199 =
3,973,134 ≤ 4M`; `67 × = 4,033,333` overruns. `398 × 60,199 = 23,959,202 ≤ 24M`; `399 × =
24,019,401` overruns.) That is **+6.5 % / +8.2 % / +7.6 %**.

---

## 3. Two things the decomposition declared unmeasured, now measured

The bundle is explicit that the emission's per-field cost is *"the SHAPE's shares, not a
measured partition — the emission is not instrumented field-by-field"*. It is measurable
from the same receipts, so it is measured here.

### 3a. The per-field partition (580 items, indent-serialized field values)

| field | records | tokens | share | avg/record |
|---|---|---|---|---|
| **`slots`** (subject/object/event, `{name,kind,confidence}`) | 294 | **18,770** | **34.8 %** | 63.8 |
| `content` (the claim) | 295 | 7,113 | 13.2 % | 24.1 |
| `quote` (verbatim, ≤200 ch — **E3**) | 216 | 5,245 | 9.7 % | 24.3 |
| `search_keys` (**E3**) | 216 | 4,448 | 8.2 % | 20.6 |
| `about_entities` (linking) | 295 | 3,704 | 6.9 % | 12.6 |
| `note` | 234 | 3,292 | 6.1 % | 14.1 |
| `src` / `dst` (operator endpoints) | 51 / 51 | 2,769 | 5.1 % | ~27 |
| `when` | 216 | 1,357 | 2.5 % | 6.3 |
| `target_edge` | 18 | 1,355 | 2.5 % | 75.3 |
| every remaining field (16 of them) | — | 5,865 | 10.9 % | — |
| **total field values** | — | **53,918** | 100 % | — |

⚠️ Read the shares, not a clean structural split: each field's value is serialized **with its
own nested keys and punctuation**, so `slots`' 34.8 % includes the `name`/`kind`/`confidence`
keys repeated inside every one of the 294 slot dicts. That is precisely why 3b is worth
measuring.

**The finding that matters for any "emit less" lever:** `slots` alone is a third of the
region, ahead of `content`. It is a *quality feature* (`#1418` typed participation), not
slack — so the honest boundary this table draws is that the remaining room is in **how the
fields are rendered**, not in which fields are emitted. That is the boundary `#7949` E2/E3
own.

### 3b. Keys declared once — the second, *smaller* rendering lever

Same data, same fields, same order; the only change is that a list's key names are declared
**once** (`{"cols": [...], "rows": [[...]]}`) instead of repeated on every record.

| layout | tokens | vs `indent=1` | vs `compact` |
|---|---|---|---|
| `indent=1` | 77,561 | — | — |
| compact separators (the shipped lever) | 52,346 | −25,215 (**−32.50 %**) | — |
| **keys-declared-once** (union of keys, `␀`-padded) | **46,028** | **−31,533 (−40.66 %)** | **−6,318 (−12.07 %)** |

So the whitespace lever is **not** the ceiling of information-neutral rendering, but the
headroom above it is **a quarter of the whitespace saving again** (6,318 of 25,215; 451
tokens per emission), and it is **not** free of the model-facing risk: key names are no
longer adjacent to their values, which is a *bigger* change to what the model reads than
moving a newline-plus-space to `,`. It is therefore **measured and NOT shipped** — a second lever needs its
own quality evidence, per this issue's rule. Per document it would be **5,752** tokens
(`48,461 − 5,752 + 16,338 = 59,047` tokens/doc → 50 / 67 / 406 documents a month) against
the shipped lever's 4,600.

⚠️ **And it is not lossless by default — these are the traps, measured.**

**The measure, stated so it can be re-run.** The three cells above are three renders of the
same 14 emissions, dumped with
`json.dumps(render, ensure_ascii=False, separators=(",", ":"))` and counted with
`tiktoken.get_encoding("cl100k_base")`. ⛔ `ensure_ascii=False` is load-bearing, and it is
**not** the call §1 measures with (§1 keeps the default `ensure_ascii=True`): the flag is
worth **375 characters** on the keys-once render — **192,559** with it, **192,934** without —
so re-measured in §1's style the three cells read **46,086 · 45,050 · 45,962** instead. Both
readings are recorded so that a reader who re-measures in §1's default style does not read
the 58-token difference as rot. The render itself is `_keys_once()` in
`tests/test_14343_embed_list_serialization.py`; the naive arm is the same shape with
`cols = list(items[0].keys())` and the `null`-padded arm the same shape with `it.get(k)` and
no sentinel.

1. **Records are heterogeneous, so `items[0].keys()` loses fields — and the render gets
   SMALLER, which is what makes it read as a win.** **10 of the 80 lists** in the corpus
   carry records with *differing key sets* (`operators` in 9 of its 12 lists, `points` in 1
   of 14). A keys-declared-once render built from `items[0].keys()` — the obvious
   implementation — **silently drops every field that a later record carries and the first
   one does not**: measured, the naive version reports **44,993** instead of the correct
   46,028 — it understates the render by **1,035 tokens while losing data**. (Per emission it
   is lossless whenever `items[0]` happens to carry the union of keys, which is why it
   survives review; the corpus aggregate is where it is wrong.) Any such render must use the
   **union** of keys and pad absent ones.
2. **`null` is a legitimate value in this schema, so it cannot be the padding.** The corpus
   carries **210 cells that are explicitly `null`** (`entities.supersedes` 150, `points.when`
   53, `events.startedAt` 4, `points.source_turn_id` 3) against **54 cells that are an absent
   key** — so `dict.get(k)` conflates "field not present" with "field present and null", which
   are different statements about a record.
3. **`null` is not the only unusable sentinel — `~`, the first one tried here, is already
   inside real values.** A keys-declared-once render therefore needs a sentinel **checked for
   absence in the data at render time** (the table above uses `␀`, U+2400, verified absent).

`tests/test_14343_embed_list_serialization.py` pins both halves: that the real corpus *is*
heterogeneous, and that the union-of-keys round-trip is exact on all 14 emissions.

---

## 4. The second lever, sized: "the story is paid three times", "the list is paid twice"

Both statements are true and **neither is a reducible-by-reference saving** — because a
reference is not something a model can dereference across stages. Stated with the measured
numbers rather than as a slogan:

**S1's story** — 2,110 tokens as the later stages see it (narrative alone; the corpus's 9
documents carrying a SOURCE TRANSCRIPT tail have it subtracted, per the bundle's §1):

| where | measured | reducible how |
|---|---|---|
| S1's **output** | 2,798 completion tokens/doc (the whole S1 output) | not by rendering — it is the model's own emission |
| S2's **user message** | 1.16 calls × (2,110 + 93 envelope) | only by **caching**, not by removing |
| S4's `{story}` slot | 1.14 calls × (2,110 + 93 envelope) | only by **caching**, not by removing |
| **input total** | **5,066 tokens/doc** | — |

Both later stages genuinely need the narrative (S2 maps it, S4 gap-reviews against it), so
"don't re-send it" means "make the re-read cheap", i.e. **prompt caching** — which `#14277`
already measured: **51.2 % hit share cold / 64.6 % warm**, with a perfect replay worth −42 %
of the *dollar* bill and the warm arm's measured share −27 %. ⛔ That is a **price/token
accounting** question, not an extraction one, and whether a cache-read token consumes
allowance is a pricing decision (`#14275`, owner). **Saving in tokens today: 0.**

**S2's embed list** — paid twice *by architecture* (S2 emits it; S4 is a later, separate call
that has no memory of it). Reference shape: **0 tokens saved**, same argument. The only
reducible part is its **rendering**, which is §1 (shipped, disarmed) and §3b (measured, not
shipped): **4,600 tokens/doc** available, **5,752** if the keys-once lever is ever admitted.

**Conclusion for lever 2, stated as the brief asks:** a reference-based shape saves **nothing
in tokens** on either object; what it can save is *price*, via a cacheable prefix, and that
is `#14275`'s decision. No change to it is proposed here.

---

## 5. The gate — exactly what arming requires, and why it did not run

`#14343`'s rule and this lane's brief agree: the bars are `#7949`'s **E1 volume / E2
correctness / E3 provenance / E4 operator edges**, and they are the arbiter. **They were not
run, so the lever was not armed.**

### 5.1 A key is NOT the blocker — corrected on re-measurement

⛔ **This section previously claimed that all five key variables are unset, that
`tools/run-with-eval-keys.sh` has nothing to load, and that `build_extractor_model()`
degrades to its "no key at all" default and returns `401`. Re-measured on the box this
file was written on (2026-11-23, from a lane there), that is false. The claim is removed,
not softened, because it is the reason a future lane would defer on a false premise.**

Measured:

- `printenv DEEPSEEK_API_KEY` → **set**; `printenv TORTOISE_API_KEY` → **set**.
  `OPENROUTER_API_KEY`, `VENICE_API_KEY` and `OPENAI_API_KEY` → unset.
- the file a **lane** loads is `$HOME/keys.env` (present on this box, mode `600`):
  agent-infra's `scripts/orchestrator-harness/lane-env.sh:61` defaults
  `_lane_env_file="${ENV_FILE:-${HOME:-/nonexistent}/keys.env}"` and `:93` sources it.
  ⚠️ `~/pi-keys.env` is a real credential location too, and the earlier wording here
  (“not the file the fleet loads”) was wrong about that: the fleet's **worker** launch
  line loads a PAIR — `worker-terminal.sh:686` iterates `"$home/keys.env"
  "$home/pi-keys.env"`, last non-blank value winning, precisely because on a Mac
  `pi-keys.env` is the only env file there is (`worker-terminal.sh:670-677`). It is simply
  **absent on this box**, which is why a lane-only reading looked complete.
- `build_extractor_model()` returns a `RoutingModel` whose primary is a
  `DeepSeekDirectModel` pointed at `https://api.deepseek.com/v1/chat/completions`. It does
  **not** fall back to the no-key default, so "every call returns `401`" was not a
  measurement either.

**So the environmental block is not established, and no figure above depends on it:** every
cell in this file is computed from committed receipts and committed measurements, with no
provider call. What actually keeps the armed arm unscored is §5.2 — a **registered** pin,
i.e. a preregistration amendment (`#14371`) rather than an environmental accident — plus
the cost of two scored corpus runs over the whole corpus, which is outside this lane's
scope.

### 5.2 Even with a key, the compact render cannot be scored — the pin is *registered*

`docs/evidence/7949-capstone/preregistration.json` carries
`/door/acceptance_arm/pinned_off[13] = "TORTOISE_S4_EMBED_JSON"`, and
`capstone_measure.py::_pin_off_the_door` **pops** that key for the whole run — so a capstone
scored while the variable is exported measures the **`indent`** render, and (since that
function's cycle-10 change) says so on stderr instead of discarding the value silently.

⛔ **This is a preregistration amendment, not a code toggle.** The pin is registered as
*"UNSET is the only state the corpus was scored under"*, so arming the lever in a scored run
does not merely edit a list — it changes the **declared acceptance arm** of the `#7949`
capstone. That is `#14371`'s work, and the amendment belongs to the owner of the
preregistration, not to this lane.

### 5.3 What a run costs, and why it is not "a subset"

The frozen corpus is **7 sessions** (`preregistration.json` → `corpus.sessions = 7`,
`fixtures_hash: sha256:dd7e85…`) — the same 7 sessions whose receipts §1 measures. So the
"affordable subset" *is* the whole corpus, and proving the bars on the armed layout means
**two** scored runs over it (the `indent` arm and the `compact` arm), because the layout is a
**process-global environment variable**: the harness installs it once per **sequential
batch** (never interleaved, or a `before` thread trips over an `after` thread — see the
bundle's §4 recipe). ⚠️ **Which instrument records that is worth being precise about:**
`tools/extractor_cost_measure.py` writes `s4_embed_json` per row and refuses a mislabelled
arm (`_assert_the_arm_is_what_rendered`, `:446`/`:591`); the **scored** capstone
(`docs/evidence/7949-capstone/probes/capstone_measure.py`) carries no `s4_embed_json` at
all — it records the flag only through its `pinned_off` read-back, and §5.2 says it pops
that flag for the whole run. So the per-row safeguard exists for the **cost** harness
today, and `#14371` must add a resolved-layout field to the **scored** rows before a
mislabelled arm could be refused there rather than averaged in.

---

## 6. Reproduce

```bash
# §1 — the serialization measurement (offline; no provider call, no spend)
uv run python tools/extractor_cost_measure.py decompose \
    --evidence docs/evidence/14277-extractor-cost/measurements.jsonl \
    --receipts tests/eval/write_path/receipts/7947-arms-2026-10-18 \
    --out /tmp/decomposition.md \
    --json-out /tmp/decomposition.json          # §"the one information-neutral lever"

# §3 — the two measurements this file adds (offline; same receipts)
# The repo route is `tools/test_lane.py run`, not a bare `pytest`: a bare run is refused on a
# URI-less box (epic #1647 P4, `tests/_embedded.py`), and `run` starts the lane's own
# container and exports the four seam variables.
uv run python tools/test_lane.py run -- tests/test_14343_embed_list_serialization.py -v
```

The pinned token cells in that test are a **recorded** `cl100k_base` measurement, deliberately
not re-derived at test time: `tiktoken.get_encoding("cl100k_base")` downloads its BPE ranks
from `openaipublic.blob.core.windows.net` on a cold cache, which is real egress from a
deterministic test — the same reason `tests/test_s2_decision_record_5087.py` pins its cells
and makes the payload **bytes** the live tripwire.

⚠️ **Re-measuring the cells is a manual step, not a rerun of the two commands above.** The
test never tokenizes (§1 refuses to fetch an encoder) and `tools/` ships no keys-once
renderer, so the §3b cells are reproduced by tokenizing `_keys_once()`'s own output with the
dump named in §3b — that recipe is the producer, and the cells are recorded here because
their re-derivation is not available to a deterministic leg.

---

## 7. The same lever, re-measured on a box with **no provider key** (`#14371`)

`#14371` carries the two items this bundle's `## Done when` could not run: a measured
before → after, and the `#7949` quality re-run. **Only the first is arithmetic**, and it was
reachable all along — that is the correction this section records. The token half is
`json.dumps(..., indent=1)` versus compact separators over the SAME objects, counted; it
needs no model, no key, no spend, and no network beyond tiktoken's own local BPE cache. It
now has a verb of its own, so it is reproducible by one command instead of by hand:

```bash
uv run python tools/extractor_cost_measure.py embed-json \
    --receipts tests/eval/write_path/receipts/7947-arms-2026-10-18 \
    --evidence docs/evidence/14277-extractor-cost/measurements.jsonl \
    --out docs/evidence/14343-amplify/embed-json-offline.md \
    --json-out docs/evidence/14343-amplify/embed-json-offline.json
```

⛔ The two `--out` flags are what make it a REGENERATION. Without them the verb
prints the document to stdout and writes nothing, so a lane following a command that
omits them sees no file change and can record a stale artifact as current.

Output: `docs/evidence/14343-amplify/embed-json-offline.md` (generated — do not hand-edit)
and `embed-json-offline.json`. The verb reuses `serialization_lever` (§1's instrument) and
`decompose` (§2's solve); a structural pin refuses a second harness.

### 7a. The cells, and their agreement with §1 and §4a

| | this run | §1 / §4a of this bundle |
|---|---|---|
| sessions / emissions / items | 7 / 14 / 580 | 7 / 14 / 580 |
| `indent=1` | **77,561** | 77,561 |
| compact | **52,346** | 52,346 |
| saved | **25,215 (32.50 %)** | 25,215 (32.5 %) |
| per emission | **1,801** | 1,801 |
| per-emission share span | 29.64 %–34.99 % | 29.6 %–35.0 % |

The instrument and the record agree exactly — the point of re-running it on the same
corpus rather than quoting it.

### 7b. Before → after per median document, with the arithmetic shown

1. the re-sent embed region is **solved** per S4 call: `23,176.0` (provider `prompt_tokens`
   per call) − `8,562` (the S4 static render) − `2,109.5` (S1's story, re-sent) − `93` (the
   #10311 untrusted-data envelope) = **12,411.5** tokens (this bundle prints `12,412` — the
   same solve at whole-token precision). Every term is subtracted because each is charged to
   its OWN row in §1; omitting the last two gives 14,614.0, **which is not this region**.
2. S4 takes **1.14** calls per document over 50 documents ⇒
   `12,411.5 × 1.14` = **14,149.1 tokens/document** (§4a's 14,149 is the same quantity at
   that display precision);
3. the measured share, exact: `25,215 / 77,561` = **32.5099 %** ⇒
   `0.325099 × 14,149.1` = **4,599.9 tokens/document** (**4,035.0 per S4 call**);
4. the median document is `48,461 in + 16,338 out` = **64,799 tokens** ⇒ after =
   `64,799 − 4,599.9` = **60,199.1 (−7.1 %)**.

| | before | after (`compact`) | Δ |
|---|---|---|---|
| input tokens / median document | 48,461 | 43,861.1 | −4,599.9 |
| output tokens / median document | 16,338 | 16,338 | 0 (**not measured** — §5.2) |
| **tokens / median document** | **64,799** | **60,199.1** | **−4,599.9 (−7.1 %)** |

⚠️ **Two rounding paths are published, and they are 2 tokens apart on purpose.** §4a applies
the ROUNDED `32.5 %` and prints **4,598** → 60,201; this section applies the EXACT
`32.5099 %` and prints **4,599.9** → 60,199.1 (§2 above also prints 4,600 for the exact
ratio). Same quantity, three display precisions — stated so a reader comparing the tables
does not read one of them as rot, and so nobody "fixes" one into agreement with the other.

### 7c. What it buys, at the allowance levels AS GIVEN

⛔ The levels are **inputs** from `#14275` (owner), not re-derived here, and no price is
computed: this section reports what a saving buys, never what a tier should cost. Every
count is a **floor** (`affordable_count`, §4a's one convention).

| tier | allowance | documents/month before | after (`compact`) | the floor, shown |
|---|---|---|---|---|
| solo | 3,000,000 | 46 | **49** | `49 × 60,199.1 = 2,949,755.9 ≤ 3,000,000`; `50 × 60,199.1 = 3,009,955.0` overruns |
| pro | 4,000,000 | 61 | **66** | `66 × 60,199.1 = 3,973,140.6 ≤ 4,000,000`; `67 × 60,199.1 = 4,033,339.7` overruns |
| team | 24,000,000 | 370 | **398** | `398 × 60,199.1 = 23,959,241.8 ≤ 24,000,000`; `399 × 60,199.1 = 24,019,440.9` overruns |

That is **+3 / +5 / +28** documents a month. (The counts agree with §4a's 49 / 66 / 398.
The multiplication lines differ in the last few tokens because §4a multiplies the unit it
prints — `60,201` — while this run's unit is `60,199.1`; **each table multiplies the unit IT
DISPLAYS**, so both re-derive as written. An earlier revision of this section multiplied the
unrounded `60,199.139…` and printed it at 1 dp, which made the printed equations evaluate 2–15
tokens short — the same display gap §7b reconciles, and now removed at the source.)

### 7d. The guards that make this quotable

- **Posture, graded by RESOLUTION through the pipeline's own predicates.** The solved region
  is only comparable to 12,411.5 under the posture the corpus was scored in, so the verb
  **refuses** a value that MOVES the render (`TORTOISE_S4_DELTA=0/off`,
  `TORTOISE_EXTRACTOR_PROMPT=compact`, `TORTOISE_CLASSIFY_LATER`, `TORTOISE_REFERENTIALITY`)
  and **does not refuse** a value that resolves to it (`S4_DELTA=1` or `garbage`,
  `EXTRACTOR_PROMPT=verbose`). Presence-based grading was measured refusing this repo's own
  suite, which exports `TORTOISE_S4_DELTA=0` from `tests/conftest.py`.
  `TORTOISE_PACKS_DIR` is graded the same way: `pack_registry.env_packs_dir()` DISCARDS a
  missing / not-a-dir / manifest-less value and the in-tree catalog renders, so a set-but-broken
  value is **accepted** (refusing it was a measured false block — the fleet's normal case is a
  reaped worktree under an exported variable), while a DIFFERENT loadable catalog is refused
  because its vocabulary cannot be proved identical.
- **One arm or no artifact.** `cost_table` prices the COLD arm whenever the corpus carries one,
  so `--arm warm` would have published warm's call structure and solved region beside cold's
  medians under a single label (measured: the team cell moved 398 → 397 on a quantity belonging
  to neither arm). A mismatch is now refused (exit 2) instead of mixed.
- **Offline, as POSITIVE evidence.** The verb wraps `tiktoken.load.read_file` — the function
  `read_file_cached` reaches ONLY on a cache miss — and reports the blob URLs it was called for.
  The recorded run called it **zero times** (`network_used: false`, `fetched: []`), so
  "reproducible with no network" is an observation rather than a claim. A directory snapshot
  could not answer this: tiktoken resolves its cache by PRESENCE of
  `TIKTOKEN_CACHE_DIR`/`DATA_GYM_CACHE_DIR` (an empty value disables caching and fetches on every
  call while writing nothing), so a snapshot of the wrong directory reports "no fetch" most
  confidently when it is wrong.

`tests/test_14371_offline_embed_json.py` pins the recorded cells against the committed
artifact, pins both layouts back to the **identical parsed object** on every real emission
(key order included — the risk is a format change a downstream parser tolerates but reads
differently), pins the guard in both directions plus the arm-mixing refusal, and runs the whole
measurement end to end wherever a BPE cache holds the `cl100k_base` blob, skipping rather than
downloading where it does not (the skip gate names the blob, not "some file in a directory":
any other encoding in the cache would pass a loose gate and then download on the miss).

### 7e. What this does NOT deliver

⛔ **The quality re-run on `#7949` is NOT delivered here.** What the layout does to what the
model EMITS (E1 volume / E2 correctness / E3 provenance / E4 operator edges) is unmeasured,
so `TORTOISE_S4_EMBED_JSON=compact` stays **OFF** — arming takes the variable AND the
`DOOR_PINNED_OFF` preregistration pin withdrawn in the same change (§5.2). `#14371`'s
item 2 is still open; this section closes its item 1 only, at the exact position `#14371`
splits them.
