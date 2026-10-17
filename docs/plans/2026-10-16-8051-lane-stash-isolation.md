---
title: "Plan — #8051 lane-scoped stash: `refs/stash` is repo-global, so a bare `git stash pop` takes a peer lane's work"
type: engineering
domain: capability
doc_status: draft
created: 2026-10-16
subjects.team: organisation-design-team
aboutSubjects: organisation-design-team
ownedBy: organisation-design-team
governingAgreement: "#8051 (P2-moderate, complexity:standard)"
aboutObjects: refs/stash, worktree-lane-protocol, lane-stash
---

<!-- research-path: none (justified-skip: zero new third-party deps. Every git behaviour asserted below was read from the SHIPPED `git stash` man page (Apple Git-155, git 2.50.1) and MEASURED in this session in throwaway temp repos — see "Measured git behaviour". No external research route was available: the Perplexity tooling in this lane reports `PERPLEXITY_API_KEY not set`, so nothing below rests on an unverified web claim.) -->

# #8051 — lane-scoped stash

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** A lane that stashes and restores its own work can no longer apply, drop, or lose a peer
lane's stash — the lane's own entries are pinned under a lane-scoped ref and retrieval resolves by
hash, never by the shared `stash@{N}` register.

**Team:** organisation-design-team

**Issue:** #8051. **Related:** agent-infra #1264 (the same root in the *skills* + guard-extension
surface — out of this repo's tree), #3022 (wrong-cwd writes — a different root: every command in the
recorded #8051 incidents ran in the *correct* worktree), #729 (cross-lane temp-file collision — the
same shared-state family).

## Confirmed problem

`refs/stash` and its reflog live in the **common** git dir (`git rev-parse --git-common-dir`), which
every linked worktree of the repo shares, while `HEAD`, the index and the worktree are per-worktree.
So `stash@{0}` is a repo-global register that every lane writes and **no lane can see is shared from
the command it types**. A `git stash pop` in lane A therefore applies and drops whichever entry
**another lane** pushed last. Three recorded occurrences (#2654, #3539, 7860/8482) — the third fired
on a lane that already knew the hazard, because the failure presents as a *successful pop* with no
conflict, no non-zero exit and no warning. The near-miss is worse than the lost stash: the natural
next step after a successful pop is `git add` + commit, which lands a peer's uncommitted work under
this lane's branch and message.

Not the symptom to fix: "tell lanes to be careful". The register is shared by construction, so the
fix is to stop depending on it for retrieval.

**Objective (from the issue):** a lane's `git stash` operation must never touch another lane's
working tree.

## Measured git behaviour (git 2.50.1, Apple Git-155; temp repos, this session)

| Measured | Result | Consequence for the design |
|---|---|---|
| `git rev-parse --git-dir` vs `--git-common-dir` in a linked worktree | `<common>/worktrees/<name>` vs `<common>` | The per-worktree gitdir is a safe place to key lane state; `refs/stash` is not. |
| A `stash push` in lane B while lane A holds `stash@{0}` | A's entry renumbers to `@{1}`; A's unqualified `pop` applies **and drops B's entry** (B's untracked file appears in A's tree) | Reproduction is exactly the issue's; no mistake by either lane is required. |
| `git stash push --ref` / any per-worktree or namespaced stash form in `git stash push -h` and the man page | **Does not exist** | Cannot ask git for a lane-scoped stash; the lane scope must be the tool's. |
| `git stash create [-u]` | Accepted `-u` **silently** but produced only a 2-parent commit — untracked files are **not** captured; no pathspec support either; does not clean the tree | Rejected as the creation primitive: it loses `-u` fidelity and forces a reimplementation of `stash push`'s cleanup. |
| `git stash apply <sha>` with the sha not on top | Restores tracked **and** untracked files from the third parent; leaves the shared register and the peer's entry untouched | The safe retrieval primitive. |
| `git stash drop <sha>` | `error: '<sha>' is not a stash reference` | There is no hash-addressed drop — the issue's own comment. |
| `git log --walk-reflogs --format='%H %gs' refs/stash` after a peer pushed on top | Still yields **our** commit, found by the unique message nonce | Deterministic way to capture our own sha even under a concurrent peer push (the push does not print it). |
| `git reflog delete --updateref 'refs/stash@{i}'` | Removes the entry at position `i` and leaves the rest, but does **not** drop `refs/stash` when the reflog empties (measured — the first draft of this row asserted the opposite): the ref still resolves at the removed sha and a bare `git stash pop` applies **and drops** it | Works, but `prune` must drop `refs/stash` itself when it takes the last entry, and the specifier is resolved **at delete time** — see the rejected alternative below. |

## Rejected alternatives (and when they would have been better)

1. **Guard only: assert `stash@{0}`'s `WIP on <branch>` matches the current branch before popping.**
   Cheap, and it is the issue's own first suggestion. Rejected as the *primary* fix: it protects the
   pop half only — it does nothing about a lane's entry being dropped by a peer, about `stash list`
   reading peer entries, or about `clear`/`gc` losing the object. It also races: the issue's second
   occurrence measured **two different `stash@{0}`s seconds apart**, so a pre-pop check that is not
   atomic with the pop re-races the same window. Kept as `lane_stash check` (the *detector*, which
   the acceptance criteria ask for) but not as the whole fix. *Would have been better* if the only
   goal were cost.
2. **Reimplement the stash object with plumbing** (`commit-tree` with a hand-built third parent for
   untracked files, via a scratch `GIT_INDEX_FILE`), so `refs/stash` is never written at all.
   Rejected: it reimplements `stash push`'s content selection (pathspecs, `-a`, conflict handling,
   cleanup) and inherits every edge case of a feature upstream maintains. The tool instead uses
   **git's own `stash push`** for creation and immediately pins the resulting commit under a
   lane-scoped ref, so the shared register is written (as git itself writes it) but is never *read*
   for retrieval.
