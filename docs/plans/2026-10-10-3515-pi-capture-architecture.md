---
title: "Architecture — #3515 Pi session capture: lifecycle guarantees, the pinned hybrid, and each required piece measured"
type: engineering
domain: platform
doc_status: draft
created: 2026-10-10
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
---

# Architecture — Pi session capture (`#3515`, Task 24)

Issue: `daniel-ospina/tortoise#3515` · Objective: `#1714` · Prerequisite: `#3512` · Downstream: `#3516`
Tier: **complex** · Domain: **Complicated** (established patterns; the uncertain part is Pi's lifecycle, which is documented upstream)

This note replaces the deleted `docs/spikes/` verdict. Its job is to pin the **right** architecture — not
whether a one-off capture is possible — and to record, **measured at the current head**, which of the five
required pieces already exist and which do not. The issue's own gap list was written against the
**agent-infra** copy of the recorder, which is no longer the shipped one; several of its gaps are therefore
stale, and the honest current picture is simultaneously *further along* and *blocked in a different place*
than the issue text implies.

## 1. What Pi guarantees (doc-grounded, unchanged)

Sources: `…/@earendil-works/pi-coding-agent/docs` — `extensions.md` §Events / §Lifecycle Overview,
`sessions.md` §Session Storage, `session-format.md`.

- `session_shutdown` fires on exit / Ctrl-C / Ctrl-D / SIGHUP / SIGTERM, with `reason` ∈
  `"quit" | "reload" | "new" | "resume" | "fork"`. It **does not fire on `SIGKILL`/crash.**
- Extension `session_shutdown` handlers **are awaited** (`emitSessionShutdownEvent` awaits
  `extensionRunner.emit`, `dist/core/extensions/runner.js:53`, awaited per handler at `:632`).
- **`agent_end` is not the end of the session** — Pi may still auto-retry, auto-compact and retry, or take
  queued follow-ups (`extensions.md:569`). `agent_settled` is the settled signal.
- Pi's own session `.jsonl` **is** the durable source of truth: auto-saved **incrementally per entry**
  (`appendFileSync`, `dist/core/session-manager.js:766`), created at the first assistant message
  (`:742-763`), header `id` equal to the filename UUID. `~/.pi/agent/sessions/--<path>--/<ts>_<uuid>.jsonl`.
  **Caveats:** no `fsync` (page-cache durable, not power-loss durable); entries before the first assistant
  message are memory-only; `--no-session` persists nothing; and the log is a **tree** (`id`/`parentId`), so a
  naive byte/line cursor can ship **abandoned branches** as live.

## 2. What actually ships today (measured, not quoted)

The shipped recorder is **product-owned at `tortoise/pi-hooks/tortoise-capture.ts`** (78,534 B) with its suite
at `tortoise-capture.test.ts` (90,017 B) and a README. It is the canonical install artifact
(`capture_install.py:188` — `"pi": "tortoise/pi-hooks/tortoise-capture.ts"`; installed at
`capture_install.py:1240`, `hook_install.py:825`), and it is covered by **product** CI
(`tests/test_pi_capture_hooks.py`, incl. the artifact guard `test_extension_artifact_is_committed`).

**⚠️ Corrections to the issue's gap list — all three were written about the agent-infra copy:**

| Issue's stated gap | Status against the shipped recorder |
|---|---|
| "hooks `agent_end` only" | **STALE.** The shipped recorder hooks **four** events: `session_start` (`:1713`), `turn_end` (`:1741`), `agent_end` (`:1750`), `session_shutdown` (`:1760`). The multi-event design is deliberate and documented in-file at `:506-517` ("capture happened ONLY on `session_shutdown`, so an… `agent_end` for a DIFFERENT session… "). |
| "`loadConfig()` runs once at extension-factory time — a mid-session config write is inert" | **Not a property of the shipped recorder** — it has no `loadConfig()` call. |
| "Both fallback spools are write-only and unbounded — nothing syncs them" | **STILL TRUE, and worse.** See §4. |

## 3. The pinned architecture — HYBRID

**In-process hook for promptness and attribution + a store-sync backstop that reads Pi's own `.jsonl`.**

The hook is the only mechanism that can capture **promptly** and **with attribution** (it knows the session
and turn context). The `.jsonl` backstop is the only mechanism that exists for **every** session regardless
of how the process died. Neither alone is sufficient: the hook can be cut by a kill, and the backstop cannot
attribute. This is why the two-piece design is pinned rather than either alone.

### The five required pieces, measured

| # | Piece | Disposition | Evidence |
|---|---|---|---|
| 1 | Durable local record, appended **per turn** | **LANDED** | `capture_spool.py` — `Snapshot` (`:148`), `write_spool_entry` (`:784`); `turn_end` (`tortoise-capture.ts:1741`). |
| 2 | A **cursor per session file** | **ABSENT for the backstop** | The spool has drain discipline, but there is **no cursor over Pi's `.jsonl`** — `rg 'agent/sessions\|session-manager\|session_file'` over non-test Python returns **zero** hits. Piece 2 as written belongs to the backstop (piece 6) and cannot exist before it. |
| 3 | Retrying sync worker, **bounded backoff** | **LANDED** | `tortoise-capture.ts:554-557` — `RETRY_BASE_MS 30_000`, `RETRY_MAX_MS 6 h`, `MAX_ATTEMPTS 64` for transient failures only. |
| 4 | At-least-once, keyed by an **idempotency key** | **LANDED** | Server contract: turn Points MERGE-keyed `{session_id}_t{i}` (`hosted_api.py:8224`); `session_id` is the upsert key. |
| 5 | **Recovery sweep on next start** draining the spool + unsynced session files | **PARTIAL** | The spool half exists (`tortoise-capture.ts:731`, `:1139`; `session_start` `:1713`). The **`.jsonl` half does not** — it is the same missing backstop as piece 2. |
| 6 | The **store-sync backstop** itself (the log-shipper) | **ABSENT** | No worker source, no `store-sync` CLI subcommand (`__main__.py:4288` carries only a passing comment). `tortoise/pi-extension/` does not exist. |

### ⚠️ The sharpest way to state the blocker

The lane marker **`store_sync` landed as a label with no producer**:
`_SESSION_CAPTURE_LANE_VALUES = frozenset({"hook", "store_sync"})` (`hosted_api.py:11805`), accepted and
persisted by the boundary and the sink (`sdk.py:2255`), and named as the "store-sync backstop" throughout
(`capture_spool.py:156`). **Nothing emits it.** This is the same shape as #3516's other open item — *the
floor's inputs landed and nothing consumes them* — and it is the honest statement of where this issue
actually stands: the **plumbing for the backstop is in place and the backstop is not.**

### Install — enforce symlinks, assert the resolved target

A hand-copied extension directory diverges silently on update, which is a silent-capture-loss vector. The
installer writes an atomic **copy** (`capture_install.py:1233-1316`) — that is the deliberate replacement
mechanism, and the divergence risk is covered instead by version-contract verification
(`hook_install.ARTIFACT_CONTRACTS["pi"]`, `hook_install.py:883`) reported via `tortoise hooks status` /
`doctor` / `session verify` (#4680 → #5366) and the installed-seam artifact check (#4620 → #4733).
**The issue's "assert the resolved target" wording is superseded**; the invariant is enforced by artifact
verification rather than by symlink resolution.

### Verification — two-part (this is the part that is genuinely unfinished)

- **Part (i) install probe → 2xx + server-side key read-back.** The endpoint exists
  (`POST /v1/sessions/install-probe`, `hosted_api.py:9097`); the client fires it
  (`tortoise-capture.ts:491`, `__main__.py:4550 _cmd_session_probe`, `claude-hooks/session-start.sh:378`) and
  transitions are pinned (`capture_status`: `off → install-pending → waiting → active`).
- **Part (ii) store-proven** — after the first real session, a synced/spooled record whose `session_id`
  matches the header `id` of a real `~/.pi/agent/sessions/**/*.jsonl`. **ABSENT.** The verdict function
  `client_capture_floor_verdict` (`capture_install.py:353`) and `install_at_unix` (`:307`) have **zero
  production callers**, and `tortoise/session_verify.py` references neither the floor, nor
  `client_captured_at`, nor `capture_lane`.

**A probe is a synthetic POST.** It proves transport — network, auth, endpoint, payload shape — and it
**cannot** prove the hook fires on real sessions. Pairing (i) with (ii) is what catches a broken install
**and** a dead hook. This is the load-bearing reason the two-part form was chosen.

## 4. The live leak — the spool is unbounded and nobody drains it

Measured on this machine at the time of writing: `~/.tortoise/session-events/` holds **671 MB across 65
`.jsonl` files**.

The same spool measured **~22,640 records / ~4.9 MB in a single day / 179 MB cumulative** when this issue
was written. It has grown to **671 MB** — i.e. **~3.7× the then-cumulative figure**, confirming the leak the
issue identified is live and has never been bounded. Piece 5's recovery sweep drains the spool only while a
recorder process is running; nothing bounds the on-disk total, and nothing drains it when capture is
disabled or a machine stops running Pi. `SPOOL_MAX_ENTRIES` (1000) / `SPOOL_MAX_TOTAL_BYTES` (256 MB) exist
as **recorder-side** constants (`tortoise-capture.ts:551-553`) but the measured directory is **over 2× that
ceiling** — so either they do not apply to what is on disk, or the directory contains entries from the
retired agent-infra recorder. **This needs its own determination and is not resolved by this note.**

## 5. Explicitly out of scope (over-engineering exclusions)

1. **Exactly-once semantics** — at-least-once plus an idempotency key is the contract; exactly-once buys
   nothing the MERGE key does not already give.
2. **A distributed broker** — the local spool plus a bounded retrying worker is the substrate.
3. **Per-turn `fsync`** — page-cache durability is accepted; Pi itself does not `fsync`.
4. **Event sourcing** — the graph is the store; a second log would be a parallel model to keep consistent.

## 6. The connector framing (supersedes "a bespoke capture path")

Per the owner's steer on `#3516`: session capture is **one connector among several** in a data-sources
catalog — alongside a GitHub issues stream, GitHub documents, and others to come — **not** a bespoke
pipeline. This note pins the capture mechanism only; it does **not** pin the catalog. What it does change
here: the install/verification surface described in §3 is the **capture connector's** install and
verification, and it should be shaped so an eventual catalog can host it beside the others rather than
special-casing it. Where that catalog lives is `#3516`'s design question, not this one.

## 7. Consequence for the dependency graph

`#3515` states it depends on `#3512` "consolidated and product-owned **first**", while `#3512` requires the
consolidation "as part of the hybrid architecture (**Task 24**)" — and Task 24 **is this issue**. That is a
cycle. The resolvable split, recorded in full on `#3512`:

- **`#3512`** owns the **agent-infra copy disposition** (shim or delete) — a recorded decision, plus
  reconciling the repo to the machine (which has already `.disabled` the old copy).
- **`#3515` (this issue)** owns the hybrid build **including** the consolidation.

The product-owned precondition is **already met**, so nothing here is waiting on code from `#3512`.

## 8. Open decisions this note does NOT settle

Recording them as open rather than silently picking:

1. **Cursor format and location** for the `.jsonl` backstop, and the exact semantics over a **tree** log
   (`id`/`parentId`) so abandoned branches are not shipped as live. This is the one genuinely hard design
   question, and it is a prerequisite for pieces 2 and 6.
2. **The 671 MB spool**: drained by this issue's recovery sweep, bounded, or parked? (§4 above finds the
   directory over the recorder's own stated ceiling, which must be explained first.)
3. **Startup resolution-assert behaviour** for a dev who has not installed the extension: fail loud vs warn.
