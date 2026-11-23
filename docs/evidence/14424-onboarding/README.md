# #14424 — the recorded first run: install/key → write → read back → search

**Recorded:** 2026-11-23, 21:40–22:00 UTC, on the `cmux` cell (Linux, 8-core), from a
**clean `HOME`** (`mktemp -d`) **and a fresh tenant** minted during the run
(org `agent-a6653d`, tier `anon`). Both were used: the clean HOME removes every
pre-existing credential so `signup` is genuinely a first run, and the tenant is new, so
every count below starts from zero.

This is the measurement #14424 asks for — a countable onboarding criterion, not prose.
It records what a new user meets, and for every dead-end whether it was **fixed in the
same change** or **declared** with what the user sees instead.

---

## 0 · The environment (and what it made untestable)

| | |
|---|---|
| **Door walked** | hosted REST (`https://api.premiselabs.co`) + the CLI a user installs, then the SDK door on the same tree |
| **Deployed build** | version `0.2.0`, commit **`e74cf1d13da7`** (`feat(14012): "keep this resident" …`), image `registry.fly.io/tortoise-y4mjjq:deployment-01M7ZVR15A7FBYYQ6BJA7YVRRS`, `started_at` **2026-11-23T20:46:30Z**, `migrations_head` `20261117000003` |
| **`origin/main` at the time** | `4993fa083f3f` — prod is **68 commits behind** |
| **Health at the time** | `status: degraded` · `record.refusing: write_failed` (53 failed writes) · `capture.in_flight 16/16, refusing: true` · `wait_bound_breaches: rest 90, mcp 1` |
| **Published artefact** | `tortoise-graph==0.2.0` on PyPI, **uploaded 2026-08-08** (3.5 months before this run) |
| **Local control run** | the same tree at `4993fa083f3f` + this change, embedded graph, fresh `HOME` |

⛔ **What could NOT be tested, and why** — stated up front so no reader over-reads the run:

1. **The document-ingest leg** (`POST /v1/sessions` → extractor). `#14261` (ACK inside the
   budget, extract behind it) merged 2026-11-23T18:16Z and is **not** in the deployed commit
   (`git merge-base --is-ancestor b143bca e74cf1d` → false), so ingest behaviour on prod is
   the pre-ACK one. **Not walked.**
2. **The hosted write → search pair as a completed success.** It never completed (see step 4/5).
   The pair *is* demonstrated — on the SDK door against the same tree — and the hosted
   failure is recorded verbatim rather than papered over.
3. **The dashboard signup path** (`tortoise.premiselabs.co/signup`) — it needs a human with
   an email/social account. The CLI zero-email path was used instead; both are documented entries.

---

## 1 · The walk, with wall clock

Hosted door, clean `HOME`, fresh tenant. Every duration is wall clock around the exact command.

