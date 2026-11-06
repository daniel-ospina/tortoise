---
title: Working an issue — the process that travels with the issue
type: operations
domain: platform
doc_status: live
created: 2026-11-05
ownedBy: organisation-design-team
aboutSubjects: issue-process
products: [tortoise]
aboutObjects: [github-issues, issue-scoping, issue-creation]
---

# Working an issue — the process that travels with the issue

## The problem this closes

The process for working an issue lived in **dispatch briefs**. A brief is written by whoever
dispatches, is not attached to the issue, and is not read by the next lane. So an issue picked up by
a lane whose dispatch forgot a step was worked without that step — and the issue could not tell,
because it carried no instructions of its own.

The second half is the sharper one. **An issue states a PROBLEM. Its completion criteria do not
exist at creation** — they are produced by scoping, which is work that happens after the issue is
filed (owner ruling, 2026-11-05):

> *"at issue creation the completion criteria might not be there. An issue is a problem, the
> completion criteria are coming from the scoping which is work that happens after."*

So the design is **two-stage**, and it must not demand criteria at creation:

| Stage | Produces | Where it lives |
|---|---|---|
| **CREATION** | the problem, plus the process instructions so any agent taking it over follows the right route even if its dispatcher forgot | the issue **body** (template-rendered) |
| **SCOPING** (later, whoever picks it up) | the `## Satisfies` checklist — one item per acceptance criterion, each with a `file:line` or a named test | a **COMMENT** on the issue |

⛔ **A fresh issue with no `## Satisfies` is NORMAL, not a defect. An issue with WIP and no
`## Satisfies` is the defect** — that is work started before the completion criteria existed, which
is how work landed on **#4282** with no finish line. (The figure the originating brief carried was
**21 merged PRs**; a `gh search prs --merged "#4282 in:body"` re-measure on 2026-11-05 returns
**69** PRs that *mention* it. The two count different things — merged-without-closing versus
any-PR-mentioning — and this doc states neither as a fresh measurement: read the issue.)

⛔ **Never put the checklist in an owner-authored body.** A body may be a record (issue `#4282` has
70 comments of record; its body is not ours to rewrite). The checklist is a comment.

## The block — paste this into any issue

Short enough to paste. This is the authoritative copy; if a dispatch brief omits a step, this wins.

```markdown
## Working this issue — process (authoritative; if a dispatch omitted it, this wins)
This issue states a PROBLEM. Its completion criteria do not exist yet — SCOPING creates them (§4).
1. PRE-FLIGHT all machines: `uv run python tools/collision_preflight.py <N> --repo .` exits 0, then
   `uv run python tools/who_is_on.py <N> --repo .` (branches + worktrees + claims). Claim before starting.
2. WIP: read the newest `<!-- p01-status -->` comment. Merged work present ⇒ NOT a fresh start; take only unproven criteria.
3. RESEARCH (from 0 only): skills/research/SKILL.md in full — preflight + domain classification + three sources incl. external.
4. SCOPING ⇒ post a `## Satisfies` COMMENT: one `- [ ]` per acceptance criterion, each with a file:line or named test.
   ⛔ Do not implement before this comment exists. Name any parallel issue covering part of it; mark a deliberate
   `⚠️ BAND-AID: <why + where the real fix lives>`.
