# Scoping + Threat Model — #4098: destruction predicates on a shared multi-user `$TMPDIR`

> **Issue:** daniel-ospina/tortoise#4098 — `security(reaper): harden the destruction predicates on a shared multi-user $TMPDIR (CWE-377 residual, out of scope in #4068)`
> **Tier:** `Level: task` / `complexity:standard`
> **Worktree:** `.worktrees/4098-tmpdir-hardening` on `fix/4098-tmpdir-hardening`
> **Domain declaration:** **adversarial** (see `### Adversarial Threat Surface`) → review bounded at 2 cycles
> **Verdict:** **(B) — a concrete, demonstrated bypass exists.** The current containment
> discipline is *not* sufficient on a shared `/tmp`. **Two** mechanical hardenings ship in this PR
> (the symlink-follow marker write and the sibling singleton-lock open, both CWE-377); the
> provenance guard that closes the remaining classes is **escalated to the human** as a decision,
> filed as **#4136** — and deliberately **not** implemented here, because it changes the reaper's
> reach on a shared box.

---

## Phase 1 — problem-diverge (the issue's framing, checked)

The issue asks to threat-model the reaper's **destruction path** on a shared `/tmp` and either
harden it or document why the existing discipline is sufficient. It names three surfaces:
`_is_ephemeral_dir`'s containment semantics, the `REAPER_OWNED_MARKER` gate + the 9-guard chain
in `_remove_stale_socket_dir`, and the discovery↔action TOCTOU.

**Re-framing (measured).** Those three are the *symptoms*. The property that actually decides
safety is **provenance**: *every destruction decision is authorized by filesystem evidence read
out of a directory the decision-maker does not own.* Concretely:

| Predicate | What it proves | What it does NOT prove |
|---|---|---|
| `_is_ephemeral_dir(dbdir_real, tmpdir_real)` | the path is under the tempdir and a component matches `EPHEMERAL_PREFIXES` | **who owns the inode** |
| `REAPER_OWNED_MARKER` present | a marker *name* exists in the dir | that the reaper (or any trusted writer) put it there |
| the 9-guard chain | the socket is dead, the pid dead, the dir aged | that any of those files were written by a trusted process |
| `registry["pidfile"]` / `redis.config` | a pid number | that the pid belongs to a redis server **of ours** |

`_is_ephemeral_dir` is a **scope** predicate (stay inside our namespace); it was never a
**provenance** predicate. Treating it as a trust boundary is the error.

**Missing from the issue's enumeration — a fourth destruction predicate.** `reap()`'s post-kill
cleanup rmtrees **two** paths: the record's `dbdir` *and* `registry["dir"]` — the latter read
verbatim from the attacker-authorable `redis.config`/`*.settings`, gated only by name
containment:

```python
reg_dir = (record.get("settings") or {}).get("dir", …)      # attacker-authored
for d in dict.fromkeys([dbdir, reg_dir]):
    if _is_ephemeral_dir(os.path.realpath(d), tmpdir_real):  # name-only gate
        _cleanup_tempdir(d)                                  # shutil.rmtree
```

So the destruction surface is **four** predicates, not three: (1) SIGTERM a pid, (2) the
marker write, (3) the rename-aside + rmtree of `dbdir`, (4) the rmtree of `reg_dir`. A hardening
that covers only (2) and (3) is incomplete by construction. The code-review gate then found a
**fifth** surface of the same CWE-377 class, outside the destruction predicates proper: the
singleton-lock `open` (T5).

### Alternative problem framings

