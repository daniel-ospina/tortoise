---
title: "Container lifetime in the test lane — and the two clocks that bound it"
type: operations
domain: platform
doc_status: live
subjects.team: epistemic-team
aboutSubjects: container-lifetime, test-lane, container-reaper
aboutObjects: tortoise, "#8180", "#10806", tools/test_lane.py, tools/container_reaper.py, tools/install-container-reaper-timer.sh
created: 2026-11-03
updated: 2026-11-03
---

# Container lifetime in the test lane — and the two clocks that bound it

**Status:** armed on cells · **Owner surface:** `tools/test_lane.py`,
`tools/container_reaper.py`, `tools/install-container-reaper-timer.sh`
**Issue lineage:** #5084 (private lane) → #8180 (ownership proofs) → #10693 / #10712
(the lane-gone proof, the ownership labels — **both open, neither merged at the time of
writing**, so this tool reads the labels defensively and works without them) → #10806
(this doc: the actuator + the clock)

---

## The measured defect

`tools/test_lane.py` creates one `fdb-lane-<sha1(worktree)[:10]>` container per
docker lane with `docker run -d` — **no `--rm`**, and nothing binds it to the
session. A lane that finishes normally is expected to call `down`. Nothing else
calls it, and **a lane that is killed mid-turn never reaches `down` at all**
(agent-infra #3391 — `pi -p` is single-turn and lanes die at turn end).

The container then outlives its lane forever. Measured 2026-11-03:

| box | containers | older than 1 h | load before | load after a hand-reap |
|---|---|---|---|---|
| cell-1 | **76** | 63 | **285.53** (8 CPUs) | **40.89** |
| cell-4 | **94** | — | 11.89 | — |
| cell-3 | **19** | — | 22.92 | — |

On cell-1 that was **101 `redis-server`, 78 `containerd-shim`, 76 `docker-proxy`** —
a box at 35× its core count, where every lane reads as slow or dead and the
orchestrator cannot tell which. All 189 containers across the three boxes were
reaped **by hand**: *an actuator that cannot deliver is a defect, not a status.*

Per container the cost is small and the count is what kills: 256 MB cgroup cap,
~5.8 MB writable layer, 12–77 MiB RSS, **3 host processes**.

## What already existed, and what did not

| component | decides? | could a scheduler call it? |
|---|---|---|
| `tools/test_lane.py::reap_orphans()` | **yes** — 3 ownership proofs + an age backstop | **no** — invoked only from `start()` |
| `tools/run_reaper.py` | GitHub Actions runs | n/a (not containers) |
| `~/.pi/agent/scripts/orphan-reap.py` | orphaned **processes** | it has a timer; `grep -ci docker` → 0 |
| `~/.pi/agent/scripts/disk-reaper.sh` | uv cache, lane venvs, worktrees, duplicate installs | yes, every 15 min; `grep -ci 'docker\|container'` → **0** |
| `tools/install-reaper-schedule.sh` | the **embedded** (redislite) reaper | cron on Linux — and cells have **no `crontab`** |

So the decision was never missing. **The actor and the clock were.**

## The two clocks

### 1. The exit hook — the primary (`tools/test_lane.py run`)

`run` is the one command every lane issues for a suite, so it is where a lane
ends. It now reaps its own container on the way out, **on the failure path too**
(a red or killed run is exactly when nobody runs `down`). `--keep-container`
escapes it for a post-mortem; a failing reap is reported and **never changes the
run's verdict**.

Three signals are handled, and the difference matters:

| how the lane ends | does the hook run? |
|---|---|
| normal exit / red tests | ✅ |
| Ctrl-C (`SIGINT`) | ✅ — unwinds through `finally` |
| `SIGTERM` (how a supervisor stops a lane) | ✅ — **only because the hook installs a handler**; the default disposition terminates without unwinding |
| `SIGKILL` | ❌ — that is what the second clock is for |

It also **will not take a peer's live target**: before reaping it asks the
container itself (`CLIENT LIST`); if a client is attached — two `run`s in one
worktree share one container — or the answer cannot be read, it leaves the
container to the second clock rather than failing someone else's suite.

### 2. The timer — the backstop (`tools/container_reaper.py` + systemd)

`tools/container_reaper.py` is the **actuator**: it enumerates, reports and
invokes. It owns **no ownership rule of its own** — every verdict comes from
`reap_orphans()`, the same call `start()` makes, so the clock and the lane can
never disagree about what an orphan is.

* **Dry run is the default.** `--apply` acts. The dry run is not a second
  implementation: it runs the real sweep with `docker rm` intercepted at
  `test_lane._docker` (the tool's single docker chokepoint), so it can differ
  from `--apply` in exactly one way that matters — **the removal does not
  happen**. It is not otherwise side-effect free: the sweep still refreshes its
  own lease records, which can only *delay* a later removal, never cause one.
* **Per-container line**: name, age, owner (with `live`/`idle`/`gone`),
  state, bytes of writable layer. The `(virtual …)` half of `docker ps -s` is
  the shared image and is deliberately **not** counted as freed.
* **Age is never the proof.** A thirty-hour-old container — past every threshold —
  whose lane holds a live process is KEPT, and a test pins that at that age.
* ⛔ **What the deployed unit will and will not reap, stated plainly.** The unit
  sets **no** arming variable, so it removes only **proof 1** — a container whose
  owning *worktree is gone*. Proofs 2 and 3 (owner worktree exists but its lane is
  gone; owner indeterminate) are **withheld by default**, because #8180's ruling
  only authorises "when the worktree that owns it is gone" and does not settle
  the other two. Consequence, so nobody is surprised: a lane killed mid-turn
  leaves its worktree in place, so the timer **reports** that container every 15
  minutes and does not remove it; an `exited` container holding one of the
  `PORT_RANGE` ports is likewise reported, not released. Arming them is one
  environment line each (`TORTOISE_TEST_LANE_REAP_LANE_GONE`,
  `…_OWNER_INDETERMINATE`, `…_STOPPED`) — a decision for the #8180 ruling, not
  for this tool.
* ⛔ **A container stranded in the MAIN checkout is exempt by design and forever.**
  The sweep never removes "this worktree's own" container, and the timer's
  `WorkingDirectory` is the main checkout — so a `run` there that is SIGKILLed
  leaves a container neither clock will take. Use `down` from that checkout.

`tools/install-container-reaper-timer.sh` installs
`tortoise-container-reaper.{service,timer}`: **every 15 minutes**, `OnCalendar`
(so `Persistent=true` is honoured and a rebooted cell catches up), `Nice=10`.
Cost per tick: **two** `docker ps` reads when there are no lane containers, and
**three** when there are — the third is `docker ps -s`, which walks every
container's writable layer to report the bytes reclaimed, and is skipped entirely
when the population is empty.

The cadence is derived, not picked: containers leak at lane **churn** (45 lane
starts / 24 h on a measured cell ≈ 1.9/h), so a 15-minute tick bounds the strand
to under one container between sweeps — and matches the rhythm the cell's disk
reaper already runs at.

### Deployed-vs-source: a stale copy cannot be what runs

The installer writes **two** sha256 pins into the unit — the actuator
(`TORTOISE_CONTAINER_REAPER_SHA256`) **and the decision half**
(`TORTOISE_TEST_LANE_POLICY_SHA256`, i.e. `tools/test_lane.py`, which holds every
ownership proof). Pinning only the actuator would invert the risk: editing the
enumerator would disable the timer, while editing the file that decides what gets
**deleted** would be invisible. The reaper re-hashes both and refuses to sweep on
a mismatch (**exit 4**). Measured on cell `cmux`:

```
container_reaper: STALE COPY — TORTOISE_CONTAINER_REAPER_SHA256 pins 55b5c0e96177…
but this file is 3c64e0b6ba22… Refusing to sweep with superseded policy;
re-run the installer (tools/install-container-reaper-timer.sh).
systemd[1]: tortoise-container-reaper.service: Main process exited, code=exited, status=4/NOPERMISSION
```

⛔ **`tools/test_lane.py` is edited by unrelated PRs all the time**, so on a box
whose pinned checkout tracks `main` the unit will start failing with exit 4 as
soon as any such merge lands — loudly, in `systemctl status`, but until someone
re-runs the installer the backstop is inert. That is a real operational cost of
pinning the decision half, accepted because the alternative (pinning only the
actuator) makes an edit to the deletion rule invisible. **Re-run this installer
after any merge touching `tools/test_lane.py`**, and treat a `FAILED` unit as
that signal; `--status` reports `POLICY DRIFT` by name.

**Consequence, by design:** editing `tools/container_reaper.py` requires re-running
the installer. `--status` prints the deployed pin, the deployed hash, this
checkout's hash, and one of `IN SYNC` / `STALE COPY` / `UNPINNED` / `behind`,
plus a warning when the unit is pinned to a **lane worktree** (which will fail
when that lane lands — the installer refuses that by default).

```bash
tools/install-container-reaper-timer.sh              # install / re-pin (idempotent)
tools/install-container-reaper-timer.sh --status     # scheduler's verb + drift verdict
tools/install-container-reaper-timer.sh --uninstall
tools/install-container-reaper-timer.sh --dry-run    # render the units only
tools/install-container-reaper-timer.sh --help       # works on any platform
```

The installer targets **systemd**, so it is Linux-only (cells are Linux, and every
measured leak is on one). On macOS it refuses and names the reason; `--help` still
answers.

## ⛔ The agent-infra seam — not edited here

The general exit hook belongs where the **lane** ends, which is agent-infra, not
this repo: `scripts/orchestrator-harness/lane.sh` / `fix-one.sh` / `merge-one.sh`
already run a lane's lifecycle to completion, so a `trap` there calling

```bash
uv run python tools/test_lane.py down    # or: tools/container_reaper.py --apply
```

in the lane's worktree would remove the container for **every** lane shape,
including the ones that never call `run`. That is a **documented seam, not a
silent cross-repo edit**: this repo supplies the callable verb (`down`, and the
`--apply` sweep) and the fail-closed semantics; agent-infra owns the trap.

Until that lands, the two clocks here bound the leak on every box that has this
repo checked out.

## Safety rules this tool obeys

* never remove a container a live lane is using (ownership proofs; `/proc`-based
  liveness, not log mtime);
* never `docker system prune -a` — it eats images other lanes need;
* never touch a volume (a non-test graph may live in one);
* `docker rm -f` on a name established as ours, and nothing else.
