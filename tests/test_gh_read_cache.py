"""Tests for tools/gh_read_cache.py — the invariant-read cache for `gh` (#1910 class).

WHY THESE TESTS EXIST
---------------------
The 5,000-call/hour pool emptied FOUR times in one night. The gated shim (`gh-gate`) paces,
serialises, honours `retry-after` and shares a cooldown — and it has NO CACHE, so every poll is a
live call. GitHub's own best practice is explicit: *"Make authenticated conditional requests, so
that unchanged data does not count against your primary rate limit"*.

A cache that can serve a STALE (or WRONG-SUBJECT) answer as if it were fresh is worse than no cache:
it turns "I could not read" into "I read X", which is this repo's false-PASS class. These tests are
therefore about the FAILURE directions, not the happy path.

⛔ MUTATION-VERIFIED, AND HONEST ABOUT WHAT IS NOT. Two earlier rounds found fences that could not
fail for the bug they named — a green suite proving nothing. Every row in the ledger below was run
against the tree at its own head, and each row states its OWN result; **M49 is the ONLY survivor**,
and it survives by the documented layering (the row says which). ⛔ THE TOTALS ARE DELIBERATELY
ABSENT, not re-derived: a running total here re-stales on the next row any round adds — the exact
failure this header has now had twice (the collected-case counts were dropped in `75c91b0ed` and
these totals went stale in `ebd389d34`, which added M76-M78 without touching them). The ledger rows
are the authority for the mutation count; `--collect-only` is the authority for the case count.
⛔ M1≡M31, M24≡M44 and M26≡M43 were re-run in later rounds rather than re-labelled, so an id may
name a re-run of an earlier one:

    M1  reader returns the unprojected body ............ KILLED   M12 decode_pages first doc only .... KILLED
    M2  _load ignores freshness ........................ KILLED   M13 envelope scope check removed ... KILLED
    M3  generic reader handler fails open .............. KILLED   M14 TTL dropped from FILENAME ..... KILLED
    M4  global-scope check removed from read() ......... KILLED   M15 import-time call unbound ...... KILLED
    M5  forbidden argv flags no longer refused ......... KILLED   M16 CLI live fallback dead-ends ... KILLED
    M6  declare_invariant skips spec validation ........ KILLED   M17 read() skips spec validation .. KILLED
    M7  import-time registry guard is a no-op .......... KILLED   M18 OverflowError escapes _load ... KILLED
    M8  _store is not atomic ........................... KILLED   M19 digest ignores projection SRC . KILLED
    M9  cache-write failure fails the read ............. KILLED   M20 shape check is a no-op ........ KILLED
    M10 projection dropped from the FILENAME only ...... KILLED    M21 non-dict page accepted ....... KILLED
    M11 projection dropped from the ENVELOPE only ...... KILLED   M22 error-document marker gone .... KILLED

Cycle-9/10/11 added M47-M52, cycle-12 added M53-M56, cycle-13 added M57-M60, cycle-14 added M61-M63.
The fixer lane then added M64-M65 (its own numbering) and cycle-15 added M66-M71 — ⛔ TWO LANES
NUMBERED INDEPENDENTLY, so the cycle-15 ids were renumbered from their first run (M64-M69) to
M66-M71 rather than left colliding with M64-M65; a mutation id must name exactly one mutation
(the cycle-16 review then added M72):
  M64 `_warn_write_failed` print unguarded ........... KILLED   M65 `_mutable_words_in` forks the vocabulary ... KILLED
  M66 required_set fabricates `contexts` again ....... KILLED   M67 `repo.labels` empty name allowed ......... KILLED
  M68 workflow `id` type check removed ............... KILLED   M69 `node_id` no longer required .............. KILLED
  M70 OverflowError dropped from ttl coercion ........ KILLED   M71 OverflowError dropped from now coercion .. KILLED
  M72 runner/label EMPTY string allowed .............. KILLED   M73 endpoint check removed from the door ..... KILLED
  M74 decode denylist restored ....................... KILLED   M75 empty-repo scope "@main" accepted ....... KILLED
  M76 the endpoint door is a DENY-LIST again .......... KILLED   M77 argv SHAPE check removed (-X POST) ...... KILLED
  M78 `scope_for` skips the empty repo/branch check ... KILLED   M79 shape guard back AFTER the flag loop .. KILLED
  M80 refusal message states SUCCESS cond.............. KILLED   M81 `_gh_reader` projects (R2 reopened).... KILLED
  M82 404->negative mapping disabled ................. KILLED   M83 negative lane removed (no own TTL) ..... KILLED
  M84 negative allow-list widened to ANY body ........ KILLED   M85 `_validate_negative` unwired ........... KILLED
  M86 protection ROUTE guard deleted ................. KILLED   M87 stderr spelling return deleted ......... KILLED
  M88 `read()` skips `_validate_negative` ............ KILLED   M89 tightening DROPS the negative .......... KILLED
  M90 door skips `_validate_negative` ................ KILLED   M91 tighten-past-negative refusal deleted .... KILLED
  M92 sentinel never recognised as a value ........... KILLED   M93 `negative=` falls through to a DROP ....... KILLED
⛔ M82-M85 are the round-18 fix lane's numbering (the documented-negative work, #11047). Each was
RUN against this head, and each states what it does NOT cover:
  M82 (`_gh_reader`'s `neg = spec.negative` forced to `None`, i.e. the 404 falls back through the
  fault path): **2 failed, 336 passed** — the two arms that assert the unprotected answer is a
  VALUE, one at the reader and one through `main()`. It does not cover the mapping's WORDING: an
  arm asserting a paraphrase of the sentinel would survive this and is deliberately absent.
  M83 (`_negative_ttl` returns `None`, so a negative is stored in the key's own 1800 s lane):
  **1 failed, 337 passed** — the arm that reads at an age the key's ceiling would still serve and
  asserts a second reader call. This is the arm the WHOLE short-TTL claim rests on, and it is
  boundary-exact (`age == NEGATIVE_ANSWER_TTL_S` is EXPIRED, one second less is a hit).
  M84 (the allow-list widened to any JSON body — the pre-fix "looks like a 404" shape): **1
  failed, 337 passed** — the `Not Found` arm. ⛔ IT DOES NOT COVER THE STDERR SPELLING, and the
  first cut of this row claimed that form "is only reached in the M82/M84 runs" — MEASURED FALSE,
  in the reassuring direction: under M82 `_is_documented_negative` is never CALLED at all, and under
  M84 the body arm returns True before the stderr line runs, so no arm in the suite exercised it and
  deleting that `return` left the suite green. M87 is the arm that closes it.
  M85 (`_validate_negative(key, spec)` deleted from `_assert_every_key_projects`): **1 failed, 337
  passed** — and this one was ADDED because the mutation SURVIVED the first cut of these tests
  (337 passed, green, with the import-time hook gone): the two declaration arms called the
  validator DIRECTLY, so the function was pinned and the wiring its own docstring promises was
  not. A direct call is never the guard.
  M86 (`_is_documented_negative`'s `_PROTECTION_SEGMENTS` comparison deleted, so any key whose
  negative is declared would take the mapping off ANY route): **1 failed, 338 passed** — the direct
  route arm. ⛔ ADDED FOR THE SAME REASON AS M85: it SURVIVED the previous cut (338 passed), because
  the `repo.metadata` arm is refused by that key's `negative=None` BEFORE the route comparison runs.
  A parametrized arm that passes for an unrelated reason is not coverage, and the arm now says so
  in its own comment.
  M87 (the measured `gh: Branch not protected (HTTP 404)` stderr return replaced with `False`):
  **2 failed, 337 passed** — the stderr-drained reader arm and the direct helper arm. Also SURVIVED
  the previous cut, for the reason recorded under M84.
⛔ M88-M93 are round 19's numbering (the guard-at-every-door fix, #11047). Each was RUN at this
head, and each states what it does NOT cover:
  M88 (`_validate_negative(key, _spec)` deleted from `read()`'s defence-in-depth): **1 failed, 343
  passed** — `test_read_REFUSES_a_spec_injected_with_a_negative_it_would_not_accept`. ⛔ THE PRE-FIX
  BEHAVIOUR IS MEASURED, not asserted: with a 1800 s-priced negative injected, `read()` served the
  sentinel and the reader was called **once across 1700 s** — the 30-minute fail-OPEN window the
  short TTL exists to prevent. It does not cover the IMPORT path (M85 does) or the DOOR (M90).
  M89 (`declare_invariant` stops preserving an omitted `negative`, i.e. the pre-fix silent loss):
  **2 failed, 342 passed** — the preservation arm and the tighten-below-the-negative arm (`declared`
  becomes `None`, so no refusal fires at all). It does not cover the `negative=None` leg: that leg
  asserts the EXPLICIT drop, which a mutation that drops everything still satisfies.
  M90 (`_validate_negative(key, spec)` deleted from `declare_invariant`): **1 failed, 343 passed** —
  the door arm's unprojectable-sentinel leg. ⛔ ITS CONTROL LEG IS WHY THIS ROW IS HONEST: the same
  arm also declares a VALID negative through the same door, so the mutant cannot pass by refusing
  every negative.
  M91 (the tightening-below-the-negative refusal deleted → `_validate_negative`'s generic message
  answers instead): **1 failed, 343 passed** — the arm asserts the refusal NAMES `negative=None`,
  because a raise alone survives this mutation (the generic validator still raises). Without that
  message assertion M91 SURVIVES, which is the same "a pass for an unrelated reason" shape as M85.
  M92 (`_is_negative_answer` returns `False` — the sentinel is never recognised, so nothing is
  priced on the short lane): **2 failed, 342 passed** — the short-TTL arm and the new
  genuine-no-checks arm. ⛔ THE SECOND ONE PINS A COST, NOT A FEATURE: it fails if the trade is
  "fixed" in the other direction too, which is why its docstring names why the RAW-body alternative
  is the fail-OPEN one.
  M93 (the `negative=` arms reduced to `if isinstance(negative, NegativeAnswer)` — the shape written
  first, and the reason M93 exists): **1 failed, 343 passed** — the door arm's malformed-value leg.
  Every value that is neither the sentinel nor a `NegativeAnswer` falls through to `None`, i.e. to a
  SILENT DROP, which is the pre-fix defect one line below where it was fixed. ⛔ IT SURVIVED THE
  FIRST CUT of this round for exactly that reason: the arm tested the three SUPPORTED values and
  never a malformed one, so the hazard lived in the shape and not in the behaviour.
⛔ M94-M98 are round 20's numbering (two reviews at `1217611e7` and `e1f1e3f4b`, #11047). M94/M95
were found SURVIVING on the first head; all five are KILLED here, and each row states what it does
NOT cover:
  M94 (`_negative_ttl`'s `min()` removed, i.e. `return int(neg.ttl_s)`): **1 failed, 346 passed** —
  `test_a_TIGHTENED_ttl_is_never_served_the_WIDER_negative`. ⛔ IT SURVIVED THE PREVIOUS HEAD (344
  passed) because every shipped key asks at its own 1800 s ceiling, where `min()` is
  INDISTINGUISHABLE from the declared value; it is observable only at `ttl_s <
  NEGATIVE_ANSWER_TTL_S`, i.e. a documented `--ttl` tighten. Measured on the mutant before the arm
  existed: `read(..., ttl_s=10)` at `age=59` SERVED a 59-second-old `{"contexts": [], "checks": []}`
  — "this branch requires no checks" — on the merge-gating key, in the fail-OPEN direction. It does
  not cover a negative TTL WIDER than the key's ceiling (refused at the declaration door, M91/M85).
  M95 (`_validate_negative`'s empty-body branch deleted): **1 failed, 346 passed** —
  `test_a_negative_with_NO_body_is_refused_BY_NAME`. ⛔ SURVIVED THE PREVIOUS HEAD (344 passed): the
  deletion is behaviourally SURVIVABLE, because the projectability check below it refuses `b""` too,
  so only a `match=` on the MESSAGE makes it lethal. `match=` is the point, not decoration — that
  line is the whole operator-facing detail on a `REFUSED` read, the M80 row's class.
  M96 (`_door(a.key)` deleted from the `--no-cache` live path): **2 failed, 347 passed** —
  `test_the_CLI_live_paths_walk_the_DOOR_and_name_the_REAL_fault` (the fault-message half) and
  `test_the_no_cache_path_REFUSES_a_spec_that_padding_guard_2_would_pass` (the guard-2 half). ⛔ THE
  PRE-FIX BEHAVIOUR IS MEASURED, not asserted: `--no-cache --key pr.state` answered `no argv is
  declared for 'pr.state'` (exit 1) — a REGISTRY-shaped fault for an R4 refusal.
  M97 (`_door(a.key)` replaced by `ttl_for(a.key)` in the `--no-cache` path — the door's SECOND
  question dropped): **1 failed, 348 passed** —
  `test_the_no_cache_path_REFUSES_a_spec_that_padding_guard_2_would_pass`. ⛔ IT SURVIVED THE FIRST
  CUT OF M96 (347 passed): the fault-message arm's two keys are both answered by `ttl_for`/`kind_of`
  alone, so the door could be halved on the one path that spends a call with nothing red. The arm
  now injects a spec carrying `--jq` (guard 2's own forbidden flag) and requires a refusal with NO
  `subprocess.run`. It does not cover the negative's short-TTL half of question 2, which `read()`'s
  own arms (M88/M92) pin on the cached path.
  M98 (`_door(a.key)` deleted from the GLOBAL-KEY FALLBACK, the sibling live exit): **1 failed, 348
  passed** — `test_the_global_fallback_walks_the_DOOR_too`. ⛔ THIS ROW CORRECTS ITSELF, ON THE
  RECORD: the first cut of M96 claimed that path's gap was "STRUCTURAL … no test can make that
  particular call observable — deleting it would leave the suite green and the path unchanged".
  Deleting it DID leave the suite green (347 passed, re-measured), but BOTH of my conclusions from
  that were wrong: the call IS observable with the suite's own established bypass (a spec injected
  straight into `INVARIANTS` after import — the exact bypass `_door`'s docstring exists for, already
  used by `test_the_negative_declaration_guard_is_WIRED_INTO_the_import_check`), and deleting it DOES
  change the path — a live `gh api user --jq .` call is spent on a guard-2-violating spec. "The
  existing suite does not catch it" is not "no test can"; the sentence was the sole justification
  for leaving a live guard unpinned, which is the M73 class this file has now had to correct twice.
⛔ M73's LEDGER ROW OVERSTATED ITS COVERAGE AND THAT IS NOW FIXED, not re-labelled. The old
parametrization had 5 arms, two of which (`/commits/main/status`, `/commits/abc/check-runs`) were
satisfied by the EARLIER `/commits` marker, and six of the ten deny-list markers had no arm at all —
so deleting `/status`, `/check-runs`, `/comments`, `/releases`, `/tags`, `/merges` left the suite
green (M73 SURVIVED at `a9321ddc6`; 290 passed). There is now ONE ARM PER FAMILY, each carrying
only itself, plus the families the deny-list never named (`/git/refs/…`, `/branches`,
`/deployments`, `/rulesets`, `notifications`, `graphql`) and five non-GET shapes.
  Measured M76 (the allow-list membership check disabled → the old accept-anything door): **27
  failed, 293 passed**. M77 (the argv shape loop disabled → `-X POST` and two-operand argvs
  accepted): **10 failed, 310 passed**. M78 (both `scope_for` empty-input refusals disabled):
  **6 failed, 314 passed** — including both `--no-cache` CLI arms, which is the non-vacuity the
  P3 asked for: the pre-fix CLI really did spend the call.
  M79 (`34ac43e354`'s shape guard put BACK below the projection-flag loop, i.e. the exact pre-fix
  ordering): **7 failed, 322 passed**. The seven are the two new arms — five of the six
  `declare_invariant` shapes and two of the three read-path shapes — and the two that stay green
  are the BARE-STRING argvs, which iterate harmlessly and are refused by `_endpoint_refusal`
  either way: a mutation row must state what it does NOT cover, or it is the M73 shape again.
  M80 (the `runners.inventory` refusal message restored to the SUCCESS condition — "entry has a
  non-null, non-empty-string `name`" — on the branch that fires because the entry does NOT have
  one): **2 failed, 327 passed**, both
  `test_a_runner_naming_nothing_with_an_EMPTY_STRING_is_refused` arms, which now assert the text
  and the offending value. That message is the WHOLE operator-facing detail on a refused live read
  (`gh_read_cache: REFUSED — …`, exit 1), so the arm pins the line a triaging lane actually reads;
  it does not cover the neighbouring refusal messages, which no arm asserts by text.
  M81 (`_gh_reader` mutated to project, re-opening R2): **1 failed, 328 passed** — and the ONE is
  the point: a projecting reader is SILENT through every assertion that reaches the projection via
  `read()`'s own `_project(...)`, because a second projection of this already-projected runners
  body is accepted. Without the UNPROJECTED-bytes arm the test's name is a claim, not a fence.
  It does not cover a reader that projects only SOME keys — the arm asserts the whole body comes
  back raw for this one key. M80/M81 are the round-17 fix lane's numbering for the two arms THAT
  round added (it did not touch an earlier id).
⛔ M67 SURVIVED on its first test, which passed a DICT page where a LIST page is required — the
SHAPE guard satisfied it and the empty-name check was unreachable. Fixed, then KILLED.
⛔ M67's EMPTY-STRING sibling (M72, cycle-16 review) reached the same blocker from the other side:
`_project_runners_inventory` REQUIRES a `labels` key, so the arm for the runner-NAME check has to
carry one — `{"name": ""}` ALONE dies at the ABSENCE guard first and leaves the empty-name check
unreachable, which is why the pinned arm is `{"name": "", "labels": []}`. Both M72 arms were
MEASURED against the `8edc371f3` code: `{"name": "", "labels": []}` projected cleanly to
`[{"name": "", "labels": []}]` and `[{"name": ""}]` was cached as a named label, instead of
raising. Both now red.

  M52 runner `labels` ABSENT read as empty ..... KILLED   M51 `visibility` cached again ......... KILLED


  M47 explicit `null` required_set accepted .... KILLED   M49 argv dropped from the FILENAME .. SURVIVES
  M48 runner labels silently filtered .......... KILLED   M50 argv dropped from the ENVELOPE .. KILLED

M49 surviving is the documented layering in its established direction: for the projection AND the
argv, the ENVELOPE is authoritative and the filename's copy only stops two different questions of
one key thrashing one file. (M10 flipped from SURVIVES to KILLED once the source-bearing digest
moved into the filename, because a thrash test now pins it.)

Two earlier rounds added M23-M46 after review (all KILLED):

  cycle-8: M39a/M39b CLI live paths emitted the RAW body (one key, two schemas) — killed only
           after a test covered the GLOBAL exit separately | M40/M41 required_set `contexts` /
           `checks` TYPE checks (`{"contexts": "docs"}` -> ["d","o","c","s"]) | M42 workflow NULL id
           | M43 runner `name` must be a str | M44 `_pick_last` required must be non-null
           M45 the added mutable words (`pr.head`, `pr.title`, `repo.topics`, …)
           M46 `_IMPORT_CHECK = frozenset(INVARIANTS)` — a literal reproducing the key SET

  cycle-6: M23 required_set ABSENT as empty | M24 _pick_last required | M25 workflow entry key
           M26 runner `name` | M27 all-null answer | M28 non-dict envelope | M29 reader non-bytes
           M30 _load denylist only
  cycle-7: M31 read() stores bytes verbatim (the guard-2 hole) | M32 reader projects instead
           M33 source digest dropped from the filename (cache never hits) | M34 kind_of mutable
           M35 _IMPORT_CHECK constant (needed the checked-key SET — a count was spoofable by `6`)
           M36 ttl_s ValueError | M37 required_set inner absence | M38 _project doesn't refuse

⛔ M10 USED TO SURVIVE AND NO LONGER DOES — the earlier claim is corrected here rather than kept.
Once the source-bearing digest moved into the FILENAME (to stop two revisions thrashing one file),
dropping the filenames' projection component reds `test_two_projection_revisions_do_not_thrash_one_cache_file`.
So both projection layers are now individually covered. For the TTL the direction is still one-way:
the FILENAME is authoritative and the envelope's `ttl_s` check is redundant, which is why that
layer alone is NOT pinned. Do not delete a layer believing the other covers the same direction.

Hermetic: no network, no `gh`, no DB. The reader and the clock are injected.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import gh_read_cache as C  # noqa: E402

SCOPE = "daniel-ospina/tortoise@main"

#: ⛔ A VALID argv for a probe key. The door is an ALLOW-LIST of endpoints (exact tuple equality on
#: the `/`-separated segments), so a probe must declare an endpoint the registry already establishes
#: as invariant — `("api", "x")`, used by 13 declarations while the door was a deny-list, is now
#: REFUSED. Migrating them is part of the fix, not a test detail: the first attempt at this rewrite
#: was reverted half-way because those 13 spellings reddened, which left a documented hole
#: (`repo.identity` over `repos/{repo}/git/refs/heads/main`) shipped instead of closed.
OK_ARGV = ("api", "repos/{repo}")


# ── helpers ────────────────────────────────────────────────────────────────────────────────────
#: A RAW (unprojected) response body per key, as a `reader` must supply it. Each carries volatile
#: fields beside the invariant ones, so a `read()` that forgot to project is caught immediately.
RAW = {
    "repo.metadata": lambda n: json.dumps(
        {
            "id": n,
            "node_id": "N1",
            "full_name": "o/r",
            "default_branch": "main",
            "visibility": "private",
            "pushed_at": "2020-01-01",
            "open_issues_count": 412,
        }
    ).encode(),
    "repo.required_set": lambda n: json.dumps(
        {
            "required_status_checks": {"contexts": ["docs"], "checks": [{"context": "docs"}]},
            "enforce_admins": {"enabled": False},
        }
    ).encode(),
    "user.identity": lambda n: json.dumps({"login": "someone", "id": n}).encode(),
}


class Counter:
    """A reader that answers with a monotonic RAW body and counts its calls.

    ⛔ The reader returns RAW response bytes: the PROJECTION is `read()`'s job, so a test reader
    that returned a projected document would hide exactly the hole that let a library caller cache
    a whole body.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail: Exception | None = None

    def __call__(self, key: str) -> bytes:
        self.calls.append(key)
        if self.fail is not None:
            raise self.fail
        shape = RAW.get(key, RAW["repo.metadata"])
        return shape(len(self.calls))

    @property
    def n(self) -> int:
        return len(self.calls)


