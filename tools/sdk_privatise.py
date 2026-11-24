#!/usr/bin/env python3
"""The #12901 final-batch privatisation sweep — one map, one rewrite, one verifier.

WHY THIS IS A TOOL AND NOT A PATCH
----------------------------------
The transformation is mechanical and it is LARGE: every off-surface method leaves the public
`TortoiseSDK` surface, and a receiver-typed walk of this repo finds thousands of references.
A hand-edited patch of that size cannot be reviewed and cannot be re-verified; the one thing a
reviewer needs — *"how many references are left, and which ones did the sweep refuse to
guess at?"* — would have to be re-derived by whoever reads it next. So the map is derived here
(never typed), the rewrite is performed here, and `--check` answers the review question from
the same walk that `--apply` used. A second implementation would be a second answer.

MECHANISM: PRIVATISATION, NEVER DELETION
----------------------------------------
An off-surface method becomes `_name`; the body stays, so the engine keeps every capability.
Two prior attempts died on the same hazard, which `--plan` refuses in advance: **`_name` may
already exist on the class**, and a naive rename then silently SHADOWS it (Python keeps the
later `def`) — `delete_entity` became infinite self-recursion and `audit` swapped bodies with
the audit-event logger. Such a name takes `_name__svc` instead, and its call sites are
re-pointed to that spelling.

SWEEPING BY RECEIVER EXPRESSION, NOT BY PARENTHESIS
---------------------------------------------------
`x.method(` is only one of the shapes a reference takes. The walk below matches the
`Attribute` NODE, so it also reaches `asyncio.to_thread(sdk.create_object, …)`,
`_safe(sdk.method, …)`, `self._search_fn = self.tortoise_fts_query`,
`TortoiseSDK.create_direct_edge` and `sdk["x"].method` — all of which a parenthesis-shaped
search misses. String-shaped references (`getattr(sdk, "query")`, `monkeypatch.setattr(sdk,
"query", …)`, `setattr(cls, "tortoise.sdk.TortoiseSDK.query", …)`) are swept in a second arm.

WHICH RECEIVERS ARE SWEPT — THE HARD PART
-----------------------------------------
47 of the 66 names are ordinary words that OTHER objects also answer to: `query` (a FalkorDB
handle), `update` (a dict), `delete` (an HTTP client), `create_point` (a projection). A
name-scoped rewrite is therefore not a sweep, it is a sabotage: on this tree the 66 names carry
22,651 `Attribute` references and roughly half of them belong to those other objects.

So a receiver is rewritten only when THIS FILE contains evidence that it carries the SDK
surface:
  * `self` inside the `TortoiseSDK` class body (sdk.py), or inside a subclass of it;
  * an identifier whose own name says SDK — `sdk`, `sdk2`, `org_sdk`, `_org_sdk`;
  * a name bound from an SDK constructor or fixture (`X = TortoiseSDK(...)`, `X = _get_sdk()`,
    a `@pytest.fixture` whose returned/yielded value is one), or annotated `TortoiseSDK`;
  * an attribute that says SDK (`self.sdk`, `obj._sdk`, `mod.TortoiseSDK`);
  * a class that STANDS IN for the SDK — it implements at least two of the APPROVED surface
    (a real stand-in does; a FalkorDB/registry/mock-HTTP double implements none), or it
    subclasses `TortoiseSDK`. Its `def` names are renamed with it, because production code
    reaches it through the private spelling.

Everything else is RESIDUE: reported with its receiver expression, never guessed at. The
residue is the sweep's visible edge, and `--check` fails while a typed reference survives so
that "did we finish" is answered by the same walk that did the work.

USAGE
    uv run python tools/sdk_privatise.py --report   # classification, collisions, stand-ins, residue
    uv run python tools/sdk_privatise.py --plan     # the rename map + collision audit
    uv run python tools/sdk_privatise.py --apply    # rewrite
    uv run python tools/sdk_privatise.py --check    # exit 1 while any TYPED ref survives
"""
from __future__ import annotations

import sys

# #5128 (the repo's entry-point contract): refuse a <3.12 interpreter HERE, before the
# rest of the imports, so a stale local `python3` fails LOUDLY with a remedy instead of
# with whichever 3.12-only construct happens to trip first. This tool was ADDED by #12901
# and its floor check sat BELOW the import block, with a message that named no remedy —
# so the #5128 arms read it as `_guard_index(...) -> None` plus a missing invocation, and
# `tests/test_entry_point_python_guard.py` redded both. Moved above the imports, and the
# remedy is now named. (⛔ It was never ABSENT: an earlier cut of this comment said
# "carried no guard", which contradicted the measured state — a guard in the wrong place
# is not a missing guard, and the distinction is the one the #5128 position arm exists for
# — #13899 re-cut review, P2.)
if sys.version_info < (3, 12):  # noqa: UP036 -- intentional RUNTIME guard
    print(
        f"tools/sdk_privatise.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/sdk_privatise.py`",
        file=sys.stderr,
    )
    raise SystemExit(70)

import argparse
import ast
import hashlib
import re
import subprocess
import warnings
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SDK_SRC = ROOT / "tortoise" / "sdk.py"
COLLISION_SUFFIX = "__svc"

#: The commit the sweep was APPLIED against (#13899 F3). `--check` derives the rename map from
#: THIS revision and not from a moving `merge-base`: once this branch merges, `merge-base HEAD
#: origin/main` resolves to a commit whose class already holds the 39 approved names, so the map
#: would be empty and `--check` would answer "0 references" over an unswept tree — a vacuous
#: PASS, which is the one thing a verifier must never do. Pinned, so the question stays the same
#: question forever; a future sweep re-pins it deliberately.
SWEEP_BASE = "28ca66878d7c617a2f28a8025ddaa40352adfcb2"

#: Non-vacuity of the map: `SWEEP_BASE` holds this many off-surface methods, and a check that
#: derived a different number is answering about a different tree. A count that silently went to
#: zero is the vacuous pass again, one level up.
SWEEP_NAMES = 66