- **P1 — "the guards are right, only discovery is exposed"** (the #4068 framing). *Refuted:*
  discovery only chooses *which* dirs get classified; every destruction decision is re-derived
  from files inside the candidate dir, so a crafted dir is indistinguishable from a real one.
- **P2 — "macOS `$TMPDIR` is per-user 0700, so this is moot."** *Partially true, and the reason
  the exposure is latent:* on macOS the tempdir is mode-private, so a foreign uid cannot plant a
  decoy at all. **On Linux `tempfile.gettempdir()` is `/tmp` (mode 1777, sticky)** — the exact
  shared, world-writable sink CWE-377 names. The code path is identical; only the platform
  differs (same asymmetry recorded for the lock scope in `docs/research/2026-08-24-1658-reaper-race/research.md` §3).
- **P3 — "sticky bit makes cross-uid destruction impossible."** *Half true:* `unlink(2)`'s sticky
  check stops a **non-root** reaper from removing another uid's directory — but it does **not**
  stop the **symlink-follow write** (a write *into* the attacker's own dir), and it does not stop
  a root-run scheduled sweep. DAC is not a substitute for a provenance check.
- **P4 — "the fake RESP server is unrealistic."** *Refuted:* the decoy answers `CLIENT LIST` with
  a 5-byte RESP bulk string (`$0\r\n\r\n`); the demonstrated PoC is ~40 lines.

---

## Phase 1.5 — Axis research (Architecture = high; no new deps)

| | Finding | Source |
|---|---|---|
| **canonical** | FIO21-C's answer is a **private root/jail** (a directory the cleaner owns, e.g. `XDG_RUNTIME_DIR` 0700, `PrivateTmp=`, a root-owned store), not a uid comparison | SEI CERT FIO21-C; `systemd.exec(5)`; XDG Base Directory spec |
| **precedent** | `systemd-tmpfiles`, `tmpreaper`, Docker/containerd GC, Testcontainers Ryuk, pytest's `tmp_path` retention each use **one of**: a private root, root privilege, symlink refusal, or a **write-access predicate** — never "trust a file read out of a shared dir". `tmpreaper`'s predicate is *write-access*, not uid equality | `tmpreaper(8)`; `systemd-tmpfiles(8)`; pytest `tmp_path_retention_count` |
| **pitfall** | **`O_NOFOLLOW` protects only the basename.** The canonical write pattern is `openat`/`dir_fd`-anchored (`open(dir, O_DIRECTORY|O_NOFOLLOW)` then `open(name, …, dir_fd=fd)`), or Linux `openat2`+`RESOLVE_NO_SYMLINKS`. **CVE-2018-6954** is the real-world failure of skipping it (fixed in systemd v240) | `open(2)`; CVE-2018-6954 |
| **pitfall** | A **pid read from a world-writable dir is not trustworthy provenance.** Precedents: **CVE-2011-1784** (keepalived `0666` PID file → arbitrary kill) and MIMEDefang (2017, non-root-writable PID file → kill a root PID). Both were fixed on the **write** side; the read side needs an independent binding | CVE-2011-1784; MIMEDefang advisory |
| **Q** | `shutil.rmtree` refuses a **top-level symlink** and uses `lstat→open→fstat→samestat` + `dir_fd` internally, but sets **no `O_NOFOLLOW`** and makes **no ownership guarantee** | CPython `Lib/shutil.py`; verified `_use_fd_functions == True` on this host |
| **Q** | For a unix-socket server, the race-free process binding is `SO_PEERCRED` / `getpeereid()` (works on macOS) or `pidfd_open`+`pidfd_send_signal` (Linux). `tempprefix`/8-char suffix private internals are NOT usable — see #4068 Q1 | `unix(7)`; `pidfd_open(2)` |

**Integration Docs:** none — stdlib only. No `pyproject.toml`/`uv.lock` change.

---

## Phase 2 — problem-converge (confirmed)

> **The reaper's FIVE write/destruction surfaces are authorized by filesystem evidence — or acted on
> with an open() — in a shared, world-writable tempdir whose entries may be owned by a different
> local uid. On Linux `/tmp` (1777) an unprivileged attacker can therefore** (i) **truncate any file
> the reaper's uid can write through a symlinked marker path, and** (ii) **do the same through a
> symlinked `<tempdir>/.tortoise/.reaper.lock`, and** (iii) **make a scheduled `--only-safe` sweep
> SIGTERM an attacker-chosen live pid, and** (iv) **make the kill path `rmtree` an attacker-chosen
> directory under the tempdir.**
>
> *(i) and (ii) are fixed in this PR; (iii)/(iv) and the discovery↔action TOCTOU are escalated as
> #4136.*

**Falsifier:** the framing dies if any destruction path already verifies that the candidate dir
(or the pid it acts on) belongs to the reaper's uid — or if a recorded decision mandates
cross-uid reaping on a shared tempdir. Both were checked and refuted:
`grep -n 'geteuid\|st_uid\|getuid\|os.access' tortoise/embedded_reaper.py` → **no matches**
(the grep was run at scoping time, against pre-#4098 code; this PR then ADDED `geteuid`/`st_uid`
for the lock — that addition is the fix, not a refutation of the finding);
no doc, comment, or `OVERRIDES` line mandates cross-uid reaping (the only related note,
`docs/research/2026-08-24-1658-reaper-race/research.md`, flags the *absence* of a cross-user
test, not a required behavior).

**Confidence:** 100/100 after independent reproduction (fresh-context verifier, same head).

---

## Threat model — the destruction path on a shared `/tmp`

**Asset.** The reaper's authority: `SIGTERM` on a pid, and `shutil.rmtree` on a path. Both run
as the **reaper's uid** — under the documented schedule (`tools/install-reaper-schedule.sh`,
`docs/infra/embedded-reaper-cron.md`) that is the invoking user; a root-run cron is not the
documented deployment but is not prevented either.

**Attacker.** Any local uid that can create an entry in the shared tempdir — i.e. any local user
on a Linux box with the default `/tmp`. The attacker does **not** need the reaper's privileges.

**Trust boundary crossed.** "Can write in `/tmp`" → "can write (truncate) files the reaper's uid
can write" and "can SIGTERM a redis-server the reaper's uid can signal".

### Demonstrated classes (reproduced at `b86f6ef20`; `_real_gettempdir` redirected to a private
scratch dir and `_pgrep_redis_servers` stubbed so the host is never touched)