5. PLAN ⇒ skills/writing-plans/SKILL.md, one step per criterion.   6. IMPLEMENT red-first per criterion (red, green, revert-fails).
7. PR body: `Satisfies #<N>: <ids>` for partial work · `Closes #<N>` ONLY on the last unchecked criterion · no-issue PRs write neither.
8. REVIEW skills/code-review/SKILL.md (fresh context), findings fixed before the record; six ENTRY checks; rail from a linked worktree.
9. CLOSE: tick proved criteria in the `## Satisfies` comment; close only when all ticked; read the closure back.
### Claim: append this line to the issue's `<!-- p01-status -->` comment
IN FLIGHT: <ids> — branch <b> — box <c> — since <UTC>
```

## Why each step is where it is

- **§1 pre-flight, both verbs.** `collision_preflight.py` is the *gate* (exit 1 collision, 4 landed);
  `who_is_on.py` is the *answer* (adds local-only branches and worktrees, which a remote-only query
  cannot see). Running only one of them is how a lane duplicates live work (#3061, #4256).
- **§2 WIP first.** Merged work under an issue means it is **not** a fresh start. Reading the newest
  `<!-- p01-status -->` comment is what tells you which criteria are already proven. ⚠️ **That marker
  is the block's own convention and no tool emits it yet** — the fleet's durable *holder* registry is
  `refs/claims/*`, read by `tools/who_is_on.py` (§1). So treat a missing `p01-status` comment as "no
  note was left", never as "nobody is on this": the claim surface is `who_is_on.py`.
  For the same reason `tools/issue_process_lint.py`'s WIP arm reads only the textual `IN FLIGHT:`
  marker and, **in the modes that actually read markers, prints how many subjects carried none** —
  because `0 markers read` and `0 issues with WIP` are different claims. ⚠️ With `--wip` /
  `--no-wip` the verdict is INJECTED from the branches/worktrees/ref-claims population, no marker is
  read, and the run says exactly that instead of printing a count: a `0 of 0` line there would claim
  a reading that never happened. ⚠️ **Spell the marker as the block does: PLAIN TEXT, on its own line.**
  A marker inside an inline code span is blanked before the scan (the quote-is-not-a-claim rule, the
  same one that makes `` `gh pr view #1234` `` a quotation), and a marker behind prose
  (`### Claim: IN FLIGHT: …`) is not anchored at line start — either spelling produces **no marker
  at all**, silently, and the arm then has nothing to say. Both are unit-pinned against this block,
  so changing the block's spelling without changing the lint reds the suite.
- **§3 research from zero only.** A lane continuing existing work inherits the research; a lane
  starting from scratch owes the three sources, external included.
- **§4 the checklist is SCOPING's output, and it is a comment.** `skills/issue-scoping/SKILL.md`
  already produces the plan as a comment; this is the *finish line* half of the same artifact —
  an acceptance criterion you cannot point at a file:line or a test for is not a criterion.
- **§7 the closing keyword is not decoration.** `Satisfies` links and proves nothing; `Closes` links
  **and** closes. Measured (#10534): of 18 PRs naming a P0/P1, **18 used `Refs`** and **5** of 29 used
  a closing keyword — so a night of merges moved no P0/P1 count. `Satisfies #N: <ids>` exists so a
  *partial* PR can name which criteria it proves without claiming the whole issue.
  ⚠️ **This is a deliberate departure, and it is not free:** `skills/code-review/SKILL.md` tells an
  author to write `Related: #N` for a non-closing reference, and `record-review.sh` resolves a PR's
  linked issue **only** through closing keywords — so a PR written exactly as this block mandates
  (`Satisfies #N:`, no `Closes`) reads to the recorder as having no closing ref, and its tier
  attestation degrades to *UNVERIFIED*. Teaching those two surfaces to read `Satisfies #N:` as a
  **linking (non-closing)** ref is the next step, not something this doc can do by asserting it.
- **§8 review at the head that lands**, findings fixed before the record (a body edit after a record
  wipes the attestation).
- **§9 close only when every criterion is ticked**, and read the closure back — a `Closes` on a
  non-default base is inert (`tools/closes_landing.py`, #10597).

## The instruments

| Question | Instrument | Verdict |
|---|---|---|
| Does a PR claim an issue that nothing resolves? | `tools/issue_process_lint.py` | **advisory: rc 0 clean / 3 findings / 2 unmeasured** |
| Does an issue with WIP have a `## Satisfies` checklist? | `tools/issue_process_lint.py` | **advisory: same codes** |
| Has the surface actually moved toward its target? | `tools/surface-target-check.py` | refusal-capable (exit 1) |

**“Advisory” is a property of the WIRING, not of the exit code.** The tool exits **3** when it finds
something — that is how a caller knows. It is advisory because **nothing mounts it**, and because any
step that ever does owes `continue-on-error: true`; `tests/test_issue_process_lint.py` parses the
workflow YAML per step to enforce that rather than trusting the string to appear somewhere in the file.

```bash
uv run python tools/issue_process_lint.py --pr 11027            # one PR
uv run python tools/issue_process_lint.py --issue 4282          # one issue
uv run python tools/issue_process_lint.py --sweep               # the newest 50 open PRs (a CAP)
uv run python tools/issue_process_lint.py --sweep-issues --label P0-blocker   # open issues, filtered
uv run python tools/issue_process_lint.py --body-file /tmp/body.md            # a PR body, offline
uv run python tools/issue_process_lint.py --issue-body-file /tmp/i.md --wip   # an issue, offline
```

⚠️ `--label` filters the **issue** population (`--sweep-issues`); `--sweep` reads PRs and has no
label filter, so the combination is refused rather than silently measuring an unfiltered
population. `--body-file` and `--issue-body-file` are mutually exclusive for the same reason: a PR
body and an issue body are different populations, and one run answers one of them. Two further pairs
are **refused, never resolved in silence**, because the flag would otherwise be accepted and dropped:
`--limit` is read by the two sweeps and by nothing else (the other modes read a fixed subject), and
`--json` + `--annotate` have no union — the JSON mode would run and emit no `::warning`/`::notice`
line at all.

⚠️ **`--sweep` / `--sweep-issues` are a CAP, not the population.** Each reads ONE `gh … list
--limit N` call (default 50), and `gh` lists newest-first — so the subjects skipped are the
**oldest**, which is exactly where "WIP started before the completion criteria existed" (the arm-2
defect) concentrates. A run that stops at the cap therefore prints a note naming it rather than
reporting `50 open issue(s)` as if that were the whole population. Raise `--limit` to widen it.

⛔ **Never make this lint a required check.** It is a warning surface (the `closes-coherence`
precedent: `continue-on-error: true`, never in `python-ci-gate.needs`, never in `.mergify.yml`). Two
populations are **deliberately not flagged**, because a false positive here would be bureaucracy
pointed at legitimate work: a **fresh issue with no criteria** (the scoping has not run yet), and a
**PR that names no issue** (not every change serves an issue).

## What this doc is not

It is not a second `issue-scoping` skill — and it does not yet own the checklist format either. Be
precise about coverage, because a process doc that overstates where it reaches is worse than none:

| Surface | Covered today |
|---|---|
| an issue created from **`.github/ISSUE_TEMPLATE/agent_finding.yml`** (the internal form) | ✅ the block is pre-filled and submitted |
| an issue created with **`gh issue create`** (how `skills/issue-creation/SKILL.md` files them) | ❌ **not yet** — the body must paste the block by hand |
| the **external beta** form (`bug_report.yml`) | ❌ **deliberately not** — it pre-fills repo-internal commands (`uv run python tools/collision_preflight.py`, `skills/research/SKILL.md`, `<!-- p01-status -->`) that an external reporter can neither run nor read. Its job is to tell the reporter what to include |
| **`skills/issue-creation/SKILL.md`** (render the block into every issue it creates; state that §Section 1's Targets are problem-level while the enforceable criteria are the scoping output) | ❌ **not yet** — in this repo `skills/` is a tracked symlink into the **agent-infra** checkout, so that file is agent-infra's and cannot be edited from a tortoise worktree. It is the next step |
| **`skills/issue-scoping/SKILL.md`** (which already produces the plan comment the checklist lives beside) | ❌ unchanged — `grep -rn Satisfies skills/` finds nothing, so the `## Satisfies` heading's only homes today are this doc and the template value. The two are pinned byte-equal by a test so they cannot drift, but the *skill* does not yet name it |

So the honest summary: **this doc introduces the block and the checklist; it does not yet own them in
the skills that produce issues, and it says so here rather than implying otherwise.**