#: The residue the sweep REFUSED to guess at, pinned as `(rows, digest)` over the sorted rows so
#: that a NEW untyped shape — the ordinary-word receivers (`x.query`, `x.update`, `x.delete`) that
#: cannot be typed from evidence — reds `--check` instead of being silently absorbed into a fresh
#: count each review (#13899 F6). Re-pin deliberately with `--repin`, in a commit that says why.
#:
#: RE-PINNED (11776, bfbf6195136edc2c) → (11782, 0f00153d4691e3de) → (11769, 3dc892001700a031)
#: by #13899's fix rounds, and both moves are the intended direction:
#:   * +6 — the 6 `reg.query(` sites in `test_delivery_tenancy.py`,
#:     `test_issue_4010_sessions_unlimited.py` and `test_mcp_http.py` were restored from
#:     the vendor PRIVATE `reg._query(` to the public `query` (P2 — `reg` is a FalkorDB
#:     `Graph`, never the SDK), so the `reg.query` row went 403 → 409;
#:   * −13 net — the SDK test doubles were renamed to the private seams the doors now
#:     call (`_QuotaSDK().query` → `._query`, `super()._tortoise_fts_query`,
#:     `ctx["sdk"]._tortoise_fts_query`, `sdk._query`/`_create_point` in
#:     `integrations/cli-extraction/extract_turns.py`, …), and every one of those was an
#:     untyped-receiver row of this residue. The pinned row set holds exactly the ordinary
#:     words that remain, `reg.query` still at 409.
#: A re-pin here is a DECLARATION of a known shape, not a new allowance: adding an
#: SDK-holding receiver to the residue still requires the same deliberate re-pin with its
#: own reason.
#:
#: RE-PINNED (11769, 3dc892001700a031) → (11758, d2311d5af77fe5d5) by the same round, and again the
#: intended direction: eleven side effects in `tests/test_tortoise_client.py` sat on the RETIRED
#: PUBLIC spellings (`mock_sdk.return_value.query` / `.create_point` / `.summarize_structure`) while
#: the client calls `_query` / `_create_point` / `_summarize_structure`, so eleven untyped-receiver
#: rows that pinned nothing are gone. The ordinary-word rows are unchanged (`reg.query` 409,
#: `graph.query` 267, `cp.query` 263, `sdk._get_registry().query` 263, `self.g.query` 202) and typed
#: references to a public name are still 0. An SDK-holding receiver is still residue: adding one
#: still requires this same deliberate step, with its own reason.
#: RE-PINNED → (11757, 7a53ce4f33a41246) by the commit that re-keyed
#: `tools/window_quality_m2.py`'s `cur["sdk"].tortoise_fts_query(` to the private
#: `._tortoise_fts_query(`: that receiver is a SUBSCRIPT, so the sweep could not type it and had
#: REPORTED it as residue — the re-key removes that one row. Measured with `--repin` on this tree,
#: not arithmetic.
#: RE-PINNED AGAIN → (11758, 3b0765cfdbe87b0d) by the RE-CUT that merged `origin/main`
#: `290912f303` INTO `feat/12901-sdk-final-c` (#13899). The residue SET moved by rows that are
#: the sweep's own documented edge rather than a defect: `declared_markers.update` — a plain
#: `set` `.update(…)` in `graph-scripts/business_map_13880/file_map.py:752`, a file `main` gained
#: with `#13880` (`update` is one of the 47 ordinary words other objects answer to, so a
#: name-scoped rewrite would have been sabotage). Diffed row-by-row between the pre-cut head and
#: the merged tree; every key that moved is an ordinary-word receiver on a plain dict/set.
#: RE-PINNED AGAIN → (11760, 7eac1e084fe07dfc) by the same re-cut taking the two commits `main`
#: landed WHILE it ran (`bd020ff45b`, `#13886` + `#13853`): +2 rows, `state.update` 9→10
#: (a plain dict, `tests/test_13036_capstone_v2.py:637`) and `tc.headers.update` 27→28 (a
#: `requests`/`httpx` header map, same file:591), plus 17 typed references in that one new file
#: that `--apply` rewrote. Again row-diffed against the pre-cut head, not arithmetic.
#: Measured with `--repin`, not arithmetic.
#: RE-PINNED DOWN → (11752, "910c86ce66c0eac9") by the lane that re-pointed the retired-name
#: CALL SITES (`#13899`'s correction comment: 25 NAME ids, 15 of them in
#: `tests/test_vocab_gating_by_graph.py`). ⛔ The move is DOWNWARD, which is the harmless
#: direction — a residue count that FALLS cannot hide a new untyped reference. Each of the 8
#: removed rows is one name the lane re-pointed to its private spelling, and each was diffed
#: by re-running `edits_for` per file against the pre-fix blob (`git show 3dd8717b66^:<f>`),
#: never by arithmetic: `type(sdk).promote_point` 1, `scale_graph.compute_confidence` 2,
#: `type(sdk).issue_insight` 1, `b.compute_confidence` 1, `clone.set_point_baseline` 1,
#: `clone.dream` 1, `store.delete_point` 1 — seven receiver expressions over six files, and
#: the eighth row is `scale_graph.compute_confidence`'s second occurrence. A receiver such as
#: `type(sdk)` or `store` is exactly what the sweep refuses to guess at, so the row was
#: REPORTED rather than rewritten; fixing the call site is what removes it.
#: RE-PINNED → (11762, "c78653328fa17ea7") by `#13906`'s query-parity harness, on a base that
#: carries `#13899`'s `--check` CALLER — so the gate first ran against this PR's own new files after
#: the forward merge, and it reded exactly there. Ten rows, every one an ordinary-word receiver on
#: an object that is not the SDK, each attributed with `edits_for` on the one file it lives in
#: rather than by arithmetic:
#:   * `self.g.query` 7 and `self.g.delete` 1 — `self.g` is a **`falkordb` Graph**
#:     (`self.client.select_graph(...)`, `tools/vector_parity_13906.py:1511` and `:1538`): the same
#:     shape as the `g.query` / `graph.query` / `proj.g.query` rows already pinned;
#:   * `self.client.list_graphs` 1 — the **`falkordb.FalkorDB` client itself**
#:     (`tools/vector_parity_13906.py:1510`), not an SDK handle;
#:   * `urlsplit(hash_shape).query` 1 — a **`urllib.parse.SplitResult` attribute**, a DSN's query
#:     component (`tests/test_vector_parity_13906.py`), caught by the `X.query` shape only because
#:     that receiver cannot be typed from evidence.
#: None of the ten is a reference to a retired public name — `typed references still pointing at a
#: public name: 0` on this tree. The pin moves UPWARD because the tree gained files: that is the
#: direction which has to be declared deliberately rather than absorbed silently.
#: RE-CUT to the REBASED tree → (11794, "d26b6f638a612643"). The pin is an ABSOLUTE count of the
#: tree, so it moves with the base as well as with this PR: the branch was rebased onto
#: `origin/main` `ef1d6f7561`, which observes 11784 on its OWN tree against the same pin of 11752.
#: ⛔ So the 32 rows between 11752 and that base's 11784 are the BASE's, not declared here, and this
#: block does not enumerate them (they are `#13854`/`#13966`-era merges' business); the ten above
#: are the only rows `#13906`'s harness owned; `#13919`'s own rows are declared below.
#: ⛔ **`--check` on that tree is RED regardless of this pin** — `typed references still pointing at
#: a public name: 4`, all four on the typed SDK receiver `.ingest_corpus` in
#: `tests/test_13826_write_gate.py:544,555,580,596` (a `#13854` file). A re-pin cannot make that
#: check green, and `#13906`'s harness did not FIX it: on that tree the four were base-owned and out
#: of that PR's scope — this branch fixed them at `21ae734ce3`, declared in the `#13919` block below.
#: ⛔ Spelling matters on that row: the four were `sdk.ingest_corpus(…)`, the PUBLIC name. A private
#: receiver (`sdk._ingest_corpus(…)`) is not typed residue at all — the rename map is keyed on public
#: names only (`off_surface_names` skips `_`-prefixed defs) and `edits_for` counts a hit only when
#: the attribute is IN that map — so a reader who chases a red by re-pointing an already-private
#: name will find nothing to fix and start distrusting the gate.
#: Measured here, so the next lane does not have to re-derive it:
#:   `typed references` on the merge parent = 4; on this tree = 4 (this PR adds NONE).
#: ⛔ THE PARAGRAPH ABOVE IS MAINTENANCE HISTORY, NOT A LIVE CLAIM — corrected by `#13919` at the
#: merge of `origin/main` `6fbd96f270` (see the `#13919` block immediately below). The four typed
#: references it records were `main`'s own red and are **0** on this tree, measured with `--check`
#: (exit 0); a reader must not re-derive `main`'s red from this paragraph.
#:
#: ⛔ WHY THIS PIN'S RED IS NEVER EXCUSABLE AS `main`'s OWN: `surface-guard` is NOT in the
#: `DEFERRABLE_LEGS` set (located with `grep -n 'DEFERRABLE_LEGS="' .github/workflows/python-ci.yml`
#: — one hit; it read `:5450` at `fe322247d1`, and the number is cited this way rather than typed
#: because D5 is exactly that a line number rots on merge), so the loop's non-test `case` arm
#: sends it straight to `FAILED_LEGS` and it reds the REQUIRED `python-ci-gate` for EVERY open PR —
#: measured on `44747206237`: `surface-guard` failure, `ci-introduced-failure` success,
#: `python-ci-gate` failure. A wrong pin here is therefore a fleet-wide block, not one PR's
#: inconvenience; the typed-reference arm is a separate repair and reads `0` on this tree.
#:
#: RE-CUT to the MERGED tree → (11822, "05e0ff542dfb8e62") by `#13919` (the `#4211` hierarchy-census
#: lane), merging `origin/main` `6fbd96f270` into `chore/4211-edge-census-mac`. The pin is an
#: ABSOLUTE count, so it moves with the base as well as with the PR, and this block therefore
#: declares ONLY the rows this PR owns — the base's rows above stay `main`'s business:
#:   * `origin/main` `6fbd96f270` on its OWN tree measures (11804, "e0c67cf6a5e97d20") — read off
#:     `--repin` in a detached worktree at that commit, so the base's contribution is measured and
#:     not inferred from the difference;
#:   * THIS PR adds +18, all inside the two files it introduces for the census, and every row
#:     delta below is the difference of two MEASURED counts — `--report --base 28ca66878d` on the
#:     merged tree minus the same on `origin/main` `6fbd96f270`'s own tree — not arithmetic on this
#:     pin: `g.query` 1483 → 1490 (+7) and `h.update` 60 → 62 (+2) in
#:     `tools/measure_4211_session_hierarchy.py`; `super().query` 62 → 67 (+5), `seen.update` 99 →
#:     101 (+2), `out.update` 41 → 42 (+1) and `graph.query` 267 → 268 (+1) in
#:     `tests/test_measure_4211_session_hierarchy.py`. 7+2+5+2+1+1 = 18 = 11822 − 11804.
#: Every one is an ordinary-word receiver on a dict/set, a `hashlib` hash object (the two `h.update`
#: rows), or a FalkorDB `Graph` (or a fake standing in for one). ⛔ The five `super().query(…)` rows
#: are a test double that OVERRIDES `Graph.query` and
#: delegates to the fake's own implementation — re-pointing them at a private spelling would break the
#: double's fidelity to the API it stands in for, which is exactly the `declared_markers.update`
#: precedent (a name-scoped rewrite would be sabotage). No new SDK-holding receiver is added.
#: The typed-reference count is 0 on this tree, measured (`--check` exit 0). It was 5 on the merged
#: tree before `21ae734ce3`: 1 `sdk.create_object(…)` in `tools/measure_4211_session_hierarchy.py` and
#: the four `sdk.ingest_corpus(…)` calls in `tests/test_13826_write_gate.py` that were `main`'s own
#: red — those two names are RETIRED public spellings (`#4282`/`#13899`) which the SDK's `__getattr__`
#: answers with an `AttributeError` migration hint rather than forwarding, so they were latent runtime
#: failures, not style.
#: ⛔ RE-CUT AGAIN onto `origin/main` `6fbd96f270` → (11804, "e0c67cf6a5e97d20"), MEASURED with
#: `--repin` on THIS tree. The value is the same one `origin/main` observes on its OWN tree
#: (`uv run python tools/sdk_privatise.py --repin` in a clean checkout of `6fbd96f270` prints
#: `(11804, 'e0c67cf6a5e97d20')`), so NONE of the rows below the previous pin are this PR's:
#: the four typed references this lane exists to fix were already re-pointed on `main` by
#: `#13973` (`tests/test_13826_write_gate.py` calls `_ingest_corpus` at `6fbd96f270`, and this
#: PR's diff no longer touches that file at all), so `typed references still pointing at a
#: public name: 0` on both sides. The +10 over `main`'s own pin of 11794 are the BASE's, from
#: the merges that landed while this PR waited (`#13914`, `#13950`, `#13972`-era); they are not
#: enumerated here, because a row list taken from another head is the D5 rot this repo forbids
#: (`docs/epics/2026-11-04-7949-extractor-v4-capstone/05-verify.md`, deviation D5). ⛔ The
#: practical effect is that `main`'s `--check` is RED on its own tree at `6fbd96f270` and this
#: re-cut turns it green; re-derive the pair with `--repin`, never by arithmetic on the last one.
#:
#: RE-CUT AGAIN → still (11822, "05e0ff542dfb8e62") by `#13919`, merging `origin/main`
#: `3d99bd51d6` into `chore/4211-edge-census-mac`. Both trees were re-measured for THIS merge rather
#: than carried forward: `origin/main` `3d99bd51d6` on its OWN tree prints
#: (11804, "e0c67cf6a5e97d20") — identical to the pair its own block above records, so that merge
#: moved neither side's number — and the merged tree prints (11822, "05e0ff542dfb8e62"), i.e. the
#: same 11804 + this PR's declared +18 (the two census files, row by row above). `--check` on the
#: merged tree: `typed references still pointing at a public name: 0`, exit 0.
#: RE-MEASURED AT THIS MERGE (the one that resolved this file's conflict) — not carried forward:
#: `origin/main` `3d99bd51d6` pins 11804 and `5ba273a1dc` (#13919) moved it to 11822 on `main`'s own
#: tree, and the merged tree here prints the SAME 11822, so this PR's delta against `main` is
#: comment-only: its contribution is the provenance and the non-delegability finding above, not a
#: new number. `--check` on the merged tree exits 0 with typed references `0`. Re-derive with
#: `--repin` on your own tree, never by arithmetic on the last pin.
#: RE-CUT DOWN 11822 → (11821, "abf4a5c65f380206") by the #14003 lane, and the move is a REMOVED
#: call site rather than a new shape: the `stub.get_point("p-1", served_out={})` call in
#: `tests/test_12941_read_your_writes.py` was the LAST site still calling the RETIRED public spelling
#: on a `TortoiseSDK` — #13899's sweep re-pointed that file's seven other sites to `_get_point` and
#: missed this one, so it raised `AttributeError` instead of asserting the record bound and reddened
#: `test (a)`. Re-pointed to `stub._get_point(...)`, which removes exactly that one untyped-receiver
#: row (-1: 11822 → 11821).
#: The proof is the ATTRIBUTION, not the sign of the delta: reverting ONLY that one call site (the
#: file's only `stub._get_point` hit) back to the retired public spelling reproduces exactly 11822,
#: so the -1 is that one removed call site and nothing else. ⛔ A shrink is NOT evidence on its own —
#: `--check` compares the `(count, digest)` PAIR and `--repin` prints a fresh pair that is pasted in
#: deliberately, so a re-pin absorbs whatever shape the tree holds in EITHER direction (remove two
#: untyped receivers, add one different shape -> net -1, absorbed). Read the rows `--repin` prints,
#: not the direction of the delta (#14003 review, round 3, P3 / #14030).
#: ⚠️ NO LINE NUMBERS ARE TYPED HERE ON PURPOSE: a reproducer that says `:803` is not reproducible
#: once the file moves, and this block carried exactly such a stale address — nothing in the tree
#: reads it (`grep -rln 'tools/sdk_privatise.py:' tests/` is empty), so a lane following it edits the
#: WRONG line, measures an unchanged 11821, and concludes the attribution is falsified (#14030
#: review, P2). Name the call site by its TEXT; the text is the durable fact.
#: `typed references still pointing at a public name: 0` on this tree says no typed site replaced it.
#: Measured with `--repin` on THIS tree, never by arithmetic on the last pin.
#:
#: RE-CUT UP (11821, "abf4a5c65f380206") → (11829, "f0301da129c0c584") by the #14062 lane on
#: `origin/main` `d4a8dddc4e`. ⛔ This one moves the WRONG WAY, so it is justified row by row rather
#: than by the delta: the pin is a SET digest and a residue that SHRINKS cannot hide a new reference
#: shape, but an INCREASE can absorb one, so every added row is named. All eight are ordinary-name
#: rows in three files merged by OTHER lanes — `update` and `query` are retired SDK names, so a plain
#: mapping spread on an untyped receiver is counted as residue beside `g.query`:
#:
#:   * `tools/strip_graph_entity_vectors.py` — +6 `poisoned.update`
#:   * `docs/evidence/7949-capstone/probes/capstone_measure.py` — +1 `measurement.update`
#:   * `tests/test_14002_record_only_journal.py` — +1 `sdk._get_proj().g.query`
#:
#: The SDK population itself did not move (`typed references still pointing at a public name: 0`),
#: which is what makes this safe to take: no typed call site is being legitimised by the re-cut. What
#: made it URGENT is that the pin is graded only on PULL REQUESTS, so `main` sat one row off the
#: pin with nothing grading it and every open PR then graded main's row as its own: #14045 measured
#: `observed: (11822, …)` against the 11821 pin on a tree that itself derived 11821, while `main` at
#: `38086c1bc5` derived 11822. A stale pin on main is a red gate on every PR that touches nothing in
#: this population (#14062). ⛔ NO CHECK-RUN COUNT IS QUOTED FOR THAT COMMIT ANY MORE: this block
#: used to cite `d4a8dddc4e` as carrying 12 check-runs, none of them `surface-guard`, and re-running
#: the job since has made that FALSE — the commit now reports 72 check-runs and `surface-guard` is
#: one of them (`completed`, `failure`). Check-run counts accumulate and never reset, so a count in
#: this block re-stales against a re-run; the durable fact is the RULE (the pin is graded on pull
#: requests only), not the measurement of one commit on one day.
#: Measured with `--repin` on THIS tree, never by arithmetic on the last pin.
#:
#: RE-CUT UP (11829, "f0301da129c0c584") → (11839, "42d5bd85c6e2fd9d") on the #14068 tree, which is
#: the MERGE tree `surface-guard` grades. Two things moved it, and the first one is the reason the
#: pin it replaces was RED ON MAIN from the moment it merged, not a row this PR added:
#:
#:   * +2 — `main`'s own rows, off the #14064 pin by construction. That lane measured
#:     `d4a8dddc4e` (11829, reproduced here) and the squash merged onto `ac57b2d38a`, which
#:     already carried #14065's `tests/test_usage_signal.py`: +2 `found.update`. Nothing in this
#:     PR touches that file; a re-cut on any PR would have had to carry it.
#:   * +8 — this PR's rows, every one of them on a receiver that is NOT the SDK:
#:     - `tortoise/cache_graph.py` — +1 `get_control_plane().query` (the Supabase control-plane
#:       client, whose `.query` is the SQL door this change adds)
#:     - `docs/evidence/12943-cache-leg/orgwide_two_graphs.py` — +4 (`sdk._get_registry().query` 2,
#:       `graph.query` 1, `db.list_graphs` 1 — the FalkorDB door plus the registry spelling that is
#:       already pinned 263 times on `main`)
#:     - `tests/test_13337_cache_graph_declaration.py` — +2 (`graph.query` 1, `seen.update` 1)
#:     - `tortoise/__main__.py` — +1 `payload.update` (a dict, beside the `payload.update` the file
#:       already carries)
#:
#: `typed references still pointing at a public name: 0` on this tree, so no typed call site is
#: legitimised by the re-cut: every row above is a receiver the sweep cannot type, which is exactly
#: what this pin exists to declare. Measured with `--repin` on THIS tree, never by arithmetic on the
#: last pin.
#:
#: RE-CUT DOWN (11831, "1aef9c61f81faf39") → (11825, "539914480e400722") by the `#14001` lane,
#: measured on `origin/main` `f392bb67d5`'s own tree. ⛔ THE SOURCE OF THIS RE-CUT IS `main`'S
#: **TREE**, NOT ITS **PIN**, and the two disagree: `main`'s pin reads 11829 (the `#14062` re-cut,
#: MEASURED on `d4a8dddc4e`'s tree and COMMITTED at `8610ed965c` — `git show
#: d4a8dddc4e:tools/sdk_privatise.py` still reads 11821, so the rev that CARRIES 11829 is the merge,
#: not the base) but `main`'s tree at `f392bb67d5` measures 11831, so a re-cut stated as
#: "11829 → 11825, delta −6" is arithmetically FALSE (11829 − 6 is 11823, and 11823 is the pair this
#: lane first pasted before it measured). Both `main` pairs here are `--repin` output, not
#: arithmetic on the pin: `11831, "1aef9c61f81faf39"` is what `main`'s own tree derives and
#: `11825, "539914480e400722"` is what THIS tree derives. The recurrence is the one `#14062`
#: documents: the pin is graded on PULL REQUESTS only, `main` merged `#14065` with nothing
#: grading it, and `--check` was therefore RED on `main` at `f392bb67d5` — that tree derives 11831
#: against its own 11829 pin and `--check` exits 1 there, measured. ⛔ Scoped to that rev and NOT
#: stated in the present tense: it was written as "`--check` is therefore RED on `main` today",
#: which `main` has since falsified by re-cutting to a pin it derives (measured, `--check` exit 0).
#: The delta from `main`'s tree is −6 and it is a REMOVAL, not an absorption: SIX of the
#: eight rows the `#14062` block above declared are the `poisoned.update` shape, and this lane
#: deleted the shape rather than declaring it. The binding-poison arms in
#: `tools/strip_graph_entity_vectors.py` called `poisoned.update(…)`, which this sweep cannot type
#: on an ordinary-word receiver. FIVE of them are now `poisoned |= …` — the same store, no row — and
#: one of those five keeps a `set(...)` conversion (`poisoned |= set(sub.names)`):
#: `ast.Global.names` / `ast.Nonlocal.names` are `list[str]` and `set |= list` is a `TypeError`
#: (`set.__ior__` takes a set, unlike `.update`, which takes any iterable), measured — the
#: `nonlocal-rebinding` row fails on exactly that. The SIXTH arm was DELETED, not converted: a
#: nested-scope `Assign` + `_dynamic_namespace_write` branch that could never run, because an
#: identical predicate `continue`s above it and the generic `Assign`/`AnnAssign` arm consumes the
#: rest. It only cost a residue row. ⛔ Five conversions and one deletion is SIX rows; the count is
#: stated here because "each is now `poisoned |= …`" was this block's own first draft and the tree
#: falsifies it (raised by a fresh review of this branch's pre-rebase head, #14058 — ⛔ cited by
#: ISSUE NUMBER, and the REASON is measured rather than asserted, because the draft that gave one
#: gave a false one: it said a pre-rebase commit "is reachable from no ref and disappears on the
#: next `gc`", and that commit (`83e94826a8a`) is an ANCESTOR of a ref, `refs/pull/14108/head`, which
#: `git gc` does not prune. What IS measured of it: on no remote BRANCH (`git branch -r --contains
#: 83e94826a8a` → empty) and not on `main` (`git merge-base --is-ancestor` → NO). ⛔ "NOT FETCHABLE
#: BY ITS SHA AT ALL" WAS ITSELF FALSIFIED (raised by review), and by the very command it cited —
#: which is why it is corrected rather than carried: `git fetch origin 83e94826a8a` fails on the
#: 11-character ABBREVIATION, NOT on the sha. git cannot `want` an abbreviation, and a FULL
#: 40-character sha IS served — `git fetch --depth=1 <url> <full sha>` in an EMPTY BARE repo returns
#: the commit, measured. So the fragility is narrower than "unreproducible": the address works only
#: written out in full, and reads as broken in the form this block normally prints it in. The rule
#: therefore stands on what IS measured — no REMOTE branch and no `main` reaches such a commit (a
#: leftover LOCAL branch may still contain it; the remote BRANCH that carried it is gone, which is
#: exactly what `git branch -r --contains` reads as empty), so it sits
#: outside every branch history the repo PUBLISHES (it IS inside `refs/pull/14108/head`'s, which is this
#: paragraph's own point, stated earlier in it — ⛔ NOT "four lines above", as an earlier draft of
#: this clause said: that offset does not reproduce, and an offset is the same RELATIVE-REFERENCE
#: class this block fixes below by naming the tree instead, since it rots as the prose above grows)
#: — and the rule does not rest on a `gc` sweep that never happens: which is why
#: this note points at the ISSUE NUMBER, and reserves the sha for the one place where the ADDRESS is
#: the claim (the `#14068` attribution below, where it is written out and its limits stated)).
#: ⛔ The −6 is verified as an ATTRIBUTION and not as a direction: the residue row sets were
#: MEASURED on both trees and diffed row by row (`py_files()` → `edits_for()` → `residue` on
#: `origin/main`'s own tree and on this one), and `poisoned.update` 6 → 0 is the ONLY row that
#: differs. Nothing else moved in either direction.
#: TWO absorbed rows are `main`'s, not this lane's, and they are named here because a re-pin that
#: only states the count is what this block forbids. `main`'s re-cut measured 11829 on
#: `d4a8dddc4e`'s tree and was COMMITTED at `8610ed965c`. ⛔ IT DID NOT DRIFT AFTERWARDS, and the
#: difference is not cosmetic: `#14065` (`ac57b2d38a`) is an ANCESTOR of `8610ed965c` — `git
#: merge-base --is-ancestor ac57b2d38a 8610ed965c` says so, measured — so the +2 was already beneath
#: the re-cut and `8610ed965c`'s own tree derives 11831. The 11829 pin was therefore STALE ON
#: ARRIVAL, not stale from a later drift. "It drifted afterwards" would imply a window in which the
#: pin was correct, and no such window existed; so `main`'s own tree at `f392bb67d5` measures 11831
#: against its own 11829 pin, and `--check` exits 1 THERE (the same recurrence `#14062` documents:
#: the pin is graded on PULL REQUESTS only, so `main` drifts and every open PR is then graded
#: against a stale number). The two rows are `found.update` 10 → 12, both in
#: `tests/test_usage_signal.py`, added by `ac57b2d38a` (`#14065`) — already an ancestor of the re-cut
#: commit, so it sits between
#: the 11821 pin and this tree, NOT "after" the 11829 one. Both sit on
#: a plain `set` fed by a generator expression, so neither is a typed reference to a swept name
#: (`typed references still pointing at a public name: 0` on this tree).
#: 11831 − 6 = 11825, and that arithmetic is a CROSS-CHECK of the measurement, not the evidence for
#: it: what was pasted into the pin is the pair `--repin` printed on THIS tree.
#: What made it URGENT is that the pin is graded only on PULL REQUESTS, so `main` sat one row off the
#: pin with nothing grading it and every open PR then graded main's row as its own: #14045 measured
#: `observed: (11822, …)` against the 11821 pin on a tree that itself derived 11821, while `main` at
#: `38086c1bc5` derived 11822. A stale pin on main is a red gate on every PR that touches nothing in
#: this population (#14062). ⛔ NO CHECK-RUN COUNT IS QUOTED FOR THAT COMMIT ANY MORE: this block
#: used to cite `d4a8dddc4e` as carrying 12 check-runs, none of them `surface-guard`, and re-running
#: the job since has made that FALSE — the commit now reports 72 check-runs and `surface-guard` is
#: one of them (`completed`, `failure`). Check-run counts accumulate and never reset, so a count in
#: this block re-stales against a re-run; the durable fact is the RULE (the pin is graded on pull
#: requests only), not the measurement of one commit on one day. ⛔ The squash-rebase onto
#: `ad79129cb59` DROPPED this retraction and re-asserted the 12-count with no conflict reported,
#: which is the same shape #14062 documents one paragraph up: a merge takes a rewritten side and
#: nothing flags the prose it silently left behind.
#:
#:
#: RE-CUT AT THE MERGE TREE (#14068 × `main`), and BOTH blocks above are carried because BOTH of their
#: facts are true of THIS tree: `main`'s −6 (the `poisoned.update` shape `#14001` deleted, so five arms
#: are now `poisoned |= …` and one dead branch is gone) AND `#14068`'s +8 receiver rows
#: (`2fe01fc775e`). ⛔ Neither
#: block's pin is this tree's pin — each was measured on ITS OWN tree, and a merged tree is a THIRD
#: tree — so the pair below was RE-DERIVED with `--repin` on the MERGE tree, which is the rule both
#: blocks cite above: measured, never arithmetic on the last pin. (11839 − 6 = 11833 would have been
#: the arithmetic guess from this tree's own HEAD block; the merged tree derives neither 11839, nor
#: 11825, nor 11833 — the pin below is what `--repin` printed.)
#: RE-CUT UP (11825, "539914480e400722") → (11833, "addee5ad564bd9bc") by the `#13874` lane. The
#: pasted pair is `--repin`'s output measured on THIS tree, never arithmetic on the last pin.
#: EIGHT added receiver rows in THREE shapes, attributed by measurement and not by count: over the
#: whole range `1956b3431f` (the `#14058` re-cut) → `ad79129cb5`, the `.py` diff carries EIGHT added
#: receiver rows and ONE removed receiver line that is the SAME statement re-indented (it is
#: re-added, so nothing was deleted) —
#: `git diff … -- '*.py' | grep -E '^[+-]' | grep -E '\.(query|update|delete)\b'`. The row counts
#: were read with `--report --base 28ca66878d… --top 5000` on both trees, not inferred from the diff:
#:   * `graph.query` 268 → 274 (**+6**), every one of them inside the file `ad79129cb5` (`#14067`,
#:     "invalidate the degraded snapshot on the eviction path") ADDS,
#:     `tests/test_evictor_invalidates_fallback_snapshot_13123.py`;
#:   * `client.delete` 42 → 43 (**+1**), in that SAME test file — ⛔ NOT in
#:     `tortoise/cache_evictor.py`, which is where the re-indented `graph.query` line lives;
#:   * `super().query` 67 → 68 (**+1**), in `tests/test_cache_eviction_evictor_pins.py`.
#: 6 + 1 + 1 == 8 == net delta (11833 − 11825), which is the cross-check that NO row was silently
#: absorbed in the other direction. ⛔ An earlier draft said "seven of the eight are `graph.query`"
#: and named only two shapes; the measurement above falsifies that (six, not seven) and the third
#: shape was missing entirely — corrected here rather than left as a reader's inheritance (review).
#: ⛔ NO NEW SHAPE APPEARED — the one thing a re-cut must never absorb silently. All three shapes are
#: already pinned rows on this tree, and their counts were re-read at THIS head rather than quoted
#: from `ad79129cb5`. ⛔ The move past the 274 measured there is `#14068`'s (`2fe01fc775e`), NOT this
#: lane's: `git diff` between `2fe01fc775e` and `83e94826a8a` over `'*.py'` carries no added receiver
#: row at all (its only non-comment `+`/`-` receiver line is the `seen.update` re-indent — the row
#: this block already counts above, `seen.update` 1 in the `+2` against
#: `tests/test_13337_cache_graph_declaration.py`). ⛔ THAT TREE IS NAMED BY SHA, because the issue
#: number cannot name it: `#14108`
#: resolved to `refs/pull/14108/head`, and that ref TRACKED the branch's head while the PR was open
#: — ⛔ IT TRACKS NOTHING NOW: `#14108` MERGED (`1b82cc7063`), its head branch is deleted, so the
#: ref is FROZEN at `d523c9be8c` and no longer moves at all. It resolved to `d523c9be8c` when the
#: figures below were measured, and `83e94826a8a` is the
#: OLDEST of the EIGHT commits between `2fe01fc775e` and it (`git log --oneline
#: 2fe01fc775e..d523c9be8c`). On that head this sentence does not drift, it INVERTS: `aee14c5ceb`
#: DELETES the `seen.update` line and puts `calls.append(…)` in that position (`git diff
#: d5ecef0d72 aee14c5ceb -- '*.py'`; the added `⛔ EVERY call is recorded…` comment sits LATER in
#: the same helper, not where the line was — an earlier draft of this clause said "leaves a comment
#: where it was", and the position holds code, raised by review), and `d523c9be8c`, the LAST of the
#: eight, inherits that deletion. So "re-indented, already counted" holds for the SIX commits from
#: `83e94826a8a` to `d5ecef0d72`, and stops at `aee14c5ceb`: asserting it of `83e94826a8a` alone is
#: too narrow, and asserting it of `d523c9be8c` is false. The sha IS resolvable: `git fetch origin
#: refs/pull/14108/head`
#: measured from a bare clone, and a FULL 40-character sha is served directly while the abbreviation
#: is not — see the note above on `#14058` for which form reproduces and which does not. Its own pin
#: is `(11838, "de5664dc98549d56")` — the capture rewrite's own row set — because `d523c9be8c`'s TREE
#: IS `1b82cc7063`'s (`git rev-parse d523c9be8c^{tree} 1b82cc7063^{tree}` → one sha), which is what
#: "inherits that deletion" above MEANS, and why it cannot carry the parent `2fe01fc775e`'s
#: `(11839, …)`. ⛔ That clause said `(11839, "0a7e60fa05aa31b9")` — "the same pair as the parent" —
#: and was FALSE (raised by review): a rev published with its PARENT's figures is the same "address
#: that resolves to a different tree" this block exists to catch, caught here in its own words. The
#: correct pair is the tree's own COMMITTED pin, `git show d523c9be8c:tools/sdk_privatise.py | grep
#: '^SWEEP_RESIDUE'`. ⛔ NOT GRADED ANY MORE: the falsifier arm that read this pair
#: (`tests/test_sdk_privatise_sweep.py`,
#: `test_a_revs_published_own_pin_is_the_pin_that_rev_commits`) was CUT in the B8
#: test-bureaucracy cut (2026-11-23), so the pair below is a record a reader checks by hand.
#: ⛔ NOT "the arm below": the arms
#: live in that test file, not under this block, so a relative pointer here would be the same
#: self-falsifying offset this paragraph removes elsewhere (an earlier draft of THIS clause said
#: "the arm below" and was wrong in the first place). Nothing here rests on the measurement; the
#: ADDRESS is the point: an address that resolves to a different tree is the defect this block
#: exists to catch.
#: So `graph.query` is **276** here, `super().query` **68**, `client.delete` **43** — read with
#: `--report`, not with a grep. A raw whole-tree grep is NOT that reading: it counts OCCURRENCES,
#: prose included, not the pinned residue ROW, and it counts this block's OWN prose about the
#: shape — so any WHOLE-TREE figure it prints is a function of the sentence quoting it, and no
#: whole-tree head figure is stable. It is quoted here only against a NAMED revision, and with the
#: pattern that actually produced the number: `git grep -hoE '\bclient\.delete\b' <rev> -- '*.py'`
#: reads **48** at `2fe01fc775e`. ⛔ NO **WHOLE-TREE** GREP FIGURE FOR THIS HEAD IS PUBLISHED,
#: DELIBERATELY, and the qualifier matters because an earlier draft of this very sentence dropped
#: it and was therefore false: the AST `--report` rows quoted above ARE head figures and are fine
#: (an AST count does not read this block's prose, only the grep does), and the SCOPED grep below
#: also publishes a head figure — **45** — because excluding this file makes it stable. What is
#: withheld is the WHOLE-TREE figure, the only one the prose moves. The reason is measured:
#: a PREVIOUS DRAFT named the wrong pattern here — it published its figures beside
#: `\bclient\.(query|update|delete)\b`, which also counts the 3 `client.query` hits and therefore
#: reads **51** at that same rev, not 48 — a figure that does not reproduce against the command in
#: the same sentence, which is the defect class this block exists to prevent, reproduced in its own
#: words (raised by review). And across the drafts, every version that stated a HEAD figure moved
#: the very figure it stated, none converging, because the grep counts this block's prose. ⛔ The
#: STABLE number is the SCOPE, not the digit: excluding this file
#: (`… -- '*.py' ':!tools/sdk_privatise.py'`) that same `\bclient\.delete\b` grep reads **45** at
#: BOTH `2fe01fc775e` AND this head — the prose population did not move at all, so any whole-tree
#: movement WOULD be entirely this block's own words. ⛔ "WOULD", not "is": an earlier draft of this
#: clause asserted that movement as a FACT, and a movement is not what the scoped pair measures — the
#: SCOPED number is the stable one precisely because, at whole-tree scope, the count is a function of
#: the prose quoting it (raised by review). So no WHOLE-TREE head figure is published and no movement
#: is asserted here: the conditional is the whole claim.
#: ⛔ AND "46" WAS REAL: `git grep -hoE '\bclient\.delete\b' ad79129cb5 -- '*.py'` reads **46** —
#: `ad79129cb5` being the very tree whose `graph.query` the old sentence quoted as "274 here"
#: (that reading is cited above, in the `graph.query` bullet of this block). Its defect was that the
#: sentence shipped on LATER
#: trees that read 48, i.e. STALE ON ARRIVAL, not that the number was invented — an earlier draft of
#: this correction over-charged it and is falsified by the same grep (raised by review). So the
#: condition this gate exists to catch — "a reference shape that was not there when this sweep was
#: verified is now on the tree" — is NOT triggered; only counts on shapes already declared moved.
#: The SDK population itself did not move (`typed references still pointing at a public name: 0`),
#: so no typed call site is legitimised by this re-cut.
#: ⛔ THIS LANE IS NOT THE CAUSE OF THE `+8`, and the attribution is carried by the paragraph below
#: rather than asserted here — it was the reason an earlier draft gave for it ("its whole diff is
#: figure literals") that was FALSE, and this slot is the one that carried the false form. ⛔ NOR IS
#: THE GATE'S RED STATE A PRESENT-TENSE FACT ABOUT `main`: the paragraph below scopes that narration
#: to `ad79129cb5`, where the pin read 11825 while that tree derived 11833. `main`'s pin is restated
#: against the rev it was measured at and never as `main`'s head — at `05d218fcaf` it reads
#: `(11831, "b14af0d2db12529b")`, so a typed "`main` today" pair is the rot this block documents.
#: The durable fact is the RULE: the pin is graded on PULL REQUESTS only, so `main` merges with
#: nothing grading it, and carrying the re-cut is what unblocks a PR that touches nothing in this
#: population — the same remedy the two blocks above took.
#:
#: ⛔ THIS RECORD CARRIES NO ASSIGNMENT, deliberately: merging two re-cuts left BOTH
#: `SWEEP_RESIDUE` lines in this file, and Python binds the LAST one, so the earlier value was
#: dead text that still read as the pin. ONE assignment, at the end of the records, cannot be
#: shadowed by a second re-cut.
#: ── #13998's own cut, on the tree that carries ALL of the records above ───────────────────
#: This PR adds EIGHT rows of its own on top of the above — SEVEN `cp.query` plus ONE `row.update`,
#: which is why it cannot be summarised as "seven `cp.query` rows":
#:
#:   * `cp.query` — `capacity_notice.read_account`'s orgs read and its drift-soft retry (2),
#:     `supabase_control.org_storage_notice_pct` and `set_org_storage_notice_pct` (2), and
#:     `tests/test_13998_capacity_notice.py` — the save/restore pair on the `_ColumnBlindCP` fake
#:     (2) AND the seam test's own `cp.query("organizations", …)` call (1) = 3, which an earlier cut
#:     of this record counted as 2. ⛔ The count is not the argument: the THIRD site is the reason
#:     this record now enumerates it, because the row diff against `origin/main`'s tree shows
#:     `cp.query` 263 → **270**, seven and not six;
#:   * `row.update` — `tests/test_13998_capacity_notice.py`'s `_org_row` helper (1).
#:
#: None is an SDK accessor (`cp` is the PostgREST client or a fake for it; `row` is a plain DICT),
#: and `typed references still pointing at a public name: 0` on this tree says no typed site became
#: untyped: seven NEW read/write calls did, which a typed-reference count cannot distinguish.
#: ⛔ MEASURED, NOT CARRIED — and every number below was printed by `--repin`, on the tree it
#: names, in the lane that wrote this line. The pre-rebase branch tree derived
#: (11838, "89ae27f32ee0f28a"); the two trees on either side of the conflict are that one and
#: `main`, so neither number is this pin. After the squash-rebase onto `origin/main` `ad79129cb59`,
#: `--repin` printed (11840, "9f1b8cff93b3a480") on the resolved tree and (11833,
#: "addee5ad564bd9bc") on `ad79129cb59`'s own tree — a delta of **+7**, which is the SEVEN rows
#: enumerated above and nothing else, so the attribution in this record is measured rather than
#: inherited. ⛔ The `#14001` block's (11825, …) is NOT the baseline to subtract from: it was
#: measured on `f392bb67d5`'s tree, and the four commits that landed between that tree and
#: `ad79129cb59` moved it to 11833. Subtracting 11825 here would invent a +15 that no diff
#: supports. A doc edit moves a counted row, so only the reading at the final tree is the pin.
#: ⛔ The SECOND merge into this branch (`origin/main` `8ad6ac46f0`, the `#13874` lane's own re-cut
#: plus the inventory and scorecard commits) was re-measured the same way and did NOT move the pin:
#: `--check` still exits 0 at 11840 on the merged tree, so the +7 attribution survives a base that
#: carries a record claiming 11833. Neither record's number is this pin; the tree is.
#: ⛔ The THIRD merge (`origin/main` `8afd28cd01d`, three commits) DID move the pin, and the move is
#: `main`'s alone: 11840 → **11838**, −2, re-pinned here at `(11838, "63ba2bae2c53c633")` from
#: `--repin`'s own output on this tree. ⛔ Attributed by MEASUREMENT and not by count — `edits_for`
#: was run per file over the 27 `.py` files that differ between `1ad388b5f6ea` and `origin/main`,
#: and ONE row differs in ONE direction: `found.update` 2 → 0 in `tests/test_usage_signal.py`, from
#: `df0525a7a90` (`#14011`'s review remainder), which rewrote that file. No other file's residue
#: counter moved, in either direction — which is the cross-check that nothing was silently absorbed.
#: ⛔ NO NEW SHAPE APPEARED: `found.update` is a DECLARED row on this tree (`10` occurrences — the
#: two `#14065` added and this commit removed are why the aggregate reads 10 rather than 12), so the
#: condition this gate exists to catch — "a reference shape that was not there when this sweep was
#: verified is now on the tree" — is NOT triggered; only a count on an already-pinned shape moved.
#: The SDK population did not move either (`typed references still pointing at a public name: 0`),
#: so no typed call site is legitimised by this re-cut. `#13998`'s own +7 above survives intact:
#: `cp.query` **270** (263 on `origin/main`'s own tree + this PR's seven) and `row.update` **60**
#: carry the eight rows that record enumerates.
#:
#: ⛔ REVERTED BY THE #14038 MERGE: an earlier cut of this file RECORDED a rename of the two
#: duck-typed onboarding seed surfaces (`_OrgSeedSurface` in `hosted_api.py`, `_SdkSeedSurface` in
#: `onboarding/selfhost.py`) from the PUBLIC doors `create_subject`/`create_edge` to the private
#: spellings. That record was FALSE in its premise and its measurement, and the rename it licensed
#: BROKE the product: `onboarding/seed.py` calls `surface.create_subject_anchor(...)` and
#: `surface.create_edge(...)` (three sites), neither surface defines `__getattr__`, so every hosted
#: AND self-hosted seed raised `AttributeError` and the REST seed route 500'd — measured at
#: `93ada9cece`: `tests/test_onboarding_selfhost_w12.py` alone was **11 failed** (17 passed),
#: `tests/test_onboarding_seed.py` **2 failed**, and **13** is the total across BOTH files — the
#: two figures are not additive pieces of one number. The doors are PUBLIC again — and the spelling
#: is ⛔ `create_subject_anchor`, NOT `create_subject`: the latter is the RETIRED SDK method name
#: (`#12901`), and `hosted_api.py` states the rule beside the door it may not be. So the surfaces
#: declare `create_subject_anchor`/`create_edge` and delegate inward, and the eight
#: test-double call sites in `tests/test_9572_discriminator_not_clobbered.py` were re-spelled to
#: `create_subject_anchor` with the surface, not left on the retired name. ⛔ THIS IS THE SHAPE THE
#: BLOCK BELOW FORBIDS, reached anyway: *"the alternative — giving a
#: SURFACE private-looking doors so a name-based sweep stays quiet — is the sweep dictating the
#: adapter's API"*. A residue count is not a reason to change a published adapter contract, and a
#: test double renamed in lockstep with the real surface hides exactly this failure
#: (`tests/test_onboarding_seed.py`'s door pin is what caught it). The eight rows are therefore
#: RESTORED as residue: they are ordinary-word receivers on an untyped object, which is what this
#: counter exists to report, not to eliminate.
#: ⛔ THE PIN MOVED BY +8 on the revert, MEASURED on this tree with `--repin` (11845 →
#: 11853), which is the eight restored call sites and nothing else — a CROSS-CHECK of the revert,
#: not the evidence for the pin. The FastAPI route `create_subject` in `hosted_api.py` is NOT
#: affected either way: that is an HTTP endpoint, not a seed-surface method.
#: ⛔ TWO DIFFERENT `+8`s LIVE IN THIS BLOCK, and they must not be read as one figure: the REVERT's
#: `+8` above (eight RESTORED double call sites → 11845 → 11853) and the FEATURE's own `+8` receiver
#: rows the second-merge paragraphs below attribute. Separate changes, separate trees.
#: ⛔ THIS LANE IS NOT THE CAUSE OF THE FEATURE'S `+8` (see the note directly above) — but NOT for
#: the reason an earlier draft gave. It said
#: "its whole diff is figure literals: stripping every digit run from each `-`/`+` pair leaves the
#: pair identical", and that is FALSE of the `#14108` commit that landed it: the capture rewrite
#: also rewrites `tests/test_13337_cache_graph_declaration.py` (the `_capture` helper and the anchors),
#: so the digit-stripped `-`/`+` multisets differ in both files. The conclusion still holds, but it must be
#: SCOPED, and an earlier draft of THIS sentence got it wrong: it said `--repin` reads the same pair
#: "on `origin/main` and on this head, so … nothing in it moves a receiver row", which CONTRADICTED
#: the re-cut paragraph FURTHER DOWN THIS BLOCK ("The −1 is THIS LANE's own"). ⛔ No line distance is
#: quoted on purpose: an earlier draft of this very sentence said "36 lines later", the measurement
#: was 38, and the figure moves whenever this block is edited — the same self-falsifying shape the
#: paragraphs above describe. Measured, and both halves are true of different trees: `--repin`
#: reads the SAME pair — `(11839, '0a7e60fa05aa31b9')` — on the capture rewrite's own PARENT,
#: `2fe01fc775e`, so everything up to that rewrite moves no receiver row; and it reads
#: `(11838, 'de5664dc98549d56')` on the rewrite itself, `1b82cc7063`, so the ONE row the rewrite
#: moves is `seen.update` **102 → 101** (the re-cut paragraph below attributes it, and states why a
#: `dict` accumulator in a test is a swept receiver). Both pairs above are `--repin` output measured
#: on the tree they name, in a detached worktree at that revision — not read off the committed pin.
#: ⛔ A reader must not have to reconcile two paragraphs that disagree, which is the "stale on
#: arrival" class this block polices (raised by review). ⛔ NO PRE-MERGE SHA IS CITED IN THIS
#: SENTENCE, and this is the sentence's second correction: an earlier draft named the lane's own
#: pre-fix tip and called it "the capture rewrite's PARENT head", which it was not — that tip's
#: parent is `2fe01fc775e`, the very rev just named, so the draft named one tree twice and called
#: the second one its parent (its pin was the same `(11839, …)`, so it carried no residue
#: measurement the parent does not already have). So this sentence names only the two revs `main`'s
#: own history survives, and nothing else. ⛔ That is a rule about THIS SENTENCE and not an embargo
#: on every sha: the `#14068` attribution above does cite one deliberately, because THERE the
#: address is the whole claim and `#14108` resolves to a different tree — its resolution path and
#: its limits are stated at that sentence rather than re-derived here.
#: ⛔ THE "GATE IS RED HERE" NARRATION IS SCOPED, NOT PRESENT-TENSE. That paragraph described
#: `ad79129cb5`, where the pin read 11825 while that tree derived 11833 — `--check` red. It is NOT
#: the state of `main` at `2fe01fc775e`, which COMMITS `(11839, "0a7e60fa05aa31b9")` — exactly what
#: `--repin` derives there, so `--check` exits 0 on that tree and on this head. `main`'s tree at
#: `e022fc935b` (the `origin/main` this was written against) re-cut to `(11838, "de5664dc98549d56")`
#: on `1b82cc7063`'s tree, which made that re-cut — and `main`'s pin has moved again since, so the
#: pair is stated against the rev and never as `main`'s head. The 11839 above is a
#: figure for the revision it names, never a stale claim about `main`'s head. ⛔ It names the TREE and
#: not "the pair THIS head pins", which is what it said before a merge re-cut this head to
#: `(11844, "70d6cfdcd8b769e4")` and left the sentence asserting a pair this head does not carry —
#: the same relative-reference class this paragraph fixes above for `e022fc935b`, and a
#: reader should not have to reconcile it against this block's own pin (raised by review).
#: The recurrence `#14062` and `#14058` document (the pin graded on PULL REQUESTS only) is real, but
#: an earlier draft asserted it as a live red and a later review falsified that (raised by review);
#: the fix for the recurrence is still to grade the pin on `main`.
#: RE-CUT AGAIN at the SECOND merge tree (#14068 × `main` `8ad6ac46f0`), for the same reason the
#: note above states: `main` moved three more commits AFTER the first merge (`628765ff3c`,
#: `1f654d16b5`, `8ad6ac46f0` — cite-census re-statements touching `tools/hosted_api_cite_census.py`,
#: `tortoise/hosted_api.py` and `tortoise/activation_scorecard.py`), so the pair above was measured on
#: a tree that no longer exists. ⛔ It is re-measured, not carried: `--repin` on THIS tree prints the
#: same pair the first merge derived — `(11841, "05a67e3380c806b1")` — and that is a MEASUREMENT, not
#: an assumption that nothing moved. What it means is that `main`'s three commits move no unresolvable
#: receiver row in either direction: `tortoise/hosted_api.py` and `tortoise/activation_scorecard.py`
#: changed only figure literals and prose, so the residue ROW SET is unchanged (same count AND same
#: digest — the digest is over the whole `row -> count` multiset, so an equal pair is an equal set).
#: Both parent blocks above stay: `main`'s −6 (`#14001`'s `poisoned.update` shape, five arms now
#: `poisoned |= …` and one dead branch deleted) and this PR's +8 receiver rows are BOTH true of this
#: tree, and a reader re-deriving the pin needs both facts, not just the surviving number.
#: `--check` on this tree: `typed references still pointing at a public name: 0`.
#: RE-CUT DOWN at the THIRD merge tree (#14068 × `main` `df0525a7a9`): (11841, "05a67e3380c806b1") →
#: (11839, "0a7e60fa05aa31b9"). The −2 is `main`'s, not this branch's, and it is an ATTRIBUTION
#: rather than a direction: the residue row sets were derived on both trees
#: (`--report --base 28ca66878d… --top 100000`, on `467e02393a` and on this merge) and diffed row by
#: row — `found.update` 12 → 10 is the ONLY row that differs. It is `tests/test_usage_signal.py`,
#: respelled by `main`'s `#14071` (`df0525a7a9`, the review remainder of `#14065`): the census helper
#: there no longer carries that shape, so the row is REMOVED rather than absorbed into a new one.
#: 11841 − 2 = 11839 and the arithmetic is a CROSS-CHECK of the measurement, not the evidence for it:
#: the pair above is what `--repin` printed on THIS tree. All blocks above stay — `main`'s −6
#: (`#14001`'s `poisoned.update` shape), this PR's +8 receiver rows, and the second merge's "nothing
#: moved" are each true of the tree they were measured on, and a reader re-deriving the pin needs the
#: chain, not just the surviving number.
#: `--check` on this tree: `typed references still pointing at a public name: 0`.
#:
#: RE-CUT → (11829, "0a549dd352726b5c") by `#13971` (this PR), and the +7 is ENTIRELY this PR's,
#: measured against the base it merged rather than inferred from the difference — the pin above is
#: `main`'s own number and `main` still observes it, so every row below is owned here:
#:   * `origin/main` `0656b056d7` (the merge this branch took) on its OWN tree prints
#:     `--report --base 28ca66878d` → **11822**, i.e. exactly the (11822, "05e0ff542dfb8e62") pin, so
#:     the base contributes NOTHING to the move;
#:   * THIS branch's merged tree prints **11829** on the same instrument, and the row delta is two
#:     shapes only, each MEASURED on both trees: `proj.g.query` 3637 → 3642 (+5) and
#:     `g.query` 1490 → 1492 (+2). 5 + 2 = 7 = 11829 − 11822.
#: Where the 7 sit, counted per file on both trees: `tests/test_10000_turns_record_only.py`
#: `proj.g.query` 26 → 28 (+2, the two `rows = proj.g.query(` reads in the review round's P1 pins
#: `test_the_graph_store_lane_keeps_the_turn_VECTOR` and
#: `test_a_refused_record_write_falls_back_to_the_graph_WITH_its_vector`), and
#: `tests/test_13826_write_gate.py` `proj.g.query` 8 → 11 (+3, the `REMOVE n.embedding` resets and the
#: `DETACH DELETE` between gate arms) and `g.query` 23 → 25 (+2, the same resets on its own graph
#: fixture). ⛔ Every one is an ordinary-word receiver on a FalkorDB `Graph` or the test's
#: `FalkorProjection` double (`proj.g`) — the API those stand in for IS `Graph.query`, so
#: re-pointing them at a private spelling would break the double's fidelity, which is exactly the
#: `super().query` / `declared_markers.update` precedent declared above. No SDK-holding receiver is
#: added, and no new shape appears: the typed-reference count is `0` on this tree, measured.
#:
#: RE-CUT AGAIN → (11832, "d96145e7d7b3d741") by the SAME PR, and this move is a REPAIR of a
#: MIS-TYPED receiver rather than a new allowance. `#13899`'s sweep renamed
#: `tortoise/onboarding/seed.py`'s seed-surface calls from `create_subject`/`create_edge` to the
#: private spellings, because the parameter holding the surface is NAMED `sdk` and this tool's
#: own contract (`_SDKISH`) says such a name IS the SDK. It is not one here: the object is the
#: seed surface (`hosted_api._OrgSeedSurface` / `onboarding.selfhost._SdkSeedSurface`), whose
#: published doors ARE `create_subject`/`create_edge` — the private spellings are what the
#: surface calls on the real SDK underneath, so the swept form could only raise `AttributeError`
#: (measured: twelve `test_onboarding_selfhost_w12.py` cases and four `test_onboarding_seed.py`
#: cases red, plus a 500 from the REST seed route). The receiver is renamed `surface` there and
#: the public doors are restored, so the three call sites become UNTYPED receivers — `create_edge`
#: was already a residue row (`proj.create_edge`), and `surface.create_subject` ×2 /
#: `surface.create_edge` ×1 are the same ordinary-word shape on an object no typing can decide.
#: +3 = 11832 − 11829; the typed-reference count went 3 → 0 and is `0` at this pin, measured.
#: ⛔ The alternative — giving a SURFACE private-looking doors so a name-based sweep stays quiet —
#: is the sweep dictating the adapter's API, and it is what this re-cut undoes.
#:
#: MERGED `origin/main` (`c841cfc269`, #14003) — and the two blocks above and below are the SAME pin
#: read at two heads, not two competing numbers. `main`'s own lane took 11822 DOWN by one (a removed
#: call site, `tests/test_12941_read_your_writes.py` re-pointed to `stub._get_point`), so neither side's
#: figure carries: the merged pin is RE-MEASURED on THIS tree with `--repin`, never computed from
#: 11821 + 7 + 3. ⛔ The row list is kept as history under the head it was measured at, because a row
#: list taken from another head is the D5 rot this repo forbids (`05-verify.md` deviation D5).
#: MEASURED ON THE MERGED TREE (the merge that resolved this file's conflict): `--repin` prints
#: (11831, "0a42bc1bd08ae831") and `--check` exits 0 with `typed references still pointing at a public
#: name: 0`. Both sides' histories above are preserved because each row is still owned by the head
#: that measured it; only THIS pin is this tree's.
#:
#: `main`'s own block, kept as the history of the -1:
#: RE-CUT DOWN 11822 → (11821, "abf4a5c65f380206") by the #14003 lane, and the move is a REMOVED
#: call site rather than a new shape: the `stub.get_point("p-1", served_out={})` call in
#: `tests/test_12941_read_your_writes.py` was the LAST site still calling the RETIRED public spelling
#: on a `TortoiseSDK` — #13899's sweep re-pointed that file's seven other sites to `_get_point` and
#: missed this one, so it raised `AttributeError` instead of asserting the record bound and reddened
#: `test (a)`. Re-pointed to `stub._get_point(...)`, which removes exactly that one untyped-receiver
#: row (-1: 11822 → 11821).
#: The proof is the ATTRIBUTION, not the sign of the delta: reverting ONLY that one call site (the
#: file's only `stub._get_point` hit) back to the retired public spelling reproduces exactly 11822,
#: so the -1 is that one removed call site and nothing else. ⛔ A shrink is NOT evidence on its own —
#: `--check` compares the `(count, digest)` PAIR and `--repin` prints a fresh pair that is pasted in
#: deliberately, so a re-pin absorbs whatever shape the tree holds in EITHER direction (remove two
#: untyped receivers, add one different shape -> net -1, absorbed). Read the ROWS from `--report`'s
#: dump against `SWEEP_BASE` (`--repin` prints only the tuple), not the direction of the delta
#: (#14003 review, round 3, P3 / #14030).
#: ⚠️ NO LINE NUMBERS ARE TYPED HERE ON PURPOSE: a reproducer that says `:803` is not reproducible
#: once the file moves, and this block carried exactly such a stale address — nothing in the tree
#: reads it (`grep -rln 'tools/sdk_privatise.py:' tests/` is empty), so a lane following it edits the
#: WRONG line, measures an unchanged 11821, and concludes the attribution is falsified (#14030
#: review, P2). Name the call site by its TEXT; the text is the durable fact.
#: `typed references still pointing at a public name: 0` on this tree says no typed site replaced it.
#: Measured with `--repin` on THIS tree, never by arithmetic on the last pin.
#:
#: RE-CUT AGAIN → (11833, "c115acaf5bbb4163") by the two onboarding-seed PINS this branch adds
#: (`tests/test_onboarding_seed.py`, `#13971` review round 8): they call
#: `surface.create_subject(...)` and `surface.create_edge(...)` on the seed surface, which are
#: ordinary-word receivers on an untyped `surface` — the shape this counter exists to report, not
#: a typed SDK site. Both calls sit in ONE test
#: (`test_the_org_seed_surface_delegates_the_seed_doors_to_the_sdk`), and they are the two rows
#: this branch adds: `surface.create_subject` 6 → 7 and `surface.create_edge` 1 → 2, read from
#: `--report`'s row dump against `SWEEP_BASE` (⛔ NOT
#: from `--repin`, which prints the tuple and nothing else). (The `create_subject` half of that row
#: is GONE as of the seed-surface door rename recorded at the pin below — the door is
#: `create_subject_anchor` now; `surface.create_edge` is still a row, because `create_edge` is not
#: a retired name and this counter reports ordinary-word receivers from the swept map only, so
#: read the LIVE rows off `--report`, never this history.) That head's TOTAL was its own
#: measurement: `--repin` printed `(11833, "c115acaf5bbb4163")` there, and the 11831 → 11833 (+2)
#: step is fully attributed to the two rows named above (+1 each). ⛔ THE DIGEST IS NOT CARRIED
#: FORWARD: after the merge and the door rename the SAME COUNT carries a DIFFERENT digest —
#: `--repin` prints `(11833, "4358280454f50b14")` and `--check` exits 0 on THIS tree. That is
#: legitimate, not a contradiction: the digest is over the whole `row -> count` MULTISET (the note
#: above says so), so an equal total with a different digest is a DIFFERENT tree — the rename
#: removed rows (the −11 attributed at the pin below) and the merge added rows. ⛔ Read the PAIR
#: from `SWEEP_RESIDUE` (the authority), never from this paragraph; a lane that re-runs `--repin`,
#: gets `4358280454f50b14`, and trusts the digest typed here would conclude its own pin is wrong.
#: The earlier per-head totals recorded ABOVE are `--repin` readings at their own heads, not
#: arithmetic on today's rows.
#: ⛔ A FIRST READING OF THIS DELTA attributed the +2 to the `tests/test_github_indexer.py`
#: citation rows added for the moved `entities.py` addresses, on the theory that a pin row
#: QUOTES its statement and so re-counts the `.query(` inside the string. That is wrong on this
#: tree and is marked here so it is not re-applied: those rows are unchanged across the move, and
#: the two rows that actually differ are `surface.create_subject` (6 → 7) and
#: `surface.create_edge` (1 → 2), both in the seed pins below.
#:
#: MERGED ONCE MORE (`origin/main` `7efdedf2382`, #14010/#14020/#14037) — this file's conflict was
#: resolved by KEEPING BOTH sides' histories (main's attribution wording and no-line-numbers warning,
#: this branch's +2 seed-pin rows) and then RE-MEASURING the pin on the merged tree with `--repin`,
#: never by combining 11833 and 11821. All three trees were measured, each on its own tree:
#: `origin/main` `7efdedf2382` prints (11822, "acf84ed8c407e706"), this branch's own pre-merge head
#: `23a186c066e2` prints (11833, "c115acaf5bbb4163"), and the MERGED tree prints
#: (11834, "0253a625dee4ed86"). So the move is this branch's +2 seed rows (already attributed above)
#: PLUS +1 that is `main`'s own — `main`'s pin itself went 11821 → 11822 after `c841cfc269` — and
#: nothing in this merge adds a row of its own. ⛔ That attribution is three measurements, not an
#: arithmetic check: the +1 is named because `main`'s OWN tree prints it, not because 11834 − 11833
#: equals 1.
#:
#: RE-CUT ONTO `origin/main` `d4a8dddc4e` (#14043/#14045/#14050/#14010/#14020/#14032/#14022…) —
#: re-measured on the merged tree with `--repin`, never computed: `(11841, "53273cee50de5834")`. The
#: two trees were measured separately on the same instrument, and the ROW LIST was diffed row by row
#: (`--report --base 28ca66878d --top 5000` on each): `origin/main` `d4a8dddc4e` prints **11829**, this
#: tree prints **11841**, and the twelve rows between them are exactly the four this branch has
#: carried since the seed work — `proj.g.query` 3637 → 3642 (+5), `g.query` 1490 → 1492 (+2),
#: `surface.create_subject` 4 → 7 (+3) and `surface.create_edge` absent → 2 (+2). No new shape comes
#: from the merge: 11829 + 12 = 11841, and 11829 is `main`'s own measurement, not its pin.
#: ⛔ `main`'s pin still reads 11821 on that tree, so `main`'s own `--check` is RED by EIGHT — that is
#: `main`'s number to fix, and this pin takes the MEASUREMENT (11829) rather than `main`'s stale pin,
#: so none of that drift is attributed to this PR. `--check` on this tree exits 0 with
#: `typed references still pointing at a public name: 0`.
#: MEASURED TWICE, INDEPENDENTLY, AND THE ROW SETS AGREE — the decomposition above the pin, so a
#: reader can reproduce it rather than trust the pair. Three trees were measured separately with
#: `--report --base 28ca66878d --top 100000` and diffed row by row: `origin/main` `2fe01fc775`
#: derives **11839**, this branch's pre-merge head derives **11843**, the merged tree **11851**.
#:  * `main` → merged is **+12**, exactly the four rows this branch has carried since the seed work
#:    — `proj.g.query` 3637 → 3642 (+5), `g.query` 1490 → 1492 (+2), `surface.create_subject`
#:    4 → 7 (+3), `surface.create_edge` absent → 2 (+2). No new shape comes from the merge.
#:  * branch → merged is **+8**, and every row is `main`'s own addition since `df0525a7a9`
#:    (`#14068`/`#14101`): `db.list_graphs` 28 → 29, `get_control_plane().query` 11 → 12,
#:    `graph.query` 274 → 276, `payload.update` 23 → 24, `sdk._get_registry().query` 263 → 265,
#:    `seen.update` 101 → 102. This PR's own diff is figure literals and prose and adds no row.
#: So 11839 + 12 = 11851 and 11843 + 8 = 11851 — a CROSS-CHECK of three measurements, never the
#: evidence for the pin. Both lanes that re-cut this file at this tree printed the same pair.
#: RE-CUT → (11851, "f1ba9f2563209e40") on the SECOND `origin/main` merge into this branch
#: (`2fe01fc775e`, the `#13337` merge, `#14068`), measured with `--repin` on that merged tree
#: rather than taken from either side: the pin is graded on PULL REQUESTS only, so `main`'s
#: own tree drifts under every merge it lands and neither side carries this tree's number.
#: ⛔ THAT PARENTHETICAL READ `#13994` AND WAS WRONG (review, round 12): `git log -1 --format=%s`
#: on `2fe01fc775e` reads `fix(12943): … (#13337) (#14068)`, so its PRs are `#13337` and `#14068`;
#: `#13994` is `e0accb8fe6`, a LATER commit that is not an ancestor of that tree. The block names
#: this tree `#14068` seven other times, so the wrong number made it give two answers for one
#: address — an address that resolves to a different tree, which is the class this block polices.
#: Rule unchanged: re-derive with `--repin` on YOUR tree, never by arithmetic on the last pin.
#: RE-CUT AGAIN → (11845, "d86902194b3dbbbb") — a **SHRINK of 6**, and every row is named, because
#: a shrink is not evidence on its own (see the -1 above). The three module-level availability probes
#: (`tests/test_assembly_sdk.py`, `tests/test_assembly_fixtures.py`,
#: `tests/longmem_eval/test_assembly_arm.py`) each carried exactly two untyped rows that #13899 left
#: behind: `_probe.create_point("statement", …)` and `_probe.tortoise_fts_query(…)`. Both spellings
#: were RETIRED by #13899 — the public `create_point` (→ `create_entity("point", …)`) and the
#: `tortoise_fts_query` attribute (→ the internal `_tortoise_fts_query`) — so the probe raised
#: `AttributeError`, its own `except Exception` swallowed it into `FALKORDB_AVAILABLE = False`, and the
#: assembly suites went SILENT (skip locally, `refusing that (#1436)` ERROR in CI where
#: `TORTOISE_TEST_EXPECT_URI=1`). That is the red `test (a)` reported on #13994's shard `a`, not a
#: code defect in this PR. Re-pointed to the surviving doors, which removes 2 rows per file: measured
#: `git grep -c 'create_point('` / `'tortoise_fts_query('` on the three files reads 2 each BEFORE and
#: 0 each AFTER, and 11851 − 6 = 11845 is this tree's own `--repin` reading, not arithmetic on the
#: last pin. `--check` exits 0 (`typed references still pointing at a public name: 0`).
#: ── RE-CUT AT THE FOURTH MERGE TREE (`#13998` × `main` `2fe01fc775`) ─────────────────────────
#: Measured with `--repin` on THIS tree, never by arithmetic on the last pin: the merge tree derives
#: (11840, "2d0be375b93fd382"). The two PARENTS derive 11832 (this branch, `27ddec57dd`) and 11839
#: (`main`, which carries `#14068` and `#14101`), so neither parent's pair is this pin — a merged tree
#: is a THIRD tree, and the digest proves it: it equals neither parent's digest.
#: ⛔ THE +1 AGAINST `main` IS ATTRIBUTED ROW BY ROW, not inferred from the count. `--report --base
#: 28ca66878d… --top 100000` was run on a clean `origin/main` tree AND on this merge tree and the two
#: row sets diffed: NINE rows are added (`cp.query` 263→**270**, `row.update` 59→**60**, and one NEW
#: `self.plane.query` — this branch's capacity-notice seam and its fake) and EIGHT are REMOVED (the
#: whole `org.create_subject` 2, `selfhost.create_subject` 2 and `surface.create_subject` 4 rows, gone
#: with this branch's onboarding-seed retirement rename). +9 − 8 = +1, and that arithmetic is a
#: CROSS-CHECK of the row diff, not the evidence for the pin. All blocks above stay verbatim,
#: including the `#14068` merge-tree block and the `#13998` cut: each is true of the tree it names,
#: and a reader re-deriving this pin needs the whole chain.
#: `--check` on this tree: `typed references still pointing at a public name: 0`.
#: RE-CUT for the CAPTURE rewrite (#14108 review round 3): (11839, "0a7e60fa05aa31b9") →
#: (11838, "de5664dc98549d56"). The −1 is THIS LANE's own and it is worth naming, because nothing
#: about it looks like a receiver edit: `_capture` in
#: `tests/test_13337_cache_graph_declaration.py` recorded calls with `seen.update(…)` and now
#: APPENDS them (so a second read cannot be truncated — the review's finding), and `update` is a
#: SWEPT NAME: `seen.update` is a residue row in its own right, so replacing the accumulator
#: REMOVED one.
#: ⛔ ATTRIBUTED BY MEASUREMENT: the residue row multisets were derived on the parent head and on
#: this tree (`--report --base 28ca66878d… --top 5000`, then diffed) — `seen.update` 102 → 101 is
#: the ONLY row that differs, and the pin is the pair `--repin` prints, never arithmetic.
#: The row is a plain `dict` accumulator in a test, not a typed reference to a swept name, so the
#: SDK population is untouched (`typed references still pointing at a public name: 0`).
#: ── RE-CUT AT THE FIFTH MERGE TREE (`#13998` × `main` `e022fc935b`) ────────────────────────
#: ⛔ BOTH SIDES' RECORDS ARE KEPT VERBATIM ABOVE and there is ONE assignment after them: each block
#: is true of the tree it names, and a reader re-deriving this pin needs the whole chain; the single
#: assignment is what stops a second re-cut's value becoming dead text that still reads as the pin.
#: Measured with `--repin` ON THIS MERGED TREE, never by arithmetic on either parent's pair: this
#: tree derives (11839, '39ce8cef9690600a'). The two parents' own pairs are the ones the blocks above
#: state — 11840 for the branch, 11838 for `origin/main` `e022fc935b` (its `#14108` `_capture`
#: rewrite replaced a swept `seen.update` accumulator) — and the digest equals NEITHER, which is the
#: evidence a merged tree is a third tree rather than a sum. `--check` on this tree: exit 0,
#: `typed references still pointing at a public name: 0`.
#:
#: MERGED `origin/main` (`e022fc935b2d`, #14100/#14108) at `74fdd9e922` — both sides' histories above
#: are kept, and the pin is RE-MEASURED on THIS tree with `--repin`, never combined arithmetically: it
#: prints `(11839, "39ce8cef9690600a")` and `--check` exits 0 with `typed references still pointing at a
#: public name: 0`. The two PARENTS' pairs are `(11840, "2d0be375b93fd382")` (this branch,
#: `991bb3436f`) and `(11838, "de5664dc98549d56")` (`main` at `e022fc935b`) — BOTH stated with the
#: commit they belong to, because `(11844, "70d6cfdcd8b769e4")` is `origin/main` `e0accb8fe6`'s OWN
#: tree, a DIFFERENT commit, and an earlier cut of this paragraph attributed it to this merge
#: (#14038 review, P3). The row composition is read off `--report --base 28ca66878d… --top 5000` AT
#: THIS HEAD, not inferred — and not as a property of the rev named above: an earlier cut dropped the
#: head scope entirely, so the figures below read as `74fdd9e922`'s own, and a reader re-deriving them
#: at that rev gets a different reading for two of the three rows. `proj.g.query` 3642 and `g.query` 1492
#: are this PR's own rows, unchanged by the merge, and
#: `seen.update` is **101** — `main`'s −1, which is `tests/test_13337_cache_graph_declaration.py`
#: accumulating by APPEND rather than `update` (#14108), a file this branch's DIFF does not touch.
#: ⛔ NO ARITHMETIC ON THE PARENTS IS OFFERED AS EVIDENCE: the merge tree's pair is what `--repin`
#: printed, and it is neither parent's — which is the field that proves a third tree, not a sum.
#: ⛔ THE PIN CHAIN, CORRECTED — each figure is true of the tree it NAMES. An earlier draft of
#: this block attributed `(11844, "70d6cfdcd8b769e4")` to THIS branch's merge of `e022fc935b2d`,
#: and that is wrong. Measured, three different trees, three different pins:
#:   * `main` at `e022fc935b`                        → (11838, "de5664dc98549d56")
#:   * THIS branch merged #14100/#14108 (`0d2dafed1d`) → (11839, "7159a040b69431a9")
#:   * `main` today, `e0accb8fe6`                     → (11844, "70d6cfdcd8b769e4"), `--check` exits 0
#: 11844 is `main`'s OWN pin, re-cut by a DIFFERENT lane's merge of the same `main` commit
#: (`8ed8d86291`, `feat/13971-point-strip`) — it is not this branch's measurement, and the
#: `11845 − 1 = 11844` "cross-check" that used to sit here was reconciling two unrelated trees.
#: That the two merge trees differ at all is this PR's own `seen.update` 102 → 101 (the `_capture`
#: helper now APPENDS instead of `update`ing, #14108), which is a row the #13971 lane does not carry.
#:
#: ⛔ AND THE "+8 IS FIGURE LITERALS" PARAGRAPH IS GONE, DELIBERATELY. This file's own block above
#: records that claim as FALSE — *"it said 'its whole diff is figure literals: stripping every digit
#: run from each `-`/`+` pair leaves the pair identical', and that is FALSE at this head: this PR
#: also rewrites `tests/test_13337_cache_graph_declaration.py`… so the digit-stripped `-`/`+`
#: multisets differ"*. Its present-tense tail was false too: "`main`'s own tree today derives 11833
#: against its own 11825 pin, i.e. `--check` is RED on `main`" — `origin/main` pins 11844 and
#: `--check` exits 0 there. A rebase splice had orphaned that paragraph's opening clause (the text
#: began mid-sentence at "run from each `-`/`+` pair…"), and an earlier attempt to repair it by
#: restoring the missing clause RE-ASSERTED the refuted universal. Deleting the paragraph is the
#: repair; restoring it is not. The #14062/#14058 recurrence it was about is real, and is argued in
#: the blocks above without the false premise.
#:
#: RE-MEASURED ON THE REBASED TREE (#14086 rebased onto `main` `e0accb8fe6`, then the review-round-3
#: commit `41672df4f9` landed on top): `--repin` on THAT tree printed **11846**. ⛔ NEITHER block
#: above was that tree's answer and neither was carried: `e0accb8fe6` COMMITS
#: `(11844, "70d6cfdcd8b769e4")` on `main` and the FIFTH merge tree measured 11839 — three different trees,
#: three different figures, NONE of which is this tree's (see the last block for the live pin).
#: ⛔ THE +2 IS ATTRIBUTED BY A ROW-BY-ROW DIFF, NOT BY ARITHMETIC. `--report --base 28ca66878d7c
#: --top 5000` was run in a worktree at `origin/main` (`e0accb8fe6`) AND on this tree, both row sets
#: sorted and diffed: EXACTLY TWO rows differ, and both are DECLARED rather than legitimised —
#:   * `captured.update` **30 → 31** — this PR's own analytics-capture stub in
#:     `tests/test_10893_pre_handler_parts.py` (the `lambda org, ev, props:
#:     captured.update(props=dict(props))` arm of its analytics-capture monkeypatch), a plain
#:     `dict.update`. ⛔ Cited by TEXT, not by line: this file's own rule above forbids a typed
#:     line number, because the reproducer goes stale the moment the file moves (`#14030` review).
#:   * `ha_mod._make_sdk(namespace='registry')._.query` **10 → 11** — the review-round-3 pin
#:     `test_a_graph_bound_capture_skips_the_org_level_checkpoint_entirely` (`41672df4f9`), which
#:     lives in `tests/test_capture_loop_responsiveness.py` (⛔ NOT in the file named above, and
#:     `_make_sdk` appears nowhere in that one). The added occurrence is the test's FIXTURE: it
#:     seeds the `Graph` row the ownership probe requires, through the REGISTRY control plane
#:     (`ha_mod._make_sdk(namespace="registry")._get_registry().query(…)`) — not the org
#:     default-graph SDK. That SDK's non-resolution is proved by a monkeypatched `_org_sdk`
#:     RECORDER, which is a different line.
#: Neither is a typed reference to a swept name, so no typed call site is legitimised. The DIGEST
#: moves with the row set (`residue_digest` hashes each row's NAME with its COUNT), which is why it
#: cannot be carried from a tree whose row set is not this one. 11844 + 2 = 11846 is a CROSS-CHECK of
#: that measurement, never the evidence for it.
#: `--check` on this tree: `typed references still pointing at a public name: 0`.
#: ⛔ AND ONE MORE ROW ON THE MERGED TREE (#14086 review round 4): the COMPLEMENT of that pin —
#: `test_a_bound_DEFAULT_key_keeps_the_org_level_checkpoint` — issues the SAME registry probe
#: (`ha_mod._make_sdk(namespace='registry')._.query` 11 → 12), so `--repin` here prints
#: `(11847, '327c14afa2e43e7c')` — the pin at that round, NOT this tree's. Same class as the two
#: rows above: a test
#: double, not a typed reference to a swept name — no call site is legitimised by it.
#: ⛔ AND ONE MORE OF THE SAME SHAPE (#14086 fixer):
#: `test_the_graph_bound_default_resolve_rides_the_worker_not_the_loop` seeds the `Graph` row its
#: graph-bound capture needs through the SAME registry control plane, so
#: `ha_mod._make_sdk(namespace='registry')._.query` moves **12 → 13** and `--repin` prints
#: `(11848, '5e58f2032543c415')` — the pin at that round, NOT this tree's. Attributed by the
#: row-by-row diff this block
#: requires (`--report --base 28ca66878d7c --top 5000` run in a detached worktree at the pre-change
#: head `60bfeb1a55` AND on this tree, both row sets sorted and diffed): EXACTLY that one row differs,
#: so no shape was silently absorbed in the other direction and NO NEW SHAPE APPEARED. `hosted_api.py`
#: adds no receiver row at all (the guard's `_org_default_namespace` call is off-loaded, and the file's
#: own `.query(` occurrence count is unchanged). A test double again, not a typed reference to a swept
#: name — `typed references still pointing at a public name: 0`, so no call site is legitimised.
#: ⛔ RE-MEASURED, NOT INHERITED. A digest measured while a merge was half-resolved is a digest of
#: the WRONG tree: an intermediate measurement of this block read `ea69254fe59a30ab` with the SAME
#: count, because stale conflict markers were still embedded in `activation_scorecard.py` and added
#: rows. Removing them returned the set to 11847 rows / `327c14afa2e43e7c` — this is the value
#: `--repin` prints on the RESOLVED tree, and the sibling commit that reverted the inert `dispatch`
#: note is already accounted for in it. That was the pin at that round; the live pin is the
#: `SWEEP_RESIDUE` line itself, and the last paragraph above it is the one that describes it.
#: MERGED `origin/main` (`e022fc935b2d`, #14100/#14108) — both sides' histories above are kept, and the
#: pin is RE-MEASURED on THIS tree with `--repin`, never combined arithmetically: it prints
#: `(11844, "70d6cfdcd8b769e4")` and `--check` exits 0 with `typed references still pointing at a
#: public name: 0`. The row composition is read off `--report --base 28ca66878d… --top 5000` AT THIS
#: HEAD, not inferred: `proj.g.query` 3642 and `g.query` 1492 are this PR's own rows, unchanged by the
#: merge, and `seen.update` is **101** — `main`'s −1, which is `tests/test_13337_cache_graph_declaration.py`
#: accumulating by APPEND rather than `update` (#14108), a file this branch's DIFF does not touch. That the
#: figure lands at 11845 − 1 = 11844 is a CROSS-CHECK of the measurement, not the evidence for it.
#:
#: RE-PINNED DOWN by #14125 → (11842, "e1caacfa6167aa7f") — the #13899-fallout sweep. The −2 is
#: THIS LANE's own and it is the harmless direction (a residue count that FALLS cannot hide a new
#: untyped reference), but it is named rather than absorbed:
#:   * `create_point (unexpected span)` 2 → 0. Both rows were
#:     `tools/measure_13436_write_costs.py`'s two arm-2 "control" shapes, which still called the
#:     RETIRED public `sdk.create_point(…)` — the arm could not run at all, because `__getattr__`
#:     refuses the name. Re-pointed to the private seam `sdk._create_point(…)`, matching the
#:     `point_ns` rows beside them, which is also what removed the two rows: the attribute is no
#:     longer in the rename map, so it is no longer counted as residue.
#: ⛔ ATTRIBUTED BY MEASUREMENT, not arithmetic: the row multisets were derived on `origin/main`
#: (`e0accb8fe6`, a detached worktree) and on this tree with the SAME walk, then diffed — that one
#: row, 2 → 0, is the ONLY difference, and 11844 − 2 = 11842 is a cross-check of the measurement,
#: never its evidence. Both moves are downward, and `typed references still pointing at a public
#: name: 0` on this tree, so no SDK-typed reference was legitimised by the re-cut.
#: RE-CUT DOWN (11844, "70d6cfdcd8b769e4") → (11833, "4358280454f50b14") by the SEED-SURFACE DOOR
#: RENAME (`tortoise/onboarding/selfhost.py`, `tortoise/hosted_api.py`, `tortoise/onboarding/seed.py`,
#: #13971): the two duck-typed seed surfaces published `create_subject`, which is a RETIRED SDK name
#: (`RETIRED_SDK_METHODS`, #12901), so `seed._write_anchor` calling it red the retired-name census
#: (`test_11609_write_containers.py`) on a CORRECT call — the census is receiver-blind and its
#: exemption table refuses a parameter receiver by design (#13863 P1), so the ambiguity is removed
#: from the product instead: the door is now `create_subject_anchor`. Three residue rows go with it,
#: and they are the whole delta: `surface.create_subject` 7, `org.create_subject` 2,
#: `selfhost.create_subject` 2 — **−11**, measured on both trees with
#: `--report --base 28ca66878d… --top 5000` and diffed (the parent head carries those rows; this head
#: carries none). No other row differs, so the `query` population this counter exists for is untouched
#: (`typed references still pointing at a public name: 0`). `create_subject_anchor` is NOT a swept
#: name, so the renamed call sites stop being residue at all — they are not "guessed at", they are
#: simply not references to a privatised method. Re-measured with `--repin` on THIS tree.
#: because BOTH facts are true of THIS tree: this branch's −2 (the two arm-2 `create_point` controls
#: re-pointed at `sdk._create_point`) AND `main`'s −11 (the seed-surface door rename). ⛔ Neither
#: block's pin is this tree's pin — each was measured on ITS OWN tree, and a merged tree is a THIRD
#: tree — so the pair below was RE-DERIVED with `--repin` on the MERGE tree, which is the rule both
#: blocks cite. (11842 − 11 = 11831 would be the arithmetic guess; the merged tree's `--repin`
#: printed exactly that count — the two row sets are DISJOINT, which the arithmetic cannot know and
#: is a cross-check of the measurement, never its evidence. The digest is the field that proves the
#: tree: it differs from BOTH sides' digests, as a third tree's must.)
#: `--check` exits 0 on the merged tree with `typed references still pointing at a public name: 0`.
#: ── RE-CUT AT THE SIXTH MERGE TREE (`#13998` × `main` `ba0eab1d86`, folded at `eec86df4c23f`)
#: ⛔ The `main` sha named here is the one this tree FOLDED, not the `e0accb8fe6` that appears in the
#: blocks above: `e0accb8fe6` is `origin/main`'s own tree at `4ae073f96e`, which prints (11845,
#: '1797f10f33f3441c'), while the fold of `ba0eab1d86` — `eec86df4c23f`, and its `4688df03b365`
#: follow-up — is the tree that prints the pair below (#14038 review, P3: an earlier cut of this
#: header named `e0accb8fe6` and then typed a pair that tree does not carry).
#: ⛔ BOTH SIDES' RECORDS ARE KEPT VERBATIM ABOVE — every block is true of the tree it names, and a
#: reader re-deriving this pin needs the whole chain. There is exactly ONE assignment after them, and
#: it is MEASURED on this merged tree with `--repin`, never computed from either parent's pair: the
#: tree prints (11842, '8978dc5cca557200'). ⛔ This merge is the SIXTH tree's (11853,
#: '6fd7431f4665e801') LESS the 11 rows `main`'s seed-surface door rename removes — the three rows the
#: block above names (`surface.create_subject` 7, `org.create_subject` 2, `selfhost.create_subject` 2)
#: — and nothing else: the −11 is the attribution `--report` gives, not the direction of a delta. `4ae073f96e` (the branch's later
#: merge on the same row) printed (11845, '1797f10f33f3441c'), and that pair is RECORDED here rather than carried: later commits changed this file, so this sixth
#: tree is a different tree and its pair was taken with `--repin` again instead of adjusted.
#: Neither parent carries it — a merged tree is a THIRD tree,
#: and the digest is what proves it. `--check` on this tree: exit 0, `typed references still pointing
#: at a public name: 0`. ⛔ NO ROW IS ATTRIBUTED BY THE DIRECTION OF THE DELTA: a re-pin absorbs
#: whatever shape the tree holds in EITHER direction (#14003 review, round 3, P3 / #14030), so the
#: count is read from `--report` against `SWEEP_BASE`, not inferred from the difference.
#: ── RE-CUT AT THE SEVENTH MERGE TREE (`#13998` × `main` `05d218fcaf`) ──────────────
#: ⛔ BOTH SIDES' RECORDS ARE KEPT VERBATIM ABOVE — each is true of the tree it names, and a merged
#: tree is a THIRD tree, so this pair is RE-DERIVED with `--repin` on the merge tree, never combined
#: from either parent's: it prints **(11840, '49b8ac930529bbf6')** — neither 11842 (this branch's
#: sixth tree) nor 11831 (`main`'s own merge tree), and its digest equals neither side's, which is
#: the field that proves a third tree. ⛔ NO ROW-BY-ROW ATTRIBUTION IS TYPED HERE: the difference
#: against `main` was not diffed row for row for THIS merge, so the two numbers above are stated as
#: what they are — two other trees' pins — and not as a decomposition of this one.
#: `--check` on this tree: exit 0, `typed references still pointing at a public name: 0`.
#: ── RE-CUT AT THE EIGHTH MERGE TREE (`#13998` × `main` `2c0d66e2ec`) ───────────────
#: ⛔ BOTH SIDES' RECORDS ARE KEPT VERBATIM ABOVE — each is true of the tree it names, and a merged
#: tree is a THIRD tree, so this pair is RE-DERIVED with `--repin` on the merge tree, never combined
#: from either parent's: it prints **(11844, '544521652dca0209')**. The two parents' own pairs are
#: (11840, '49b8ac930529bbf6') (this branch's seventh merge tree) and (11844, "70d6cfdcd8b769e4")
#: (`main` at `2c0d66e2ec`, the pair its OWN copy of this file pins). The COUNTS coincide — 11844
#: on both — and that is exactly why the digest is the field that decides: this tree's digest equals
#: neither parent's, so it is a third tree and not either side carried forward. ⛔ NO ROW-BY-ROW
#: ATTRIBUTION IS TYPED HERE: the difference against `main` was not diffed row for row for THIS
#: merge, so the two numbers above are stated as what they are — two other trees' pins — and not as
#: a decomposition of this one.
#: `--check` on this tree: exit 0, `typed references still pointing at a public name: 0`.
#: ── RE-CUT AT THE NINTH MERGE TREE (`#13998` × `main` `69908d006b`) ──────────────
#: ⛔ BOTH SIDES' RECORDS ARE KEPT VERBATIM ABOVE — each is true of the tree it names, and a merged
#: tree is a THIRD tree, so this pair is RE-DERIVED with `--repin` on the merge tree, never combined
#: from either parent's: it prints **(11852, '52e4a15116d6f32b')**. The two parents' own pairs are
#: (11844, '544521652dca0209') (this branch's eighth merge tree) and (11835, "891187f5a4636487")
#: (`main` at `69908d006b`, the pair its OWN copy of this file pins). Neither the count nor the
#: digest is either parent's, which is the field that proves a third tree rather than a sum. ⛔ NO
#: ROW-BY-ROW ATTRIBUTION IS TYPED HERE: the difference against `main` was not diffed row for row for
#: THIS merge, so the two numbers above are stated as what they are — two other trees' pins — and
#: not as a decomposition of this one.
#: ⛔ THIS RE-CUT WAS MEASURED TWICE, INDEPENDENTLY, AND THE PAIR AGREED. Two lanes re-cut this same
#: merge tree without seeing each other's pin (`--repin` returned 11852 / `52e4a15116d6f32b` both
#: times), which is the corroboration one measurement cannot supply. What the second lane adds is a
#: CORRECTION to a review that reported this delta: the +8 was attributed to four sites
#: (`tools/graphevent_home.py` ×3 plus `tests/test_13094_graphevent_home.py:251`), but
#: `docs/evidence/13094-graphevent/measure_graphevent_residency.py` carries the other four (`:122`,
#: `:141`, `:147`, `:154`). The count was right and the attribution in that finding was incomplete —
#: recorded, not adopted, and still not promoted into a row decomposition this pin does not claim.
#: `--check` on this tree: exit 0, `typed references still pointing at a public name: 0`.
#:
#: RE-CUT → (11843, "077a6f82c7284536") by the `#14012` lane ("keep this resident" + "see what's
#: outside"). The +12 is THIS lane's own and it is ATTRIBUTED PER FILE, not by arithmetic on the
#: pin: the residue rows were derived on the merge parent and on this tree with the tool's own
#: `edits_for` walk, and the five files this branch ADDS account for the whole delta —
#: `tortoise/outside_view.py` +2 (`graph.query`), `tortoise/resident_pin.py` +3 (`graph.query`), and
#: the three hermetic suites: `tests/test_14012_outside_view.py` +2 (`self.query`, `super().query` —
#: the fake graph's doors, mirroring the shape they stand in for),
#: `tests/test_14012_resident_pin.py` +4 (`self.query`, `super().query`, `props.update`, and
#: `self.meta.setdefault(params['key'], {}).update` — a dict accumulator in the fake), and
#: `tests/test_14012_resident_cli.py` +1 (the same `setdefault(...).update` accumulator). Every row
#: is an ordinary-word receiver on an object no typing can decide, which is exactly what this
#: counter exists to report; `typed references still pointing at a public name` stays **0**, so no
#: typed site was added and the SDK population is untouched. ⛔ The `graph.query` rows are NOT a new
#: shape — `graph.query` was already a residue row on `main` (281 occurrences there); these files
#: increase its count, and the digest moves because the row's COUNT moves. That the figure lands at
#: 11831 + 12 = 11843 is a CROSS-CHECK of the measurement, not the evidence for it: the pair below
#: is what `--repin` prints on THIS tree, and it is re-derived whenever the branch grows a file (the
#: CLI suite landed after the first re-cut and moved this pair by exactly its own +1).
#: RE-CUT → (11844, "066890c3a3b0837a") by the SAME `#14012` lane, one review round later, and the
#: +1 is ATTRIBUTED rather than inferred: measured by restoring ONLY
#: `tests/test_14012_outside_view.py` to the previous head's version and re-running `--repin`
#: (11843, `077a6f82c7284536`), then with this tree's version (11844, the digest below) — every other
#: file in that round is unchanged in its contribution. The row is the review round's `_NoCount`
#: fake in `tests/test_14012_outside_view.py`: `def query(...)` + `return super().query(...)`, i.e.
#: the `super().query` residue shape the suite already used, one more time. It is a fake standing in
#: for the door it exercises, so it is reported rather than avoided — and `typed references still
#: pointing at a public name` stays **0**, so no typed site moved and the SDK population is untouched.
#:
#: ⛔ MERGED `origin/main` at `ba0eab1d86` — measured on THAT merge tree with `--repin`, never
#: combined from the two sides' figures: it printed `(11837, "59e8f90d787a0cf8")`. `--report --base 28ca66878d… --top 5000` reads "UNTYPED receivers NOT swept
#: (the visible edge): 11837", `proj.g.query` 3642 and `g.query` 1492 — this branch's OWN rows,
#: carried unchanged through the merge — and NO `create_subject` residue row anywhere (the rename
#: map still lists `create_subject → _create_subject`, which is the map, not a residue), so the 11
#: rows `main`'s seed-surface door rename removed are the whole delta from this branch's pre-merge
#: 11848. `11848 − 11 = 11837` reconciles the two; it is a CROSS-CHECK of the measurements, never
#: the evidence for the pin.
#:
#: ⛔ MERGED `origin/main` AGAIN, at `05d218fcaf` (the base had moved past `ba0eab1d86`): THIS is
#: the pin, and it is `--repin`'s output on THIS tree — `(11835, "891187f5a4636487")` — never the
#: previous merge's 11837 carried forward. ⛔ THE −2 IS ATTRIBUTED BY A ROW-BY-ROW DIFF, not by
#: arithmetic: `--report --base 28ca66878d… --top 5000` was run in a detached worktree at the
#: pre-merge head `b18b89e7` AND on this tree, both row sets sorted and diffed, and EXACTLY ONE row
#: differs — `create_point (unexpected span)` **2 → 0**. That row is `main`'s own between
#: `ba0eab1d86` and `05d218fcaf` — **#14129**, which re-pointed exactly those two arm-2 controls
#: (`tools/measure_13436_write_costs.py`, `sdk.create_point(` → `sdk._create_point(`) — not this
#: branch's: this branch adds no row here and removes none. No other row differs, so no shape
#: was absorbed in the other direction. `11837 − 2 = 11835` is a CROSS-CHECK of that measurement,
#: never its evidence. `--check` exits 0 on this tree:
#: `typed references still pointing at a public name: 0`.
#:
#: ⛔ RE-PINNED FROM `main`'s DRIFT, not by a change on this branch: `f9f480a55ab3` (#14164, the
#: #13094 change-feed reader census + measured residency) added untyped `Graph` receivers and
#: re-pinned nothing, so `--check` reds on `origin/main` `69908d006b3d` itself — measured with
#: `--repin` in a detached worktree at that commit: `(11843, "6aed5368ffa10513")`, the SAME pair
#: this branch's tree carried at `72c104bcbb`, so the red was the BASE tree's and not the change's
#: (its diff is `docs/infra-runbook.md` + `tests/test_fly_secret_drift.py` and adds no row).
#: THE +8 IS ATTRIBUTED BY MEASUREMENT, NOT ARITHMETIC: `--repin` was run in one detached worktree
#: at `2c0d66e2ec07` (the previous pin's commit) → `(11835, "891187f5a4636487")`, at
#: `dc92b0eecc15` → the same, and at `f9f480a55ab3` → `(11843, "6aed5368ffa10513")`, so exactly
#: ONE commit moved it; and `--report --base 28ca66878d… --top 5000` was run at the pre-move commit
#: AND at `f9f480a55ab3`, both row sets sorted and diffed — EXACTLY TWO rows differ,
#: `g.query` 1492 → 1499 and `proj.g.query` 3642 → 3643, with the row SHAPE set unchanged on both
#: sides (858 rows). Both are FalkorDB `Graph` receivers, which is this sweep's documented edge (a
#: `Graph` is never the SDK), so no shape was absorbed and no defect is hidden — the direction the
#: pin exists to catch is a NEW shape, and there is none. `--check` exits 0 on this tree after the
#: re-pin, with `typed references still pointing at a public name: 0`.
#: ── RE-CUT AT THE TENTH MERGE TREE (`#13998` × `main` `72c104bcbb`) ───────────────
#: ⛔ BOTH SIDES' RECORDS ARE KEPT VERBATIM ABOVE — every block is true of the tree it names, and a
#: reader re-deriving this pin needs the whole chain. Neither side's figure is combined into this
#: one: THE PAIR THIS RECORD PRINTS BELOW is `--repin`'s output on the tree this block NAMES
#: (`#13998` × `main` `72c104bcbb`, the tenth merge tree), and nothing else.
#: It prints **(11852, '52e4a15116d6f32b')** — the SAME pair as the ninth merge tree, and that is a
#: measurement rather than a carry: the digest is over the whole `row -> count` multiset, so an
#: identical digest is an identical residue SET, which says `main`'s `69908d006b..72c104bcbb` commits
#: (the `reap-redislite-orphans` rewrite, the platform-gated node-id list, the `.env`/runbook edits)
#: add and remove NO untyped-receiver row. `--check` exits 0 on this tree.
#:
#: ⛔ RE-PINNED FROM `main`'s DRIFT, not by a change on this branch: `f9f480a55ab3` (#14164, the
#: #13094 change-feed reader census + measured residency) added untyped `Graph` receivers and
#: re-pinned nothing, so `--check` reds on `origin/main` `69908d006b3d` itself — measured with
#: `--repin` in a detached worktree at that commit: `(11843, "6aed5368ffa10513")`, the SAME pair
#: this branch's tree carried at `72c104bcbb`, so the red was the BASE tree's and not the change's
#: (that commit's diff is `docs/infra-runbook.md` + `tests/test_fly_secret_drift.py` and adds no row).
#: ⛔ NO TREE ON THIS BRANCH PRINTS IT ANY MORE — the merges with `main` re-measured the pair, and the
#: block at the END of this run carries the reading that is current: read that one for the live pin.
#: THE +8 IS ATTRIBUTED BY MEASUREMENT, NOT ARITHMETIC: `--repin` was run in one detached worktree
#: at `2c0d66e2ec07` (the previous pin's commit) → `(11835, "891187f5a4636487")`, at
#: `dc92b0eecc15` → the same, and at `f9f480a55ab3` → `(11843, "6aed5368ffa10513")`, so exactly
#: ONE commit moved it; and `--report --base 28ca66878d… --top 5000` was run at the pre-move commit
#: AND at `f9f480a55ab3`, both row sets sorted and diffed — EXACTLY TWO rows differ,
#: `g.query` 1492 → 1499 and `proj.g.query` 3642 → 3643, with the row SHAPE set unchanged on both
#: sides (858 rows). Both are FalkorDB `Graph` receivers, which is this sweep's documented edge (a
#: `Graph` is never the SDK), so no shape was absorbed and no defect is hidden — the direction the
#: pin exists to catch is a NEW shape, and there is none. `--check` exits 0 on this tree after the
#: re-pin, with `typed references still pointing at a public name: 0`.
#:
#: ⛔ RE-PINNED FROM `main`'s DRIFT AGAIN. `SWEEP_RESIDUE` moved repeatedly on 2026-11-21 — count it
#: yourself with `git log -G'^SWEEP_RESIDUE = ' -- tools/sdk_privatise.py`, and bucket it on ONE clock,
#: because the total depends on which timezone window you pick (the same day reads differently on UTC
#: and on -06:00) and `git log -L` on this line TRUNCATES the chain — measured on `origin/main`, it
#: stops at `2c0d66e2ec` — so a numeral quoted here is a claim about the instrument, not about the
#: drift. This paragraph therefore quotes none. What the day does measure is not thrift: twice at
#: least, the census moved in a commit that re-pinned NOTHING and the recovery landed later, in a
#: commit about something else. `f9f480a55ab3` (#14164) moved it and `427c418ebb` (#14188) re-pinned
#: afterwards; `67dc298ec3` (#14194) with `1b0db04894` (#14162) moved it and THIS commit re-pins.
#: That is the shape of the problem rather than a coincidence: `SWEEP_RESIDUE` is a WHOLE-TREE
#: census, so any commit that adds or retires an untyped receiver anywhere moves it, while the
#: commits that do so have no reason to touch this file. At `1b0db04894` — this branch's own tree,
#: before the merges — `--repin` prints `(11840, "b7eb08ff3acdcce4")`, measured, never carried
#: forward by arithmetic; `72c104bcbb` COMMITS `(11843, "6aed5368ffa10513")` and its tree still
#: satisfies that pin — the row map re-derived there totals 11843 — so the move happened
#: strictly between those two commits.
#: THE −3 IS ATTRIBUTED BY A ROW-BY-ROW DIFF, not by arithmetic: the residue map was re-derived on
#: BOTH heads from this file's own `edits_for`/`rename_map` (not by grep over the source, which
#: over-counts receivers that appear inside comments and string literals), and EXACTLY SIX rows
#: differ, with the row SHAPE set going 858 → 855:
#:   `67dc298ec3` (#14194) REWROTE `tests/test_read_ledger.py` — it is the ONLY commit in the range
#:   that touches that file, and it retires four `inst.*` rows: `inst.query` 3 → 0,
#:   `inst.get_point` 1 → 0, `inst.paginated_query` 1 → 0, `inst.query_points_by_tag` 1 → 0, i.e.
#:   the six removed `inst.*` source lines = −6;
#:   `1b0db04894` (#14162) is the only commit in the range that touches `tools/miss_rate_m5.py` or
#:   its test, and it GROWS the `cur['proj'].g.query` ROW 2 → 4 tree-wide (+2; that file adds two
#:   of the four, `tools/window_quality_m2.py` holds the other two) and adds one NEW row,
#:   `terms.update` 0 → 1 in `tests/test_miss_rate_m5.py` (+1), i.e. +3; net −3 → `11843 − 3`.
#: The ONE new shape is DECLARED rather than waved through: `terms` at that site
#: (`_pp_terms`, `tests/test_miss_rate_m5.py`) is a plain `set[str]` built by `re.findall`, so
#: `.update` there is the ordinary-word receiver this sweep deliberately leaves alone — it is
#: neither an SDK handle nor a FalkorDB `Graph`. `typed references still pointing at a public
#: name: 0` on BOTH heads, so no public name was absorbed in either direction and no defect is
#: hidden: the four retired `inst.*` rows are that test rewrite's own deletions, not an escape,
#: and the direction the pin exists to catch — the SET moving — is what this re-pin declares.
#:
#: ⛔ MERGED `origin/main` (`2c0d66e2ec`) INTO THIS BRANCH — the resolution of the ONE conflict this
#: merge had. Both sides had edited ONLY this pin line (verified: with the `#:` blocks stripped, the
#: three-way diff is a single `SWEEP_RESIDUE` replacement on each side and nothing else), so the
#: resolution is not "pick a side" — EVERY block above is carried, because each is a true
#: measurement of the tree it NAMES, and the pin is RE-MEASURED on the MERGED tree with `--repin`,
#: which is what all three blocks' own prose requires of a third tree. It prints
#: `(11847, "ef7d5433fc80acd7")` and `--check` exits 0 with `typed references still pointing at a
#: public name: 0`. ⛔ NEITHER 11843 (this branch's own tree) NOR 11835 (`main`'s) is carried.
#: ⛔ THE +4 IS ATTRIBUTED BY A ROW-BY-ROW DIFF, not by arithmetic: `--report --base 28ca66878d…
#: --top 5000` was run in a detached worktree at the pre-merge head `aed338ab` AND on this tree, both
#: row sets sorted and diffed, and EXACTLY TWO rows differ, both `main`'s and neither this branch's:
#:   * `ha_mod._make_sdk(namespace='registry')._.query` **10 → 13** — three occurrences `main` added to
#:     `tests/test_capture_loop_responsiveness.py` (that file carries 1 → 4 of the shape). Test-fixture
#:     registry probes, not typed references to a swept name.
#:   * `captured.update` **30 → 31** — one occurrence in `tests/test_10893_pre_handler_parts.py`, a
#:     file `main` ADDS (0 → 1 there). A plain `dict.update` in an analytics-capture stub.
#: No other row differs, so no shape was absorbed in the other direction and this branch's +12 (the
#: block above, attributed per file there) is intact and unchanged by the merge. `11843 + 4 = 11847`
#: and `11835 + 12 = 11847` both land on the pin, as CROSS-CHECKS of the two measurements, never as
#: their evidence.
#:
#: ⛔ CARRIED FORWARD THROUGH A CONCURRENT-LANE MERGE — and this is the pin, re-measured on THIS
#: tree with `--repin`, never combined from the two sides: `(11848, "91e7c7d82018c838")`. TWO lanes
#: were fixing this PR at once (one on review round 1, one on round 2), so the branch is a merge of
#: two branches off the same head, and a merge of two measured trees is a THIRD tree — the case all
#: the blocks above say must be re-measured rather than inherited. ⛔ NEITHER 11844 (round 1's tree)
#: NOR 11847 (round 2's tree, already merged with `main` `2c0d66e2ec`) is carried.
#: ⛔ THE +1 IS ATTRIBUTED IN THE BLOCK DIRECTLY ABOVE (round 1's `_NoCount` fake in
#: `tests/test_14012_outside_view.py`) and THE +4 IN THE BLOCK DIRECTLY BELOW (`main`'s two row
#: moves); both were measured against their own pre-merge head, so the merged figure is the sum of
#: two MEASURED deltas, and `11843 + 1 + 4 = 11848` is a CROSS-CHECK of the `--repin` run, never its
#: evidence. `--check` exits 0 on this tree with
#: `typed references still pointing at a public name: 0`.
#: ⛔ MERGED `origin/main` (`69908d006b`) INTO THIS BRANCH, one merge after the block above — and
#: the pin is RE-MEASURED on THIS tree with `--repin`, never carried: `(11856, "e9249c5490bc38d7")`.
#: ⛔ THE +8 IS ATTRIBUTED BY THE TOOL'S OWN ROW DIFF, not by arithmetic: `--report --base
#: 28ca66878d… --top 5000` was run in a detached worktree at the pre-merge head `aaf341542b` AND on
#: this tree, both row sets sorted and diffed, and EXACTLY TWO rows differ, both `main`'s and neither
#: this branch's:
#:   * `g.query` **1492 → 1499** (+7)
#:   * `proj.g.query` **3642 → 3643** (+1)
#: No other row differs, so no shape was absorbed in the other direction. The occurrences arrive with
#: `main`'s three commits (`69908d006b`, `f9f480a55a`, `dc92b0eecc`) and land in files this branch
#: never touches — `tools/graphevent_home.py` (0→4 `g.query(`), `tests/test_14125_retired_name_calls.py`
#: (4→8), `tests/test_13094_graphevent_home.py` (0→1) and `docs/evidence/13094-graphevent/` (0→5 plus
#: one in `readers.json`). `11848 + 8 = 11856` is a CROSS-CHECK of the two measurements, never their
#: evidence. `--check` exits 0 on this tree:
#: `typed references still pointing at a public name: 0`.
#:
#: RE-CUT → (11857, "28611b0c960d0070") by the SAME lane's review rounds 3-4, and the +1 is
#: ATTRIBUTED by the tool's own row diff (`--report --base 28ca66878d7c --top 5000` on both
#: trees, sorted and diffed): EXACTLY ONE row differs — `super().query` 71 → 72, the
#: `_PartiallyKeyed` fake in `tests/test_14012_outside_view.py` delegating to its parent for
#: the statements it does not answer. An ordinary-word receiver on an object no typing can
#: decide, which is what this counter reports; `typed references still pointing at a public
#: name` stays **0**, so no SDK-typed site is legitimised.
#:
#: RE-CUT → (11859, "a32f1e6cc360070c") by the merge of the concurrent lane's round-6 fixes into
#: this lane's round-5 ones. The +2 is ATTRIBUTED by the tool's own row diff (`--report --base
#: 28ca66878d7c --top 5000` on both trees, sorted and diffed): EXACTLY ONE row differs —
#: `super().query` 72 → 74, two more fakes delegating to their parent in
#: `tests/test_14012_resident_cli.py` / `tests/test_14012_resident_pin.py` from the two sides'
#: tests. An ordinary-word receiver on an object no typing can decide, which is what this counter
#: reports; `typed references still pointing at a public name` stays **0**, so no SDK-typed site is
#: legitimised.
#:
#: ⛔ MERGED `origin/main` `1b0db0489408` — a FIFTH tree, so the pair is `--repin`'s output on THIS
#: tree and neither side's figure is carried. ⛔ AND THE BASE'S OWN PIN IS STALE AGAIN, which is why
#: the delta is taken against a MEASUREMENT: `--check` on a detached worktree of `1b0db0489408`
#: reports **11840** against the 11843 that tree pins. The +16 over that measured 11840 is THIS
#: branch's own, ATTRIBUTED ROW BY ROW (`--report --base 28ca66878d7c --top 5000` on both trees,
#: both row sets sorted and diffed): `graph.query` 276 → 281 (+5; two reads in
#: `tortoise/outside_view.py`, three in `tortoise/resident_pin.py`), `super().query` 68 → 74 (+6),
#: `self.query` 15 → 17 (+2), `self.meta.setdefault(params['key'], …).update` 0 → 2 (+2) and
#: `props.update` 14 → 15 (+1) — the last four across the three hermetic suites and the two sides'
#: review-round fakes. Those five rows are the whole delta, so no shape was absorbed in the other
#: direction. Every row is an ordinary-word receiver on an object no typing can decide, which is
#: what this counter exists to report; `typed references still pointing at a public name` stays
#: **0**, so no SDK-typed site is legitimised.
#:
#: RE-CUT → (11857, "ed40203dbbf32b87") by this lane's round-7 rows, ON THE MERGED TREE (a sixth
#: tree, so the pair is `--repin`'s own output and neither side's figure is carried). The +1 over the
#: 11856 this block pinned is ATTRIBUTED by the tool's row diff: exactly one row moves —
#: `super().query` 74 → 75, the `_EventGraph` fake in `tests/test_14012_outside_view.py` delegating to
#: its parent for the statements it does not answer (the end-to-end per-label timestamp row).
#: `typed references still pointing at a public name` stays **0**.
#:
#: ⛔ MERGED `origin/main` (`e74cf1d13d`, the `#14012` lane's "keep this resident" + "see what's
#: outside") INTO THIS BRANCH — and THIS branch's own census contribution is ZERO ROWS, so the
#: merged tree's pair is MEASURED on the RESOLVED tree and it coincides with `main`'s own: `--repin`
#: here prints `(11857, "ed40203dbbf32b87")`, the SAME pair `main` pins at `e74cf1d13d`. ⛔ THE
#: COINCIDENCE IS NOT THE EVIDENCE — the evidence is that `--repin` run. What makes it expected
#: rather than lucky is MEASURED too, not assumed: `git diff origin/main` on the resolved tree
#: touches exactly TWO files, and every line it adds is either `#:`-prefixed prose or the single
#: docstring figure in `tests/test_sdk_privatise_sweep.py` — not one executable line. `edits_for`
#: walks the AST and cannot see a comment, and a docstring is a `Constant`, not an `Attribute`, so
#: this branch can move no row in either direction, which is why the two readings agree.
#: outside") INTO THIS BRANCH. ⛔ ITS "ZERO ROWS" PREMISE IS `main`'s OWN and NOT THIS BRANCH'S, and
#: leaving it unscoped would make the log contradict itself against the +3 block below (whose target
#: sentence this branch has since DELETED, so a line-distance here would not reproduce): on `main`'s tree the
#: diff against this branch adds no executable line, but THIS branch's end-proof adds THREE untyped
#: receivers (`g.query` ×2 in `tools/graphevent_home.py`, `super().query` ×1 in the test stub), which
#: is why `--repin` on the tree this branch resolved prints `(11860, "267dce26b4191743")` and not
#: what `e74cf1d13d` COMMITS `(11857, "ed40203dbbf32b87")` on `main`. Both readings are
#: measurements of
#: different trees; read the block at the END for the live pin. ⛔ THE "NO EXECUTABLE LINE" REASONING
#: THAT ONCE STOOD HERE IS DELETED, not reworded: it argued the resolved tree's diff against
#: `origin/main` touched "exactly TWO files" and added "not one executable line", so "this branch
#: can move no row in either direction, which is why the two readings agree". Measured at the tree
#: this branch actually resolved: `git diff --numstat origin/main HEAD` is FIFTEEN files and
#: `tools/graphevent_home.py` alone is 215/47 — executable, and the two readings do NOT agree (11860
#: against 11857). ⛔ A REASON REFUTED BY ITS OWN NUMBERS IS WORSE THAN NO REASON, and it is the
#: shape this run exists to police: the +3 attribution in the block below is the evidence, and a
#: reader who took the deleted sentence would have concluded the pin must NOT have moved.
#: ⛔ NEITHER SIDE'S FIGURE IS INHERITED BY ARITHMETIC: this branch's own tree `1b0db04894` measured
#: `(11840, "b7eb08ff3acdcce4")` (the block above), and the `#14012` block above independently
#: reports the SAME 11840 for that commit — "`--check` on a detached worktree of `1b0db0489408`
#: reports **11840** against the 11843 that tree pins" — so `main`'s +17 from there is `#14012`'s own
#: rows, attributed row by row in that block, not this branch's. `11840 → 11857` is a CROSS-CHECK of
#: two independent readings, never the derivation of this pin.
#: `--check` exits 0 on this tree with `typed references still pointing at a public name: 0`.
#:
#: ⛔ MERGED `origin/main` `e74cf1d13d` (`#14161` + `#14208`) — a SEVENTH tree, so the pair is
#: `--repin`'s output on THIS tree and NEITHER side's figure is carried: the 11857 in the block above
#: marked the CONCURRENT lane's tree, which this one is not. It prints
#: **(11866, '74d2e8bbd0314d5a')**, and `--check` exits 0 with
#: `typed references still pointing at a public name: 0`.
#: ⛔ THE +17 IS ATTRIBUTED BY THE TOOL'S OWN ROW DIFF over the ONLY files the merge changed — the
#: thirteen `.py` paths `git diff --name-only <pre-merge head> -- '*.py'` prints; every other file is
#: byte-identical on the two trees, so it contributes the same rows and cannot be hiding a shape.
#: EXACTLY FIVE rows differ, and all five are `main`'s, none this branch's:
#:   * `super().query` 0 → 7 (+7) — fakes in `tests/test_14012_outside_view.py` (5) and
#:     `tests/test_14012_resident_pin.py` (2) delegating to their parent
#:   * `graph.query` 2 → 7 (+5) — `tortoise/resident_pin.py` (3), `tools/graphevent_home.py` (3)
#:     replacing 5 the pre-merge tree carried elsewhere in the same set
#:   * `self.query` 0 → 2 (+2), `self.meta.setdefault(params['key'], {}).update` 0 → 2 (+2) and
#:     `props.update` 0 → 1 (+1) — the three `#14012` test files' new fakes
#: Every one is an ordinary-word receiver on an object no typing can decide, which is what this
#: counter exists to report; the +17 against the pre-merge tree's 11849 (11866 − 17, a CROSS-CHECK
#: of the row diff and never its evidence) is `main`'s, and no row was absorbed in the other
#: direction — this branch's own rows are the ones the blocks above already attribute per file.
#: ⛔ AN ERRATUM ON THE ERRATUM, MEASURED, because the correction above named the wrong tree and
#: then drew the wrong conclusion from it. `2c0d66e2ec` COMMITS `(11835, "891187f5a4636487")` and
#: that tree does measure it — both true, and both beside the point: the
#: illustration's claim is that a tree can PIN 11835 and MEASURE 11843, and the tree that does is
#: the MERGE `8889f1140105` — it commits `(11835, "891187f5a4636487")` inherited from `main` — and
#: `--repin` ON THAT TREE prints `11843` / `6aed5368ffa10513`, which its successor `27cd81d55ebd`
#: commits `(11843, "6aed5368ffa10513")`: it re-pinned to the pair its own tree measures. So the
#: example is a REAL instance of the rule, not "the wrong EXAMPLE"; what was wrong was reading its
#: pair against `2c0d66e2ec`, where pin and tree agree.
#: ⛔ BOTH PAIRS ARE GRADED, and the grading SURVIVES A MEASURED FETCH TEST. An earlier cut of this
#: re-cut concluded the opposite — that an `OWN_PINS` entry "would have to name a refspec that cannot
#: be fetched — a lie in the map" — and wrote the pairs in an ungraded spelling. THAT IS FALSE, and
#: it was falsified the cheap way: a sha WANT is answered for both revs from a clone holding neither
#: object — `git fetch --dry-run --depth=1 https://github.com/daniel-ospina/tortoise.git <sha>`
#: prints `* branch <sha> -> FETCH_HEAD` for `8889f1140105677add2c7c4dd60d0c8c5080ea26` and for
#: `27cd81d55ebd42948c27051d821df970bbc0ba2f`, while the NEGATIVE CONTROL — the same command with an
#: invented sha — prints `fatal: remote error: upload-pack: not our ref`. So "unreachable from any
#: advertised ref" (TRUE: `27cd81d55ebd` sits in no ref at all, `8889f1140105`'s only ref is a local
#: branch, and `refs/pull/14086/head` contains neither) is NOT the claim "not want-able by sha",
#: and `_ARM_REFSPECS` grades the print-a-usable-remedy property, not reachability. Both revs
#: therefore sit in `OWN_PINS` with the pair each commits, and in `_ARM_REFSPECS` as full-sha wants.
#: ⛔ THE ONE REAL CAVEAT IS DURABILITY, NOT FETCHABILITY: an unadvertised sha is subject to the
#: server's GC, so a re-arm can stop resolving where a branch-backed rev never would — a reason to
#: keep the pairs TRUE, never to leave them ungraded (review of PR 14183: the ungraded spelling WAS
#: the P2, and this block is the record of both attempts).
#: ⛔ AND THE HAZARD THAT MADE THE NARROWING LOOK NECESSARY, recorded because it is real and it
#: bit: the clause-attribution matcher (the `_BACKTICKED_REV_RE` regex, itself CUT with the B8
#: cut, 2026-11-23) matched ANY backticked hex token, so the 16-character DIGEST
#: `891187f5a4636487` was itself a candidate rev, and the binding rule was "the NEAREST
#: backticked token before the claim". That is why this sentence names `8889f1140105` immediately
#: before the word `commits`: written the other way round the claim binds to whatever hex sits
#: closest — the defect that once reddened that (now CUT, B8 2026-11-23) pair arm in the first
#: re-cut. Re-word the ORDER, never the map — nothing grades the order now, so it is the writer's
#: rule.
#: (Two lanes restored this block concurrently, which is why the rule is stated once, above.)
#: ⛔ AND THE GROUND RULE FOR EVERY BLOCK IN THIS RUN: all blocks above are KEPT, because each is a
#: true measurement of the tree it NAMES — including the ones no tree on this branch prints any more,
#: which is what a measurement log is for. A later merge re-measures the pin rather than deleting a
#: previous reading; if a block conflicts with the live pin, the block is HISTORY and the last one is
#: the PIN.
#: ⛔ RESTORED AT THE RE-CUT — the resolution that merged `origin/main` `9f83a63bfab7` into this
#: branch kept EVERY `main` block and dropped THREE OF THIS BRANCH'S OWN: the "SECOND cut" block,
#: the "RE-MEASURED AGAIN after merging `origin/main` `e74cf1d13da7`" block, and the "THE BASE
#: FIGURE IS THE BASE **TREE's** MEASUREMENT" block. That is the one thing this file's own rule
#: forbids — "EVERY block above is carried, because each is a true measurement of the tree it
#: NAMES" — and a merge that carries one side wholesale deletes the record of what THIS branch
#: measured and why: its +3 end-proof receivers, and the round-5 review pair (11835 committed vs
#: 11843 measured) those figures falsified. They are restored VERBATIM from `a2d29463`, ABOVE this
#: block, so no side's text is dropped here: main's is intact and this branch's is back.
#: ⛔ THE PIN IS RE-MEASURED ON THIS TREE, never carried: `--repin` prints
#: `(11860, "267dce26b4191743")` — unchanged from the pre-restoration pin, because every restored
#: line is `#:`-prose and `edits_for` walks the AST and cannot see a comment. `--check` exits 0
#: here with `typed references still pointing at a public name: 0`.
#:
#: ⛔ MERGED `origin/main` `9f83a63bfa` (the `docs` red at this PR's head: five commits, none of
#: them `tortoise/hosted_api.py` or `tortoise/sdk.py`) — and `--repin` on the MERGED tree prints
#: **(11866, '74d2e8bbd0314d5a')**, the SAME pair this line already carried. That is a measurement,
#: not a carry: the digest is over the whole `row -> count` multiset, so an identical digest is an
#: identical residue SET, and it says `main`'s `e74cf1d13d..9f83a63bfa` adds and removes NO untyped
#: receiver row. ⛔ DO NOT READ THAT INTERVAL AS TOUCHING ONLY DOCS — it is NOT the quiet range the
#: first draft of this note described (`#14209` review, P3): `9f83a63bfa` alone rewrote
#: `tests/_html_links.py` (+2,789/−28) and `tests/test_website_docs_consistency.py` (+5,003/−116),
#: which is exactly the size of test change a residue delta would come from — the NULL is the
#: MEASURED result of `--repin` over that churn, not an inference from a small diff. `--check` exits
#: 0 on this tree with `typed references still pointing at a public name: 0`. ⛔ So this merge
#: re-pins NOTHING, deliberately: the pin is not "refreshed" to show that a merge happened, and no
#: numeral above is re-cut to match a tree that did not move it.
#:
#: ⛔ FOLDED `origin/main` `10e51693f1` INTO THIS PR — an EIGHTH tree, so the pair is `--repin`'s
#: output on THIS tree and neither side's figure is carried: the conflicting 11866 above marked the
#: PRE-FOLD branch tree and `main`'s 11858 marked `main`'s OWN tree, which is not this one either.
#: `--repin` on the resolved tree prints **(11870, '6da40999bb5162fa')**, and `--check` exits 0 with
#: `typed references still pointing at a public name: 0`.
#: ⛔ ATTRIBUTED BY THE TOOL'S OWN ROW DIFF, not by arithmetic: `--report --base 28ca66878d7c
#: --top 5000` was run on this tree AND on a clean checkout of `origin/main` `10e51693f1`, and the
#: two sorted row sets differ in EXACTLY THREE shapes, +9 rows — `cp.query` 263 → 270 (+7),
#: `row.update` 59 → 60 (+1) and one NEW `self.plane.query` (+1, this branch's capacity-notice seam
#: and its fake, which is why it cannot be read as a `cp.query` row). No shape was absorbed in the
#: other direction, so nothing left by the fold went uncounted.
#: ⛔ AND THE +12 OVER `main`'s PIN IS NOT ALL THIS TREE'S: `main`'s OWN tree at `10e51693f1`
#: measures **11861** against the **11858** it pins (`--check` exits 1 THERE, on `main`, for that
#: reason and not for anything this PR does), so 11858 + 9 (this branch, measured) + 3 (`main`'s own
#: stale pin) = 11870. Neither side's numeral was carried into that fold's pin; it was
#: `--repin`'s print on THAT tree.
#: ⛔ RE-CUT → (11858, "a1552662bf07a10b"). The 11857 pair was NOT measured on this branch: it is
#: `main`'s own pin (`e74cf1d13d`), inherited here at the merge and left in place while this branch
#: kept adding rows under it, so `--check` reds on this tree by exactly ONE. `git log` proves the
#: inheritance rather than asserting it: the ONLY commit that ever set the constant to that pair is
#: `e74cf1d13d` itself, and that tree carries no `out.update` (measured: 0 occurrences in
#: `tests/test_14125_retired_name_calls.py`), so the pair was true of ITS tree and false of this one.
#: THE ONE ROW: `out.update` 1 — `_global_declarations` in `tests/test_14125_retired_name_calls.py`,
#: an `out: set[str]` accumulated with `out.update(node.names)` over an `ast.Global`. It arrived with
#: this branch's own `a4e97d6f5b`, which is why the block above can still claim this branch's census
#: contribution is "ZERO ROWS" and be wrong at this head — that reading was taken before the row
#: existed and was never re-measured, the recurrence this pin chain keeps recording.
#: ⛔ ATTRIBUTED BY MEASUREMENT, NOT ARITHMETIC: the residue counter was derived over the whole tree
#: and the SINGLE row removed; `11858 − out.update` prints `(11857, "ed40203dbbf32b87")` — the pinned
#: pair EXACTLY — so that one row is the whole delta and no other shape moved in either direction.
#: The row is DECLARED rather than rewritten away, as `main` declared `terms.update` 0 → 1:
#: a plain `set[str]` in a test helper is the ordinary-word receiver this sweep deliberately leaves
#: alone, neither an SDK handle nor a FalkorDB `Graph`, and `typed references still pointing at a
#: public name` stays **0**. Measured on the MERGED tree: this head has `origin/main` `9f83a63bfa`
#: merged in, and `--repin` printed the same pair before and after that merge, so the merge
#: contributes no row here either — nor do the sibling commits merged after it (all on this
#: branch's census file, and named by log rather than counted here, because a count restated in this
#: block goes stale at the next sibling push, the class `2465cda150` was written to stop). The
#: pin is RE-DERIVED after every integration rather than carried forward. ⛔ EVERY PAIR HERE IS
#: SCOPED TO THE TREE IT WAS MEASURED ON, and an earlier cut of this paragraph attributed one to
#: another (review of #14287, round 4, P1): `(11861, "f0647020531c078c")` is what `main` COMMITS
#: at `0467ba329d` and again at `47e28b549e76` (measured: `git show <rev>:tools/sdk_privatise.py`),
#: and it is the pair THIS BRANCH carried before its FIRST rebase (`561a53ec04a7` and the original
#: tip `f800a528acb9` both carry it) — so integrating `main` at those two commits re-measured the
#: SAME pair and added no untyped receiver. The FIRST rebase is what moved the pin, and it moved it
#: to `main`'s own pair: `--repin` on that rebased tree prints `(11864, "2a6400518a87775c")`, the
#: SAME pair `main` commits at `588cf7aeb61f`, so those three arrived with `main`'s commits. The
#: SECOND rebase onto `main` `cc936d47dba1` did NOT move it: `--repin` on the pre-rebase tip
#: `a7690edb9ce8` prints `(11864, "2a6400518a87775c")` and so does the rebased tree, and
#: `--check` exits 0 on each. ⛔ NOT EVERY REBASE MOVES IT, and asserting that it always does was
#: the fault of an earlier cut of this sentence (review of #14287, round 8, P2): "the REBASE … DID
#: move the pin" was TRUE of the first and FALSE of the second, and it named `f800a528acb9` as
#: "the pre-rebase tip" when that rev is the pre-rebase tip of the FIRST rebase, NOT an ancestor of
#: the tip it meant (measured: `git merge-base --is-ancestor f800a528acb9 a7690edb9ce8` exits 1).
#: ⛔ "NO INTEGRATION ADDED A
#: ROW" IS FALSE AS A GENERAL CLAIM: an integration CAN add untyped receivers, and re-cutting the
#: pin is exactly how that gets recorded — the block BELOW is one, where merging `10e51693` moved
#: the pair to `(11864, …)`, "FOUR MORE than this branch's parent". ⛔ AND THE EVIDENCE IS
#: `--repin`'s OUTPUT, NOT THE CHECK'S EXIT: `--check` exiting 0 says the observed pair equals the
#: pin, which it reports just as readily on a tree whose pin was re-cut after the integration
#: added rows.
#: ⛔ NAMED BY ITS COMMITS, NOT BY "this branch's base": the base moves under every integration
#: and a parenthetical reading `this branch's base` went stale at the very next merge (measured:
#: written naming `0467ba329d`, false the moment `47e28b549e76` was merged).
#: ⛔ MERGED `origin/main` `10e51693` INTO THIS BRANCH AND KEPT BOTH SIDES' BLOCKS ABOVE — each is a
#: true measurement of the tree it NAMES, which is the ground rule of this run, so neither side is
#: dropped to make the conflict go away. THE PIN IS THEN RE-MEASURED ON THE RESOLVED TREE, because
#: that tree is a THIRD tree: the two parents pinned `(11860, "267dce26b4191743")` (this branch's
#: own, a tree no rev is named for) and what `10e51693f1` COMMITS `(11858, "a1552662bf07a10b")`
#: (`origin/main`'s own re-cut, which the re-cut block above publishes as well), and `--repin` ON THIS
#: RESOLUTION prints `(11864, "2a6400518a87775c")` — FOUR MORE than this branch's parent, none of
#: which is the branch's own +3 end-proof rows, so the four arrived with `origin/main`'s commits and
#: an inheritance of either parent's line would have been wrong in both directions. Arithmetic
#: across the parents is a CROSS-CHECK here, never the evidence. `--check` exits 0 on this
#: resolution with `typed references still pointing at a public name: 0`.
#:
#: ⛔ STALE AS OF THE RE-CUT BELOW — THIS SENTENCE DESCRIBES THE PREVIOUS PIN'S TREE, NOT THIS ONE
#: (#14282 review cycle 2, P3). The count the re-cut below publishes is `11861`, and `--check`
#: exits 0 against it at `c5e442acd7`, the head that carries that pair, not against `11858`. Read
#: as a statement about the tree the reader is standing on, "11858 is re-measured on the tree that
#: carries them" is false, and accepting it is precisely the inherited-figure error the re-cut
#: exists to repair. It is kept, marked, because it is the record of how that superseded pair arose.
#:
#: RE-PINNED (11858, "a1552662bf07a10b") → (11861, "f0647020531c078c"), measured with `--repin` on
#: TWO trees rather than derived by arithmetic. `main`'s OWN tree at `5e83c2a2f6` prints the new
#: figure, and so does this lane's tree, so the +3 belongs to the BASE and this change contributes
#: NO row at all.
#: THE THREE ROWS, named: `proj.g.query` 3643 → 3646, all three in `tools/read_wave_latency.py` —
#: a file ADDED by `94d75667dc` (`#14224`), which merged ONE MINUTE BEFORE `10e51693f1` (`#14160`)
#: committed the pair this one replaces. So the pair it replaced was false of the tree it landed on
#: from the moment it landed, and this is the recurrence the block above records, not a new class.
#: ⛔ WHAT MEASURES `main` — MEASURED, NOT ASSUMED (#14282 review, P3). The pin IS graded on `main`:
#: `python-ci.yml` runs on `push: branches: [main]` and the `surface-guard` job carries no `if:` and
#: no `paths:` filter, so a main push's own run executes this step — run `44967485260`
#: (`event=push`, `sha=70fbd1a27dd5`, i.e. `main`) REDS it, and `44952266542` (`b4da1f5c01`) passed
#: it, so the step demonstrably RUNS on `main` rather than being skipped there. The real mechanism
#: is DROPPED RUNS, not a missing gate: a superseded main push is routinely discarded before it
#: starts a job (this workflow's own concurrency block measures 17 of the 20 push-to-main runs
#: created 2026-10-26T22:40Z..10-27T04:50Z never starting one; read again over the 20 push-to-main
#: runs newest at this PR's base, `gh api …/runs?event=push&branch=main&per_page=20` prints 15
#: `cancelled`, 3 `failure` and 2 unfinished at the instant of that read — a POINT READING, not a
#: standing figure: an earlier cut of this clause said "18 of the last 20 are `cancelled`", which no
#: reading of that window reproduces), so the red arrives after merge or not at all — and
#: every open PR then grades `main`'s rows as its own (`#14062`). ⛔ CORRECTED: this sentence read
#: "the pin is graded on PULL REQUESTS only, so `main` drifts off it with nothing measuring it",
#: which is false of the tree it lands on and sends the next lane looking for a gate to BUILD
#: instead of at the drop that already happens. ⛔ CORRECTED AGAIN (#14282 review, P3): the sentence
#: "`surface-guard` is not a REQUIRED context, so a merge does not wait for it" is FALSE of this
#: repository, and it is the same wrong conclusion one level down from the one above.
#: `surface-guard` IS in the required aggregate's `needs:` (`python-ci.yml` leg-table row
#: `surface-guard|…|-`, the `-` meaning no diff gate, so it must be SUCCESS) and is absent from
#: `DEFERRABLE_LEGS`, so a failed non-test leg lands in `FAILED_LEGS` → `exit 1` — and
#: `python-ci-gate` is itself the required context (`merge_conditions` in `.mergify.yml`). A red
#: `surface-guard` therefore BLOCKS EVERY MERGE through the aggregate, which is why this repair is
#: a merge-unblocking fix and not a cosmetic pin refresh. ⛔ THE INHERITANCE IS MEASURED,
#: NOT ASSUMED: a `--repin` on `10e51693f1`'s OWN tree prints (11861, "f0647020531c078c") too, so
#: that delta is the base's and never this lane's, and no arithmetic on the last figure was used.
#: `typed references still pointing at a public name: 0` on both trees, so no typed call site is
#: legitimised by re-cutting: the three rows are `proj.g.query` on a FalkorDB project handle, the
#: ordinary-word receiver shape this pin exists to DECLARE and deliberately leaves alone.
#: Measured with `--repin`, never by arithmetic on the last pin, and EACH ADDRESS BELOW IS GRADED
#: RATHER THAN DEAD WEIGHT (#14282 review, P3): all three revs COMMIT the superseded pair, so a
#: mutant that swaps one for a rev committing a different pair reds in `OWN_PINS` instead of
#: passing as prose. `5e83c2a2f6` — `main`'s own tree at this branch's PRE-REBASE base point: the
#: PRE-REBASE head's parent, NOT the branch's base point, the branch having been rebased onto a
#: later `main` afterwards. Its `--repin` prints the re-cut figure. Its own pin is
#: `(11858, "a1552662bf07a10b")`. `10e51693f1` — the squash that committed that stale pair. Its own
#: pin is `(11858, "a1552662bf07a10b")` too, and by then the row set had already moved under it, so
#: the pair was committed to a tree it was not a reading of. ⛔ AND THE INTERVAL COMES AFTER THE
#: CLAUSE, NEVER BETWEEN THE REV AND IT (#14282 review cycle 5, P3): a clause belongs to the nearest
#: backticked hex token BEFORE it, so writing `... 705 s BEFORE
#: `5e83c2a2f6`` here — between this rev and its own clause — silently reassigns the clause to
#: `5e83c2a2f6` and leaves `10e51693f1` reading as though its pair were never stated. (That
#: mis-attribution used to be a RED; its arm went in the B8 cut, so the placement rule is now the
#: writer's to observe.) By committer time it is 64 s after `94d75667dc` (stated with its own
#: evidence above) and 705 s BEFORE `5e83c2a2f6`, so the bare "64 s later" an earlier cut wrote
#: named the wrong antecedent.
#: `efe5605e976a` — this branch's PRE-REBASE head, named as what it is and NOT as a relative
#: "THIS tree": its own `--repin` prints the re-cut pair, so the MEASUREMENT was true and only its
#: ADDRESS was stale. Its own pin is `(11858, "a1552662bf07a10b")`, the pair it COMMITS.
#:
#: ⛔ THE SAME PAIR WAS INDEPENDENTLY REACHED ON `main`, AND ITS RECORD IS KEPT BELOW
#: RATHER THAN PICKED AGAINST (#14282 merged with #14258). Both trees arrived at
#: `(11861, "f0647020531c078c")` from the SAME +3 — `proj.g.query` 3643 → 3646 in
#: `tools/read_wave_latency.py` — so neither paragraph is the winner and neither is
#: superseded prose. The paragraphs ABOVE record what THIS branch measured, and which revs
#: its OWN addresses (`5e83c2a2f6`, `10e51693f1`, `efe5605e976a`) commit. The paragraphs
#: BELOW record `#14258`'s independent derivation on `main`, and they carry two facts the
#: block above does not: a pair must be quoted from `--repin` rather than `--report`, and the
#: +3 is attributed by a per-rev call-SITE measurement rather than by arithmetic. Both are
#: LOAD-BEARING, which is why neither side may be dropped by the merge: the revs named below
#: (`10e51693f166`, `0351fe856942`) are exactly the `OWN_PINS` entries #14258 added, and its
#: `commits (N, "digest")` claim spelling was graded by an assertion it added to the pair arm —
#: which the B8 test-bureaucracy cut (2026-11-23) removed, so that spelling is UNGRADED now.
#: Merged rather than chosen,
#: because resolving a conflict between two CORRECT records by deleting one is how a measured
#: fact becomes folklore.
#:
#: ⛔ SUPERSEDED BY THE RE-CUT — the sentence that CLOSES "on the tree that carries them, not carried
#: forward" (it closes the paragraph that OPENS `⛔ RE-CUT → (11858, "a1552662bf07a10b")`, and the
#: paragraph that opens `STALE AS OF THE RE-CUT` marks that same reading of the
#: sentence) did NOT survive the commits merged
#: beside it: the census moved three rows before the pin commit itself landed. Read the re-cut, not
#: that sentence. ⛔ NAMED BY ITS CLOSING WORDS, NOT BY "the sentence above" (#14282, P3): `main`
#: wrote that address positionally and it was CORRECT where it wrote it — the merge inserted the
#: `#14258` reconciliation paragraph between the note and the sentence, so it now names a different
#: record. ⛔ AND THE FIRST FORM OF THIS NOTE ALSO CLAIMED THE "below" HALF HAD ROTTED, WHICH IS
#: FALSE AND WAS MEASURED (#14282 review, P3): the paragraph `main`'s "below" named — the one
#: that OPENS `RE-PINNED FROM `main`'s DRIFT AGAIN, AND THIS TIME THE MEASUREMENT` — is
#: byte-identical on `origin/main` and here, compared line for line — nothing was inserted under it,
#: so its "below" reading survived the merge untouched. What actually happened is that the
#: `(11858 …) → (11861 …)` re-cut this branch publishes sits ABOVE the note, separating the note
#: from it — so a positional "the re-cut below" cannot name it either way, not because the address
#: rotted but because it never named this branch's re-cut at all. Both targets are therefore named
#: by TEXT, the only form that survives an insertion, which is the rule this block already states.
#: ⛔ AND NO DISTANCE OR OFFSET IS TYPED HERE — not here, and not in the parenthetical above
#: either, which names its targets by their OPENING WORDS rather than by "several paragraphs up":
#: a span or an offset restated in this block goes stale at the next edit, which is the reason the
#: census figure is named by log rather than counted. ⛔ AND THE FIRST FORM OF THAT
#: QUOTE WAS A PREFIX CALLED AN ENDING (#14282 fix lane, P3): `the sentence that ENDS "`--check`
#: exits 0 at this head on the SAME pair, so 11858 is re-measured"` is FALSE of this text — the
#: sentence runs on to `not carried forward` — so a reader sent to verify the ending finds no
#: sentence that ends there. The closing words quoted first ARE the close, being that sentence's
#: own last words.
#:
#: ⛔ RE-PINNED FROM `main`'s DRIFT AGAIN, AND THIS TIME THE MEASUREMENT WAS TAKEN ON A BASE THAT
#: HAD ALREADY MOVED UNDER IT — the one failure a pin cannot see from inside, because the reading
#: and the tree are both green at the moment it is taken. `10e51693f166` (#14160) commits
#: `(11858, "a1552662bf07a10b")`, and `--check` in a detached worktree AT THAT COMMIT reds against
#: that committed pair: it prints observed `(11861, "f0647020531c078c")`, the identical pair
#: `--check` prints on `main` at `70fbd1a27dd5` and the pair the required `surface-guard` job
#: printed for this branch's run. So the committed pair was true of the tree `--repin` was run on
#: (the branch head, which carried `main` at `9f83a63bfab7` merged in) and false of the tree the
#: squash landed on: `SWEEP_RESIDUE` is a WHOLE-TREE census, so a commit that adds an untyped
#: receiver anywhere moves it while having no reason to touch this file — and one did, on `main`,
#: between that merge and the squash.
#: THE +3 IS ATTRIBUTED BY A ROW-BY-ROW DIFF, not by arithmetic, and the ISOLATING pair of trees
#: is the branch tip against `main` — NOT `9f83a63bfab7`, which predates this branch's own declared
#: `out.update` row and so reads a delta of four. `0351fe856942` (#14160's PRE-squash tip,
#: resolvable through `refs/pull/14160/head` — `git fetch origin refs/pull/14160/head`) commits
#: `(11858, "a1552662bf07a10b")`, and `--repin` in a detached worktree there prints that same
#: pair, so the red is a property of the LANDING tree rather than of the branch. ⛔ NOT the
#: post-squash tip `c5e84ad180f2`, which is where `origin/fix/14125-census-p1s` stands TODAY: it is
#: a DESCENDANT of `0351fe856942`, pushed after the squash, whose own content never landed, and it
#: happens to commit the same pair — which is why it reads the same and is still the wrong tree to
#: name (an earlier cut of this paragraph did, review round 2, P2). `main` at `70fbd1a27dd5`
#: re-measured with `--repin` reads `(11861, "f0647020531c078c")`.
#: ⛔ THE PAIRS COME FROM `--repin`, NOT `--report`. `--report` prints the untyped COUNT and the
#: rows and NO digest at all (measured: `--report --base 28ca66878d… --top 5 | grep -c
#: f0647020531c078c` → **0**), so a pair quoted as `--report`'s output sends a reader to a command
#: that cannot reproduce it. `--repin` prints `SWEEP_RESIDUE = (n, digest)`; `--check` prints an
#: observed digest ONLY on its failure path (`observed: (n, digest)` on stderr), never on a
#: passing run — so a pair quoted from a green tree is `--repin`'s or nothing.
#: `--report --base 28ca66878d… --top 5000` run at BOTH trees is what yields the row SETS, sorted
#: and diffed, and EXACTLY ONE row differs
#: out of an UNCHANGED 856-row SHAPE set on both sides:
#:   * `proj.g.query` **3643 → 3646** (+3). `94d75667dc76` (#14224) adds three `proj.g.query(...)`
#:     call sites in the new `tools/read_wave_latency.py` (two pass a `count(n)` query, the third a
#:     multiline A/B id query), and it is the only commit in `9f83a63bfab7..10e51693f166` that adds
#:     a `proj.g.query` call SITE. Measured per rev, with BOTH non-zero readings printed rather
#:     than one: `git show <rev> --format='' -- '*.py' | grep -c '^+.*proj\.g\.query'` reads **3**
#:     for `94d75667dc76` (the three sites above) and **2** for `10e51693f166` itself — both of
#:     those inside DOCSTRING text in `tests/test_14125_retired_name_calls.py`, which is a string
#:     the AST counter does not read — and **0** for each of the other eight of the ten
#:     `.py`-touching revs in that range (`git log --oneline 9f83a63bfab7..10e51693f166 -- '*.py'
#:     | wc -l` → 10). A `proj.g` receiver chain is this sweep's documented edge — `proj.g` binds a
#:     `_GuardedGraph` over the FalkorDB `Graph`, and a graph handle is never the SDK — and the ROW
#:     already existed, so a count moved; no shape appeared.
#: ⛔ `out.update` IS NOT PART OF THAT DELTA, and saying it was is the inversion this paragraph was
#: rewritten to remove: the row reads **43** on `#14160`'s pre-squash tip `0351fe856942` AND on
#: `main` at
#: `70fbd1a27dd5`, so it sits INSIDE the committed pair rather than on top of it. It moves only
#: against `9f83a63bfab7` (**42 → 43**), the merge base this branch carried — `#14160`'s own row,
#: which the block above already DECLARES (`_global_declarations` in
#: `tests/test_14125_retired_name_calls.py`), and never the reason the squash reds.
#: No other row differs in either direction, so nothing was absorbed: `typed references still
#: pointing at a public name` is **0** on this tree, and the direction the pin exists to catch — a
#: NEW row SHAPE — is not what happened here. Re-measured on `main`'s own tree with `--repin`,
#: never carried from the pair quoted above by arithmetic: `(11861, "f0647020531c078c")`, and
#: `--check` exits 0 on it.
#: ⛔ MERGED `origin/main` `0467ba329d` INTO THIS PR (the PR was `CONFLICTING`: four commits,
#: `#14258`, `#14295`, `#14281`, `#14303`) — a NINTH tree, so the pair is `--repin`'s output on
#: THIS merged tree: the branch's 11870 marked its own pre-merge tree and `main`'s 11861 marked
#: `main`'s — and the merged reading IS the branch's own, so `main`'s numeral is not carried. `--repin` prints
#: **(11870, '6da40999bb5162fa')** and `--check` exits 0 on it with `typed references still
#: pointing at a public name: 0`.
#: ⚠️ THE MERGED PIN EQUALS THE BRANCH'S PRE-MERGE FIGURE AND SITS 9 ROWS ABOVE `main`'S (11870
#: against 11870 / 11861), so the merge did NOT net-remove rows. What this record does NOT
#: do is attribute that net by arithmetic on the three numerals — the direction the pin exists to
#: catch is a NEW row SHAPE, and `--repin`'s own row report (`--report --base <sha> --top 5000`,
#: run on this tree against each side's tree) is the only thing that attributes it. Unmeasured
#: attribution is left unstated rather than guessed; the pair above IS measured, on this tree.
#: ⛔ ONE ASSIGNMENT: this file carries exactly ONE `SWEEP_RESIDUE = (` — the two sides each
#: brought their own across this merge, so the dead one was deleted with its prose KEPT (a second
#: assignment is what `tests/test_sdk_privatise_sweep.py`'s one-assignment arm exists to refuse,
#: because Python binds the LAST while a first-anchored walk grades only the prose above the FIRST).
#: ⛔ THE ATTRIBUTION THE PARAGRAPH ABOVE LEAVES UNSTATED, MEASURED RATHER THAN GUESSED — and the
#: record of what the merge COMMITTED against what its own tree derives. `c3d33f9e1a39` committed
#: `SWEEP_RESIDUE = (11860, '6051234d8dab29c8')`, and `--check` AT THAT COMMIT prints observed
#: `(11870, '6da40999bb5162fa')` against that committed pair and exits **1** — so the committed
#: pair was not the tree's reading, and the required `surface-guard` job (a `` `needs:` `` arm of
#: `python-ci-gate`) graded RED under it. The same observed pair is printed at
#: `840c201272a08d2a085cf8995a3585a42b81132e`, and
#: `git diff c3d33f9e1a39..840c201272a0 -- '*.py'` adds no receiver at all (its single added match is
#: a `proj.g.query(` INSIDE A COMMENT, in `tests/test_12184_shifted_cites.py`, which an AST census
#: does not read) — so the merge, not the three commits after it, is where the pair went wrong.
#: ⛔ THE +9 OVER `main` IS ATTRIBUTED BY A ROW-SET DIFF, never by arithmetic:
#: `--report --base 28ca66878d7c617a2f28a8025ddaa40352adfcb2 --top 5000` was run on THIS tree and
#: in a detached worktree at `origin/main` `0467ba329d` (whose own pin is
#: `(11861, "f0647020531c078c")` and whose `--check` exits 0, so `main` is NOT the red one here),
#: and the two sorted row sets hold 856 rows there and 857 here, differing in EXACTLY three:
#: `cp.query` **263 → 270** (+7), `row.update` **59 → 60** (+1), and **one row that exists only
#: here** — `self.plane.query` **1**, the capacity-notice seam and its fake, which the block above
#: already DECLARES. No row was absorbed in the other direction, so nothing left by the fold went
#: uncounted, and the direction this pin exists to catch — a NEW row SHAPE that no record declares —
#: did not happen: the one new shape is the declared one. A plain `cp` handle and a `set[str]`-style
#: `.update` are the ordinary-word receivers this sweep deliberately leaves alone; `typed
#: references still pointing at a public name` is **0** on this tree.
#: ⛔ RE-PINNED ON THE MERGE WITH `origin/main` `588cf7ae`, and the re-cut is the POINT: the
#: pin is a reading of a TREE, so it is taken here rather than inherited from either parent.
#: `--repin` on THIS resolved tree prints **(11873, 'b617f45a195476a6')** and `--check` exits 0
#: on it with `typed references still pointing at a public name: 0`. ⚠️ The first reading of this
#: same resolution printed 11864 and was NINE LOW, because `tests/test_7949_capstone_measure.py`
#: still carried conflict markers when it was taken: a file that does not PARSE is silently
#: uncounted, so a census of such a tree is a reading of a different tree — the numerals in the
#: paragraphs above are the PREVIOUS re-cuts' readings of the trees THEY named, kept as history
#: and never carried as this tree's.
#: ⛔ THE BASE FIGURE IS THE BASE **TREE's** MEASUREMENT, never the `SWEEP_RESIDUE` line committed
#: on it — AND AT THIS CUT THE TWO DISAGREE ON MAIN. `origin/main` `1b0db0489408` COMMITS
#: `(11843, "6aed5368ffa10513")` while its tree MEASURES `(11840, "b7eb08ff3acdcce4")`, so
#: `--check` exits 1 there: main's own drift (the `inst.*` rows `#14162` removed and the
#: `terms.update` / `cur['proj'].g.query` rows it added), which belongs to main and is re-pinned
#: there (#14180/#14170 are the same repair, twice). ⛔ NOTE THE COLLISION a reader must not fall
#: into: main's committed COUNT is 11843 — the same count this pin carries — while its DIGEST is
#: a different tree's. Read that committed line as the base and this pin looks off by 3, or worse,
#: looks confirmed. ⛔ AND THE SAME TRAP IS WHY #14183'S ROUND-5 REVIEW CALLED THIS PAIR FALSE
#: while `--check` was green: the base before it committed 11835 and measured 11843 — off by 8
#: (#14183 review round 5, finding 2, falsified by measurement). The branch's figure stays its
#: own `--repin` output, taken on the merged tree.
#:
#: ⛔ MERGED `origin/main` `414761ae` INTO THIS BRANCH (the PR was `CONFLICTING`: six files) —
#: BOTH SIDES' BLOCKS ABOVE ARE KEPT, which is this run's ground rule: each is a true measurement of
#: the tree it NAMES, so neither side is dropped to make a conflict go away. ⛔ THE PIN IS THEN
#: RE-MEASURED ON THE RESOLVED TREE, because a merge tree is a THIRD tree and neither parent's
#: numeral is inherited: `--repin` here prints **(11873, 'b617f45a195476a6')**, and `--check` exits 0
#: on it with `typed references still pointing at a public name: 0`. ⚠️ THE MERGED PAIR EQUALS THIS
#: BRANCH'S PRE-MERGE PIN AND SITS 9 ROWS ABOVE `main`'S OWN `(11864, "2a6400518a87775c")` at
#: `414761ae`, so `main`'s two commits added no net row here — stated as a MEASUREMENT of the two
#: trees, never as arithmetic across the parents. A later merge re-measures this pin rather than
#: deleting a previous reading; if a block above contradicts the live pin, that block is HISTORY and
#: the last one is the PIN.
#: ⛔ SUPERSEDED BY THE RE-CUT BELOW — this block is `#14263`'s own reading of ITS tree, kept
#: rather than dropped because merging two CORRECT records by deleting one is how a measured fact
#: becomes folklore. ⛔ RE-CUT → (11870, "fccc31bf7f22f941") by the `#14263` relation-pair lane, and BOTH SIDES of
#: the move are MEASURED on their OWN tree — `--repin` in a detached worktree at `origin/main`
#: `70fbd1a27dd5` prints `(11861, "f0647020531c078c")`, and `--repin` on THIS tree prints
#: `(11870, "fccc31bf7f22f941")`, so the base's contribution is read rather than inferred from a
#: difference. `main`'s own +3 over the 11858 this block used to pin is `main`'s business and is
#: tracked there (`#14294` measures that same `(11861, "f0647020531c078c")` on that same tree), so
#: this block declares ONLY the 9 rows this PR owns — and it declares them because `--check` reds
#: by 12 on this tree, i.e. the base's 3 plus these 9.
#: THE +9 IS ATTRIBUTED BY A ROW-BY-ROW DIFF, not by arithmetic: `--report --base 28ca66878d7c
#: --top 200` was run on THIS tree AND in a detached worktree at `origin/main` `70fbd1a27dd5`, both
#: row sets sorted and diffed, and EXACTLY THREE rows differ — `proj.g.query` 3646 → 3647 (+1),
#: `sdk._get_proj().g.query` 1431 → 1438 (+7) and `s._get_proj().g.query` 29 → 30 (+1), i.e. 1+7+1
#: = 9 = 11870 − 11861. Every one is this PR's own new code: the pair-leg readers in
#: `tortoise/sdk.py` (`_endpoint_kind_map`, `_relation_pair_notes`, `_carry_relation_declaration`)
#: and the one test-side `s._get_proj().g.query(…)` in `tests/test_relation_guards_14263.py`.
#: All three rows are `Graph.query` calls on a FalkorDB `Graph` — the documented edge this counter
#: reports and deliberately leaves alone, never an SDK handle — so no new shape was absorbed and
#: no defect is hidden: the row SHAPE set is unchanged, and `typed references still pointing at a
#: public name` is **0** on this tree (measured, `--check`). ⛔ NEITHER SIDE'S FIGURE WAS CARRIED BY
#: ARITHMETIC on the previous pin: both pairs above are `--repin` output on the tree they name.
#: ⛔ THE +9 IS THE READING TAKEN BEFORE `dcf5e3621c` — that commit removed the third row, so the
#: live delta on that branch's own tree was +8, re-measured there rather than adjusted by arithmetic.
#: ⛔ THIS RE-CUT MERGED `origin/main` `0467ba32` (base `70fbd1a2`) AND KEPT BOTH BLOCKS ABOVE —
#: the branch's `(11864, "2a6400518a87775c")` resolution of `10e51693` and `main`'s
#: `(11861, "f0647020531c078c")` re-pin, each a true measurement of the tree it NAMES — so no
#: side's reading is dropped to make the conflict go away, and the pair below is a THIRD
#: measurement: `--repin` run on the resolved tree, never an inheritance of either parent's line
#: (the parents DISAGREE, 11864 against 11861, and arithmetic across them is a cross-check at
#: most). `--check` exits 0 on the resolution with
#: `typed references still pointing at a public name: 0`.
#: ⛔ RE-MEASURED ON THIS MERGE RESOLUTION, and the peer's reading of the SAME resolution is
#: kept here because it measures a different failure: `--repin` on the FULLY RESOLVED tree prints
#: `(11864, "2a6400518a87775c")` — this branch's parent pair, and three above `origin/main`'s
#: (`(11861, …)`). ⛔ AND AN EARLIER READING OF THIS SAME RESOLUTION PRINTED 11854, which is
#: TEN LOW: it was taken while two conflicting test files still carried conflict markers, so
#: they did not PARSE and their residue shapes were silently uncounted. A census of a tree
#: that does not parse is a reading of a DIFFERENT tree — the same class this block keeps
#: naming, one layer out. Re-pin only from a tree whose covering suites are green.
#: ⛔ THIS PR'S OWN CONTRIBUTION IS ZERO ROWS, and that is measured rather than asserted:
#: `edits_for` run over BOTH files whose CENSUS text this change touches yields zero typed
#: references and ZERO residue rows (`tools/sdk_privatise.py` contributes only the two
#: `residue_all.update`/`after_residue.update` accumulators already inside the pinned set, and
#: `tests/test_sdk_privatise_sweep.py` contributes nothing), so the pair this file pins is the
#: base's and not the branch's — the attribution above is `main`'s drift, re-measured on the
#: MERGED tree, never carried from either side's figure by arithmetic. `--check` prints
#: `typed references still pointing at a public name: 0`, so no public name was legitimised in
#: either direction and no new SHAPE was absorbed: both rows are DECLARED here rather than
#: rewritten away — re-pointing an ordinary-word receiver on a `Graph` would be sabotage, which is
#: the `declared_markers.update` precedent, and a plain `set[str]` in a test helper is the
#: `terms.update` precedent.
#: ⛔ THIS MERGE (`origin/main` `38f2ab7fc594` into `chore/14263-pack-business-logic`) KEEPS BOTH
#: SIDES' BLOCKS ABOVE, which is this chain's ground rule: the branch measured its own pair-leg
#: readers and then re-cut its own tree when a dead test-side teardown was deleted, and `main`
#: independently re-pinned for ITS tree — each is a true reading of the tree it NAMES, so neither is
#: dropped to make the conflict go away. The pair below is a THIRD measurement: `--repin` RUN on the
#: fully resolved merge tree, never an inheritance of either parent's line and never arithmetic
#: across them (the parents disagree). It reads **11872**, three above the branch's own re-cut and
#: eight above `main`'s; that difference is RECORDED, not attributed — resolving which rows came
#: from where is `--report --base`'s work, and this block does not claim it. ⛔ Re-pin only from a
#: tree whose covering suites are green: a tree that does not PARSE reads a different tree, which is
#: the class this block already records one layer out. `--check` exits 0 on the pair below with
#: `typed references still pointing at a public name: 0`.
#: ⛔ MERGED `origin/main` `d2cd8ab06e2` INTO THIS BRANCH (the PR was `CONFLICTING`: eight files)
#: — BOTH SIDES' BLOCKS ABOVE ARE KEPT, which is this file's standing ground rule: each block is a
#: true measurement of the tree it NAMES, so neither is dropped to make a conflict go away. The pair
#: below is a THIRD measurement: `--repin` RUN on the fully resolved merge tree, never an inheritance
#: of either parent's line and never arithmetic across them (the parents disagree).
#: PROVISIONAL until the fold is green — re-pinned below by `--repin` on the resolved tree.
#: RE-CUT AT THE `#14232` MERGE (`432540dc44`, × `main` `0467ba329d`) — `main`'s
#: `(11861, "f0647020531c078c")` → `(11875, "6c8727fc80045370")`, and the +14 is ENTIRELY the
#: `#14232` change rather than inherited drift, which is measured rather than assumed the same way
#: the merge notes above measure it: `--report --base 28ca66878d --top 5000` on `origin/main`
#: `0467ba329d`'s own tree prints **11861** — the number that tree re-cut to, so the base
#: contributes nothing — and **11875** on the merged tree. Seven shapes move, each read on BOTH
#: trees and none absorbed in the other direction:
#:   * `_proj().g.query` did not exist on the base and reads **6** here: the six
#:     `_proj().g.query(` reads the async-ACK rows add in `tests/test_14232_ingest_ack_split.py`
#:     (`git diff origin/main...HEAD -- tests/ | grep -c '^+.*_proj()\.g\.query'` → 6);
#:   * `proj.g.query` 3646 → 3649 (**+3**, the NET of `tortoise/hosted_api.py` +8/−6 as the capture
#:     path is split around the ACK, plus +1 in `tools/acceptance_14232_drain.py`);
#:   * `graph_cls.query` 11 → 12 (**+1**), `registry.query` 38 → 39 (**+1**) and
#:     `string 'query' on untyped receiver` 109 → 110 (**+1**), all three in this branch's own test
#:     rows;
#:   * two shapes the base did not carry at all: `self.get_session` **0 → 1** and
#:     `transport.get_session` **0 → 1**, the acceptance tool's transport read reached through an
#:     untyped receiver, left alone deliberately like every other row here.
#: 6 + 3 + 1 + 1 + 1 + 1 + 1 = 14 = 11875 − 11861, which is the cross-check that no row was
#: silently absorbed; the pair is `--repin`'s output on the merged tree at `432540dc44`, not
#: arithmetic on `main`'s, and `--check` exits 0 on it with `typed references still pointing at a
#: public name` at **0**.
#: ⛔ THIS PR-LEVEL MERGE (`origin/main` `a7baa98d05` × `#14232`, resolved on `a740f2c6fd`) KEEPS
#: BOTH RECORDS ABOVE, which is this chain's ground rule: `main`'s `#14263` chain re-cut ITS OWN
#: tree to `(11872, "1f07a0b7bcf05138")` and this branch re-cut its tree to
#: `(11875, "6c8727fc80045370")`; each is a true measurement of the tree it NAMES, so neither is
#: deleted to make a conflict go away. The pair below is a THIRD measurement — `--repin` RUN on the
#: fully resolved merge tree, never inherited from either parent's line (they disagree, 11872 against
#: 11875) and never arithmetic across them. ⛔ Re-pin only from a tree whose covering suites are
#: green: a tree that does not PARSE reads a DIFFERENT tree, which is the class the blocks above
#: already record one layer out. `--check` exits 0 on the pair below with
#: `typed references still pointing at a public name: 0`.
#: It reads **11886**, i.e. `main`'s 11872 plus this branch's own 14 — the SAME +14 the `#14232`
#: block above attributes row-by-row on ITS base (11861 → 11875), which is the cross-check that the
#: branch's contribution survived the merge unchanged; ⛔ the sum is a CHECK, never the source: the
#: pair below is `--repin`'s own output at the resolved tree.
#: ⛔ RE-CUT AGAIN AT THE `origin/main` MERGE (`663eb19` × `main` `9aaaff10`), AND THE MOVER IS
#: `main`'S RED, NOT THIS BRANCH'S. `main`'s own tree at `9aaaff10` derives
#: **(11887, "e21c8c3fd680e043")** against its own pin of 11886 — measured by running `--check` in
#: a detached worktree at that sha — so `surface-guard` was RED on `main` BEFORE this branch merged
#: it, and the merged tree reproduces `main`'s set bit-for-bit (same digest), never a new one.
#: Attribution, measured rather than assumed: THIS branch contributes **0** references of that
#: class — `git diff -U0 <merge-base> 663eb19 | grep '^+' | grep -cE '\.(ro_)?(query|execute)\s*\('`
#: prints **0** — while the `+1` is `main`'s `#14389` (`9aaaff10`), which adds exactly ONE new
#: residue shape row (`1  lost_fields.update`) and did not re-cut this pin with the file;
#: `--report --base 28ca66878d7c --top 5000` on `9d6d2e347` (its parent) versus `9aaaff10` is the
#: one-row diff that says so. DECLARED, not swept: `lost_fields` is a plain `set[str]` local in a
#: test helper (`tests/test_14343_embed_list_serialization.py:377`), so the untyped-receiver arm is
#: doing exactly what it documents, and re-spelling another PR's file to move a count would be the
#: wrong repair. The pair below is `--repin`'s own output on the fully resolved merged tree — a
#: THIRD measurement, neither parent's line — and carrying it here repairs `main`'s red as well.
#: ⛔ MERGED `origin/main` `9aaaff10acd` (× `f5fc7ca52c`) INTO THIS BRANCH — BOTH SIDES' BLOCKS
#: ABOVE ARE KEPT, this file's standing ground rule. The pair below is `--repin`'s own output on
#: the RESOLVED merge tree, never inherited from either parent (they disagree: this branch's
#: provisional `11881` against `main`'s `11886`) and never arithmetic across them.
#: ⛔ RE-CUT AT THIS BRANCH'S MERGE WITH `origin/main` `0fbfbb5b210c` — AND THE RE-CUT REPAIRS `main`'s OWN
#: RED. `main`'s committed pin at that sha is `(11896, "ea44c5f732d2a97d")`, and `--check` on `main`'s OWN
#: tree there exits 1 with `untyped receivers left alone (deliberate, reported): 11911`: two untyped
#: receivers landed on `main` between its last re-cut and this head, so `main` is RED on `surface-guard`
#: before this branch is involved at all. This cut re-derives the pair from the MERGED tree, so it repairs
#: that red. ⛔ Those two are RECORDED, not attributed: resolving which rows came from where is
#: `--report --base`'s work, and this block does not claim it (the blocks above state the same convention).
#: THIS BRANCH'S OWN CONTRIBUTION IS EXACTLY ONE OCCURRENCE INSIDE ONE EXISTING ROW, measured by
#: `--report --base 28ca66878d --top 3000` on BOTH trees and diffed row by row: `main` `0fbfbb5b210c`
#: reads **11898** over **861** distinct rows, and the merged tree reads **11899** over the SAME **861**
#: rows — `store.list_batches` was REMOVED by this branch (`b59b38787f3a`, below), so the row count does not move
#: and no row is absorbed in either direction.
#:   * `proj.g.query` **3650 → 3651** (+1), `tortoise/hosted_api.py::_batch_store_for`:
#:     `_batch_ingest.GraphBatchStore(proj.g.query)`. #14219's batch layer is handed the project handle's
#:     own door instead of opening a second one — the ordinary-word receiver shape this pin exists to
#:     DECLARE and deliberately leaves alone, and `typed references still pointing at a public name` is **0**.
#: ⛔ A SECOND new shape was REMOVED rather than declared, and that is why this branch is +1 and not +2.
#: `store.list_batches` (+1) was the batch layer's own store method carrying a RETIRED public SDK name
#: (`#11609`: `tortoise/sdk_retired.py` maps `list_batches` → `list_knowledge`, and
#: `archive/sdk-methods/12901-sdk-final/list_batches.py` holds the body). The sweep cannot type the
#: receiver, so it reported the call as residue — CORRECTLY, because a reader who greps the retired name
#: finds a live call and no way to tell it from the retired surface coming back. The method is now
#: `list_recent_batches` (the REST handler keeps the route's own name, which is not a receiver call site).
#: Re-pin only from a tree whose covering suites are green: `--check` exits 0 on the pair below.
#: ⛔ A THIRD FOLD (`main` `0fbfbb5b21`, the `fix/14344-expansion` × `origin/main` merge) KEEPS BOTH
#: blocks again, exactly as the two above argue, and re-derives the pair with `--repin` on THIS
#: resolved tree: neither parent's line is inherited (`663eb19` read 11887, the `9aaaff10acd` fold
#: read 11896) and no arithmetic crosses them.
#: The mover is `main`'S OWN STALE PIN, not this branch — MEASURED, not inferred from commit
#: titles: `main`'s own tree at `0fbfbb5b21` derives **(11898, "8cf59d52af09e536")** against its
#: own pin of `(11896, "ea44c5f732d2a97d")` — `--repin` and `--check` in a detached worktree at that
#: sha, where `--check` exits **1** printing `observed: (11898, '8cf59d52af09e536')` /
#: `pinned: (11896, 'ea44c5f732d2a97d')` — so `surface-guard` was RED on `main` BEFORE this branch
#: merged it. The merged tree reproduces `main`'s set bit-for-bit (same count AND the same digest),
#: which is the cross-check that nobody here moved it; this branch's product, test and tool files
#: carry **0** residue rows (`edits_for` returns an empty Counter for each), so the `+2` against
#: `main`'s pin is not this branch's. The pair below is `--repin`'s own output on the tree that
#: SHIPS, and `--check` exits 0 on it — which repairs `main`'s red as well.
#: ⛔ RE-PINNED FROM `main`'S DRIFT BY THE `#14342 D1` LANE (#14350, PR #14423), and THIS BRANCH'S
#: OWN CONTRIBUTION IS ZERO ROWS — measured on three trees, not inferred from a difference.
#: That lane merges `origin/main` `0fbfbb5b210c` into `fix/14342-d1-drain`, re-cuts the pin on the
#: resolved tree with `--repin`, and reads **(11898, "8cf59d52af09e536")**. The drift is NOT this
#: branch's: the SAME observation — observed `(11898, "8cf59d52af09e536")` against a pinned
#: `(11896, "ea44c5f732d2a97d")` — is printed by `--check` on
#:   * `origin/main` `0fbfbb5b210c`'s OWN tree (detached worktree), i.e. `main` is red here itself;
#:   * `1457f7905d`'s OWN tree, the `#14038` squash-merge that CUT the `11896` — so the pair it
#:     committed is already one step behind the tree it commits, and no later `main` commit moved it;
#: and this branch adds none of it: its three new files (`tortoise/drain_claim.py`,
#: `tools/measure_drain_convergence.py`, `tests/test_14350_drain_convergence.py`) carry no
#: `.query` / `.update` / `.delete` reference AT ALL (`grep -nE '\.(query|update|delete)\b'` → no
#: match on all three), and the branch's other edits are inside files that already carry their rows.
#: ⛔ RE-PINNED A SECOND TIME BY THE SAME LANE (#14423), AND AGAIN FROM `main`'S DRIFT — the
#: observation is **(11901, "953c70c05b55923b")**, +3 rows and a new digest, measured after this
#: branch merged `origin/main` `b2d1e9086a` (the `tests/test_7879…` / `#14371 offline-embed` pair).
#: The same three-tree rule as the `11898` block above, re-measured rather than assumed: a detached
#: worktree at `origin/main`'s OWN `43182ac09a` reports the IDENTICAL
#: `observed: (11901, '953c70c05b55923b')` against the old `11898` pin — so the drift is `main`'s,
#: this branch contributes no row, and the branch's own three new files still carry no
#: `.query`/`.update`/`.delete` reference at all. ⛔ ROW-LEVEL ATTRIBUTION OF THE +3 IS NOT CLAIMED
#: here for the reason the block above already gives: the committed pin stores a COUNT and a DIGEST,
#: never the row set, so which of `main`'s new files added them cannot be recovered from the
#: artifact — only re-measured against a tree that predates it, which is what the `43182ac09a`
#: measurement IS. `--check` is green on the new pair with
#: ⛔ THE +2 IS THEREFORE NOT ATTRIBUTED ROW BY ROW HERE, AND THAT IS STATED RATHER THAN HIDDEN: the
#: committed pin stores a COUNT and a DIGEST, never the row set, so the rows `#14038` moved cannot be
#: recovered from the artifact — only re-measured against a tree that predates it. What IS measured
#: is that the reading is identical on `main`'s own tip and on `#14038`'s own tree, which is what
#: makes this a repair of `main`'s red rather than a declaration of this branch's rows. The open
#: roots are `#14308` and `#14294` ("`main` is red on `test (c)`, `test-slow (a)` and
#: `surface-guard` — three stale-guard roots every PR reproduces"); this block is the same class, a
#: pin one step behind the tree, and the evidence above is posted there rather than filed as a peer.
#: ⛔ `surface-guard` is NOT in `DEFERRABLE_LEGS`, so its red travels into the REQUIRED
#: `python-ci-gate` for EVERY open PR — leaving it is a fleet-wide landing block, not this PR's
#: inconvenience. `--check` exits 0 on the pair below with
#: `typed references still pointing at a public name: 0`.
#: ⛔ RE-CUT AGAIN AT `4b7be98fb38b` — ONE OCCURRENCE, NOT A NEW SHAPE, and the row count does not move.
#: `--report --base 28ca66878d --top 3000` on the previous head (`1cd022aa06cc`) and on this one, diffed
#: row by row, yields exactly one difference out of an UNCHANGED **861**-row set:
#:   * `seen.update` **101 → 102** (+1) — an ordinary-word receiver on a Python SET (`.update(...)` in the
#:     meter bookkeeping), which is the class this pin exists to DECLARE and deliberately leaves alone: the
#:     sweep cannot type the receiver, and a `set` is not an SDK method, so "fixing" it would mean renaming
#:     a builtin method call. `untyped receivers left alone (deliberate, reported): 11911`, and
#:     `typed references still pointing at a public name` stays **0**, so no typed call site is legitimised
#:     by this re-cut — that is the property `--check` gates, and the pin only records where the edge sits.
#: Re-pinned by `--repin` on THIS tree, never by arithmetic on the last pair: `(11900, "79e3ca9943a7dfee")`.
#: ⛔ MERGED `origin/main` `43182ac09a7d` (the `#14186` embedder seam and the `#14394`
#: witness budget) INTO THIS BRANCH. BOTH SIDES' BLOCKS ABOVE ARE KEPT, this file's
#: standing ground rule; the pair below is `--repin`'s own output on the RESOLVED merge
#: tree, never inherited from either parent and never arithmetic across them.
#: RE-CUT (11896, "ea44c5f732d2a97d") → (11898, "8cf59d52af09e536") on the `#12039` witness-budget PR,
#: and the SOURCE IS `main`'S TREE, NOT ITS PIN — the `#14062` recurrence, measured again rather than
#: assumed: `origin/main` `4993fa083f3f` derives **11898** on its OWN tree (`--repin`, run in a
#: detached worktree at that commit), so `--check` exits 1 THERE against its own 11896 pin and every
#: open PR is then graded against a number `main` does not derive. THIS PR'S OWN CONTRIBUTION IS ZERO
#: ROWS, and that is measured too: its merged tree derives the SAME pair as `main`'s own tree, and
#: `git diff <merge-base>..HEAD` over the only two files it touches (`MEMORY.md`,
#: `tests/test_fleet_state.py`) adds no `.query`/`.update`/`.delete` row at all.
#: WHY THE PIN WAS STALE ON ARRIVAL and not stale from a later drift: the commit that CARRIES 11896
#: (`1457f7905daa`, `#14038`) derives 11898 on its own tree, and its parent (`a3b0911f2049`, `#14416`)
#: derives 11887 — so 11896 is the tree of NOTHING in `main`'s first-parent chain. It is nine above the
#: pre-squash main and two below the squash, i.e. the pair was measured on a branch tree that was never
#: the merge tree, and two rows landed after that reading. The row-by-row diff `1457f7905daa^` →
#: `1457f7905daa` (both trees measured, sorted row sets diffed) is `cp.query` 263 → 270 (+7),
#: `row.update` 59 → 60 (+1), `super().query` 76 → 78 (+2), and ONE SHAPE THAT DID NOT EXIST BEFORE,
#: `self.plane.query` 0 → 1 (`tests/test_13998_capacity_notice.py:212`, a test double's fake
#: control-plane): 7+1+2+1 = 11 = 11898 − 11887, which is the cross-check that no row was silently
#: absorbed. So the two rows the pin misses are among `#14038`'s own, they are DECLARED here rather
#: than rewritten away (re-pointing an ordinary-word receiver inside a peer's test double would be
#: sabotage — the `declared_markers.update` precedent), and the one new SHAPE among them is named
#: above so the next reader does not have to re-derive it.
#: `typed references still pointing at a public name: 0` on this tree, so no typed call site is
#: legitimised by the re-cut; the pair below is `--repin`'s own output on THIS tree, never arithmetic
#: on the last pin, and `--check` exits 0 on it.
#: ⛔ A FOURTH FOLD KEEPS BOTH SIDES' BLOCKS AGAIN (`origin/main` `43182ac09a7d`, this merge) and
#: re-derives the pair with `--repin` on THIS resolved tree: neither block above is inherited as the
#: pin and no arithmetic crosses them. The mover is `main` again — measured, not inferred from commit
#: titles: a detached worktree at `43182ac09a7d` derives **(11901, "953c70c05b55923b")** on `main`'s
#: OWN tree (`--repin` there), i.e. `main` is RED against the 11898 it ships, and the tree this PR
#: ships reproduces that pair bit-for-bit (same count AND same digest) — the cross-check that this
#: branch contributes **0** rows, since its only residue-bearing change is prose and a module
#: (`tortoise/expansion_merge.py`) that opens no session. ⛔ **THE MOVER IS `main`'s `b2d1e9086a92`
#: (`#14434`), NOT `#14394`** — the first cut of this block named `#14394` (the tip being merged) and
#: that was an INFERENCE FROM THE MERGE POINT, so it is corrected here by measurement, per this
#: file's own rule: `--repin` in a detached worktree reads **11898** at `0fbfbb5b210` and at
#: `67774cdec666`, **11901** at `b2d1e9086a92`, and **11901** again — unchanged — at its children
#: `bbe54363ad65` (`#14220`) and `43182ac09a7d` (`#14394`). The sorted row sets of the two trees name
#: all three rows: `projection.g.query` 24 → 26 (+2) and `env.update` 101 → 102 (+1), so
#: 2 + 1 = 3 = 11901 − 11898 and nothing was absorbed silently. The pair below repairs `main`'s red
#: as well as ours. `--check` exits 0 on this tree; the pair is the output of `--repin` on it.
#: ⛔ A FIFTH FOLD (`origin/main` `953c299c9b1f`, `#14436`) KEPT BOTH SIDES' BLOCKS AGAIN and changed
#: NOTHING about this pin: the pair below is unchanged, and that is measured, not assumed — `--check`
#: exits 0 on it BEFORE and AFTER the merge (same count AND same digest), so neither side moved a row.
#: The merge conflict here was the prose, not the number, and its resolution kept `main`'s `#14350`
#: drift block whole beside this branch's folds rather than picking a side.
#: ⛔ A SIXTH FOLD (`origin/main` `f635f9e00075`, `#14446`) KEPT BOTH SIDES' BLOCKS and DID move the
#: pin — `+1` row and a new digest — and the mover is MEASURED, never inferred from the merge tip:
#: `--repin` in a detached worktree reads **11901** at `fb3b326fdbad` and **11902** at
#: `f635f9e00075`, so `#14446` is the commit that moved it and the tip being merged is not. The row
#: arithmetic closes rather than being asserted: that commit's ONLY changed file
#: (`tests/test_7879_record_chain.py`, +354/−37) adds three `reached.update(` lines and retires two
#: `projection.g.query(` rows, and 3 − 2 = 1 = 11902 − 11901. None of the five is a product call
#: site — the additions are `set.update` on a test's local import collector, the retirements are a
#: test double's count and delete — which is why they are DECLARED here rather than re-spelled to
#: hold the count still. This branch contributes **0** rows: its only residue-bearing change is prose
#: and a module (`tortoise/expansion_merge.py`) that opens no session. `--check` exits 0 on the pair.
#: ⛔ A SEVENTH FOLD, and the mover is `main`'s spool-drain batch again — MEASURED on main's own tree,
#: never inferred from the merge point: `--repin` in a detached worktree at `origin/main` `88cd00a068`
#: prints the IDENTICAL `(11906, "045a9146647cdfaa")` this tree derives, so the pin `main` ships
#: (11902) is RED on `main`'s own tree and carrying this pair repairs it; this branch contributes
#: **0** rows (its only residue-bearing change is prose plus `tortoise/expansion_merge.py`, whose one
#: dict-literal merge is written to avoid the `x.update` shape). ⛔ ROW-LEVEL ATTRIBUTION IS NOT CLAIMED,
#: for the reason the blocks above already give: the pin stores a COUNT and a DIGEST, never the row
#: set. What IS measured is the shapes `989765162f..88cd00a068` adds — `environ.update(` ×3 (two in a
#: test that greps for `os.environ.update(_mode_knobs(`), `h.update(` ×1 and `registry.query(` ×1 — and
#: the observed delta (+4), which the artifact cannot decompose further. All five are ordinary-word
#: receivers on test doubles, an `os.environ` map and a hash object, so they are DECLARED here rather
#: than re-spelled to hold a count still. The pair below is `--repin`'s own output on the tree that
#: SHIPS.
#: The last two re-cuts are attributable and worth knowing: 11896 -> 11900 was this PR's own batch
#: surface at `4b7be98fb38b`; 11900 -> 11908 is the 402-copy tests added on the recut, whose new
#: `ha.` / `est.` / `detail` attribute reads are untyped receivers the sweep deliberately leaves alone.
#: ⛔ MERGED `origin/main` `989765162f` INTO THIS BRANCH — the pair below is `--repin`'s own output on
#: the RESOLVED merge tree, never inherited from either parent (this branch pinned `(11911,
#: "270de036aa1b72b7")`, `main` `(11901, "953c70c05b55923b")`). THE +1 IS ATTRIBUTED BY THE TOOL'S
#: OWN ROW DIFF, not by arithmetic: `--report --base 28ca66878d --top 3000` run at the pre-merge head
#: `e2f46b75e678` (in a detached worktree) AND on this tree, both row sets sorted and diffed, yields
#: exactly TWO differences out of an 861-row set, BOTH `main`'s and both in ONE file:
#:   * `reached.update` absent → **3** — a shape that did not exist before, and main's
#:   * `projection.g.query` **26 → 24** (−2) — the same edit removing two receiver calls
#: both in `tests/test_7879_record_chain.py`, from main's `#14446` (`f635f9e00075`): a set-mutation
#: receiver (`reached.update(...)` over a comprehension of imported names) is exactly the ordinary-word
#: shape this pin exists to DECLARE and deliberately leave alone — renaming it would rewrite a test's
#: own bookkeeping. Net **+1 occurrence over +1 row** (861 → 862), and `typed references still
#: pointing at a public name` stays **0**, so no typed call site is legitimised by this re-cut.
#: ⛔ AND THE PEER LANE FOLDED `origin/main` IN AGAIN on its own head (its commits, plus
#: `origin/feat/14219-batches`'s `edd9c68585b7`, are in the tree this record is written on), so the
#: attribution above describes ONE of the two folds and the pair below is `--repin`'s own output on
#: the RESOLVED tree — never either parent's, and never the sum of the two.
#: ⛔ MERGED `origin/main` `43182ac0` (× `bbe54363`) INTO THIS BRANCH (`#14414`, the abuse
#: alert-delivery PR) AND RE-CUT ON THE RESOLVED TREE — both sides' blocks above are KEPT, this
#: file's standing ground rule. The pin arrived ALREADY STALE, and that half is MEASURED rather
#: than inferred: `--repin` in a detached worktree at `origin/main` `43182ac0` prints
#: `(11901, "953c70c05b55923b")` on `main`'s OWN tree, i.e. `main` carries `11898` and derives
#: `11901` — so every open PR is graded against a number `main` does not read (the
#: `#14062`/`#14394` recurrence the block above records once already).
#: THE TWO CONTRIBUTIONS ARE MEASURED SEPARATELY, never differenced into the pin:
#:   * `main`'s +3 is ONE file — `tests/test_7879_record_chain.py`, added by `#14434` after the
#:     pin was cut: `projection.g.query` +2 (a fake control plane's own accessor) and
#:     `env.update` +1 (a test double's environment) — ordinary-word receivers of exactly the
#:     kind this pin exists to DECLARE rather than rewrite;
#:   * THIS branch's +8, measured per file by running `edits_for` over `origin/main`'s blob and
#:     the branch's for every file the two differ in: `tests/test_14407_alert_delivery.py`
#:     `storage.delete` +4 and `super().delete` +2 (the migration tests drive `AlertStore`'s
#:     delete-to-move legs through a `MemoryStorage` subclass, and the fourth row is the failed
#:     write/read test a peer lane landed in `d45e2362` while this merge ran), and
#:     `tortoise/alert_store.py` `self._storage.delete` +2 (the migration's legacy-object release
#:     and its rollback).
#: Neither side legitimises a typed reference: every added row is an untyped receiver, and
#: `typed references still pointing at a public name: 0` on this tree.
#: `11901 + 8 = 11909` is the CROSS-CHECK of those two measurements, never the source — the pair
#: below is `--repin`'s own output on THIS resolved tree, and `--check` exits 0 on it. ⛔ The merge
#: that brought the peer lane's two commits (`439db940`, `d45e2362`) re-measured BOTH halves rather
#: than adding one row to the last reading: it prints the same `main` tree figure and this branch's
#: +8, because a peer's test is a new blob on the branch side, not a new `main`.
#: ⛔ MERGED `origin/main` `953c299c` (× `da82ece4`, `387daa31`) INTO THIS BRANCH AND RE-CUT ON THE
#: RESOLVED TREE — both sides' blocks above are KEPT, this file's standing ground rule. Here the
#: drift is the OPPOSITE direction from the `43182ac0` fold: `main` has ALREADY re-cut its own pin
#: to `(11901, "953c70c05b55923b")` (the `#14350` D1 lane, `#14423`, cut it on its own merged tree),
#: so this side has no stale pin to repair — what moved is the TREE the two sides resolve to, and
#: the pair below is `--repin`'s own output on it, never arithmetic on either parent's pin.
#: ⛔ THE PAIR DID NOT MOVE ON THIS FOLD, and that is measured rather than assumed: `--repin` on the
#: resolved tree prints the SAME `(11909, "55c8de942f8d2747")` the `43182ac0` fold recorded, even
#: though `#14423` added `tests/test_14350_drain_convergence.py` to the pool in `da82ece4` —
#: because that same commit re-cut `main`'s pin ON ITS OWN MERGED TREE instead of leaving a step of
#: drift, so its rows are already inside the `11901` `main` now carries and the two sides fold onto
#: a tree whose reading is unchanged while the tree itself moved. ⛔ The +4 an earlier draft of this
#: block predicted from the new test file is NOT there, and the prediction is recorded rather than
#: quietly dropped: the number came from `#14423`'s own re-cut, not from a row this branch added.
#: `11909 − 8 = 11901` is the CROSS-CHECK of that reading against `main`'s own pin, never the
#: source. `--check` exits 0 on this tree with
#: `typed references still pointing at a public name: 0`.
#: ⛔ MERGED `origin/main` `f635f9e000` (× `fb3b326f`) INTO THIS BRANCH AND RE-CUT AGAIN ON THE
#: RESOLVED TREE — both sides' blocks above are KEPT, this file's standing ground rule, and this is
#: the fold that finally DID move the pair: `--repin` prints **(11910, "93429504f32ddc7c")**, +1 row
#: over the `11909` the two folds above measured. The +1 is `main`'s, measured rather than
#: inferred from the fold shape: NOTHING on this branch's side changed between the two readings
#: (`git diff` over the branch's own files is empty across it), so the row arrived with `#14428`
#: (the capability-census deletion) / `#14446` (`test_7879_record_chain.py`) and not from this
#: branch. ⛔ ROW-LEVEL ATTRIBUTION OF THE +1 IS NOT CLAIMED, on the same ground the block above
#: already gives for its own `+3`: the committed pin stores a COUNT and a DIGEST, never the row set.
#: `--check` exits 0 on the new pair with
#: `typed references still pointing at a public name: 0`.
#:
#: ⛔ AND A LATER FOLD MOVED IT AGAIN, so the paragraph above is HISTORY with its own
#: tree's reading, not this one's: folding the peer lane's head brought main's `#:`
#: blocks and main's own rows, and `--repin` on the resolved tree prints
#: **(11914, "4732a51728684cca")** — +4 over the `11910` above, +5 over the `11909`
#: before it. The arithmetic is stated only as a CROSS-CHECK against those two
#: measurements, never as the source; each figure is `--repin`'s own output on its own
#: tree, and the pair below is the only one this commit publishes.
#: ⛔ RE-CUT DOWN BY TWO, AND THIS ONE REPAIRS `main` ITSELF — not a branch's residual. `#14446`'
#: (`tests/test_7879_record_chain.py`) squash-merged at `f635f9e000`, and on `origin/main` `989765162f`
#: the pin this paragraph precedes read **(11901, "953c70c05b55923b")** while that tree DERIVES
#: **(11902, "3a694c6f3ff4e36f")**. So `--check` exited 1 on `main`'s OWN tree, and because
#: `surface-guard` is not deferrable that red travelled into the REQUIRED `python-ci-gate` for every
#: open PR — the fleet-wide landing block `#14308` describes, not one PR's inconvenience.
#: BOTH DIRECTIONS OF THE DELTA ARE ATTRIBUTED ROW BY ROW HERE, which the `#14350` blocks above twice
#: state they could not do: they hold a count and a digest, never the rows. Measured with this tool's
#: own `edits_for` on both versions of the one drifted file (`git diff --stat` names no other):
#:   on `main`:  `env.update` 1, `reached.update` 3, `projection.g.query` 0  = 4
#:   this head:  `env.update` 1                                             = 1
#: `#14446`'s item 4 replaced two raw `projection.g.query(...)` Cypher calls with the typed
#: `_label_vector_count` / `_delete_entity_by_id` API (the `#13610` aggregate and the `#3299`/`#3860`
#: label-scoped delete), a genuine SHRINK of −2; its new `_imported_modules` collector added
#: `reached.update` ×3, a genuine +3. Net +1 is the red.
#: ⛔ THE +3 IS FIXED HERE, NOT DECLARED — the tool's own "fix or declare the new shape" arm — and the
#: choice is measured, not assumed: the collector now accumulates with `|=` set comprehensions, which
#: carry no meaning `set.update(...)` did not. Declaring it would have re-pinned `main` UP for drift
#: that is avoidable in three characters of this change's own new code, in a PR whose subject is that
#: a guard was too narrow. A PEER's `.update` receiver is STILL declared, never rewritten away (the
#: `declared_markers.update` and `#13998` test-double precedents above); what decides it is that
#: `reached` is this change's OWN local `set[str]`, not someone else's code being edited to suit a
#: counter. The −2 is NOT undone: the typed rewrite is the change's point, and the census follows it.
#: The pair below is `--repin`'s own output on THIS tree with both edits applied, never arithmetic on
#: the last pin; `--check` exits 0 on it with `typed references still pointing at a public name: 0`.
#: ⛔ AT THE RESOLVED MERGE TREE (this branch × `main` `148dece319`), `--repin` prints
#: `(11903, "df4f73cd9d41cdd1")` — and `main`'s OWN tip derives that same pair against the 11899 pin it
#: ships (`--check` exits 1 there, `observed: (11903, ...)` / `pinned: (11899, ...)`), so this pair
#: repairs `main`'s red as well and this branch contributes **0** rows: its only residue-bearing change
#: is prose plus `tortoise/expansion_merge.py`, whose one dict-literal merge is written to avoid the
#: `x.update` shape. The 11906 above was THIS branch's reading at `88cd00a068`; `#14461` then retired
#: the three `reached.update` rows it had added, which is the 3-row step between 11906 and 11903.
#: ⛔ RE-CUT A FOURTH TIME IN THE `#14419` (B7) LANE'S FOLD, AND THIS ONE IS AGAIN `main`'S OWN RED,
#: MEASURED RATHER THAN ASSUMED. The lane merged `origin/main` `148dece31973` (a descendant of the
#: `88cd00a06879` fold it already carried) and re-measured every tree in its OWN worktree:
#:   * `origin/main` `148dece31973`'s own tree derives **(11903, "df4f73cd9d41cdd1")** against the
#:     **(11899, "543a559f66bc5e86")** committed right above — so `--check` exits 1 on `main` ITSELF, in
#:     a PRISTINE detached worktree (`git status --porcelain` empty, `git ls-files --others` 0), i.e.
#:     not a dirty measurement. That is the `#14062` recurrence a fifth time: `#14461` re-cut this pin
#:     and its own tree is 4 rows above the number it committed. Open roots are `#14294` / `#14308`;
#:     `surface-guard` is not in `DEFERRABLE_LEGS`, so the red travels into the REQUIRED
#:     `python-ci-gate` for EVERY open PR — it is repaired here for that reason, not absorbed.
#:   * THAT LANE'S RESOLVED merge tree derives **11902 / `f904d86c`**, i.e. one row under `main`'s, and the −1
#:     is attributed ROW BY ROW rather than waved through: `--report --base 28ca66878d… --top 5000`
#:     was run on BOTH trees (`origin/main`'s and this one), row sets sorted and diffed, EXACTLY ONE
#:     row differs — `window.update` **1 → 0**. That row lived in the helper `_wall_clock_cap_claims`
#:     (`main:tests/test_ci_selection.py:15998`), which this B7 cut DELETES; the row SHAPE set goes
#:     861 → 860, and no shape is added or absorbed in either direction.
#: ⛔ The one vanishing row is a DELETION this PR declares; the direction the pin exists to catch is a
#: NEW shape nothing accounts for. `typed references still pointing at a public name: 0` on this tree,
#: so no typed call site is legitimised by the re-cut. The pair below is `--repin`'s own output on THIS
#: tree, never arithmetic on the last pin; `--check` exits 0 on it.
#: ⛔ RE-CUT AT THIS LANE'S FOLD OF `origin/main` `e6b3cf2908ba` INTO THE BRANCH — the pair below
#: is `--repin`'s own output on the RESOLVED tree, never inherited from either parent. `main`'s own
#: block above stays (this file's standing rule: both sides' records are kept), and the +10 over
#: `main`'s measurement is attributed ROW BY ROW rather than waved through: the tool's own
#: `--report --base 28ca66878d --top 3000` was run in a detached worktree at `origin/main` AND on
#: this tree, the row sets sorted (860 rows on BOTH sides) and diffed, and EXACTLY FOUR rows differ:
#:   * `graph.query` 281 → 287 (+6)
#:   * `graph.delete` 5 → 7 (+2)
#:   * `proj.g.query` 3651 → 3652 (+1)
#:   * `seen.update` 101 → 102 (+1)
#: The first three are this PR's batch surface and its tests driving a real graph handle, the
#: ordinary-word receiver shape this pin exists to DECLARE; `seen.update` is a Python set's own
#: method, the same class. No shape is added or absorbed in either direction, and `typed references
#: still pointing at a public name: 0` on this tree, so no typed call site is legitimised by the
#: re-cut. The pair below is `--repin`'s own output on THIS tree, never arithmetic on the last pin.
#: ⛔ BOTH SIDES' BLOCKS FROM THE `origin/main` FOLD ARE KEPT above and below this line, this file's
#: standing ground rule (one assignment, both records): `origin/main`'s block recorded `(11910,
#: "adc8229295339239")` on ITS tree, which is not this one, and neither figure is the pin below —
#: the pair published here is `--repin`'s own output on the RESOLVED merge tree, never inherited
#: from either parent and never arithmetic across them. ⛔ THAT MERGE'S TREE IS THAT LANE'S, NOT
#: THIS ONE'S — main's own reading was **(11910, "adc8229295339239")** with `--check` exiting 0 on
#: it, and that record is preserved rather than overwritten.
#: ⛔ THIS FOLD — `origin/main` `ae9bf0f959` (the three commits above `abf5fefeb951`) — RE-CUT ON THE
#: RESOLVED TREE, and the pair below is that reading. The branch and `main` had DIVERGED on this
#: very constant (the branch published `11912`, `main` `11910`), so the conflict this file carried
#: was the pin itself, not the census: a side-picking resolution would have published a number no
#: tree derives, which is the `#14062` recurrence this block records five times.
#: THE +10 OVER `main` IS ATTRIBUTED BY A ROW-SET DIFF, never by arithmetic: `--report --base
#: 28ca66878d --top 5000` was run on THIS resolved tree AND in a pristine detached worktree at
#: `origin/main` `ae9bf0f959`, and the two sorted row sets hold **860 rows on BOTH sides**, of which
#: EXACTLY FOUR differ, all of them this branch's:
#:   * `graph.query` 281 -> 287 (+6)
#:   * `graph.delete` 5 -> 7 (+2)
#:   * `proj.g.query` 3651 -> 3652 (+1)
#:   * `seen.update` 101 -> 102 (+1)
#: 6+2+1+1 = 10 = 11920 - 11910, which is the cross-check that no row was silently absorbed and no
#: new row SHAPE arrived unaccounted for. The first three are this PR's batch surface and its tests
#: driving a real graph handle, the ordinary-word receiver shape this pin exists to DECLARE rather
#: than rewrite; `seen.update` is a Python set's own method, the same class. `typed references still
#: pointing at a public name: 0` on this tree, so no typed call site is legitimised by the re-cut.
#: ⛔ RE-CUT AT THIS LANE'S FOLD OF `origin/main` `59a08fdb8b40` INTO THE BRANCH — the pair below is
#: `--repin`'s own output on the RESOLVED tree, never inherited from either parent and never
#: arithmetic across them. Both parents' records stay (this file's standing rule). The +1 over the
#: branch's own pre-fold pin (`11920`, `4fd188645b9ecef0`) is attributed ROW BY ROW: `--report --base
#: 28ca66878d --top 5000` was run on this tree and in a detached worktree at the branch's pre-fold
#: head `4cc0eff8619b`, the two row sets sorted (860 rows on BOTH sides) and diffed, and EXACTLY ONE
#: row differs:
#:   * `seen.update` 102 → 103 (+1)
#: No row SHAPE is added or absorbed in either direction, and `seen.update` is a Python set's own
#: method — the ordinary-word receiver class this pin DECLARES rather than rewrites. `typed
#: references still pointing at a public name: 0` on this tree, so no typed call site is legitimised
#: by the re-cut.
#: references still pointing at a public name: 0` on this tree, so no typed call site is legitimised
#: by the re-cut.
#: ⛔ BOTH SIDES' BLOCKS FROM THE `origin/main` FOLD ARE KEPT above and below this line, this file's
#: standing ground rule (one assignment, both records). The fold recorded ABOVE is this branch's;
#: the NEXT fold — `origin/main` `59a08fdb8b40` — read **(11910, "adc8229295339239")** on ITS tree,
#: which is not this one, and that record is preserved rather than overwritten.
#: ⛔ THAT MERGE'S TREE IS THAT LANE'S, NOT THIS ONE'S — the pair below is `--repin`'s output HERE.
#: ⛔ THIS PIN WAS RE-DERIVED ON THE **MERGE TREE**,
#: not carried from either side of the `origin/main` integration: `--repin` HERE prints
#: **(11911, "3e92044bab08cc3d")**, which is `main`'s own 11910 plus exactly ONE row — the durable
#: `claim_unavailable` self-heal's `recorded.update(keep)` (`tortoise/capture_spool.py`, the only new
#: untyped `.update(` receiver in this diff vs `origin/main`). `--check` exits 0 on it.
#: ⛔ THIS FOLD — `origin/main` `2cccf90f4dd4` (the two commits above `59a08fdb8b40`) — is RE-CUT ON
#: THE RESOLVED TREE, never inherited from either parent and never arithmetic across them:
#: `--repin` on THIS tree prints **(11922, "97af31f6c4fda3c5")**, which is this branch's own pre-fold
#: 11921 plus exactly the ONE row `main`'s block above names, and NO other row moves — the count a
#: reader can check, not a delta taken on trust. `--check` exits 0 on it.
#: ⛔ KEPT FROM `origin/main` (`6a02515d14ad`) — that side's record, preserved rather than
#: overwritten (this file's standing rule: one assignment, both records). It was derived on ITS
#: tree, not this one, and it is not the publication below.
#: so no typed call site is legitimised by the re-cut. ⛔ THAT TREE IS THAT LANE'S, NOT THIS ONE'S.
#: #4625's own fold then RE-PINNED DOWN 11910 -> **11908** — the pin's HARMLESS direction, because a
#: residue count that FALLS cannot hide a new untyped reference. The two rows that left are the last
#: `_JSONB_CACHE.update({...})` calls in `tortoise/record_projection.py`, deleted with the 5 s `jsonb`
#: TTL this PR retires: the TTL was the only reason that dict was refreshed in place rather than
#: cleared through `_release_schema_cache()` — measured `--repin` on the branch's tree printed
#: `(11908, 'f03fb8e5f6e44a9c')`, no shape added or absorbed.
#: ⛔ THE MERGE WITH `origin/main` MOVED IT BACK UP BY ONE, to **11909 / `1f020f859e887f94`** — the
#: OTHER direction, so it is attributed ROW BY ROW rather than accepted as a flake: `main`'s rewrite
#: of `tests/test_collision_preflight.py` adds exactly one untyped `.update` receiver,
#: `seen.update(kwargs)` in its new `spy()` fixture, and that merge deleted no `.update` line anywhere
#: (`#4625`'s own -2 is still in this tree: `_JSONB_CACHE.update` is 2 rows on `origin/main` and 0
#: here). A rising pin is the direction the guard exists to CATCH, and here the row is main's, so the
#: pin followed `main` — measured `--repin` on that merged tree, never arithmetic on the old number.
#: ⛔ AND THE NEXT FOLD OF `origin/main` RE-DERIVED IT AGAIN RATHER THAN INHERITING EITHER SIDE, because
#: the fold's own tree is a THIRD tree: this merge brings main's durable `claim_unavailable` self-heal,
#: whose `recorded.update(keep)` in `tortoise/capture_spool.py` is one new untyped `.update(`
#: receiver, and `--repin` on THIS resolved tree prints **(11910, "3216395d35345904")**. The -3
#: between that and main's committed 11911 is attributed ROW BY ROW, not waved through:
#:   * `main` `2cccf90f4dd4` pinned 11911 while ITS OWN tree derives **11912** — the missing +1 is
#:     `seen.update(kwargs)` in `tests/test_collision_preflight.py`'s `spy()` fixture, which arrived
#:     with `#14491`'s rewrite and which that re-pin did not count. So `--check` exits 1 on `main`
#:     ITSELF right now (the `#14062` recurrence the blocks above record a sixth time); this branch
#:     inherits main's row set, not main's number.
#:   * `#4625`'s own -2 is the pair of `_JSONB_CACHE.update({...})` rows the retired 5 s `jsonb` TTL
#:     took with it — 2 rows on `origin/main`, 0 here, and the report row sets are otherwise IDENTICAL
#:     (`--report --top 5000` on both trees, 862 shape-carrying lines either side, one differing line).
#: 11912 - 2 = 11910, and the pair below is `--repin`'s own output on THIS tree, never arithmetic; a
#: pin that FALLS cannot hide a new untyped reference, which is the direction this arm exists to catch.
#: ⛔ THIS FOLD — `origin/main` `6a02515d14ad` (#14459/#4625 + #14522) — is RE-CUT ON THE RESOLVED
#: TREE, never inherited from either parent: `--repin` HERE prints **(11920, "932d96bdb7151f75")**.
#: The whole movement against this branch's own published 11922 is -2, attributed ROW BY ROW rather
#: than waved through: the two `_JSONB_CACHE.update({...})` receivers in
#: `tortoise/record_projection.py` that `origin/main`'s #4625 fold deleted with the retired 5 s
#: `jsonb` TTL. MEASURED on both sides of THIS merge — pre-fold HEAD 7ce74ac: 2 rows, `origin/main`
#: 6a02515: 0, merge-base 2cccf90: 2, resolved tree: 0 — so the fold removes exactly those two and
#: adds none. A FALLING pin cannot hide a new untyped reference, which is the direction this arm
#: exists to catch; every other parent record above stays verbatim.
#: ⛔ THIS FOLD — `origin/main` `e66997b81e76` (#14544's DoD-probe cut, #14445's
#: `tools/sdk_privatise.py`-only suite selection, #14540's pool/extent re-cut and #14537's
#: capture-in-flight pin) — is RE-CUT ON THE RESOLVED TREE, never inherited from either parent:
#: `--repin` HERE prints **(11913, "4953069e1b2ae57c")**. The movement against this branch's own
#: published 11920 is -7, and against `main`'s committed 11903 it is +10. ⛔ NEITHER SIDE'S NUMBER
#: IS THIS ONE, which is why the pair is re-derived rather than side-picked: the fold's tree is a
#: THIRD tree. The -7 from the branch is attributed by the fold's own subject rather than waved
#: through — `#14544` CUT `tests/test_dod_5090_probe.py` from 14,426 to 440 lines, deleting the
#: untyped receivers that test carried, and this branch's `batch_ingest.py` contributes none of the
#: census-VISIBLE shapes (#14422: its fifteen statements go through an injected callable, so
#: `self._query(` matches no door predicate). A FALLING pin cannot hide a new untyped reference,
#: which is the direction this arm exists to catch; every parent record above stays verbatim.
#: ⛔ `main`'S OWN RECORDS FROM THE FOLD OF `origin/main` `e66997b81e76`, KEPT VERBATIM —
#: the distinct paragraphs this branch's resolution did not already carry (146 lines, in
#: `main`'s own order, no figure added or moved). The pair below is still `--repin`'s own
#: output on THIS resolved tree; these lines are the parent's provenance for it.
#: ⚠️ THIS PR (#14480) MOVED THE PIN BY ONE FROM ITS OWN LINE — stated because the pin's own note
#: says a count that moves without a named cause is the drift it exists to catch: the durable
#: `claim_unavailable` record writes `recorded.update(keep)` in `capture_spool`'s self-heal
#: (`tortoise/capture_spool.py:4107`, the one new untyped `.update(` receiver in this diff), so
#: `--repin` on the tree being merged prints **(11911, "3e92044bab08cc3d")**. Re-derived by RUNNING
#: the tool on that tree, never by arithmetic on the delta. ⛔ THAT PAIR IS `#14480`'s MERGE TREE, NOT
#: THIS ONE: after `#14445`'s re-cut and this fold of `origin/main` `2cccf90f4dd4`, `--repin` here
#: prints (11912, "73827c80583e139f") — see the pin's own note below, which owns this tree's value.
#: ⛔ RE-PINNED FROM `main`'S DRIFT BY THE `#14342 D1` LANE (#14350, PR #14423), and THIS BRANCH'S
#: OWN CONTRIBUTION IS ZERO ROWS — measured on three trees, not inferred from a difference.
#: That lane merges `origin/main` `0fbfbb5b210c` into `fix/14342-d1-drain`, re-cuts the pin on the
#: resolved tree with `--repin`, and reads **(11898, "8cf59d52af09e536")**. The drift is NOT this
#: branch's: the SAME observation — observed `(11898, "8cf59d52af09e536")` against a pinned
#: `(11896, "ea44c5f732d2a97d")` — is printed by `--check` on
#:   * `origin/main` `0fbfbb5b210c`'s OWN tree (detached worktree), i.e. `main` is red here itself;
#:   * `1457f7905d`'s OWN tree, the `#14038` squash-merge that CUT the `11896` — so the pair it
#:     committed is already one step behind the tree it commits, and no later `main` commit moved it;
#: and this branch adds none of it: its three new files (`tortoise/drain_claim.py`,
#: `tools/measure_drain_convergence.py`, `tests/test_14350_drain_convergence.py`) carry no
#: `.query` / `.update` / `.delete` reference AT ALL (`grep -nE '\.(query|update|delete)\b'` → no
#: match on all three), and the branch's other edits are inside files that already carry their rows.
#: ⛔ RE-PINNED A SECOND TIME BY THE SAME LANE (#14423), AND AGAIN FROM `main`'S DRIFT — the
#: observation is **(11901, "953c70c05b55923b")**, +3 rows and a new digest, measured after this
#: branch merged `origin/main` `b2d1e9086a` (the `tests/test_7879…` / `#14371 offline-embed` pair).
#: The same three-tree rule as the `11898` block above, re-measured rather than assumed: a detached
#: worktree at `origin/main`'s OWN `43182ac09a` reports the IDENTICAL
#: `observed: (11901, '953c70c05b55923b')` against the old `11898` pin — so the drift is `main`'s,
#: this branch contributes no row, and the branch's own three new files still carry no
#: `.query`/`.update`/`.delete` reference at all. ⛔ ROW-LEVEL ATTRIBUTION OF THE +3 IS NOT CLAIMED
#: here for the reason the block above already gives: the committed pin stores a COUNT and a DIGEST,
#: never the row set, so which of `main`'s new files added them cannot be recovered from the
#: artifact — only re-measured against a tree that predates it, which is what the `43182ac09a`
#: measurement IS. `--check` is green on the new pair with
#: ⛔ THE +2 IS THEREFORE NOT ATTRIBUTED ROW BY ROW HERE, AND THAT IS STATED RATHER THAN HIDDEN: the
#: committed pin stores a COUNT and a DIGEST, never the row set, so the rows `#14038` moved cannot be
#: recovered from the artifact — only re-measured against a tree that predates it. What IS measured
#: is that the reading is identical on `main`'s own tip and on `#14038`'s own tree, which is what
#: makes this a repair of `main`'s red rather than a declaration of this branch's rows. The open
#: roots are `#14308` and `#14294` ("`main` is red on `test (c)`, `test-slow (a)` and
#: `surface-guard` — three stale-guard roots every PR reproduces"); this block is the same class, a
#: pin one step behind the tree, and the evidence above is posted there rather than filed as a peer.
#: ⛔ `surface-guard` is NOT in `DEFERRABLE_LEGS`, so its red travels into the REQUIRED
#: `python-ci-gate` for EVERY open PR — leaving it is a fleet-wide landing block, not this PR's
#: inconvenience. `--check` exits 0 on the pair below with
#: ⛔ RE-CUT AGAIN on a later fold of `origin/main`, and the +3 is `main`'s for the SAME
#: reason: `--repin` on the resolved tree prints **11901 / `953c70c05b55923b`** where the
#: pin said 11898, and a detached worktree at `origin/main` measures the IDENTICAL pair
#: (11901 / `953c70c0`), so this branch contributes zero rows and the drift arrived on
#: `main` between the two folds — the same class as the paragraph above, and the same
#: repair (a tree that does not match its pin reds every PR that merges it).
#: so no typed call site is legitimised by the re-cut.
#: ⛔ KEPT VERBATIM, AND IT IS **NOT** THIS LANE'S: it is the `#14480` lane's own re-derivation on
#: ITS merge of `origin/main`, and the only tree that reproduces it is `05c46e24a2782` (measured —
#: `--repin` there prints the pair below and `--check` exits 0). It reached this branch through the
#: `a2f165b0ba47` fold, whose own tree derives 11912 and NOT this pair. ⛔ THIS PIN WAS RE-DERIVED ON
#: THE **MERGE TREE**, not carried from either side of the `origin/main` integration: `--repin` HERE
#: prints **(11911, "3e92044bab08cc3d")**, which is `main`'s own 11910 plus exactly ONE row — the durable
#: `claim_unavailable` self-heal's `recorded.update(keep)` (`tortoise/capture_spool.py`, the only new
#: untyped `.update(` receiver in this diff vs `origin/main`). `--check` exits 0 on it.
#: ⛔ A FIFTH FOLD KEEPS BOTH SIDES' BLOCKS AGAIN (`origin/main` `2cccf90f4dd4`, this merge) and
#: re-derives the pair with `--repin` on THIS resolved tree: neither block above is inherited as the
#: pin and no arithmetic crosses them. The pair below is `--repin`'s own output on the tree that
#: SHIPS; `--check` exits 0 on it.
#:
#: ⛔ #14445 RE-CUT IT ON `main`'s BEHALF — a deliberate re-pin, `--repin`'s own output and never
#: arithmetic on the last one. `main` pinned (11910, "adc8229295339239") while its OWN tree derives
#: (11911, "4ed42694957a174d") at `59a08fdb8b40`, so the required `surface-guard` leg was red ON
#: `main` and every open PR inherited that red — the #14062 class this pin has recurred in before.
#: The new reading is ONE ROW, `seen.update` **101 -> 102**, a COUNT and not a shape: the row sets
#: were diffed with `--report --base 28ca66878d… --top 5000` on both trees and 860 shapes are on
#: each. The row arrived with `#14506` on `main`: a `seen: dict[str, object] = {}` accumulator's
#: `.update(kwargs)` inside a `subprocess.Popen` spy — the ordinary-word receiver class this pin
#: REPORTS rather than refuses. `typed references still pointing at a public name: 0` on this tree,
#: so no typed call site is legitimised by this re-cut. ⛔ THAT TREE IS `main`'s, NOT THIS ONE'S.
#: ⛔ RE-CUT ONCE MORE ON `main`'s BEHALF, AND
#: BOTH TREES WERE MEASURED RATHER THAN THE DELTA ASSUMED. `main` pins (11911, "3e92044bab08cc3d")
#: while its OWN tree derives (11912, "73827c80583e139f"): `--check` exits 1 in a PRISTINE detached
#: worktree at `2cccf90f4dd4` (`git status --porcelain` empty, `git ls-files --others` 0), so the
#: required `surface-guard` leg was red ON `main` and every open PR inherited that red — the #14062
#: class, again, and repaired here for that reason rather than absorbed.
#: ⛔ THIS PR ADDS NO ROW, AND THAT IS THE MEASUREMENT, NOT A CLAIM: `--repin` on THIS merge tree
#: prints the SAME pair, (11912, "73827c80583e139f") — identical COUNT and identical DIGEST, so no
#: row is attributable to this diff and the pin moves only because a red on `main` blocks every PR.
#: ⛔ `main`'s own 11911 → 11912 movement is NOT attributed row-by-row here: the earlier re-cut note
#: on `main`'s behalf (`seen.update` 101 → 102, arrived with `#14506`) is THAT commit's record of
#: `main`'s 11910 → 11911 movement, not a claim about this diff, and it was not re-measured here.
#: `typed references still pointing at a public name: 0` and `--check` exits 0 on the pair below.
#:   ⛔ THE ASSIGNMENT THAT CLOSED THAT PARAGRAPH ON ITS OWN HEAD READ
#:   `SWEEP_RESIDUE = (11912, "73827c80583e139f")`, and it survives here as PROSE rather than as a
#:   second assignment: a merge brings both sides' assignments in, and this file carries exactly
#:   ONE — the LAST binds while a first-anchored walk grades only the prose above the FIRST.
#: ⛔ `#4625`'s OWN FOLD THEN RE-PINNED IT DOWN 11910 -> **11908** — the pin's HARMLESS direction,
#: because a residue count that FALLS cannot hide a new untyped reference. The two rows that left are
#: the last `_JSONB_CACHE.update({...})` calls in `tortoise/record_projection.py`, deleted with the
#: 5 s `jsonb` TTL that PR retires: the TTL was the only reason that dict was refreshed in place
#: rather than cleared through `_release_schema_cache()` — measured `--repin` on that branch's tree
#: printed `(11908, 'f03fb8e5f6e44a9c')`, no shape added or absorbed.
#: ⛔ THAT PR'S MERGE WITH ITS OWN `origin/main` MOVED IT BACK UP BY ONE, to
#: **11909 / `1f020f859e887f94`** — the OTHER direction, so it is attributed ROW BY ROW rather than
#: accepted as a flake: `main`'s rewrite of `tests/test_collision_preflight.py` adds exactly one
#: untyped `.update` receiver, `seen.update(kwargs)` in its new `spy()` fixture, and that merge
#: deleted no `.update` line anywhere (`#4625`'s own -2 is still in that tree: `_JSONB_CACHE.update`
#: is 2 rows on `origin/main` and 0 there). A rising pin is the direction the guard exists to CATCH,
#: and there the row is main's, so the pin followed `main` — measured `--repin` on that merged tree,
#: never arithmetic on the old number.
#: ⛔ AND THE NEXT FOLD OF `origin/main` RE-DERIVED IT AGAIN RATHER THAN INHERITING EITHER SIDE,
#: because the fold's own tree is a THIRD tree: that merge brought main's durable
#: `claim_unavailable` self-heal, whose `recorded.update(keep)` in `tortoise/capture_spool.py` is one
#: new untyped `.update(` receiver, and `--repin` on THAT resolved tree printed
#: **(11910, "3216395d35345904")**. The -3 between that and that `main`'s committed 11911 is
#: attributed ROW BY ROW, not waved through:
#:   * that `main` `2cccf90f4dd4` pinned 11911 while ITS OWN tree derives **11912** — the missing +1
#:     is `seen.update(kwargs)` in `tests/test_collision_preflight.py`'s `spy()` fixture, which
#:     arrived with `#14491`'s rewrite and which that re-pin did not count. So `--check` exited 1 on
#:     that `main` ITSELF (the `#14062` recurrence the blocks above record a sixth time); this
#:     branch inherits main's row set, not main's number.
#:     took with it — 2 rows on that `origin/main`, 0 there, and the report row sets are otherwise
#:     IDENTICAL (`--report --top 5000` on both trees, 862 shape-carrying lines either side, one
#:     differing line). 11912 - 2 = 11910, and that pair was `--repin`'s own output on that tree, never
#:     arithmetic; a pin that FALLS cannot hide a new untyped reference, the direction this arm catches.
#:   ⛔ THAT PARAGRAPH'S ASSIGNMENT READ `SWEEP_RESIDUE = (11910, "3216395d35345904")` ON THAT HEAD,
#:   kept as prose here for the same one-assignment reason, and ⛔ ITS PAIR IS `#4625`'S MERGE TREE,
#:   NOT THIS ONE'S.
#: ⛔ MERGED `origin/main` `6a02515d14ad` INTO THIS BRANCH — the PR was `CONFLICTING`, and the two
#: conflicted files are exactly the ones this pin lives in (`tools/sdk_privatise.py` and its grading
#: suite): each side had appended its own re-cut record above the SAME `SWEEP_RESIDUE` assignment.
#: BOTH SIDES' BLOCKS ABOVE ARE KEPT VERBATIM, which is this block's ground rule: each is a true
#: measurement of the tree it NAMES, so neither side is dropped to make a conflict go away — and the
#: one line that CANNOT be kept twice is the assignment itself, because a second one binds silently
#: while the walk still grades the first.
#: ⛔ THE PIN IS THEN RE-MEASURED ON THE RESOLVED TREE: a merge tree is a THIRD tree, so neither
#: parent's numeral is inherited, and `--repin` HERE is the only thing that reads it. It prints
#: **(11910, "3216395d35345904")** with `typed references still pointing at a public name: 0`, and
#: `--check` exits 0 on it. ⚠️ THAT IS BYTE-IDENTICAL TO THE PAIR `main` COMMITS, and the agreement
#: is a MEASUREMENT of two trees rather than a carry: this fold adds NO row, so the matching numeral
#: must not be read as confirmation of anything this branch contributes.
#: ⛔ THE 2-ROW GAP BETWEEN THIS PAIR AND THE BRANCH'S OWN PRE-MERGE 11912 IS ATTRIBUTED ROW BY ROW,
#: not waved through: the branch's pre-merge tree still carried the two `_JSONB_CACHE.update({...})`
#: rows `#4625` retires (measured `git grep -c '_JSONB_CACHE.update'` → **2** on that tree, **0** on
#: `origin/main`), which is exactly the pair the block above already accounts for. Adopting main's
#: deletion is what makes this tree read main's count; nothing fell unaccounted in either direction.
#: ⛔ A SIXTH FOLD KEEPS BOTH SIDES' BLOCKS AGAIN (`origin/main` `533e256f2c89`, this merge) and
#: re-derives the pair with `--repin` on THIS resolved tree: neither block above is inherited as the
#: pin and no arithmetic crosses them. `main` ships (11910, "3216395d35345904") and its OWN tree
#: derived (11912, "73827c80583e139f") two folds ago; the pair below is `--repin`'s own output on the
#: tree that SHIPS, and `--check` exits 0 on it.
#:
#: ⛔ RE-PINNED DOWN 11910 -> 11903 ON THE `#14544` CUT, AND THE 7 IS ATTRIBUTED ROW BY ROW. That
#: PR deletes 270 cases from `tests/test_dod_5090_probe.py`, and `--repin` on THIS tree prints
#: **(11903, "1fb861e2c8163aec")**. Measured with this module's own `edits_for`, not reasoned from
#: the diff: the pre-cut file carried **8** residue rows (`graph.query` x3, `declared.update`,
#: `se._BREAKERS.update`, `db.list_graphs`, `degraded.update`, `graph.delete`) and the surviving
#: 677-line file keeps **1** (`se._BREAKERS.update`), so 8 - 1 = 7 is exactly the movement — and no
#: other file in that PR touches a `.query`/`.update`/`.delete` receiver.
#: ⚠️ A FALLING PIN CANNOT HIDE A NEW UNTYPED REFERENCE, the direction this arm catches, so the fall
#: is the pin's HARMLESS direction and the row set is otherwise identical. It is re-pinned rather
#: than left red because that PR moves the very tree this pin grades, and `surface-guard` is a
#: code-measuring check on the PR's EVALUATED tree that the land rail refuses to merge red,
#: required or not (`scripts/admin-merge.sh`, "the PR's evaluated-tree surface BLOCKS").
#: ⛔ A SEVENTH FOLD KEEPS BOTH SIDES' BLOCKS AGAIN (`origin/main` `af40c97ca31`, this merge) and
#: re-derives the pair with `--repin` on THIS resolved tree: neither block above is inherited as the
#: pin and no arithmetic crosses them. The pair below is `--repin`'s own output on the tree that
#: SHIPS; `--check` exits 0 on it.
#: ⛔ AN EIGHTH FOLD KEEPS BOTH SIDES' BLOCKS AGAIN (`origin/main` `e627ca89375`, this merge) and
#: re-derives the pair with `--repin` on THIS resolved tree: neither block above is inherited as the
#: pin and no arithmetic crosses them. The pair below is `--repin`'s own output on the tree that
#: SHIPS, and it reproduces `main`'s literal BIT-FOR-BIT (same count AND same digest) even though the
#: tree carries this branch's `tortoise/expansion_merge.py` — the cross-check that this branch
#: contributes no residue row, because that module's one dict-literal merge is written with `{**a,
#: **b}` precisely to avoid the `x.update` shape these pins count. `--check` exits 0 on it.
SWEEP_RESIDUE = (11913, '4953069e1b2ae57c')
#: A name that says "this is the SDK" in its own spelling. Deliberately a WORD match, so
#: `sdk`, `sdk2`, `org_sdk`, `_get_org_sdk` and `sdk_factory` all qualify while `sudoku` —
#: and, importantly, `queue`-shaped accidental substrings — do not.
_SDKISH = re.compile(r"(?i)(?:^|_)sdk\d*(?:_|$)")


