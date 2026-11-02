# main-red-prereg — the pre-registration guard vs the repo's own squash merge

Box: cell (this worktree), branch `t-main-red-prereg`, PR **#10546**.
Written 2026-11-01 by the lane; a killed lane must still leave its diagnosis, so this file is the
artifact, not the reply.

---

## Step 1 — THE DIAGNOSIS, VERIFIED ON A LIVE RUN (not taken on faith)

`gh run view 41342506404` (head `2e84e80df`, the red main tip):

* `gh run view --json jobs`: `workflow-lint` success, **`changes` FAILURE**, `surface-guard` success,
  `manifest-integrity` success, `canary-streak` success, `ci-introduced-failure` success; **8 jobs
  `skipped`** behind it (`needs: changes`), and `python-ci-gate` red because its needs never ran.
* **The diff selector SUCCEEDED** — so the brief's claim is TRUE. From the `changes` job's own log
  (`gh run view --job 124955309445 --log`):
  `selection: {"surfaces": ["api","battery","core","ep","onboarding","sdk","classify","eval"],
  "full": true, "test_files": "ALL", ...}` — the FULL matrix was chosen.
* **The failing STEP** is `Capstone pre-registration gate — the ordering half (#9075)`:
  `python3 tools/capstone_preregistration_check.py --dir docs/evidence/7949-capstone --no-corpus
  --dir docs/evidence/5087-hypothesis`
  ```
  === docs/evidence/7949-capstone/PRE-REGISTRATION.md ===      -> OK
  === docs/evidence/5087-hypothesis/PRE-REGISTRATION.md ===
     FAIL — 1 problem(s):
       - s3-ablation-a1075dfc.log: added at 1793558646 which is not strictly after the
         pre-registration's add-commit 1793558646 — ... (carries 0 BEGIN and 0 END verdict markers ...)
  ```
  exit code 1. Only the #10468 bundle fails.
* **Structural cause CONFIRMED**: `a1075dfc2` (`prereg(10463): amend definitions before any run`) is a
  branch commit that is **not an ancestor of main**; the squash `cfa3de857` added
  `PRE-REGISTRATION.md` **and** `s3-ablation-a1075dfc.log` in ONE commit, so
  `git log --diff-filter=A` reports one add-commit for both and `min(add_epochs)` is equal.
  The measurement commit on the branch was `325f2a1a5` (`00:26:01Z`), 13m44s after the bars
  (`00:12:17Z`): **the ordering existed, and the merge strategy deleted it.**

## Step 2 — ROUTE DECISION, with the contradiction test recorded

**ROUTE (A) — fix the guard.** Route (B) — re-add the evidence log in a fresh commit so its add-epoch
is later — REJECTED on two independent grounds:

1. it satisfies the letter of the guard while the real ordering is unchanged: exactly the fabrication
   the guard exists to prevent;
2. **it does not even work.** the epoch readers take the OLDEST add, so a delete-and-re-add keeps the
   squash's epoch — already recorded in the tool's own docstring as `#9075 review r3 P3`.

**CONTRADICTION TEST (run first).** Decisions checked: #9075's method constraint ("pre-register before
running — corpus, split, scoring"), #9736/#9738 (the registration's history claims, "amended in the
open"), #10463/#10468, and the bundles' own errata ("editing it would re-cut the registration this
falsification is reported against").

* (B) CONTRADICTS #9075's method constraint → refused, not adopted, not "adopted with a caveat".
* (A) preserves it: the attack shape (the log's add-commit is an **ancestor** of the registration's)
  is a hard failure **no disclosure can waive**; only the genuinely *unorderable* state — identical
  add-commit — is disclosed.

**EXTERNAL CONVERGENCE** (`web_search`, model `sonar`, 1 query / 6 results returned):

* OSF registrations are timestamped **read-only snapshots**; later changes are versioned amendments.
* `preregister`-style experiment ledgers: canonical JSON + SHA-256 + "external anchors to detect
  unregistered edits or **rewritten history**".
* `reproducible-science/prereg`: "git history can be rewritten and therefore anchors digests
  externally".

⇒ the field binds by **content** and **discloses**; it never relies on position in a rewritable
history. That decided (A) over (B), and it decided the *shape* of (A): bind by commit identity and
topology, disclose the unorderable case, never compare clock fields.

## Step 3 — THE FIX (implemented; NOT yet proven on main)

Ten commits, ten fresh-context review cycles (9, 6, 10, 5, 4, 6, 2, 4, 3, 3 findings). The guard now:

* decides ordering by **commit identity + ancestry**, never by `%ct` — which is
  `GIT_COMMITTER_DATE`-controlled. This was the single most-repeated defect class: cycles 5, 8 and 10
  each found a *different* arm trusting a clock field;
