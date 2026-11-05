"""Hermetic tests for tools/gh_read_cache.py — the fleet's read cache.

RED-FIRST, by contract. Every test here was written against the SPEC in the tool's
docstring before the tool existed, and the four mandated cases are the four the
lane brief names:

  1. `test_cached_hit_serves_without_a_second_call`  — a fresh invariant key is
     served from cache; the underlying `gh` is invoked EXACTLY ONCE.
  2. `test_ttl_expiry_refetches`                     — once the entry ages past its
     TTL the value is NOT served; `gh` is invoked again.
  3. `test_mutable_key_is_refused`                   — a mutable state read (PR
     state, mergeStateStatus, run status) is REFUSED, and nothing is written to
     the cache dir. This is the rule that stops the tool from ever handing a lane
     a stale merge verdict.
  4. `test_fail_closed_on_unprovable_freshness` (+ the refresh-failure half) —
     when freshness cannot be PROVEN (no timestamp, corrupt entry, clock skew)
     the cached bytes are never emitted; and when the re-fetch itself fails, the
     tool fails rather than falling back to the stale value.

The load-bearing property, stated once: **stale bytes are never emitted as fresh.**
A cache miss is allowed to be slow. It is never allowed to be wrong.

No network, no Docker, no FalkorDB. `gh` is a stub script that records its own
invocation count and echoes a fixture body, so "served from cache" is asserted by
COUNTING INVOCATIONS rather than by trusting a log line.

Run under pytest:  python3 -m pytest tests/test_gh_read_cache.py -q
Run standalone:    python3 tests/test_gh_read_cache.py
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL = REPO_ROOT / "tools" / "gh_read_cache.py"
sys.path.insert(0, str(REPO_ROOT / "tools"))

# The declared TTL for `repo`, imported rather than duplicated: a test that hardcodes
# 3600 passes even when the registry changes underneath it.
from gh_read_cache import INVARIANTS as _INV  # noqa: E402

INVARIANT_REPO_TTL = _INV["repo"].ttl


# ── stub `gh` ────────────────────────────────────────────────────────────────
# Counts invocations in $GH_STUB_DIR/calls and prints $GH_STUB_DIR/body on stdout.
# Exit code is read from $GH_STUB_DIR/exit (default 0) so the fail-closed path can
# be driven without a network and without patching the tool.
_STUB = r'''#!/usr/bin/env bash
set -u
d="${GH_STUB_DIR:?}"
n=0
[ -f "$d/calls" ] && n="$(cat "$d/calls")"
printf '%s' "$((n + 1))" > "$d/calls"
printf '%s\n' "$*" >> "$d/argv.log"
code=0
[ -f "$d/exit" ] && code="$(cat "$d/exit")"
if [ -f "$d/body" ]; then cat "$d/body"; fi
exit "$code"
'''


class CacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="ghrc-")
        self.stub = Path(self.tmp) / "gh"
        self.stub.write_text(_STUB)
        self.stub.chmod(self.stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        self.stubdir = Path(self.tmp) / "stub"
        self.stubdir.mkdir()
        (self.stubdir / "body").write_text('{"id": 1296269, "default_branch": "main"}')
        self.cachedir = Path(self.tmp) / "cache"
        self.cachedir.mkdir()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── helpers ──────────────────────────────────────────────────────────────
    def run_tool(self, *args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["GH_STUB_DIR"] = str(self.stubdir)
        return subprocess.run(
            [sys.executable, str(TOOL), *args],
            capture_output=True,
            text=True,
            env=env,
        )

    def calls(self) -> int:
        f = self.stubdir / "calls"
        return int(f.read_text()) if f.exists() else 0

    def entry_path(self, key: str) -> Path:
        hits = sorted(self.cachedir.glob(f"{key}*.json"))
        self.assertTrue(hits, f"no cache entry written for {key!r} in {self.cachedir}")
        return hits[0]

    def age_entry(self, key: str, seconds: float) -> None:
        """Simulate the passage of time by moving the entry's stamp into the past.

        This tests the REAL freshness predicate (the same one production uses)
        instead of sleeping, so the suite is deterministic and fast.
        """
        p = self.entry_path(key)
        data = json.loads(p.read_text())
        data["fetched_at"] = float(data["fetched_at"]) - seconds
        p.write_text(json.dumps(data))

    # ── 1. cached hit ────────────────────────────────────────────────────────
    def test_cached_hit_serves_without_a_second_call(self) -> None:
        a = self.run_tool("get", "repo", "--repo", "octocat/Hello-World", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(a.returncode, 0, a.stderr)
        self.assertIn("1296269", a.stdout)
        self.assertEqual(self.calls(), 1, "first read must hit gh exactly once")

        b = self.run_tool("get", "repo", "--repo", "octocat/Hello-World", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(b.returncode, 0, b.stderr)
        self.assertEqual(b.stdout, a.stdout, "a cache hit must return identical bytes")
        self.assertEqual(self.calls(), 1, "a cache hit must NOT invoke gh a second time")

    # ── 2. TTL expiry ────────────────────────────────────────────────────────
    def test_ttl_expiry_refetches(self) -> None:
        r = self.run_tool("get", "repo", "--repo", "octocat/Hello-World", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls(), 1)

        # Inside the declared TTL the entry is served.
        inside = self.run_tool("get", "repo", "--repo", "octocat/Hello-World", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(inside.returncode, 0, inside.stderr)
        self.assertEqual(self.calls(), 1, "still inside TTL: no new call")

        # Push the entry past the declared TTL -> must re-fetch, never serve stale.
        self.age_entry("repo", 10_000)
        expired = self.run_tool("get", "repo", "--repo", "octocat/Hello-World", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(expired.returncode, 0, expired.stderr)
        self.assertEqual(self.calls(), 2, "past TTL the value must be re-fetched, not served")

    # ── 3. mutable key refused ───────────────────────────────────────────────
    def test_mutable_key_is_refused(self) -> None:
        # Every one of these is a state read the brief forbids caching outright:
        # a lane reasoning from a stale verdict is the failure mode this rule exists for.
        for key in ("pr_state", "merge_state_status", "run_status"):
            r = self.run_tool("get", key, "--repo", "octocat/Hello-World", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
            self.assertEqual(r.returncode, 3, f"{key} must be REFUSED (rc=3), got {r.returncode}: {r.stderr}")
            self.assertEqual(r.stdout, "", f"{key} must emit NO value when refused")
            self.assertIn("mutable", r.stderr.lower())

        self.assertEqual(self.calls(), 0, "a refused key must not even call gh")
        self.assertEqual(list(self.cachedir.glob("*.json")), [], "a refused key must write nothing")

    def test_mutable_verb_through_an_invariant_named_key_is_refused(self) -> None:
        """The guard must be on the COMMAND, not only on the key's name.

        Declaring a mutable read under an invariant-sounding name is the obvious
        way to smuggle one past a name list, so the argv is classified too.
        """
        r = self.run_tool(
            "get", "repo", "--repo", "octocat/Hello-World",
            "--gh", str(self.stub), "--cache-dir", str(self.cachedir),
            "--argv", "pr", "view", "42", "--json", "mergeStateStatus",
        )
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertEqual(self.calls(), 0)

    def test_mutable_actions_route_is_refused_without_a_field_word(self) -> None:
        """REGRESSION ([VGATE] F1): a mutable ROUTE, with no verdict field in it.

        The first cut compared the FORMATTED command against placeheld prefixes
        (`repos/{repo}/actions/runs`), so this call was NOT refused: it fetched,
        cached an Actions run body under the key `repo`, and a later plain
        `get repo` served it as an invariant. Silently, and with the suite green.
        """
        for route in (
            ("api", "repos/o/n/actions/runs"),
            ("api", "repos/o/n/actions/runs/123/jobs"),
            ("api", "repos/o/n/actions/workflows"),
            ("api", "repos/o/n/actions/workflows/python-ci.yml"),
            ("api", "repos/o/n/commits"),
            ("api", "repos/o/n/pulls/42"),
            ("api", "repos/o/n/issues/7"),
            # the fleet's REAL polling spellings: a query, a leading global flag.
            ("api", "repos/o/n/actions/runs?per_page=100"),
            ("api", "repos/o/n/commits?per_page=5"),
            ("api", "--paginate", "repos/o/n/actions/runs"),
            ("api", "-X", "GET", "repos/o/n/actions/runs/1/jobs"),
        ):
            r = self.run_tool(
                "get", "repo", "--repo", "o/n",
                "--gh", str(self.stub), "--cache-dir", str(self.cachedir),
                "--argv", *route,
            )
            self.assertEqual(r.returncode, 3, f"{' '.join(route)} must be refused: {r.stderr}")
            self.assertEqual(r.stdout, "")
        self.assertEqual(self.calls(), 0, "a refused route must not reach gh")
        self.assertEqual(list(self.cachedir.glob("*.json")), [], "and must write nothing")

    def test_repo_slug_that_looks_like_a_verdict_is_still_served(self) -> None:
        """REGRESSION ([VGATE] F2): the resolved slug is not a REQUEST.

        Scanning the substituted argv made `--repo status/status` exit 3, i.e. an
        invariant read was unservable purely because of the repository's NAME.
        Err-closed, but a false refusal that silently bricks the tool.
        """
        for slug in ("status/status", "acme/merged-web", "o/conclusion-svc",
                     "foo/pulls", "foo/commits", "foo/issues", "acme/check-runs"):
            r = self.run_tool(
                "get", "repo", "--repo", slug,
                "--gh", str(self.stub), "--cache-dir", str(self.cachedir),
            )
            self.assertEqual(r.returncode, 0, f"{slug} must be served: {r.stderr}")
            self.assertIn("1296269", r.stdout)

    def test_clock_skew_tolerance_boundary_is_pinned_both_ways(self) -> None:
        """`CLOCK_SKEW_TOLERANCE_S` is the only bound on served UNPROVEN freshness.

        Mutants 60→10 and 60→600 both left the earlier 40-test suite green (the tests sat at
        +5 and +4000). With the documented `--ttl 1`, a stamp 59 s in the future is SERVED
        and its age is displayed as 0 — the tolerance, not the requested TTL, decides. So the
        boundary is pinned from both sides, through `status`, where it is observable.
        """
        self.run_tool("get", "rate_limit", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        p = self.entry_path("rate_limit")

        def stamp_relative(delta: float) -> dict:
            d = json.loads(p.read_text())
            d["fetched_at"] = time.time() + delta
            p.write_text(json.dumps(d))
            out = self.run_tool("status", "rate_limit", "--ttl", "1", "--cache-dir", str(self.cachedir))
            self.assertEqual(out.returncode, 0, out.stderr)
            return json.loads(out.stdout)

        self.assertTrue(stamp_relative(59)["fresh"], "a stamp inside the tolerance reads as age 0 and is served")
        self.assertFalse(stamp_relative(61)["fresh"], "a stamp beyond the tolerance is a MISS")
        self.assertEqual(
            stamp_relative(61)["reason"], "clock_skew_future",
            "and it must say WHY, so a skewed peer's writes are distinguishable from a stale entry",
        )

    def test_status_refuses_a_mutable_key_as_rc3(self) -> None:
        """REGRESSION ([VGATE] F3): `status` must honour the same contract as `get`."""
        r = self.run_tool("status", "pr_state", "--repo", "o/n", "--cache-dir", str(self.cachedir))
        self.assertEqual(r.returncode, 3, f"status of a mutable key must be rc=3, got {r.returncode}")
        self.assertIn("mutable", r.stderr.lower())

    # ── 4. fail closed ───────────────────────────────────────────────────────
    def test_fail_closed_on_unprovable_freshness(self) -> None:
        """Each of these makes the entry's AGE UNPROVABLE — none may be served."""
        r = self.run_tool("get", "repo", "--repo", "octocat/Hello-World", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls(), 1)
        good = (self.cachedir / self.entry_path("repo").name).read_text()

        # (a) a stamp in the FUTURE: the age cannot be computed, so it cannot be proven fresh.
        p = self.entry_path("repo")
        d = json.loads(good)
        d["fetched_at"] = time.time() + 4_000
        p.write_text(json.dumps(d))
        skew = self.run_tool("get", "repo", "--repo", "octocat/Hello-World", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(skew.returncode, 0, skew.stderr)
        self.assertEqual(self.calls(), 2, "unprovable age (future stamp) must re-fetch, never serve")

        # (b) a CORRUPT entry: unreadable bytes are not evidence of anything.
        p = self.entry_path("repo")
        p.write_text("{ this is not json")
        corrupt = self.run_tool("get", "repo", "--repo", "octocat/Hello-World", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(corrupt.returncode, 0, corrupt.stderr)
        self.assertEqual(self.calls(), 3, "a corrupt entry must re-fetch")

        # (c) a missing timestamp: no clock, no claim.
        p = self.entry_path("repo")
        d = json.loads(good)
        d.pop("fetched_at", None)
        p.write_text(json.dumps(d))
        nots = self.run_tool("get", "repo", "--repo", "octocat/Hello-World", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(nots.returncode, 0, nots.stderr)
        self.assertEqual(self.calls(), 4, "an entry without a timestamp must re-fetch")

    def test_refresh_failure_never_falls_back_to_stale(self) -> None:
        """THE hard half of fail-closed: a failed re-fetch must NOT emit old bytes.

        Returning the stale value here would be the exact "stale as fresh" the
        brief forbids — and it is the tempting behaviour, because the cache is
        right there with a perfectly good answer in it.
        """
        r = self.run_tool("get", "repo", "--repo", "octocat/Hello-World", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(r.returncode, 0, r.stderr)
        good = r.stdout
        self.assertEqual(self.calls(), 1)

        # Expire it, then make gh fail.
        self.age_entry("repo", 10_000)
        (self.stubdir / "exit").write_text("1")

        failed = self.run_tool("get", "repo", "--repo", "octocat/Hello-World", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(failed.returncode, 4, f"a failed refresh must exit 4, got {failed.returncode}: {failed.stderr}")
        self.assertNotIn(good.strip(), failed.stdout, "the STALE body must never appear on a failed refresh")
        self.assertEqual(failed.stdout, "", "a failed refresh emits no value at all")

    # ── write-once semantics / no implicit caching of concurrent shapes ──────
    def test_unknown_key_is_refused(self) -> None:
        r = self.run_tool("get", "not_a_declared_key", "--repo", "octocat/Hello-World", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        # A review finding: this was `assertIn((2, 3))`, which cannot tell
        # "undeclared" (2) from "mutable refusal" (3) — it passed for either.
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(self.calls(), 0, "an undeclared key must not reach gh")

    def test_malformed_repo_is_usage_not_fail_closed(self) -> None:
        """A permanently malformed slug must not look like a transient fetch failure."""
        r = self.run_tool("get", "repo", "--repo", "not-a-slug", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(r.returncode, 2, f"usage error must be rc=2, not a retryable rc=4: {r.stderr}")

    def test_slug_charset_guard_rejects_route_smuggling(self) -> None:
        """A `?`/`#`/space/brace in `--repo` rewrites the route the fetch actually asks.

        Deleting the charset guard left the whole suite green while `--repo 'o/n?per_page=1'`
        answered rc=0 and reached gh as `api repos/o/n?per_page=1`: the tool then cached the
        answer under an identity no legitimate caller could reproduce. Braces are worse —
        `_format_argv` substitutes TWICE, so `--repo 'o/{repo}'` sent `repos/o/{repo}` to gh
        with the placeholder intact, and `--repo 'o/{base}'` silently fetched `o/main`.
        """
        for slug in ("o/n?per_page=1", "o/n#frag", "o /n", "o/{repo}", "o/{base}", "o/n/x/y"):
            r = self.run_tool("get", "repo", "--repo", slug, "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
            self.assertEqual(r.returncode, 2, f"--repo {slug!r} must be usage (rc=2): {r.stderr}")
        self.assertEqual(self.calls(), 0, "a malformed request must not spend a call")

    def test_repo_key_without_repo_is_usage(self) -> None:
        """A route that uses `{repo}` must REFUSE a missing --repo as usage (rc=2).

        Without this, a mutant that lets it through to the fetch path returns rc=4 — a
        RETRYABLE fail-closed verdict for a permanently malformed request, i.e. the same
        confusion `test_malformed_repo_is_usage_not_fail_closed` exists to prevent.
        """
        r = self.run_tool("get", "repo", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(r.returncode, 2, f"missing --repo is usage: {r.stderr}")
        self.assertEqual(self.calls(), 0, "nothing should be fetched for a malformed request")

    def test_verdict_field_on_an_unlisted_route_is_refused(self) -> None:
        """The MUTABLE_FIELDS half must discriminate ON ITS OWN.

        Without this case the field scan could be deleted entirely and every other
        test would still pass — it was covered redundantly by the `pr view` prefix.
        """
        r = self.run_tool(
            "get", "repo", "--repo", "o/n",
            "--gh", str(self.stub), "--cache-dir", str(self.cachedir),
            "--argv", "api", "repos/o/n/labels", "--json", "state",
        )
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertEqual(self.calls(), 0)

    def test_argv_override_is_pass_through_and_stores_nothing(self) -> None:
        """REGRESSION: `--argv` used to cache a live verdict under an invariant key.

        `get repo --argv api repos/o/n/labels` is not the declared command, so it
        has no declared invariant to cache. It may run; it may NOT write.
        """
        r = self.run_tool(
            "get", "repo", "--repo", "o/n",
            "--gh", str(self.stub), "--cache-dir", str(self.cachedir),
            "--argv", "api", "repos/o/n/labels",
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls(), 1, "a pass-through still runs the command once")
        self.assertIn("1296269", r.stdout)
        self.assertEqual(list(self.cachedir.glob("*.json")), [], "a pass-through must store NOTHING")

        # …and must not have left a `repo` entry behind for a later plain read.
        again = self.run_tool("get", "repo", "--repo", "o/n", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(self.calls(), 2, "the plain read must fetch, not reuse the override")

    def test_base_is_not_keyed_when_the_route_does_not_use_it(self) -> None:
        """REGRESSION: `base` was keyed unconditionally, fragmenting the cache.

        A route whose template has no `{base}` is the same question on every branch, so
        keying base spent two calls for one answer — against the budget this tool exists
        to conserve.
        """
        a = self.run_tool("get", "repo", "--repo", "o/r", "--base", "main", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(a.returncode, 0, a.stderr)
        b = self.run_tool("get", "repo", "--repo", "o/r", "--base", "develop", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(b.returncode, 0, b.stderr)
        self.assertEqual(self.calls(), 1, "a branch-insensitive route must be ONE answer")
        self.assertEqual(len(list(self.cachedir.glob("repo*.json"))), 1)

    def test_no_unresolved_placeholder_ever_reaches_gh(self) -> None:
        """GENERALISED C2#1: a `{placeholder}` leaking into the argv is a silent
        wrong-question fetch (it once handed gh `branches/{base}/protection`)."""
        self.run_tool("get", "repo", "--repo", "o/r", "--base", "release/1.0", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.run_tool("get", "rate_limit", "--repo", "o/r", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        logged = (self.stubdir / "argv.log").read_text()
        # Only the RESOLVED placeholders — the `--jq` projection legitimately carries
        # `{full_name,...}` braces, so a bare "{" test would be a false alarm.
        for ph in ("{repo}", "{base}"):
            self.assertNotIn(ph, logged, f"an unresolved {ph} reached gh: {logged!r}")
        self.assertIn("repos/o/r", logged)

    def test_nan_timestamp_is_a_miss(self) -> None:
        """REGRESSION: NaN fails every comparison, so it read as fresh FOREVER."""
        r = self.run_tool("get", "repo", "--repo", "octocat/Hello-World", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(r.returncode, 0, r.stderr)
        p = self.entry_path("repo")
        p.write_text(p.read_text().replace('"fetched_at": ', '"fetched_at": NaN, "_x": '))
        nan = self.run_tool("get", "repo", "--repo", "octocat/Hello-World", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(nan.returncode, 0, nan.stderr)
        self.assertEqual(self.calls(), 2, "a NaN stamp must never be served")

    def test_infinite_stamp_reports_no_age(self) -> None:
        """`math.isfinite` in `cmd_status` is not redundant with the surrounding `try`.

        `float(inf)` does not raise, so `_reported_age` answers `max(0, now - inf) == 0.0`: an
        infinite stamp was reported as `age_s: 0.0` — "just written" — on an entry `status`
        simultaneously called not fresh. Only `isfinite` produces the null.
        """
        self.run_tool("get", "rate_limit", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        p = self.entry_path("rate_limit")
        d = json.loads(p.read_text())
        d["fetched_at"] = float("inf")
        p.write_text(json.dumps(d))
        out = self.run_tool("status", "rate_limit", "--cache-dir", str(self.cachedir))
        self.assertEqual(out.returncode, 0, out.stderr)
        report = json.loads(out.stdout)
        self.assertFalse(report["fresh"], "an infinite stamp is not fresh")
        self.assertIsNone(report["age_s"], "and its age is uncomputable — it must not read as 0.0, i.e. 'just written'")

    def test_ttl_override_can_only_shrink(self) -> None:
        """BEHAVIOURAL, because asserting a stored field proved nothing.

        The earlier version read `ttl_s` back from `status` WITHOUT passing the big TTL,
        so it reported the declared value whether or not the clamp existed — deleting the
        clamp left the suite green.
        """
        big = "31536000"
        r = self.run_tool("get", "repo", "--repo", "o/n", "--ttl", big, "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls(), 1)

        # Age it past the DECLARED ttl (3600) but far short of the requested one.
        self.age_entry("repo", INVARIANT_REPO_TTL + 60)
        again = self.run_tool("get", "repo", "--repo", "o/n", "--ttl", big, "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(self.calls(), 2, "the clamp must have expired it at the DECLARED ttl")

        s = self.run_tool("status", "repo", "--repo", "o/n", "--ttl", big, "--cache-dir", str(self.cachedir))
        self.assertEqual(json.loads(s.stdout)["ttl_s"], INVARIANT_REPO_TTL, "status must report the clamped ttl")

    def test_nonpositive_ttl_is_usage(self) -> None:
        for bad in ("0", "-5"):
            r = self.run_tool("get", "repo", "--repo", "o/n", "--ttl", bad, "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
            self.assertEqual(r.returncode, 2, f"--ttl {bad} must be rc=2: {r.stderr}")
        self.assertEqual(self.calls(), 0)

    def test_entry_without_a_body_is_never_served(self) -> None:
        """REGRESSION: freshness was proven for the envelope, not the payload.

        A body-less or null-body entry was served as a HIT with rc=0 and empty stdout
        (or raised TypeError), and never self-healed for a whole TTL.
        """
        r = self.run_tool("get", "repo", "--repo", "o/n", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls(), 1)

        for mutate in (lambda d: d.pop("body", None),
                       lambda d: d.update(body=None),
                       lambda d: d.update(body="   ")):
            p = self.entry_path("repo")
            d = json.loads(p.read_text())
            d["fetched_at"] = time.time()
            mutate(d)
            p.write_text(json.dumps(d))
            out = self.run_tool("get", "repo", "--repo", "o/n", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
            self.assertEqual(out.returncode, 0, f"must re-fetch, not crash: {out.stderr}")
            self.assertIn("1296269", out.stdout, "a body-less entry must be re-fetched, never served")

    def test_empty_fetch_is_not_cached(self) -> None:
        """A `gh` that exits 0 with empty stdout must not mint a HIT that emits nothing."""
        (self.stubdir / "body").write_text("")
        r = self.run_tool("get", "repo", "--repo", "o/n", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(r.returncode, 4, f"an empty answer is a fail-closed read: {r.stderr}")
        self.assertEqual(list(self.cachedir.glob("*.json")), [], "an empty body must not be stored")

        # ⛔ WHITESPACE-ONLY IS THE SAME ANSWER. `if not body:` let `"   "` through, which
        # stores an entry that can never be a hit (the predicate requires a non-blank body),
        # so every later read re-fetched and printed nothing with rc=0 — a paid call whose
        # result is silently discarded, on every poll.
        (self.stubdir / "body").write_text("   \n\t ")
        w = self.run_tool("get", "repo", "--repo", "o/n", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(w.returncode, 4, f"a blank answer is a fail-closed read: {w.stderr}")
        self.assertEqual(list(self.cachedir.glob("*.json")), [], "a blank body must not be stored")

    def test_unwritable_cache_dir_still_emits_the_value(self) -> None:
        """A spent call must not be thrown away because the cache dir is unwritable.

        uid-INDEPENDENT on purpose: `chmod 0500` does not block root, so the earlier
        version passed on a root container without ever entering the failure branch.
        Putting a DIRECTORY where the entry file must go fails `os.replace` as anyone,
        and also pins that no temp file is left behind.
        """
        s = self.run_tool("status", "repo", "--repo", "o/n", "--cache-dir", str(self.cachedir))
        entry_path = Path(json.loads(s.stdout)["path"])
        entry_path.mkdir(parents=True, exist_ok=True)

        r = self.run_tool("get", "repo", "--repo", "o/n", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(r.returncode, 0, f"must degrade, not crash: {r.stderr}")
        self.assertIn("1296269", r.stdout, "the fetched body must still be emitted")
        self.assertEqual(self.calls(), 1)
        leftovers = [p.name for p in self.cachedir.glob("*.tmp")]
        self.assertEqual(leftovers, [], f"a failed install must not leak its temp file: {leftovers}")

    def test_uncreatable_cache_dir_still_emits_the_value(self) -> None:
        """REGRESSION: `_cache_dir` mkdir sat outside every `except OSError`."""
        blocked = Path(self.tmp) / "not-a-dir"
        blocked.write_text("i am a file")
        r = self.run_tool("get", "repo", "--repo", "o/n", "--gh", str(self.stub), "--cache-dir", str(blocked))
        self.assertEqual(r.returncode, 0, f"must degrade to an uncached read, not rc=1: {r.stderr}")
        self.assertIn("1296269", r.stdout)

    def test_argv_with_no_tokens_is_usage(self) -> None:
        """`--argv` with an empty remainder parsed to `[]` (falsy) and silently CACHED."""
        r = self.run_tool("get", "repo", "--repo", "o/n", "--gh", str(self.stub), "--cache-dir", str(self.cachedir), "--argv")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(self.calls(), 0)
        self.assertEqual(list(self.cachedir.glob("*.json")), [])

    def test_two_repos_do_not_share_an_entry(self) -> None:
        """THE cross-repo poisoning pin (survivors M31/M32/M34).

        Every other test uses ONE repo per key and a constant stub body, so collapsing the
        identity to a constant left 33/33 green. This one CHANGES the body between repos,
        so serving acme's bytes for globex becomes observable.
        """
        (self.stubdir / "body").write_text('{"who": "acme"}')
        a = self.run_tool("get", "repo", "--repo", "acme/one", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(a.returncode, 0, a.stderr)
        self.assertIn("acme", a.stdout)

        (self.stubdir / "body").write_text('{"who": "globex"}')
        b = self.run_tool("get", "repo", "--repo", "globex/two", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(b.returncode, 0, b.stderr)
        self.assertIn("globex", b.stdout, "a second repo must NOT be served the first repo's cached bytes")
        self.assertEqual(self.calls(), 2, "two repos are two questions")

        # …and both are now independently cached.
        self.run_tool("get", "repo", "--repo", "acme/one", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.run_tool("get", "repo", "--repo", "globex/two", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(self.calls(), 2, "re-reads of both repos must be hits")

    def test_leading_slash_route_is_refused(self) -> None:
        """`gh api /repos/...` is a real spelling and matched neither candidate."""
        r = self.run_tool("get", "repo", "--repo", "o/n", "--gh", str(self.stub), "--cache-dir", str(self.cachedir),
                          "--argv", "api", "/repos/o/n/pulls/42")
        self.assertEqual(r.returncode, 3, f"a leading-slash route must still be refused: {r.stderr}")
        self.assertEqual(self.calls(), 0)

    def test_attached_projection_flag_spellings_are_refused(self) -> None:
        """`--json=state`, `--jq=.state`, `-q.state` are real invocations."""
        for tail in (("--json=state",), ("--jq=.state",), ("--template=.state",), ("-q.state",), ("-t.state",)):
            r = self.run_tool("get", "repo", "--repo", "o/n", "--gh", str(self.stub), "--cache-dir", str(self.cachedir),
                              "--argv", "api", "repos/o/n/labels", *tail)
            self.assertEqual(r.returncode, 3, f"{' '.join(tail)} must be refused: {r.stderr}")
        self.assertEqual(self.calls(), 0)

    def test_host_prefixed_gh_repo_form_is_accepted(self) -> None:
        """gh documents `HOST/OWNER/REPO`; rejecting it broke a standard GH_REPO export."""
        r = self.run_tool("get", "repo", "--repo", "github.com/o/n", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(r.returncode, 0, f"host-prefixed form must be accepted: {r.stderr}")
        self.assertIn("1296269", r.stdout)

    def test_a_single_label_or_port_host_is_accepted(self) -> None:
        """Detecting a host by the presence of a DOT refused documented forms.

        `ghe/o/n` (a single-label GHES host) and `localhost:8080/o/n` are both valid, and
        both answered rc=2 "--repo must be owner/name (or host/owner/name)" — the false
        usage class this tool's own malformed-slug test exists to avoid.
        """
        for form in ("ghe/o/n", "localhost:8080/o/n"):
            r = self.run_tool("get", "repo", "--repo", form, "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
            self.assertEqual(r.returncode, 0, f"{form} is a documented HOST/OWNER/REPO form: {r.stderr}")

    def test_two_hosts_do_not_share_an_entry(self) -> None:
        """SAME `owner/name` ON TWO GH INSTANCES IS TWO REPOSITORIES.

        The host was accepted and then DISCARDED (`parts = parts[1:]`), and `_identity` had
        no host component, so `github.com/o/n` and `ghe.example.com/o/n` shared one entry:
        the second read answered rc=0 with the FIRST instance's `default_branch` /
        `visibility` and reported it FRESH for up to an hour — the one output this tool
        forbids, reached through the identity instead of the read path. The cross-repo pin
        next door could not catch it (two slugs, one host).
        """
        a = self.run_tool("get", "repo", "--repo", "github.com/o/n", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(a.returncode, 0, a.stderr)
        self.assertIn("1296269", a.stdout)

        (self.stubdir / "body").write_text('{"id": 42, "default_branch": "ghe-main"}')
        b = self.run_tool("get", "repo", "--repo", "ghe.example.com/o/n", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(b.returncode, 0, b.stderr)
        self.assertEqual(self.calls(), 2, "a different instance must be a MISS, not the other host's bytes")
        self.assertIn("ghe-main", b.stdout)
        self.assertNotIn("1296269", b.stdout)

    def test_gh_host_env_var_is_part_of_the_identity(self) -> None:
        """`GH_HOST` is how gh picks the instance, and it was never read."""
        env = dict(os.environ, GH_STUB_DIR=str(self.stubdir), GH_HOST="ghe.example.com")
        first = subprocess.run(
            [sys.executable, str(TOOL), "get", "repo", "--repo", "o/n", "--gh", str(self.stub), "--cache-dir", str(self.cachedir)],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        # The same slug WITHOUT GH_HOST is a different instance -> a different question.
        (self.stubdir / "body").write_text('{"id": 42, "default_branch": "dotcom"}')
        second = self.run_tool("get", "repo", "--repo", "o/n", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.calls(), 2, "GH_HOST must participate in the cache identity")
        self.assertIn("dotcom", second.stdout)

    def test_rate_limit_is_keyed_by_instance_too(self) -> None:
        """The host is WHICH SERVER ANSWERED — true of `/rate_limit` as much as of `repo`.

        The first host fix gated `host` on `uses_repo`, and `rate_limit`'s template has no
        `{repo}`, so `host` was blanked for it: `GH_HOST=ghe.example.com get rate_limit`
        then `get rate_limit` on github.com answered rc=0 from the FIRST instance's entry,
        reporting another server's remaining quota as this one's, fresh for 30 s. A lane
        reads quota precisely when it is deciding whether it may spend — the worst moment
        to hand it a sibling instance's number.
        """
        env = dict(os.environ, GH_STUB_DIR=str(self.stubdir), GH_HOST="ghe.example.com")
        (self.stubdir / "body").write_text('{"resources": {"core": {"remaining": 4990}}}')
        first = subprocess.run(
            [sys.executable, str(TOOL), "get", "rate_limit", "--gh", str(self.stub), "--cache-dir", str(self.cachedir)],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("4990", first.stdout)

        (self.stubdir / "body").write_text('{"resources": {"core": {"remaining": 12}}}')
        second = self.run_tool("get", "rate_limit", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.calls(), 2, "two instances must not share a rate_limit entry")
        self.assertIn("12", second.stdout)
        self.assertNotIn("4990", second.stdout)

    def test_spelled_and_unspelled_host_are_one_repository(self) -> None:
        """`o/n` and `github.com/o/n` are the SAME repo, so they must be ONE entry.

        Canonicalising an unspelled host to `""` made them two: `get --repo github.com/o/n`
        wrote one entry, `status --repo o/n` looked for the other and reported
        `present: false`, and the next `get` spent a call fetching what was already on disk.
        """
        g = self.run_tool("get", "repo", "--repo", "github.com/o/n", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(g.returncode, 0, g.stderr)
        st = self.run_tool("status", "repo", "--repo", "o/n", "--cache-dir", str(self.cachedir))
        self.assertEqual(st.returncode, 0, st.stderr)
        self.assertTrue(json.loads(st.stdout)["present"], "status must find the entry get wrote")
        again = self.run_tool("get", "repo", "--repo", "o/n", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(self.calls(), 1, "the unspelled spelling must be a HIT, not a second call")
        self.assertEqual(len(list(self.cachedir.glob("repo*.json"))), 1, "one repository, one entry")

    def test_non_utf8_gh_output_fails_closed_not_crash(self) -> None:
        """UnicodeDecodeError escaped as an undocumented rc=1 traceback."""
        (self.stubdir / "body").write_bytes(b"\xff\xfe\x00binary")
        r = self.run_tool("get", "repo", "--repo", "o/n", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(r.returncode, 4, f"binary output must fail closed, not rc=1: {r.stderr}")
        self.assertEqual(r.stdout, "")
        self.assertNotIn("Traceback", r.stderr)

    def test_spelled_out_slug_is_not_treated_as_a_verdict_request(self) -> None:
        """The ALLOWANCE direction of the field scan (survivor M15).

        Without this, reverting the cycle-5 fix (scanning the whole command) leaves the
        suite green while `--argv api repos/o/state/labels` is falsely refused.
        """
        for route in (("api", "repos/o/state/labels"), ("api", "repos/o/status/status")):
            r = self.run_tool("get", "repo", "--repo", "o/n", "--gh", str(self.stub), "--cache-dir", str(self.cachedir),
                              "--argv", *route)
            self.assertEqual(r.returncode, 0, f"{' '.join(route)} is not a verdict request: {r.stderr}")

    def test_field_words_are_matched_as_whole_names_not_substrings(self) -> None:
        """The ALLOWANCE direction of the FIELD scan for expressions.

        `if field in lowered` over the joined projection text refused `--jq .merged_at` as
        "names the verdict field 'merged'", and `mergeable_state` / `state_reason` the same
        way — legitimate reads rejected by a guard whose job is to reject verdicts.
        """
        for expr in (("--jq", ".merged_at"), ("--jq", ".mergeable_state"), ("--json", "state_reason")):
            r = self.run_tool("get", "repo", "--repo", "o/n", "--gh", str(self.stub), "--cache-dir", str(self.cachedir),
                              "--argv", "api", "repos/o/n/labels", *expr)
            self.assertEqual(r.returncode, 0, f"{' '.join(expr)} is not a verdict field: {r.stderr}")

    def test_encoded_and_uppercase_route_spellings_are_refused(self) -> None:
        """gh resolves these; a guard that only knows one spelling is not a guard.

        `p%75lls` percent-decodes to `pulls` inside gh, `PULLS` is the same route, and an
        `api graphql` query is a live read whose content cannot be classified from argv.
        """
        spellings = (
            ("api", "repos/o/n/p%75lls/42"),
            ("api", "repos/o/n/PULLS/42"),
            ("api", "graphql", "-f", 'query={repository(owner:"o"){pullRequest(number:1){state}}}'),
            ("api", "repos/o/n/actions/artifacts"),
        )
        for argv in spellings:
            r = self.run_tool("get", "repo", "--repo", "o/n", "--gh", str(self.stub), "--cache-dir", str(self.cachedir),
                              "--argv", *argv)
            self.assertEqual(r.returncode, 3, f"{' '.join(argv)} must be refused: {r.stderr}")
        self.assertEqual(self.calls(), 0, "a refused route must not reach gh at all")

    def test_bracket_index_verdict_spellings_are_refused(self) -> None:
        """jq's `.["state"]` defeated both the name splitter and the `.field` regex.

        Five real verdict reads were SERVED rc=0 while `.state` was refused — the documented
        refusal contract, bypassable with a pair of quotes. A quoted STRING COMPARISON is a
        filter, not a projection, and must stay allowed.
        """
        refused = (
            ('.["state"]',), ('.[ "state" ]',), ('$x["mergeStateStatus"]',),
            ('.["conclusion"]',), ('.["merged"]',), ('.["state"] // empty',),
        )
        for (expr,) in refused:
            r = self.run_tool("get", "repo", "--repo", "o/n", "--gh", str(self.stub), "--cache-dir", str(self.cachedir),
                              "--argv", "api", "repos/o/n/labels", "--jq", expr)
            self.assertEqual(r.returncode, 3, f"--jq {expr} names a verdict field: {r.stderr}")
        # …and the filter form is not a projection.
        ok = self.run_tool("get", "repo", "--repo", "o/n", "--gh", str(self.stub), "--cache-dir", str(self.cachedir),
                           "--argv", "api", "repos/o/n/labels", "--jq", 'map(select(.name=="state"))')
        self.assertEqual(ok.returncode, 0, f"a string COMPARISON is not a projection: {ok.stderr}")
        self.assertEqual(self.calls(), 1, "only the allowed read may reach gh")

    def test_concurrent_writers_leave_one_valid_entry_and_no_temp_files(self) -> None:
        """Atomicity (survivor M22): N parallel readers must not leave debris."""
        env = dict(os.environ)
        env["GH_STUB_DIR"] = str(self.stubdir)
        procs = [
            subprocess.Popen(
                [sys.executable, str(TOOL), "get", "repo", "--repo", "o/n",
                 "--gh", str(self.stub), "--cache-dir", str(self.cachedir)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
            )
            for _ in range(8)
        ]
        for p in procs:
            out, err = p.communicate(timeout=120)
            self.assertEqual(p.returncode, 0, err)
        self.assertEqual([p.name for p in self.cachedir.glob("*.tmp")], [], "no temp file may survive")
        entries = list(self.cachedir.glob("*.json"))
        self.assertEqual(len(entries), 1, f"exactly one entry expected, got {[e.name for e in entries]}")
        self.assertIn("1296269", json.loads(entries[0].read_text())["body"])

    def test_check_self_test_passes(self) -> None:
        """`check` is advertised as the self-check; nothing asserted it ran."""
        r = self.run_tool("check")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("freshness cases behave", r.stdout)

    # ── the invariant is the PROJECTION (C4 finding: unpinned) ───────────────
    def test_repo_projection_excludes_live_counters(self) -> None:
        """The `--jq` projection IS the invariant — deleting it must fail here.

        Without this, `/repos/{repo}` caches `open_issues_count`, `pushed_at` and
        `stargazers_count` for an hour and serves hour-old live numbers as fresh.
        """
        r = self.run_tool("get", "repo", "--repo", "o/n", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(r.returncode, 0, r.stderr)
        logged = (self.stubdir / "argv.log").read_text()
        self.assertIn("--jq", logged, "the projection must be sent")
        for live in ("open_issues_count", "pushed_at", "updated_at", "stargazers_count",
                     "forks_count", "watchers", "subscribers_count", "size"):
            self.assertNotIn(live, logged, f"live counter {live!r} must not be in the projection")
        for keep in ("default_branch", "visibility", "archived", "full_name"):
            self.assertIn(keep, logged)

    # ── rate_limit is ACCOUNT-scoped (C4 finding: fragmented by repo) ────────
    def test_rate_limit_needs_no_repo_and_is_not_fragmented(self) -> None:
        a = self.run_tool("get", "rate_limit", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(a.returncode, 0, f"an account-scoped route must work bare: {a.stderr}")
        b = self.run_tool("get", "rate_limit", "--repo", "o/other", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(b.returncode, 0, b.stderr)
        self.assertEqual(self.calls(), 1, "the account bucket is one answer, not one per repo")

    # ── identity / schema guards must be live at the CALL SITE ───────────────
    def test_tampered_entry_identity_is_a_miss(self) -> None:
        """C4 finding: dropping `expect=` at the call site served a poisoned entry."""
        r = self.run_tool("get", "repo", "--repo", "o/n", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls(), 1)

        for field, value in (("repo", "ATTACKER/other"), ("host", "ghe.attacker.example"), ("argv", ["api", "repos/x/y"])):
            p = self.entry_path("repo")
            d = json.loads(p.read_text())
            d["fetched_at"] = time.time()
            d[field] = value
            p.write_text(json.dumps(d))
            st = self.run_tool("status", "repo", "--repo", "o/n", "--cache-dir", str(self.cachedir))
            self.assertFalse(json.loads(st.stdout)["fresh"], f"a tampered {field} must not read fresh")
            before = self.calls()
            out = self.run_tool("get", "repo", "--repo", "o/n", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
            self.assertEqual(out.returncode, 0, out.stderr)
            # ⛔ THE CALL COUNT IS THE ASSERTION. The body is unchanged by an identity
            # tamper, so asserting only on the body passed even with `expect=None` — the
            # guard could be deleted at the call site and the suite stayed green.
            self.assertEqual(
                self.calls(), before + 1,
                f"a tampered {field} must be RE-FETCHED (expect= must not be dropped)",
            )
            self.assertIn("1296269", out.stdout)

    def test_wrong_schema_is_a_miss(self) -> None:
        r = self.run_tool("get", "repo", "--repo", "o/n", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(r.returncode, 0, r.stderr)
        p = self.entry_path("repo")
        d = json.loads(p.read_text())
        d["schema"] = 1
        d["fetched_at"] = time.time()
        p.write_text(json.dumps(d))
        st = self.run_tool("status", "repo", "--repo", "o/n", "--cache-dir", str(self.cachedir))
        self.assertFalse(json.loads(st.stdout)["fresh"], "a foreign schema must not read fresh")
        out = self.run_tool("get", "repo", "--repo", "o/n", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.calls(), 2, "a foreign schema must be re-fetched, never served")

    def test_oversized_integer_stamp_is_a_miss_not_a_crash(self) -> None:
        """C4 finding: a `10**400` stamp made `math.isfinite` raise -> rc=1 traceback.

        ⛔ `status` IS CHECKED WHILE THE CORRUPT STAMP IS STILL ON DISK. The first cut
        asserted it only AFTER a second `get`, which re-fetched and OVERWROTE the entry, so
        the assertion inspected a valid record: deleting `cmd_status`'s isfinite/`try` left
        the whole suite green while `status` on a genuinely oversized stamp raised
        `OverflowError: int too large to convert to float` -> an undocumented rc=1.
        """
        r = self.run_tool("get", "repo", "--repo", "o/n", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(r.returncode, 0, r.stderr)
        p = self.entry_path("repo")
        d = json.loads(p.read_text())
        d["fetched_at"] = 10 ** 400
        p.write_text(json.dumps(d))

        st = self.run_tool("status", "repo", "--repo", "o/n", "--cache-dir", str(self.cachedir))
        self.assertEqual(st.returncode, 0, f"status must not crash on a corrupt stamp: {st.stderr}")
        report = json.loads(st.stdout)
        self.assertFalse(report["fresh"], "an oversized stamp must not read as fresh")
        self.assertIsNone(report["age_s"], "an uncomputable age must be null, not a traceback")

        out = self.run_tool("get", "repo", "--repo", "o/n", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(out.returncode, 0, f"a corrupt stamp must be a MISS, not rc=1: {out.stderr}")
        self.assertEqual(self.calls(), 2)

    def test_status_degrades_with_an_uncreatable_cache_dir(self) -> None:
        """C4 finding: only `get`'s degraded path was pinned."""
        blocked = Path(self.tmp) / "blocked-status"
        blocked.write_text("i am a file")
        r = self.run_tool("status", "repo", "--repo", "o/n", "--cache-dir", str(blocked))
        self.assertEqual(r.returncode, 0, f"status must degrade, not crash: {r.stderr}")
        self.assertIsNone(json.loads(r.stdout)["path"])

    def test_reported_age_is_never_negative(self) -> None:
        """C4 finding: `--explain` printed `age -5s` for a stamp inside tolerance."""
        r = self.run_tool("get", "repo", "--repo", "o/n", "--gh", str(self.stub), "--cache-dir", str(self.cachedir))
        self.assertEqual(r.returncode, 0, r.stderr)
        p = self.entry_path("repo")
        d = json.loads(p.read_text())
        d["fetched_at"] = time.time() + 5  # inside the 60 s tolerance -> fresh
        p.write_text(json.dumps(d))
        hit = self.run_tool("get", "repo", "--repo", "o/n", "--gh", str(self.stub), "--cache-dir", str(self.cachedir), "--explain")
        self.assertEqual(hit.returncode, 0, hit.stderr)
        self.assertIn("cache HIT", hit.stderr)
        self.assertNotIn("age -", hit.stderr, f"age must not be reported negative: {hit.stderr}")
        st = self.run_tool("status", "repo", "--repo", "o/n", "--cache-dir", str(self.cachedir))
        self.assertGreaterEqual(json.loads(st.stdout)["age_s"], 0)

    def test_keys_lists_the_registry_with_ttls(self) -> None:
        r = self.run_tool("keys")
        self.assertEqual(r.returncode, 0, r.stderr)
        for key in ("repo", "rate_limit"):
            self.assertIn(key, r.stdout)
        # `protection` is deliberately NOT declared — see the registry comment. If it
        # comes back, the 404-on-unprotected retry hazard must be solved first.
        self.assertNotIn("protection ", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