def _read(
    key: str, *, now: float, reader, ttl_s: int | None = None, cache_dir: Path, scope: str = SCOPE
):
    return C.read(key, scope=scope, cache_dir=cache_dir, now=now, reader=reader, ttl_s=ttl_s)


def _entry(cache_dir: Path, key: str = "repo.metadata", scope: str = SCOPE) -> Path:
    return cache_dir / C._cache_path(key, scope).name


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 1. THE CACHE ACTUALLY SAVES A CALL
# ══════════════════════════════════════════════════════════════════════════════════════════════
def test_cached_hit_does_not_call_reader(tmp_path: Path) -> None:
    r = Counter()
    first = _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    second = _read("repo.metadata", now=1000.0 + 1.0, reader=r, cache_dir=tmp_path)

    assert json.loads(first)["id"] == 1
    assert second == first, "a fresh cached value must be served verbatim"
    assert r.n == 1, (
        "the pool emptied because every poll was a live call — a hit inside the TTL MUST NOT "
        "call the reader, or the cache is decoration"
    )


def test_ttl_expiry_calls_reader_again(tmp_path: Path) -> None:
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    out = _read(
        "repo.metadata", now=1000.0 + C.ttl_for("repo.metadata") + 1.0, reader=r, cache_dir=tmp_path
    )
    assert r.n == 2
    assert json.loads(out)["id"] == 2


def test_exact_ttl_boundary_is_a_miss(tmp_path: Path) -> None:
    """`age == ttl` is EXPIRED. A `<=` silently makes every TTL `ttl + one poll`."""
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    _read("repo.metadata", now=1000.0 + C.ttl_for("repo.metadata"), reader=r, cache_dir=tmp_path)
    assert r.n == 2


def test_different_ttls_do_not_evict_each_others_entries(tmp_path: Path) -> None:
    """Two callers at different TTLs must each keep their entry.

    The filename had no TTL component while `_load` rejects a mismatched stamped ttl, so alternating
    TTLs produced 12 live reads where a working cache does 2 — silently disabling the cache for that
    key, i.e. exactly the burn this module exists to stop.
    """
    tight = 900
    r = Counter()
    for i in range(6):
        _read("repo.metadata", now=1000.0 + i, reader=r, cache_dir=tmp_path)
        _read("repo.metadata", now=1000.0 + i, reader=r, cache_dir=tmp_path, ttl_s=tight)
    assert r.n == 2, f"alternating TTLs thrashed one file: {r.n} live reads, expected 2"
    assert len(list(tmp_path.iterdir())) == 2, "each TTL must own its own entry"


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 2. SCOPE IS CACHE IDENTITY (guard 1)
# ══════════════════════════════════════════════════════════════════════════════════════════════
def test_a_different_scope_is_a_different_entry(tmp_path: Path) -> None:
    """⛔ THE P0 of the first review. One repo's answer must never be served as another's."""
    calls: list[str] = []

    def reader_a(key: str) -> bytes:
        calls.append("A")
        return json.dumps({"required_status_checks": {"contexts": ["tortoise-only"]}}).encode()

    def reader_b(key: str) -> bytes:
        calls.append("B")
        return json.dumps({"required_status_checks": {"contexts": ["agent-infra-only"]}}).encode()

    a = _read(
        "repo.required_set",
        now=1000.0,
        reader=reader_a,
        cache_dir=tmp_path,
        scope="daniel-ospina/tortoise@main",
    )
    b = _read(
        "repo.required_set",
        now=1000.0,
        reader=reader_b,
        cache_dir=tmp_path,
        scope="daniel-ospina/agent-infra@main",
    )

    assert a != b, "a cross-scope hit served repo A's required set as repo B's"
    assert json.loads(b)["contexts"] == ["agent-infra-only"]
    assert calls == ["A", "B"], "reader B must actually be called"


def test_a_different_branch_is_a_different_entry(tmp_path: Path) -> None:
    r = Counter()
    _read("repo.required_set", now=1000.0, reader=r, cache_dir=tmp_path, scope="o/r@main")
    _read("repo.required_set", now=1000.0, reader=r, cache_dir=tmp_path, scope="o/r@release")
    assert r.n == 2


def test_two_branches_of_one_LONG_named_repo_never_thrash_one_entry(tmp_path: Path) -> None:
    """⛔ The SCOPE component of the FILENAME DIGEST, pinned — and it was not.

    `_filename` is `…-{_slug(key)}-{_slug(scope)[:20]}-{payload_id}.json`, and the readable slug is
    TRUNCATED to 20 characters. Every scope in the test above is short enough that the READABLE
    half already differs, so the whole scope contribution of `payload_id` could be deleted with the
    suite green (measured: 329/329) — the readable half carried the identity alone.

    Real scopes are longer than 20 characters once slugged: `daniel-ospina/tortoise@main` and
    `daniel-ospina/tortoise@release` BOTH slug to `daniel-ospina-tortoi`, so with the digest's
    component gone the two branches share one filename — and `_load` then rejects it on the
    envelope's scope, so every single access is a MISS and the cache is silently DISABLED for the
    pair. Measured on the mutant: 8 alternating reads -> 8 live reads, 1 file (shipped: 2 reads).

    That is the burn this module exists to stop, and it fails in complete silence — correctness is
    unharmed (the envelope still refuses the wrong subject), which is exactly why nothing reds.
    """
    main_scope = "daniel-ospina/tortoise@main"
    release_scope = "daniel-ospina/tortoise@release"
    assert C._slug(main_scope)[:20] == C._slug(release_scope)[:20], (
        "the readable half must actually collide or this test proves nothing about the digest"
    )
    assert C._cache_path("repo.required_set", main_scope).name != (
        C._cache_path("repo.required_set", release_scope).name
    ), "two branches of one repo share ONE cache filename — the digest lost its scope component"

    r = Counter()
    for i in range(4):
        _read("repo.required_set", now=1000.0 + i, reader=r, cache_dir=tmp_path, scope=main_scope)
        _read(
            "repo.required_set",
            now=1000.0 + i,
            reader=r,
            cache_dir=tmp_path,
            scope=release_scope,
        )
    assert r.n == 2, f"two branches thrashed one entry: {r.n} live reads, expected 2"
    assert len(list(tmp_path.iterdir())) == 2, "each scope must own its own entry"


def test_scope_is_required(tmp_path: Path) -> None:
    """A caller cannot FORGET the scope — it is a required keyword, not a defaulted one."""
    r = Counter()
    with pytest.raises(TypeError):
        C.read("repo.metadata", cache_dir=tmp_path, now=1000.0, reader=r)  # type: ignore[call-arg]
    assert r.n == 0


@pytest.mark.parametrize("bad", ["", "   "])
def test_empty_scope_is_refused(bad: str, tmp_path: Path) -> None:
    r = Counter()
    with pytest.raises(C.TtlNotPermitted):
        _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path, scope=bad)
    assert r.n == 0


def test_envelope_scope_must_match(tmp_path: Path) -> None:
    """Defence in depth: a file placed at the right NAME is still rejected if its scope disagrees."""
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    p = _entry(tmp_path)
    env = json.loads(p.read_text())
    env["scope"] = "someone/else@main"
    p.write_text(json.dumps(env))
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    assert r.n == 2


# ── global keys are scoped by CREDENTIAL ──────────────────────────────────────────────────────
def test_read_refuses_a_non_credential_scope_for_a_global_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_TOKEN", "tok-a")
    r = Counter()
    with pytest.raises(C.TtlNotPermitted):
        _read("user.identity", now=1000.0, reader=r, cache_dir=tmp_path, scope=SCOPE)
    assert r.n == 0


def test_read_recomputes_the_credential_scope_rather_than_trusting_a_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⛔ A PREFIX TEST WAS NOT ENOUGH (cycle-4 finding 3).

    Any literal `"global@…"` satisfied `scope.startswith("global@")`, so two lanes with different
    tokens could both pass `"global@"` and share one entry. `read()` now recomputes the expected
    scope and requires exact equality.
    """
    monkeypatch.setenv("GH_TOKEN", "tok-a")
    r = Counter()
    # The literal `"global@"` is exactly what the OLD prefix test accepted as a valid scope.
    with pytest.raises(C.TtlNotPermitted):
        _read("user.identity", now=1000.0, reader=r, cache_dir=tmp_path, scope="global@")
    assert r.n == 0, "a bare 'global@' prefix was accepted as a credential scope"

    # The computed scope is accepted, and a DIFFERENT credential's computed scope is not the same
    # entry even though both are `global@…`.
    real = C.scope_for("user.identity", "o/r", "main")
    _read("user.identity", now=1000.0, reader=r, cache_dir=tmp_path, scope=real)
    assert r.n == 1
    monkeypatch.setenv("GH_TOKEN", "tok-b")
    other = C.scope_for("user.identity", "o/r", "main")
    assert other != real, "two credentials derived the same scope"
    _read("user.identity", now=1000.0 + 1.0, reader=r, cache_dir=tmp_path, scope=other)
    assert r.n == 2


def test_two_credentials_never_share_an_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def reader(key: str) -> bytes:
        calls.append("x")
        return json.dumps(
            {"login": "alice" if len(calls) == 1 else "bob", "id": len(calls)}
        ).encode()

    monkeypatch.setenv("GH_TOKEN", "tok-alice")
    a = _read(
        "user.identity",
        now=1000.0,
        reader=reader,
        cache_dir=tmp_path,
        scope=C.scope_for("user.identity", "o/r", "main"),
    )
    monkeypatch.setenv("GH_TOKEN", "tok-bob")
    b = _read(
        "user.identity",
        now=1000.0,
        reader=reader,
        cache_dir=tmp_path,
        scope=C.scope_for("user.identity", "o/r", "main"),
    )

    assert a != b, "two credentials shared one identity entry — wrong subject, valid envelope"
    assert len(calls) == 2


def test_global_scope_never_contains_the_raw_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "super-secret-token-value")
    scope = C.scope_for("user.identity", "o/r", "main")
    assert "super-secret-token-value" not in scope


def test_no_identifiable_credential_refuses_to_cache_a_global_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown identity FAILS CLOSED — a global key is simply not cacheable."""
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert C._credential_fingerprint() is None
    with pytest.raises(C.TtlNotPermitted):
        C.scope_for("user.identity", "o/r", "main")


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 3. A MUTABLE KEY IS REFUSED, NEVER PASSED THROUGH (guard R4)
# ══════════════════════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "key",
    [
        "pr.state",
        "pr.mergeStateStatus",
        "pr.checks",
        "run.status",
        "runner.status",
        "rate_limit.buckets",
        "issue.state",
        "pr.mergeable",
        "pr.reviewDecision",
        "repo.branches",
        "repo.tags",
        "repo.assignees",
        "repo.releases",
        "pr.approved",
        "pr.approvals",
        "repo.headsha",
        "repo.headoid",
        "repo.forks",
        "repo.stargazers",
        "repo.size",
        "repo.archived",
        "pr.files",
        "pr.additions",
        "pr.rebaseable",
    ],
)
def test_mutable_key_is_refused(key: str, tmp_path: Path) -> None:
    r = Counter()
    with pytest.raises(C.MutableReadRefused):
        _read(key, now=1000.0, reader=r, cache_dir=tmp_path)
    assert r.n == 0, (
        "a refused key must not reach the network — a refusal that still issues the call is a "
        "labelled pass-through, not a guard"
    )


def test_a_mutable_key_has_no_addressable_cache_entry(tmp_path: Path) -> None:
    """There is no path at which a mutable entry could even be PLANTED.

    `_cache_path` resolves the key through the registry, so a key outside it has no filename, no
    directory and no lookup: the refusal is structural rather than a check that could be reordered
    behind an `if path.exists()`.
    """
    with pytest.raises(KeyError):
        C._cache_path("pr.state", SCOPE)
    r = Counter()
    with pytest.raises(C.MutableReadRefused):
        _read("pr.state", now=1000.0, reader=r, cache_dir=tmp_path)
    assert r.n == 0
    assert not list(tmp_path.iterdir()), "a refused key must not have created an entry"


def test_undeclared_key_is_refused(tmp_path: Path) -> None:
    r = Counter()
    with pytest.raises(C.MutableReadRefused):
        _read("something.nobody.declared", now=1000.0, reader=r, cache_dir=tmp_path)
    assert r.n == 0, "an UNDECLARED key is not provably invariant — refuse, do not assume"


def test_registry_cannot_admit_a_mutable_name() -> None:
    # ⛔ `argv` must be one the ENDPOINT door accepts, or this pin asserts nothing: with
    # `("api", "x")` the refusal it catches is `_endpoint_refusal`'s, which raises the SAME
    # `MutableReadRefused` class from `_validate_spec` — so the pin stayed GREEN with the
    # mutable-name DECISION disabled entirely (measured: 36 pins red, this one green). `OK_ARGV`
    # leaves the name check as the only thing that can refuse, so the pin reds when it is
    # weakened. Re-measured after the fix: GREEN clean, RED under the same mutation.
    with pytest.raises(C.MutableReadRefused):
        C.declare_invariant(
            "pr.mergeStateStatus", ttl_s=600, argv=OK_ARGV, projection="p", project=lambda d: d
        )


def test_runner_inventory_is_cached_but_runner_status_is_not() -> None:
    assert C.kind_of("runners.inventory") == "invariant"
    assert C.kind_of("runner.status") == "mutable"


# ── vocabulary shape ─────────────────────────────────────────────────────────────────────────
def test_tokenizer_splits_camel_case() -> None:
    assert C._tokens("pr.reviewDecision") == ["pr", "review", "decision"]
    assert C._tokens("mergeStateStatus") == ["merge", "state", "status"]


def test_tokenizer_does_not_over_match() -> None:
    """The false-POSITIVE side: `run` must not match `runners`."""
    assert C._tokens("runners.inventory") == ["runners", "inventory"]
    assert not C._is_mutable_name("runners.inventory")


@pytest.mark.parametrize("key", ["x.accurateLimit", "repo.generateLimit", "corporate.limits"])
def test_glued_phrase_does_not_match_across_a_word_boundary(key: str) -> None:
    """Scanning the WHOLE glued key refused `accurate`+`limit` — the `"run" in "runners"` class,
    in the other direction. The phrase table matches ADJACENT TOKEN PAIRS only."""
    assert not C._is_mutable_name(key), f"{key!r} is legitimate but was refused as mutable"


def test_rate_limit_still_refused_after_pair_matching() -> None:
    assert C._is_mutable_name("rate_limit.buckets")
    assert C._is_mutable_name("ratelimit")


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 4. FAIL CLOSED WHEN FRESHNESS CANNOT BE PROVEN (guards R3, R5)
# ══════════════════════════════════════════════════════════════════════════════════════════════
def test_corrupt_envelope_is_a_miss(tmp_path: Path) -> None:
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    _entry(tmp_path).write_bytes(b"{not json")
    out = _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    assert r.n == 2
    assert json.loads(out)["id"] == 2


def test_tampered_payload_is_a_miss(tmp_path: Path) -> None:
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    p = _entry(tmp_path)
    env = json.loads(p.read_text())
    env["payload"] = base64.b64encode(b"payload-99").decode()
    p.write_text(json.dumps(env))
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    assert r.n == 2


def test_clock_ahead_of_stored_at_is_a_miss(tmp_path: Path) -> None:
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    p = _entry(tmp_path)
    env = json.loads(p.read_text())
    env["stored_at"] = 9999.0
    p.write_text(json.dumps(env))
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    assert r.n == 2, "a future stamp is unprovable freshness, not infinite freshness"


def test_stale_entry_is_never_a_fallback_for_a_failed_read(tmp_path: Path) -> None:
    """⛔ THE LOAD-BEARING TEST. A reader that fails on a MISS must RAISE."""
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    r.fail = C.Unreadable("gh api failed: HTTP 403")
    with pytest.raises(C.Unreadable):
        _read(
            "repo.metadata",
            now=1000.0 + C.ttl_for("repo.metadata") + 1.0,
            reader=r,
            cache_dir=tmp_path,
        )


