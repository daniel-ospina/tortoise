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
to one key and a swap would pass. **Both of lychee's failure maps are read.**
lychee reports a hard failure in `error_map` and a link it could not reach in
time in `timeout_map`, and its own verdict (`ResponseStats::is_success`, lychee
0.24.2) is `error_map.is_empty() && timeout_map.is_empty()` — the
`--accept-timeouts` opt-out exists precisely because a timeout **is** a failure
by default. Because the `docs` job runs the link check with the action's
`fail: false` (so the differ decides), a map the differ does not read is a class
it cannot fail on, and one `update` cannot record either: keying on `error_map`
alone let a new dead link that **timed out** instead of erroring pass the
required check. Both maps are now parsed, on both the `check` and the `update`
side, and `timeout_map` is a required key of the report — so the differ sees
exactly the findings that make lychee exit non-zero. The committed snapshot as
generated records no timeout entries, so a timed-out link in a changed file
fails the check until a re-baseline records it (the fail-closed direction).
The two halves are compared differently: a
markdownlint finding is counted as a **multiset**, because how many times a rule
fires in a file is a property of that file, so several findings can share a key
and adding a third still fails; a lychee finding is compared as a **set** of
`path|link_target` keys, because how many times a link appears in the run is a
property of the run's population, not of the tree — a link that appears twice in
one file must never read as a new finding (#7475).

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
  runs) — so slack above the observed range cannot hide an append. The same test
  pins a **content digest** of each list, because a ceiling bounds only the
  count: a PR could otherwise delete one legitimate entry and append the finding
  it introduced, keeping the count constant. Append, delete and swap therefore
  all require a ceiling *and* a digest raised in the same change, out loud.
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

## The linter policy is part of the snapshot

A rule turned off in **any** `.markdownlint*` config, or a path ignored in
`.lycheeignore`/`lychee.toml`, makes the linter report **fewer** findings — which
the differ reads as "nothing new", passing the required check with new debt.
`markdownlint-cli2` reads that config family from *any* directory on the path to a
linted file, and a more specific config **overrides** the repo one, so pinning
only the root file would leave a same-PR `docs/.markdownlint-cli2.jsonc` free to
turn a rule off. The snapshot therefore records a content digest of **every
tracked file with one of those basenames, at any depth**, keyed by repo path — so
*adding* a config is a policy change too. lychee does not take its policy from
`lychee.toml` alone: it also auto-loads `[tool.lychee]` from `pyproject.toml`,
`"lychee"` from `package.json` and `[package.metadata.lychee]` from `Cargo.toml`,
so those sections are digested too — only the section, never the whole file,
because a dependency bump is not a policy change, while a *missing* section is a
fixed marker, so adding one still fails closed. The whole map is **required**:
`check` fails closed if the field is absent, because a snapshot that simply
omitted it would disable this check. A policy change therefore cannot be made
without moving this snapshot, and the map's digest is pinned by
`tests/test_docs_lint_baseline.py`, so the edit is always visible in the diff and
must be made out loud, with the reason. A config that does not **hold** its own
policy is refused rather than followed: `extends` and `customRules` name a second
file, and a `.cjs`/`.mjs` config executes, so a rule switched off in what they
load would move this snapshot not at all. Separately, both `docs` paths reject a
changed `.md` that **adds** a `markdownlint-disable` directive — a suppressed
finding is not a fixed one. A pre-existing directive is part of the baselined
debt and is unaffected; that guard greps the added-markdown diff read from a
**file**, never a pipe, because `grep -q` exits at its first match and the
resulting SIGPIPE under `set -o pipefail` made the pipeline non-zero — silently
skipping the guard on a large diff. It matches **case-insensitively**, because
cli2's own directive parser does (an uppercase `MARKDOWNLINT-DISABLE` comment
suppresses a finding just as the lowercase form does), and it runs with rename
detection on, so a pure `git mv` of a file that already carries a directive is
not mistaken for an added one. It is a **heuristic over added lines**, and it has
exactly one false positive, which is deliberate and fails loud: a directive
written inside a code FENCE is not honoured by cli2 — the finding it names still
reports — but the added line still matches the pattern (measured: a directive
inside a fence left the named finding in place, while the same directive in a
backtick span removed it). The remedy is to reword the example; the check is a
required gate, so failing a demonstration that hides nothing is the safe
direction.

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
