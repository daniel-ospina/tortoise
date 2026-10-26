# Probes — how each measurement in the parent document was produced

Everything here is runnable, reads provider keys from the process env, and prints its own result.
Run each from the repository root with the eval interpreter (`PYTHONPATH=. .venv/bin/python ...`) —
`PYTHONPATH=.` matters: it makes `tools.longmem_eval` and `tortoise` resolve to THIS tree rather than
to an installed copy.

## `build_pilot_slice.py`

Builds the run protocol's step-3 type-stratified 50-question slice through the **committed** selector
(`tools/longmem_eval/run_protocol.py::_build_stratified_slice`) into `/tmp/8486/run/`. Prints the
composition (the parent document's §2.1). A head slice (`--limit 50`) is the #2516 defect and is not
produced here.

```bash
PYTHONPATH=. .venv/bin/python docs/evidence/2026-10-25-longmemeval-pilot-s/probes/build_pilot_slice.py
```

## `probe_judge_raw.py` — the falsifier for the judge fix

Issues the **official judge call shape** (`messages=[user]`, `n=1`, `temperature=0`, `max_tokens=10`,
no `response_format`, no system message) against four candidates, including both DeepSeek ids with and
without the vendor's documented `thinking:{"type":"disabled"}` control. Prints `content`,
`finish_reason`, the reasoning field and the usage block for each.

It exists because the failure it hunts is **invisible in a score**: a reasoning-by-default model spends
the 10-token budget on hidden reasoning, returns `content: ""`, and the benchmark's label rule
(`'yes' in response.lower()`) grades that as *wrong* — a 0.0000 accuracy that looks like a result.
Output committed as `judge-call-shape-2026-10-25.log`.

```bash
PYTHONPATH=. .venv/bin/python docs/evidence/2026-10-25-longmemeval-pilot-s/probes/probe_judge_raw.py
```

## `probe_judge.py` — the label-level probe

Four sanity cases (one answerable-and-correct, two answerable-and-wrong, one abstention) through
`build_judge(spec).judge(...)`, so the failure is visible at the level the benchmark actually consumes
(a `True`/`False` label) rather than at the transport. Candidates: `deepseek:deepseek-flash`,
`deepseek:deepseek-v4-pro`, `gemini:gemini-2.5-flash`, `gemini:gemini-2.5-pro`.

```bash
PYTHONPATH=. .venv/bin/python docs/evidence/2026-10-25-longmemeval-pilot-s/probes/probe_judge.py
```

At run time the Gemini ids answered HTTP 404 ("no longer available to new users") and the 3.x
successors answered 429/503 on this key — recorded in §8.5 of the parent document.

## Key provenance for the measured run

The run billed the **ambient** `DEEPSEEK_API_KEY` from the process environment, not a repo `.env`
(`tools/run-with-eval-keys.sh` is unusable here — the repo `.env` carries no provider key and the
wrapper strips the ambient one; parent document §8.4).

```
DEEPSEEK_API_KEY  prefix=sk-fb2  len=35  sha256[:16]=b23361d221f8dff7
```

No other provider key was used by the run. Re-derive the fingerprint without ever printing the key:

```bash
python3.12 -c 'import hashlib,os;k=os.environ["DEEPSEEK_API_KEY"];print(k[:6],len(k),hashlib.sha256(k.encode()).hexdigest()[:16])'
```

## Cost arithmetic (parent document §3.3, §8.3)

The runner's own `usage.cost.usd` reads `$0.008456` because the reader lane's served id
(`deepseek-flash`) has no `PRICING_MAP` entry, so that lane prices at `usd: 0.0` while `priced: false`.
Recomputed from the runner's exact token counts with the map's own DeepSeek rates
(`tools/longmem_eval/costing.py`, map version `2026-09-04`; flash-family in `0.14` / out `0.28` /
cache-hit `0.0028` per 1M, verified `2026-09-03`; `deepseek-v4-pro` in `0.87` / out `1.74`, flagged
`ESTIMATED` in the map):

| lane | prompt | cache-hit | completion | USD |
|---|---|---|---|---|
| reader `deepseek-flash` | 437 253 | 60 928 | 2 330 | 0.05351 |
| judge `deepseek-v4-pro` | 9 530 | 0 | 50 | 0.00838 |
| preflight overhead | 122 | 0 | 57 | 0.00003 |
| **total** | 446 972 | 60 928 | 2 447 | **0.06192** |
