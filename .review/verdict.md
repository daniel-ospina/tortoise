## Fresh-context code review — verdict: `NO ISSUES FOUND`

Independent reviewer, fresh context (no memory of authoring cycles), reviewing `git diff origin/main...HEAD` at head `01c62ed12af044bce796246b78e31f9eb7235502`. Files: `tortoise/derived_invalidation.py`, `tests/test_11926_derived_invalidation.py`.

### Departure 1 — the issue's identity is genuinely false (verified at the cited lines)

`tests/test_11926_derived_invalidation.py:1516` (`counts["retired"] == 1`) and `:1518` (`counts["failed"] == 1`) are asserted on a **single** finding of a single-goal sweep, so `stale == retired + alreadyRetired + failed + gone` reads `1 == 2` there. The counter-example is real, and the replacement rule ("every finding lands in ≥1 bucket") holds on every return path of `invalidate` — `:830` (absent → `alreadyRetired`), `:843` (already terminal), `:864` (post-exception terminal), `:877-879` (success → `invalidated`) — plus every raise, which the sweep converts to a record carrying `error` (`:1096-1104`) counted by `failed` at `:1133`.

### `found` totality

Present on all four `invalidate` return paths (`:830`, `:843`, `:864`, `:879`) and on the synthesized refusal record (`:1102`), where `.get("found")` previously read `None`/`KeyError`d. The two raise paths never return a dict, so nothing is missing there. `:1064-1072` says honestly that `None` sometimes discards a knowable `True` rather than claiming it is unknowable. `status` is correctly documented as *not* a discriminator (`:1037-1042`), and the code agrees: `:837` reads `claim.get("status")`.

### Counts predicates

`alreadyRetired` (`:1119`) is `r.get("alreadyRetired")`, so the absent arm lands there and the refusal record sets `False` (`:1101`); the overlap is documented (`:1016-1029`) and `gone` is `(r.get("cache") or {}).get("gone") is True` (`:1125`), which cannot fire without a reconcile report.

### Mutation sensitivity of the new tests

Reverting `alreadyRetired: False` reds `:1180` and `:1138`. Reverting `_reconcile_or_report` on the absent arm reds `gone == 1` (`:1266`) and `goalState is None` (`:1206`). On the pre-read terminal arm it reds `report["cache"]` (`:1236-1237`); on the post-exception arm `cache["checked"]` (`:1301`). Removing the `try/except` lets the exception escape, so `:1340` reds while `failed` stays 1. Wrong-`goal_id` mutants red via `goalState` still `met` / `gone` becoming 1.

### Departure 2 — in scope, not creep

The absent arm *needs* a `cache` report for the #12315 fix to be reconcilable at all (`counts.gone`/`failed` read it), and `alreadyRetired: True` with no reconcile would assert a clean no-op over a goal whose displayed word nothing backs. Extending the same reconcile to the two already-terminal arms is consistency, gated by the existing word rule in `reconcile_goal_state_cache:957-961`, and uses `latest_judgement`, which is live-only (`goal_judgement.py:988-999`), so it cannot repoint the cache at the claim being retired. Each of the three arms carries its own failing-if-reverted test.

No in-repo consumer branches on the changed shape: `alreadyRetired`/`found` appear only in `tortoise/derived_invalidation.py` and its single test file (`git grep` over tracked files, including `docs/` and `graph-scripts/`), and the module is imported nowhere else in production code.

### Local suite (this head)

`uv run python tools/test_lane.py run -- tests/test_11926_derived_invalidation.py -q` → **81 passed in 31.98s**.

### Non-blocking note (not a defect)

The bucket-overlap paragraph (`:1016-1021`) names the `retired`+`failed` and `retired`+`gone` pairs explicitly; the newly reachable `alreadyRetired`+`failed` pair is covered by the general "per-finding predicates, not a partition" sentence and pinned by a test. Nothing false is claimed.