3. **Make `pop` drop the shared-register entry it applied** (the git behaviour it replaces).
   Rejected on measurement: the drop is positional (`refs/stash@{i}`, re-resolved at delete time), so
   a peer's push between the verification and the delete removes **a different entry** — possibly an
   entry that a bare-git lane never pinned, i.e. exactly the silent loss this issue is about. The
   tool never deletes from the shared register implicitly; `lane_stash prune` does it explicitly,
   with the pin-or-applied precondition below.
4. **Per-lane JSON registry of entries (in the per-worktree gitdir) as the source of truth.**
   Rejected: a lost update in the registry loses the pointer to live work, and the ref store already
   provides exactly this (gc-protected, name-ordered, CAS-allocatable). The ref *is* the registry;
   the stash commit's own message carries the human metadata. This is also why the application
   record `pop` writes is a **ref** (`refs/lane/<key>/applied/<sha>`) and not a JSON entry: a lost
   update there would make `prune` refuse (conservative), but a ref keeps it CAS-correct anyway.

## OVERRIDES (deliberate departures — also relayed as a comment on #8051)

**OVERRIDES:** `git stash pop` drops the entry it applies — `lane_stash pop` deliberately leaves the
shared-register entry in place because the positional reflog delete can drop a bare-git peer's
unpinned entry (measurement row 8 above); reclamation is the explicit `lane_stash prune`.

## Design

One primitive, in `tools/lane_stash.py`:

- **lane key** = sanitized worktree directory basename + `sha1(worktree root)[:8]`, deliberately
  BRANCH-INDEPENDENT: a lane keeps its pins when it checks out another branch to take a baseline
  (the incident that prompted this tool *was* a baseline stash), and the path hash makes two
  worktrees — or two worktrees forced onto one branch — distinct.
- **`push`** runs `git stash push -m "lane_stash <nonce> [<lane-key>] <label>"` (flags
  `-u`/`-a`/`--paths` pass
  through), then finds **our own** commit by the nonce in `git log --walk-reflogs refs/stash` — so a
  peer's push landing during our push does not mis-identify it — and pins it at
  `refs/lane/<lane-key>/stash/<n>`, allocated with a CAS create (retry on collision).
- **`apply` / `pop` / `show` / `drop`** resolve the entry from `refs/lane/<lane-key>/stash/<id>` and
  act on the **sha**: `git stash apply [--index] <sha>`. `pop` = apply, then one atomic CAS move of
  *the lane ref only* to `refs/lane/<lane-key>/applied/<sha>` (the durable application record).
  A foreign entry cannot be resolved, so it cannot be applied; a peer's push cannot renumber a lane ref.