#: The declaration module, read BY PATH. ⛔ Never import it as `tortoise.__all__`: a dotted
#: import SETS the `__all__` attribute on the `tortoise` package, and from then on
#: `from tortoise import *` raises `TypeError: 'module' object is not iterable` for the rest of
#: the process — in a pytest shard that runs this file BEFORE the declaration's own suite, it is
#: `test_loading_the_declaration_by_path_does_not_shadow_star_import` that reds, and it is right
#: to red: the pin exists to catch exactly this import shape.
DECLARATION = ROOT / "tortoise" / "__all__.py"


def approved_methods() -> set[str]:
    """The owner-approved set, read from the declaration — never re-typed here."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("_sweep_approved_declaration", DECLARATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return set(module.__all__) - {"Tortoise"}


def _sdk_source(rev: str | None = None) -> str:
    """`tortoise/sdk.py` at `rev` (a git revision) or the working tree when `rev` is None."""
    if rev is None:
        return SDK_SRC.read_text(encoding="utf-8")
    out = subprocess.run(
        ["git", "show", f"{rev}:tortoise/sdk.py"], cwd=ROOT, capture_output=True, text=True
    )
    if out.returncode != 0:
        raise SystemExit(f"cannot read tortoise/sdk.py at {rev!r}: {out.stderr.strip()}")
    return out.stdout


def class_body(src: str | None = None) -> ast.ClassDef:
    tree = ast.parse(src if src is not None else self_source())
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "TortoiseSDK":
            return node
    raise SystemExit("no top-level `TortoiseSDK` class in tortoise/sdk.py")


def self_source() -> str:
    return SDK_SRC.read_text(encoding="utf-8")


def class_bindings(src: str | None = None) -> dict[str, str]:
    """EVERY name bound in the class body, mapped to its kind.

    Any binding kind counts, not just `def`: a `@property`, a class-level assignment or a
    nested class called `_query` collides with a `_query` method just as hard, and checking
    only functions would let exactly that through.
    """
    out: dict[str, str] = {}
    for node in class_body(src).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = "def"
        elif isinstance(node, ast.ClassDef):
            out[node.name] = "class"
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out[node.target.id] = "annassign"
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = "assign"
    return out


def off_surface_names(src: str | None = None) -> list[str]:
    """Public methods on `TortoiseSDK` that are NOT on the approved surface."""
    approved = approved_methods()
    public = {
        n.name
        for n in class_body(src).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("_")
    }
    return sorted(public - approved)


@dataclass(frozen=True)
class Entry:
    name: str
    private: str
    collided: bool


def rename_map(rev: str | None = None) -> list[Entry]:
    """The map: off-surface name -> the private spelling it takes.

    The collision arm is the whole reason this is derived rather than typed: `audit` and
    `delete_entity` already own `_audit` / `_delete_entity` on this class, and a naive rename
    would shadow them.

    `rev` reads the class at a git revision — the map the sweep was APPLIED with. After
    `--apply` the live class no longer holds the names, so `--check` MUST be given the base
    or it derives an empty map and reports a vacuous zero.
    """
    src = _sdk_source(rev)
    bindings = class_bindings(src)
    out = []
    for name in off_surface_names(src):
        plain = "_" + name
        if plain in bindings:
            out.append(Entry(name, plain + COLLISION_SUFFIX, True))
        else:
            out.append(Entry(name, plain, False))
    return out


def py_files() -> list[Path]:
    tracked = subprocess.run(
        ["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True
    ).stdout.split()
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "*.py"],
        cwd=ROOT, capture_output=True, text=True,
    ).stdout.split()
    out = []
    for f in tracked + untracked:
        if f.startswith(("archive/", ".venv/", ".worktrees/")):
            continue
        p = ROOT / f
        if p.is_file():
            out.append(p)
    return sorted(set(out))


def residue_digest(rows: Counter) -> str:
    """A stable digest of the residue SET (row -> count), for the `SWEEP_RESIDUE` pin."""
    text = "\n".join(f"{row}\t{rows[row]}" for row in sorted(rows))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Call):
        fn = node.func
        return fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
    return ""


def _annotation_is_sdk(node: ast.expr | None) -> bool:
    if node is None:
        return False
    text = ast.unparse(node)
    return "TortoiseSDK" in text


def _is_sdk_value(value: ast.expr | None) -> bool:
    """Is this expression a value an SDK is built from? Evidence from THIS file only.

    The arm is the CALL's own name — `TortoiseSDK(...)`, `_get_sdk()`, `_registry_sdk()`.
    """
    if not isinstance(value, ast.Call):
        return False
    return _SDKISH.search(_call_name(value)) is not None or _call_name(value) in {
        "TortoiseSDK", "Tortoise",
    }


def _scope_body(node: ast.AST) -> list[ast.AST]:
    """Every node belonging to THIS scope — nested scopes are excluded, they bind their own."""
    out: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(node))
    while stack:
        n = stack.pop()
        out.append(n)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(n))
    return out


_SCOPE_NODES = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass(frozen=True)
class Scope:
    """One lexical scope, and what ITS OWN bindings prove about the names it binds."""

    lo: int
    hi: int
    typed: frozenset[str]
    untyped: frozenset[str]


def _scope_bindings(node: ast.AST) -> tuple[set[str], set[str]]:
    """The (typed, untyped) names this scope's OWN statements bind.

    Every arm is evidence from THIS file, never a guess about what a name probably means:
      * `X = TortoiseSDK(...)` / `X = <a name that says sdk>(...)`;
      * `X: TortoiseSDK = ...`, and a parameter annotated `TortoiseSDK`;
      * `with <sdk-ish>() as X:`.
    """
    typed: set[str] = set()
    untyped: set[str] = set()

    def bind(name: str, is_typed: bool) -> None:
        (typed if is_typed else untyped).add(name)

    for n in _scope_body(node):
        if isinstance(n, ast.Assign):
            for target in n.targets:
                if isinstance(target, ast.Name):
                    bind(target.id, _is_sdk_value(n.value))
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            bind(n.target.id, _annotation_is_sdk(n.annotation) or _is_sdk_value(n.value))
        elif isinstance(n, (ast.With, ast.AsyncWith)):
            for item in n.items:
                if isinstance(item.optional_vars, ast.Name):
                    bind(item.optional_vars.id, _is_sdk_value(item.context_expr))
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        args = node.args
        for arg in (*args.args, *args.kwonlyargs, *args.posonlyargs):
            if _annotation_is_sdk(arg.annotation):
                bind(arg.arg, True)
    # A name bound BOTH ways in ONE scope is ambiguous and is NOT rewritten. This is the
    # `#13899` F2 lesson, measured in `tortoise/hosted_api.py`: `reg` is a real SDK in one
    # function (`reg = _registry_sdk()`) and a FalkorDB handle in the next
    # (`reg = sdk._get_registry()`), and a FILE-WIDE set rewrote 80 graph calls into
    # `reg._query(` — a dependency on a vendor private method. Scope, then ambiguity.
    return typed - untyped, untyped


class Typing:
    """Scope-resolved SDK evidence for a module: which local names carry the surface, WHERE."""

    def __init__(self, scopes: list[Scope]) -> None:
        # Innermost FIRST: the smallest span containing the line decides.
        self._scopes = sorted(scopes, key=lambda s: (s.hi - s.lo, s.lo))
        typed: set[str] = set()
        for s in scopes:
            typed |= s.typed
        self._any_typed = typed

    def typed(self, name: str, line: int) -> bool:
        """Does the NEAREST enclosing scope that binds `name` type it as SDK?"""
        for s in self._scopes:
            if not (s.lo <= line <= s.hi):
                continue
            if name in s.typed:
                return True
            if name in s.untyped:
                return False
        return name in self._any_typed

    def typed_anywhere(self, name: str) -> bool:
        """For a name reached through an ATTRIBUTE (`mod.fixture.method`): the receiver names
        the module, not the scope, so the nearest-scope rule cannot decide it."""
        return name in self._any_typed


def sdk_typing(tree: ast.Module) -> Typing:
    """The scope-resolved typing for one module, fixtures included.

    A `@pytest.fixture` whose RETURN or YIELD is an SDK value types the FIXTURE'S NAME in the
    scope the fixture is defined in — the name its consumers reach it by. ⛔ RETURN/YIELD
    only: an earlier attempt typed a fixture from any mention of `TortoiseSDK(` in its body,
    which typed a FastAPI `TestClient` fixture that merely built an SDK inside itself and
    rewrote `client.delete(` into `client._delete(` — a false positive this arm exists to make
    impossible.
    """
    parents: dict[int, ast.AST] = {}
    scope_nodes: list[ast.AST] = []
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
        if isinstance(node, _SCOPE_NODES):
            scope_nodes.append(node)
    bindings: dict[int, tuple[set[str], set[str]]] = {
        id(n): _scope_bindings(n) for n in scope_nodes
    }
    for node in scope_nodes:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any(
            (getattr(d, "attr", None) or getattr(d, "id", None)) == "fixture"
            for d in node.decorator_list
        ):
            continue
        own_typed = bindings[id(node)][0]
        if not any(
            isinstance(sub, (ast.Return, ast.Yield))
            and (
                _is_sdk_value(sub.value)
                or (isinstance(sub.value, ast.Name) and sub.value.id in own_typed)
            )
            for sub in _scope_body(node)
        ):
            continue
        owner = node
        while not isinstance(parents.get(id(owner)), _SCOPE_NODES) and id(owner) in parents:
            owner = parents[id(owner)]
        enclosing = parents.get(id(owner))
        if enclosing is None or id(enclosing) not in bindings:
            continue
        typed, untyped = bindings[id(enclosing)]
        typed.add(node.name)
        untyped.discard(node.name)
        bindings[id(enclosing)] = (typed, untyped)
    scopes = []
    for n in scope_nodes:
        lo = 1 if isinstance(n, ast.Module) else n.lineno
        hi = getattr(n, "end_lineno", None) or lo
        typed, untyped = bindings[id(n)]
        scopes.append(Scope(lo, hi, frozenset(typed), frozenset(untyped)))
    return Typing(scopes)


def sdk_standin_classes(tree: ast.Module, rmap: dict[str, str]) -> set[str]:
    """Classes that STAND IN for the SDK — they implement the APPROVED surface.

    This is deliberately stricter than "defines several off-surface names": a FalkorDB
    handle, a registry double and a mock HTTP client all answer to `query` / `delete` /
    `update` — the same WORDS, a different contract. Production code reaches a stand-in
    through the private spelling, so its `def`s move; those other objects must not. The
    approved-surface requirement is the discriminator, because a real stand-in implements
    the verbs production actually calls and a `g.query` mock implements none of them.
    """
    approved = approved_methods()
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if any(
            (isinstance(b, ast.Name) and b.id == "TortoiseSDK")
            or (isinstance(b, ast.Attribute) and b.attr == "TortoiseSDK")
            for b in node.bases
        ):
            out.add(node.name)
            continue
        names = {
            n.name
            for n in node.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        if len(names & approved) >= 2:
            out.add(node.name)
    return out


def receiver_is_sdk(
    recv: ast.expr, typing: Typing, standins: set[str], in_sdk_class: bool, line: int
) -> bool:
    """Is this receiver something that carries the SDK surface? Evidence only, never a guess."""
    if isinstance(recv, ast.Name):
        if recv.id == "self":
            # `self` is typed ONLY inside the `TortoiseSDK` class body. Typing every
            # `self.x.<name>` renamed `self._dirty_roots.update()` into `._update()`.
            return in_sdk_class
        if recv.id == "TortoiseSDK":
            return True
        return (
            bool(_SDKISH.search(recv.id))
            or typing.typed(recv.id, line)
            or recv.id in standins
        )
    if isinstance(recv, ast.Attribute):
        if recv.attr == "TortoiseSDK" or _SDKISH.search(recv.attr):
            return True
        return isinstance(recv.value, ast.Name) and typing.typed_anywhere(recv.attr)
    if isinstance(recv, ast.Call):
        name = _call_name(recv)
        return name == "TortoiseSDK" or bool(_SDKISH.search(name)) or typing.typed_anywhere(name)
    if isinstance(recv, ast.Subscript):
        # `sdk["x"].method` — typed by what is being subscripted.
        return receiver_is_sdk(recv.value, typing, standins, in_sdk_class, line)
    return False


def _class_ranges(tree: ast.Module) -> list[tuple[int, int, str]]:
    return [
        (n.lineno, n.end_lineno or n.lineno, n.name)
        for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef)
    ]


def _in_sdk_class(line: int, ranges: list[tuple[int, int, str]]) -> bool:
    return any(lo <= line <= hi and nm == "TortoiseSDK" for lo, hi, nm in ranges)


def _def_name_span(path: Path, node: ast.AST, name: str) -> tuple[int, int] | None:
    """The (start, end) columns of the NAME in `def <name>(`, read from the source line.

    ⛔ NOT `col_offset + 4`: for an `AsyncFunctionDef`, `col_offset` points at `async`, so
    offset arithmetic writes `asyn_deletedelete`, a syntax error the sweep then cannot see
    because the file no longer parses.
    """
    line = path.read_text(encoding="utf-8").splitlines()[node.lineno - 1]
    m = re.search(rf"\bdef\s+({re.escape(name)})\b", line[node.col_offset:])
    if not m:
        return None
    start = node.col_offset + m.start(1)
    return start, start + len(name)


@dataclass
class FileEdits:
    attrs: list[tuple[int, int, int, str]]
    defs: list[tuple[int, int, int, str]]
    strings: list[tuple[int, int, int, str]]
    residue: Counter


#: A string literal naming one of the methods: `"query"`, `"sdk.query"`,
#: `"tortoise.sdk.TortoiseSDK.query"`. Swept only when the surrounding call makes the SDK
#: unambiguous (`getattr(<typed>, …)`, `setattr(<typed>, …)`) or when the literal itself names
#: the class (`TortoiseSDK.<name>` / `tortoise.sdk.TortoiseSDK.<name>`).
_STRING_CLASS_REF = re.compile(r"(?:^|\.)TortoiseSDK\.([A-Za-z_][A-Za-z0-9_]*)$")
_STRING_PLAIN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)$")


def edits_for(path: Path, rmap: dict[str, str]) -> FileEdits:
    src = path.read_text(encoding="utf-8")
    # SOUND, not a heuristic: an attribute, `def` or string reference to a name cannot exist
    # without that name's literal text in the file, so a file containing none of them has no
    # edit and no residue. It is what keeps `--check` a gate people will actually run.
    if not any(name in src for name in rmap):
        return FileEdits([], [], [], Counter())
    try:
        with warnings.catch_warnings():
            # These files are READ, never run: a `\s`-in-docstring warning from someone else's
            # module is not this gate's business, and it buries the verdict line.
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(src)
    except SyntaxError:
        return FileEdits([], [], [], Counter({"<<syntax-error>>": 1}))
    in_sdk = path.resolve() == SDK_SRC.resolve()
    standins = sdk_standin_classes(tree, rmap)
    typing = sdk_typing(tree)
    ranges = _class_ranges(tree)
    lines = src.splitlines()
    attrs: list[tuple[int, int, int, str]] = []
    defs: list[tuple[int, int, int, str]] = []
    strings: list[tuple[int, int, int, str]] = []
    residue: Counter = Counter()

    for node in ast.walk(tree):
        # ── attribute accesses ────────────────────────────────────────────────
        if isinstance(node, ast.Attribute) and node.attr in rmap:
            in_class = in_sdk and _in_sdk_class(node.lineno, ranges)
            if not receiver_is_sdk(node.value, typing, standins, in_class, node.lineno):
                residue[f"{ast.unparse(node.value)[:40]}.{node.attr}"] += 1
                continue
            if node.value.end_lineno != node.end_lineno:
                residue[f"{node.attr} (multiline receiver)"] += 1
                continue
            start = node.value.end_col_offset  # EXCLUSIVE: this is the '.'
            end = node.end_col_offset
            if lines[node.lineno - 1][start:end] != "." + node.attr:
                residue[f"{node.attr} (unexpected span)"] += 1
                continue
            attrs.append((node.lineno, start, end, "." + rmap[node.attr]))
            continue

        # ── string-shaped references ──────────────────────────────────────────
        if isinstance(node, ast.Call):
            fn = _call_name(node)
            # A DECLARED BINDING: `sdk_method="create_point"` in `tool_registry.py` is
            # documented as "Attribute name on TortoiseSDK", so it moves with the
            # attribute. It is not an attribute access and no parenthesis search sees it.
            for kw in node.keywords:
                if kw.arg != "sdk_method":
                    continue
                lit = kw.value
                if isinstance(lit, ast.Constant) and isinstance(lit.value, str):
                    m = _STRING_PLAIN.match(lit.value)
                    if m and m.group(1) in rmap:
                        strings.append(
                            (lit.lineno, lit.col_offset + 1,
                             lit.col_offset + 1 + len(lit.value), rmap[m.group(1)])
                        )
            if fn in {"getattr", "setattr", "delattr", "hasattr"} and len(node.args) >= 2:
                target, literal = node.args[0], node.args[1]
                if isinstance(literal, ast.Constant) and isinstance(literal.value, str):
                    m = _STRING_PLAIN.match(literal.value)
                    if m and m.group(1) in rmap and receiver_is_sdk(
                        target, typing, standins, False, literal.lineno
                    ):
                        strings.append(
                            (literal.lineno, literal.col_offset + 1,
                             literal.col_offset + 1 + len(literal.value), rmap[m.group(1)])
                        )
                        continue
                    m2 = _STRING_CLASS_REF.match(literal.value)
                    if m2 and m2.group(1) in rmap:
                        new = literal.value[: m2.start(1)] + rmap[m2.group(1)]
                        strings.append(
                            (literal.lineno, literal.col_offset + 1,
                             literal.col_offset + 1 + len(literal.value), new)
                        )
                        continue
                    if m and m.group(1) in rmap:
                        residue[f"string {literal.value!r} on untyped receiver"] += 1

        # ── stand-in `def`s ───────────────────────────────────────────────────
        if isinstance(node, ast.ClassDef) and node.name in standins:
            # ⛔ `TortoiseSDK` is itself a stand-in by the approved-surface rule, and the
            # class-body block at the end of this function emits its `def`s. Emitting them
            # here TOO puts two edits on the SAME span, and applying both mangles the name
            # (`def query` -> `def _queryy`) because the second one's columns were computed
            # against the pre-first-edit line. One owner per span.
            if node.name == "TortoiseSDK":
                continue
            for m in node.body:
                if (
                    isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and m.name in rmap
                ):
                    span = _def_name_span(path, m, m.name)
                    if span is None:
                        residue[f"def {m.name} (span not located)"] += 1
                        continue
                    defs.append((m.lineno, span[0], span[1], rmap[m.name]))

    if in_sdk:
        for m in class_body().body:
            if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name in rmap:
                span = _def_name_span(SDK_SRC, m, m.name)
                if span is None:
                    raise SystemExit(
                        f"cannot locate the name span for `def {m.name}` at "
                        f"tortoise/sdk.py:{m.lineno}"
                    )
                defs.append((m.lineno, span[0], span[1], rmap[m.name]))
    return FileEdits(attrs, defs, strings, residue)


def apply_edits(path: Path, edits: list[tuple[int, int, int, str]]) -> int:
    if not edits:
        return 0
    lines = path.read_text(encoding="utf-8").split("\n")
    by_line: dict[int, list[tuple[int, int, str]]] = defaultdict(list)
    for lineno, start, end, repl in edits:
        by_line[lineno].append((start, end, repl))
    for lineno, spans in by_line.items():
        spans.sort(key=lambda s: s[0], reverse=True)  # right-to-left: spans stay valid
        line = lines[lineno - 1]
        for start, end, repl in spans:
            line = line[:start] + repl + line[end:]
        lines[lineno - 1] = line
    path.write_text("\n".join(lines), encoding="utf-8")
    return len(edits)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--plan", action="store_true", help="the rename map + collision audit")
    ap.add_argument("--report", action="store_true",
                    help="classification, collisions, stand-ins and every untyped residue shape")
    ap.add_argument("--apply", action="store_true", help="rewrite the tree")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 while any TYPED reference to a now-private name survives, or "
                         "the residue set moved off its pin, or the map derived from the "
                         "pinned base is empty/wrong-sized (a vacuous verifier is a failure)")
    ap.add_argument(
        "--base", default=None,
        help=f"rev to derive the rename map from (default for --check: the PINNED sweep base "
             f"{SWEEP_BASE[:12]}; default for --plan/--apply/--report: the working tree). "
             "⛔ `--check` MUST NOT default to `merge-base origin/main`: after this branch "
             "merges the live class no longer holds the names, the map is empty, and the "
             "check would answer 0 references over an unswept tree — a vacuous PASS.")
    ap.add_argument("--repin", action="store_true",
                    help="print the SWEEP_RESIDUE pin for the current tree (deliberate re-pin)")
    ap.add_argument("--top", type=int, default=25, help="residue rows to print")
    args = ap.parse_args()
    if not (args.plan or args.apply or args.check or args.report or args.repin):
        ap.print_help()
        return 2

    if args.base is None and (args.check or args.repin):
        args.base = SWEEP_BASE

    entries = rename_map(rev=args.base)
    rmap = {e.name: e.private for e in entries}

    if args.check and len(entries) != SWEEP_NAMES:
        # A map that is empty (or a different size) means the check is answering about a
        # different tree than the one the sweep was applied to. That is a FAILURE, not a pass:
        # a verifier whose answer cannot vary is the fail-open this file exists to prevent.
        print(
            f"VACUOUS CHECK: the rename map derived from {args.base[:12]} holds "
            f"{len(entries)} name(s), expected exactly {SWEEP_NAMES}. `--check` cannot answer "
            "\"did we finish\" from a map that is empty or has drifted; re-pin SWEEP_BASE / "
            "SWEEP_NAMES deliberately, in a commit that says why.",
            file=sys.stderr,
        )
        return 2

    if args.plan or args.report:
        print(f"{len(entries)} off-surface methods on TortoiseSDK\n")
        for i, e in enumerate(entries, 1):
            mark = f"  ⚠️ COLLISION -> {e.private}" if e.collided else ""
            print(f"{i:2d}. {e.name:26s} -> {e.private}{mark}")
        print(f"\ncollisions: {sum(1 for e in entries if e.collided)}")

    total_attrs = total_defs = total_strings = 0
    residue_all: Counter = Counter()
    per_file_attrs: Counter = Counter()
    # ONE walk per invocation: the check block below re-derives the same counts, so running
    # this loop for `--check` too doubled the gate's wall clock for nothing (measured: 4m20 →
    # 2m10 on the tree at this head).
    walk_here = args.plan or args.report or args.apply
    for p in py_files() if walk_here else ():
        e = edits_for(p, rmap)
        residue_all.update(e.residue)
        if e.attrs or e.defs or e.strings:
            per_file_attrs[p.relative_to(ROOT).as_posix()] = len(e.attrs)
            total_attrs += len(e.attrs)
            total_defs += len(e.defs)
            total_strings += len(e.strings)
            if args.apply:
                n = apply_edits(p, e.attrs + e.defs + e.strings)
                print(f"  rewrote {n:5d} reference(s) in {p.relative_to(ROOT)}")

    if args.apply:
        print(f"\nTOTAL rewrites: attr={total_attrs} def={total_defs} string={total_strings}")

    if args.report:
        print(f"\nTYPED rewrites available: {total_attrs} attribute accesses, "
              f"{total_defs} `def` lines, {total_strings} string references")
        print("\ntop files by attribute rewrites:")
        for f, c in per_file_attrs.most_common(12):
            print(f"  {c:5d}  {f}")
        print(f"\nUNTYPED receivers NOT swept (the visible edge): {sum(residue_all.values())}")
        for r, c in residue_all.most_common(args.top):
            print(f"  {c:5d}  {r}")

    if args.check or args.repin:
        after = 0
        after_residue: Counter = Counter()
        for p in py_files():
            e2 = edits_for(p, rmap)
            after += len(e2.attrs) + len(e2.defs) + len(e2.strings)
            after_residue.update(e2.residue)
        digest = residue_digest(after_residue)
        if args.repin:
            print(f"SWEEP_RESIDUE = ({sum(after_residue.values())}, {digest!r})")
            return 0
        print(f"typed references still pointing at a public name: {after}")
        print(f"untyped receivers left alone (deliberate, reported): {sum(after_residue.values())}")
        pinned = SWEEP_RESIDUE
        if (sum(after_residue.values()), digest) != pinned:
            print(
                "the residue SET moved off its pin: a reference shape that was not there when "
                "this sweep was verified is now on the tree, and the ordinary-word receivers "
                "(`x.query`, `x.update`, `x.delete`) are exactly the shape a typed-reference "
                "count cannot see. Read the rows below, fix or declare the new shape, then "
                f"re-pin with --repin.\n  observed: ({sum(after_residue.values())}, "
                f"{digest!r})\n  pinned:   {pinned}",
                file=sys.stderr,
            )
            for r, c in after_residue.most_common(10):
                print(f"  {c:5d}  {r}", file=sys.stderr)
            return 1
        return 1 if after else 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
