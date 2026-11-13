---
title: "Scoping #11802 — the capture drain destroys what it could not read (research: internal + external)"
type: engineering
domain: platform
doc_status: live
subjects.team: epistemic-team
aboutSubjects: tortoise-hosted
aboutObjects: capture, spool, durability, discard-ledger
created: 2026-11-13
---

# Scoping — #11802: the drain destroys what it could not read

**Lane:** `fix/11802-capture-discard` · **Worktree:** `.worktrees/fix-11802` · **Tier:**
standard+complex · **Domain:** Complicated (an established pattern applied one level up, not a new
mechanism) · **Scope:** the tortoise-repo half only; the renderer/ownership half is
agent-infra#3921.

> **Why this doc exists.** The brief for this lane mandated research before code. This is that
> research: what the repo already measured and holds, what the ontology says about what a spool
> entry *is*, which deployed copy is being patched, and what the field converges on outside. Every
> internal claim carries a `file:line`. Nothing here re-derives the causal chain the issue already
> measured — it points at it.
>
> **Citations name SYMBOLS wherever they describe live code** (`list_spool_metas`, `recordSpoolMeta`, …):
> the first draft transcribed `file:line` offsets against an older revision and only 2 of 11 still
> resolved, so symbols replaced them and survive the next edit.
>
> Code line numbers survive only where they name the tree they were measured on — three such groups:
> the §2.5 PRE-fix table is pinned to the **base** (`origin/main` @ `2dbfb046d7e3`, whose lines are the
> defect sites this change moves); the §3 copies table is measured on the **shipping** artifact (the
> repo copy is byte-identical from `8ad837711f` onward) and says so in its own lead-in; and one live
> citation, `tortoise/hook_install.py` `PI_EXTENSION_NAME`, keeps a line number because the symbol is
> a module constant rather than something to quote — safe because this PR does not touch that file.
>
> A **fourth** group is offsets into two documents this PR never edits: `docs/ONTOLOGY.md` (§1's layer,
> `eventId` and `AgentSession` anchors) and `docs/architecture/PI-CAPTURE-ARCHITECTURE.md` (the orphan
> reap note). Those are safe *only* because the files are untouched here, and they are the group most
> likely to drift next — every one was resolved against a HEAD checkout when this note was written,
> so re-check them if either document is ever edited.

---

## 1 · The problem, restated in the ontology's own terms

The issue measured the mechanism (root-owned writer, user-owned drain, `EACCES` before any parse,
1,356 of 3,384 lost sessions never readable by the process that discarded them). This section
answers the question the brief asked: **which layer does a captured session live on, and which node
is the durable record of "this session happened"?**

Per `docs/ONTOLOGY.md`:

| Layer (§2, `docs/ONTOLOGY.md:59-71`) | What a captured session is there |
|---|---|
| **Semantic** | a **`:Source`** with `sourceKind: agentSession` — the provenance bridge (`docs/ONTOLOGY.md:180`, `:1130`). Its **bytes live outside the graph**, reached through the source (§4.4, `docs/ONTOLOGY.md:461-463`). |
| **Episodic** | an **`:Event`** with `eventKind: AgentSession` (exact spelling; `sessionCaptured` is an alias of the same concept — `docs/ONTOLOGY.md:531`). |
| **Epistemic** | whatever `:Point`s are **extracted** from it, each tied back by `extractedFrom` → the `:Source` (`docs/ONTOLOGY.md:180`). |

**So: a captured session is an `:Event` (Episodic layer) plus a `:Source` (Semantic layer) whose
bytes are outside the graph — and the durable record that "this session happened" is that
`:Event`.** The `:Source` is the address; the bytes are the transcript; the Points are what the
extractor eventually believes.

### The part that makes this a durability bug and not a tidiness bug

The `AgentSession` Event's `eventId` is **content-addressed from `session_id` + `captured_at`**
(`docs/ONTOLOGY.md:520`, citing #909 §4.3 #3). Those are the two inputs to the Event anchor.

Now read what the spool meta actually holds (`tortoise/pi-hooks/tortoise-capture.ts` `SpoolMeta`):
`session_id`, `client_captured_at`, `created_at`, `content_digest`, `capture_key`, `attempts`,
`next_attempt_at_ms`, `filed_key`, `filed_lane`. That is **the Event anchor's inputs plus the entire
retry/idempotency state**.

The repo already states this consequence about the same file, in its own words
(`tortoise/capture_spool.py` `_quarantine_log` docstring, which carries the "NOT re-fileable" note):

> `NOT re-fileable` — … the entry's key is `sha256(session_id)[:32]` and **the corrupt meta (the only
> copy of the session id) is REMOVED, so no verb can reconstruct the session id the server needs.**