- **`list`** prints this lane's entries (id, sha, message, liveness) and, separately, an advisory
  enumeration of the shared register naming which entries are foreign, which are pinned by a lane ref
  and which are unpinned debris.
- **`check`** — the executable detector: exit 1 when a bare `git stash pop` in this worktree would
  take a **foreign** entry — a peer lane's (identified by the `[<lane-key>]` marker the tool writes
  into every message, so two worktrees forced onto ONE branch are still distinct), or any entry whose
  origin cannot be read: an expired/absent `refs/stash` reflog, an unreadable subject, or a
  marker-less entry whose subject names the current branch (a branch name identifies a BRANCH, never
  a lane, and the pre-existing register is marker-less bare-git entries by construction). A FOREIGN
  lane ref or `[<lane-key>]` marker is decisive and OUTRANKS this lane's own pin: a cross-lane
  `recover` could otherwise leave one sha claimed by both lanes and `check` would clear a bare pop on
  a peer's work; exit 0 only when the top entry is this lane's own AND no other lane's ref or marker
  names it, or the register is empty AND `refs/stash` does not resolve (an emptied reflog over a
  resolving ref is the emptied-register hazard, and `check` fails closed on it). Unpinned debris is
  reported as an
  ADVISORY note, not a failure: a bare pop takes only the TOP entry, so failing on entries below it
  would refuse states that are genuinely safe. Safe to call from a pre-flight.
- **`recover <sha>`** — the repair recipe from the incident: `git stash store -m "<original subject>"
  <sha>` **and** pin it, so the restored entry is retrievable by this lane. It REFUSES an entry that
  another lane's ref or `[<lane-key>]` marker names, or one already ENUMERATED by the register that is
  not provably this lane's (nothing was dropped, so `recover` would only mint ownership; `-m` asserts
  it deliberately): pinning a peer's entry here would leave it claimed by both lanes and `check`
  would then clear a bare pop on work that is not this lane's. A peer's entry is restored with bare
  `git stash store`, or by that lane from its own worktree.