@pytest.mark.parametrize(
    "boom",
    [
        RuntimeError("gh api failed"),
        OSError("disk/network fault"),
        TimeoutError("reader timed out"),
        ValueError("unparseable body"),
    ],
)
def test_ANY_reader_failure_is_fail_closed_not_just_unreadable(
    boom: Exception, tmp_path: Path
) -> None:
    """⛔ THE GENERIC HANDLER NEEDS ITS OWN FENCE (cycle-3 finding 1).

    The `Unreadable` case above is re-raised by `except Unreadable: raise` and NEVER reaches the
    generic `except Exception -> raise Unreadable`. Replacing that generic branch with `return b""`,
    or with a real stale fallback, kept the earlier suite fully green.
    """
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    r.fail = boom
    with pytest.raises(C.Unreadable):
        _read(
            "repo.metadata",
            now=1000.0 + C.ttl_for("repo.metadata") + 1.0,
            reader=r,
            cache_dir=tmp_path,
        )


def test_missing_cache_dir_is_a_miss_not_a_crash(tmp_path: Path) -> None:
    r = Counter()
    out = _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path / "nope" / "deeper")
    assert json.loads(out)["id"] == 1
    assert r.n == 1


@pytest.mark.parametrize("bad_now", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_now_never_serves_a_hit(bad_now: float, tmp_path: Path) -> None:
    """A NaN `now` made `age < 0` AND `age >= ttl` both false, serving an arbitrary old entry."""
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    _read("repo.metadata", now=bad_now, reader=r, cache_dir=tmp_path)
    assert r.n == 2, "a non-finite clock cannot prove freshness"


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 5. NO WIDENING (guard R6)
# ══════════════════════════════════════════════════════════════════════════════════════════════
def test_ttl_may_not_exceed_the_key_ceiling(tmp_path: Path) -> None:
    r = Counter()
    with pytest.raises(C.TtlNotPermitted):
        _read(
            "repo.metadata",
            now=1000.0,
            reader=r,
            ttl_s=C.ttl_for("repo.metadata") + 1,
            cache_dir=tmp_path,
        )
    assert r.n == 0


def test_requested_ttl_mismatch_invalidates_the_entry(tmp_path: Path) -> None:
    r = Counter()
    ceiling = C.ttl_for("repo.metadata")
    _read("repo.metadata", now=1000.0, reader=r, ttl_s=ceiling, cache_dir=tmp_path)
    _read(
        "repo.metadata", now=1000.0 + 1.0, reader=r, ttl_s=max(60, ceiling // 2), cache_dir=tmp_path
    )
    assert r.n == 2


def test_shorter_ttl_is_allowed(tmp_path: Path) -> None:
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, ttl_s=60, cache_dir=tmp_path)
    _read("repo.metadata", now=1000.0 + 1.0, reader=r, ttl_s=60, cache_dir=tmp_path)
    assert r.n == 1


def test_declare_invariant_cannot_widen_an_existing_ceiling() -> None:
    """⛔ The refusal must come from the CEILING guard, not from a different one.

    The earlier version passed `--jq "."`, so the projection guard fired first and
    `pytest.raises(TtlNotPermitted)` was satisfied by the wrong guard — deleting the ceiling check
    outright left the suite green.
    """
    before = C.ttl_for("repo.metadata")
    with pytest.raises(C.TtlNotPermitted) as excinfo:
        C.declare_invariant(
            "repo.metadata",
            ttl_s=before * 4,
            argv=OK_ARGV,
            projection="p",
            project=lambda d: d,
        )
    assert "ceiling" in str(excinfo.value), f"refused by a different guard: {excinfo.value}"
    assert C.ttl_for("repo.metadata") == before


def test_declare_invariant_may_tighten() -> None:
    key = "repo.tightenable"
    C.declare_invariant(key, ttl_s=600, argv=OK_ARGV, projection="p1", project=lambda d: d)
    C.declare_invariant(key, ttl_s=60, argv=OK_ARGV, projection="p1", project=lambda d: d)
    assert C.ttl_for(key) == 60
    C.INVARIANTS.pop(key, None)


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 6. GUARD 2 — NO KEY CACHES A WHOLE BODY. BEHAVIOURAL, not flag-string inspection.
# ══════════════════════════════════════════════════════════════════════════════════════════════
def test_registry_keys_all_declare_a_projection_and_a_callable() -> None:
    for key, spec in C.INVARIANTS.items():
        assert callable(spec.project), f"{key!r} has no projection callable"
        assert spec.projection and spec.projection.strip() != ".", f"{key!r} has no projection id"


VOLATILE = {
    "status": "online",
    "busy": True,
    "pushed_at": "2020-01-01T00:00:00Z",
    "updated_at": "2020-01-01T00:00:00Z",
    "created_at": "2020-01-01T00:00:00Z",
    "open_issues_count": 7,
    "stargazers_count": 9,
    "forks_count": 3,
    "size": 12345,
    "archived": False,
    "ephemeral": True,
    "total_count": 99,
    "enforcement_level": "everyone",
    "topics": ["a"],
    "plan": {"name": "free"},
}

#: A REALISTIC body per endpoint, carrying volatile fields beside the invariant ones.
SHAPED = {
    "repo.metadata": [
        {
            **VOLATILE,
            "id": 1,
            "node_id": "N1",
            "full_name": "o/r",
            "default_branch": "main",
            "visibility": "private",
        }
    ],
    "repo.required_set": [
        {
            **VOLATILE,
            "required_status_checks": {"contexts": ["docs"], "checks": [{"context": "docs"}]},
        }
    ],
    "repo.labels": [[{**VOLATILE, "name": "bug", "color": "fff"}]],
    "repo.workflows": [
        {
            **VOLATILE,
            "workflows": [
                {**VOLATILE, "id": 3, "path": ".github/workflows/ci.yml", "state": "active"}
            ],
        }
    ],
    "runners.inventory": [
        {
            **VOLATILE,
            "runners": [
                {**VOLATILE, "name": "cmux", "labels": [{"name": "self-hosted", "color": "fff"}]}
            ],
        }
    ],
    "user.identity": [{**VOLATILE, "login": "someone", "id": 42}],
}


@pytest.mark.parametrize("key", sorted(C.INVARIANTS))
def test_shipped_projections_drop_volatile_fields(key: str) -> None:
    """⛔ BEHAVIOURAL GUARD 2. Feed a REAL body stuffed with volatile fields; none may survive.

    Strictly stronger than inspecting a `--jq` string: it tests what the code DOES to a realistic
    payload, so it keeps working if the projection is rewritten, and it cannot be satisfied by an
    argv that merely mentions a flag.
    """
    out = C.INVARIANTS[key].project(SHAPED[key])
    dumped = json.dumps(out)
    for field in (
        "pushed_at",
        "updated_at",
        "created_at",
        "open_issues_count",
        "stargazers_count",
        "forks_count",
        "archived",
        "ephemeral",
        "total_count",
        "enforcement_level",
        "topics",
        "plan",
        "busy",
        "status",
        "color",
    ):
        assert field not in dumped, f"{key!r} leaked volatile field {field!r}: {dumped}"


@pytest.mark.parametrize("key", sorted(C.INVARIANTS))
def test_shipped_projections_keep_their_invariant_fields(key: str) -> None:
    """The other half: a projection that dropped EVERYTHING would pass the test above."""
    out = C.INVARIANTS[key].project(SHAPED[key])
    assert out, f"{key!r} projected to an empty answer from a well-formed body"
    dumped = json.dumps(out)
    for expected in {
        "repo.metadata": ("o/r", "main"),
        "repo.required_set": ("docs",),
        "repo.labels": ("bug",),
        "repo.workflows": (".github/workflows/ci.yml",),
        "runners.inventory": ("cmux", "self-hosted"),
        "user.identity": ("someone",),
    }[key]:
        assert expected in dumped, f"{key!r} DROPPED invariant value {expected!r}: {dumped}"


# ── D3: a body the projection does not recognise must RAISE, never project nothing ────────────
@pytest.mark.parametrize(
    ("key", "body"),
    [
        # An error document projected to a complete-looking all-None identity, cached 6 h fresh.
        ("repo.metadata", [{"message": "Server Error"}]),
        ("repo.metadata", [{"whatever": 1}]),
        ("user.identity", [{"message": "Bad credentials"}]),
        # A wrong-shaped PAGE was silently dropped, so page 1 looked like the whole answer.
        ("repo.labels", [[{"name": "a"}], {"message": "oops"}]),
        ("repo.labels", [{"message": "oops"}]),
        ("repo.workflows", [[{"id": 1}]]),
        ("repo.workflows", [{"message": "oops"}]),
        ("runners.inventory", [[{"name": "r"}]]),
        ("runners.inventory", [{"message": "oops"}]),
        # A missing collection is not an empty collection.
        ("runners.inventory", [{"total_count": 0}]),
        ("repo.workflows", [{"total_count": 3}]),
        # An error body that ALSO carries the collection key: only the `message` marker catches
        # this one, so without it an error projects to a cached "empty answer".
        ("repo.workflows", [{"message": "oops", "workflows": []}]),
        ("runners.inventory", [{"message": "oops", "runners": []}]),
        # GitHub's REAL error shape (message + documentation_url + status).
        (
            "repo.metadata",
            [{"message": "Not Found", "documentation_url": "https://x", "status": "404"}],
        ),
        # No page at all.
        ("repo.metadata", []),
    ],
)
def test_a_wrong_shaped_body_is_Unreadable_not_an_empty_answer(key: str, body) -> None:
    """⛔ D3. `{"message": "Server Error"}` used to cache `{"id": None, …}` as fresh for six hours,
    and a wrong-shaped page 2 used to be dropped so page 1 looked like the whole answer.

    A fresh entry built from a body the code did not understand is a LIE, not a saving.
    """
    with pytest.raises(C.Unreadable):
        C.INVARIANTS[key].project(body)


def test_an_explicitly_empty_collection_is_a_valid_answer() -> None:
    """An EMPTY list is a real answer — `{"total_count": 0, "runners": []}`. A MISSING list is not."""
    assert C.INVARIANTS["runners.inventory"].project([{"total_count": 0, "runners": []}]) == []
    assert C.INVARIANTS["repo.workflows"].project([{"total_count": 0, "workflows": []}]) == []


def test_an_empty_label_PAGE_is_a_valid_answer() -> None:
    """A page that is an empty array is a real answer; a MISSING page is not (`no page to project`)."""
    assert C.INVARIANTS["repo.labels"].project([[]]) == []


def test_runner_inventory_projection_drops_status_and_busy() -> None:
    spec = C.INVARIANTS["runners.inventory"]
    page = {
        "runners": [
            {
                "name": "r1",
                "status": "online",
                "busy": True,
                "labels": [{"name": "self-hosted", "type": "read-only"}],
            },
        ]
    }
    out = spec.project([page])
    assert out == [{"name": "r1", "labels": ["self-hosted"]}], out
    assert "status" not in json.dumps(out) and "busy" not in json.dumps(out)


def test_repo_metadata_projection_keeps_only_identity_fields() -> None:
    spec = C.INVARIANTS["repo.metadata"]
    out = spec.project(
        [
            {
                "id": 1,
                "full_name": "o/r",
                "default_branch": "main",
                "visibility": "private",
                "node_id": "N",
                "pushed_at": "x",
                "open_issues_count": 5,
            }
        ]
    )
    assert set(out) == {"id", "node_id", "full_name", "default_branch"}
    # ⛔ `visibility` flips public -> private; a stale "public" turns a quoting gate into a false
    # PASS, so it is deliberately NOT cached.
    assert "visibility" not in out


def test_declare_invariant_requires_a_projection_callable() -> None:
    with pytest.raises(C.TtlNotPermitted):
        C.declare_invariant(
            "repo.noproj", ttl_s=600, argv=("api", "x"), projection="p", project=None
        )  # type: ignore[arg-type]


@pytest.mark.parametrize("proj", ["", "   ", "."])
def test_declare_invariant_requires_a_real_projection_id(proj: str) -> None:
    with pytest.raises(C.TtlNotPermitted):
        C.declare_invariant(
            "repo.badproj", ttl_s=600, argv=("api", "x"), projection=proj, project=lambda d: d
        )


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 7. THE `--slurp`/`--jq` STRUCTURAL FIX (cycle-4 blockers 1 and 2)
# ══════════════════════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "flag", ["--jq", "-q", "--slurp", "--template", "-t", "--jq=.", "--template=x"]
)
def test_argv_may_not_carry_gh_projection_flags(flag: str) -> None:
    """⛔ MEASURED: `gh api --paginate --slurp --jq '…'` → exit 1, 'the `--slurp` option is not
    supported with `--jq` or `--template'`.

    So requiring BOTH made every paginated key dead — and pflag takes the LAST `--jq` while a
    validator sees the FIRST, so `--jq '{id}' --jq '.'` passed validation and cached the whole
    body. Projection is this module's job; the argv may not carry these at all.
    """
    with pytest.raises(C.TtlNotPermitted):
        C.declare_invariant(
            "repo.flagged", ttl_s=600, argv=("api", "x", flag), projection="p", project=lambda d: d
        )


def test_no_shipped_key_carries_a_projection_flag() -> None:
    for key, spec in C.INVARIANTS.items():
        for a in spec.argv:
            assert a not in C._FORBIDDEN_ARGV_FLAGS, f"{key!r} carries {a!r}"


@pytest.mark.parametrize("key", ["repo.labels", "repo.workflows", "runners.inventory"])
def test_paginated_keys_still_paginate(key: str) -> None:
    assert "--paginate" in C.INVARIANTS[key].argv, f"{key!r} must still fetch every page"


# ── decode_pages: what replaces `--slurp` ────────────────────────────────────────────────────
def test_decode_pages_handles_several_concatenated_documents() -> None:
    """`gh api --paginate` without `--slurp` emits one document PER PAGE."""
    raw = b'[{"name":"a"}]\n[{"name":"b"}]'
    assert C.decode_pages(raw) == [[{"name": "a"}], [{"name": "b"}]]


def test_decode_pages_handles_a_single_document() -> None:
    assert C.decode_pages(b'{"id": 1}') == [{"id": 1}]


def test_decode_pages_handles_object_pages() -> None:
    raw = b'{"workflows":[{"id":1}]}{"workflows":[{"id":2}]}'
    assert C.decode_pages(raw) == [{"workflows": [{"id": 1}]}, {"workflows": [{"id": 2}]}]


@pytest.mark.parametrize("raw", [b"", b"   \n\t ", b"not json at all", b"<html>504</html>"])
def test_decode_pages_refuses_a_non_json_body(raw: bytes) -> None:
    with pytest.raises(C.Unreadable):
        C.decode_pages(raw)


def test_projection_over_multiple_pages_concatenates() -> None:
    spec = C.INVARIANTS["repo.labels"]
    assert spec.project([[{"name": "a"}], [{"name": "b"}, {"name": "c"}]]) == ["a", "b", "c"]


def test_the_shipped_runners_projection_drops_status_and_busy_and_the_argv_carries_no_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """⛔ WHAT THE SHIPPED CODE ACTUALLY DOES (R2), pinned as such: `_gh_reader` returns the WHOLE
    body and `read()` applies the PROJECTION — the layering is deliberate, so a caller supplying
    its own reader is protected too (see `_gh_reader`, "RAW BYTES. The PROJECTION is applied by
    `read()`, never here").

    This drives the real reader with a stubbed `subprocess.run` (no network) and asserts it hands
    back the UNPROJECTED bytes (`status`/`busy` still present — so the projection's effect below is
    the projection's), that the projection drops those volatile fields, and that the argv `gh`
    would receive carries no `--jq`/`--slurp`.
    """
    seen: list[list[str]] = []

    class Proc:
        returncode = 0
        stderr = b""
        # gh's real shape for this endpoint is one OBJECT per page, not an array of objects.
        stdout = json.dumps(
            {
                "total_count": 1,
                "runners": [
                    {
                        "name": "r1",
                        "status": "online",
                        "busy": True,
                        "labels": [{"name": "self-hosted"}],
                    }
                ],
            }
        ).encode()

    def fake_run(cmd, **kw):
        seen.append(list(cmd))
        return Proc()

    monkeypatch.setattr(C.subprocess, "run", fake_run)
    raw = C._gh_reader("o/r", "main")("runners.inventory")

    # ⛔ The reader returns RAW bytes on purpose: the projection belongs to `read()`, so that a
    # caller supplying its own reader is protected too. Projecting here would leave the library
    # path caching whole bodies (measured: `pushed_at` served for a 6-hour TTL).
    # ⛔ THE LAYERING ITSELF, observed rather than assumed: the reader hands back the WHOLE body.
    # Without this arm the test cannot support its name — it would only show that the TEST's own
    # `_project(...)` call drops the fields, not that `_gh_reader` leaves them for `read()`.
    assert b"status" in raw and b"busy" in raw, (
        "the reader projected; R2 puts the projection in read(), never in _gh_reader"
    )

    stored = json.loads(C._project("runners.inventory", raw))
    assert stored == [{"name": "r1", "labels": ["self-hosted"]}], (
        f"the projection let volatile fields through: {stored!r}"
    )
    assert "online" not in json.dumps(stored) and "busy" not in json.dumps(stored)
    assert seen and "--paginate" in seen[0]
    for flag in C._FORBIDDEN_ARGV_FLAGS:
        assert flag not in seen[0], f"the argv handed to gh carries {flag!r}"


def test_gh_reader_surfaces_a_nonzero_exit_as_unreadable(monkeypatch: pytest.MonkeyPatch) -> None:
    class Proc:
        returncode = 1
        stdout = b""
        stderr = b"gh: HTTP 403"

    monkeypatch.setattr(C.subprocess, "run", lambda cmd, **kw: Proc())
    with pytest.raises(C.Unreadable) as excinfo:
        C._gh_reader("o/r", "main")("repo.metadata")
    assert "403" in str(excinfo.value)


