# Withdrawn evidence logs

`s3-ablation-a1075dfc.log` — withdrawn 2026-11-01 by the ruling on #10463 (comment 6564634454): the
squash merge #10468 landed `PRE-REGISTRATION.md` and this log in ONE commit, so the ordering guard
(which required the registration's add-commit to strictly precede the log's) could not be shown
from the merged history. **Contents are untouched** (sha256
`bba52c90d005ae728dc7c3cc7d25c232e322d0abe5bffc6873a6e74fa5960b4d`); it is moved out of the
checked directory, not deleted, and no number in it is retracted — the measurement's ordering
*provenance* is, and #10463 records it as PROVISIONAL until re-measured with the registration
already on the base branch.

The directory is not scanned: the guard globs `*.log` non-recursively in the registration's own
directory, so nothing under `withdrawn/` is read.

## Why this is the shape, not a delete

The rule is `tools/capstone_preregistration_check.py` check 2. Its defect was that it compared two
sibling commits **inside one contribution**, which a squash merge collapses — so the same tree
passed its PR checks and failed on `main` minutes later. That defect is **not fixed by this
withdrawal**: the guard repair ruled on #10463 (part 4 — the ordering claim becomes base ancestry,
not PR-internal order) is a SEPARATE change, and it must not be read as landed. Withdrawing this
log is the deliberate act that restores `main` without touching a gate under pressure.