And `docs/architecture/PI-CAPTURE-ARCHITECTURE.md:160` says the same from the reaper's side: a
quarantined `.turns.jsonl.orphan` is **"unreachable to every surface"** and is therefore **reaped
ahead of both delivery classes**.

> ⚠️ **CORRECTION (2026-11-13, review cycle 1).** The eviction ORDER below was first written inverted.
> `capture_spool.py` `prune_spool` eviction-order docstring states the truth: *"A quarantined log goes LAST of the evictable classes,
> not first: a FILED entry's content is already on the wire, so reclaiming it is a CERTAIN zero-cost
> choice, while an orphan's delivered-ness is unknowable"* — and the code agrees (filed pass, then the
> orphan pass, then unfiled entries which are never evicted). So an orphan is spent **second, not
> first**; `PI-CAPTURE-ARCHITECTURE.md:160`'s "ahead of both classes" is the imprecise one, and it was
> repeated here. **The argument is unaffected in substance and is restated correctly below:** the orphan
> is still reachable by the ceiling and still permanently unfilable, which is what makes quarantine a
> delayed delete. The order only changes *how long* the delay is.

Confirmed in code: `tortoise/capture_spool.py`'s module docstring allows an `.orphan` to be reclaimed because it is
"unreachable to every surface". The order is **filed entries → orphans → UNFILED, which are never
evicted**: there is no unfiled eviction pass at all — the unfiled arm is a bare `continue`
(`capture_spool.py` `_saturated_for_metas` and the unfiled bare `continue`; TS `pruneSpool`), guarded by *"an UNDELIVERED entry is the only copy of a
conversation … the cap therefore stops here and the spool reports SATURATED instead"*. An earlier draft of
this document read the orphan pass as sitting *"between"* filed and unfiled and thereby implied a third
pass that reaps unfiled work — corrected here, because that misreading is exactly the one this section
exists to remove.

**Therefore the current behaviour is not "preserve the transcript for forensics". It is a delayed
delete**: quarantine strips the transcript of the only key that can re-address it, and the cap
reclaims orphan bytes as soon as the filed entries are exhausted. An entry whose meta was merely
*unreadable at that instant* is destroyed on a path that can never retry, and the transcript that
"survived" is on a path that the ceiling will reach and that no surface can file. That is why "never
destroy what you could not read" is a durability requirement and not politeness.

---

## 2 · What the repo already holds — the fix is an existing pattern, applied one level up

This is the most important internal finding: **the codebase already has the correct semantics and
applies them everywhere except here.** The fix introduces no new doctrine.

### 2.1 The read path already says unreadable ≠ corrupt

`tortoise-capture.ts` `countPendingEntries` (`// unreadable is not empty`, about an unlistable DIRECTORY) and `healthScan`'s `Math.max(0, onDisk - metas.length)` — an uncounted meta still counts as pending — on the same files:

> A meta we cannot read **still counts as PENDING (it may be waiting)** and is EXCLUDED from the
> saturation metas.

The health line and the drain therefore currently disagree about the same bytes: the reader says
"pending, may be waiting", the repair pass says "corrupt, destroy". The fix resolves that
contradiction in favour of the position the read path already records.

### 2.2 The doctrine is already written down, verbatim, for a neighbouring case

