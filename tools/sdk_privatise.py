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
#: grading it, and `--check` is therefore RED on `main` today.
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
#: ISSUE NUMBER and not by sha on purpose: a pre-rebase commit is reachable from no ref and
#: disappears on the next `gc`, so a sha here would be an unreproducible address of exactly the
#: kind this block forbids elsewhere).
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
#: pin was correct, and no such window existed; so `main`'s own tree measures 11831 against its own
#: 11829 pin, and `--check` is RED on `main` right now (the same recurrence `#14062` documents: the
#: pin is graded on PULL REQUESTS only, so `main` drifts and every open PR is then graded against a
#: stale number). The two rows are `found.update` 10 → 12, both in `tests/test_usage_signal.py`,
#: added by `ac57b2d38a` (`#14065`) — already an ancestor of the re-cut commit, so it sits between
#: the 11821 pin and this tree, NOT "after" the 11829 one. Both sit on
#: a plain `set` fed by a generator expression, so neither is a typed reference to a swept name
#: (`typed references still pointing at a public name: 0` on this tree).
#: 11831 − 6 = 11825, and that arithmetic is a CROSS-CHECK of the measurement, not the evidence for
#: it: what was pasted into the pin is the pair `--repin` printed on THIS tree.
#:
#: made it URGENT is that the pin is graded only on PULL REQUESTS — `main`'s `d4a8dddc4e` carries 12
#: check-runs and none of them is `surface-guard` — so main drifted 1 row off the pin and every open
#: PR then graded main's row as its own: #14045 measured `observed: (11822, …)` against the 11821 pin
#: on a tree that itself derived 11821, while `main` at `38086c1bc5` derived 11822. A stale pin on
#: main is a red gate on every PR that touches nothing in this population (#14062).
#: ⛔ THIS RECORD CARRIES NO ASSIGNMENT, deliberately: merging two re-cuts left BOTH
#: `SWEEP_RESIDUE` lines in this file, and Python binds the LAST one, so the earlier value was
#: dead text that still read as the pin. ONE assignment, at the end of the records, cannot be
#: shadowed by a second re-cut.
#: ── #13998's own cut, on the tree that carries BOTH records ────────────────────────────────
#: This PR adds SEVEN rows of its own on top of the above — SIX `cp.query` plus ONE `row.update`,
#: which is why it cannot be summarised as "seven `cp.query` rows":
#:
#:   * `cp.query` — `capacity_notice.read_account`'s orgs read and its drift-soft retry (2),
#:     `supabase_control.org_storage_notice_pct` and `set_org_storage_notice_pct` (2), and
#:     `tests/test_13998_capacity_notice.py`'s save/restore pair on the `_ColumnBlindCP` fake (2);
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
SWEEP_RESIDUE = (11840, "9f1b8cff93b3a480")

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