def test_gh_reader_surfaces_a_projection_failure_as_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A projection that cannot run must not yield a cached answer."""
    key = "repo.boom_projection"

    def boom(docs):
        raise KeyError("missing field")

    C.declare_invariant(key, ttl_s=600, argv=OK_ARGV, projection="boom", project=boom)
    try:

        class Proc:
            returncode = 0
            stderr = b""
            stdout = b'{"ok": true}'

        monkeypatch.setattr(C.subprocess, "run", lambda cmd, **kw: Proc())
        with pytest.raises(C.Unreadable):
            C._project(key, C._gh_reader("o/r", "main")(key))
    finally:
        C.INVARIANTS.pop(key, None)


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 8. PROJECTION IS PART OF THE CACHE IDENTITY (guard 4)
# ══════════════════════════════════════════════════════════════════════════════════════════════
def test_changing_a_projection_invalidates_the_cached_entry(tmp_path: Path) -> None:
    """A maintenance edit to a shipped projection must not serve OLD-schema entries as fresh.

    `repo.metadata` holds 6 h, so otherwise a newly-added field would be missing for six hours.
    """
    key = "repo.projection_probe"
    C.declare_invariant(key, ttl_s=600, argv=OK_ARGV, projection="v1", project=lambda d: {"id": 1})
    try:
        r1 = Counter()
        _read(key, now=1000.0, reader=r1, cache_dir=tmp_path)
        assert r1.n == 1

        C.INVARIANTS[key] = C.Invariant(
            ttl_s=600,
            argv=OK_ARGV,
            projection="v2",
            project=lambda d: {"id": 1, "default_branch": "main"},
        )
        r2 = Counter()
        _read(key, now=1000.0 + 1.0, reader=r2, cache_dir=tmp_path)
        assert r2.n == 1, "an entry selected by the OLD projection was served as a fresh hit"
    finally:
        C.INVARIANTS.pop(key, None)


def test_envelope_projection_must_match(tmp_path: Path) -> None:
    """The second layer: a file at the right NAME is still rejected if its projection disagrees."""
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    p = _entry(tmp_path)
    env = json.loads(p.read_text())
    env["projection"] = C._projection_digest("a-different-projection")
    p.write_text(json.dumps(env))
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    assert r.n == 2


def test_envelope_digest_is_over_the_payload_only() -> None:
    env = json.loads(C._envelope("repo.metadata", SCOPE, b"abc", now=1.0, ttl_s=2).decode())
    assert env["sha256"] == hashlib.sha256(b"abc").hexdigest()


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 9. ATOMICITY (guard R8)
# ══════════════════════════════════════════════════════════════════════════════════════════════
def test_partial_entry_from_a_killed_writer_is_a_miss(tmp_path: Path) -> None:
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    p = _entry(tmp_path)
    p.write_bytes(p.read_bytes()[:10])
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    assert r.n == 2


def test_store_is_atomic_a_failed_replace_leaves_the_old_entry_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⛔ Discriminating: a regression to `path.write_bytes(blob)` would pass every other test."""
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    good = _entry(tmp_path).read_bytes()

    def boom(src, dst):
        raise OSError("simulated crash during replace")

    monkeypatch.setattr(C.os, "replace", boom)

    def reader_then_crash(key: str) -> bytes:
        return json.dumps(
            {"id": 999, "node_id": "N999", "full_name": "o/r", "default_branch": "main"}
        ).encode()

    got = _read(
        "repo.metadata",
        now=1000.0 + C.ttl_for("repo.metadata") + 1,
        reader=reader_then_crash,
        cache_dir=tmp_path,
    )
    assert json.loads(got)["id"] == 999, (
        "a failed cache WRITE must not fail a read that just succeeded"
    )
    assert _entry(tmp_path).read_bytes() == good, (
        "a crashed swap must not have touched the existing entry — the write was not staged"
    )
    assert not [p for p in tmp_path.iterdir() if ".tmp." in p.name], "staged temp file leaked"


def test_import_time_guard_rejects_a_hand_registered_whole_body_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """⛔ MUTATION 9's FENCE. `INVARIANTS[k] = …` bypasses `declare_invariant`, so the import-time
    guard has to be re-run — and its behaviour must be pinned, not just its presence."""
    key = "repo.hand_registered"
    C.INVARIANTS[key] = C.Invariant(ttl_s=600, argv=("api", "x"), projection=".", project=None)  # type: ignore[arg-type]
    try:
        with pytest.raises(C.TtlNotPermitted):
            C._assert_every_key_projects()
    finally:
        C.INVARIANTS.pop(key, None)


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 10. CYCLE-5 FINDINGS — the paths that were described but NOT pinned
# ══════════════════════════════════════════════════════════════════════════════════════════════
def test_the_import_time_registry_guard_actually_RAN() -> None:
    """⛔ The call was bare, so it could be deleted with all 107 earlier tests still green
    (every test called `_assert_every_key_projects` DIRECTLY). It is now bound to `_IMPORT_CHECK`,
    so deleting the call site raises AttributeError here."""
    # ⛔ `id(spec)` is the proof that a CALL happened. A literal `frozenset(INVARIANTS)` reproduces
    # the key SET and left all 172 tests green (measured); a count was equally spoofable (`6`). An
    # object id can only come from a loop that actually visited the registry.
    assert frozenset(f"{k}#{id(v)}" for k, v in C.INVARIANTS.items()) == C._IMPORT_CHECK


def test_read_validates_the_spec_on_every_call(tmp_path: Path) -> None:
    """Defence in depth for guard 2: even a key inserted straight into INVARIANTS (bypassing
    `declare_invariant`) cannot be read if it declares no projection."""
    key = "repo.injected"
    C.INVARIANTS[key] = C.Invariant(ttl_s=600, argv=("api", "x"), projection=".", project=None)  # type: ignore[arg-type]
    try:
        r = Counter()
        with pytest.raises(C.TtlNotPermitted):
            _read(key, now=1000.0, reader=r, cache_dir=tmp_path)
        assert r.n == 0
    finally:
        C.INVARIANTS.pop(key, None)


def test_a_non_numeric_clock_is_refused_not_a_crash(tmp_path: Path) -> None:
    """`read(now="1000")` reached `math.isfinite` and raised TypeError — unhandled, where the
    contract promises "any doubt → MISS"."""
    r = Counter()
    # A NUMERIC STRING is accepted (it converts cleanly); a non-number is REFUSED, where the old
    # code crashed with an unhandled TypeError from inside the freshness test.
    for bad in ("not-a-number", None, object()):
        with pytest.raises(C.TtlNotPermitted):
            _read("repo.metadata", now=bad, reader=r, cache_dir=tmp_path)  # type: ignore[arg-type]
    assert r.n == 0
    assert json.loads(_read("repo.metadata", now="1000", reader=r, cache_dir=tmp_path))["id"] == 1


def test_an_Infinity_stamped_envelope_is_a_MISS_not_a_latched_crash(tmp_path: Path) -> None:
    """⛔ D2. `json.loads` ACCEPTS the bare `Infinity` literal and `int(inf)` raises OverflowError,
    which is NOT a ValueError — so it escaped `_load`, escaped `read()`'s contract, escaped
    `main()`'s handlers, and LATCHED: the read never reached the reader, so the bad entry was never
    rewritten and every later read of that key raised too, until `--purge`."""
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    p = _entry(tmp_path)
    env = json.loads(p.read_text())
    env["ttl_s"] = float("inf")
    p.write_text(json.dumps(env))  # written as the bare Infinity literal, which json accepts

    out = _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)  # must NOT raise
    assert r.n == 2, "the malformed entry must be a MISS, and the MISS must rewrite it"
    assert json.loads(out)["id"] == 2
    # ...and it must be HEALED: the next read is a normal hit, not another failure.
    _read("repo.metadata", now=1000.0 + 1.0, reader=r, cache_dir=tmp_path)
    assert r.n == 2, "the bad entry was not healed by the re-read"


@pytest.mark.parametrize("field", ["stored_at", "ttl_s"])
def test_a_non_finite_envelope_field_is_a_miss(field: str, tmp_path: Path) -> None:
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    p = _entry(tmp_path)
    env = json.loads(p.read_text())
    env[field] = float("nan") if field == "stored_at" else float("-inf")
    p.write_text(json.dumps(env))
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    assert r.n == 2


# ── D4: the digest tracks the SOURCE, not the hand-written id ──────────────────────────────
def test_projection_digest_changes_when_the_source_changes() -> None:
    """⛔ An id is a RENAME-detector. Editing the projection BODY while leaving `…-v1` in place used
    to serve the old-schema entry as a fresh hit for the whole TTL."""

    def v_one(docs):
        return {"id": 1}

    def v_two(docs):
        return {"id": 1, "full_name": "o/r"}

    assert C._projection_digest("same-id", v_one) != C._projection_digest("same-id", v_two)
    assert C._projection_digest("same-id", v_one) == C._projection_digest("same-id", v_one)
    assert C._projection_digest("a", v_one) != C._projection_digest("b", v_one)


def test_shipped_projections_are_identified_by_source_not_only_by_id() -> None:
    for key, spec in C.INVARIANTS.items():
        with_source = C._projection_digest(spec.projection, spec.project)
        id_only = C._projection_digest(spec.projection)
        assert with_source != id_only, (
            f"{key!r}: the source contributes nothing to the identity, so an edit to the "
            f"projection body would not invalidate the cached entry"
        )


def test_a_cached_entry_stops_matching_when_the_shipped_projection_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = "repo.metadata"
    r = Counter()
    _read(key, now=1000.0, reader=r, cache_dir=tmp_path)
    assert r.n == 1

    base = C.INVARIANTS[key].project  # capture BEFORE patching, or it recurses into itself

    def widened(docs):
        return {**base(docs), "new_field": True}

    import dataclasses

    monkeypatch.setitem(  # same declared id — only the BODY changed
        C.INVARIANTS, key, dataclasses.replace(C.INVARIANTS[key], project=widened)
    )
    r2 = Counter()
    _read(key, now=1000.0 + 1.0, reader=r2, cache_dir=tmp_path)
    assert r2.n == 1, "an entry built from the OLD projection was served after the body changed"


# ── the CLI, which was entirely untested ──────────────────────────────────────────────────
class _Err:
    """Stands in for sys.stderr so a CLI refusal can be ASSERTED rather than eyeballed.

    The refusal line IS the whole operator-facing detail on a failed read, so the arms that pin it
    need the text, not a stream that pytest captures.
    """

    def __init__(self) -> None:
        self.buffer = __import__("io").BytesIO()

    def write(self, s: str) -> int:
        self.buffer.write(s.encode())
        return len(s)

    def flush(self) -> None:
        return None


class _Out:
    """Stands in for sys.stdout so `main()`'s `sys.stdout.buffer.write` works under pytest."""

    def __init__(self) -> None:
        self.buffer = __import__("io").BytesIO()
        self.text: list[str] = []

    def write(self, s: str) -> int:
        self.text.append(s)
        return len(s)

    def flush(self) -> None:
        return None


def test_cli_list_prints_the_registry() -> None:
    import io
    import sys as _sys

    buf = io.StringIO()
    old = _sys.stdout
    _sys.stdout = buf
    try:
        rc = C.main(["--list"])
    finally:
        _sys.stdout = old
    assert rc == 0
    # ⛔ VACUOUS ASSERTION REMOVED: `assert out is not None` read a variable that was never
    # installed as stdout, so it proved nothing about the listing.
    for key in C.INVARIANTS:
        assert key in buf.getvalue()


def test_cli_purge_removes_entries(tmp_path: Path) -> None:
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    assert list(tmp_path.iterdir())
    rc = C.main(["--purge", "--cache-dir", str(tmp_path)])
    assert rc == 0
    assert not list(tmp_path.iterdir()), "--purge left entries behind"