| # | Class | Mechanism | Verified consequence |
|---|---|---|---|
| **T1** | Symlink-follow marker write (**CWE-377 / FIO21-C**) | decoy `tmpEVILXX/` with a real dead socket + registry + **aged** dir, and `.reaper-owned` a **symlink** to a file outside it | `_remove_stale_socket_dir(rec, dry_run=False)` → `acted is not None`; the symlink target was **truncated to `"reaper-owned\n"`** — **FIXED in this PR** |
| **T5** | Symlink-follow **lock** open (**CWE-377 / FIO21-C**) | `<tempdir>/.tortoise/.reaper.lock` planted as a symlink to a file the reaper's uid can write | `_ReaperLock.acquire()` returned `True` and the target was **truncated to the reaper pid** (e.g. `'16059'`). A FIFO at the lock path **blocked startup** before the SIGALRM watchdog was armed; a symlinked `.tortoise` dir redirected the lock. Found by the code-review gate, not the scoping pass — **FIXED in this PR** |
| **T2** | Attacker-chosen pid SIGTERM | decoy with a fake RESP server answering `$0\r\n\r\n`, a registry `pidfile` naming a **live** pid whose cmdline contains `redis-server`, and an attacker-authored `.tortoise-owners/<deadpid>-0` record | `_run_sweep(dry_run=False, only_safe=True)` — the **scheduled** mode — logged `killed orphan PID …`; victim **exit code -15 (SIGTERM)** |
| **T3** | Attacker-chosen `rmtree` via `reg_dir` | `redis.config`'s `dir=` set to a bystander ephemeral-named dir | the bystander dir (with its data) was **removed** by `reap()`'s post-kill cleanup |
| **T4** | Discovery↔action TOCTOU | the dir at `dbdir_real` is swapped between the guard chain's `realpath` and the action | subsumed by the **missing provenance check**; the marker write is the exploited step (T1). The guard chain re-verifies deadness, but re-verification cannot distinguish a crafted dir from a real one. **The final component is closed by the `dir_fd` anchor; an INTERMEDIATE path component is not (`O_NOFOLLOW` is basename-only)** — reachable only via a nested `dbdir` (pass-1 derived), so it rides with the escalated provenance set |

**Why each existing guard fails to stop T1/T2/T3/T5.** `_is_ephemeral_dir` is satisfied by naming the
decoy `tmp…`; the dead-socket probe is satisfied by `bind()` + `close()`; the age guard is
satisfied by `os.utime`; the #3599 owner-record confirmation is satisfied by writing a
`.tortoise-owners/<deadpid>-0` file; `_pid_is_redis` is a **substring test on `ps` output** and
the attacker never has to touch the victim's cmdline. Every guard reads evidence the attacker
authored.

---

## Adversarial Threat Surface

Declared bound: **2 review cycles**; acceptance = every class declared **fixed in this PR** is
covered by a **mutation-verified** test and green CI; classes not fixed are declared **out of scope
and filed** (never left in the in-scope set).

**IN SCOPE — fixed and test-pinned in this PR**