| # | step | command | wall | result |
|---|---|---|---|---|
| 0 | install | `uv tool install tortoise-graph` | **3.8 s** | ✅ installs `tortoise`, `tortoise-ingest`, `tortoise-serve` (PyPI `0.2.0`, 2026-08-08) |
| 1 | first command from the docs | `tortoise --help` | 0.6 s | ❌ **`argument cmd: invalid choice: 'signup'`** — the published CLI has 24 verbs; `signup`, `config`, `recover`, `install`, `hooks`, `volunteer` are absent (dead-end **D1**) |
| 2 | get a key (workaround: the repo checkout's CLI) | `tortoise signup` | **10.7 s** | ✅ `agent-a6653d`, key + recovery token, saved to `~/.tortoise/credentials.json` (0600); prints its own next step |
| 3 | "is it working?" | `tortoise doctor` | **26.2 s** | ⚠️ `5 pass, 7 warn, 0 fail` — the `Credentials` + `Hosted` rows **exist** (#11912's work is live); but the `Graph: health` row told a **hosted** user to run `tortoise init` (dead-end **D3**) |
| 4 | **first write** | `tortoise create-point "The onboarding probe marker is ZQ-14424-ORCHID."` | **10.9 s** | ❌ `504` — *"the server's wait budget … was exceeded … The work may still complete"* (dead-end **D2/D4**) |
| 4b | retry, exactly as the refusal instructs | same command | **14.8 s** | ❌ `504` again |
| 5 | **read back** | `GET /v1/points?limit=50` (no CLI verb exists — **D5**) | 9.4 s cold / 3.7 s warm | ✅ `200` — **the org holds 4 Points carrying the marker verbatim**: one per attempt. All four `504`s **committed server-side** |
| 6 | **first search — the criterion's real moment** | `GET /v1/search?q=ZQ-14424-ORCHID` | **10.6 / 11.9 / 13.5 / 10.7 / 11.0 / 10.7 / 11.4 s** (7 attempts) | ❌ `504` every time. Attribution the server shipped: `stage: retrieval`, `pre_handler 5194.5 ms`, `embed_encode 4559.0 ms`; a later attempt `pre_handler 7919.1 ms` |
| 7 | the agent door | MCP `initialize` → `https://api.premiselabs.co/mcp/` | **34.9 s** (then failed) | ❌ `httpx.HTTPStatusError: 504` — a fresh agent cannot even connect |

**Total to "search returns the thing I wrote": never reached**, after ~2.5 minutes of
compliant retries. The criterion's moment is **not demonstrable on the deployed build**.

### The same tree, SDK door (local control) — the pair, verbatim

`docs/evidence/14424-onboarding/probe_sdk_local.py`, fresh `HOME`, embedded graph
(`tortoise init` 1.7 s), total probe **1.6 s**:

```
create_entity  879.0 ms  -> node.id 1a20a657a39-bc5df918fb63
get_entity       6.5 ms  -> "The onboarding probe marker is ZQ-14424-ORCHID (local SDK door)."
search_knowledge 37.0 ms -> 1 hit, id 1a20a657a39-bc5df918fb63,
                            scores {fts: 2.2085, vector: null, rrf: 2.2085}, match_source "fts"
```

`read_back_ok: true`, `search_found_it: true`. ⚠️ `vector: null` is the documented
`#2985` seam: this box's venv has no `sentence-transformers`, so the dense leg is absent and
the hit is FTS-only. **A keyword hit is not a semantic hit** — the pair is proven, the
*hybrid* claim is not, on this box.

### The hosted responses, verbatim

First write (attempt 1 and 2 identical except for the duration):

```
POST /v1/points -> 504 in 11.96 s        retry-after: 10
{"detail":"The server's wait budget for this request was exceeded before a response was ready.
 The work may still complete on the server. Wait for the advertised delay before retrying, and
 retry only if repeating the operation is safe."}
```

Read back — **4 copies** of one intended write:

```
GET /v1/points?limit=50 -> 200
1a20a5edcc5-397733efd45b 2026-11-23T21:45:18.789218+00:00 'The onboarding probe marker is ZQ-14424-ORCHID.'
1a20a5e4caf-b9bd631b4f80 2026-11-23T21:44:41.902945+00:00 'The onboarding probe marker is ZQ-14424-ORCHID.'
1a20a5de80e-8087d966acaf 2026-11-23T21:44:16.142628+00:00 'The onboarding probe marker is ZQ-14424-ORCHID.'
1a20a5d6842-fc7f2dc7e352 2026-11-23T21:43:43.426618+00:00 'The onboarding probe marker is ZQ-14424-ORCHID.'
```

Search:

```
GET /v1/search?q=ZQ-14424-ORCHID&limit=5 -> 504 in 10.63 s
{"detail":"The server's wait budget … retry only if repeating the operation is safe.",
 "stage":"retrieval",
 "stages_ms":{"pre_handler":5194.5,"embed_get":0.0,"embed_encode":4559.0}}
```

---

## 2 · The dead-end audit (AX admission test: what changed / what next / how to override)

A surface may not refuse where the next action is knowable (`docs/ax/ax-principles.md`,
`#10183`). Each dead-end is classified **fixed here** or **declared** with what the user
sees instead.

| # | dead-end | which part was missing | status |
|---|---|---|---|
| **D1** | The **published** CLI (`tortoise-graph==0.2.0`, 2026-08-08) has no `signup`/`config`/`recover`/`install`/`hooks` — the first command `docs/quickstart-cloud.md` gives. `tortoise --help` → `invalid choice: 'signup'` | **next action** (argparse lists choices, not the fix) | **DECLARED** — releasing is a release decision, not a lane edit. The doc now names the gap and the source-install workaround. What the user sees instead: install from source, or use the dashboard/MCP door |
| **D2** | `tortoise create-point` on `504`: drops the `Retry-After` header the refusal refers to as *"the advertised delay"*, and says nothing that makes the instructed retry safe — **4 attempts → 4 duplicate Points** | **next action** *and* **override** | **FIXED** (this PR) — the hint prints the advertised delay, says the write may have landed, names the duplicate cost, and names the override: re-run with `--dedup` (server-side content-hash fold, `CreatePointRequest.dedup`, #1643) |
| **D3** | `tortoise doctor` on a hosted-only box printed *"no Tortoise DB at ~/.tortoise/tortoise.db … Run `tortoise init` to create it"* — a self-hosted action, for a user whose memory lives at `api.premiselabs.co` | **next action** (present but wrong) | **FIXED** (this PR) — with a hosted credential on the default target the row now says the local graph is not this setup, names `tortoise create-point "…"` as the next step, points at the `Hosted` row for whether it will work, and keeps `tortoise init` as the named override |
| **D4** | Hosted **write**, **read**, **search** and MCP **initialize** all `504` inside the 10 s bound; every failed write committed | — (not a message defect) | **DECLARED** — a **deploy** gap, not a code gap: prod is 68 commits behind `main`, and two merged performance fixes are undeployed (`8fa0662249` `perf(14185)` *"a write acks, the projection follows"*; `94d75667dc` `perf(14192)` *"post-fusion reads run as one wave"*). ⛔ The 10 s bound is **not** raised (`#3834`) — this is a defect to fix, not a timeout to extend. What the user sees instead: a 504 that now names the delay, the duplicate risk and the safe retry (D2) |
| **D5** | **No CLI verb reads back or searches.** `create-point` ends at `Created point: <id>`; verifying it or finding it again requires the REST/MCP doors | **next action** | **DECLARED** — a new CLI verb is a surface decision, not a dead-end patch. The SDK and MCP doors answer the question today and the pair above is recorded through them |
| **D6** | `docs/quickstart-cloud.md` said `tortoise signup` "saves it to `.tortoise` in the current directory" — it writes `~/.tortoise/credentials.json` (`#1708`). A user following the doc goes looking for a file that is not there, and the wrong claim advertises exactly the working-tree credential `#10656` forbids | **what changed** (wrong location) | **FIXED** (this PR) — the block now shows the measured output, including the recovery token, which the doc omitted entirely |

### Not dead-ends (checked, so they are not re-reported)

- `uv tool install` ends with *"…is not on your PATH"* + `uv tool update-shell` — it names its own fix.
- `doctor`'s local rows on a hosted box (`Session extraction: no LLM provider key`, `Harnesses: none`) each name the environment variable or command that clears them.
- `tortoise signup` is idempotent and says so (`Already have a Tortoise Cloud key … reusing it`), and prints its own next step.

### 2.1 · The fixes, verified against the live 504 (not just unit tests)

Run through the real CLI, in a clean `HOME` holding only the fresh credential, against
prod `e74cf1d13da7`:

```
$ tortoise create-point "The onboarding probe marker is ZQ-14424-ORCHID (hint verification)."
API error (504): {"detail":"The server's wait budget … retry only if repeating the operation is safe."}
The server advertised Retry-After: 10 s — wait that long before re-sending.
This failure does NOT mean nothing was written: the work may have completed on the server.
Re-running WITHOUT --dedup can create a duplicate Point for content that already landed
(measured: 4 attempts, 4 Points). Re-run with --dedup to make the retry safe.
        wall 11.5 s

$ tortoise create-point "…same content…" --dedup
API error (504): {"detail":"The server's wait budget … retry only if repeating the operation is safe."}
The server advertised Retry-After: 10 s — wait that long before re-sending.
This failure does NOT mean nothing was written: the work may have completed on the server.
Re-running is safe with this attempt's --dedup: the server folds identical content onto the
 existing Point instead of adding another.
        wall 10.9 s
```

**One of those two attempts landed; the other did not.** The org read back 7 Points at
22:02 UTC — the original 4, the 2 from the hosted probe, and exactly **one** of the two
above (created 22:02:05). So the `504` → committed relationship is **nondeterministic**,
which is precisely why the refusal is unactionable without a reconciliation path: the
caller cannot infer from the status whether the write landed. (The `--dedup` fold itself
stays **code-verified only** — the second attempt had nothing to fold onto.)

```
$ tortoise doctor --offline      # clean HOME holding ONLY a credential, no local graph
  ⚠️ Graph: health: not used by this setup — you have a hosted credential (credentials.json,
     https://api.premiselabs.co), so your memory lives there and a local graph is not required.
     Your next step is `tortoise create-point "…"` — the `Hosted` row below says whether that
     will work. Override: run `tortoise init` only if you ALSO want a local graph.
  ✅ Credentials: …/.tortoise/credentials.json — tt_…436f (fingerprint fa5dbb0654f2), …
  ⚠️ Hosted: … not probed (`--offline`); hosted reachability and key validity were NOT tested
5 pass, 7 warn, 0 fail
```

---

## 3 · Could a new user reproduce this from this evidence alone?

**The SDK pair: yes.** Two commands, no credential, no tenant:

```bash
HOME=$(mktemp -d) uv run python -m tortoise init
HOME=$(mktemp -d) uv run python docs/evidence/14424-onboarding/probe_sdk_local.py   # exit 0 == the pair held
```

**The hosted walk: yes, given a signup budget** (2 anonymous teams/IP/24 h):

```bash
HOME=$(mktemp -d) ; export HOME
uv run python -m tortoise signup
docs/evidence/14424-onboarding/probe_hosted_rest.sh        # prints status + Retry-After + bodies, per step
```

⚠️ Two facts a reproducer must expect: the durations depend on the **deployed build** (the
table above names the commit, so a run against a newer deploy may pass), and Step 4's duplicate
count depends on **how many times the refusal was obeyed** — that is the defect, not a variable
to control for.

## 4 · What this does NOT claim

- **No MCP-tool or SDK-method change** (`TOOL_REGISTRY` / `TortoiseSDK` untouched — the mandate
  on #4282 is unaffected). `create-point` is an existing **CLI** verb; it gains `--dedup` (an
  opt-in flag) and a failure hint.
- **`--dedup` is not host-verified**, only code- and unit-verified: the deployed build `504`s
  every write, so no hosted run could exercise it. The field is `CreatePointRequest.dedup`
  (`hosted_api.py`), default `False`, forwarded to `sdk._create_point(dedup=…)` — the same
  content-hash fold the SDK door already applies by default (measured: re-creating identical
  content returned the **same** node id).
- **`#10656`** (relocate `init --api-key`'s working-tree key) and **`#13224`** (subject-create
  latency) are **not** attempted here — both need their own review and neither is a message fix.
- **No capture caps** (#4010) and **no second token meter** (#14275) are involved: this run made
  ~20 requests and wrote 4 Points, all inside the `anon` tier.
