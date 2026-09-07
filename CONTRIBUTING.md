# Contributing to Tortoise

Thanks for wanting to contribute. Tortoise is the epistemic graph engine from
Premise Labs — a small team. Before you open a PR, please read this page and
the license note below.

## What this project is

Tortoise is licensed under the **Business Source License 1.1** (BSL) — see
[LICENSE](LICENSE). BSL is **not** an OSI open-source license: you may read,
use (within the Additional Use Grant), and modify the code, but the license
terms differ from MIT/Apache/BSD. Four years after publication the project
converts to **MPL 2.0**.

This matters for contributions: because the project is BSL today (and MPL 2.0
later), we need a clear license grant from every outside contributor. See the
[contribution license note](#contribution-license-note) below.

## Ways to contribute

- **Bugs & feedback** — file a bug report via the [bug template](https://github.com/daniel-ospina/tortoise/issues/new?template=bug_report.yml), or ask/chat in [Discussions](https://github.com/daniel-ospina/tortoise/discussions).
- **Questions** — Discussions, not issues.
- **Code** — see below. Please open an issue or a Discussion first for anything
  non-trivial: this project is small and deliberately scoped, and we route
  inbound through a triage queue — a PR that arrives without a tracked issue
  may be closed and queued rather than reviewed in place.

## Opening a pull request

1. **Start from a tracked issue.** If your change isn't already an issue,
   open one first and reference it in the PR.
2. **Fork + branch.** Work on a descriptive branch; keep the change small and
   focused.
3. **Add the contribution license note.** Every PR must include the donation
   statement below (the PR template has a checkbox for it). PRs without it
   cannot be accepted.
4. **Tests.** Tortoise runs `uv run pytest tests/ -v` (Python 3.12+; Docker
   FalkorDB lane by default — see [AGENTS.md](AGENTS.md) for the embedded
   carve-out). Add/adjust tests for your change and make sure the suite passes
   locally.
5. **CI must be green.** The project's checks (lint, tests, drift gates) run
   on every PR.

## Contribution license note

This project follows the pattern used by MariaDB (the BSL originators) for
receiving outside contributions into a BSL-licensed codebase.

By submitting a pull request or patch, you agree that your contribution is
donated under one of:

1. the **BSD 3-Clause License** (the "New BSD" license), with the following
   statement in the PR description:

   > I donate this contribution to the Tortoise project under the BSD
   > 3-Clause License.

   (BSD-3 is compatible with both the current BSL terms and the future MPL 2.0
   conversion, so the project can continue to use your code after the Change
   Date), or

2. dedicated to the **public domain** (CC0 / "no rights reserved"), with the
   same statement style, or

3. a **Contributor Agreement** with Premise Labs (for regular/large
   contributors — ask first).

## Review & merge policy

- Outside contributions are **untrusted code**: every PR goes through normal
  review + CI and is merged only by a maintainer. Nothing is auto-merged.
- Be patient — this is a small team and inbound flows through a triage queue.
- If your PR arrives without a linked issue, expect it to be closed with a
  pointer and queued rather than reviewed immediately.

## Code of conduct

Be constructive and respectful. This is a research-grade engine — questions
about design decisions are welcome in Discussions.