| # | Adversarial input | Required behavior | Covered by |
|---|---|---|---|
| 1 | A `REAPER_OWNED_MARKER` path that is a **symlink** planted in the candidate dir | The marker write must never follow it, never truncate the target, and must still reap the dir **when the occupant is removable**; an occupant that cannot be `unlink`ed (another uid's entry under a sticky dir, a directory) **fails closed** and the record is skipped | `test_stale_marker_write_never_follows_symlink` (end-to-end through `_run_sweep`; reddens against the pre-#4098 body) |
| 1b | A **HARDLINK** planted at the marker path (a REGULAR file, so `O_NOFOLLOW` + `S_ISREG` both pass) | Truncation must happen only AFTER the gate, and the gate must require `st_nlink == 1`; the attacker's LINK is removed (never the victim's content) and a fresh marker is created | `test_marker_write_never_truncates_a_planted_hardlink` |
| 2 | A **non-regular / unremovable occupant** at the marker path (a directory; a re-planted symlink) | Fail **closed**: never return a non-regular fd, never follow, abort the record | `test_marker_write_non_regular_occupant_fails_closed` |
| 3 | A **FIFO** at the marker path | Never block (`O_NONBLOCK`); remove and replace non-blocking | `test_open_marker_no_follow_replaces_fifo_without_blocking` |
| 4 | A **transient write failure** (`ENOSPC`) on the marker | Skip THIS record only — never propagate out of `reap()`, never abort the sweep | `test_marker_write_error_skips_one_record_not_the_sweep` |
| 5 | A **symlink planted at the lock path** (T5) — the bind-relative `<tempdir>/.tortoise-reaper-<uid>/.reaper.lock`, and a link at the lock DIR | `_ReaperLock.acquire()` must refuse it (ELOOP), never truncate the target; the lock dir must be **owned by the invoking uid** (a foreign-owned dir fails closed) | `test_reaper_lock_never_follows_symlink`, `test_reaper_lock_refuses_a_foreign_owned_lock_dir` |
| 5b | A **symlinked lock DIR**, and a **foreign-owned lock DIR** (T5) | The holder read must be anchored on the dir fd (basename-only `O_NOFOLLOW` is insufficient), bounded, ownership-gated, and use `O_NONBLOCK` | `test_lock_holder_pid_never_follows_a_symlinked_lock_dir`, `test_lock_holder_pid_ignores_a_foreign_owned_lock_dir`, `test_reaper_lock_holder_pid_never_blocks_on_fifo` |
| 5c | A **pre-created lock-dir name we cannot remove** — a directory, symlink, or plain file owned by a foreign uid under a 1777 sticky `/tmp`; and any NON-contention lock fault (a full/read-only tempfs) | EVERY refusal must fail closed AND log — a silent `return False` is indistinguishable from ordinary lock contention, and the pre-creation case is permanent, so silence would stop the reaper without a trace. Ordinary `flock` contention (`EWOULDBLOCK`/`EAGAIN`) must stay SILENT | `test_foreign_owned_lock_dir_fails_closed_and_warns`, `test_symlink_at_lock_dir_refuses_loudly`, `test_lock_contention_is_silent`, `test_lock_write_failure_is_loud_not_silent`, `test_lock_flock_failure_is_loud_not_silent` |

**ESCALATED — declared out of scope, filed as #4136** (these are NOT in this PR's contract)

| # | Adversarial input | Required behavior |
|---|---|---|
| 6 | A decoy dir whose `redis.config` `pidfile` names a live foreign pid (T2) | Refuse to signal a pid not proven to belong to this user |
| 7 | A decoy dir whose `redis.config` `dir=` names a bystander dir (T3) | Refuse to rmtree a path that is not the dir the evidence came from |
| 8 | Discovery↔action TOCTOU / an INTERMEDIATE path component (T4) | Anchor every action on a provenance-checked inode |

**OUT OF SCOPE** (unchanged by this diff)

- Same-uid co-tenants (a process of the *same* uid is inside the trust boundary already).
- The `redis-server` cmdline substring test as a *positive* signal — only its use as the sole
  cross-check for a kill is in scope here.
- Any OS-level isolation (`unshare`, per-suite private `TMPDIR`) — see the rejected alternative E
  in `docs/drafts/1365-reaper-chaos-alternatives.md`.
- A **directory** occupant at the marker path pins that dir (fail-closed): bounded and
  attacker-owned; the pre-change code also refused it (`IsADirectoryError`).

---

## Phase 4/5 — solution-diverge / converge

