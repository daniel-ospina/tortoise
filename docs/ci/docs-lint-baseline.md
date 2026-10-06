---
title: "The docs lint baseline — the snapshot the `docs` check diffs against"
type: engineering
domain: capability
doc_status: live
created: 2026-10-06
subjects.team: epistemic-team
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
issue: 7435
---

# The docs lint baseline

> The REQUIRED `docs` check lints every changed `.md` file **whole**. When this
> snapshot was taken, 11,238 markdownlint findings sat in 650 of the 838 tracked
> markdown files, plus the link findings — read those from the snapshot's own
> `counts`, because that half checks REMOTE links and varies between
> generations. Because a file is read whole,
> every one of those files was a trap: the next change to touch one was failed
> for findings it did not write.

## What it is

`config/docs-lint-baseline.json` records the findings that already existed on
main when it was taken. The `docs` job now **fails only on findings that are not
in it**. That does not weaken the check — a genuinely new finding still fails it;
it stops a change being charged for debt already on main.

It records debt. **It repairs none of it.**

A finding is keyed on its `path` and the rule or target, **never on a line
number** — a line-number key would invalidate the whole snapshot the moment any
file grew above the offending line. The lychee key is `path|link_target` with
**no status text**: lychee reports a URL it has already seen in a run as
`Error (cached)`, so a status would describe the run's file population rather
than the link — the same untouched link keys differently when the run covers the
whole repo (what `update` does) and when it covers a PR's changed files (what CI
does), which would fail unrelated PRs on inherited findings. The one exception is
lychee's `error:` placeholder (it could not extract a URL at all): there the
target has no distinguishing content, so the offending line's own text is used
as the identity — otherwise two different broken links in one file would collapse
to one key and a swap would pass. Findings are counted as a **multiset**, not a
set: several findings can share a key, and adding a third still fails.

Two measured instances motivated it:

- **#5434** — its own new plan document carried 9 markdownlint findings
  (MD001, MD007, MD032). They reproduce locally in about 24 seconds, but were
  discovered only after a full CI cycle.
- **#7475** — a change that touched `docs/product/mcp-sdk-surface.md` failed the
  link step on a link that had been broken **on main for as long as the file
  existed**. The file is generated, so the fix belonged in its generator, not in
  the rendered markdown — which the failure message did not say.

## The end state — a ceiling, never a floor

This snapshot is **not an amnesty**, and it is not a fix. It is bounded, and the
bound is enforced: `tests/test_docs_lint_baseline.py` pins a **ceiling** on both
counts, so a PR cannot append the findings it introduces and bump
`snapshot.counts` to make its own change pass. The snapshot may shrink freely;
growing it requires raising that ceiling in the same change, out loud.

- Every finding removed from the codebase must be removed from the snapshot.
- The entry count must never grow. A genuinely new entry is a new failure, not a
  snapshot edit. A pinned ceiling in `tests/test_docs_lint_baseline.py` enforces
  it: **exact** for markdownlint (deterministic), and the **maximum observed
  across generations** for the lychee half (its remote-link count drifts between
  runs) — so slack above the observed range cannot hide an append.
- **#7534** owns the burn-down. Note its **population**: this snapshot covers ALL
tracked markdown — 11,238 findings in 650 of the 838 tracked files. The `docs/`
subtree holds 8,980 of those in 514 files; the remaining 2,258 in 136 files sit
outside it, and 1,422 of those sit in 41 tracked
`website/apps/dashboard/node_modules` files that a vendored re-install rewrites,
so they cannot be fixed by hand-editing the `.md`. #7534 was scoped to the
`docs/` subtree, so it does not yet own the remainder; that gap is recorded on
issue #7534. When the snapshot is empty, the file and
`tools/docs_lint_baseline.py` are deleted, and the check goes back to deciding on
its own.

## A changed path with an unsupported character fails the job

`markdownlint-cli2` (and lychee's `--files-from`) treats every positional
argument as a **globby pattern**, so a changed path containing a glob
metacharacter is expanded to a different, pre-existing file. The linter then
truthfully reports `Linting: 1 file` for a file it never read, the differ's
file-count check is satisfied, and a new finding in the changed file passes
silently. This is **extglob too** — `./docs/@(README).md` lints
`./docs/README.md`, and it contains none of `* ? [ ] { }`, so the guard is an
**allowlist** (`A-Za-z0-9._@/-`, which every one of the 838 tracked `*.md` paths
matches) rather than an enumerable denylist. No cli2 flag disables the expansion
(`--no-globs` only ignores the config's `globs`), so both `docs` paths **fail
closed** on any other character rather than lint the wrong file. No tracked
markdown file is affected today.

## Regenerating it

The snapshot is generated by the check's own pinned linters, never written by
hand:

```bash
uv run python tools/docs_lint_baseline.py update
```

(`uv run python`, not a bare `python3`: the tool refuses an interpreter below
3.12 — it imports `datetime.UTC` — and a bare `python3` is 3.9 on hosts that
have not installed one.)

That runs `markdownlint-cli2@0.23.3` and `lychee 0.24.2` over every tracked
markdown file and rewrites the snapshot. Regenerate it in its own change, and say
why the count changed.

## Findings in generated files

When a finding is inside a file rendered by a generator, the fix target is the
**generator** — editing the rendered `.md` reverts on the next render and turns a
lint failure into a drift failure. The differ names the generator in that case.