`tortoise-capture.ts` (the retention doctrine comment in `writeSpoolEntry`'s growth-refusal arm):

> **A refusal is a delay the operator can reverse (`--retry-terminal`), an unlink is a loss nobody
> can.**

and `tortoise/capture_spool.py` on `DISCARD_TRANSCRIPT_TORN`:

> The entry is RECORDED and KEPT (marked terminal), NEVER unlinked: … a read/decode failure must
> never delete user data (#7708 follow-up).

`DISCARD_TRANSCRIPT_UNREADABLE` (`tortoise/capture_spool.py`) exists for exactly a read
failure — on the transcript. The meta is the one sibling that never got the same treatment.

### 2.3 The mechanisms the fix needs already exist

- **A `retained` flag on the record**, with defined precedence over the reason:
  `tortoise-capture.ts` `DiscardRecord.retained`,
  `tortoise/capture_spool.py` `RETAINED_DISCARD_REASONS` + `is_lost_discard` ("an explicit
  `retained` flag on the record WINS over the reason").
- **`recordDiscard` already accepts `retained`** (`tortoise-capture.ts` `recordDiscard`)
  and writes it to the append-only `discarded.jsonl` ledger.
- **Precedent arms that keep + record**: `DISCARD_ENTRY_GROWTH_REFUSED` and `DISCARD_SPOOL_SATURATED`
  both write `retained: true` (`tortoise-capture.ts` the two `retained: true` arms), with the
  comment "a refused growth is a delay, not a loss".
- **A `repair: false` reader** already skips corrupt entries without mutating
  (`tortoise-capture.ts` `listSpoolEntries`'s retry comment).

### 2.4 Discard reasons are not a closed set at the server

Only `tortoise/capture_spool.py`, `tortoise/__main__.py`, `tests/test_capture_spool.py` and
`tests/test_capture_lane_3516.py` name the `DISCARD_*` values. (`tortoise/__main__.py` names exactly
one — `DISCARD_SPOOL_SATURATED`, and filters on it; `tests/test_capture_lane_3516.py` names
`DISCARD_HTTP_REJECTED`; `tests/test_3516_client_captured_at.py` names NONE, though the first draft
listed it here.) (By contrast the *closed* sets
are `CAPTURE_LANES` and `CLIENT_CAPTURED_AT_SOURCES` (`tortoise/capture_spool.py`), which
the server does validate.) So a new reason needs no server change — but it must be added to **both**
legs, because the two legs drain one shared directory
(`tortoise-capture.ts` `SPOOL_DIR`, one shared directory for both uids).

### 2.5 Both legs carry the defect

> ⚠️ **Line numbers in the table below are against `origin/main` (PRE-fix), on purpose:** the
> exact lines they cite are the ones this change removes, so they cannot be cited at HEAD.
> Everywhere else this document cites **symbols, not line numbers** — the first draft transcribed
> `capture_spool.py:<line>` offsets against an older revision and only 2 of 11 still resolved at
> HEAD (review cycle 5 measured it), so each citation now names the function or constant it means
> and survives the next edit.

| Leg | Site | `catch` |
|---|---|---|
| TypeScript (the deployed harness seam) | `tortoise-capture.ts` `listSpoolEntries` (base `:1453-1457`) | `catch { meta = undefined }` — collapses *read failure* and *parse failure* into one outcome, then renders it as `meta is not valid JSON` (base `:1465`). |
| Python (the CLI drain) | `capture_spool.py` `list_spool_metas` (base `:1370-1375`) | `except (OSError, json.JSONDecodeError, UnicodeDecodeError): meta = None` — `OSError` **includes `PermissionError`**. Same collapse, same `corrupt_entry`, same quarantine + unlink (base `:1392-1395`). |

The TS comment above `listSpoolEntries`'s parse arm claims the function "Mirrors
`capture_spool.list_spool_metas`" — it does, which is why fixing one leg alone would leave the other
leg destroying the same files on the next tick.

---

## 3 · Which deployed copy is being patched, and how that was proved

**The brief warned not to guess. Measured on the SHIPPING artifact — the repo copy is byte-identical
from `8ad837711f` onward, so these numbers hold for this branch — and on this box's live install.
Every number below is a `wc -lc` / `head -1` reading, not an inference:**

| Copy | Path | Size / lines | Marker |
|---|---|---|---|
| Repo (canonical source) | `tortoise/pi-hooks/tortoise-capture.ts` | 206,167 B / 4,210 | `// tortoise-hook-version: 21` |
| Fleet deployed | `~/.pi/agent/extensions/tortoise-capture.ts` | 190,157 B / 3,935 | `// tortoise-hook-version: 18` |
| Other repo | `agent-infra/extensions/tortoise-capture/index.ts` | 33,983 B / 827 | not the same artifact |

**Proof of which one the fleet runs, from the installer's own declaration**, not from the paths:
`tortoise/hook_install.py:834` (`PI_EXTENSION_NAME = "tortoise-capture.ts"`) and
`tortoise/hook_install.py` `_pi_artifact_contract()` — it returns
`source = tortoise/pi-hooks/<PI_EXTENSION_NAME>`, `install_name = <PI_EXTENSION_NAME>`,
`root_relpath = .pi/agent/extensions`. So `~/.pi/agent/extensions/tortoise-capture.ts` **is written
from `tortoise/pi-hooks/tortoise-capture.ts`** by `tortoise install pi`, and the marker is the
contract number the installer and its drift detector both read.

**Disposition.** The repo copy is patched — it is the source of truth and the only copy the installer
reads. The deployed copy measured here is **generation 18**, while the base `origin/main` carried
**generation 20** (`head -1 tortoise/pi-hooks/tortoise-capture.ts` → `// tortoise-hook-version: 20`,
same for `tortoise-volunteer.ts`) — the doc's first draft read the highest *changelog* block in
`tortoise-capture.ts` (generation 19's note; main carried no generation-20 note at all) instead of the
marker, which is the number the installer and its drift detector actually read. So the fleet was **two
generations behind**, and this change moves the repo copy to **generation 21**, leaving an
un-reinstalled seam three behind. Consequence stated plainly: **patching the repo does not fix the
running fleet until `tortoise install pi` is re-run.** That is the installer's contract, not a gap this lane may paper
over by hand-editing `~/.pi/agent/extensions/`; the lane reports it and the fix ships in the
artifact the installer distributes.

---

## 4 · External research

**PREFLIGHT — search tool.** Search tool: Perplexity API (model `sonar`), called directly with curl
because the `web_search` tool could not see the credential — rung 1 unavailable (`mcp_load`
`seo-intelligence` → "Unknown MCP server 'seo-intelligence'"); rung 2's harness route →
`PERPLEXITY_API_KEY` not set in the process env, while the key IS present in `~/keys.env` (mode 600,
as the skill documents) — queries: 4 attempted / 4 returned (query 2 answered HTTP 429 and was
retried serially after backoff, per the skill's rule; it then returned). `exa` was not used and is
not on the ladder.

**PREFLIGHT — graph.** Graph: 0 hit(s) over 2 queries ("capture spool unreadable permission discard
quarantine", "unreadable meta quarantine data loss") — unread — no query ran — failed: q1 504×1, q2
504×1 (server wait-budget exceeded on both; retried once each per the skill); wrote: not written —
no query ran, so no evidence to file. The BRIEF's script route was tried first and reported
`GRAPH-UNREACHABLE(NO-KEY)` (no key in this process's env); sourcing `~/keys.env` reached the server,
which then answered `504` with a 237-byte wait-budget body on both queries. Recorded, not silently
skipped.

### 4.1 A consumer must never destroy what it could not read

The field converges **strongly** here. The recommended policy is to classify the failure *before*
deciding anything, and to keep three classes apart:

- **transient operational** (timeout, dependency outage, throttling) → retry with bounded backoff;
- **malformed/semantically invalid** — the consumer *successfully read the bytes* and cannot parse
  them → stop retrying, dead-letter/quarantine;
- **unreadable** (`EACCES`, missing file, I/O error, decryption failure) → **never classified as
  malformed.**

The stated reason `EACCES` must not cause deletion is exactly our case: *"`EACCES` proves that the
consumer failed to access the message; it does not prove that the message is malformed. Deleting it
would convert an infrastructure or authorization defect into silent data loss"* — and the file may
be owned by another user, the condition may be temporary (an ACL or mount change), and *"another
consumer or operator may be able to read and repair it."*

That is a precise description of #11802: a root-owned entry in a user-owned spool, whose bytes are
valid JSON and whose readability is a property of *who is asking*.

**Convergent invariant** (query 4, and it is the cleanest statement of the whole fix):

> **No successful read, no destructive decision; no durable completion, no cursor advancement.**

Corollaries the field states explicitly, all of which bear on our code:

- **Quarantine requires a successful read of the metadata first.** *"A cleaner should not blindly move
  or delete an entry it cannot `stat`, open, or read. It should first successfully read the metadata
  needed to identify the item … If metadata cannot be read, the safe result is retryable skip, not
  quarantine."* — This is the direct verdict on `listSpoolEntries`: it quarantines a transcript whose
  meta it never opened. It also explains why quarantine is worse than useless here: a quarantine
  record that cannot name the item is not an audit trail.
- **Do not advance a cursor/watermark past an unread entry.** Our "cursor" is the repair pass itself;
  leaving the entry in place and rescanning is the idempotent shape the field prescribes.
- **Atomic rename within one filesystem** is the right quarantine primitive *when quarantine is
  warranted* — which it is not, for an unread entry.
- Analogy sources: `rsync --partial/--partial-dir` keeps incomplete data so a later run resumes
  rather than presenting an incomplete destination as finished; `logrotate`'s `delaycomp*` postpones
  acting on a file a writer may still hold; `systemd-tmpfiles` cleans by explicit age policy, never
  by "one scan could not read it".

**Where the field does NOT converge:** the *name* and the *retention policy* for the middle state.
Dead-letter queue, quarantine directory, and "recorded skip" are all in use, and which is right
depends on whether replay is possible. For #11802 the answer is dictated by the repo, not the field:
the entry can simply **stay in the spool and be retried**, because the spool *is* the retry queue —
no DLQ needed.

### 4.2 How a spool shared by more than one uid is owned in production

The field converges on one negative and is explicit that there is no clever single flag:

> A sound design is usually **not** "one writable directory with several UIDs." Use a root-owned,
> non-writable container, create **one subdirectory per producer UID**, and **run the consumer under
> the same UID as the producer** whenever possible.

- **`StateDirectory=` / `DynamicUser=`** are built around **one unit → one state directory → one
  effective owner**, and systemd may *recursively re-own* a managed directory whose ownership does
  not match the configured identity — so a directory shared by several services can be actively
  damaged by being treated as a normal `StateDirectory=`.
- **`/var/spool/mail`** is **single-owner-per-mailbox** (per-recipient files), not several arbitrary
  UIDs mutating one file; it also needs locks, because directory permissions alone do not make a
  shared append safe.
- **Setgid** makes new entries inherit the directory's group — it *"does not make files mutually
  writable or solve ownership conflicts."* The **sticky bit** stops one user deleting another's
  entries; it does **not** stop reading them, nor opening/truncating them, nor name-replacement,
  symlink, hardlink or TOCTOU attacks.

**Consequence for this lane, stated plainly:** the *ownership* defect (two identities rendered from
two different sources) is **agent-infra#3921** and is correctly out of scope here. But the field
confirms the tortoise half is not a workaround for it — it is the *other required half*. As long as
two uids can write one spool, **the consumer will meet entries it cannot read**, and the only correct
consumer behaviour is the retryable skip. Fixing only the ownership would leave the destroy-on-read
path armed for every future mismatch (a mount change, an ACL, a `sudo` run, a migration).

### 4.3 The observability obligation — explicit and metered, not silent

The field converges on "counted event with an explicit disposition", and its disposition vocabulary
maps **one-to-one onto a field the repo already has** (`retained`):

| Field's term | Meaning | Repo equivalent |
|---|---|---|
| `retained` | not progressing, but recovery remains possible | `retained: true` |
| `retryable` | failed temporarily, may succeed later | entry kept, `next_attempt_at_ms` backoff |
| `dead-lettered` | isolated for investigation/replay | (n/a here — the spool is the retry queue) |
| `permanent` | will not be recoverable | current `corrupt_entry` + unlink |

The decisive argument for splitting the metric: **"A retryable item is an operational failure but not
necessarily data loss. A permanently lost item is a correctness or durability failure. Combining them
makes dashboards, SLOs, and alerts answer the wrong question."** Today an `EACCES` and a genuinely
corrupt meta land in the *same* `corrupt_entry` bucket, so the ledger reports an authorization defect
as data corruption — which is precisely the complaint in the issue (§13's conflation).

Both halves are prescribed together: a **counter** (how often, is it rising) *and* a **structured
ledger record** (which item, when, why) — *"Do not use a log line as the sole signal … Do not use a
counter as the sole record: it cannot identify or reconstruct an individual lost item."* The repo
already has the ledger (`discarded.jsonl`, append-only) and a per-run `spool DISCARD` warning line
(`tortoise-capture.ts` the `spool DISCARD` warning line), so the metering requirement reduces to: **the
new reason must be distinguishable in both**, and marked `retained` so `is_lost_discard` does not
count it as a loss.

---

## 5 · Contradiction test (run before adopting anything above)

Checked against every recorded decision that reaches this work:

| Decision | Reached by this fix? | Verdict |
|---|---|---|
| **#10175** — owner ruling 2026-10-27: the hosted capture cap is **HELD deliberately**; refusal is correct until the new extractor/storage architecture lands; carries an `OVERRIDES:` line. Must not touch the cap, prune the graph, or file the refusal as a defect. | **No.** The fix changes no ceiling (`SPOOL_MAX_ENTRIES`/`SPOOL_MAX_TOTAL_BYTES`/`MAX_ENTRY_BYTES` untouched), adds no eviction, prunes nothing. | **Not contradicted — and it is the same ruling.** A cap that REFUSES writes and KEEPS the backlog is exactly what #10175 holds; keeping an unreadable entry is *more* consistent with it than destroying one. **⚠️ CORRECTION (review cycle 1): the first version of this row claimed a retained unreadable entry "continues to occupy a count/byte slot". That is FALSE and was measured false.** Both legs compute count and bytes from PARSED metas (`capture_spool.py` `prune_spool`, which counts PARSED metas only; TS `pruneSpool`/`spoolSaturated`), so an unparsed meta and its live `.turns.jsonl` are counted by NEITHER the meta sum NOR `_orphan_paths` — a spool holding one unreadable entry measured 5,422 bytes on disk while `spool_saturated(…, max_total_bytes=1000)` returned `False`. This is a REAL residual of the retained path and is declared in §7; it is not fixed here — but NOT because #10175 holds against it (see §7: it does not); the reason is risk/scope. Filed as **tortoise#12483**. |
| **#10977 comment `6781757043`** (2026-11-10) — one-belief-kind: a claim is a `:Point` with `pointKind: statement`; Object/Event/Subject/Source are different nodes on different layers, never a kind of claim; `decision` is an Event. | **No.** This fix writes no graph node and adds no kind. It reads the ontology only to locate what the spool entry *is*. §1 above obeys the ruling (a session is an `:Event` + `:Source`, not a claim). | **Not contradicted.** |

No `OVERRIDES:` line is added by this lane, because nothing here departs from a default
against a decision — the fix adopts the repo's own written doctrine.

---

## 6 · Design decided, and the reasoning that rejected the alternatives

**Decided:** split the single `catch` into a *read* outcome and a *parse* outcome. A read refusal
(`EACCES`/`EIO`/`ENOENT`-between-readdir-and-open/any I/O error) records a **retained** discard with
its own reason and leaves **both** the meta and the transcript exactly where they are, so the next
drain retries. Only a genuine `JSON.parse` failure (or valid JSON with no usable `session_id`) keeps
today's quarantine + unlink path. Both legs implement it; both reasons are distinguishable in the
ledger and in the warning line.

Alternatives considered and **rejected**, with the reason:

1. **Leave it to agent-infra#3921 (fix the ownership only).** Rejected: §4.2 — the consumer will still
   meet unreadable entries on any future mismatch (mount, ACL, sudo, migration). The field's invariant
   is unconditional: *no successful read, no destructive decision.*
2. **Quarantine the meta as well as the transcript, and record it.** Rejected: §4.1 — quarantine
   requires a successful metadata read to identify the item; and per §1 the meta is the *only* copy of
   the session id, so removing it from the scan path guarantees the entry can never be filed. A
   quarantined copy of the meta would be reaped too — and the quarantine happens at the END of the scan, so
   the copy would survive exactly one drain and then be gone (`capture_spool.py` module docstring: an
   `.orphan` is "unreachable to every surface").
3. **Add a dead-letter queue.** Rejected: the spool already **is** the retry queue with backoff
   (`next_attempt_at_ms`, `attempts`). A DLQ adds machinery and another unreachable surface, which is
   the very failure mode being fixed.
4. **Only shut down the destroy path (skip silently).** Rejected: §4.3 and the issue's own §13 — the
   conflation must stop being *reported* as corruption. Silence would be a new defect: the read path
   and the operator both need to know an entry is waiting unread.
5. **Treat unreadable as terminal (record, mark terminal, keep).** Rejected: an unreadable entry has
   not been *inspected*, so there is nothing to declare terminal about it; marking it terminal would
   permanently stop retrying an entry that becomes readable the moment the permission mismatch
   resolves. `attempts`/backoff already bound the retry rate.

**Stated residual:** the fix makes the *consumer* honest. It does not make the two uids able to read
each other's files — until agent-infra#3921 lands, a foreign-uid entry will now **accumulate** rather
than be destroyed. That is the trade this fix deliberately makes: **a visible, reversible backlog
instead of silent, irreversible loss.**

---

## 7 · Residuals found by review cycle 1 — declared, not absorbed

A fresh-context reviewer broke three claims in the first version of the change and of this document.
Findings 2 and 3 were **fixed** in the change; findings 1 and 4 are **declared here** because fixing
them would cross the lane's stated boundary.

**(1) A retained unreadable entry is NOT counted by either ceiling — measured, and the first version
of §5 said the opposite.** Both legs compute count and bytes from **parsed** metas
(`tortoise/capture_spool.py` `prune_spool` computes both from `metas, _ = list_spool_metas(root)`; the
TS equivalent is `pruneSpool`/`spoolSaturated`). A retained unreadable entry is unparsed, so its meta is
in neither `len(metas)` nor the byte sum, and its turn log keeps its `.turns.jsonl` name so
`_orphan_paths` does not see it either. **Measured on this worktree:** `entries/` held **5,422 bytes**
and `spool_saturated(root, Bounds(max_total_bytes=1000))` returned `False` — writes keep being
admitted past the ceiling with no refusal and no `capture: SATURATED`. Before the change this was
bounded only because the entry was *destroyed*; the retained path trades that for uncounted growth.

**Why it is not fixed here** — and the reason is NOT #10175, which the first draft wrongly cited.
Review cycle 2 is right that #10175 holds a cap which *refuses rather than deletes*, and that counting
**more** bytes moves in that same direction, so it is not the obstacle. The repo even has the precedent one
class over: orphan bytes are already credited to `total` for exactly this reason
(`capture_spool.py` `prune_spool` eviction-order docstring — *"the byte ceiling stopped applying the moment a meta was quarantined"*).
The real reason is **risk and scope**: this is a behavioural change to the adjudication inside
`prune_spool` / `spoolSaturated` (and both TS twins), the highest-consequence function in the module, and it
needs its own review plus the cap owner's sign-off — not a rider on a data-loss fix. A naive `total +=`
would also make the eviction loop spend every **filed** entry chasing a ceiling it cannot reach, because
the retained bytes are unevictable; the correct shape credits them to `total` **and** exempts them from
the eviction budget, which is a design, not a line. Filed as **tortoise#12483** with that shape spelled
out. Bounded in practice meanwhile by `SPOOL_MAX_ENTRY_BYTES` per entry, and fully relieved the moment
the permission mismatch is repaired (the entries become parsable and re-enter the accounting).

**(3) The WRITE path still concludes "absent" from a read refusal — same class, one layer over.**
`capture_spool.py` `read_spool_meta` returns `None` on any `OSError`, and the write path calls it for `prior`
(`write_spool_entry`); `tortoise-capture.ts` `writeSpoolEntry` has the same shape. If the session being written is the unreadable one, `prior`
is `None`, so `write_spool_entry` treats it as a fresh snapshot and `_atomic_write` **replaces** both the
meta and the log — dropping any stored turns outside the current `MAX_TURNS` window and discarding
`filed_key`/`attempts`. Pre-existing (the drain fix does not reach it), lower frequency (it needs the same
session to be the writer), but it is the same defect shape and it is **not** fixed here — the lane's scope
is the drain. Filed as **tortoise#12487** with this analysis.

**(4) The two legs disagree destructively on non-UTF-8 metas — pre-existing, not introduced here.**
Python's decoder raises `UnicodeDecodeError` (→ `corrupt_entry`, entry destroyed); Node's
`readFileSync(…, "utf8")` never throws and substitutes `U+FFFD`, so `JSON.parse` returns a *usable*
meta and the Pi leg files it. The two legs drain ONE directory, so which leg touches the file first
decides whether the entry is filed or deleted. **This divergence predates the change** — it is not
introduced by the read/parse split — but the first version of the change *documented the function as a
faithful mirror*, which is false on this axis; the comment now names the divergence explicitly. Filed as
**tortoise#12484** rather than widened here.

**Fixed over review cycles 1–2 (recorded so the corrections are not later re-litigated):**

- **(2) Unbounded ledger growth — cycle 1's fix was INSUFFICIENT, and cycle 2 caught it.** The first
  version recorded the read refusal with an unconditional `recordDiscard`/`record_discard`; because the
  repairing scan also runs on the **per-turn** write path and the entry never becomes readable, that
  appends one line **per turn** into an uncapped `discarded.jsonl` in the spool root — the exact
  amplification class `_record_refusal`/`recordRefusal` exists to prevent (`capture_spool.py` `_record_refusal`, whose docstring names the amplification class).
  Cycle 1 switched both legs to the refusal recorder and claimed "5 scans → 1 line" — **but the refusal
  key was `(session_id, reason, entries/ stamp)` and that stamp includes `entries/` mtime, which every
  turn moves** by writing a new meta. So the claim held only on a STATIC directory and was false on the
  real per-turn path; the N-wide window also crowded admission refusals out at N>32. **Cycle 2's fix,
  now in this change:** the arm records **ONE aggregate** for the whole unreadable SET, keyed on a
  digest of the **stem set alone** (`stemSetStamp` / `_stem_set_stamp` — byte-identical across the two
  legs, so they dedupe each other's records), and consumes ONE refusal-window slot instead of N.
  Pinned in both legs **with a new readable meta written between every scan** — the mutation test now
  reds at `got 5` when the dir-stamp key is restored, which is precisely the cycle-2 measurement.
- **(3) An inverted eviction-order claim.** Corrected in §1 above and in both code comments; the
  "between filed and unfiled" wording that implied a third reaping pass is corrected too (cycle 2).

---

## Sources

**Internal (this repo):** `docs/ONTOLOGY.md:59-71` (§2 layers), `:180` (extractedFrom / sourceKind),
`:461-463` (§4.4 bytes live outside the graph), `:520` (content-addressed `eventId` from session_id +
captured_at), `:531` (`AgentSession` eventKind), `:1130` (`agentSession` is the provenance bridge) ·
`tortoise-capture.ts` `SpoolMeta`, `listSpoolEntries` (the conflated catch, and the "not valid JSON" lie about bytes it never opened —
 both removed here), `recordDiscard` (accepts `retained`), the retention doctrine comment,
the two `retained: true` arms, `SPOOL_DIR` (one shared directory), `healthScan` (read path: unread
meta is PENDING), the `spool DISCARD` warning line ·
`tortoise/capture_spool.py` module docstring (orphans reclaimable), `DISCARD_TRANSCRIPT_UNREADABLE` /
`DISCARD_TRANSCRIPT_TORN`, `RETAINED_DISCARD_REASONS`, `is_lost_discard`, the "NOT re-fileable" note
(no verb can reconstruct the session id), `list_spool_metas` (the `OSError` catch), the `prune_spool` orphan pass (orphans spent
AFTER the filed pass, never before it) · `docs/architecture/PI-CAPTURE-ARCHITECTURE.md:160` (orphan unreachable, reaped) ·
`tortoise/hook_install.py` (`PI_EXTENSION_NAME`, `_pi_artifact_contract()` — the install contract) · issue #11802 §1–§7 (the measured
root cause and scale — cited, not re-derived).

**External (Perplexity, model `sonar`, 4 queries — see the PREFLIGHT line):** poison-message /
unreadable-vs-malformed policy and the invariant *no successful read, no destructive decision*;
quarantine requires a successful metadata read; `rsync --partial`, `logrotate delaycompress`,
`systemd-tmpfiles` as the tolerance analogues; multi-uid spool ownership (systemd `StateDirectory=`,
`/var/spool/mail`, setgid and sticky-bit limits); observability (`retained`/`retryable`/`permanent`
dispositions, counters vs structured ledger, why a combined counter answers the wrong question).
⚠️ **single-category:** all four are Perplexity (rung 1 and rung 2 are one engine, so this is ONE
source category, not two) — the convergence claims are consistent across all four queries, but no
independent second engine corroborated them; Brave is not loadable and `exa` is prohibited.