| # | Approach | Verdict |
|---|---|---|
| **A** | **Provenance guard**: every destruction path requires the candidate dir to be owned by the reaper's euid (`os.stat(…).st_uid == os.geteuid()`, re-checked at action time, `dir_fd`-anchored), which refuses T1-foreign, T2, T3 and T4 in one place | **CHOSEN — but escalated.** It is a behavioral/semantic change (the reaper stops reaping another uid's residue) and needs a human decision on the root-run case. It is *not* shipped in this PR. |
| **B** | **Private tempdir root**: reaper creates an 0700 root and only reaps inside it | **rejected** — `redislite` (third-party) creates the socket dir with a bare `mkdtemp()` directly under the shared tempdir; the reaper cannot redirect it. Contradicts nothing, but it is unimplementable from this side. |
| **C** | **`dir_fd` + `O_NOFOLLOW` marker write** (the canonical openat pattern) | **CHOSEN and shipped.** Mechanical, **strictly tightening** (a non-readable or non-writable candidate dir is now abandoned rather than written through), closes T1's **symlink and hardlink** variants (`st_nlink == 1`, truncate only after the gate). |
| **D** | **Socket-derived pid provenance** (`SO_PEERCRED`/`getpeereid` on the probed socket; `pidfd` on Linux) instead of the pidfile | **deferred** — closes T2 at the root (the pid must equal the process on the other end of *our* socket) but requires a live-server protocol binding and a platform matrix; a candidate to fold into the escalated decision. |
| **E** | **`unshare`/per-suite private `TMPDIR`** | **rejected** — Linux-only, seccomp-fragile test-harness change; already rejected in `docs/drafts/1365-reaper-chaos-alternatives.md`. |

**Chosen this PR: C only** (plus its regression test). C is orthogonal to A and remains correct
under any outcome of the escalation.

---

## Wiring check

| Touch point | Type | Covered by | Status |
|---|---|---|---|
| `_remove_stale_socket_dir` guard 6 (marker write) | internal | this change (was plain `open(…, "w")`) | ✅ |
| `_open_marker_no_follow` | new internal helper | this change + tests 1-4 above | ✅ |
| `_ReaperLock.acquire` (T5) | internal | this change (`O_NOFOLLOW` + 0700 dir + `dir_fd`; was `open(path, "a")`) | ✅ |
| `_lock_holder_pid` | internal | this change (dir-fd-anchored, `O_NONBLOCK`, bounded read) | ✅ |
| `_sweep_quarantine_dirs` marker **read** | internal | **unchanged and still attacker-satisfiable** — `os.path.exists` is `os.stat` and DOES follow a symlink (a planted marker symlink to any existing file passes it; only a dangling one fails). Non-destructive, so not a primitive; the provenance question is deferred with #4136 | ✅ (unchanged) |
| `tests/test_reaper.py` | test | this change (11 new tests across the PR: 1 + 10) | ✅ |
| `docs/scoping/2026-09-18-4098-tmpdir-hardening-scoping.md`, `docs/00_index.md` | docs | this change | ✅ |
| `tools/install-reaper-schedule.sh` (stale lock-path comment), `docs/infra/embedded-reaper-cron.md` | scheduler/docs | lock-path comment corrected in both; the cron doc's own stale lock-path citations (`~/.tortoise/.reaper.lock`, `<tempdir>/.tortoise/.reaper.lock`) were corrected to `<tempdir>/.tortoise-reaper-<uid>/.reaper.lock`; the unrelated `~/.tortoise/reaper.log` LOG path is untouched | ✅ |
| Escalated provenance guard (paths `reap`, `_classify`, `_remove_stale_socket_dir`, `_mark_orphan_confirmation`, `_sweep_quarantine_dirs`) | design decision | **not implemented** — human decision required (#4136) | ⏸ |

---

## Decision required (escalation)

**Should the reaper gain a provenance guard — act only on candidate dirs owned by the invoking
user's uid — and if so, at what scope and with what escape hatch?** The full options, analysis and
recommendation are filed as **#4136** (the T2/T3/T4 residual of this issue; #4098's own deliverable —
the threat model plus the two mechanical symlink-write hardenings (marker + lock) — is complete
here). This PR does **not** change which candidate dirs the reaper may DESTROY — the destruction
predicate is untouched, and the only reach change is the lock's uid-scoping (recorded in the
`OVERRIDES` block below). What it changes is the WRITE path: the two demonstrated symlink-follow
writes (marker, lock) are closed.