* takes the verdict over **every** add-commit, so a backdated delete-and-re-add cannot choose the pair;
* requires a **disclosure** (`atomic_add_ordering` in `preregistration.json` — deliberately NOT the
  pins block: cycle 2 proved a pins edit re-cuts `document_sha256`, which makes the record describe a
  document that post-dates the run, which the run probe refuses and the bundle's errata forbids) for
  the atomic state, unless the log proves it measured nothing;
* bounds both arms by **topology** (`_late_touches`), so an edit after the registration is a failure
  however its dates were set;
* never prints a declaration, a waiver, or a `verified` claim that the deciding evidence does not
  support (a `--json` consumer reads `run_order_claim`).

**LOCAL PROOF — the exact CI command, on full history (this box was unshallowed):**
```
=== docs/evidence/7949-capstone/PRE-REGISTRATION.md ===   OK   run-order claim: verified      exit 0
=== docs/evidence/5087-hypothesis/PRE-REGISTRATION.md === OK
    machine record run-order claim: verified (atomic-add DECLARED: s3-ablation-a1075dfc.log)
    ordering: ATOMIC-ADD declared — ... the disclosure in preregistration.json.atomic_add_ordering
    is the record of it:
      s3-ablation-a1075dfc.log: squashed by cfa3de857 measure(10463): ... ratio 0.870 (#10468) ...
      s3-ablation-a1075dfc.log: a1075dfc2 prereg(10463): amend definitions before any run ...
      s3-ablation-a1075dfc.log: 325f2a1a5 measure(10463): the S3 prior-injection hypothesis ...
EXIT=0
```
`tests/test_capstone_preregistration_9075.py` + `tests/test_s3_prior_ablation.py`: **289 passed**.
`PRE-REGISTRATION.md` byte-identical to `origin/main`; `document_sha256` unchanged (`70264efe…`).

## STEPS 3/4 (green main, railed PRs) — NOT DELIVERED, and why, without euphemism

* **No `python-ci.yml` run with `conclusion: success` on main is quoted, because none can exist yet** —
  the fix is a PR and `gh` returned `HTTP 403: API rate limit exceeded` when I tried to read runs. A
  failed call is not evidence of green *or* red ⇒ **`UNMEASURED(GH-CONTENDED: gh run list
  --branch t-main-red-prereg)`**.
* **No PR was railed.** The admin rail needs the same exhausted API budget, and the review gate's
  attestation needs the reviewer verdict posted as a PR comment (also an API write). ⇒
  **`UNMEASURED(GH-CONTENDED)`**. Nothing was merged; no override env var was used.
* **The review loop did NOT reach a clean verdict.** Cycle 10 found 3 issues; all 3 are fixed and
  probe-verified in `226a004c9`, but **no cycle-11 review has run**. This is an **ESCALATION exit, not
  a completion** — every reported defect is fixed; what is missing is the confirming pass.

## Step 5 — THE STRUCTURAL FINDING (the part that outlives this PR)

**A guard whose evidence cannot survive the repository's own merge strategy will recur on the next
pre-registration PR, and the recurrence is a property of the guard, not of the author.** #9075's
ordering half compared two commit dates. A squash merge legally collapses N branch commits into one,
destroying the ordering the check reads and making its verdict **permanently false-red** — a state no
honest author can clear, which the fleet then clears by disabling the check. Three lessons:

1. **Never bind an integrity claim to a clock field.** `%ct` is `GIT_COMMITTER_DATE`, which the author
   sets. Three arms of this one tool trusted it, and each produced a *silent-green* exploit when fixed
   naively (backdated edit; backdated re-add; same-second pair). Ancestry is the only ordering an
   author cannot forge without rewriting a sha.
2. **A merge strategy is part of a guard's contract.** If the repo squashes, "A was committed before
   B" is not merely hard to check — it is *unobservable in the merged history*. Any check phrased that
   way must be phrased differently or must declare its third outcome loudly. The tool already knew
   this for shallow clones (`UNPROVABLE — shallow clone`); it did not know it for squashes, and that
   asymmetry is what red-ed main for a day and blocked 14 PRs.
3. **A permanent red is not a conservative default.** It looks safe and is the opposite: it forces an
   override, and the override is what actually disables the guard. The conservative answer to an
   unprovable ordering is a **disclosure printed on every run**, not a red nobody can clear.

⚠️ **KNOWN, UNDEFENDED, DISCLOSED IN THE SOURCE**: a `git mv` still hides pre-rename history (no
`--follow`; adding it made `--diff-filter=A` report the merge-base as the add for ordinary files and
broke 8 tests). Renames are not a defended surface, and that is written in `_added_commits` so the
next lane inherits the fact rather than the illusion.