- **`prune`** — explicit reclamation of the shared register: an entry is removed only when the
  delete cannot lose work — its object is **pinned by a lane ref**, or this lane left a durable
  **applied-record** at `refs/lane/<key>/applied/<sha>` (written atomically by `pop` on a
  successful apply, because `push` writes the message marker BEFORE `_pin` runs and the marker is
  therefore not evidence of application); positions are walked high→low, and the sha at each
  position is re-verified immediately before the delete. That check-then-delete is not atomic with
  git's positional `reflog delete`, so `prune` also guard-pins every entry it intends to KEEP at
  `refs/pi-lane-prune-guard/<key>/<sha>` (outside the `refs/lane` namespace) and re-stores any entry a
  concurrent push's position shift caused it to remove — every entry it intends to keep and that was
  present at the start keeps a handle (an entry bound by a lane pin or this lane's applied-record is
  collected deliberately, and `_clear_applied` drops that record once the entry is gone). The state
  comparisons are **counter** comparisons: `recover <sha>` of a sha already in the register prepends
  a duplicate, and a set-style comparison would read a correct delete of one occurrence as a
  concurrency event. Run it when
  the register is quiet: an entry pushed during the prune is outside the guard set, and because git
  offers no compare-and-delete a shift that removes one cannot be undone — `prune` compares the
  register's whole sha state against the expected one after each delete, so a shift that changes that
  state is **detected and reported, never silently absorbed**. When it
  takes the LAST entry it also drops
  `refs/stash` itself (git does not, and a resolving ref over an empty reflog is both live for bare
  git and a fail-closed wedge for `check`). Any other unpinned entry — a peer lane's, bare git's, or
  this lane's own never-applied pin-failure entry — is reported and left alone. A register can also
  DIVERGE from its ref (a bare `git reflog delete` without `--updateref` leaves `refs/stash` on a sha
  the register no longer enumerates). `prune` **refuses** any non-orphan diverged register before it
  builds a guard or deletes anything — whether or not other refs name the head — because `reflog
  delete --updateref` would MOVE the ref to the reflog's new top, the head cannot be re-listed (`stash
  store` no-ops while the ref already equals the sha, and dropping the ref would delete the whole
  reflog with it), and the positional pre-check misreads `stash@{0}` while the ref diverges. (The
  EMPTY-register orphan is still handled by `_reclaim_orphan_refs_stash`, which is the one diverged
  state with a safe exit.) `check`
  stays fail-closed on the state and the operator reconciles the ref deliberately.
- **Restoration is verified, not inferred from an exit code.** `git stash store` returns 0
  *without* creating a reflog entry when `refs/stash` already points at that sha (measured) —
  precisely the state an emptied reflog leaves. `recover` and `prune`'s reconciliation therefore
  check the register after storing, and only report success on the verified state. The repair —
  drop the stale ref and store once more — is taken **only when the enumerated register is empty**
  (the genuine orphan). A `refs/stash` diverged from a NON-empty reflog (a bare `git reflog delete`
  without `--updateref` leaves exactly that) still holds peer entries, and dropping the ref would
  delete the whole reflog with them: that state is **refused, never repaired destructively**. A
  failed re-store puts the ref back rather than leaving the object handle-less. Storing MOVES
  `refs/stash`, so a head that the store would leave without a handle — the ref points at a sha the
  register does not enumerate and no other ref names — is guard-pinned **before** the store, in the
  empty register AND in a register still holding other entries (a diverged ref); a head another ref
  already names is left alone, so no redundant guard is leaked. An unpinnable head refuses the store
  (fail-closed).
- **Residual race (documented, NOT detectable).** `prune` compares the register's whole sha state
  against the expected one after each delete, which catches every interleaving that changes that
  state. It is not a proof. A push landing *inside* the delete and a concurrent remover taking the
  intended sha can cancel out in the multiset: the register ends exactly as a clean delete would
  leave it, so a during-prune push can be the entry removed and `prune` reports success. Git exposes
  no compare-and-delete (`reflog delete` only takes positions) and the two histories are
  observationally identical, so no check can separate them — this is why `prune` is safe to run only
  when the register is quiet, and why the tool refuses to reclaim anything it cannot *prove* is
  already restored. `test_prune_cannot_distinguish_a_cancelled_shift` pins this residual.
- **Orphan retention.** When an emptied register leaves `refs/stash` pointing at an entry no pin and
  no applied-record names, `prune` retains it; it now guard-pins the object rather than relying on
  `refs/stash`, because the reconciliation's `stash store` can move that ref and would otherwise have
  destroyed the handle the retention message just promised. A retained guard has exactly two
  lifetimes: it is released once `recover` puts the entry back under `refs/lane` — under **any** lane
  key, since another worktree may be the one to rescue it — or when the prune that created it sees
  the sha back in the register, which releases **only its own** guard: a foreign guard for the same
  sha may belong to a concurrent prune that is still deleting positions, and "in the register" is
  exactly the transient state a guard exists for. `lane_stash list` prints any still held and, per
  guard, whether another ref names the object, so the namespace can be enumerated and cleared rather
  than silently keeping objects un-gc-able forever.

Out of scope (reported, not absorbed): the skills text (`using-git-worktrees`, `commit-workflow`) is
a symlink to agent-infra and belongs to agent-infra #1264; the `main-worktree-guard` extension
deliberately does not classify stash inside a linked worktree ("Worktrees are ISOLATED"), and that
extension is also out of this repo.

## Survey: how the fleet's own artifacts use stash (issue Research item 2)

`git grep -n 'git stash'` over this repo, plus the skill/AGENTS surfaces the lane actually reads:

| Artifact | What it says | Verdict |
|---|---|---|
| `AGENTS.md` (this repo) | no stash instruction at all before this change | the rule added by this PR is the first in-repo guidance |
| `agent-infra/skills/executing-plans/SKILL.md:278`, `:291`, `:819` | an in-hub `git stash push` is **BLOCKED** (the M4 hub-disorder gate sanctions only recovery verbs); the sanctioned route is the dirty-hub salvage path, and inside a worktree there is no "changes on main" to stash | the hazardous primitive is GONE — agent-infra `00d60c51` replaced `git stash push -m "pre-<branch>-wip"` (the text this row originally quoted, then at `:291`) with the block. The original finding stays on agent-infra #1264 |
| `agent-infra/skills/commit-workflow`, `using-git-worktrees` | no stash instruction | #1264's "name the hazard" half is still open |
| `eldato/AGENTS.md:155` | "Always `git stash` before `gh pr merge`" | repo-specific instance of the same teaching; recorded on #1264 rather than fixed from this worktree |
| `docs/plans/2026-09-03-*.md`, `2026-09-06-2249-*.md`, `2026-10-11-2081-*.md` (3 files) | recommend a bare `git stash`/`git stash pop` as a measurement technique | historical plan docs — not edited (they are records); the systemic fix is the `writing-plans` half of #1264 |
| `extensions/main-worktree-guard/classify-git.mjs:99` (the `stash-pop` rule) | already blocks `git stash pop\|apply\|drop\|clear` — but only in the MAIN checkout ("Worktrees are ISOLATED", by design) | confirms the linked-worktree case is unguarded and out of this repo's tree (#1264) |

Nothing in this repo *invokes* stash in tooling, so no internal caller needed rewiring; the tool +
rule is additive.

## Integration Surface Map

| # | Surface | Type | Data Flow | Test Layer | Contract | Key Failure Modes |
|---|---|---|---|---|---|---|
| 1 | `git stash push` (worktree mutation) | External process / State | Out (writes tree + shared register) | Integration (real git, temp repo) | rc + stderr; "No local changes to save" is rc 0 with no entry | push saved nothing → tool must say so, not fabricate an entry; push failed → propagate rc |
| 2 | `refs/stash` reflog (concurrent, shared) | Concurrent | Both (read for locate; written by git) | Integration (two REAL worktrees, real concurrent pushes) | `stash@{N}` positions are NOT stable; `%H` is | peer push between our push and our locate → must still find OUR sha by nonce |
| 3 | Lane-scoped refs `refs/lane/<key>/stash/<n>` | State (git ref store) | In/Out | Integration | CAS create; id = path segment; gc-protected | id collision under concurrent push → retry; ref missing after pop |
| 4 | `git stash apply <sha>` | External process | In (tree mutation) | Integration | restores tracked+staged+untracked; conflict → `UU` + rc≠0 | apply conflict must NOT delete the lane ref (work preserved); foreign sha must be unreachable by construction |
| 5 | Per-worktree identity (`--git-dir` vs `--git-common-dir`, worktree path) | Config/State | Read | Unit | lane key from the worktree basename + path hash, branch-independent | two worktrees on one branch → distinct keys; a branch switch inside a lane must NOT orphan its pins; detached HEAD → stable key |
| 6 | `check` exit contract | CLI contract | Out | Integration | 0 = this lane's own top entry (a `[<lane-key>]` marker, or a pinned sha) or an empty register, 1 = foreign top (another lane's marker) / an entry only a branch name could attribute / unreadable origin, 2 = unreadable register (a state error) | must fail CLOSED: unreadable state ⇒ non-zero, never "safe"; unpinned debris is advisory, not a failure |

### Bug Pattern Flags

- **Race conditions (shared resource):** the `refs/stash` register under concurrent lane pushes —
  required verification: a test whose two lanes are two real worktrees and whose pushes are real
  sequential `git stash push` calls, asserting the victim's entry survives and is not applied.
- **Conditional guards:** `check`'s verdict gates; both sides of every branch get a test (safe top,
  foreign top, same-branch peer lane, marker-less entry naming this branch, unreadable subject,
  unreadable register, a `refs/stash` left resolving over an emptied reflog).
- **Silent function skips:** `push` when there is nothing to stash → asserted, so the tool can never
  report a pinned entry it did not create.

### Checklist Notes

- Failure modes enumerated and tested: nothing-to-save; peer push during locate; apply conflict;
  missing object (post-gc); no lane entries; unpinned shared debris; detached HEAD.
- No network, no Docker, no FalkorDB, no third-party API — the whole matrix runs hermetically, so it
  is registerable in the `core` (non-DB) surface and runnable with `TORTOISE_TEST_CARVE_OUT=1`.

## Verification Plan

| Layer | Applies | Command |
|---|---|---|
| Unit | yes — lane key, entry id allocation, subject parsing | `.venv/bin/python -m pytest tests/test_lane_stash.py -q` |
| Integration (real git + 2 worktrees) | yes — the acceptance reproduction | same file |
| E2E / pgTAP / UX | no | nothing crosses a browser, SQL or UI boundary — this is a CLI tool + a git contract |

## Tasks

### Task 1: the tool

- Create: `tools/lane_stash.py`
- Steps: lane-key derivation → `push` (nonce locate + CAS pin) → `list` → `apply`/`pop`/`drop`/`show`
  → `check` → `recover` → `prune` → `argparse` CLI with documented exit codes.

### Task 2: the reproduction tests (written first, per TDD)

- Create: `tests/test_lane_stash.py`
- The acceptance test is the literal 2-worktree scenario: lane A pushes, lane B pushes, lane A
  `pop`s; assert (a) A's tree holds exactly A's diff and **not** B's file, (b) B's entry is still
  present in `git stash list` with its original sha, (c) A's entry is intact for B.
- Plus: the hazard detector (`check`), untracked fidelity, apply-conflict preservation, foreign-top
  pop, nothing-to-stash, per-worktree lane keys, `recover`, `prune` pin-or-applied precondition.

### Task 3: registration + guidance

- Modify: `config/ci-surfaces.yml` (register `test_lane_stash.py` under `core`)
- Modify: `tools/ci_selection.py` (`CORE_ALSO += tools/lane_stash.py`, so a tools-only diff selects
  `core` and the pin runs on the change it guards)
- Modify: `AGENTS.md` (lane rule: the hazard, the safe alternative, the recovery recipe)
- Modify: `docs/00_index.md` (register this plan)
- Verify: `python tools/ci_selection.py --integrity`

## Acceptance criteria

1. The 2-worktree reproduction applies **0** foreign entries and leaves the peer's entry present in
   `git stash list` with an unchanged sha.
2. `lane_stash check` exits non-zero and names the foreign lane when the shared top is a peer's, and
   for a marker-less bare-git entry whose origin only a branch name could attribute — including a
   branch name that matches the current one — and exits 0 when the top is provably this lane's own.
3. `pop` never deletes from the shared register (it records the apply durably and releases only the
   lane pin); `prune` removes an entry only when a lane ref pins it or this lane's applied-record
   names it — a never-applied entry (e.g. one whose `_pin` failed) is refused. This is the
   **intent**, not a blanket invariant: `prune`'s delete is positional, so the documented residual
   (see the `prune` bullet's "Residual race") can remove a during-prune entry while reporting
   success and must be read with this criterion.
4. `AGENTS.md` states the hazard and names the safe alternative (issue Target: 1 guidance line).
5. `python tools/ci_selection.py --integrity` is clean with the new test registered.

## Learnings

- `git stash create -u` accepts `-u` **silently** and still emits a 2-parent commit (untracked files
  dropped). A tool built on it would lose untracked work with no error — measured, not read. This is
  why the tool uses `git stash push` for creation and only for creation.
- `git stash push` does not print the sha, so the entry you just created is not directly addressable.
  The uuid nonce in the message + `git log --walk-reflogs refs/stash` is the only race-free way to
  identify it, and it survives a peer's push landing on top.
- `git reflog delete --updateref 'refs/stash@{i}'` resolves the position at DELETE time, so a
  check-then-delete cannot be made atomic. That single measurement is what turned "pop should drop
  like git does" into an explicit `OVERRIDES` and a conservative `prune`.
- The same command empties the reflog WITHOUT dropping `refs/stash` (measured; the first draft of the
  plan asserted the opposite), so `prune` must drop the ref itself when it takes the last entry —
  otherwise the reclaimed entry is still live for bare git and `check` fails closed forever on a ref
  no lane_stash command could clear.
- A first draft of the fail-closed test asserted that `git stash push -m "<no branch prefix>"`
  produces an unattributable subject — it does not: `push -m` always prefixes `On <branch>:`. Only
  `git stash store -m` produces a bare subject (which is how the real register's `RESCUED by …` notes
  were written). The test now uses `store` — a reminder that the incident evidence and the
  reproduction must be built on the same primitive.
- The repo's `tools/` directory is a flat `NON_PYTHON_PREFIXES` entry in `tools/ci_selection.py`: a
  tools-only diff selects **no** surface, so a new tool must ALSO be added to `CORE_ALSO` or its pin
  never runs on the file it guards.

## Approvals

- Human gate: none required — no MCP tool registry change, no SDK surface change, no migration, no
  production state write; the change is a new tool + tests + one guidance block.