The lock hardening was found by the **code-review gate's** security + architecture reviewers after
the scoping pass had declared the surface; it is recorded here as T5 and fixed rather than
escalated, because it is the identical mechanical class on the scheduled path and the sibling
`tortoise/index_lock.py` already carries the same fix (#280).

## OVERRIDES

> **OVERRIDES:** the pre-#4098 guard-6 marker write's *overwrite-in-place* semantics (plain
> `open(path, "w")`, which follows a symlink at the marker path) — the marker is now opened
> through an `O_NOFOLLOW`-anchored dir fd with `O_WRONLY|O_CREAT|O_NOFOLLOW|O_NONBLOCK`, truncated
> only AFTER the open via `os.ftruncate` (never by `O_TRUNC`, which fires before any check). An
> occupant that is not a regular file is `unlink`ed (never followed) and retried once; an occupant
> that IS a regular file with `st_nlink > 1` — a planted HARDLINK, against which `O_NOFOLLOW` and
> `S_ISREG` are both no defence, because the inode lives outside the candidate dir — is refused the
> same way. Fail-closed after one retry.
> Reason: on a shared world-writable tempdir a plain `w`-open is an arbitrary-file-truncation
> primitive (CWE-377 / FIO21-C); the overwrite semantics are preserved for a regular marker (the
> dir still ends up with exactly one `"reaper-owned\n"` regular file, and no write permission on
> the candidate dir is newly required) while the write can no longer be redirected.

> **OVERRIDES:** the reaper singleton lock's `open(<tempdir>/.tortoise/.reaper.lock, "a")` — the
> lock is now opened relative to an `O_NOFOLLOW`-anchored 0700 dir fd with
> `O_CREAT|O_RDWR|O_NOFOLLOW`, and the holder read uses `O_RDONLY|O_NOFOLLOW|O_NONBLOCK` (the
> `O_NONBLOCK` is load-bearing: `_lock_holder_pid()` runs before `signal.alarm` is armed, so a FIFO
> at the lock path would otherwise hang reaper startup forever). Reason: the lock lives
> in the same shared world-writable tempdir (moved there by #1658), so it is the identical CWE-377
> sink — a planted symlink truncated the target and a FIFO hung startup before the watchdog was
> armed.
>
> **OVERRIDES:** the lock dir is UID-SCOPED, `<tempdir>/.tortoise-reaper-<euid>` (it was the fixed
> `<tempdir>/.tortoise`), and `acquire()` fails closed when that dir exists but is not owned by our
> euid. Reason: a fixed name under a shared `/tmp` is pre-creatable by ANY local uid, and combined
> with the ownership check that made the refusal permanent — a zero-privilege denial of the
> victim's reaper. **This is a deliberate reduction in reach:** on a shared-tempdir box a foreign
> uid's reaper no longer contends with ours.
>
> **The residual this accepts, stated plainly:** a hostile uid can still pre-create our exact
> `<euid>` name — as a directory, a symlink, or a plain file — and the 1777 sticky bit means we
> cannot remove it, so it is a targeted denial of OUR sweeps. EVERY refusal path therefore logs
> `"reaper lock unavailable (<reason>) at <path> — refusing to lock; <tail>"`, where the tail
> attributes ownership **only where ownership was established**: the foreign-owned-dir refusal says
> "this path is owned by another uid and can only be removed by its owner or root, so sweeps are
> skipped until then", and every other refusal says "this uid cannot clear the cause, so sweeps are
> skipped until it is fixed". So no variant can fail silently and be mistaken for ordinary lock
> contention. The refusals routed this way are: unopenable dir, unremovable/foreign-owned dir (stat
> failing to read it included), unopenable lock
> file, unwrappable fd, non-regular lock path, `flock` failing for a reason other than
> `EWOULDBLOCK`/`EAGAIN`, and `seek`/`truncate`/`write`/`flush` failing (a full or read-only
> tempfs — `ENOSPC`, `EDQUOT`, `EIO`). ORDINARY contention (`EWOULDBLOCK`/`EAGAIN`) stays SILENT
> by design: a warning there would fire on every concurrent run and be noise. It is logged, not
> prevented. Cross-uid non-interference of the DESTRUCTION paths is likewise NOT established by
> this PR — that is the open provenance question tracked by #4136; the only thing claimed here is
> that a foreign uid's sweeper shares no lock with ours.