def test_cli_requires_a_key(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        C.main(["--cache-dir", str(tmp_path)])


def test_cli_refuses_a_mutable_key(tmp_path: Path) -> None:
    assert C.main(["--key", "pr.state", "--repo", "o/r", "--cache-dir", str(tmp_path)]) == 1


def test_cli_no_cache_reads_live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_reader(repo: str, branch: str):
        def _r(key: str) -> bytes:
            calls.append(key)
            return json.dumps(
                {"id": 5, "node_id": "N5", "full_name": "o/r", "default_branch": "main"}
            ).encode()

        return _r

    monkeypatch.setattr(C, "_gh_reader", fake_reader)
    out = _Out()
    monkeypatch.setattr(C.sys, "stdout", out)
    rc = C.main(
        ["--key", "repo.metadata", "--repo", "o/r", "--cache-dir", str(tmp_path), "--no-cache"]
    )
    assert rc == 0
    emitted = json.loads(out.buffer.getvalue())
    assert emitted["id"] == 5
    # ⛔ `--no-cache` bypasses the CACHE, not the projection: one key must not have two schemas.
    assert "full_name" in emitted and set(emitted) <= {
        "id",
        "node_id",
        "full_name",
        "default_branch",
        "visibility",
    }
    assert calls == ["repo.metadata"]
    assert not list(tmp_path.iterdir()), "--no-cache must not write an entry"


def test_cli_global_key_without_a_token_still_reads_LIVE(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⛔ D5. `scope_for` correctly refuses to guess an account, but the CLI must NOT dead-end
    there: the promise is a live read, and that branch had ZERO coverage — deleting it silently
    turned a live read into `REFUSED exit 1`."""
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    calls: list[str] = []

    def fake_reader(repo: str, branch: str):
        def _r(key: str) -> bytes:
            calls.append(key)
            # extra volatile fields, so a RAW emit is distinguishable from a PROJECTED one
            return json.dumps(
                {"login": "someone", "id": 9, "plan": {"name": "free"}, "created_at": "2020-01-01"}
            ).encode()

        return _r

    monkeypatch.setattr(C, "_gh_reader", fake_reader)
    out = _Out()
    monkeypatch.setattr(C.sys, "stdout", out)
    rc = C.main(["--key", "user.identity", "--cache-dir", str(tmp_path)])
    assert rc == 0, "a global key with no identifiable credential must be read LIVE, not refused"
    emitted = json.loads(out.buffer.getvalue())
    assert emitted["login"] == "someone"
    # ⛔ The GLOBAL live-read path must project too — it is a second exit that emitted the RAW body,
    # giving one key two schemas.
    assert set(emitted) <= {"login", "id"}, f"the global live path emitted a raw body: {emitted}"
    assert calls == ["user.identity"]
    assert not list(tmp_path.iterdir()), "an unidentifiable-credential read must not be cached"


def test_cli_honours_a_tightened_ttl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_reader(repo: str, branch: str):
        def _r(key: str) -> bytes:
            calls.append(key)
            return json.dumps(
                {"id": 1, "node_id": "N1", "full_name": "o/r", "default_branch": "main"}
            ).encode()

        return _r

    monkeypatch.setattr(C, "_gh_reader", fake_reader)
    out = _Out()
    monkeypatch.setattr(C.sys, "stdout", out)
    assert (
        C.main(
            ["--key", "repo.metadata", "--repo", "o/r", "--cache-dir", str(tmp_path), "--ttl", "60"]
        )
        == 0
    )
    assert (
        C.main(
            ["--key", "repo.metadata", "--repo", "o/r", "--cache-dir", str(tmp_path), "--ttl", "60"]
        )
        == 0
    )
    assert len(calls) == 1, "--ttl 60 should have produced one live read and one hit"


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 11. CYCLE-6 FINDINGS — "an identity with nulls" is not an answer
# ══════════════════════════════════════════════════════════════════════════════════════════════
def test_required_set_ABSENT_is_not_the_same_as_NULL() -> None:
    """⛔ THE MERGE-GATING KEY. An empty required set reads as "this branch requires no checks".

    Measured before this check: `[{}]` — and a BRANCH OBJECT (`{"id":1,"name":"main",
    "protection":{}}`) — both projected to `{"contexts": [], "checks": []}` and were cached for 30
    minutes. `required_status_checks: null` is the legitimate answer; an ABSENT key is not.
    """
    for body in ([{}], [{"id": 1, "name": "main", "protection": {}}]):
        with pytest.raises(C.Unreadable):
            C.INVARIANTS["repo.required_set"].project(body)


def test_required_set_null_is_the_legitimate_no_checks_answer() -> None:
    assert C.INVARIANTS["repo.required_set"].project([{"required_status_checks": None}]) == {
        "contexts": [],
        "checks": [],
    }


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 11b. THE DOCUMENTED NEGATIVE — `404 Branch not protected` is an ANSWER, not a read failure
# ══════════════════════════════════════════════════════════════════════════════════════════════
#: The UNPROTECTED-branch response, byte for byte as measured on this repo
#: (`gh api repos/daniel-ospina/tortoise/branches/quota-discipline-2/protection` → rc=1).
_UNPROTECTED_BODY = json.dumps(
    {
        "message": "Branch not protected",
        "documentation_url": "https://docs.github.com/rest/branches/branch-protection",
        "status": "404",
    }
).encode()
_UNPROTECTED_STDERR = b"gh: Branch not protected (HTTP 404)\n"


def _proc(returncode: int, *, stdout: bytes = b"", stderr: bytes = b""):
    """A stand-in for `subprocess.run`'s CompletedProcess, with gh's real field names."""

    class Proc:
        pass

    p = Proc()
    p.returncode = returncode
    p.stdout = stdout
    p.stderr = stderr
    return p


def test_an_unprotected_branch_is_the_documented_negative_not_a_read_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """⛔ THE COMMON CASE OF THE MERGE-GATING KEY, and it used to be reported as a FAULT.

    An unprotected branch has no protection rules, so GitHub answers `404 Branch not protected`
    rather than an empty required set. Raising `Unreadable` for that turned the legitimate answer
    ("this branch requires no checks") into "I could not read" — indistinguishable from an outage,
    on the merge-gating key, and re-spent on every poll. It is a VALUE now.
    """
    monkeypatch.setattr(
        C.subprocess,
        "run",
        lambda cmd, **kw: _proc(1, stdout=_UNPROTECTED_BODY, stderr=_UNPROTECTED_STDERR),
    )
    raw = C._gh_reader("o/r", "unprotected")("repo.required_set")
    # The sentinel travels the key's OWN projection, so the answer has the one documented shape.
    assert json.loads(C._project("repo.required_set", raw)) == {"contexts": [], "checks": []}
    # ⛔ THE STDERR SPELLING, END TO END. Measured: the body arm returns first on every arm that
    # reaches here, so deleting the stderr return left the suite GREEN (338 passed) — this call is
    # the one that reds it. `gh`'s prose is the ONLY identifier when stdout is empty.
    monkeypatch.setattr(
        C.subprocess, "run", lambda cmd, **kw: _proc(1, stdout=b"", stderr=_UNPROTECTED_STDERR)
    )
    raw = C._gh_reader("o/r", "unprotected")("repo.required_set")
    assert json.loads(C._project("repo.required_set", raw)) == {"contexts": [], "checks": []}


def test_the_documented_negative_is_pinned_on_ONE_ROUTE_and_in_BOTH_spellings() -> None:
    """⛔ THE ROUTE GUARD, PINNED DIRECTLY — because the arm below could not fail for it.

    Measured: deleting the `_PROTECTION_SEGMENTS` comparison inside `_is_documented_negative` left
    the suite at **338 passed**, because the `repo.metadata` arm below is refused by that key's
    `negative=None` BEFORE the comparison ever runs. That is this file's own "fence that could not
    fail for the bug it names" class, so the route is asserted on the helper as well as through it.
    """
    argv = C.INVARIANTS["repo.required_set"].argv
    # The negative, on the one route that can answer it.
    assert C._is_documented_negative(argv, _UNPROTECTED_BODY, _UNPROTECTED_STDERR) is True
    # ⛔ THE SAME BODY, OFF THE PROTECTION ROUTE — not the negative.
    assert (
        C._is_documented_negative(
            ("api", "repos/{repo}"), _UNPROTECTED_BODY, _UNPROTECTED_STDERR
        )
        is False
    )
    # The stderr spelling with the body DRAINED, and a different 404's prose refused.
    assert C._is_documented_negative(argv, b"", _UNPROTECTED_STDERR) is True
    assert C._is_documented_negative(argv, b"", b"gh: Not Found (HTTP 404)\n") is False


def test_the_CLI_live_paths_walk_the_DOOR_and_name_the_REAL_fault(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """⛔ THE LIVE PATHS ARE THE ONES THAT SPEND A CALL, so they walk the same door `read()` does.

    Both CLI live exits (`--no-cache`, and the global-key fallback where `scope_for` cannot name a
    credential) call `reader(key)` directly, so `read()`'s door never ran on them. The refusal still
    happened — `_gh_reader` refuses an undeclared key before `subprocess.run` — but it was reported
    as `no argv is declared for 'pr.state'`: a REGISTRY-shaped fault that sends a triaging lane
    hunting a missing registration, when the true reason is R4's (mutable-state vocabulary).

    ⛔ THIS ARM COVERS QUARTER 1 OF THE DOOR ONLY (`ttl_for`: may this key be cached at all?). The
    second half — guard 2 and the negative's short-TTL rule — is pinned by
    `test_the_no_cache_path_REFUSES_a_spec_that_padding_guard_2_would_pass`, because replacing
    `_door(a.key)` with `ttl_for(a.key)` here satisfied BOTH needles below and left the suite green
    (measured). A parametrized arm that passes for an unrelated reason is not coverage.
    """

    def explode(cmd, **kw):  # the door must refuse BEFORE any call is made
        raise AssertionError(f"the door let a live call through: {cmd}")

    monkeypatch.setattr(C.subprocess, "run", explode)
    for key, needle in (
        ("pr.state", "mutable read"),
        ("acme.whatever", "is not a declared invariant"),
    ):
        err = _Err()
        out = _Out()
        monkeypatch.setattr(C.sys, "stderr", err)
        monkeypatch.setattr(C.sys, "stdout", out)
        rc = C.main(
            [
                "--no-cache",
                "--key",
                key,
                "--repo",
                "o/r",
                "--branch",
                "main",
                "--cache-dir",
                str(tmp_path),
            ]
        )
        assert rc == 1, f"{key} must be REFUSED, not read live"
        assert needle in err.buffer.getvalue().decode(), (
            f"{key} was refused with the wrong reason: {err.buffer.getvalue()!r} — the message IS "
            f"the whole operator-facing detail, so it must name the refusal that fired"
        )
        assert b"no argv is declared" not in err.buffer.getvalue()


def _inject_spec(key: str, **kwargs) -> object:
    """Put a spec STRAIGHT into the registry — the bypass `_door`'s defence-in-depth exists for.

    `declare_invariant` is the guarded door; this is the door being walked around, which is exactly
    the shape an import-time-only check cannot see (the M85/M88 rows' bypass).
    """
    saved = C.INVARIANTS[key]
    C.INVARIANTS[key] = C.Invariant(**kwargs)
    return saved


def test_the_no_cache_path_REFUSES_a_spec_that_padding_guard_2_would_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """⛔ THE DOOR'S SECOND QUESTION, ON THE PATH THAT SPENDS THE CALL.

    `_door` asks two things: (1) may this key be cached at all, (2) is its spec still valid — guard 2
    (no whole body, no projection flags) and the negative's short-TTL rule. `read()` re-checks (2)
    at run time because the import check runs ONCE; the `--no-cache` and global-fallback paths skip
    `read()` entirely, so without `_door` there is (2) nowhere on the path that actually calls `gh`.

    ⛔ MEASURED: with `_door(a.key)` replaced by `ttl_for(a.key)` — which answers (1) only — the
    whole suite stayed GREEN (347 passed), because the sibling arm's two keys are both refused by
    (1). This arm injects a spec whose ARGV carries `--jq` (guard 2's own forbidden flag) and
    requires the live path to refuse WITHOUT spending the call.
    """

    def explode(cmd, **kw):
        raise AssertionError(f"the door let a live call through: {cmd}")

    monkeypatch.setattr(C.subprocess, "run", explode)
    saved = _inject_spec(
        "repo.metadata",
        ttl_s=1800,
        argv=("api", "repos/{repo}", "--jq", "."),
        projection="inj-v1",
        project=C.INVARIANTS["repo.metadata"].project,
    )
    try:
        err = _Err()
        monkeypatch.setattr(C.sys, "stderr", err)
        monkeypatch.setattr(C.sys, "stdout", _Out())
        rc = C.main(
            [
                "--no-cache",
                "--key",
                "repo.metadata",
                "--repo",
                "o/r",
                "--branch",
                "main",
                "--cache-dir",
                str(tmp_path),
            ]
        )
        assert rc == 1, "a spec violating guard 2 must be REFUSED on the live path"
        assert b"REFUSED" in err.buffer.getvalue(), err.buffer.getvalue()
        assert b"--jq" in err.buffer.getvalue(), (
            "the refusal must name the forbidden flag, not leave the caller guessing"
        )
    finally:
        C.INVARIANTS["repo.metadata"] = saved


def test_the_global_fallback_walks_the_DOOR_too(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """⛔ THE SIBLING LIVE EXIT: `scope_for` cannot name a credential, so `main()` reads live.

    ⛔ THIS ARM EXISTS BECAUSE THE FIRST CUT OF THE LEDGER ROW BELOW CLAIMED THE GAP WAS
    "STRUCTURAL — no test can make that particular call observable". That was FALSE, and it was the
    sole justification for leaving a live guard unpinned: a spec injected straight into
    `INVARIANTS` reaches this path (the suite's own established bypass technique), the guard refuses
    BEFORE `subprocess.run`, and deleting the `_door` call there spends a real call on it.
    """

    def explode(cmd, **kw):
        raise AssertionError(f"the door let a live call through: {cmd}")

    monkeypatch.setattr(C.subprocess, "run", explode)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    saved = _inject_spec(
        "user.identity",
        ttl_s=43200,
        argv=("api", "user", "--jq", "."),
        projection="inj-v1",
        project=C.INVARIANTS["user.identity"].project,
    )
    try:
        err = _Err()
        monkeypatch.setattr(C.sys, "stderr", err)
        monkeypatch.setattr(C.sys, "stdout", _Out())
        rc = C.main(["--key", "user.identity", "--cache-dir", str(tmp_path)])
        assert rc == 1, "the global fallback must REFUSE a guard-2-violating spec"
        stderr = err.buffer.getvalue()
        assert b"live read (not cached)" in stderr, stderr
        assert b"REFUSED" in stderr, stderr
        assert b"--jq" in stderr, stderr
    finally:
        C.INVARIANTS["user.identity"] = saved


def test_the_unprotected_answer_is_a_VALUE_the_CLI_exits_0_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """⛔ NON-RETRYABLE BY CONSTRUCTION: an answer, not a `REFUSED`.

    #11052's decision asks for "a distinct non-retryable verdict". The verdict is `0` with the
    answer on stdout — nothing about it looks transient, so no caller retries it into the pool that
    this module exists to protect.
    """
    monkeypatch.setattr(
        C.subprocess,
        "run",
        lambda cmd, **kw: _proc(1, stdout=_UNPROTECTED_BODY, stderr=_UNPROTECTED_STDERR),
    )
    out = _Out()
    monkeypatch.setattr(C.sys, "stdout", out)
    rc = C.main(
        [
            "--key",
            "repo.required_set",
            "--repo",
            "o/r",
            "--branch",
            "unprotected",
            "--cache-dir",
            str(tmp_path),
        ]
    )
    assert rc == 0, "an unprotected branch must not be reported as a read failure"
    assert json.loads(out.buffer.getvalue()) == {"contexts": [], "checks": []}


def test_the_negative_is_cached_on_its_OWN_short_ttl_not_the_keys(tmp_path: Path) -> None:
    """⛔ BOTH HALVES OF THE DECISION, MEASURED — the sentinel AND its own short TTL.

    A negative is falsified by ONE human edit (someone PROTECTS the branch), so it must not sit
    behind the key's 30-minute ceiling: at an age the ceiling would still have served, this MUST go
    back to `gh`. The positive lane keeps its full ceiling — the two entries coexist.
    """
    calls: list[str] = []

    def neg_reader(key: str) -> bytes:
        calls.append(key)
        return C._NO_REQUIRED_CHECKS_BODY

    def pos_reader(key: str) -> bytes:
        calls.append(key)
        return RAW["repo.required_set"](1)

    assert json.loads(
        _read("repo.required_set", now=1000.0, reader=neg_reader, cache_dir=tmp_path)
    ) == {"contexts": [], "checks": []}
    assert len(calls) == 1
    # Fresh ONE SECOND SHORT of the negative's ttl — served from its own lane, no reader call.
    _read(
        "repo.required_set",
        now=1000.0 + C.NEGATIVE_ANSWER_TTL_S - 1,
        reader=neg_reader,
        cache_dir=tmp_path,
    )
    assert len(calls) == 1
    # ⛔ EXPIRED BY ITS OWN TTL at an age the key's 1800 s ceiling would still have served it.
    _read(
        "repo.required_set",
        now=1000.0 + C.NEGATIVE_ANSWER_TTL_S,
        reader=pos_reader,
        cache_dir=tmp_path,
    )
    assert len(calls) == 2, "the negative was served past its own short ttl"
    # And the POSITIVE answer still lives on the long lane at an age the negative could not reach.
    _read(
        "repo.required_set",
        now=1000.0 + C.NEGATIVE_ANSWER_TTL_S + 1700,
        reader=pos_reader,
        cache_dir=tmp_path,
    )
    assert len(calls) == 2, "the positive answer lost its own 30-minute lane"


def test_a_TIGHTENED_ttl_is_never_served_the_WIDER_negative(tmp_path: Path) -> None:
    """⛔ `--ttl` IS A TIGHTEN, and a tighten must never widen the negative lane.

    `_negative_ttl` returns `min(int(neg.ttl_s), effective)` for exactly this: a caller asking for
    10 s must not be handed a 60-second-old `{"contexts": [], "checks": []}` — "this branch
    requires no checks" — on the MERGE-GATING key, in the fail-OPEN direction.

    ⛔ MEASURED, NOT ASSERTED: dropping the `min()` (`return int(neg.ttl_s)`) left the WHOLE suite
    green (344 passed) before this arm existed, because every shipped key asks at its own 1800 s
    ceiling, where `min()` is indistinguishable from the declared value. It is only observable at
    `ttl_s < NEGATIVE_ANSWER_TTL_S`, which is a documented tighten, so this arm stores at the
    DEFAULT ceiling and then reads 30 s later asking for 10 s.
    """
    calls: list[str] = []

    def neg_reader(key: str) -> bytes:
        calls.append(key)
        return C._NO_REQUIRED_CHECKS_BODY

    # Stored at the key's default ceiling, on the negative's own 60 s lane.
    assert json.loads(
        _read("repo.required_set", now=1000.0, reader=neg_reader, cache_dir=tmp_path)
    ) == {"contexts": [], "checks": []}
    assert len(calls) == 1
    # 30 s of age, asked for under a 10 s ttl: the answer is OLDER THAN THE ASK, so it is a MISS
    # and must go back to `gh` — the negative's own 60 s must not override the caller's 10 s.
    _read("repo.required_set", now=1030.0, ttl_s=10, reader=neg_reader, cache_dir=tmp_path)
    assert len(calls) == 2, (
        "a 30-second-old negative was served to a caller that asked for 10 seconds — `--ttl` is a "
        "TIGHTEN, and the fail-OPEN direction on a merge gate is the one this lane exists to close"
    )


@pytest.mark.parametrize(
    ("key", "stdout", "stderr"),
    [
        # ⛔ THE ALLOW-LIST BOUNDARY. A DIFFERENT 404 on the SAME route — a repo or branch the
        # token cannot see — must stay a read failure: mapping it to "no required checks" would
        # answer a merge gate with "nothing is required" about a branch it never read.
        (
            "repo.required_set",
            json.dumps(
                {"message": "Not Found", "documentation_url": "https://x", "status": "404"}
            ).encode(),
            b"gh: Not Found (HTTP 404)\n",
        ),
        # ⛔ A KEY WITH **NO DECLARED NEGATIVE** STAYS FAIL-CLOSED. Note the MECHANISM, measured:
        # this arm is refused by `repo.metadata`'s `negative=None` before the route comparison runs,
        # so it does NOT pin the route guard — the helper is asserted directly for that, above.
        ("repo.metadata", _UNPROTECTED_BODY, _UNPROTECTED_STDERR),
    ],
)
def test_the_documented_negative_is_an_ALLOW_LIST_of_one(
    monkeypatch: pytest.MonkeyPatch, key: str, stdout: bytes, stderr: bytes
) -> None:
    """The mapping is ONE documented message, and only a key that DECLARES a negative takes it."""
    monkeypatch.setattr(C.subprocess, "run", lambda cmd, **kw: _proc(1, stdout=stdout, stderr=stderr))
    with pytest.raises(C.Unreadable):
        C._gh_reader("o/r", "main")(key)


def test_a_negative_ttl_at_or_above_the_keys_ceiling_is_refused() -> None:
    """⛔ The short TTL is a DECLARATION GUARD, not a convention: at the key's own ceiling a
    falsifiable "nothing is required" would be cached for 30 minutes."""
    spec = C.Invariant(
        ttl_s=1800,
        argv=C.INVARIANTS["repo.required_set"].argv,
        projection="probe-v1",
        project=C._project_required_set,
        negative=C.NegativeAnswer(body=C._NO_REQUIRED_CHECKS_BODY, ttl_s=1800, why="probe"),
    )
    with pytest.raises(C.TtlNotPermitted):
        C._validate_negative("repo.required_set", spec)


def test_the_negative_declaration_guard_is_WIRED_INTO_the_import_check() -> None:
    """⛔ A DIRECT CALL TO `_validate_negative` IS NOT THE GUARD ITSELF.

    Measured: deleting the `_validate_negative(key, spec)` line from `_assert_every_key_projects`
    left the whole suite GREEN (337 passed), because the two arms above call the validator by hand
    and so pin the FUNCTION while the import-time hook its docstring promises went unpinned. This
    arm runs the hook, so it reds when the wiring is removed.
    """
    bad = C.Invariant(
        ttl_s=1800,
        argv=C.INVARIANTS["repo.required_set"].argv,
        projection="probe-v1",
        project=C._project_required_set,
        negative=C.NegativeAnswer(body=C._NO_REQUIRED_CHECKS_BODY, ttl_s=1800, why="probe"),
    )
    saved = C.INVARIANTS["repo.required_set"]
    C.INVARIANTS["repo.required_set"] = bad
    try:
        with pytest.raises(C.TtlNotPermitted):
            C._assert_every_key_projects()
    finally:
        C.INVARIANTS["repo.required_set"] = saved


def test_a_negative_with_NO_body_is_refused_BY_NAME() -> None:
    """⛔ THE EMPTY-BODY BRANCH IS ITS OWN GUARD, and its MESSAGE is the fence.

    Deleting the branch is behaviourally survivable — the projectability check below it refuses `b""`
    too — so a bare `pytest.raises(TtlNotPermitted)` arm could not tell the two apart and the branch
    SURVIVED its own mutation. `match=` is what makes the deletion lethal: an operator reading
    `REFUSED — …` must be told the sentinel is EMPTY, not that the key's projection dislikes it.
    """
    spec = C.Invariant(
        ttl_s=1800,
        argv=C.INVARIANTS["repo.required_set"].argv,
        projection="probe-v1",
        project=C._project_required_set,
        negative=C.NegativeAnswer(body=b"", ttl_s=60, why="probe"),
    )
    with pytest.raises(C.TtlNotPermitted, match="declares a negative answer with no body"):
        C._validate_negative("repo.required_set", spec)


def test_a_negative_body_the_keys_own_projection_REFUSES_is_not_declared() -> None:
    """A sentinel the projection does not understand would be a second answer SHAPE — the exact
    class of body this key's `_require_shape` history exists to refuse."""
    spec = C.Invariant(
        ttl_s=1800,
        argv=C.INVARIANTS["repo.required_set"].argv,
        projection="probe-v1",
        project=C._project_required_set,
        negative=C.NegativeAnswer(body=b'{"message": "oops"}', ttl_s=60, why="probe"),
    )
    with pytest.raises(C.TtlNotPermitted):
        C._validate_negative("repo.required_set", spec)


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 11c. THE DECLARATION GUARD MUST HOLD AT EVERY DOOR, NOT ONLY AT IMPORT
# ══════════════════════════════════════════════════════════════════════════════════════════════
#: A REAL protection body for a branch that IS protected and requires NO checks — measured shape
#: (`gh api repos/daniel-ospina/tortoise/branches/main/protection`). It projects to the SAME
#: `{"contexts": [], "checks": []}` the 404 sentinel does, which is the whole point of the arm below.
_PROTECTED_NO_CHECKS_BODY = json.dumps(
    {
        "url": "https://api.github.com/repos/o/r/branches/main/protection",
        "required_status_checks": None,
        "enforce_admins": {"enabled": False},
        "required_signatures": {"url": "https://api.github.com/x", "enabled": False},
        "required_linear_history": {"enabled": False},
    }
).encode()


def test_a_GENUINE_no_checks_answer_is_priced_on_the_short_lane_too(tmp_path: Path) -> None:
    """⛔ THE STATED COST OF THE SENTINEL'S SHAPE — pinned so no later lane "fixes" it silently.

    A branch that IS protected with NO required checks projects to the SAME
    `{"contexts": [], "checks": []}` as the 404 sentinel, so the two cannot be told apart BY VALUE
    and this genuine answer is cached for `NEGATIVE_ANSWER_TTL_S`, not the key's 30 minutes. That is
    the deliberate fail-SAFE trade (an extra live read a minute vs. a merge gate read as "nothing
    is required" after someone adds a rule) and it is documented in `_is_negative_answer`.

    ⛔ IT MUST NOT BE PRICED ON THE RAW BODY ALONE INSTEAD: a caller-supplied reader returning the
    sentinel raw would then have its negative cached for the full ceiling, which is the fail-OPEN
    direction. This arm is what a change to that line reds.
    """
    calls: list[str] = []

    def reader(key: str) -> bytes:
        calls.append(key)
        return _PROTECTED_NO_CHECKS_BODY

    assert json.loads(
        _read("repo.required_set", now=1000.0, reader=reader, cache_dir=tmp_path)
    ) == {"contexts": [], "checks": []}
    assert len(calls) == 1
    # One second short of the negative's ttl: served, like any fresh entry.
    _read(
        "repo.required_set",
        now=1000.0 + C.NEGATIVE_ANSWER_TTL_S - 1,
        reader=reader,
        cache_dir=tmp_path,
    )
    assert len(calls) == 1, "a genuine no-checks answer lost even the short lane"
    # ⛔ AND IT RE-READS AT 60s — i.e. it is NOT sitting on the key's 1800s lane. If this ever
    # becomes 1 here, the sentinel-shape answer was quietly priced at the ceiling.
    _read(
        "repo.required_set",
        now=1000.0 + C.NEGATIVE_ANSWER_TTL_S,
        reader=reader,
        cache_dir=tmp_path,
    )
    assert len(calls) == 2, "the no-checks answer was cached past the negative lane's short ttl"


def test_the_declaration_guard_runs_at_the_PUBLIC_DOOR_not_only_at_import() -> None:
    """⛔ `declare_invariant()` IS the documented way to add a key, so it is a DOOR, not a helper.

    Measured at `224aa3704`+: `_validate_negative` was called only from
    `_assert_every_key_projects`, which runs ONCE at import over the STATIC registry — so a key
    declared at runtime could carry a negative its own projection refuses, and nothing refused it.
    """
    key = "repo.required_set"
    saved = C.INVARIANTS[key]
    try:
        # A sentinel this key's projection does not understand: the door must refuse it.
        with pytest.raises(C.TtlNotPermitted):
            C.declare_invariant(
                key,
                ttl_s=1800,
                argv=saved.argv,
                projection=saved.projection,
                project=saved.project,
                negative=C.NegativeAnswer(body=b'{"message": "oops"}', ttl_s=60, why="probe"),
            )
        # ⛔ THE CONTROL ARM: a VALID negative through the same door is ACCEPTED and lands on the
        # spec. Without this, the arm above would pass on a door that refused every negative.
        C.declare_invariant(
            key,
            ttl_s=1800,
            argv=saved.argv,
            projection=saved.projection,
            project=saved.project,
            negative=C.NegativeAnswer(
                body=C._NO_REQUIRED_CHECKS_BODY, ttl_s=C.NEGATIVE_ANSWER_TTL_S, why="probe"
            ),
        )
        assert C.INVARIANTS[key].negative is not None
        assert C.INVARIANTS[key].negative.ttl_s == C.NEGATIVE_ANSWER_TTL_S
        # ⛔ AND A VALUE THAT IS NEITHER A `NegativeAnswer` NOR `None` IS A CALLER BUG, NOT A DROP.
        # An `isinstance`-shaped body leaves every other value falling through to `None`, i.e. the
        # same silent loss this parameter was added to close, one line below where it was closed.
        with pytest.raises(C.TtlNotPermitted):
            C.declare_invariant(
                key,
                ttl_s=1800,
                argv=saved.argv,
                projection=saved.projection,
                project=saved.project,
                negative="oops",  # type: ignore[arg-type]
            )
        assert C.INVARIANTS[key].negative is not None, "a malformed negative silently dropped it"
    finally:
        C.INVARIANTS[key] = saved


def test_declare_invariant_PRESERVES_a_declared_negative_when_it_TIGHTENS() -> None:
    """⛔ THE SILENT-LOSS BUG, MEASURED. A tightening call is the DOCUMENTED-ALLOWED operation.

    `declare_invariant` rebuilt the `Invariant` without the field it did not know about, so on
    `224aa3704`+ this returned normally and left `negative=None` behind: the key's documented
    negative — and the P2 it fixes — vanished through the public door with no error and no red test,
    so every unprotected-branch read became `Unreadable` again. A tighten must not silently reverse
    a declaration the caller never mentioned.
    """
    key = "repo.required_set"
    saved = C.INVARIANTS[key]
    assert saved.negative is not None, "the key under test must carry a declared negative"
    try:
        C.declare_invariant(
            key,
            ttl_s=saved.ttl_s - 900,
            argv=saved.argv,
            projection=saved.projection,
            project=saved.project,
        )
        assert C.INVARIANTS[key].ttl_s == saved.ttl_s - 900
        assert C.INVARIANTS[key].negative is saved.negative, (
            "a tightening call dropped the declared negative — the public door silently reversed "
            "a declaration the caller never mentioned"
        )
        # ⛔ AND `negative=None` STILL DROPS IT, EXPLICITLY — the omission is what must not decide.
        C.declare_invariant(
            key,
            ttl_s=saved.ttl_s - 900,
            argv=saved.argv,
            projection=saved.projection,
            project=saved.project,
            negative=None,
        )
        assert C.INVARIANTS[key].negative is None
    finally:
        C.INVARIANTS[key] = saved


def test_declare_invariant_REFUSES_to_tighten_past_the_negative_and_names_the_WAY_OUT() -> None:
    """A tighten that outruns the negative's own ttl is refused — and the message says how to
    resolve it, because generic text would send the caller looking for a bug in `read()`."""
    key = "repo.required_set"
    saved = C.INVARIANTS[key]
    try:
        with pytest.raises(C.TtlNotPermitted) as excinfo:
            C.declare_invariant(
                key,
                ttl_s=C.NEGATIVE_ANSWER_TTL_S - 30,
                argv=saved.argv,
                projection=saved.projection,
                project=saved.project,
            )
        msg = str(excinfo.value)
        assert "negative=None" in msg, f"the refusal does not name the way out: {msg}"
        assert "NEGATIVE ttl" in msg
    finally:
        C.INVARIANTS[key] = saved


def test_read_REFUSES_a_spec_injected_with_a_negative_it_would_not_accept(
    tmp_path: Path,
) -> None:
    """⛔ `read()`'s DEFENCE-IN-DEPTH COVERED GUARD 2 ONLY, AND THIS IS THE SAME BYPASS.

    The import check runs ONCE, so a spec inserted straight into `INVARIANTS` afterwards skips it.
    Measured at `224aa3704`+: with a negative priced at the key's own 1800 s ceiling, `read()`
    accepted it and SERVED it — **one reader call across 1700 s** of a merge-gating key, i.e.
    exactly the 30-minute fail-OPEN window the short negative TTL exists to prevent.
    """
    key = "repo.required_set"
    saved = C.INVARIANTS[key]
    calls: list[str] = []

    def reader(k: str) -> bytes:
        calls.append(k)
        return C._NO_REQUIRED_CHECKS_BODY

    try:
        C.INVARIANTS[key] = C.Invariant(
            ttl_s=saved.ttl_s,
            argv=saved.argv,
            projection=saved.projection,
            project=saved.project,
            negative=C.NegativeAnswer(
                body=C._NO_REQUIRED_CHECKS_BODY, ttl_s=saved.ttl_s, why="a ceiling-priced negative"
            ),
        )
        with pytest.raises(C.TtlNotPermitted):
            _read(key, now=1000.0, reader=reader, cache_dir=tmp_path)
        assert calls == [], "the refused spec reached the pool before the guard ran"

        # ⛔ THE CONTROL ARM: a WELL-FORMED injected spec still reads normally, so the arm above
        # cannot pass on a `read()` that refused every negative it was handed.
        C.INVARIANTS[key] = C.Invariant(
            ttl_s=saved.ttl_s,
            argv=saved.argv,
            projection=saved.projection,
            project=saved.project,
            negative=C.NegativeAnswer(
                body=C._NO_REQUIRED_CHECKS_BODY, ttl_s=C.NEGATIVE_ANSWER_TTL_S, why="probe"
            ),
        )
        assert json.loads(
            _read(key, now=1000.0, reader=reader, cache_dir=tmp_path)
        ) == {"contexts": [], "checks": []}
        assert len(calls) == 1
    finally:
        C.INVARIANTS[key] = saved


@pytest.mark.parametrize(
    ("key", "body"),
    [
        # A body that names no identity: `login` absent used to yield {"login": None, "id": 1}.
        ("user.identity", [{"id": 1}]),
        ("repo.metadata", [{"id": 1}]),
        # An inventory/workflow entry that names nothing.
        ("repo.workflows", [{"workflows": [{}]}]),
        ("repo.workflows", [{"workflows": [{"id": 1, "path": "a"}]}]),
        ("runners.inventory", [{"runners": [{}]}]),
        # Everything projected is null.
        ("user.identity", [{"login": None, "id": None}]),
    ],
)
def test_an_identity_with_nulls_is_not_an_answer(key: str, body) -> None:
    """⛔ `[{"id": 1}]` used to project `user.identity` to `{"login": None, "id": 1}`: an identity
    naming no account, cached for 12 hours. Same for `[{}]` inside a collections response."""
    with pytest.raises(C.Unreadable):
        C.INVARIANTS[key].project(body)


def test_pick_last_refuses_an_all_null_answer_even_with_no_required_fields() -> None:
    """⛔ M27 WAS NOT ACTUALLY KILLED. Both `_pick_last` callers pass `required=`, so the `missing`
    check fires first and the all-null guard was UNREACHABLE — removing it left 208 tests green.
    A future key with optional fields is exactly the spelling that reaches it."""
    with pytest.raises(C.Unreadable):
        C._pick_last([{"a": None, "b": None}], ("a", "b"), key="probe", required=())
    # ...and a body with at least one real value still projects.
    assert C._pick_last([{"a": None, "b": 2}], ("a", "b"), key="probe", required=()) == {
        "a": None,
        "b": 2,
    }


def test_a_well_formed_identity_still_projects() -> None:
    """The other direction — the added strictness must not refuse a real body."""
    assert C.INVARIANTS["user.identity"].project([{"login": "a", "id": 7}]) == {
        "login": "a",
        "id": 7,
    }


def test_a_non_dict_envelope_is_a_miss_not_an_AttributeError(tmp_path: Path) -> None:
    """A JSON ARRAY envelope raises AttributeError out of `_load` on the `env.get(...)` path — the
    same uncaught-exception/latch class as the OverflowError defect."""
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    _entry(tmp_path).write_text("[1, 2, 3]")
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)  # must not raise
    assert r.n == 2


@pytest.mark.parametrize("raw", [b"[1,2,3]", b'"a string"', b"null", b"0", b"{}"])
def test_odd_envelopes_never_escape_load(raw: bytes, tmp_path: Path) -> None:
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    _entry(tmp_path).write_bytes(raw)
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    assert r.n == 2


@pytest.mark.parametrize("payload", ["a string", None, 42, {"a": 1}])
def test_a_reader_returning_non_bytes_is_Unreadable(payload, tmp_path: Path) -> None:
    """`bytes("str")` raises TypeError out of `read()` — uncaught, where the contract promises
    `Unreadable`."""
    # ⛔ VACUOUS ASSERTION FIXED: this used to allocate an unused `Counter()` and then pass a
    # DIFFERENT reader, so `assert r.n == 0` could not fail. The probe is the reader now.
    called: list[str] = []

    def bad_reader(key: str):
        called.append(key)
        return payload

    with pytest.raises(C.Unreadable):
        _read("repo.metadata", now=1000.0, reader=bad_reader, cache_dir=tmp_path)
    assert called == ["repo.metadata"]


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 12. CYCLE-7 FINDINGS — guard 2 must hold for EVERY caller, and the cache must actually HIT
# ══════════════════════════════════════════════════════════════════════════════════════════════
def test_read_PROJECTS_a_raw_body_from_an_injected_reader(tmp_path: Path) -> None:
    """⛔ THE HOLE. `read()` stored whatever the reader returned, VERBATIM, so guard 2 held only
    inside the private `_gh_reader`. A caller following the documented `reader=my_reader` usage
    cached a WHOLE body — measured: `pushed_at` and `open_issues_count` served for a 6-hour TTL,
    which is exactly what the `repo.metadata` registry comment says must never happen.

    The reader contract is therefore: supply RAW response bytes. `read()` decodes and projects,
    before the store, on every path.
    """
    raw = json.dumps(
        {
            "id": 1,
            "full_name": "o/r",
            "default_branch": "main",
            "visibility": "private",
            "node_id": "N1",
            "pushed_at": "1999-01-01",
            "open_issues_count": 412,
            "stargazers_count": 7,
        }
    ).encode()
    calls: list[str] = []

    def reader(key: str) -> bytes:
        calls.append(key)
        return raw

    out = _read("repo.metadata", now=1000.0, reader=reader, cache_dir=tmp_path)
    assert b"pushed_at" not in out, f"read() cached a whole body: {out!r}"
    assert b"open_issues_count" not in out
    assert json.loads(out)["full_name"] == "o/r"

    # ...and the HIT is the projection too, not the raw body.
    hit = _read("repo.metadata", now=1001.0, reader=reader, cache_dir=tmp_path)
    assert hit == out
    assert b"pushed_at" not in hit
    assert len(calls) == 1


def test_read_REFUSES_an_error_document_from_an_injected_reader(tmp_path: Path) -> None:
    """Claim 6 has to hold on the library path too, not only inside `_gh_reader`."""

    def reader(key: str) -> bytes:
        return b'{"message": "Server Error"}'

    with pytest.raises(C.Unreadable):
        _read("repo.metadata", now=1000.0, reader=reader, cache_dir=tmp_path)
    assert not list(tmp_path.iterdir()), "an unrecognised body must not be stored"


def test_two_projection_revisions_do_not_thrash_one_cache_file(tmp_path: Path) -> None:
    """⛔ A cache that never HITS is as bad as none.

    The source-bearing digest was in the envelope only, so two checkouts of this file at different
    projection revisions overwrote each other's entry on every access (measured: 8 alternating
    accesses -> 8 live reads, never converging — and `main()` defaults to a box-wide cache dir).
    """
    key = "repo.proj_rev_probe"

    def v1(docs):
        return {"id": 1}

    def v2(docs):
        return {"id": 1, "extra": True}

    C.declare_invariant(key, ttl_s=600, argv=OK_ARGV, projection="same-id", project=v1)
    try:
        p1 = C._cache_path(key, SCOPE, ttl_s=600).name
        C.INVARIANTS[key] = C.Invariant(ttl_s=600, argv=OK_ARGV, projection="same-id", project=v2)
        p2 = C._cache_path(key, SCOPE, ttl_s=600).name
        assert p1 != p2, (
            "two revisions of the same projection share one filename, so each overwrites the "
            "other's entry and the cache never hits"
        )
    finally:
        C.INVARIANTS.pop(key, None)


def test_a_hand_registered_mutable_key_is_still_refused() -> None:
    """⛔ `_assert_every_key_projects` checks only the SPEC, never the NAME, so `kind_of`'s
    mutable-name branch is the ONLY gate against a key inserted straight into INVARIANTS. It was
    unpinned: deleting those two lines left the suite green."""
    C.INVARIANTS["pr.state"] = C.Invariant(
        ttl_s=600, argv=("api", "x"), projection="p", project=lambda d: d
    )
    try:
        assert C.kind_of("pr.state") == "mutable"
        r = Counter()
        with pytest.raises(C.MutableReadRefused):
            _read("pr.state", now=1000.0, reader=r, cache_dir=Path("/tmp/nope-unused"))
        assert r.n == 0
    finally:
        C.INVARIANTS.pop("pr.state", None)


def test_a_non_int_ttl_is_refused_not_a_ValueError(tmp_path: Path) -> None:
    """The same conversion hole that was fixed for `now`: a caller mistake must be a
    `TtlNotPermitted` refusal, not a traceback out of `int()`."""
    r = Counter()
    for bad in ("abc", None if False else "1.5", object()):
        with pytest.raises(C.TtlNotPermitted):
            _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path, ttl_s=bad)
    assert r.n == 0


def test_required_set_without_contexts_or_checks_is_refused() -> None:
    """Absent one level DEEPER: `{"strict": false}` names no check list, so reading it as an EMPTY
    required set infers "no required checks" from an absence — on the merge-gating key."""
    for rsc in ({"strict": False}, {}):
        with pytest.raises(C.Unreadable):
            C.INVARIANTS["repo.required_set"].project([{"required_status_checks": rsc}])


def test_required_set_with_only_checks_projects() -> None:
    """The other direction: a real App-based protection sends `checks` and no `contexts`."""
    out = C.INVARIANTS["repo.required_set"].project(
        [{"required_status_checks": {"checks": [{"context": "docs"}]}}]
    )
    # ⛔ `contexts` is OMITTED, not fabricated as []: an empty list for a key the body did not
    # carry reads as "no required checks" on the merge-gating key.
    assert out == {"checks": ["docs"]}


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 13. CYCLE-8 FINDINGS — type/null validation, one schema per key, a real import fence
# ══════════════════════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    ("key", "body"),
    [
        # `{"contexts": "docs"}` projected to ["d","o","c","s"] — a required set invented from a
        # string, on the merge-gating key.
        ("repo.required_set", [{"required_status_checks": {"contexts": "docs"}}]),
        ("repo.required_set", [{"required_status_checks": {"contexts": [1, 2]}}]),
        # A DICT of checks iterated to its keys and silently dropped, yielding an EMPTY required set.
        ("repo.required_set", [{"required_status_checks": {"checks": {"docs": {"app_id": 1}}}}]),
        ("repo.required_set", [{"required_status_checks": {"checks": [{"context": 5}]}}]),
        # NULL as well as ABSENT: both used to cache an answer naming nothing.
        ("repo.workflows", [{"workflows": [{"id": None, "path": None, "state": None}]}]),
        ("repo.workflows", [{"workflows": [{"id": 1, "path": 5, "state": "active"}]}]),
        # a NULL id with a perfectly good path/state: only the null-ness check catches this one
        ("repo.workflows", [{"workflows": [{"id": None, "path": "ci.yml", "state": "active"}]}]),
        ("runners.inventory", [{"runners": [{"name": None, "labels": []}]}]),
        ("runners.inventory", [{"runners": [{"name": "r", "labels": "x64"}]}]),
        ("repo.metadata", [{"id": 1, "full_name": None, "default_branch": None}]),
        ("user.identity", [{"login": "a", "id": None}]),
        ("repo.metadata", [{"id": 1, "full_name": "o/r", "default_branch": None}]),
    ],
)
def test_presence_is_not_enough_type_and_null_are_checked(key: str, body) -> None:
    """⛔ Inner projections validated PRESENCE only, so a shape they did not understand became a
    cached wrong answer — including an EMPTY required set on the merge-gating key."""
    with pytest.raises(C.Unreadable):
        C.INVARIANTS[key].project(body)


def test_the_documented_shapes_still_project() -> None:
    """The added strictness must not refuse the shapes GitHub actually sends."""
    assert C.INVARIANTS["repo.required_set"].project(
        [{"required_status_checks": {"contexts": ["docs"]}}]
    ) == {"contexts": ["docs"]}
    assert C.INVARIANTS["repo.workflows"].project(
        [{"workflows": [{"id": 1, "path": ".github/workflows/ci.yml", "state": "active"}]}]
    ) == [{"id": 1, "path": ".github/workflows/ci.yml", "state": "active"}]
    assert C.INVARIANTS["runners.inventory"].project(
        [{"runners": [{"name": "cmux", "labels": [{"name": "x64"}]}]}]
    ) == [{"name": "cmux", "labels": ["x64"]}]


def test_cli_no_cache_emits_the_SAME_SCHEMA_as_the_cached_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⛔ One key must not have two output schemas. `--no-cache` bypasses the CACHE, not the
    projection: a caller written against the documented shape read NO required checks from a live
    body that named one."""
    raw = json.dumps(
        {"required_status_checks": {"contexts": ["docs"]}, "enforce_admins": {"enabled": True}}
    ).encode()

    def fake_reader(repo: str, branch: str):
        return lambda key: raw

    monkeypatch.setattr(C, "_gh_reader", fake_reader)
    cached, live = _Out(), _Out()
    monkeypatch.setattr(C.sys, "stdout", cached)
    assert (
        C.main(["--key", "repo.required_set", "--repo", "o/r", "--cache-dir", str(tmp_path)]) == 0
    )
    monkeypatch.setattr(C.sys, "stdout", live)
    assert (
        C.main(
            [
                "--key",
                "repo.required_set",
                "--repo",
                "o/r",
                "--cache-dir",
                str(tmp_path),
                "--no-cache",
            ]
        )
        == 0
    )
    assert cached.buffer.getvalue() == live.buffer.getvalue()
    assert json.loads(live.buffer.getvalue()) == {"contexts": ["docs"]}


@pytest.mark.parametrize(
    "key",
    [
        "pr.head",
        "pr.headref",
        "pr.base",
        "pr.title",
        "repo.description",
        "repo.topics",
        "repo.refs",
    ],
)
def test_moving_refs_and_editable_metadata_are_refused(key: str) -> None:
    """⛔ The mutable vocabulary UNDER-refused: `pr.head`'s SHA changes on every push and was
    ACCEPTED by `declare_invariant` — the natural name for a moving ref."""
    assert C._is_mutable_name(key), f"{key!r} is mutable but was accepted"
    with pytest.raises(C.MutableReadRefused):
        C.declare_invariant(key, ttl_s=600, argv=("api", "x"), projection="p", project=lambda d: d)


def test_the_shipped_keys_survive_the_wider_vocabulary() -> None:
    for key in C.INVARIANTS:
        assert C.kind_of(key) == "invariant", f"{key!r} is shipped but now reads as mutable"


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 14. CYCLE-9 FINDINGS — explicit null, silent label filters, and the REQUEST as identity
# ══════════════════════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "rsc",
    [
        {"checks": None},
        {"contexts": None},
        {"contexts": None, "checks": None},
        {"contexts": [], "checks": None},
    ],
)
def test_an_explicit_NULL_required_set_is_not_an_empty_one(rsc) -> None:
    """⛔ Absent and wrong-typed were refused; an explicit `null` was not, and it reads as "this
    branch requires no checks" on the merge-gating key. GitHub sends `[]`, never `null`."""
    with pytest.raises(C.Unreadable):
        C.INVARIANTS["repo.required_set"].project([{"required_status_checks": rsc}])


def test_empty_lists_are_still_the_legitimate_no_checks_answer() -> None:
    assert C.INVARIANTS["repo.required_set"].project(
        [{"required_status_checks": {"contexts": [], "checks": []}}]
    ) == {"contexts": [], "checks": []}


@pytest.mark.parametrize(
    "labels",
    [[None], ["self-hosted"], [{}], [{"name": None}], [{"name": 5}], [{"name": ""}], "x64"],
)
def test_a_runner_label_entry_the_projection_does_not_understand_is_refused(labels) -> None:
    """⛔ `if isinstance(lbl, dict)` was a SILENT FILTER, so a lane asking "is there a runner with
    label X" got a cached "no" (``labels: []``) from a body the projection did not understand, for
    30 minutes. `_project_labels` type-checks; this now does too.

    `[{"name": ""}]` (mutation M72) is the EMPTY spelling of the same thing: `""` is a `str`, so
    the type check alone passed it and the entry cached as a named label for 30 minutes. Every
    sibling projection tightened in the same round already refused it.
    """
    with pytest.raises(C.Unreadable):
        C.INVARIANTS["runners.inventory"].project([{"runners": [{"name": "r1", "labels": labels}]}])


@pytest.mark.parametrize("entry", [{"name": ""}, {"name": "", "labels": []}])
def test_a_runner_naming_nothing_with_an_EMPTY_STRING_is_refused(entry) -> None:
    """⛔ MUTATION M72. `{"name": ""}` projected cleanly to ``[{"name": "", "labels": []}]`` and
    was cached for 30 minutes — the third spelling of "an inventory entry naming no runner", after
    `[{}]` and `[{"name": null}]` which this function already refuses.

    Non-vacuous against the pre-fix code: the old check was `isinstance(r.get("name"), str)`, and
    `""` satisfies it, so both arms below projected an answer instead of raising.

    The MESSAGE is pinned too: this text is the entire operator-facing detail on a refused live
    read (`gh_read_cache: REFUSED — …`, exit 1), and it used to state the SUCCESS condition —
    "entry has a non-null, non-empty-string `name`" — on the branch that fires because the entry
    does NOT have one. A lane triaging that was told the opposite of the fault.
    """
    with pytest.raises(C.Unreadable) as excinfo:
        C.INVARIANTS["runners.inventory"].project([{"runners": [entry]}])
    msg = str(excinfo.value)
    assert "is not a non-empty string" in msg, msg
    assert repr(entry["name"]) in msg, f"the message does not name the offending value: {msg}"


@pytest.mark.parametrize("entry", [{"name": "cmux"}, {"name": "cmux", "os": "linux"}])
def test_a_runner_entry_with_NO_labels_key_is_refused(entry) -> None:
    """⛔ ABSENCE read as "carries no labels" and cached that "no" for 30 min — the last spelling of
    the failure the sibling comment closes. `labels: []` is legitimate; a MISSING key is not."""
    with pytest.raises(C.Unreadable):
        C.INVARIANTS["runners.inventory"].project([{"runners": [entry]}])


def test_a_runner_with_no_labels_still_projects() -> None:
    assert C.INVARIANTS["runners.inventory"].project(
        [{"runners": [{"name": "r1", "labels": []}]}]
    ) == [{"name": "r1", "labels": []}]


def test_changing_a_keys_endpoint_invalidates_the_cached_entry(tmp_path: Path) -> None:
    """⛔ THE REQUEST IS PART OF THE IDENTITY.

    Without the argv, pointing a key at a different endpoint (same scope, same key, same
    projection) served the PREVIOUS endpoint's answer as a provably-fresh hit — measured with ZERO
    reader calls, for the whole 6-hour `repo.metadata` TTL. That is guard 1's own failure mode with
    the REQUEST as the subject.
    """
    import dataclasses

    key = "repo.metadata"
    r = Counter()
    _read(key, now=1000.0, reader=r, cache_dir=tmp_path)
    assert r.n == 1

    # ⛔ A DIFFERENT ALLOWLISTED ENDPOINT, not an invented one: the door refuses a path outside the
    # allow-list, so `repos/{repo}/somewhere-else` would be refused before the identity check ran
    # and this test would pass without ever exercising the argv digest.
    monkeypatch_argv = ("api", "repos/{repo}/labels")
    original = C.INVARIANTS[key]
    C.INVARIANTS[key] = dataclasses.replace(C.INVARIANTS[key], argv=monkeypatch_argv)
    try:
        r2 = Counter()
        _read(key, now=1000.0 + 1.0, reader=r2, cache_dir=tmp_path)
        assert r2.n == 1, (
            "the previous ENDPOINT's answer was served as a fresh hit — the argv is not in the "
            "cache identity"
        )
    finally:
        # ⛔ RESTORE THE ORIGINAL OBJECT, not `dataclasses.replace(...)` of the mutant. A rebuilt
        # spec is a NEW object, so its `id()` no longer matches `_IMPORT_CHECK` and
        # `test_the_import_time_registry_guard_actually_RAN` is left stale — it passes only because
        # this file happens to run before it. Measured with the two node ids in the other order:
        # `Extra items in the left set: 'repo.metadata#…'` / `right set: 'repo.metadata#…'`.
        C.INVARIANTS[key] = original


def test_the_envelope_records_the_argv(tmp_path: Path) -> None:
    """Defence in depth: a file at the right NAME is still rejected if its argv disagrees."""
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    p = _entry(tmp_path)
    env = json.loads(p.read_text())
    env["argv"] = ["api", "repos/{repo}/labels"]
    p.write_text(json.dumps(env))
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    assert r.n == 2


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 15. CYCLE-12 FINDINGS — the leak field was reachable, and two docs contradicted the code
# ══════════════════════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "key",
    [
        "repo.visibility",
        "repo.locked",
        "repo.homepage",
        "repo.permissions",
        "repo.customProperties",
        "pr.labels",
        "issue.labels",
    ],
)
def test_the_leak_field_and_assignment_sets_are_refused(key: str) -> None:
    """⛔ `visibility` is the ONE field the module singles out as a LEAK rather than a wait — and
    `declare_invariant("repo.visibility", …)` was ACCEPTED, so a stale "public" for an hour was
    reachable through the registry door the guard claims to close.

    `labels` is the SCOPE-DEPENDENT case: `repo.labels` is the repo's DEFINITION set (a shipped,
    invariant key); `pr.labels`/`issue.labels` is an ASSIGNMENT that changes on every triage.
    """
    assert C._is_mutable_name(key), f"{key!r} is mutable but was accepted"
    with pytest.raises(C.MutableReadRefused):
        C.declare_invariant(key, ttl_s=600, argv=("api", "x"), projection="p", project=lambda d: d)


def test_a_failing_cache_WRITE_is_announced_not_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """⛔ An unwritable cache dir turned this tool into a PASS-THROUGH with no diagnostic: every
    read a MISS forever, indistinguishable from a cold cache, while the burn this module exists to
    stop resumed invisibly. The value must still be RETURNED (it was just read live) — only the
    silence was the defect.
    """
    monkeypatch.setattr(C, "_WRITE_WARNED", False)

    def boom(path, blob):
        raise OSError("read-only file system")

    monkeypatch.setattr(C, "_store", boom)
    r = Counter()
    out = _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    assert json.loads(out)["id"] == 1, "a failed WRITE must still return the value just read"
    err = capsys.readouterr().err
    assert "could not write the cache entry" in err
    assert "pass-through" in err
    assert "LIVE call" in err


def test_the_write_warning_is_emitted_ONCE(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setattr(C, "_WRITE_WARNED", False)

    def boom(path, blob):
        raise OSError("read-only file system")

    monkeypatch.setattr(C, "_store", boom)
    for i in range(4):
        _read("repo.metadata", now=1000.0 + i, reader=Counter(), cache_dir=tmp_path)
    assert capsys.readouterr().err.count("could not write the cache entry") == 1


def test_a_silent_successful_write_emits_nothing(tmp_path: Path, capsys) -> None:
    _read("repo.metadata", now=1000.0, reader=Counter(), cache_dir=tmp_path)
    assert capsys.readouterr().err == ""


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 16. CYCLE-13 FINDINGS — four guards that were correct but UNPINNED on the path that runs
# ══════════════════════════════════════════════════════════════════════════════════════════════
def test_scope_for_carries_the_BRANCH() -> None:
    """⛔ The CLI derives its scope EXCLUSIVELY through `scope_for`, and dropping `@branch` from it
    left 221 tests green — every `read()` scope test passes `scope=` as a literal, and every
    `scope_for` test used the GLOBAL key. Mutating it to `return repo` served one branch's required
    set as another's, on the merge-gating key."""
    main = C.scope_for("repo.required_set", "o/r", "main")
    release = C.scope_for("repo.required_set", "o/r", "release")
    assert main == "o/r@main"
    assert release == "o/r@release"
    assert main != release, "the branch is not part of the scope, so branches share one entry"


def test_scope_for_keeps_two_repos_apart() -> None:
    assert C.scope_for("repo.metadata", "o/a", "main") != C.scope_for(
        "repo.metadata", "o/b", "main"
    )


@pytest.mark.parametrize("key", ["repo.workflows", "runners.inventory"])
def test_a_MULTI_PAGE_response_is_CONCATENATED_not_replaced(key: str) -> None:
    """⛔ `rows = list(inner)` instead of `rows.extend(inner)` left 221 tests green, and cached the
    LAST page as the whole inventory. This repo has 41 workflow files, so `repo.workflows` really
    does page. `test_projection_over_multiple_pages_concatenates` covers `repo.labels`, which does
    not go through `_expand` — so the concat rule had no fence at all."""
    item = {"id": 1, "path": "a.yml", "state": "active", "name": "r1", "labels": [{"name": "x64"}]}
    page1 = {
        f"total_{'workflows' if key == 'repo.workflows' else 'runners'}": 2,
        ("workflows" if key == "repo.workflows" else "runners"): [item],
    }
    page2 = {
        f"total_{'workflows' if key == 'repo.workflows' else 'runners'}": 2,
        ("workflows" if key == "repo.workflows" else "runners"): [{**item, "id": 2, "name": "r2"}],
    }
    out = C.INVARIANTS[key].project([page1, page2])
    assert len(out) == 2, f"{key!r} kept only the last page: {out}"
    field = "id" if key == "repo.workflows" else "name"
    assert {row[field] for row in out} == ({1, 2} if field == "id" else {"r1", "r2"}), (
        f"{key!r} lost a page: {out}"
    )


@pytest.mark.parametrize("key", ["prstate", "runstatus", "mergestatestatus"])
def test_a_GLUED_mutable_spelling_is_refused(key: str) -> None:
    """⛔ The glued-word clause was only tested in the direction that must NOT match. Removing it
    left 221 green and let `declare_invariant("prstate", …)` succeed — a mutable-state read
    becoming cacheable. The docstring's over-refusal note is the invitation to loosen it."""
    assert C._is_mutable_name(key), f"{key!r} is a glued mutable spelling but was accepted"
    with pytest.raises(C.MutableReadRefused):
        C.declare_invariant(key, ttl_s=600, argv=("api", "x"), projection="p", project=lambda d: d)


def test_a_slash_named_branch_is_ESCAPED_into_the_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """⛔ `release/1.0` spelled raw addresses a DIFFERENT route
    (`…/branches/release/1.0/protection`), so every read of a slash-named protected branch was
    refused. `{repo}` must keep its `/` — that one IS the path."""
    seen: list[list[str]] = []

    class Proc:
        returncode = 0
        stderr = b""
        stdout = b'{"required_status_checks": {"contexts": [], "checks": []}}'

    def fake_run(cmd, **kw):
        seen.append(list(cmd))
        return Proc()

    monkeypatch.setattr(C.subprocess, "run", fake_run)
    C._gh_reader("o/r", "release/1.0")("repo.required_set")

    cmd = seen[0]
    joined = " ".join(cmd)
    assert "release%2F1.0" in joined, f"the branch was not escaped: {joined}"
    assert "repos/o/r/branches/release%2F1.0/protection" in joined, joined
    # the repo's own slash must survive
    assert "repos/o/r/" in joined


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 17. CYCLE-14 FINDINGS — presence+null was still not type, and the exemption was a prefix
# ══════════════════════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    ("key", "body"),
    [
        # an EMPTY STRING passed the null test: a 1-hour identity naming no repository
        ("repo.metadata", [{"id": 1, "full_name": "", "default_branch": "main"}]),
        ("repo.metadata", [{"id": 1, "full_name": "o/r", "default_branch": ""}]),
        # a BOOLEAN id passes `is not None`, and a non-string login reaches callers as an identity
        ("repo.metadata", [{"id": True, "full_name": "o/r", "default_branch": "main"}]),
        ("user.identity", [{"login": {"unexpected": 1}, "id": 1}]),
        ("user.identity", [{"login": "someone", "id": True}]),
        ("user.identity", [{"login": "", "id": 1}]),
        ("user.identity", [{"login": 42, "id": 1}]),
    ],
)
def test_identity_fields_are_TYPE_checked_not_only_null_checked(key: str, body) -> None:
    """⛔ `{"full_name": ""}` and `{"login": {...}, "id": true}` both passed the null test and were
    cached — a 1-hour identity naming no repository, and a 12-hour identity whose `login` is not a
    string. Every other projection in this module type-checks its fields; `_pick_last` did not."""
    with pytest.raises(C.Unreadable):
        C.INVARIANTS[key].project(body)


def test_a_real_identity_still_projects() -> None:
    assert C.INVARIANTS["user.identity"].project([{"login": "a", "id": 7}]) == {
        "login": "a",
        "id": 7,
    }
    assert C.INVARIANTS["repo.metadata"].project(
        [{"id": 1, "node_id": "N", "full_name": "o/r", "default_branch": "main"}]
    ) == {"id": 1, "node_id": "N", "full_name": "o/r", "default_branch": "main"}


@pytest.mark.parametrize("key", ["repo.pr.labels", "repo.issue.labels", "repo.pulls.labels"])
def test_the_labels_exemption_is_the_EXACT_shipped_key_not_a_prefix(key: str) -> None:
    """⛔ The exemption was `tokens[0] == "repo"`, so a `labels` ASSIGNMENT spelled with the
    registry's own convention was admitted: `repo.pr.labels` (argv
    `repos/{repo}/pulls/9/labels`) cached a PR's triage set for an hour."""
    assert C._is_mutable_name(key), f"{key!r} is an assignment set but was exempted"
    with pytest.raises(C.MutableReadRefused):
        C.declare_invariant(key, ttl_s=600, argv=("api", "x"), projection="p", project=lambda d: d)


def test_repo_labels_is_still_the_invariant_definition_set() -> None:
    """The other direction: the shipped key must not be caught by the tightened rule."""
    assert not C._is_mutable_name("repo.labels")
    assert C.kind_of("repo.labels") == "invariant"


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 17. CYCLE-15 FINDINGS — the announce that could discard a live read, and a refusal with no reason
# ══════════════════════════════════════════════════════════════════════════════════════════════
def test_an_unwritable_STDERR_cannot_discard_a_read_that_SUCCEEDED(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⛔ `_warn_write_failed` runs INSIDE `read()`'s `except OSError` handler and ended in an
    UNGUARDED `print(..., file=sys.stderr)`. An exception raised inside an `except` block
    propagates, so an unwritable cache (the very fault being announced) plus an unwritable stderr
    (a closed stream -> `ValueError`, a broken pipe -> `BrokenPipeError`) discarded the payload that
    had just been read live and raised out of `read()` instead of returning it — the exact
    conflation the comment at the call site forbids ("a full disk into a total gh outage").

    Two simultaneous faults are needed, so this was P3; but R8 says *never*, and the previous code
    (`contextlib.suppress(OSError)` around the store) could not raise at all.
    """

    class _BrokenStderr:
        def write(self, _s: str) -> int:
            raise BrokenPipeError(32, "Broken pipe")

        def flush(self) -> None:
            raise BrokenPipeError(32, "Broken pipe")

        def close(self) -> None:
            raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr(C, "_WRITE_WARNED", False)
    monkeypatch.setattr(C, "_store", _raise_read_only_fs)
    # `_warn_write_failed` does `import sys as _sys` at CALL time, so patching the real module
    # object is the seam that reaches it — a monkeypatched name in this test's namespace is not.
    monkeypatch.setattr(sys, "stderr", _BrokenStderr())

    out = _read("repo.metadata", now=1000.0, reader=Counter(), cache_dir=tmp_path)
    assert json.loads(out)["id"] == 1, (
        "a failed cache WRITE plus a failed ANNOUNCE must still RETURN the value that was read live"
    )


def _raise_read_only_fs(path: Path, blob: bytes) -> None:
    raise OSError("read-only file system")


@pytest.mark.parametrize("key", ["pr.labels", "issue.labels"])
def test_the_refusal_names_the_WORD_that_made_the_key_mutable(key: str) -> None:
    """⛔ The container-scoped vocabulary was added to the DECISION (`_is_mutable_name`) but not to
    the MESSAGE builder (`_mutable_words_in`), so `declare_invariant("pr.labels", …)` was refused
    with `carries mutable-state vocabulary ()` — an empty reason list, on exactly the key pair the
    new vocabulary was added for. The two functions must walk ONE set; they now share
    `_vocabulary_for(key)`.
    """
    assert C._mutable_words_in(key) == ["labels"], (
        f"{key!r} was refused for a word the message did not name: {C._mutable_words_in(key)!r}"
    )
    with pytest.raises(C.MutableReadRefused) as exc:
        C.declare_invariant(key, ttl_s=600, argv=("api", "x"), projection="p", project=lambda d: d)
    assert "(labels)" in str(exc.value), str(exc.value)


@pytest.mark.parametrize(
    "key",
    [
        "pr.labels",
        "issue.labels",
        "repo.labels",
        "repo.pr.labels",
        "repo.issueLabels",
        "repo.visibility",
        "pr.head",
        "repo.checksum",
        "repo.rateLimit",
        "pr.reviewDecision",
        "repo.topics",
        "issue.title",
        "x.accurateLimit",
        "runners",
        "user.identity",
    ],
)
def test_the_decision_and_the_refusal_message_AGREE_on_every_key(key: str) -> None:
    """The property the drift violated: a key the guard REFUSES must produce a non-empty reason
    list. Any future fork of the two sets reds here instead of shipping an empty `()`."""
    assert C._is_mutable_name(key) == bool(C._mutable_words_in(key)), (
        f"{key!r}: decision={C._is_mutable_name(key)} words={C._mutable_words_in(key)!r}"
    )


# ══════════════════════════════════════════════════════════════════════════════════════════════
# CYCLE-15 — never fabricate a key; type the list projections; no denylist on coercion
# ══════════════════════════════════════════════════════════════════════════════════════════════
def test_required_set_NEVER_fabricates_a_key_the_body_did_not_carry() -> None:
    """⛔ Returning `"contexts": []` for a body that only sent `checks` is an empty required set
    inferred from an ABSENCE — and a merge gate reading `contexts` (the field legacy protection
    uses) then sees "no required checks" for a branch that requires `docs`."""
    checks_only = C.INVARIANTS["repo.required_set"].project(
        [{"required_status_checks": {"checks": [{"context": "docs"}]}}]
    )
    assert checks_only == {"checks": ["docs"]}
    assert "contexts" not in checks_only

    contexts_only = C.INVARIANTS["repo.required_set"].project(
        [{"required_status_checks": {"contexts": ["docs"]}}]
    )
    assert contexts_only == {"contexts": ["docs"]}
    assert "checks" not in contexts_only


def test_required_set_null_still_answers_both_keys_empty() -> None:
    """The legitimate "nothing configured" answer names BOTH, so it cannot be misread."""
    assert C.INVARIANTS["repo.required_set"].project([{"required_status_checks": None}]) == {
        "contexts": [],
        "checks": [],
    }


@pytest.mark.parametrize(
    ("key", "body"),
    [
        # ⛔ a LIST page: the dict spelling `[{"name": ""}]` is refused by the SHAPE guard instead,
        # leaving the empty-name check unreachable — measured, M65 SURVIVED on it.
        ("repo.labels", [[{"name": ""}]]),
        ("repo.labels", [[{"name": 5}]]),
        # the FOURTH projection, same rule (M72): a runner naming nothing is not an inventory
        # entry. `labels` must be present or the ABSENCE guard raises first and this check is
        # unreachable — the same unreachability that let M67 survive on its first test.
        ("runners.inventory", [{"runners": [{"name": "", "labels": []}]}]),
        ("repo.workflows", [{"workflows": [{"id": "abc", "path": "a", "state": "active"}]}]),
        ("repo.workflows", [{"workflows": [{"id": True, "path": "a", "state": "active"}]}]),
        ("repo.workflows", [{"workflows": [{"id": 1, "path": "", "state": "active"}]}]),
        ("repo.workflows", [{"workflows": [{"id": 1, "path": "a", "state": ""}]}]),
        ("repo.metadata", [{"id": 1, "node_id": None, "full_name": "o/r", "default_branch": "m"}]),
    ],
)
def test_the_list_projections_type_check_too(key: str, body) -> None:
    """⛔ "wrong type / empty / explicit null are refused" was uniform for `_pick_last` only."""
    with pytest.raises(C.Unreadable):
        C.INVARIANTS[key].project(body)


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan"), 10**400])
def test_a_ttl_that_cannot_be_an_int_is_refused_not_OverflowError(bad, tmp_path: Path) -> None:
    """⛔ `int(inf)` raises OverflowError, which is NOT a ValueError, so a denylist here let it
    escape `read()`'s contract and reach the CLI's `except (MutableReadRefused, TtlNotPermitted,
    Unreadable)` as a traceback."""
    r = Counter()
    with pytest.raises(C.TtlNotPermitted):
        _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path, ttl_s=bad)
    assert r.n == 0


def test_a_clock_that_cannot_be_a_float_is_refused_not_OverflowError(tmp_path: Path) -> None:
    """The identical hole on the `now` conversion: `float(10**400)` raises OverflowError."""
    r = Counter()
    with pytest.raises(C.TtlNotPermitted):
        _read("repo.metadata", now=10**400, reader=r, cache_dir=tmp_path)
    assert r.n == 0


@pytest.mark.parametrize("nonfinite", [float("inf"), float("-inf"), float("nan")])
def test_a_non_finite_clock_is_a_MISS_not_a_refusal(nonfinite, tmp_path: Path) -> None:
    """The other half: `inf` IS a float, so it can only ever fail to prove freshness — a MISS."""
    r = Counter()
    out = _read("repo.metadata", now=nonfinite, reader=r, cache_dir=tmp_path)
    assert json.loads(out)["id"] == 1
    assert r.n == 1


# ══════════════════════════════════════════════════════════════════════════════════════════════
# CYCLE-16/17 — the DOOR is an ALLOW-LIST of endpoints; the decode denylist is a latch; empty inputs
# ══════════════════════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "argv",
    [
        # ── ONE ARM PER FAMILY the retired deny-list enumerated. Each arm carries ONLY that family:
        #    the two `/commits/...` arms that used to be here were BOTH refused by `/commits`, so
        #    `/status` and `/check-runs` were never the marker that fired — deleting them from the
        #    deny-list left all 290 tests green (measured at `a9321ddc6`). That is the shape this
        #    suite has fixed twice already (M67 unreachable, M72's missing `labels` key): a pin that
        #    cannot fail for the bug it names.
        ("api", "repos/{repo}/pulls/9"),
        ("api", "repos/{repo}/issues/9"),
        ("api", "repos/{repo}/actions/runs"),
        ("api", "repos/{repo}/commits"),
        ("api", "repos/{repo}/commits/abc/check-runs"),
        ("api", "repos/{repo}/commits/abc/status"),
        ("api", "repos/{repo}/issues/9/comments"),
        ("api", "repos/{repo}/releases"),
        ("api", "repos/{repo}/tags"),
        ("api", "repos/{repo}/merges"),
        # ── the families the deny-list NEVER named, which is why a deny-list could not hold.
        #    `/git/refs/…` is a MOVING REF: measured at `a9321ddc6`, it was served an hour stale.
        ("api", "repos/{repo}/git/refs/heads/main"),
        ("api", "repos/{repo}/branches"),
        ("api", "repos/{repo}/deployments"),
        ("api", "repos/{repo}/rulesets"),
        ("api", "notifications"),
        ("api", "graphql"),
        # ── NON-GET argv — the second half of the same door. The mutation ran ONCE and every later
        #    caller inside the TTL was handed its response without the mutation running at all.
        ("api", "repos/{repo}", "-X", "POST"),
        ("api", "repos/{repo}", "--method", "DELETE"),
        ("api", "repos/{repo}", "-f", "name=x"),
        ("api", "repos/{repo}", "-F", "field=@payload.json"),
        ("api", "repos/{repo}", "--input", "payload.json"),
        # ── ARGV SHAPE: a second operand, no operand, an empty segment, a non-placeholder repo
        ("api", "repos/{repo}", "labels"),
        ("api",),
        ("api", "repos//labels"),
        ("api", "repos/o/r/labels"),
    ],
)
def test_declare_invariant_REFUSES_an_endpoint_outside_the_ALLOWLIST(argv) -> None:
    """⛔ `_is_mutable_name` reads the KEY. `declare_invariant("repo.identity",
    argv=("api","repos/{repo}/pulls/9"))` was ACCEPTED — an invariant-sounding name over a mutable
    endpoint — and cached a PR's `state` for an hour, served as a fresh hit after it closed. An
    invariant-sounding NAME is not an invariant READ."""
    with pytest.raises(C.MutableReadRefused):
        C.declare_invariant(
            "repo.identity",
            ttl_s=3600,
            argv=argv,
            projection="pr-state-v1",
            project=lambda d: {"state": d[-1]["state"]},
        )
    assert "repo.identity" not in C.INVARIANTS


def test_a_moving_ref_cannot_be_READ_even_when_injected_past_the_door(tmp_path: Path) -> None:
    """⛔ THE MEASURED FAILURE, as a test. At `a9321ddc6` the door was a substring deny-list that
    never named `/git/refs`, so an invariant-sounding key over a MOVING REF was accepted:
    `{"sha":"AAAA"}` was cached at t=1000, the ref moved to `BBBB` by t=4500, and the read at
    t=4500 returned `AAAA` with ZERO reader calls — a moving ref served as a provably-fresh hit for
    the whole hour.

    The spec is injected STRAIGHT INTO ``INVARIANTS`` here (bypassing ``declare_invariant``) so the
    refusal is pinned on the READ path too, not only at the declaration door.
    """
    key = "repo.identity"
    C.INVARIANTS[key] = C.Invariant(
        ttl_s=3600,
        argv=("api", "repos/{repo}/git/refs/heads/main"),
        projection="moving-ref-v1",
        project=lambda d: {"sha": d[-1]["sha"]},
    )
    try:
        r = Counter()
        with pytest.raises(C.MutableReadRefused):
            _read(key, now=1000.0, reader=r, cache_dir=tmp_path)
        assert r.n == 0, "a mutable endpoint reached the reader"
        assert not list(tmp_path.iterdir()), "a refused read must not create an entry"
    finally:
        C.INVARIANTS.pop(key, None)


def test_the_door_is_EXACT_membership_not_a_prefix() -> None:
    """A prefix match on `repos/{repo}` would re-admit `repos/{repo}/pulls` — the hole the
    allow-list replaces. And `{repo}`/`{branch}` must be PLACEHOLDERS: a concrete repo in a declared
    argv would read ONE fixed repo for every scope (guard 1's failure, with the wrong subject)."""
    assert C._endpoint_refusal(OK_ARGV) is None
    assert C._endpoint_refusal(("api", "repos/{repo}/labels", "--paginate")) is None
    assert C._endpoint_refusal(("api", "user")) is None
    for refused in (
        ("api", "repos/{repo}/pulls"),
        ("api", "repos/o/r/labels"),
        ("api", "repos/{repo}/labels/"),
        ("api", "repos/{repo}/labels", "--method", "GET"),
    ):
        reason = C._endpoint_refusal(refused)
        assert reason is not None, f"{refused!r} was accepted by the door"
        assert isinstance(reason, str) and reason, reason
    # A non-sequence argv is REFUSED, not iterated (a bare string would otherwise walk its chars).
    for bad in ("api repos/{repo}", None, 42):
        assert C._endpoint_refusal(bad) is not None, bad


@pytest.mark.parametrize(
    "bad_argv",
    [
        None,
        42,
        "api repos/{repo}",
        ("api", 1),
        ("api", None),
        ("api", "repos/{repo}", 7),
    ],
)
def test_the_door_REFUSES_a_malformed_argv_THROUGH_the_path_that_RUNS(bad_argv) -> None:
    """⛔ A PIN THAT CALLED `_endpoint_refusal` DIRECTLY COULD NOT FAIL FOR THIS.

    `_validate_spec` iterated the argv in its projection-flag loop BEFORE calling
    `_endpoint_refusal`, so the helper's `isinstance` guard was UNREACHABLE from every door that
    actually runs. Measured at `34ac43e354` (the head this review found it at):

        declare_invariant(…, argv=None)      → TypeError: 'NoneType' object is not iterable
        declare_invariant(…, argv=42)        → TypeError: 'int' object is not iterable
        declare_invariant(…, argv=("api",1)) → AttributeError: 'int' object has no attribute
                                               'startswith'

    Fail-closed — nothing was cached — but a traceback where the module promises
    `MutableReadRefused`, and the review measured the helper directly, which is why the old pin
    stayed green. This goes through `declare_invariant`, the documented extension path.
    """
    with pytest.raises(C.MutableReadRefused):
        C.declare_invariant(
            "repo.identity",
            ttl_s=3600,
            argv=bad_argv,
            projection="probe-v1",
            project=lambda d: d,
        )
    assert "repo.identity" not in C.INVARIANTS


@pytest.mark.parametrize("bad_argv", [None, ("api", 1), "api repos/{repo}"])
def test_a_malformed_argv_is_REFUSED_on_the_READ_path_too(bad_argv, tmp_path: Path) -> None:
    """The same shape guard, injected STRAIGHT INTO `INVARIANTS` so the refusal is pinned on
    `read()` — the path that actually runs per call — and not only at the declaration door. Before
    the fix this raised `TypeError`/`AttributeError` out of `read()`, past its documented contract.
    """
    key = "repo.identity"
    C.INVARIANTS[key] = C.Invariant(
        ttl_s=3600,
        argv=bad_argv,  # type: ignore[arg-type]  — the malformed value IS the subject
        projection="malformed-argv-v1",
        project=lambda d: d,
    )
    try:
        r = Counter()
        with pytest.raises(C.MutableReadRefused):
            _read(key, now=1000.0, reader=r, cache_dir=tmp_path)
        assert r.n == 0, "a malformed argv reached the reader"
        assert not list(tmp_path.iterdir()), "a refused read must not create an entry"
    finally:
        C.INVARIANTS.pop(key, None)


def test_declare_invariant_still_accepts_the_registrys_own_endpoints() -> None:
    """The refusal must not be a blanket ban: `/branches/{branch}/protection` (required_set),
    `/labels`, `/actions/workflows`, `/actions/runners` and `user` are all in the registry."""
    for spec in C.INVARIANTS.values():
        C._validate_spec("probe", spec.argv, spec.projection, spec.project)  # must not raise


@pytest.mark.parametrize("payload", [b"[" * 20000 + b"]" * 20000, b"["])
def test_a_decode_that_raises_anything_is_a_MISS_not_a_latch(payload, tmp_path: Path) -> None:
    """⛔ The decode block caught `(OSError, ValueError, UnicodeDecodeError)` — a DENYLIST — while
    the coercion block below it was fixed to `except Exception:` for exactly this reason.
    `json.loads` on ~20 000 nested arrays raises RecursionError, which escaped `_load`, `read()`
    and `main()`; and because the read never reached the reader the poison entry was never
    rewritten, so EVERY later read raised until `--purge`."""
    scope = C.scope_for("repo.metadata", "o/r", "main")
    # ⛔ a REAL read first: the poisoned file must be at the exact path the cache looks for, or the
    # test proves nothing about `_load` (it would just be an ordinary MISS).
    _read("repo.metadata", scope=scope, now=1000.0, reader=Counter(), cache_dir=tmp_path)
    entries = sorted(tmp_path.rglob("*.json"))
    assert len(entries) == 1, entries
    entries[0].write_bytes(payload)
    r = Counter()
    # ⛔ THREE reads, and all three must SUCCEED. Under the denylist the first one raises and — never
    # having reached the reader — never rewrites the entry, so the second and third raise too: a
    # latch. `r.n == 1` is the PROOF of self-healing, not a weak assertion: the first read MISSes
    # and overwrites, so reads two and three are HITS that require no reader at all.
    for _ in range(3):
        out = _read("repo.metadata", scope=scope, now=2000.0, reader=r, cache_dir=tmp_path)
        assert json.loads(out)["id"] == 1
    assert r.n == 1


def test_a_runner_naming_nothing_is_not_an_inventory_row() -> None:
    """`{"name": ""}` projected to `[{"name": "", "labels": []}]` and cached 30 min — every sibling
    list projection requires a non-empty string, and this one did not."""
    with pytest.raises(C.Unreadable):
        C.INVARIANTS["runners.inventory"].project([{"runners": [{"name": "", "labels": []}]}])


def test_an_empty_repo_is_refused_rather_than_spending_a_call(tmp_path: Path) -> None:
    """A repo-scoped key with no repo derives `"@main"`, which passes the non-empty-scope test and
    spends one call from the pool this module protects, on `gh api repos/` (a guaranteed 404)."""
    r = Counter()
    with pytest.raises(C.TtlNotPermitted):
        _read("repo.metadata", scope="@main", now=1000.0, reader=r, cache_dir=tmp_path)
    assert r.n == 0


# ══════════════════════════════════════════════════════════════════════════════════════════════
# CYCLE-17 — the empty-input refusal belongs in `scope_for`, the ONE function both live paths call
# ══════════════════════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("repo,branch", [("", "main"), ("   ", "main"), ("o/r", ""), ("o/r", "  ")])
def test_scope_for_refuses_an_empty_repo_or_an_empty_branch(repo: str, branch: str) -> None:
    """⛔ The empty-repo guard lived in `read()` ONLY, so the CLI's `--no-cache` branch — which calls
    `reader(a.key)` directly and never enters `read()` — walked around it and spent one call on
    `gh api repos//branches/main/protection` (a guaranteed 404, reported as a GitHub fault).

    An empty BRANCH had the same shape and no guard at all: `f"{repo}@{branch}"` gives `"o/r@"`,
    which is neither empty nor `"@"`-leading, so `read()`'s scope check, `_load` and the reader all
    accepted it and the request became `branches//protection`.
    """
    with pytest.raises(C.TtlNotPermitted):
        C.scope_for("repo.required_set", repo, branch)


def test_scope_for_still_scopes_a_global_key_without_a_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusal must not swallow the GLOBAL branch: `user.identity` depends on the TOKEN, so it
    is legitimately called with no repo/branch at all."""
    monkeypatch.setenv("GH_TOKEN", "tok-value")
    assert C.scope_for("user.identity", "", "").startswith("global@")


@pytest.mark.parametrize("extra", [["--repo", ""], ["--repo", "o/r", "--branch", ""]])
def test_the_CLI_no_cache_path_spends_no_call_on_an_empty_repo_or_branch(
    extra, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """The `--no-cache` arm, end to end. Measured at `a9321ddc6`: `main(["--key","repo.required_set",
    "--repo","","--no-cache"])` attempted the call while the cached path attempted none."""
    calls: list[str] = []
    monkeypatch.setattr(
        C, "_gh_reader", lambda repo, branch: lambda key: calls.append(key) or b"{}"
    )
    rc = C.main(["--key", "repo.required_set", "--no-cache", *extra])
    assert rc == 1
    assert calls == [], "a guaranteed-404 call was spent from the pool this module protects"
    assert "REFUSED" in capsys.readouterr().err


def test_the_CLI_cached_path_refuses_an_empty_repo_too(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """The cached path must reach the same verdict — both paths through `scope_for`."""
    calls: list[str] = []
    monkeypatch.setattr(
        C, "_gh_reader", lambda repo, branch: lambda key: calls.append(key) or b"{}"
    )
    rc = C.main(["--key", "repo.required_set", "--repo", ""])
    assert rc == 1
    assert calls == []
    assert "REFUSED" in capsys.readouterr().err
