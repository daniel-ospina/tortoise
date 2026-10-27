"""Hermetic tests for tools/collision_preflight.py (#3061).

No network, no Docker, no FalkorDB. The GitHub surfaces are driven by a stub
`gh` binary that reads JSON fixtures from $GH_STUB_DIR; the git surfaces use a
real temp git repo (branches, a remote-tracking ref, real `git worktree add`).

Run standalone:      python3 tests/test_collision_preflight.py
Run under pytest:    python3 -m pytest tests/test_collision_preflight.py -q

Coverage:
  * CLEAN: no hits on any surface -> exit 0, VERDICT: CLEAN
  * one stubbed/real HIT per hit-capable surface -> exit != 0, surface named
  * UNTRUNCATED worktree scan: a hit that a `tail -8` window would hide is found
  * #6622 a RECORD is not a WORKER: a registered worktree whose directory is
    gone, or whose `.git` marker is gone, is reported with its basis (`exists=`
    / `gitdir=` / `age=`) and CANNOT block; a `refs/remotes/<x>/…` ref that NO
    configured remote's fetch REFSPEC targets (a one-shot fetch cache key, with
    or without a trailing segment — `pr/6592` and `pr6592` alike) is
    non-blocking, while a ref inside a namespace a configured remote actually
    fetches into still blocks
  * number boundary: `3061` does NOT match `30610` (no fabricated collision)
  * PR-body PROSE is not a collision: a closed PR body that merely
    cross-references `#N` (the live #2926/#2754 shapes) is WEAK/non-blocking ->
    CLEAN; a body closing reference (`Closes #N`) is still a strong hit
  * the claim gate reads the HOLDER, never comment prose (#8175): the FLEET BOARD
    attributes a lane to the issue (branch slug / worktree path / the lane's own
    first user message) and that lane's own `session.pid_alive` says whether it
    still holds it. A LIVE holder BLOCKS; a STOPPED holder (the board MEASURED
    liveness and found a recorded, non-alive `pi_pid`) is reported and does not;
    a terminal workspace is never evidence about the process (it can only
    supply the STOPPED reason's detail), so a terminal lane whose liveness is
    unmeasured is INCOMPLETE and blocks;
    an unmeasured liveness, an ambiguous attribution, or a board that cannot
    answer (unreadable, aged past the freshness window AND unrebuildable) is
    INCOMPLETE (exit 2), never CLEAN. There is NO dismissal switch, and no
    comment body is fetched at all
  * PR lists are completeness-checked: an OPEN list longer than its cap is
    reported TRUNCATED and the run is INCOMPLETE (exit 2), never CLEAN
  * the closed-PR surface is fetched in ONE bounded request over the REST API
    (`gh api -i`), not the `gh pr list` GraphQL path that resets on this host
    (#3587) and not the `--paginate` multi-request enumeration it replaced
    (#5251). It is ADVISORY: a hit is reported but can never block, and a
    failure / partial sample is reported but can never force INCOMPLETE. A
    partial sample is `~N` (derived from the response's own Link header), never
    silently presented as the whole list
  * keyword hits match name-like fields only (`.worktrees/` structural token
    in a title does not collide)
  * an unqueryable surface (gh / git / keyword source) -> INCOMPLETE, exit 2,
    never CLEAN
  * every surface row is always present (the check cannot run partially)
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "collision_preflight.py"
PYTHON = sys.executable
ISSUE = 3061
#: The slug the fixture's git remote resolves to. `_fleet_board_state` stamps it
#: as the board's `repo`, so a cached board matches the queried slug by default.
SLUG = "test-owner/test-repo"

GH_STUB = r"""#!/usr/bin/env bash
set -u
d="${GH_STUB_DIR:?GH_STUB_DIR unset}"
state=""
prev=""
for a in "$@"; do
  if [ "$prev" = "--state" ]; then state="$a"; fi
  prev="$a"
done
repo=""
prev=""
for a in "$@"; do
  if [ "$prev" = "--repo" ]; then repo="$a"; fi
  prev="$a"
done
json_args=""
prev=""
for a in "$@"; do
  if [ "$prev" = "--json" ]; then json_args="$a"; fi
  prev="$a"
done

# `gh api user -q .login` -> the current account (ownership attribution).
if [ "$1 $2" = "api user" ]; then
  if [ -f "$d/user.txt" ]; then cat "$d/user.txt"; else echo "test-agent"; fi
  exit 0
fi

# REST issue metadata (#3435): `gh api repos/<slug>/issues/<N>`, the fallback the
# tool takes when the GraphQL budget is exhausted. Deliberately emits REST's
# LOWERCASE `state` and `html_url`, so a test proves the normalisation onto the
# `gh issue view --json` shape rather than assuming the two agree.
if [ "$1" = "api" ] && [[ "$2" =~ ^repos/[^/]+/[^/]+/issues/[0-9]+$ ]]; then
  # ⛔ MIRROR THE REAL ENDPOINT'S FAILURES, or the fallback becomes a fail-open
  # TEST HARNESS. `$repo` is parsed from `--repo`, which this REST form does not
  # carry, so the slug must be recovered FROM THE PATH — a guard keyed on an empty
  # `$repo` can never match and is dead code that merely looks like protection.
  _slug="${2#repos/}"; _slug="${_slug%/issues/*}"
  if [ "${GH_STUB_ISSUE_REST_FAIL:-0}" = "1" ]; then
    echo "gh-stub: HTTP 403: Resource not accessible" >&2
    exit 1
  fi
  # #3435 review (P1): a 200 with a body that BREAKS the issue-payload contract.
  # Without this seam the malformed-REST arm is never executed, which is why a
  # crash on it survived a review cycle: the only other failures here are
  # transport failures, which never reach the validation.
  if [ "${GH_STUB_ISSUE_REST_MALFORMED:-0}" = "1" ]; then
    printf '{"number": %s}\n' "${2##*/}"
    exit 0
  fi
  # #3435 review (P2): REST says 404 while the GraphQL read was rate-limited.
  # A REST-only absence seam, because GH_STUB_ISSUE_ABSENT fails the `issue view`
  # call FIRST (so the fallback is never taken) and would pin the wrong path.
  if [ "${GH_STUB_ISSUE_REST_ABSENT:-0}" = "1" ]; then
    echo "gh-stub: HTTP 404: Not Found (https://api.github.com/repos/x/y/issues/1)" >&2
    exit 1
  fi
  # #3435 review (P2): `subprocess.run(text=True)` decodes STRICTLY, so one bad
  # byte used to raise UnicodeDecodeError — not a SurfaceError — and escaped as a
  # traceback with no VERDICT line and exit 1 (COLLISION).
  if [ "${GH_STUB_ISSUE_REST_INVALID_UTF8:-0}" = "1" ]; then
    printf '\xff\xfe{"number": %s}\n' "${2##*/}"
    exit 0
  fi
  # #3435 review (P2): `/issues/<N>` is the ISSUE-OR-PULL-REQUEST endpoint, and a
  # PR object carries every key the normaliser reads — so without a guard it was
  # read as an issue (measured: VERDICT CLEAN, i.e. the fail-open direction).
  if [ "${GH_STUB_ISSUE_REST_PULL_REQUEST:-0}" = "1" ]; then
    printf '{"number": %s, "title": "feat: a PR", "state": "open", "state_reason": null, "assignees": [], "html_url": "https://github.com/x/y/pull/%s", "pull_request": {"url": "https://api.github.com/repos/x/y/pulls/%s"}}\n' "${2##*/}" "${2##*/}" "${2##*/}"
    exit 0
  fi
  # #3435 review (P2): a body about a DIFFERENT issue must not be read as this
  # one's state/assignees (an `OPEN` with no assignees reads free).
  if [ "${GH_STUB_ISSUE_REST_WRONG_NUMBER:-0}" = "1" ]; then
    printf '{"number": %s, "title": "some other issue", "state": "open", "state_reason": null, "assignees": [], "html_url": "https://github.com/x/y/issues/%s"}\n' "$(( ${2##*/} + 1 ))" "$(( ${2##*/} + 1 ))"
    exit 0
  fi
  # #3435 review (P2): the CLOSED-over-REST path, and the ONE key REST spells
  # differently. `_issue_view_rest` maps REST's `state_reason` onto the
  # `stateReason` key `gh issue view --json` returns, and every other REST
  # success here answers an OPEN issue with `state_reason: null` — so deleting
  # that mapping kept the whole suite green while a CLOSED issue rescued over
  # REST lost its reason and fell back to the generic "TERMINAL" wording
  # instead of naming the closure that happened (the #8497 wording defect,
  # `state_reason=not_planned` read as a shipment, reached through a second key
  # spelling). It is also the only fixture that decides LANDED from a REST body
  # alone: a terminal state SKIPS the timeline query.
  if [ -n "${GH_STUB_ISSUE_REST_CLOSED:-}" ]; then
    printf '{"number": %s, "title": "florfenicol dosing audit", "state": "closed", "state_reason": "%s", "assignees": [], "html_url": "https://github.com/%s/issues/%s"}\n' "${2##*/}" "$GH_STUB_ISSUE_REST_CLOSED" "$_slug" "${2##*/}"
    exit 0
  fi
  # #3435 review (P2): a top-level NON-OBJECT body (a JSON array, a bare
  # string) is refused BY NAME. Unpinned, deleting the guard turns the body into
  # an `AttributeError` in the normaliser — no VERDICT line, exit 1, the code
  # this tool documents as COLLISION.
  if [ "${GH_STUB_ISSUE_REST_NON_OBJECT:-0}" = "1" ]; then
    printf '[]\n'
    exit 0
  fi
  # NOTE: there is deliberately NO `GH_STUB_ISSUE_ABSENT` guard here, and no
  # copy of it belongs here either. That seam fails the `issue view` call FIRST
  # (so the fallback is never attempted), which makes a copy inside THIS branch
  # unreachable code that reads as protection — the same defect the `$repo` note
  # above names. The live REST-404 seam is `GH_STUB_ISSUE_REST_ABSENT` above.
  # (Cycle 4's commit text recorded this copy as DELETED; that commit had only
  # added this note, so the record and the code disagreed until now.)
  # The endpoint failing FOR THIS SLUG (an unreachable host, a proxy refusing
  # the repo) — the REST counterpart of the `$repo`-keyed probe guard further
  # down. ⛔ IT MUST BE FIRED BY A TEST OR IT DOES NOT EXIST (#3435 review, P2):
  # the only other writer of `probe_unqueried.txt` is the cross-repo CANDIDATE,
  # while this branch is reached only for the TARGET slug, so an unwired guard
  # here is dead code that merely looks like protection — the defect the `$repo`
  # note above names. `test_a_rest_transport_failure_names_the_transport_not_a_
  # missing_issue` drives it (a `--repo` run, so the ambiguity probe does not
  # read the file, plus a rate-limited `issue view`); deleting this guard makes
  # that case go CLEAN.
  if [ -f "$d/probe_unqueried.txt" ] && grep -qx "$_slug" "$d/probe_unqueried.txt"; then
    echo "gh-stub: could not connect to api.github.com" >&2; exit 1
  fi
  printf '{"number": %s, "title": "florfenicol dosing audit", "state": "open", "state_reason": null, "assignees": [], "html_url": "https://github.com/%s/issues/%s"}\n' "${2##*/}" "$_slug" "${2##*/}"
  exit 0
fi

# #9402: the OPEN-PR REST rescue. `gh pr list --json closingIssuesReferences` is
# GraphQL-only and the GraphQL budget is fleet-shared, so when it refuses the tool
# enumerates open PRs over REST (`gh api repos/<slug>/pulls?state=open&per_page=N`),
# which has its own budget. Deliberately emits REST's LOWERCASE `state`, nested
# `head.ref` and `html_url`/`merged_at` spellings, so a test proves the
# normalisation onto the `gh pr list --json` projection rather than assuming the two
# agree. ⛔ The payload carries NO `closingIssuesReferences` — REST has no such
# field — so a test cannot accidentally model a rescue that answered it.
#
# ⛔ THE PATTERN IS ANCHORED TO THE ENDPOINT THE RESCUE ACTUALLY CALLS. It used to
# match `^repos/<owner>/<repo>/pulls`, which also swallowed
# `_branch_terminal_state_from_prs`'s `pulls?state=all&head=…` lookup and answered
# it with THIS fixture (REST's `/pulls` shape, no `headSha`, no `mergedAt`), so the
# branch surface's "targeted head-ref lookup could not run" path stopped being
# exercised by the integration fixtures. A stub that answers an endpoint it does
# not model is a fixture lying about the wire.
# #10174: the MERGED LEFTOVER note asks the ONE question only GitHub can answer
# authoritatively — does the remote still hold this branch? (`api
# repos/<slug>/branches/<name>`; the issue's own measurement on #3524 read a 404
# there and wrote `branch API 404` in its evidence table). The default is 404:
# the merged-leftover population is branches whose remote counterpart was deleted
# at merge, so "gone" is the honest default for a fixture that models one. The
# other two states are opt-in seams — a fixture body for "still there", and
# `GH_STUB_REMOTE_BRANCH_FAIL` for an unreadable read (the note must never
# narrate a failed read as GONE, nor let it undo the demotion).
if [ "$1" = "api" ] && [[ "$2" =~ ^repos/[^/]+/[^/]+/branches/ ]]; then
  # ONE line per INVOCATION: the cost bound (#10174) is "only for a ref the merge
  # history already demoted", and a log is what makes that falsifiable — a
  # regressed per-branch read would append once per LOCAL BRANCH.
  printf '%s\n' "$*" >> "$d/remote-branch-calls.log"
  printf '%s\n' "$@" > "$d/remote-branch-argv.txt"
  if [ "${GH_STUB_REMOTE_BRANCH_FAIL:-0}" = "1" ]; then
    echo "gh-stub: could not connect to api.github.com" >&2
    exit 1
  fi
  if [ -f "$d/remote_branch.json" ]; then cat "$d/remote_branch.json"; exit 0; fi
  echo "gh-stub: HTTP 404: Not Found (https://api.github.com/$2)" >&2
  exit 1
fi

if [ "$1" = "api" ] && [[ "$2" =~ ^repos/[^/]+/[^/]+/pulls\?state=open ]]; then
  printf '%s\n' "$@" > "$d/open-prs-rest-argv.txt"
  if [ "${GH_STUB_OPEN_PR_REST_FAIL:-0}" = "1" ]; then
    echo "gh-stub: could not connect to api.github.com" >&2
    exit 1
  fi
  # #9423 review (P3): the REST rescue's own `except ValueError` arm — the
  # strict-decoding refusal `_issue_view_rest` has carried since #3435 — was
  # UNPINNED: no seam wrote a bad byte to `open_prs_rest.json`, so DELETING the
  # arm left the suite green. The sibling path's note records why that matters
  # ("without this seam the malformed-REST arm is never executed, which is why a
  # crash on it survived a review cycle"); this is the same hole on the new
  # transport.
  if [ "${GH_STUB_OPEN_PR_REST_INVALID_UTF8:-0}" = "1" ]; then
    printf '\xff\xfe[]\n'
    exit 0
  fi
  f="$d/open_prs_rest.json"
  if [ ! -f "$f" ]; then echo "gh-stub: no fixture: $f" >&2; exit 1; fi
  cat "$f"
  exit 0
fi

case "$1 $2" in
  "pr list")
    # #3587: the `gh pr list` GraphQL path for CLOSED PRs resets on this host
    # ("read: connection reset by peer") while the REST endpoint works. The
    # tool must use REST for the closed surface; if it ever regresses to the
    # GraphQL path, fail loudly exactly as production does.
    if [ "${state:-open}" = "closed" ] && [ "${GH_STUB_CLOSED_PR_LIST_FAILS:-1}" != "0" ]; then
      echo "gh-stub: read tcp 127.0.0.1:1->20.26.156.210:443: read: connection reset by peer" >&2
      exit 1
    fi
    # #3435 review (P2): GitHub's PRIMARY GraphQL budget is ONE shared bucket, so
    # a genuine exhaustion kills `pr list` too. Without this seam the CLEAN test
    # models a state production cannot reach and overstates what the fallback
    # delivers (the OPEN-PR surface stays GraphQL-only by design).
    if [ "${state:-open}" = "open" ] && [ "${GH_STUB_PR_LIST_RATE_LIMITED:-0}" = "1" ]; then
      echo "GraphQL: API rate limit already exceeded for user ID 81560491." >&2
      exit 1
    fi
    # #9423 review round 10 (P3): a VERBOSE exhaustion whose SIGNATURE falls past
    # the point where the surface note truncates the gh error (`_one_line(...,
    # limit=...)` keeps the FIRST N chars). The remedy must still name the budget
    # — that is the whole #9402 defect — so this seam is what pins the cause as
    # DATA on the surface rather than a substring of a cut note.
    if [ "${state:-open}" = "open" ] && [ "${GH_STUB_PR_LIST_VERBOSE_EXHAUSTION:-0}" = "1" ]; then
      printf '%0.schatter-' $(seq 1 120) >&2
      echo "GraphQL: API rate limit already exceeded for user ID 81560491." >&2
      exit 1
    fi
    # #9423 review (P1): the SAME exhaustion behind a LONGER stderr. `gh` emits
    # transport chatter before the GraphQL error on a verbose/retrying run, and
    # the tool one-lines that stderr into its message — so a fixed display cut
    # can land BEFORE the discriminator and the classifier then answers "not an
    # exhaustion" about an exhaustion. ⛔ NOTE WHAT IS *NOT* THE MECHANISM: the
    # 200-character cut applies to the STDERR only, never to the assembled
    # message, so a long `--repo` slug alone cannot hide the signature (the
    # `test_a_long_stderr_...` docstring records that measurement). The seam
    # therefore pads the STDERR, which is the string the cut is applied to.
    if [ "${state:-open}" = "open" ] && [ "${GH_STUB_PR_LIST_LONG_STDERR:-0}" = "1" ]; then
      printf 'gh-stub: request trace: %s\n' "$(printf 'x%.0s' {1..240})" >&2
      echo "GraphQL: API rate limit already exceeded for user ID 81560491." >&2
      exit 1
    fi
    # #9423 review (P3): a NON-exhaustion failure of the SAME read. The REST
    # rescue is gated on the CAUSE (`_is_budget_exhaustion`), not on "the read
    # failed", so this seam is what makes that gate falsifiable: a rescue that
    # fired on every SurfaceError would answer this from REST and hide a genuine
    # "could not read" — the fail-open direction. Without a seam of this shape
    # the scoping test could only assert the happy path, which the pre-fix tool
    # passes too (it has no rescue at all).
    if [ "${state:-open}" = "open" ] && [ "${GH_STUB_PR_LIST_FAIL:-0}" = "1" ]; then
      echo "gh-stub: read tcp 127.0.0.1:1->20.26.156.210:443: read: connection reset by peer" >&2
      exit 1
    fi
    # The target repo is sent EXPLICITLY (#4027): record it so a test can
    # prove the selector reached gh rather than being inferred from the cwd.
    printf '%s\n' "$@" > "$d/pr-list-argv.txt"
    f="$d/${state:-open}_prs.json" ;;
  "api --paginate")
    # #8560: the landed surface's TARGETED, PAGINATED timeline query
    # (`api --paginate --slurp repos/<slug>/issues/<N>/timeline?...`). It writes
    # its own argv + call log: the closed-PR surface's "ONE bounded request"
    # assertion reads `api-calls.log`, and sharing it would count this call too.
    # `GH_STUB_TIMELINE_FAIL=1` is a SEPARATE failure seam from
    # `GH_STUB_API_FAIL_AFTER_OUTPUT`, which fails the ADVISORY closed-PR request
    # and must leave this BLOCKING surface readable — otherwise every
    # closed-PR-failure test would flip to INCOMPLETE.
    f="$d/timeline.json"
    printf '%s\n' "$@" > "$d/timeline-argv.txt"
    printf '%s\n' "$*" >> "$d/timeline-calls.log"
    if [ "${GH_STUB_TIMELINE_FAIL:-0}" = "1" ]; then
      echo "gh-stub: read tcp 127.0.0.1:1->20.26.156.210:443: read: connection reset by peer" >&2
      exit 1
    fi ;;
  "api -i")
    # #5251: the closed-PR surface is ONE bounded request made with `gh api -i`.
    # `-i` prefixes the body with the status line + headers + a blank line, and
    # the tool reads the total from the response's OWN `Link: rel="last"` header
    # (emitted only when GH_STUB_CLOSED_PR_TOTAL_PAGES is set to an integer) so a
    # partial sample stays observable. GH_STUB_API_NO_SEPARATOR=1 drops the blank
    # line to exercise the tool's refusal of an ambiguous header/body split.
    f="$d/closed_prs.json"
    printf '%s\n' "$@" > "$d/api-argv.txt"
    # ONE line per INVOCATION: proves the sample is a single bounded request
    # (a regressed `--paginate` would append once per page).
    printf '%s\n' "$*" >> "$d/api-calls.log"
    if [ ! -f "$f" ]; then echo "gh-stub: no fixture: $f" >&2; exit 1; fi
    echo 'HTTP/2.0 200 OK'
    echo 'content-type: application/json; charset=utf-8'
    if [ -n "${GH_STUB_CLOSED_PR_TOTAL_PAGES:-}" ]; then
      base="$3"
      printf 'link: <https://api.github.com/%s&page=1>; rel="first", <https://api.github.com/%s&page=%s>; rel="last"\n' "$base" "$base" "$GH_STUB_CLOSED_PR_TOTAL_PAGES"
      # GH_STUB_CLOSED_PR_FORCE_NEXT=1 emits `rel="next"` even when the last
      # page number says this is a single-page result. That is the only way to
      # construct the case where the two relations DISAGREE, which is the shape
      # a page reported as "the complete list" while proving a further page
      # exists (review cycle 9).
      if [ "$GH_STUB_CLOSED_PR_TOTAL_PAGES" -gt 1 ] 2>/dev/null \
         || [ "${GH_STUB_CLOSED_PR_FORCE_NEXT:-0}" = "1" ]; then
        printf 'link: <https://api.github.com/%s&page=2>; rel="next"\n' "$base"
      fi
    fi
    if [ "${GH_STUB_API_NO_SEPARATOR:-0}" = "1" ]; then
      cat "$f"
      exit 0
    fi
    echo
    cat "$f"
    if [ "${GH_STUB_API_FAIL_AFTER_OUTPUT:-0}" = "1" ]; then
      echo "gh-stub: read tcp 127.0.0.1:1->20.26.156.210:443: read: connection reset by peer" >&2
      exit 1
    fi
    exit 0 ;;
  "issue view")
    # A number ABSENT from the target repo is not "no in-flight work" (#4027).
    if [ "${GH_STUB_ISSUE_ABSENT:-0}" = "1" ]; then
      echo "GraphQL: Could not resolve to an issue or pull request with the number of $3. (repository.issue)" >&2
      exit 1
    fi
    # #3435: GraphQL budget exhausted while REST still answers. The exact text
    # the real CLI prints on this host, which is NOT "the issue could not be
    # read" — it is "this endpoint is out of budget".
    if [ "${GH_STUB_ISSUE_VIEW_RATE_LIMITED:-0}" = "1" ]; then
      echo "GraphQL: API rate limit already exceeded for user ID 81560491." >&2
      exit 1
    fi
    # ⛔ THE CANONICAL EXHAUST FORM, and the reason the gate matches three strings
  # rather than one (#3435 review, P2): `gh` renders a non-2xx as
  # `HTTP <code>: <status> (<url>)` — so a 429 with NO body prose arrives with
  # no "rate limit" substring at all, and a bare substring test under-triggers.
  # Unpinned, deleting the `"too many requests"` / `"http 429"` arms restores that
  # defect with a green suite.
  if [ "${GH_STUB_ISSUE_VIEW_HTTP_429:-0}" = "1" ]; then
    echo "gh-stub: HTTP 429: Too Many Requests (https://api.github.com/graphql)" >&2
    exit 1
  fi
  # Probe form (the cross-repo ambiguity check asks for `--json number` only).
    if [ "$json_args" = "number" ]; then
      if [ -f "$d/probe_unqueried.txt" ] && grep -qx "$repo" "$d/probe_unqueried.txt"; then
        echo "gh-stub: could not connect to api.github.com" >&2; exit 1
      fi
      if [ -f "$d/probe_holds.txt" ] && grep -qx "$repo" "$d/probe_holds.txt"; then
        echo "{\"number\": $3}"; exit 0
      fi
      echo "GraphQL: Could not resolve to an issue or pull request with the number of $3. (repository.issue)" >&2
      exit 1
    fi
    # #9423 review (P3): the SAME exhaustion behind a LONGER stderr, on the
    # ISSUE read. The open-PR rescue has its own long-stderr seam
    # (`GH_STUB_PR_LIST_LONG_STDERR`) because the failure is reported on TWO
    # branches — the rescue succeeding and the rescue failing — and the failing
    # branch builds the surface note from the (200-char-cut) display message. A
    # seam only on the PR read would leave the ISSUE rescue's failing branch
    # unpinned, so this mirror exists for it. Deliberately placed AFTER the
    # `--json number` probe branch: the cross-repo ambiguity probe is a
    # different question and must not start failing here.
    if [ "${GH_STUB_ISSUE_VIEW_LONG_STDERR:-0}" = "1" ]; then
      printf 'gh-stub: request trace: %s\n' "$(printf 'x%.0s' {1..240})" >&2
      echo "GraphQL: API rate limit already exceeded for user ID 81560491." >&2
      exit 1
    fi
    # ⛔ PAST THE NOTE'S TRUNCATION POINT (#9423 review round 12, P3). The
    # remedy classifies the surface NOTE, which embeds this stderr after
    # `_one_line(..., limit=_REFUSED_READ_NOTE_LIMIT)` — a cut that keeps the
    # FIRST N chars. The 240-char seam above sits well INSIDE the cut, so it
    # cannot tell a surface that records its cause as DATA from one that re-parses
    # the cut note. This one puts the signature past the cut, which is the only
    # shape that discriminates the two.
    if [ "${GH_STUB_ISSUE_VIEW_VERBOSE_EXHAUSTION:-0}" = "1" ]; then
      printf 'gh-stub: retry trace: %s\n' "$(printf 'y%.0s' {1..900})" >&2
      echo "GraphQL: API rate limit already exceeded for user ID 81560491." >&2
      exit 1
    fi
    # #9957: a PRE-FLIGHT THAT RESOLVES A PR NUMBER reads TWO different issues in
    # one run (the PR-as-issue it refused, then the work item it resolved to), so
    # the stub must be able to answer a NUMBER-SPECIFIC payload. `issue-<N>.json`
    # wins when present; `issue.json` stays the default, so every pre-existing
    # test — and every fixture that models one issue — is untouched.
    f="$d/issue-$3.json"; [ -f "$f" ] || f="$d/issue.json"
    printf '%s\n' "$@" > "$d/issue-argv.txt" ;;
  *) echo "gh-stub: unexpected argv: $*" >&2; exit 64 ;;
esac
if [ ! -f "$f" ]; then echo "gh-stub: no fixture: $f" >&2; exit 1; fi
cat "$f"
"""

# A Python stand-in for the fleet's `map-sessions.py` was written for a
# fleet-session surface (#1233) and then deliberately NOT shipped: its
# prescribed discipline cannot tell a session that holds an issue from one that
# has merely read the fleet board. See the module docstring of
# tools/collision_preflight.py.

GIT_STUB = r"""#!/usr/bin/env bash
set -u
if [ "${1:-}" = "for-each-ref" ]; then
  for a in "$@"; do
    if [ "$a" = "refs/heads" ]; then
      echo "git-stub: refusing refs/heads" >&2
      exit 1
    fi
  done
fi
exec "${REAL_GIT:?REAL_GIT unset}" "$@"
"""

# Fails ONLY the `origin/main` tip lookup, so the `--merged` walk still runs.
# That is the single combination under which the fail-closed guard in
# `_branch_terminal_state` is reachable: predicate 2 (which can only DOWNGRADE a
# hit) has its ancestor set but NOT the tip to judge it against.
GIT_STUB_NO_MAIN_TIP = r"""#!/usr/bin/env bash
set -u
if [ "${1:-}" = "rev-parse" ]; then
  for a in "$@"; do
    if [ "$a" = "origin/main" ]; then
      exit 1
    fi
  done
fi
exec "${REAL_GIT:?REAL_GIT unset}" "$@"
"""

# Fails ONLY the DISTANCE read the stale-holder test needs (`rev-list --count
# --left-right`). The terminal predicates use `rev-list --first-parent`, which still
# works, so this is the single combination where a stale-looking branch is judged with NO
# measurement of how far behind main it is — which must keep it BLOCKING.
GIT_STUB_NO_DISTANCE = r"""#!/usr/bin/env bash
set -u
if [ "${1:-}" = "rev-list" ]; then
  for a in "$@"; do
    if [ "$a" = "--count" ]; then
      echo "git-stub: refusing rev-list --count" >&2
      exit 1
    fi
  done
fi
exec "${REAL_GIT:?REAL_GIT unset}" "$@"
"""

# Fails ONLY `git log -1 --format=%ct`, the tip-commit DATE read. The distance and every
# terminal witness still work, so the branch is a candidate by divergence and unmeasurable
# by silence: an unread age must never become a downgrade.
GIT_STUB_NO_TIP_DATE = r"""#!/usr/bin/env bash
set -u
if [ "${1:-}" = "log" ]; then
  for a in "$@"; do
    case "$a" in
      --format=%ct) echo "git-stub: refusing the commit date" >&2; exit 1 ;;
    esac
  done
fi
exec "${REAL_GIT:?REAL_GIT unset}" "$@"
"""

# Fails ONLY `rev-list --first-parent`. This is the ONE combination in which
# `_first_parent_shas` returns None while `main_tip` (a separate `rev-parse`
# call) and `for-each-ref --merged` are both READABLE. `GIT_STUB_NO_MAIN_TIP`
# above is the opposite case, so neither stub covers the other's guard.
GIT_STUB_NO_FIRST_PARENT = r"""#!/usr/bin/env bash
set -u
if [ "${1:-}" = "rev-list" ]; then
  for a in "$@"; do
    if [ "$a" = "--first-parent" ]; then
      exit 1
    fi
  done
fi
exec "${REAL_GIT:?REAL_GIT unset}" "$@"
"""

SURFACE_ROWS = (
    "issue landed",
    "merged PRs naming an OPEN issue",
    "open PRs",
    "recently-closed PRs",
    "local branches",
    "remote branches",
    "local worktrees",
    "issue assignee/holder",
)

def _git(repo: Path, *args: str) -> None:
    # A temp repo has no committer identity, so any git commit through this
    # helper dies with exit 128 ("Author identity unknown") on a machine with
    # no global user.email. Set it in the ENVIRONMENT rather than by running
    # `git config user.email` per repo: the environment covers every temp repo
    # this module creates, including the ones built inside a test body, and it
    # cannot be forgotten at a new call site the way a per-repo config can.
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    subprocess.run(["git", *args], cwd=repo, check=True,
                   capture_output=True, text=True, env=env)


def _write_exec(path: Path, body: str) -> Path:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


#: The cause `load_registry` records for the real #9392 incident: the roster was
#: never written in that cell. Module-level so both test classes model the SAME
#: cause text the tool actually prints.
ABSENT_ROSTER = (
    "FileNotFoundError: [Errno 2] No such file or directory: "
    "'/tmp/cell-home/.pi/agent/state/lane-registry.tsv'"
)


def _fleet_board_state(*, issues=None, lanes=None, registry_measured=True,
                       registry_error="", github_measured=True,
                       reconciliation=True,
                       registry_rows=None, workspaces_count=None,
                       workspaces_measured=True,
                       generated_at=None, repo=SLUG,
                       liveness_unmeasured=False,
                       open_issues_truncated=False) -> dict:
    """A `fleet-state.json` shaped the way `collision_preflight._read_board`
    consumes it.

    `issues` maps an issue number to a list of `(lane_label, evidence)` pairs —
    the project of `fleet_state.attribute_claims` (branch slug / worktree path /
    the lane's own first user message). `lanes` is a list of lane specs:
    `{"label": str, "live": True|False|None, "pid_alive": bool, "pi_pid": int|None,
    "liveness_measured": bool, "terminal": str, "reason": str,
    "guess_refused": str, "bound": bool, "shared_with": [str],
    "absent_pid_key": bool}`. `live` is the lane's OWN `pid_alive` reading:
    `True`/`False` are measured, `None` models the REAL sessionless record
    (`pid_alive: False`, `pi_pid: None`, `bound: False`, `sid: None`,
    `liveness_measured: False`) — `build_state` always writes those keys.
    `absent_pid_key=True` models the wire-impossible shape of a session with NO
    pid key at all, which one test keeps on purpose. The
    `pid_alive`/`pi_pid`/`liveness_measured` overrides pin the other real shapes.

    `repo` is the stamp `_board_is_for_repo` compares against the queried slug;
    pass another slug to model a board built for a different repo.
    `liveness_unmeasured=True` adds the board's `liveness-unmeasured` orphan,
    which is how `build_state` publishes "the live-session store could not be
    read" — the board-level case in which every lane's `pid_alive` is False by
    default and must NOT be read as a dead owner.

    `reconciliation=False` omits the provenance block entirely, which is the
    shape a board from an older writer (or a truncated write) has.
    """
    state: dict = {
        "generated_at": generated_at if generated_at is not None
        else datetime.now(UTC).isoformat(),
        "repo": repo,
        "lanes": [],
        "orphans": [],
        "index": {"issues": {}},
    }
    if liveness_unmeasured:
        state["orphans"].append({
            "kind": "liveness-unmeasured",
            "reason": ("the live-session store could not be read — dead-owner "
                       "detection suppressed"),
        })
    if reconciliation:
        state["reconciliation"] = {
            "registry_measured": bool(registry_measured),
            # The cause TEXT `load_registry` records, so a test can model the two
            # shapes #9392 must tell apart (#9392). Passed through UNCHANGED: a
            # `str()` coercion here made a non-string cause inexpressible, so the
            # `_board_registry_error` guard that collapses it to "no cause
            # recorded" could be reverted with every test still green.
            "registry_error": "" if registry_error is None else registry_error,
            "github_measured": bool(github_measured),
            # #8175: was the OPEN-ISSUE window wide enough to prove a negative?
            "open_issues_truncated": bool(open_issues_truncated),
            "open_issues_read": 989,
            "open_issues_limit": 2000,
        }
        # The roster SIZE, only when a test asks for it: a board from an older
        # writer carries no counts, and `None` here models exactly that (#9392).
        # `workspaces_measured` travels with `workspaces_count` because the count
        # is only a measurement when that read SUCCEEDED — an unreachable cmux
        # publishes no workspaces, and `False` + a count is the fail-open shape
        # that must not print "0 live workspace(s)" (#9392 review P1).
        for key, value in (("registry_rows", registry_rows),
                           ("workspaces_count", workspaces_count),
                           ("workspaces_measured", workspaces_measured)):
            if value is not None:
                state["reconciliation"][key] = value
    for number, entries in (issues or {}).items():
        state["index"]["issues"][str(number)] = {
            "lanes": [label for label, _ in entries],
            "evidence": {label: evidence for label, evidence in entries},
        }
    for spec in lanes or ():
        live = spec.get("live")
        has_records = live is not None
        measured = spec.get("liveness_measured", has_records)
        session: dict = {}
        # ⛔ `live=None` IS THE REAL SESSIONLESS RECORD, not an absent key:
        # `build_state` ALWAYS writes `session.pid_alive` (the `bool(alive_pids)`
        # default, False) plus `pi_pid: None`, and `SessionResolution.to_dict()`
        # gives `bound: False` / `sid: None`. A lane WITH records (live not None)
        # resolves to `bound: True` and a sid — possibly after a dangling pane
        # binding was recorded, see `guess_refused`. The one shape with no pid
        # KEY at all is wire-impossible and is opted into explicitly.
        if not spec.get("absent_pid_key"):
            resolved = measured and has_records
            session = {
                "pi_pid": spec.get("pi_pid", 4242 if resolved else None),
                "pid_alive": spec.get("pid_alive", bool(live)),
                "sid": spec.get("sid", f"sid-{spec['label']}" if resolved else None),
                "bound": spec.get("bound", resolved),
            }
        if spec.get("guess_refused"):
            session["guess_refused"] = spec["guess_refused"]
        if spec.get("shared_with"):
            session["shared_with"] = list(spec["shared_with"])
        if "bound" in spec:
            session["bound"] = spec["bound"]
        terminal = spec.get("terminal") or ""
        state["lanes"].append({
            "identity": {"lane": spec["label"]},
            "terminal": ({"state": terminal, "reason": spec.get("reason", "")}
                         if terminal else {}),
            "liveness": {"liveness_measured": measured},
            "session": session,
        })
    return state


def _write_board(path: Path, state: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state))
    return path


_TOOL_MODULE = None


def _tool_module():
    """Import `tools/collision_preflight.py` (hyphenated name) once, so the
    escape stripper and the holder classification can be asserted directly
    without spawning the tool 40 times. Importing the module has ONE import-time
    side effect — it appends the repo root to `sys.path` and imports
    `fleet_state` for the claim gate's holder read (#8175) — which is why it is
    loaded once here rather than per test; `main()` itself is guarded."""
    global _TOOL_MODULE
    if _TOOL_MODULE is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "collision_preflight_under_test", TOOL)
        module = importlib.util.module_from_spec(spec)
        # Register BEFORE exec: dataclasses resolve annotations via
        # `sys.modules[cls.__module__]` while the module body runs.
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _TOOL_MODULE = module
    return _TOOL_MODULE


class CollisionPreflightTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="collision-preflight-")
        self.tmp = Path(self._tmp.name)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main", "--template=")
        _git(self.repo, "config", "user.email", "test@example.com")
        _git(self.repo, "config", "user.name", "Test")
        # The GitHub target is DERIVED OFFLINE from the git remote (#4027), so
        # every gh call can be sent an explicit `--repo owner/name` without a
        # network round-trip.
        _git(self.repo, "remote", "add", "origin",
             "https://github.com/test-owner/test-repo.git")
        (self.repo / "seed.txt").write_text("seed\n")
        _git(self.repo, "add", "seed.txt")
        _git(self.repo, "commit", "-qm", "seed")

        self.gh_dir = self.tmp / "ghstub"
        self.gh_dir.mkdir()
        self.gh = _write_exec(self.gh_dir / "gh", GH_STUB)
        (self.gh_dir / "user.txt").write_text("test-agent")
        # ⛔ HOME IS REDIRECTED. The claim gate reads the FLEET BOARD at
        # `$HOME/.pi/agent/state/fleet-state.json`, and a missing or aged board
        # is REBUILT — a real 3 s sample plus live `gh`/`cmux` calls. A test must
        # never inherit the operator's HOME (that would be slow and
        # non-hermetic), so `HOME` points inside the temp dir and a fresh,
        # answerable "no holders" board is written here. A test that wants a
        # holder writes its own with `fleet_board`.
        self.home = self.tmp / "home"
        self.fleet_board()
        # Where a REBUILD's shadowed `gh`/`cmux` stubs record that they were
        # called, so a test can prove the rebuild ran through THIS path and not
        # the network (see `failing_rebuild_path`).
        self.rebuild_stub_log = self.tmp / "rebuild-stub-calls.log"
        # Default: every GitHub surface is queryable and empty.
        self.gh_fixtures(open_prs=[], closed_prs=[], issue=self.issue_payload())
        # #8560: the landed surface's timeline is queryable by default (one empty
        # page) so every pre-existing test keeps its verdict. A test that wants
        # landed evidence or a timeline FAILURE opts in explicitly.
        self.timeline_fixture()
        self._timeline_calls = 0

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ── fixtures ────────────────────────────────────────────────────────────

    def issue_payload(self, title="florfenicol dosing audit", assignees=(),
                      state="OPEN", state_reason=None, number=ISSUE) -> dict:
        """The issue payload `gh issue view --json
        number,title,state,stateReason,assignees,url` returns.

        `state_reason` mirrors `gh issue view --json stateReason`: null while
        OPEN, `COMPLETED` / `NOT_PLANNED` once closed. Normalized in because the
        real CLI always returns the key when asked, so omitting it models a
        payload gh cannot produce. Tests that want the CLOSED-as-NOT_PLANNED
        wording pass it explicitly (#8560 review).

        `number` exists for #9957: a run that RESOLVES a PR number reads the
        work item's own payload as a SECOND issue, and a fixture that answered
        every number with one object would model a GitHub that does not exist.

        ⛔ NO COMMENT BODIES (#8175): the claim gate reads the HOLDER from the
        fleet board, and `gh issue view` no longer requests `comments` at all,
        so a fixture that carried them would model a payload nothing reads.
        """
        return {
            "number": number,
            "title": title,
            "state": state,
            "stateReason": state_reason,
            "url": f"https://example.invalid/issues/{number}",
            "assignees": [{"login": a} for a in assignees],
        }

    def gh_issue_for(self, number: int, **kwargs) -> dict:
        """Write a NUMBER-SPECIFIC `gh issue view <N>` payload (#9957).

        `gh_fixtures(issue=…)` writes `issue.json`, which answers EVERY number —
        fine for the single-issue runs that make up almost every test, wrong for
        a run that reads both the PR-as-issue it was asked about and the work
        item it resolved to. The stub prefers `issue-<N>.json` when it exists.
        """
        payload = self.issue_payload(number=number, **kwargs)
        (self.gh_dir / f"issue-{number}.json").write_text(json.dumps(payload))
        return payload

    def gh_fixtures(self, open_prs=None, closed_prs=None, issue=None) -> None:
        if open_prs is not None:
            # AND THE FIELD IS NORMALIZED IN, HERE.
            #
            # `gh pr list --json closingIssuesReferences` ALWAYS returns the field
            # — as a list, often empty — so a fixture that omits it models a
            # payload the real CLI cannot produce. The tool REQUIRES it on the
            # blocking open-PR surface (absence is INCOMPLETE, because reading a
            # missing field as "closes nothing" would DROP a blocking signal).
            #
            # Normalizing in ONE place is deliberate: ~20 call sites construct
            # open-PR fixtures, and updating each of them is how a deletion
            # misses one (the lesson from #5251's review loop). A test that
            # wants the missing-field path DELETES the key explicitly — see
            # `test_missing_closing_reference_field_is_incomplete_not_clean`.
            normalized = []
            for pr in open_prs:
                pr = dict(pr)
                pr.setdefault("closingIssuesReferences", [])
                # See the closed-PR normalizer below for why these two are
                # normalized in rather than left to each call site.
                pr.setdefault("state", "open")
                pr.setdefault("body", "")
                pr.setdefault("mergedAt", None)
                normalized.append(pr)
            (self.gh_dir / "open_prs.json").write_text(json.dumps(normalized))
            # #9402: the SAME list projected into REST's shape, so the REST rescue
            # has a fixture that cannot drift from the GraphQL one. Derived here,
            # in the ONE place the open-PR fixtures are normalised, because ~20 call
            # sites construct them and updating each is how a deletion misses one.
            # The projection is deliberately honest about what REST does NOT carry:
            # no `closingIssuesReferences` key at all (see the stub's note).
            rest = []
            for pr in normalized:
                rest.append({
                    "number": pr.get("number"),
                    "title": pr.get("title") or "",
                    # ⛔ REAL REST SENDS `null` FOR AN EMPTY DESCRIPTION, NOT
                    # `""` (#9423 review round 7, P3). The projection used to emit
                    # `""` for every description-less PR, so the wire's null-body
                    # ACCEPTANCE branch in `_rest_pr_wire_reason` — the one that
                    # keeps the rescue working on any repo with a single
                    # description-less open PR, i.e. the COMMON production shape —
                    # was never exercised: a regression dropping `is not None`
                    # would refuse the whole enumeration and the suite would stay
                    # green. The projection now models the wire it claims to model.
                    "body": pr.get("body") or None,
                    "state": str(pr.get("state") or "open").lower(),
                    "html_url": pr.get("url") or "",
                    "merged_at": pr.get("mergedAt"),
                    "head": {"ref": pr.get("headRefName") or ""},
                })
            (self.gh_dir / "open_prs_rest.json").write_text(json.dumps(rest))
        if closed_prs is not None:
            # `state` and `body` are normalized in for the same reason as
            # `closingIssuesReferences` above: `gh pr list --json` and the REST
            # `/pulls` projection in `_closed_pr_sample` BOTH always send them, so
            # a fixture that omits one models a payload neither can produce.
            #
            # A test that wants a payload the tool must REFUSE deletes the key
            # explicitly (see the missing-`state` / missing-`body` tests); a test
            # that wants a NON-TERMINAL payload sets a state the tool does not
            # read as terminal rather than deleting the key — `gh pr list --json
            # state` returns the GRAPHQL casing ("OPEN"), which `_pr_terminal_state`
            # does not match, so that is a real shape as well as a non-terminal
            # one.
            normalized = []
            for pr in closed_prs:
                pr = dict(pr)
                pr.setdefault("state", "closed")
                pr.setdefault("body", "")
                # `mergedAt` is the PROJECTED key (`mergedAt: .merged_at`), and
                # the harvest that feeds the squash-merge demotion is gated on
                # it, so absence is information loss rather than a shape.
                pr.setdefault("mergedAt", None)
                # `headSha` is the PROJECTED key (`headSha: (.head.sha // "")`),
                # and it is the DATA the `mergedAt` gate is gating — the harvest
                # reads it to decide whether a branch tip was landed by a merge
                # GitHub did not keep as an ancestor. It is normalized in for the
                # same reason as `mergedAt`: the projection always emits it, so
                # omitting it models a payload the wire cannot produce. A test
                # that wants the refusal DELETES the key.
                pr.setdefault("headSha", "")
                normalized.append(pr)
            (self.gh_dir / "closed_prs.json").write_text(json.dumps(normalized))
        if issue is not None:
            # AND THE KEY IS NORMALIZED IN, HERE. `gh issue view --json
            # ...,assignees,url` ALWAYS returns `assignees` (as an array, often
            # empty), so a fixture omitting it models a payload the real CLI
            # cannot produce — the same rule as `closingIssuesReferences` above,
            # for the same reason: a missing key is a malformed payload, and
            # reading it as "no assignees" would DROP one. A test that wants the
            # missing-key path DELETES the key explicitly. (`comments` is not in
            # the contract at all any more — the claim gate does not fetch it,
            # #8175.)
            issue = dict(issue)
            issue.setdefault("assignees", [])
            (self.gh_dir / "issue.json").write_text(json.dumps(issue))

    def clear_fixtures(self) -> None:
        # `open_prs_rest.json` is written as a DERIVED projection by
        # `gh_fixtures`, so a helper that clears the open-PR fixture must clear
        # its REST twin too — otherwise a test that clears fixtures and then
        # drives the REST rescue reads a previous test's PR list.
        for name in ("open_prs.json", "closed_prs.json", "open_prs_rest.json",
                     "issue.json", "timeline.json"):
            (self.gh_dir / name).unlink(missing_ok=True)
        # #9957: the number-specific issue payloads (``issue-<N>.json``) are a
        # SECOND fixture family, so a clear must not leave one behind to answer a
        # later test's resolved read.
        for stale in self.gh_dir.glob("issue-*.json"):
            stale.unlink(missing_ok=True)

    def timeline_fixture(self, events=None, pages=None) -> None:
        """Write the landed surface's timeline fixture (#8560).

        `events` is ONE page (the common case); `pages` is the raw slurped
        array-of-pages, for multi-page and malformed cases. Defaults to one
        empty page, which is what `gh api --paginate --slurp` returns for an
        issue with no events.
        """
        if pages is None:
            pages = [list(events or [])]
        (self.gh_dir / "timeline.json").write_text(json.dumps(pages))

    @staticmethod
    def merged_cross_reference(number: int, title: str,
                               merged_at: str = "2026-10-05T09:19:18Z") -> dict:
        """A real `cross-referenced` timeline event whose source PR MERGED.

        Shape taken from the live REST payload
        (`repos/<o>/<r>/issues/7174/timeline`): the merged marker lives at
        `source.issue.pull_request.merged_at`, and the title is
        `source.issue.title`.
        """
        return {
            "event": "cross-referenced",
            "source": {
                "issue": {
                    "number": number,
                    "title": title,
                    "state": "closed",
                    "pull_request": {
                        "url": f"https://api.github.invalid/repos/o/r/pulls/{number}",
                        "html_url": f"https://github.invalid/o/r/pull/{number}",
                        "merged_at": merged_at,
                    },
                }
            },
        }

    def add_worktree(self, name: str, branch: str | None = None) -> Path:
        path = self.tmp / "wt" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if branch:
            subprocess.run(["git", "worktree", "add", "-q", "--no-checkout", "-b", branch, str(path), "HEAD"],
                           cwd=self.repo, check=True, capture_output=True, text=True)
        else:
            subprocess.run(["git", "worktree", "add", "-q", "--no-checkout", "--detach", str(path), "HEAD"],
                           cwd=self.repo, check=True, capture_output=True, text=True)
        return path

    def fleet_board(self, **kwargs) -> Path:
        """Write the claim gate's board to `$HOME/.pi/agent/state/fleet-state.json`.

        See `_fleet_board_state` for the shape. The default (`setUp`'s call) is a
        FRESH, answerable board that attributes nothing, which is what keeps
        every pre-existing test hermetic: an aged or unanswerable board would
        make the tool REBUILD (a 3 s sample plus live `gh`/`cmux`).
        """
        return _write_board(
            self.home / ".pi" / "agent" / "state" / "fleet-state.json",
            _fleet_board_state(**kwargs),
        )

    def failing_rebuild_path(self) -> str:
        """A `PATH` on which a REBUILD's own reads fail locally (review P2).

        ⛔ A REBUILD RUNS IN THE TOOL SUBPROCESS (`_read_board` →
        `fleet_state.build_state`), so it cannot be mocked from here, and it
        deliberately uses BARE `gh`/`cmux` from `PATH` rather than this tool's
        `--gh`. An unanswerable board therefore reaches the network: bare `gh
        pr list` / `gh issue list` against a nonexistent repo, plus a bare `cmux
        list-workspaces`. On an authenticated box that is two real GraphQL calls;
        on a box where cmux IS reachable the rebuild reads the OPERATOR's real
        workspace list. Both are the non-hermetic path this harness exists to
        avoid.

        So the two binaries are shadowed with stubs that FAIL. `_run` degrades a
        non-zero exit to "", i.e. exactly the production fail-open shape, and the
        cause under test — the REAL `FileNotFoundError` from `load_registry`,
        which runs BEFORE either — is untouched.

        The stubs RECORD that they were called (`self.rebuild_stub_log`), so a
        test can prove the rebuild really ran and really went through THIS path
        — a test that stopped rebuilding would otherwise still pass.
        """
        stub_dir = self.tmp / "rebuild-stubs"
        stub_dir.mkdir(exist_ok=True)
        log = self.rebuild_stub_log
        for name in ("gh", "cmux"):
            _write_exec(stub_dir / name,
                        "#!/usr/bin/env bash\n"
                        f'printf \'%s\\n\' "$0 $*" >> {log}\n'
                        f'echo "{name}-stub: rebuilds never leave the test" >&2\n'
                        "exit 127\n")
        return f"{stub_dir}:{os.environ.get('PATH', '')}"

    # ── runner ──────────────────────────────────────────────────────────────

    def run_tool(self, issue: int = ISSUE,
                 self_branches: tuple[str, ...] = (),
                 self_worktrees: tuple[str, ...] = (),
                 git_bin: Path | None = None, env_extra: dict | None = None,
                 extra_args: list[str] | None = None,
                 repo_arg: object = "__default__", cwd: Path | None = None,
                 gh_bin: Path | None = None):
        env = dict(os.environ)
        # ⛔ THE CACHE SEAMS ARE NEUTRALISED (review P2). They are STATEFUL: an
        # exported `COLLISION_PREFLIGHT_BOARD_CACHE` would let one run's rebuild
        # answer a later test's pre-flight through a cache entry on the operator's
        # own disk, which makes the suite order-dependent — the same class
        # `tests/test_who_is_on.py` filters with its "no `COLLISION_PREFLIGHT_*`
        # leak" env. A test that WANTS the cache sets it via `env_extra`, which is
        # applied after this filter.
        env = {k: v for k, v in env.items()
               if not k.startswith("COLLISION_PREFLIGHT_")}
        env["GH_STUB_DIR"] = str(self.gh_dir)
        # ⛔ HOME IS REDIRECTED. `collision_preflight` reads the FLEET BOARD at
        # `$HOME/.pi/agent/state/fleet-state.json`, and a missing or aged board
        # is REBUILT with live `gh`/`cmux`. `setUp` writes a fresh, answerable
        # "no holders" board inside `self.home`; a test that wants a holder
        # writes its own with `fleet_board`.
        env["HOME"] = str(self.home)
        if env_extra:
            env.update({k: str(v) for k, v in env_extra.items()})
        cmd = [PYTHON, str(TOOL), str(issue)]
        if repo_arg == "__default__":
            cmd += ["--repo", str(self.repo)]
        elif repo_arg is not None:
            cmd += ["--repo", str(repo_arg)]
        cmd += ["--gh", str(gh_bin or self.gh)]
        # Self-identity is DECLARED, never inferred (#3504 classes 1/4): the
        # caller knows its own refs, and "is this mine?" is not a property of
        # any string. Every flag is repeatable.
        for ref in self_branches:
            cmd += ["--self-branch", ref]
        for wt in self_worktrees:
            cmd += ["--self-worktree", wt]
        if git_bin:
            cmd += ["--git", str(git_bin)]
        if extra_args:
            cmd += [str(a) for a in extra_args]
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env,
                              cwd=str(cwd) if cwd else None, timeout=90)
        return proc.returncode, proc.stdout + proc.stderr

    def assert_all_surface_rows(self, out: str) -> None:
        for row in SURFACE_ROWS:
            self.assertIn(row, out, f"surface row missing from output: {row}")

    # ── clean ───────────────────────────────────────────────────────────────

    def test_clean_no_hits_exits_zero_and_says_clean(self):
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertIn("no in-flight work found for #3061", out)
        self.assertNotIn("VERDICT: COLLISION", out)
        # #9957: an ISSUE number is not resolved, and must not pay the resolution
        # path or print its header. The issue arm is today's behaviour, exactly.
        self.assertNotIn("a PULL REQUEST number was given", out)
        self.assertNotIn("RESOLUTION:", out)
        # `VERDICT:`-scoped: the `claim gate:` rules line legitimately names the
        # INCOMPLETE outcome (and its fail-closed causes), so a bare substring
        # check would read the RULES as a verdict.
        self.assertNotIn("VERDICT: INCOMPLETE", out)
        self.assert_all_surface_rows(out)
        self.assertIn("worktree(s) enumerated (untruncated)", out)

    def test_an_unprovisioned_roster_end_to_end(self):
        """The reported symptom, on the real tool, end to end (#9392).

        The board is written unanswerable and NO roster exists under the
        redirected HOME, so the tool rebuilds through the real
        `fleet_state.build_state`, meets the real `FileNotFoundError`, and must
        end with the PROVISIONING remedy — not the rebuild that just failed.

        ⛔ THE REBUILD IS KEPT LOCAL (review P2): its bare `gh`/`cmux` reads are
        shadowed by failing stubs, so the FILE read under test is still the real
        one while no GraphQL call and no operator cmux read can happen.
        """
        self.fleet_board(registry_measured=False,
                         registry_error=ABSENT_ROSTER)
        rc, out = self.run_tool(
            env_extra={"PATH": self.failing_rebuild_path()})
        self.assertEqual(rc, 2, out)
        verdict = self._verdict_line(out)
        self.assertIn("LANE ROSTER is UNKNOWN", verdict)
        self.assertIn("lane-registry.tsv", verdict)
        self.assertIn("NEVER PROVISIONED", verdict)
        self.assertIn("CANNOT create it", verdict)
        self.assertIn("PROVISIONING", verdict)
        # Neither dead end is offered for THIS surface's cause…
        self.assertNotIn("fix gh auth/network", verdict)
        self.assertNotIn("rebuild the fleet board", verdict.lower())
        # …and the surface row above the verdict gives the SAME answer (both
        # take it from `fleet_state.registry_remedy`), so a lane reading either
        # one is not misled — the loop this issue files.
        rows = [ln for ln in out.splitlines()
                if ln.startswith("  [issue assignee/holder]")]
        self.assertEqual(len(rows), 1, out)
        self.assertIn("lane-registry.tsv", rows[0])
        self.assertIn("PROVISIONING", rows[0])
        # ⛔ AND THE REBUILD REALLY RAN — through the LOCAL stubs (review P2).
        # Without this a later change that stopped rebuilding would leave the
        # test green while the reason it exists (the tool meets the REAL
        # `FileNotFoundError` from `load_registry`) silently disappeared.
        log = self.rebuild_stub_log.read_text()
        self.assertIn("/gh ", log)
        self.assertIn("/cmux ", log)

    # ── one stubbed/real hit per hit-capable surface ────────────────────────

    def test_open_pr_hit_names_surface(self):
        self.gh_fixtures(open_prs=[{
            "number": 9999, "title": "fix: guard retrieval (#3061)",
            "body": "closes it", "headRefName": "fix/guard",
        }])
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("[open PRs]", out)
        self.assertIn("issue-number (3061) in title", out)

    def test_terminal_pr_hits_are_reported_but_non_blocking(self):
        # A TERMINAL PR cannot be in flight (#4886, #5112, #4533). Before this,
        # a merged docs-half PR whose title necessarily names its issue blocked
        # that issue forever — having shipped part of the work was what stopped
        # the rest from being dispatched. The hit is still REPORTED (it is
        # evidence about the past) but at `weak` strength, which the verdict
        # ignores. Both terminal shapes are covered, and `mergedAt` is asserted
        # separately because GitHub's REST `/pulls` reports `state: "closed"`
        # for merged AND unmerged PRs alike — a rule keyed on
        # `state == "merged"` would silently never fire (the #5052 F10 trap).
        # ⛔ The third shape used to omit `state`, which the normalizer now
        # supplies — and must SUPPLY, because REST `/pulls` always sends it. This
        # is the pair the comment above is about: `state: "closed"` is true of
        # merged and unmerged PRs alike, so `mergedAt` is what separates them.
        # Both read as terminal here, so this pins that the `mergedAt` arm is
        # present and does not disturb a terminal read — not that it is what
        # decided the verdict.
        for terminal in ({"state": "closed"},
                         {"state": "CLOSED", "mergedAt": "2026-09-23T03:45:47Z"},
                         {"state": "closed", "mergedAt": "2026-09-23T03:45:47Z"}):
            with self.subTest(terminal=terminal):
                self.gh_fixtures(closed_prs=[{
                    "number": 4356,
                    "title": "fix(dashboard): the rotate mint's plaintext (#4356)",
                    "body": "Closes #3061.",
                    "headRefName": "fix/3061-fts-determinism",
                    **terminal,
                }])
                rc, out = self.run_tool()
                self.assertEqual(rc, 0, f"terminal={terminal!r}\n{out}")
                self.assertIn("VERDICT: CLEAN", out)
                self.assertNotIn("do NOT dispatch", out)
                self.assertIn("immutable history, not in-flight work", out)
                self.assertIn("WEAK SIGNALS", out)

    def test_closed_pr_body_prose_mention_is_not_a_collision(self):
        # LIVE-BUG FIXTURES. These are verbatim shapes from a real run:
        #   * closed PR #2926's body says "(restored in #2745)"
        #   * closed PR #2754's body says "Triaged and filed as #2751"
        # A closed PR cannot be in-flight and cross-reference prose is not
        # work. Matching the bare number used to fabricate a strong COLLISION
        # ("do NOT dispatch") for #2745 and #2751. Both must now be weak
        # signals only -> CLEAN, exit 0.
        self.gh_fixtures(closed_prs=[
            {"number": 2926,
             "title": "chore(battery): remove the unreachable class-sentinel pre-flight branch (#2746)",
             "body": "`test_real_preflight_refuses_unpinned_or_fixed_sentinel` "
                     "(restored in #2745): the arms.yaml pin must win.",
             "headRefName": "chore/2746-dead-sentinel"},
            {"number": 2754,
             "title": "test(e2e): migrate dashboard specs to local-preview document loads",
             "body": "Triaged and filed as #2751 — **not introduced by this PR**.",
             "headRefName": "fix/2744-local-preview-migration"},
        ])
        rc, out = self.run_tool(issue=2745)
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertNotIn("VERDICT: COLLISION", out)
        self.assertNotIn("do NOT dispatch", out)
        self.assertIn("[recently-closed PRs]", out)
        self.assertIn("prose mention of #2745", out)
        self.assertIn("WEAK SIGNALS", out)
        self.assertIn("non-blocking", out)
        # The POSITIVE arm: the prose bucket is named from `Hit.kind`
        # (`"prose-body"`), not from a substring of the detail.
        self.assertIn(_tool_module().WEAK_KIND_PROSE_ONLY, out)
        self.assertNotIn(_tool_module().WEAK_HIT_KINDS, out)

        rc, out = self.run_tool(issue=2751)
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertNotIn("do NOT dispatch", out)
        self.assertIn("prose mention of #2751", out)

    def test_closed_pr_own_number_is_weak_not_blocking(self):
        # Closed PR #3061 (the issue is itself a closed PR). Its own number and
        # any prose in its body are not separate in-flight work.
        self.gh_fixtures(closed_prs=[{
            "number": 3061, "title": "fix(battery): #2712 restore the pin test",
            "body": "restored in #3061", "headRefName": "fix/2712-pin-preflight-test",
        }])
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertNotIn("do NOT dispatch", out)
        self.assertIn("PR number == issue (3061)", out)

    def test_advisory_surface_cannot_refuse_a_number_that_is_the_issue(self):
        """An ADVISORY surface must never be able to force exit 2 (#7009).

        The advisory closed-PR sample's elements are whatever the payload
        returned, and `_pr_terminal_state` accepts a NON-TERMINAL `state` — so an
        UNSCOPED number==issue refusal raised `NotAWorkItem` from the ADVISORY
        scan too: a surface with no authority to block stopped a dispatch with
        exit 2, stderr only and no report at all.

        `test_closed_pr_own_number_is_weak_not_blocking` structurally could not
        catch it. It does put a number==issue PR on the advisory surface, but its
        fixture omits `state`, which `gh_fixtures` normalizes to "closed" — i.e.
        TERMINAL — so the guard's `terminal is None` clause was False and the
        number clause never decided. This test is that same shape with the state
        made non-terminal: the one value that reaches the guard, and therefore the
        only shape that pins the scoping.

        Measured 2026-10-07: it fails against the PRE-FIX tool (`origin/main`, exit
        2, "is an OPEN PULL REQUEST") and passes here. It also passes against the
        intermediate tool that scoped on `use_closing_field` instead of
        `surface.authority` — those two are behaviourally identical at the
        production call sites, so this test pins that SOME scope exists.
        """
        self.gh_fixtures(closed_prs=[{
            "number": 3061, "title": "fix(battery): #2712 restore the pin test",
            "body": "restored in #3061", "state": "OPEN",
            "headRefName": "fix/2712-pin-preflight-test",
        }])
        rc, out = self.run_tool()
        # The hit is still REPORTED — an advisory surface reports, it just cannot
        # block — and the verdict stays CLEAN at exit 0.
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertIn("PR number == issue (3061)", out)
        self.assertNotIn("do NOT dispatch", out)
        # The pre-fix symptom, pinned by its exact text: the refusal must not be
        # reachable from the CLOSED sample.
        self.assertNotIn("is an OPEN PULL REQUEST", out)

    def test_blocking_surface_without_the_closing_field_reports_it_unread(self):
        """A BLOCKING surface that never requested the closing field must not
        claim the PR "names no closing issue".

        The refusal guard keys on `surface.authority` (may this surface refuse?)
        while the closing-reference read keys on `use_closing_field` (did the
        CALLER request the field?). Those are different questions, and this is the
        shape where they diverge: authority BLOCKING with the field not requested.
        `linked` stays empty but `unreadable` is True, so the message says the
        field could not be read instead of asserting contents the tool never
        looked at.

        ⛔ THE DIVERGENCE IS LIVE since #9402, not hypothetical: the BLOCKING
        open-PR call site passes `use_closing_field=not via_rest`, so a list
        retrieved by the REST rescue (no `closingIssuesReferences` on the wire) is
        scanned with the field NOT requested — and a pre-flight on an OPEN PR's
        own number reaches this arm for real. This test drives the arm DIRECTLY
        (it must not depend on a rate-limit seam to be exercised) and
        `test_open_pr_number_refusal_survives_a_rest_rescued_list` pins the live
        end-to-end route. The arm is still worth a unit test on its own: a branch
        already shipped in this PR unpinned (the `use_closing_field` scoping at
        `48ceaca5a`), and mutation confirmed a hard `raise` substituted for this
        arm left the whole suite green before this test existed.
        """
        cp = _tool_module()
        surface = cp.Surface("open PRs", authority=cp.AUTHORITY_BLOCKING)
        pr = {
            "number": 3061, "state": "open", "title": "fix(scanner): something",
            "headRefName": "fix/3061-something", "body": "",
        }
        with self.assertRaises(cp.NotAWorkItem) as caught:
            cp.scan_pr_surface(surface, [pr], 3061, cp.Identity(),
                               use_closing_field=False)
        message = str(caught.exception)
        # The refusal still happens — the arm changes only what it CLAIMS.
        self.assertIn("is an OPEN PULL REQUEST", message)
        self.assertIn("closing-reference field could not be read", message)
        # The present-and-empty wording belongs to a field that was READ.
        self.assertNotIn("It names no closing issue", message)

    def test_open_pr_own_number_REFUSES_instead_of_cleaning(self):
        """An OPEN PR's number is not a work item — an UNRESOLVABLE one must refuse (#7009).

        The test above pins `CLEAN` for a TERMINAL PR, and both answers are
        correct: a merged/closed PR is immutable history, while an OPEN PR names
        a live work item whose ownership its own number says nothing about.

        Measured 2026-10-06: with the self-match filed as a `weak` hit,
        `collision_preflight.py <open-PR-number>` returned `CLEAN (exit 0)` for a
        PR whose issue was held on FOUR surfaces at once. Exit 0 is this
        protocol's instruction to DISPATCH, so the fail-closed tool was
        authorising a dispatch onto held work — the failure the whole pre-flight
        exists to prevent (#3061). Note the polarity history: #7009's original
        symptom was the same input read as COLLISION (fail-CLOSED, lost
        throughput, nothing duplicated); once the self-match was downgraded to
        `weak` it became fail-OPEN.

        Exit 2 is pinned deliberately rather than 1: it is the protocol's own
        "could not be completed — do not start on a guess" verdict, so the
        refusal lands in a channel lanes already know how to obey.

        ⛔ THE HEAD REF IS NUMBER-FREE ON PURPOSE (#9957). This fixture used a
        `fix/2712-…` branch, and a branch naming an issue is now one of the three
        signals the gate RESOLVES a PR number with — so that shape no longer
        refuses at all; it is gated as #2712 and exits 1. The resolvable shape is
        pinned end to end by
        `test_a_pull_request_number_is_gated_as_the_work_item_it_serves`; THIS
        test keeps the residual property, which is the one that must never rot:
        when no arm yields a work item, the answer is a refusal, NEVER clean.
        """
        self.gh_fixtures(open_prs=[{
            "number": 3061, "title": "fix(battery): restore the pin test",
            "body": "no closing reference in this body", "state": "open",
            "headRefName": "chore/record-reads",
        }])
        rc, out = self.run_tool()
        self.assertEqual(rc, 2, out)
        self.assertIn("is an OPEN PULL REQUEST, not a work item", out)
        # The REMEDY text is part of the contract and nothing pinned it: mutation
        # showed restoring the old hardcoded "Re-run against the issue number
        # (#7009)." left every refusal test green.
        self.assertIn("Re-run with the issue number", out)
        self.assertNotIn("#7009", out)
        # The refusal is delivered on STDERR WITH NO REPORT: `main()` prints it
        # and returns before `format_report` runs. Pin the WHOLE guarantee, not
        # just "not CLEAN": the module docstring and AGENTS.md both promise a
        # caller that keys its remedy on `VERDICT: INCOMPLETE` that this cause
        # emits NO `VERDICT` line at all, and `assertNotIn("VERDICT: CLEAN")`
        # alone would pass a regression that ended the refusal in
        # `VERDICT: INCOMPLETE` — still exit 2, but now mis-remedied as a
        # broken surface ("fix gh auth/network") instead of "re-run with the
        # issue number". The two causes must stay distinguishable.
        self.assertNotIn("VERDICT", out)
        # The refusal names the number it refused. This does NOT distinguish a
        # targeted refusal from a blanket one — the number is in the message by
        # construction, as is "is an OPEN PULL REQUEST". What it does catch is a
        # regression that drops the number from the message.
        self.assertIn("#3061", out)
        # ⛔ #9957: THE REFUSAL MUST NAME WHAT IT COULD NOT RESOLVE. "could not
        # resolve" with no evidence is the thing this issue files — the gate held
        # the datum and did not say it. All three arms are named, so a reader can
        # see which signal was missing and supply it (the branch or the title).
        self.assertIn("could not resolve #3061 to a work item", out)
        self.assertIn("closing references name nothing", out)
        self.assertIn("names no issue as `(#N)`", out)
        self.assertIn("names no issue as `<type>/<n>-<slug>`", out)
        self.assertIn("NEVER clean", out)

    def test_open_pr_own_number_REFUSES_even_when_its_branch_is_the_callers(self):
        """The refusal must not be suppressible by the caller's OWN branch (#7009).

        Regression for the fail-open found in review. The self/ownership check
        ran FIRST and `continue`d, so when the open PR's head branch was also one
        of `--self-branch` the self-arm matched, filed a `weak` hit, and the run
        fell through to `VERDICT: CLEAN` (exit 0) — a refusal the caller's own
        branch could suppress.

        That is not an exotic input: `main()` auto-declares the current branch
        for the documented `--repo .` invocation, so the case that broke was the
        PR belonging to the lane RUNNING the gate — exactly the #7477 shape that
        motivated the change. Measured before this fix, both via `--self-branch
        <PR head>` and via auto-detect: RC=0, `CLEAN`, refusal absent.

        Declaring the branch is how the tool models auto-detect here: both paths
        reach the same `identity.owns_branch` predicate, so pinning the declared
        form pins the mechanism (an auto-detect test would only re-assert that
        `main()` populates the same set).

        The refusal is an INPUT-VALIDITY test, not a match test, so the
        "self before match" ordering (#4567) does not apply to it.

        ⛔ THE HEAD REF IS NUMBER-FREE, for the reason the sibling test records
        (#9957): a `fix/2712-…` branch is now a RESOLUTION signal, so the shape
        resolves instead of refusing. What this test pins is unchanged — that a
        caller's own branch cannot suppress the fail-closed answer — and the
        resolution runs BEFORE identity is consulted at all, so a resolvable
        number cannot be suppressed either.
        """
        self.gh_fixtures(open_prs=[{
            "number": 3061, "title": "fix(battery): restore the pin test",
            "body": "no closing reference in this body", "state": "open",
            "headRefName": "chore/record-reads",
        }])
        rc, out = self.run_tool(
            self_branches=("chore/record-reads",))
        self.assertEqual(rc, 2, out)
        self.assertIn("is an OPEN PULL REQUEST, not a work item", out)
        # No `VERDICT` line at all — see the sibling test for why "not CLEAN"
        # is too weak a pin (it would let `VERDICT: INCOMPLETE` through).
        self.assertNotIn("VERDICT", out)
        # Names the object it refused — see the sibling test for what this
        # assertion does and does not prove.
        self.assertIn("#3061", out)

    def test_open_pr_own_number_with_UNREADABLE_closing_field_still_refuses(self):
        """The refusal must survive a malformed closing-reference field (#7009).

        Regression for a guard that could not fire in CI. `_closing_ref_numbers`
        raises `SurfaceError` on an absent/malformed `closingIssuesReferences`, and
        it is evaluated to DECORATE the refusal message — so without the guard the
        open-PR handler absorbs that error and the run reports a MISLABELLED broken
        surface (`closing-reference-source-unavailable … fix gh auth/network`)
        instead of the refusal. That remedy is unrelated to the problem and no
        amount of retrying resolves it.

        No other test reaches this path: the only open-PR fixture whose number ==
        the issue carries the field, and the existing deleted-field test uses a
        different number, so block 0 never runs. Reaching it at all requires
        DELETING the key after `gh_fixtures`, which normalizes it in — a draft of
        this test that omitted it was VACUOUS: the field was present-and-empty,
        the guard never ran, and the test stayed green against a guard-removed
        copy (measured).

        It also pins the SECOND fix here: the message must not assert "it names no
        closing issue" for a field that was merely UNREADABLE. A wrong reason for a
        right decision is the defect class this whole change is about.

        ⛔ THE HEAD REF IS NUMBER-FREE (#9957) — see the siblings — so the run
        still ends in the refusal. The resolution message repeats the unread
        cause in its own words ("the computed field could not be read"), which is
        why the assertion below matches both sentences.
        """
        self.gh_fixtures(open_prs=[{
            "number": 3061, "title": "fix(battery): restore the pin test",
            "body": "no closing reference in this body", "state": "open",
            "headRefName": "chore/record-reads",
        }])
        # The harness normalizes `closingIssuesReferences` IN (setdefault, ~20
        # call sites) so no call site can forget it, which means the key must be
        # DELETED explicitly to reach the guard at all — exactly as
        # `test_missing_closing_reference_field_is_incomplete_not_clean` does.
        # Without this the field is present-and-empty, `_closing_ref_numbers`
        # never raises, and the guard can be deleted with the test still green.
        prs = json.loads((self.gh_dir / "open_prs.json").read_text())
        del prs[0]["closingIssuesReferences"]
        (self.gh_dir / "open_prs.json").write_text(json.dumps(prs))

        rc, out = self.run_tool()
        self.assertEqual(rc, 2, out)
        self.assertIn("is an OPEN PULL REQUEST, not a work item", out)
        self.assertNotIn("fix gh auth/network", out)
        # No report at all — the same contract the sibling refusal tests pin.
        self.assertNotIn("VERDICT", out)
        # The REASON must not assert a fact the tool cannot know. The field was
        # UNREADABLE, not absent, so "It names no closing issue" is false — a
        # wrong reason for a right decision, the defect class this change is
        # about. The corrected message names the unreadable field instead.
        self.assertIn("could not be read", out)
        self.assertNotIn("It names no closing issue", out)

    def test_open_pr_own_number_with_a_closing_reference_RESOLVES_both(self):
        """A PR number whose closing references name TWO issues gates BOTH (#9957).

        ⛔ THIS TEST USED TO PIN THE REFUSAL MESSAGE'S LINKED arm (`It closes
        #999, #1000.`). Since #9957 that arm is not where the run ends: the
        closing references ARE the resolution, so the gate computes a verdict for
        each named work item and takes the WORST. Two references are kept for the
        original reason — with one, the `", ".join` separator (then in the
        message, now in the header and the summary) is unexercised, so a
        regression in it would leave this test green (measured — `" and "` passed
        a single-issue version).

        Both work items COLLIDE here, for the reason that makes the whole fix
        safe: each resolved number is matched against the PR that named it (this
        one closes both), so a resolved run can only ever RAISE the verdict.
        """
        self.gh_fixtures(open_prs=[{
            "number": 3061, "title": "fix(battery): restore the pin test",
            "body": "Closes #999, closes #1000.", "state": "open",
            "headRefName": "chore/record-reads",
            "closingIssuesReferences": [{"number": 999}, {"number": 1000}],
        }])
        rc, out = self.run_tool()
        self.assertEqual(rc, 1, out)
        # The mapping is PRINTED — a resolution the caller cannot see is a
        # resolution it cannot verify against the brief it was handed.
        self.assertIn("a PULL REQUEST number was given — #3061", out)
        self.assertIn("Resolved via closing reference: #3061 → #999, #1000", out)
        # ONE report per work item, and the aggregate names them all.
        self.assertIn("collision-preflight: issue #999", out)
        self.assertIn("collision-preflight: issue #1000", out)
        self.assertIn("RESOLUTION: PR #3061 → 2 work item(s)", out)
        self.assertIn("#999 COLLISION", out)
        self.assertIn("#1000 COLLISION", out)
        self.assertIn("the WORST decides COLLISION (exit 1)", out)
        self.assertNotIn("could not resolve", out)
        self.assertNotIn("VERDICT: CLEAN", out)

    # ── #9957: a PR number is a dispatch input, so RESOLVE it ───────────────

    def test_a_pull_request_number_is_gated_as_the_work_item_it_serves(self):
        """The reported defect, end to end: a PR-numbered dispatch is gated by
        the SAME rule as its issue, so it can never read CLEAN past a holder.

        Measured 2026-10-25 (#9957): #9941 served #7970, THREE lanes held #7970,
        and a fourth dispatch keyed on `#9941` was one keystroke from being
        allowed — `collision_preflight 7970` returned `COLLISION (4 blocking
        hits)` while the same call with the PR number returned a refusal that
        named #7970 and then asked the dispatcher to re-read it. The gate had the
        mapping in its own message and did not use it.

        ⛔ THIS TEST IS RED ON `origin/main` (exit 2, no report) and green only
        when the number is RESOLVED. It asserts the HOLDER surface fired for the
        resolved number — not merely that the verdict is non-zero — because "the
        PR is still open, so exit 1 anyway" would pass a fix that resolved
        nothing at all.
        """
        self.gh_fixtures(open_prs=[{
            "number": 7000,
            "title": "feat(storage): serve reads from the record (#2712)",
            # NO closing keyword: the live #9941 carried none either, so the
            # title/branch arms are what must resolve it. A fixture with a
            # `Closes #N` body would pin only the field arm.
            "body": "Automated follow-up work.",
            "state": "open",
            "headRefName": "feat/2712-record-serves-anchored-reads",
        }])
        # The PR-as-issue payload `gh issue view 7000` really returns, and the
        # work item's own payload — two different objects in one run.
        self.gh_issue_for(7000,
                          title="feat(storage): serve reads from the record (#2712)")
        self.gh_issue_for(2712, title="feat(storage): serve reads from the record")
        self.fleet_board(issues={2712: [("holder-live", "branch slug")]},
                         lanes=[{"label": "holder-live", "live": True}])

        rc, out = self.run_tool(issue=7000)
        self.assertEqual(rc, 1, out)
        # The mapping is PRINTED, and it names the arm it came from — a reader can
        # check the resolution against the brief it was handed.
        self.assertIn("a PULL REQUEST number was given — #7000", out)
        self.assertIn("Resolved via title `(#N)`: #7000 → #2712", out)
        # The verdict is about the WORK ITEM, on every surface.
        self.assertIn("collision-preflight: issue #2712", out)
        self.assertIn("title: feat(storage): serve reads from the record", out)
        self.assertIn("holder:holder-live", out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("do NOT dispatch", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        self.assertNotIn("could not resolve", out)
        # A refusal is no longer what a resolvable PR number gets.
        self.assertNotIn("is an OPEN PULL REQUEST", out)

    def test_a_pull_request_number_whose_work_item_is_landed_exits_4(self):
        """A PR number whose work item is CLOSED keeps LANDED's own exit code.

        #9957 keeps the protocol's four codes intact: resolving a PR number must
        not flatten "already shipped" into "someone is on it" (1) or into "the
        world could not be read" (2), because the three remedies differ — verify
        and close, coordinate, or re-run.
        """
        self.gh_fixtures(open_prs=[{
            "number": 7000,
            "title": "feat(storage): serve reads from the record (#2712)",
            "body": "Automated follow-up work.",
            "state": "open",
            "headRefName": "feat/2712-record-serves-anchored-reads",
        }])
        self.gh_issue_for(7000,
                          title="feat(storage): serve reads from the record (#2712)")
        self.gh_issue_for(2712, title="feat(storage): serve reads from the record",
                          state="CLOSED", state_reason="COMPLETED")

        rc, out = self.run_tool(issue=7000)
        self.assertEqual(rc, 4, out)
        self.assertIn("Resolved via title `(#N)`: #7000 → #2712", out)
        # ANCHORED: a single work item must keep the plain `VERDICT:` line (only a
        # MULTI-item run demotes its per-item lines to `WORK ITEM #N VERDICT:`).
        self.assertIn("\nVERDICT: LANDED (exit 4)", out)
        self.assertNotIn("WORK ITEM #", out)
        self.assertNotIn("VERDICT: CLEAN", out)

    def test_a_METRIC_shaped_branch_resolving_to_a_CLOSED_issue_is_LANDED(self):
        """The union's ONE direction that is not fail-closed, pinned.

        The section note above `_TITLE_ISSUE_RE` says the union's cost is NOISE
        and that noise is the fail-CLOSED direction. That is true for COLLISION
        and INCOMPLETE, and FALSE here: a branch naming a SIZE or a METRIC
        (`perf/1200-rps-cache`) whose number happens to be a real CLOSED issue
        resolves the run onto that issue and reports LANDED (exit 4) — and LANDED
        is the answer the caller treats as authoritative and therefore does not
        re-ask (`refresh-candidates.sh`'s retry-stopping arm). The effect is
        STARVATION of a real candidate, not the mere delay an over-block costs.

        ⛔ THIS PINS A DISCLOSED TRADEOFF, NOT A DESIRED BEHAVIOUR. Its job is to
        couple the arm to its note: narrowing the branch arm (the obvious
        remedy) turns this test red, which forces the section note to be
        rewritten in the same change instead of quietly going stale. If the arm
        is narrowed deliberately, replace this case with the narrower shape —
        do not delete it silently.
        """
        self.gh_fixtures(open_prs=[{
            "number": 7000,
            "title": "perf(storage): cache the hot read path",
            # No `(#N)` in the title and no closing keyword: the branch is the
            # ONLY arm that speaks, so the resolution is the branch arm's alone.
            "body": "Automated follow-up work.",
            "state": "open",
            "headRefName": "perf/1200-rps-cache",
        }])
        self.gh_issue_for(7000, title="perf(storage): cache the hot read path")
        # The METRIC's number IS a real closed issue — the whole point: the arm
        # cannot tell a rate from an issue number, so it resolves onto one.
        self.gh_issue_for(1200, title="chore: retire the old cache",
                          state="CLOSED", state_reason="COMPLETED")

        rc, out = self.run_tool(issue=7000)
        self.assertEqual(rc, 4, out)
        self.assertIn(
            "Resolved via branch `<type>/<n>-<slug>`: #7000 → #1200", out)
        # ONE work item keeps its verdict ANCHORED, and it is the retry-stopper.
        self.assertIn("\nVERDICT: LANDED (exit 4)", out)
        self.assertNotIn("VERDICT: CLEAN", out)

    def test_a_work_item_a_LOSING_arm_named_is_still_gated(self):
        """EVERY arm's number is gated, not only the first arm that speaks.

        The shipped shape returned on the FIRST arm that spoke, so a body saying
        `Closes #1000` hid a `(#2712)` title and a `feat/2712-…` branch: #2712 was
        never gated, never resolved and never printed. Where the work item the
        losing arm named is the one a lane holds (exactly #9957's measured case)
        the run then reports about the wrong issue and can read CLEAN — `dispatch`
        — while `collision_preflight 2712` exits 1. That is this issue's own
        duplicate-dispatch class, reintroduced one layer up.

        ⛔ THE RESOLVED WORK ITEM IS ALWAYS NAMED BY THE PR, and an OPEN PR naming
        an issue is itself a blocking hit, so a resolved run cannot reach CLEAN
        even when the resolution is wrong — which is why this test asserts the
        GATED WORK ITEM and its holder hit, not only the exit code (which is 1
        either way). The exit-code half is pinned in
        `test_the_aggregate_is_the_WORST_and_the_ONLY_anchored_VERDICT_line`.
        """
        self.gh_fixtures(open_prs=[{
            "number": 7000,
            "title": "feat(storage): serve reads from the record (#2712)",
            "body": "Closes #1000.",
            "state": "open",
            "headRefName": "feat/2712-record-serves-anchored-reads",
            "closingIssuesReferences": [{"number": 1000}],
        }])
        self.gh_issue_for(
            7000, title="feat(storage): serve reads from the record (#2712)")
        self.gh_issue_for(1000, title="docs: tidy the index")
        self.gh_issue_for(2712, title="feat(storage): serve reads from the record")
        self.fleet_board(issues={2712: [("holder-live", "branch slug")]},
                         lanes=[{"label": "holder-live", "live": True}])

        rc, out = self.run_tool(issue=7000)
        self.assertEqual(rc, 1, out)
        # BOTH contributing arms are named, so the reader can check the union
        # against the brief instead of trusting a single-arm sentence.
        self.assertIn(
            "Resolved via closing reference, title `(#N)`: #7000 → #1000, #2712",
            out)
        # ...and the work item the LOSING arm named is gated, with its real hit.
        self.assertIn("collision-preflight: issue #2712", out)
        self.assertIn("holder:holder-live", out)
        self.assertIn("RESOLUTION: PR #7000 → 2 work item(s)", out)

    def test_a_PARTIAL_closing_read_is_named_in_the_header_the_caller_sees(self):
        """The success header must not sound authoritative over a partial read.

        On the REST-rescued open-PR surface (#9402) `closingIssuesReferences` is
        absent for EVERY entry, so the closing arm resting on the body's keyword
        is the NORMAL route whenever the GraphQL budget is spent — and the header
        used to print a bare `Resolved via closing reference` for it. That is a
        sentence about evidence the tool never had; the `checked` line saying so
        is rendered on the FAILURE path only.
        """
        self.gh_fixtures(open_prs=[{
            "number": 7000, "title": "fix(storage): serve reads",
            "body": "Closes #1000.", "state": "open",
            "headRefName": "chore/record-reads",
        }])
        # The harness normalises the field IN (so no call site can forget it),
        # so the unreadable shape must be reached by DELETING the key — the same
        # rule the sibling refusal test records.
        prs = json.loads((self.gh_dir / "open_prs.json").read_text())
        del prs[0]["closingIssuesReferences"]
        (self.gh_dir / "open_prs.json").write_text(json.dumps(prs))
        self.gh_issue_for(7000, title="fix(storage): serve reads")
        self.gh_issue_for(1000, title="docs: tidy the index")

        rc, out = self.run_tool(issue=7000)
        # The verdict is INCOMPLETE, not COLLISION: the same absent field makes
        # the open-PR SURFACE unreadable, which is INCOMPLETE by design. That is
        # the point — the run is decided on a partial read, so the header must
        # not present the resolution as if the source had spoken.
        self.assertEqual(rc, 2, out)
        self.assertIn("the body's closing keyword ONLY", out)
        self.assertIn(
            "GitHub's computed `closingIssuesReferences` field could not be read",
            out)
        # The bare, authoritative form must NOT be what a caller reads.
        self.assertNotIn("Resolved via closing reference: #7000", out)
        # ONE work item keeps its verdict ANCHORED: demoting it (the first cut of
        # this fix) would leave a single-item resolved run with no anchored
        # verdict at all, so the text-keyed consumer would read an empty value.
        self.assertIn("\nVERDICT: INCOMPLETE (exit 2)", out)
        self.assertNotIn("WORK ITEM #1000 VERDICT", out)

    def test_the_resolver_reads_every_arm_and_prefers_the_source(self):
        """`resolve_pull_request_work_items` in isolation (#9957).

        Driven DIRECTLY so each arm is exercised without a subprocess (and
        without the open-PR list, which is what supplies the record in
        production). Every case here is a shape a real PR carries.

        ⛔ `arms`/`arm` IS A LABEL, `issues` IS THE UNION, AND THE TWO ARE NOT
        THE SAME QUESTION. The first case has arms that DISAGREE (the computed
        field names #3000, the title and branch name #2712); the shipped shape
        returned `(3000,)` and dropped #2712, which is the work item a lane
        actually holds. SOURCE-first order decides what the header NAMES, never
        which numbers get gated — see
        `test_a_work_item_a_LOSING_arm_named_is_still_gated`.
        """
        cp = _tool_module()
        resolve = cp.resolve_pull_request_work_items

        # The SOURCE is LABELLED first — GitHub's computed field outranks the
        # title/branch conventions, which are re-derivations (#3504) — but every
        # arm's number is GATED.
        got = resolve({
            "number": 7000, "title": "feat: thing (#2712)", "body": "",
            "headRefName": "feat/2712-thing",
            "closingIssuesReferences": [{"number": 3000}],
        }, 7000)
        self.assertEqual(got.issues, (2712, 3000))
        self.assertEqual(got.arm, cp._ARM_CLOSING)
        self.assertEqual(got.arms, (cp._ARM_CLOSING, cp._ARM_TITLE))

        # ...and when it speaks for nothing, the title convention (#9941's own
        # shape — no closing keyword, the tag in the title).
        got = resolve({
            "number": 9941,
            "title": "feat(storage): serve reads (#7970)", "body": "",
            "headRefName": "feat/7970-record-serves-anchored-reads",
            "closingIssuesReferences": [],
        }, 9941)
        self.assertEqual(got.issues, (7970,))
        self.assertEqual(got.arm, cp._ARM_TITLE)

        # ...and then the branch, which is what a PR with neither still carries.
        got = resolve({
            "number": 7000, "title": "fix(storage): reads", "body": "",
            "headRefName": "feat/2712-record-serves-anchored-reads",
            "closingIssuesReferences": [],
        }, 7000)
        self.assertEqual(got.issues, (2712,))
        self.assertEqual(got.arm, cp._ARM_BRANCH)

        # An UNREADABLE closing field is not an empty one: the arm says so and
        # resolution falls through to the next signal instead of asserting.
        got = resolve({
            "number": 7000, "title": "fix: reads (#2712)", "body": "",
            "headRefName": "feat/2712-reads",
        }, 7000)
        self.assertEqual(got.issues, (2712,))
        self.assertIn("the computed field could not be read", " ".join(got.checked))

        # A tool-generated branch claims nothing (the same rule the surfaces use).
        got = resolve({
            "number": 7000, "title": "chore: refresh", "body": "",
            "headRefName": "mergify/merge-queue/6160bad001",
            "closingIssuesReferences": [],
        }, 7000)
        self.assertEqual(got.issues, ())
        self.assertIsNone(got.arm)
        self.assertIn("tool-generated", " ".join(got.checked))

        # THE ARMS ANCHOR THEIR DIGITS, which is what makes the grammar safe on
        # arbitrary PR text: `(#N)` needs the closing paren, so a hex-ish run that
        # merely starts with a number is not a title tag. (This case is why the
        # arms need no `_inside_hex_digest` guard — see `_arm_numbers`. A case
        # that carried no digest at all would pin nothing: it was found VACUOUS by
        # review in cycle 1 and replaced with this one.)
        got = resolve({
            "number": 7000, "title": "feat: thing (#2712cafe)", "body": "",
            "headRefName": "chore/reads",
            "closingIssuesReferences": [],
        }, 7000)
        self.assertEqual(got.issues, ())
        self.assertIsNone(got.arm)

        # THE PR'S OWN NUMBER IS NEVER A WORK ITEM — it is the same object the
        # run refused, and handing it back either loops or re-refuses with a
        # misleading reason. `dropped_self` is what lets the refusal say so.
        got = resolve({
            "number": 3061, "title": "feat: thing (#3061)", "body": "Closes #3061",
            "headRefName": "feat/3061-thing",
            "closingIssuesReferences": [{"number": 3061}],
        }, 3061)
        self.assertEqual(got.issues, ())
        self.assertTrue(got.dropped_self)
        self.assertIn("the PR's OWN number", cp._resolution_failure(got))

    def test_closing_reference_still_sees_short_issue_numbers(self):
        """`closing_reference` must not lose the SHORT end of the range (#9957).

        ⛔ THE FAIL-OPEN A REVIEW CYCLE FOUND. `closing_reference` was rewritten to
        delegate to the resolver's shared grammar, and the first version of that
        grammar was `#(\\d{3,7})` — copied from the branch-slug width. The
        predicate it replaced was `#{issue}(?![0-9])` with NO width bound, so the
        delegation silently stopped matching `Closes #42` for issue 42, `#7` for
        issue 7, `#99` for issue 99 (measured: True -> False for all three).

        On the REST-rescued open-PR surface the body keyword is the ONLY closing
        signal and its non-terminal hits are `strong`/blocking, so that narrowing
        DROPS a blocking hit — a false CLEAN authorising a dispatch, which is the
        failure the whole gate exists to prevent. Nothing pinned it: the rest of
        the suite uses four-digit issue numbers.
        """
        cp = _tool_module()
        for text, issue, expected in (
            ("Closes #42.", 42, True),
            ("Fixes #7", 7, True),
            ("resolves #99", 99, True),
            ("Closes #420.", 42, False),
            ("Closes #3061.", 3061, True),
            ("Closes #30610.", 3061, False),
            ("no keyword here #42", 42, False),
            ("Closes #12345678901.", 12345678901, True),
        ):
            with self.subTest(text=text, issue=issue):
                self.assertEqual(cp.closing_reference(text, issue), expected)
        # And the inverse reader agrees with the predicate it inverts.
        self.assertEqual(cp._closing_numbers_in_text("Closes #42, fixes #7."),
                         [42, 7])

    def test_the_resolver_is_NOT_table_driven(self):
        """No mapping table: an arbitrary, unseen number resolves correctly.

        #9957's own warning — "a mapping table of known numbers is not a fix" —
        is what this pins. A table keyed by the numbers someone happened to
        record answers for those and silently stops answering for the next one,
        which is the same class of defect as the one being fixed.
        """
        cp = _tool_module()
        got = cp.resolve_pull_request_work_items({
            "number": 8888, "title": "", "body": "Closes #8887.",
            "headRefName": "chore/unrelated",
            "closingIssuesReferences": [{"number": 8887}],
        }, 8888)
        self.assertEqual(got.issues, (8887,))
        self.assertEqual(got.arm, cp._ARM_CLOSING)

    def test_a_partial_closing_read_is_NAMED_and_not_dressed_as_the_source(self):
        """An unreadable computed field must be disclosed on the SUCCESS path.

        `_closing_ref_numbers` raises when `closingIssuesReferences` is absent —
        which is the NORMAL shape on the REST-rescued open-PR surface (#9402),
        so this is not a corner case but the route taken whenever the GraphQL
        budget is spent. The resolver then unions in the body's closing keyword,
        and the closing arm FIRES on that partial data. Before review the header
        printed a bare `Resolved via closing reference` for it — an authoritative
        sentence over evidence the tool never had, and (because the precedence
        chain discarded the other arms) with the numbers those arms saw shown
        nowhere at all.

        `checked` said `could not be read` all along, but it is rendered only on
        the FAILURE path, so a reader of a successful run could not see it.
        """
        cp = _tool_module()
        partial = cp.resolve_pull_request_work_items({
            "number": 7000, "title": "fix(storage): serve reads",
            "body": "Closes #1000.",
            "headRefName": "chore/record-reads",   # no computed field at all
        }, 7000)
        self.assertTrue(partial.closing_field_unreadable)
        self.assertEqual(partial.issues, (1000,))
        route = cp._resolution_route(partial)
        self.assertIn("the body's closing keyword ONLY", route)
        self.assertIn("could not be read", route)
        # A READABLE field carries no such caveat — so the flag is a fact about
        # the read, not a constant. (Without this half, hard-coding the caveat on
        # would pass.)
        whole = cp.resolve_pull_request_work_items({
            "number": 7000, "title": "fix(storage): serve reads",
            "body": "Closes #1000.",
            "headRefName": "chore/record-reads",
            "closingIssuesReferences": [{"number": 1000}],
        }, 7000)
        self.assertFalse(whole.closing_field_unreadable)
        self.assertNotIn("could not be read", cp._resolution_route(whole))
        self.assertEqual(cp._resolution_route(whole), cp._ARM_CLOSING)

    def test_the_resolver_drops_a_number_it_cannot_treat_as_an_issue(self):
        """`_ISSUE_NUMBER_MAX` is POLICY THE RESOLVER APPLIES, and it is tested.

        Review found the bound had NO test at all, so deleting it was undetected.
        It is deliberately the resolver's own (the shared grammar stays
        unbounded — see `_arm_numbers`), because a snowflake or a timestamp in
        `(#1234567890123)` form is not an issue and resolving one spends a
        `gh issue view` on a number that cannot exist.
        """
        cp = _tool_module()
        for value, expected in (
            (1, True), (42, True),
            (cp._ISSUE_NUMBER_MAX, True),
            (cp._ISSUE_NUMBER_MAX + 1, False),
            (0, False), (-1, False),
        ):
            with self.subTest(value=value):
                self.assertEqual(cp._is_plausible_issue_number(value), expected)

        # AND IT IS APPLIED, not merely defined: the same arm resolves just
        # inside the bound and refuses just outside it, so neither the guard nor
        # a constant `False` can pass this pair.
        outside = cp.resolve_pull_request_work_items({
            "number": 7000, "title": f"feat: thing (#{cp._ISSUE_NUMBER_MAX + 1})",
            "body": "", "headRefName": "chore/reads",
            "closingIssuesReferences": [],
        }, 7000)
        self.assertEqual(outside.issues, ())
        self.assertIsNone(outside.arm)
        inside = cp.resolve_pull_request_work_items({
            "number": 7000, "title": f"feat: thing (#{cp._ISSUE_NUMBER_MAX})",
            "body": "", "headRefName": "chore/reads",
            "closingIssuesReferences": [],
        }, 7000)
        self.assertEqual(inside.issues, (cp._ISSUE_NUMBER_MAX,))

    def test_the_severity_order_ranks_decisiveness_not_numeric_value(self):
        """The aggregation order is DECISIVENESS, and it is pinned (#9957).

        Review measured that `_EXIT_SEVERITY` had no test anywhere in `tests/`,
        so every permutation of it stayed green: `_EXIT_SEVERITY[EXIT_CLEAN] = 99`
        (a plausible typo beside `: 0`) made a CLEAN+COLLISION resolution return
        **exit 0** and print "the WORST decides CLEAN". Replacing the map with
        `max(numeric code)` is the other silent mutant, and it is the one that
        changes the REMEDY the caller is told to apply (INCOMPLETE — "re-run" —
        would outrank COLLISION — "do not dispatch") — hence the
        INCOMPLETE<COLLISION assertion, which the codes' own values invert.
        """
        cp = _tool_module()
        severity = cp._EXIT_SEVERITY
        self.assertEqual(
            set(severity),
            {cp.EXIT_CLEAN, cp.EXIT_COLLISION, cp.EXIT_INCOMPLETE, cp.EXIT_LANDED},
        )
        # EXIT_USAGE is never a report code, so it must not be in the map (a
        # missing key there would be a KeyError in the aggregator).
        self.assertNotIn(cp.EXIT_USAGE, severity)
        self.assertLess(severity[cp.EXIT_CLEAN], severity[cp.EXIT_INCOMPLETE])
        self.assertLess(severity[cp.EXIT_INCOMPLETE], severity[cp.EXIT_COLLISION])
        self.assertLess(severity[cp.EXIT_COLLISION], severity[cp.EXIT_LANDED])

    def _aggregate(self, codes):
        """Drive `_verdict_for_pull_request` over a STUBBED per-item pre-flight.

        The aggregation is what review found untested, and it cannot be reached
        end-to-end for every combination: a resolved number is ALWAYS named by
        the PR (that is how it resolved), and an OPEN PR naming an issue is
        itself a blocking hit — so a resolved work item can never read CLEAN in
        the real tool. Stubbing `run_one` is therefore the only way to exercise
        the CLEAN+COLLISION arm the review asked for, and it exercises the REAL
        aggregator rather than a re-implementation of it.
        """
        cp = _tool_module()
        issues = [100, 200][:len(codes)]
        pr = {
            "number": 7000, "title": "fix(x)",
            "body": "Closes " + ", closes ".join(f"#{n}" for n in issues) + ".",
            "headRefName": "chore/aggregate",
            "closingIssuesReferences": [{"number": n} for n in issues],
        }
        by_issue = dict(zip(issues, codes, strict=True))

        def run_one(issue):
            code = by_issue[issue]
            return (
                f"collision-preflight: issue #{issue}\n\n"
                f"VERDICT: {cp._EXIT_LABEL[code]} (exit {code}) — stubbed #{issue}\n",
                code,
            )

        text, rc = cp._verdict_for_pull_request(
            cp.NotAWorkItem("refused", pr=pr), 7000, run_one,
        )
        return cp, text, rc

    def test_the_aggregate_is_the_WORST_and_the_ONLY_anchored_VERDICT_line(self):
        """ONE anchored `VERDICT:` line, and it is the deciding one (#9957).

        The shipped shape sorted worst-LAST and printed every work item's
        `VERDICT:` line, on the belief that a text-keyed caller greps the TAIL.
        The fleet's only such caller greps the HEAD and never compares the value
        to the exit code
        (`agent-infra/scripts/orchestrator-harness/refresh-candidates.sh`:
        `grep -E '^VERDICT' | head -1`), so on `[#100 CLEAN, #200 COLLISION]` it
        read **CLEAN** — the arm that QUEUES a dispatch — out of a report whose
        exit code refused the dispatch. Reordering alone would only reverse the
        bug (a tail reader would then get it wrong), so the per-item lines are
        re-prefixed instead and exactly one anchored line survives.
        """
        for codes, expected in (
            ((0, 1), ("COLLISION", 1)),      # CLEAN + COLLISION
            ((1, 4), ("LANDED", 4)),         # COLLISION + LANDED
            ((0, 2), ("INCOMPLETE", 2)),     # CLEAN + INCOMPLETE
            ((1, 1), ("COLLISION", 1)),      # the same verdict twice
        ):
            with self.subTest(codes=codes):
                cp, text, rc = self._aggregate(codes)
                self.assertEqual(rc, expected[1], text)
                anchored = [line for line in text.splitlines()
                            if line.startswith(cp._VERDICT_LINE_PREFIX)]
                self.assertEqual(len(anchored), 1, text)
                self.assertIn(f"VERDICT: {expected[0]} (exit {expected[1]})",
                              anchored[0])
                # A caller reading the FIRST anchored line gets the decision —
                # the exact read `refresh-candidates.sh` makes.
                self.assertIn(expected[0], anchored[0])
                # ...and every per-item verdict is still VISIBLE, attributable,
                # and worst-first, instead of being dropped to fix the reader.
                self._assert_per_item_verdicts_are_visible(cp, text, codes)

    def _aggregate_with_an_unrunnable_item(self, codes, unrunnable_issue):
        """Drive the aggregate when ONE resolved number is ITSELF an open PR.

        `run_one` is the real single-number pre-flight: handed a number that is
        an open pull request, it raises `NotAWorkItem` — which is exactly what a
        resolved work item can be (`#7000` resolving to `[#100, #8000]` where
        `#8000` is a PR). The other item still gets a real verdict, and the
        aggregator is expected to decide over BOTH.
        """
        cp = _tool_module()
        pr = {
            "number": 7000, "title": "fix(x)",
            "body": "Closes " + ", closes ".join(f"#{n}" for n in codes) + ".",
            "headRefName": "chore/unrunnable",
            "closingIssuesReferences": [{"number": n} for n in codes],
        }

        def run_one(issue):
            if issue == unrunnable_issue:
                raise cp.NotAWorkItem(
                    f"#{issue} is an open pull request, not a work item."
                )
            code = codes[issue]
            return (
                f"collision-preflight: issue #{issue}\n\n"
                f"VERDICT: {cp._EXIT_LABEL[code]} (exit {code}) — stubbed #{issue}\n",
                code,
            )

        text, rc = cp._verdict_for_pull_request(
            cp.NotAWorkItem("refused", pr=pr), 7000, run_one,
        )
        return cp, text, rc

    def test_an_unrunnable_resolved_item_cannot_discard_the_other_items(self):
        """A refused item is ONE item's verdict, never the whole run's (#9970).

        Review measured this on the shipped shape: the loop returned from
        inside itself on `NotAWorkItem`, BEFORE the aggregate, so a resolution
        over `[#100 LANDED, #8000 <an open PR>]` reported `exit 2` and the
        sentence "so nothing was checked" — while `#100` HAD been checked and
        its LANDED had already been computed. Two things went wrong at once:

        * a terminal fact (`EXIT_LANDED`, which the module's own precedence rule
          says outranks 2) was thrown away, so the caller was told to "re-run"
          instead of to "verify and close";
        * the refusal claimed a check that DID run never happened — the standard
          `_resolution_failure`'s docstring sets for every refusal in this file.

        The fix folds the refusal in as THIS ITEM's `EXIT_INCOMPLETE` and lets
        `_EXIT_SEVERITY` decide, for either order of the two items.

        ⛔ EITHER ORDER MEANS TWO ORDERS THE LOOP ACTUALLY REACHES. The
        resolver returns its work items SORTED
        (`resolve_pull_request_work_items`: `issues=tuple(sorted(gated))`), so
        the item LOOP order is the sorted order and nothing the fixture's dict
        insertion order contributes reaches it. An earlier version of this test
        passed `{100: 4, 8000: 4}` and `{8000: 4, 100: 4}` and ran `100, 8000`
        TWICE (#9970 review) — the "unrunnable first" subTest never ran first.
        So the unrunnable number is chosen to invert the SORTED order.
        """
        for codes, unrunnable, runnable in (
            # unrunnable SECOND — the loop had already COMPUTED the other
            # item's terminal LANDED, which is the fact the shipped code
            # discarded. Sorted order: (100, 8000).
            ({100: 4, 8000: 4}, 8000, 100),
            # unrunnable FIRST — nothing had been computed yet, so the old
            # run's "nothing was checked" sentence was TRUE and the fix must
            # not turn that case into a refusal of its own. Sorted order:
            # (8000, 9000).
            ({8000: 4, 9000: 4}, 8000, 9000),
        ):
            with self.subTest(order=list(codes), unrunnable=unrunnable):
                cp, text, rc = self._aggregate_with_an_unrunnable_item(
                    codes, unrunnable,
                )
                # The terminal fact wins: `4` is decisive, so exit 2 is wrong.
                self.assertEqual(rc, cp.EXIT_LANDED, text)
                anchored = [line for line in text.splitlines()
                            if line.startswith(cp._VERDICT_LINE_PREFIX)]
                self.assertEqual(len(anchored), 1, text)
                self.assertIn("VERDICT: LANDED (exit 4)", anchored[0])
                # The run did NOT forget the item it could not gate — bound to
                # that ITEM's own (demoted) body line. ⚠️ A bare
                # `f"#{unrunnable} INCOMPLETE"` does NOT bind it: that substring
                # is produced by the `RESOLUTION:` tail's per-item list, so it
                # stays green if the item line is dropped.
                self.assertIn(
                    f"WORK ITEM #{unrunnable} VERDICT: INCOMPLETE", text,
                )
                # The item that DID run keeps its verdict and its report.
                self.assertIn(
                    f"WORK ITEM #{runnable} VERDICT: LANDED (exit 4) — "
                    f"stubbed #{runnable}",
                    text,
                )
                # ...and the run must not fall back to the WHOLE-RUN refusal,
                # the shape that claimed a check which DID run never happened.
                # Bound to the text `_resolution_failure` actually emits (both
                # of its branches open with `could not resolve #<n>`); an
                # earlier assertion pinned the lowercase phrase "nothing was
                # checked", which appears NOWHERE in this tool's output — only
                # in a source comment — so it could never fail.
                self.assertNotIn(f"could not resolve #{7000}", text)
                self.assertIn("could not be gated", text)

    def test_a_single_unrunnable_resolved_item_still_prints_one_anchored_verdict(
        self,
    ):
        """The one-item refusal keeps an anchored verdict (#9970).

        `#7000` resolving ONLY to `#8000` (itself an open PR) is the shape that
        used to print nothing to stdout at all. A text-keyed consumer greps
        `^VERDICT` and reads an EMPTY value, which is indistinguishable from a
        report that never ran. The refusal must therefore carry the anchored
        line itself, exactly as a single-item resolved run does.
        """
        cp, text, rc = self._aggregate_with_an_unrunnable_item({8000: 4}, 8000)
        self.assertEqual(rc, cp.EXIT_INCOMPLETE, text)
        anchored = [line for line in text.splitlines()
                    if line.startswith(cp._VERDICT_LINE_PREFIX)]
        self.assertEqual(len(anchored), 1, text)
        self.assertIn("VERDICT: INCOMPLETE (exit 2)", anchored[0])
        self.assertIn("open pull request", anchored[0])

    def _assert_per_item_verdicts_are_visible(self, cp, text, codes):
        """Each work item keeps a verdict line, demoted and worst-first."""
        issues = [100, 200][:len(codes)]
        positions = {}
        for code, issue in zip(codes, issues, strict=True):
            marker = f"WORK ITEM #{issue} {cp._VERDICT_LINE_PREFIX}"
            position = text.find(marker)
            self.assertGreaterEqual(position, 0, text)
            self.assertIn(f"{marker}{cp._EXIT_LABEL[code]}", text)
            positions[issue] = position
        worst_code = max(codes, key=lambda code: cp._EXIT_SEVERITY[code])
        worst_issue = issues[codes.index(worst_code)]
        self.assertEqual(
            min(positions, key=positions.get), worst_issue,
            "the deciding work item must be printed first",
        )

    def test_terminal_pr_closing_reference_is_reported_but_non_blocking(self):
        # The contractual "Closes #N" is the strongest statement a PR body can
        # make — and on a TERMINAL PR it is still history, not in-flight work.
        # It stays a hard hit on the OPEN surface (the test below), which is
        # where it can actually still be in flight.
        self.gh_fixtures(closed_prs=[{
            "number": 9997, "title": "unrelated title",
            "body": "Closes #3061.", "headRefName": "chore/9997-unrelated",
            "state": "closed",
        }])
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("closing reference to #3061", out)
        self.assertIn("PR is closed: immutable history", out)
        self.assertNotIn("do NOT dispatch", out)

    def test_open_pr_closing_reference_is_still_a_hard_hit(self):
        # The live half of the same rule. "Closes #N" IS a claim on the issue,
        # and an OPEN PR can still be in flight, so it must stay a strong hit.
        for body in ("Closes #3061.", "closes: #3061", "Fixes #3061",
                     "Resolves #3061", "fixed #3061"):
            with self.subTest(body=body):
                self.gh_fixtures(open_prs=[{
                    "number": 9997, "title": "unrelated title",
                    "body": body, "headRefName": "chore/9997-unrelated",
                    "state": "open",
                }])
                rc, out = self.run_tool()
                self.assertNotEqual(rc, 0, f"body={body!r}\n{out}")
                self.assertIn("VERDICT: COLLISION", out)
                # The message names WHICH source spoke: the computed field
                # or the body regex (the deliberate union). A closing
                # reference is a STRONG hit, so the run must not be CLEAN.
                self.assertIn("#3061", out)
                self.assertIn("closing", out)

    def _write_issue_payload(self, payload: dict) -> None:
        """Write the issue fixture DIRECTLY, bypassing `gh_fixtures`' normalizer.

        That normalizer adds `assignees` because the real `gh issue view
        --json` always returns it — so a test for the MISSING-key path has to
        bypass it, which is the point of these tests. (Comment bodies are not
        part of the payload contract at all: the claim gate does not fetch or
        read them, #8175.)
        """
        (self.gh_dir / "issue.json").write_text(json.dumps(payload))

    def test_issue_payload_missing_key_is_incomplete_not_clean(self):
        # ⛔ THE FAIL-OPEN DIRECTION. `gh issue view --json ...,assignees` ALWAYS
        # returns the key, so a missing one means a malformed payload — and
        # reading it as "no assignees" DROPS a different-account assignee.
        # Absence looks exactly like emptiness, which is why the check is on
        # PRESENCE. Same reasoning that already makes an absent
        # `closingIssuesReferences` INCOMPLETE.
        payload = {"number": ISSUE, "title": "t", "state": "OPEN",
                   "assignees": []}
        del payload["assignees"]
        self._write_issue_payload(payload)
        rc, out = self.run_tool()
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertIn("has no 'assignees' key", out)
        self.assertNotIn("VERDICT: CLEAN", out)

    def test_issue_payload_malformed_element_is_incomplete_not_a_traceback(self):
        # ⛔ A non-object ELEMENT reached `.get` and raised `AttributeError`, which
        # is neither `SurfaceError` nor `RuntimeError` — so it escaped as a
        # traceback with NO `VERDICT` line and exit 1, the code this module
        # documents as COLLISION. `_require_pr_dicts` closed that for the PR
        # lists; the issue payload had no contract at all.
        payload = {"number": ISSUE, "title": "t", "state": "OPEN",
                   "assignees": [None]}
        self._write_issue_payload(payload)
        rc, out = self.run_tool()
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertIn("non-object element in 'assignees' at index 0", out)
        self.assertNotIn("Traceback", out)

    def test_issue_payload_non_string_login_is_incomplete_not_a_traceback(self):
        # A non-string `login` crashed inside `assignee_attribution`'s
        # `.strip()`, giving the same verdict-less traceback and exit 1.
        for bad in (5, True, {"nested": 1}):
            self._write_issue_payload({
                "number": ISSUE, "title": "t", "state": "OPEN",
                "assignees": [{"login": bad}],
            })
            rc, out = self.run_tool()
            self.assertEqual(rc, 2, f"login={bad!r}\n{out}")
            self.assertIn("non-string assignee login", out)
            self.assertNotIn("Traceback", out)


    def test_issue_payload_non_list_container_is_incomplete(self):
        # ⛔ The per-ELEMENT check cannot cover a malformed CONTAINER, and the
        # mutation `if not isinstance(value, list): value = []` passed every
        # test. Treating a malformed container as empty is the same fail-open the
        # presence check exists for — it DROPS every assignee. No fixture
        # supplied a non-list `assignees`.
        payload = {"number": ISSUE, "title": "t", "state": "OPEN",
                   "assignees": []}
        payload["assignees"] = {"login": "other-agent"}
        self._write_issue_payload(payload)
        rc, out = self.run_tool()
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertIn("non-list 'assignees'", out)
        self.assertNotIn("VERDICT: CLEAN", out)

    def test_issue_payload_unvalidated_reader_fields_are_incomplete(self):
        # ⛔ THE FIRST VERSION OF `_require_issue_payload` REPRODUCED ITS OWN
        # DEFECT ONE READER OVER: it validated `assignees[*].login` but left
        # `title`/`state`/`number` (and, at the time, the comment fields — which
        # #8175 deleted from the payload entirely) unchecked. A wrong type there
        # did not produce a verdict at all.
        #
        # The rule the contract follows is that it covers every field a reader
        # touches; the reader set is a `grep` of the call sites, not a list kept
        # here — such a list is a claim about the whole call graph and it goes
        # stale on the next edit.
        #
        # Every case is built on a payload that is otherwise WELL-FORMED, so a
        # regression cannot pass by returning INCOMPLETE for an unrelated reason.
        base = {
            "number": ISSUE, "title": "t", "state": "OPEN",
            "url": "https://example.invalid/issues/3061",
            "assignees": [{"login": "other-agent"}],
        }
        cases = [
            ("assignee login", ("assignees", 0, "login"),
             lambda c: c["assignees"][0].__setitem__("login", 5)),
            ("title", ("title",), lambda c: c.__setitem__("title", 5)),
            ("state", ("state",), lambda c: c.__setitem__("state", 5)),
            ("number", ("number",), lambda c: c.__setitem__("number", "3061")),
            ("number bool", ("number",), lambda c: c.__setitem__("number", True)),
        ]
        for label, path, mutate in cases:
            with self.subTest(field=path):
                payload = json.loads(json.dumps(base))
                mutate(payload)
                self._write_issue_payload(payload)
                rc, out = self.run_tool()
                self.assertEqual(rc, 2, f"{label}\n{out}")
                self.assertIn("VERDICT: INCOMPLETE", out)
                self.assertNotIn("Traceback", out)

    def test_pr_payload_non_string_field_is_incomplete_not_a_traceback(self):
        # The PR side of the same rule: `_require_pr_dicts` checked that an
        # element was an OBJECT and left its STRING fields untouched, so a
        # non-string value in one of them produced a traceback with no VERDICT
        # line and exit 1 — read by callers as COLLISION. `title`/`headRefName`
        # are required and must be strings; only `body` may legitimately be
        # null. `state` is covered here because it is one of those fields, and a
        # contract has to cover the whole set rather than the three that came
        # first to mind. See
        # `test_open_pr_with_null_state_and_truthy_merged_at_is_incomplete` for
        # why a null `state` is a REFUSAL rather than a nullable field: its
        # absence removes the liveness short-circuit, so a live open PR carrying
        # a truthy `mergedAt` was read as merged and reported CLEAN.
        for key, bad in (("title", 5), ("headRefName", 5), ("body", 5),
                         ("state", 5)):
            with self.subTest(field=key):
                pr = {"number": 5150, "title": "unrelated", "body": "",
                      "headRefName": "feat/9999-other", "state": "open"}
                pr[key] = bad
                self.gh_fixtures(open_prs=[pr])
                rc, out = self.run_tool()
                self.assertEqual(rc, 2, f"key={key}\n{out}")
                self.assertIn("VERDICT: INCOMPLETE", out)
                self.assertIn(f"non-string '{key}'", out)
                self.assertNotIn("Traceback", out)

    def test_closed_pr_without_head_sha_cannot_demote_a_branch(self):
        # ⛔ THE CONTRACT REQUIRED THE HARVEST'S GATE AND NOT ITS DATA.
        # `mergedAt` was required; `headSha` — the field the harvest actually
        # READS to populate `merged_heads` — was not. Absence read as the
        # empty string, `if _sha:` was False, the map stayed empty, and the
        # squash-merge demotion simply did not run: #5186's resignal died
        # silently while the run reported a clean surface. This is the same
        # class of hole `_merged_pr_fixture`'s docstring records one key over
        # (the wrong-key version of it, which made the predicate dead code in
        # production while every test passed).
        #
        # The control is `test_squash_merged_branch_is_terminal_and_cannot_block`
        # — the identical fixture, with `headSha` present, is CLEAN. Deleting the
        # key must not produce the same answer.
        ref = f"docs/research-{ISSUE}-4333"
        _git(self.repo, "branch", ref)
        self.gh_fixtures(closed_prs=[self._merged_pr_fixture(ref)])
        prs = json.loads((self.gh_dir / "closed_prs.json").read_text())
        del prs[0]["headSha"]
        (self.gh_dir / "closed_prs.json").write_text(json.dumps(prs))
        rc, out = self.run_tool(issue=ISSUE)
        self.assertIn("no 'headSha'", out)
        # And the DIRECTION is the safe one: with no evidence to demote on, the
        # branch keeps blocking. The defect was never a wrong verdict on the
        # happy path — it was a mechanism that had stopped running.
        self.assertEqual(rc, 1, out)
        self.assertIn("VERDICT: COLLISION", out)

    def test_blank_state_with_a_truthy_merged_at_is_incomplete_not_clean(self):
        # ⛔ THE DEMONSTRATED FAIL-OPEN OF THE CYCLE-11 CONTRACT. `state` was
        # required to be a `str`, which accepts `""` — and `_pr_terminal_state`
        # STRIPS it, so `""` is not in ("open", "opened"), the liveness
        # short-circuit does not fire, and a truthy `mergedAt` then marks a LIVE
        # PR terminal. Every title/head match on the BLOCKING open-PR surface
        # goes out as `weak` and the run reports CLEAN.
        #
        # A blank `state` is not a state either transport produces, so it is a
        # malformed payload rather than a value, and the refusal has to catch it
        # where the null case is caught.
        self.gh_fixtures(open_prs=[{
            "number": 5150, "title": f"guard retrieval ({ISSUE})", "body": "",
            "headRefName": "feat/9999-other", "state": "",
            "mergedAt": "2026-09-01T00:00:00Z",
        }])
        rc, out = self.run_tool()
        self.assertEqual(rc, 2, out)
        self.assertIn("blank or non-string 'state'", out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        # The counterfactual, stated as an assertion: this exact fixture WITHOUT
        # the blank-state refusal is CLEAN, because the truthy `mergedAt` demotes
        # the hit. Pin it so a future relaxation has to delete this line.
        self.assertNotIn("VERDICT: CLEAN", out)

    def test_pr_payload_missing_number_is_incomplete_not_clean(self):
        # `number` is the field the hit test compares against the issue
        # (`str(pr.get("number")) == str(issue)`), so its absence is not a
        # cosmetic `?` in the label: the "this PR IS the issue" demotion stops
        # matching. Both transports project it, so absence is malformed.
        self.gh_fixtures(open_prs=[{
            "number": 5150, "title": "unrelated", "body": "",
            "headRefName": "feat/9999-other", "state": "open",
            "mergedAt": None,
        }])
        prs = json.loads((self.gh_dir / "open_prs.json").read_text())
        del prs[0]["number"]
        (self.gh_dir / "open_prs.json").write_text(json.dumps(prs))
        rc, out = self.run_tool()
        self.assertEqual(rc, 2, out)
        self.assertIn("no 'number'", out)
        self.assertIn("VERDICT: INCOMPLETE", out)

    def test_pr_payload_missing_state_is_incomplete_not_clean(self):
        # ⛔ THE VERIFIED FAIL-OPEN. `state` is what `_pr_terminal_state` reads to
        # decide a PR is still LIVE: `state in ("open", "opened")` returns None
        # (not terminal) before `mergedAt` is consulted. So a payload with NO
        # `state` and any truthy `mergedAt` was read as MERGED, and every match on
        # the BLOCKING open-PR surface went out as `weak` → CLEAN. Both transports
        # always send `state`, so its absence is malformation and must be a
        # refusal, not an accidental non-terminal read.
        self.gh_fixtures(open_prs=[{
            "number": 5150, "title": f"guard retrieval ({ISSUE})",
            "body": "", "headRefName": "feat/9999-other", "state": "open",
        }])
        # DELETED AFTER the fixture is written, because the normalizer supplies
        # the key — writing it through `gh_fixtures` and deleting from the tmp
        # profile is the same shape the missing-`closingIssuesReferences` test
        # uses, and the only way to model a payload that omits it.
        prs = json.loads((self.gh_dir / "open_prs.json").read_text())
        del prs[0]["state"]
        (self.gh_dir / "open_prs.json").write_text(json.dumps(prs))
        rc, out = self.run_tool()
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertIn("no 'state'", out)
        self.assertNotIn("VERDICT: CLEAN", out)

    def test_open_pr_with_null_state_and_truthy_merged_at_is_incomplete(self):
        # The exact payload the review reproduced: a VALID-looking open PR whose
        # `state` is null and whose `mergedAt` is truthy. It must be REFUSED, not
        # demoted — `mergedAt` is only trustworthy once `state` has said the PR is
        # not open, and a null `state` cannot say that.
        # A null `state` is not a string, so the type check refuses it whatever
        # `mergedAt` says — which is the point: `mergedAt` is only meaningful
        # once `state` has said the PR is not open, and null cannot say that.
        self.gh_fixtures(open_prs=[{
            "number": 5150, "title": f"guard retrieval ({ISSUE})",
            "body": "", "headRefName": "feat/9999-other",
            "state": "open", "mergedAt": None,
        }])
        prs = json.loads((self.gh_dir / "open_prs.json").read_text())
        prs[0]["state"] = None
        prs[0]["mergedAt"] = "2026-09-01T00:00:00Z"   # well-formed, and truthy
        (self.gh_dir / "open_prs.json").write_text(json.dumps(prs))
        rc, out = self.run_tool()
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertIn("non-string 'state'", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        # `mergedAt` gates a DOWNGRADE, so a non-string one (all of these are
        # truthy) must be refused rather than read as a merge timestamp.
        for merged in (1, {"any": "object"}, ["x"], True):
            with self.subTest(merged_at=merged):
                self.gh_fixtures(open_prs=[{
                    "number": 5150, "title": f"guard retrieval ({ISSUE})",
                    "body": "", "headRefName": "feat/9999-other",
                    "state": "open", "mergedAt": merged,
                }])
                rc, out = self.run_tool()
                self.assertEqual(rc, 2, out)
                self.assertIn("VERDICT: INCOMPLETE", out)
                self.assertNotIn("VERDICT: CLEAN", out)

    def test_pr_payload_missing_merged_at_is_incomplete_not_clean(self):
        # ⛔ THE CONTRACT SAID "REQUIRED AND NULLABLE" AND THE CODE ACCEPTED
        # ABSENCE. `mergedAt` is the key the squash-merge harvest is GATED on
        # (`if _pr.get("merged_at") or _pr.get("mergedAt")`), so a payload that
        # omits it contributes NO head SHA — the #5186 demotion silently stops
        # firing for that PR and the branch stays blocking. Fail-closed, so not a
        # safety hole, but it is a written contract the code did not implement,
        # in the function written specifically to stop exactly that.
        # On the BLOCKING open-PR surface, where a malformed payload is a
        # NAMED INCOMPLETE and exit 2.
        self.gh_fixtures(open_prs=[{
            "number": 5150, "title": "unrelated", "body": "",
            "headRefName": "feat/9999-other", "state": "open",
            "mergedAt": "2026-09-01T00:00:00Z",
        }])
        prs = json.loads((self.gh_dir / "open_prs.json").read_text())
        del prs[0]["mergedAt"]
        (self.gh_dir / "open_prs.json").write_text(json.dumps(prs))
        rc, out = self.run_tool()
        self.assertEqual(rc, 2, out)
        self.assertIn("no 'mergedAt'", out)
        self.assertIn("VERDICT: INCOMPLETE", out)

        # On the ADVISORY closed-PR surface the SAME refusal is still stated, but
        # #5251 keeps it out of the exit code: the surface cannot conceal a
        # collision. Asserting both halves is what distinguishes "the guard
        # fired" from "the guard fired AND the authority split still holds".
        self.gh_fixtures(closed_prs=[{
            "number": 4242, "title": "land it", "body": "", "state": "closed",
            "headRefName": "feat/9999-other",
            "headSha": "0" * 40, "mergedAt": "2026-09-01T00:00:00Z",
        }])
        prs = json.loads((self.gh_dir / "closed_prs.json").read_text())
        del prs[0]["mergedAt"]
        (self.gh_dir / "closed_prs.json").write_text(json.dumps(prs))
        (self.gh_dir / "open_prs.json").write_text(json.dumps([]))
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("no 'mergedAt'", out)
        self.assertIn("ADVISORY SURFACES", out)
        self.assertNotIn("VERDICT: INCOMPLETE", out)

    def test_pr_payload_missing_body_is_incomplete_not_clean(self):
        # ⛔ `body` is READ on the blocking open-PR surface: the body-regex union
        # exists precisely because GitHub's `closingIssuesReferences` can miss a
        # reference the body states plainly. Reading a MISSING body as "no body"
        # drops that leg silently, which is the absence-as-emptiness drop the
        # contract exists to prevent. An unrelated title/branch plus a reference
        # only in the body is how it fails open.
        self.gh_fixtures(open_prs=[{
            "number": 5150, "title": "unrelated title",
            "body": f"Closes #{ISSUE}.",   # the ONLY reference is in the body
            "headRefName": "feat/9999-other", "state": "open",
        }])
        prs = json.loads((self.gh_dir / "open_prs.json").read_text())
        del prs[0]["body"]
        (self.gh_dir / "open_prs.json").write_text(json.dumps(prs))
        rc, out = self.run_tool()
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertIn("no 'body'", out)
        self.assertNotIn("VERDICT: CLEAN", out)

    def test_worktree_porcelain_without_a_branch_line_is_refused(self):
        # ⛔ THE SHAPE CHECK COVERED TWO OF THE THREE FIELDS THE HIT TEST READS.
        # `path` was required, HEAD-or-`bare` was required, and `branch` was not
        # — yet `scan_worktree_surface` tests `number_present(path, issue) or
        # number_present(branch, issue)`, so a record with no `branch` line was
        # accepted, the field read as the EMPTY string, and a worktree whose ONLY
        # issue-naming field is its branch was reported as no hit at all. That is
        # the silently-smaller answer this check exists to refuse.
        mod = _tool_module()
        with self.assertRaises(mod.SurfaceError):
            mod._worktree_blocks(
                "worktree /tmp/wt/fix-3061-mine\n"
                "HEAD 0123456789abcdef0123456789abcdef01234567\n"
            )
        # A `bare` record legitimately has neither, so it must still parse.
        self.assertEqual(
            len(mod._worktree_blocks("worktree /tmp/wt/bare\nbare\n")), 1
        )

    def test_unattributable_assignee_is_a_blocking_hit(self):
        # ⛔ THE ARM WITH NO COVERAGE, and it is the FAIL-CLOSED one. The claim
        # gate deliberately moved the SHARED account to advisory (#3504 class 3),
        # and kept the UNATTRIBUTABLE case blocking — "we cannot tell" is not
        # "it is not ours". Nothing exercised it: every fixture supplied a
        # concrete login, so folding this branch into `shared` (an easy
        # refactor) turned a blocking assignee into a non-blocking one with the
        # suite still green.
        for login in ("ghost", None):
            with self.subTest(login=login):
                self.gh_fixtures(issue=self.issue_payload(assignees=(login,)))
                rc, out = self.run_tool()
                self.assertNotEqual(rc, 0, out)
                self.assertIn("VERDICT: COLLISION", out)
                self.assertIn("could not be attributed", out)

    def test_worktree_porcelain_with_a_newline_in_the_path_is_refused(self):
        # ⛔ THE PARSER SILENTLY TRUNCATED. `git worktree list --porcelain` does
        # not C-quote a newline in a path — it writes the raw byte — so a worktree
        # at `<tmp>/wt/fix\n3061-mine` arrives as `worktree <tmp>/wt/fix` plus a
        # bare `3061-mine`. The old parser ignored the second line, the path was
        # truncated BEFORE the issue number, the worktree stopped matching, and
        # the surface silently reported one fewer worktree with no error at all.
        # The call-site guard could not catch it: it compared `len(blocks)` with
        # the number of `worktree ` lines, and the parser created one block per
        # such line, so the two were equal by construction.
        mod = _tool_module()
        good = (
            "worktree /tmp/wt/fix-3061-mine\n"
            "HEAD 0123456789abcdef0123456789abcdef01234567\n"
            "branch refs/heads/fix/3061-mine\n\n"
            "worktree /tmp/wt/other\n"
            "HEAD abcdef0123456789abcdef0123456789abcdef01\n"
            "detached\n\n"
        )
        self.assertEqual(len(mod._worktree_blocks(good)), 2)
        # The SAME input with a newline inside the first path.
        bad = good.replace("/tmp/wt/fix-3061-mine",
                           "/tmp/wt/fix\n3061-mine")
        with self.assertRaises(mod.SurfaceError):
            mod._worktree_blocks(bad)
        # And a record that parses to no HEAD at all is refused too, so the
        # "profitable" truncation cannot simply drop the field.
        with self.assertRaises(mod.SurfaceError):
            mod._worktree_blocks("worktree /tmp/wt/x\nbranch refs/heads/x\n")

    def test_non_list_closing_reference_container_is_incomplete(self):
        # ⛔ `_closing_ref_numbers`'s non-list guard was verified by nothing: the
        # fixtures supplied the field absent, `[]`, or a list of bad elements —
        # never a bad CONTAINER. Replacing the guard with `return []` passed all
        # four closing-reference tests, and that mutation is a genuine FAIL-OPEN:
        # a field GitHub returned in an unreadable shape would be read as
        # "closes nothing" and the surface would report CLEAN on real work.
        for bad in ("3061", {}, 5, True):
            with self.subTest(container=bad):
                self.gh_fixtures(open_prs=[{
                    "number": 5150, "title": "unrelated", "body": "",
                    "headRefName": "feat/9999-other", "state": "open",
                    "closingIssuesReferences": bad,
                }])
                rc, out = self.run_tool()
                self.assertEqual(rc, 2, f"container={bad!r}\n{out}")
                self.assertIn("VERDICT: INCOMPLETE", out)
                self.assertIn("closing-reference-source-unavailable", out)
                self.assertNotIn("VERDICT: CLEAN", out)

    def test_branch_still_blocks_when_the_closed_pr_sample_fails(self):
        # ⛔ THE ONE PLACE THE ADVISORY/BLOCKING SPLIT IS NOT STRUCTURAL, pinned
        # so it cannot drift. `merged_heads` — the squash-merge test's ONLY
        # evidence — is harvested from the ADVISORY closed-PR sample and consumed
        # by the BLOCKING branch scan. That is deliberate (D4: the sample is
        # already fetched), and it is safe because it can only ever REMOVE a hit:
        # when the sample cannot be read, the map stays EMPTY and every branch
        # stays BLOCKING.
        #
        # So the failure direction is fail-CLOSED, and this asserts it: a branch
        # named after the issue, with a malformed closed-PR sample, must still
        # COLLIDE. If a later edit ever made advisory data ADD a hit, or made a
        # failed sample suppress one, this is the test that goes red.
        _git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        _git(self.repo, "branch", f"fix/{ISSUE}-mine")
        # A malformed element: `_require_pr_dicts` refuses it, so the harvest
        # never runs and `merged_heads` stays empty. Written DIRECTLY,
        # because the fixture normalizer itself rejects a non-object element.
        (self.gh_dir / "closed_prs.json").write_text(json.dumps([None]))
        rc, out = self.run_tool(issue=ISSUE)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("do NOT dispatch", out)
        self.assertNotIn("squash-merged", out)

    def test_malformed_open_pr_element_is_incomplete_not_a_traceback(self):
        # ⛔ A list holding a non-object used to CRASH with no VERDICT line and
        # exit 1 — the code this tool documents as COLLISION. A caller reads exit
        # 1 as "another lane is on it" and waits for a lane that does not exist,
        # so a malformed payload was indistinguishable from real contention. The
        # container was type-checked and its ELEMENTS were not.
        (self.gh_dir / "open_prs.json").write_text(json.dumps([None]))
        rc, out = self.run_tool()
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertIn("non-object element at index 0", out)
        self.assertNotIn("Traceback", out)

    def test_malformed_closed_pr_element_is_named_not_a_traceback(self):
        # The closed surface is ADVISORY, so a malformed element cannot make the
        # run INCOMPLETE — but it must still be NAMED and must not traceback.
        # (Before the fix, the harvest loop hit `.get` on `None` and the process
        # died with exit 1 and no verdict.)
        (self.gh_dir / "closed_prs.json").write_text(json.dumps([None]))
        self.gh_fixtures(open_prs=[])
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertIn("non-object element at index 0", out)
        self.assertNotIn("Traceback", out)

    def test_closed_pr_prose_without_closing_keyword_is_not_a_hit(self):
        # "fixed in #3061" / "see #3061" are prose, not closing keywords: the
        # fallback regex requires the keyword IMMEDIATELY before `#N`.
        #
        # ⛔ IT USED TO ASSERT NOTHING ITS NAME CLAIMED, and a verifier proved it
        # by loosening the regex (`closing_reference` -> `number_present`) and
        # watching the test still pass. `rc == 0` is vacuous on this surface now
        # that it is ADVISORY — an advisory surface can never set exit 1 — and
        # "do NOT dispatch" only ever appears on the COLLISION verdict line, so
        # neither assertion could observe a hit at all. Assert the PROPERTY, plus
        # the positive half so the test cannot pass by the surface not running.
        for body in ("fixed in #3061", "see #3061 for context", "restored in #3061"):
            self.gh_fixtures(closed_prs=[{
                "number": 9996, "title": "unrelated title",
                "body": body, "headRefName": "chore/9996-unrelated",
                "state": "closed",
            }])
            rc, out = self.run_tool()
            self.assertEqual(rc, 0, f"body={body!r}\n{out}")
            self.assertNotIn("do NOT dispatch", out)
            # The property: no CLOSING-REFERENCE hit was derived from prose...
            self.assertNotIn("closing reference to #3061", out)
            # ...while the body WAS read and reported as a weak prose mention.
            self.assertIn("prose mention of #3061", out)

    def test_number_inside_a_hex_digest_is_not_a_reference(self):
        # #4935 / #3611: a review-signature value is hex, so every 4-digit
        # substring occurs inside it by chance. The SHAPE of the containing run
        # decides it — no vocabulary, no stop-word list.
        #
        # ⛔ THE GUARD MUST BE TESTED WHERE IT CAN MATTER. An earlier version put
        # the digest in the PR BODY, where a number match is only a WEAK,
        # non-blocking prose signal — so every assertion passed with
        # `_inside_hex_digest` monkeypatched to `return False`. The test could
        # not fail. The digest now sits in the TITLE, which is a STRONG path:
        # delete the guard and the title match makes this a COLLISION. The body
        # is asserted separately, so both paths are pinned.
        for digest in ("3f1a4889d6a3061b2e0c7f9a1d4b8e2c5a3f6d9b0e1c4a7f2b5d8e1a4c7f0",
                       "sig=deadbeef3061cafe",
                       "a3061bcd"):
            with self.subTest(digest=digest):
                self.gh_fixtures(open_prs=[{
                    "number": 9995,
                    "title": f"chore: re-attest the review {digest}",
                    "body": f"attestation {digest}",
                    "headRefName": "chore/9995-attest",
                    "state": "open",
                }])
                rc, out = self.run_tool()
                self.assertEqual(rc, 0, f"digest={digest!r}\n{out}")
                self.assertIn("VERDICT: CLEAN", out)
                self.assertNotIn("do NOT dispatch", out)
                # The guard FIRED on the STRONG path: the number IS present in
                # the fixture, so a green run cannot be a fixture artefact.
                self.assertNotIn("matched issue-number (3061)", out)
                # ...and the body path is pinned too: the digest must not even
                # produce a weak prose signal.
                self.assertNotIn("prose mention of #3061", out)

    def test_number_after_a_non_hex_letter_still_matches(self):
        # The guard must not over-fire. `w3061` is a reference: `w` is not a hex
        # digit, so the run is the bare `3061` — four characters, not a digest.
        for head in ("fix/w3061-bare-run", "fix/3061-x", "fix/x3061"):
            with self.subTest(head=head):
                self.gh_fixtures(open_prs=[{
                    "number": 9994, "title": "unrelated", "body": "",
                    "headRefName": head, "state": "open",
                }])
                rc, out = self.run_tool()
                self.assertNotEqual(rc, 0, f"head={head!r}\n{out}")
                self.assertIn("VERDICT: COLLISION", out)

    def test_live_branch_whose_number_leads_a_hex_run_still_collides(self):
        # REVIEW CYCLE 1 REPRODUCED A FAIL-OPEN BYPASS HERE. A shape-only guard
        # (>= 8 hex chars containing a letter) swallowed `fix/3061cafe` as a
        # digest while `w3061` matched: `cafe` is a word, and dropping the
        # separator must not hide a LIVE branch naming #3061. The fix is
        # POSITION — a number leading the run is a reference, a number embedded
        # mid-run is a fragment — which is strictly more fail-closed, because
        # the discarded case is now the blocking one.
        for head in ("fix/3061cafe", "fix/3061abcd", "fix/3061beef",
                     "fix/beef3061", "fix/facade3061", "fix/abcd3061"):
            with self.subTest(head=head):
                _git(self.repo, "branch", head)
                rc, out = self.run_tool()
                self.assertNotEqual(rc, 0, f"{head}\n{out}")
                self.assertIn("VERDICT: COLLISION", out)
                self.assertIn("[local branches]", out)

    def test_open_pr_with_a_stray_merged_timestamp_stays_a_hit(self):
        # Hardening from review cycle 1: `state` is authoritative for liveness,
        # so an OPEN PR is never terminal even if a payload carries a truthy
        # `mergedAt`. No real transport does this (every open PR observed
        # carries `mergedAt: null`), but the polarity must fail CLOSED.
        self.gh_fixtures(open_prs=[{
            "number": 9993, "title": "guard retrieval (#3061)", "body": "",
            "headRefName": "fix/guard", "state": "open",
            "mergedAt": "2026-09-23T03:45:47Z",
        }])
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertNotIn("immutable history", out)

    def test_bad_timeout_and_limit_seams_are_usage_not_collision(self):
        # #3619 / #4053. The old eager `float(os.environ[...])` / `int(...)` ran
        # at add_argument time, so a typo'd env value raised an uncaught
        # ValueError -> traceback + exit 1, which is EXIT_COLLISION: a
        # misconfiguration read as "another lane is on it".
        #
        # The message must NAME the seam, not just exit 3: the operator set an
        # env var, and "--timeout must be a number > 0" is what tells them the
        # variable they exported is the one being rejected (the env name is
        # documented on the flag's own `help=`). And no `VERDICT` may appear at
        # all — a usage failure is a fact about the INVOCATION, so printing a
        # verdict would assert something no surface was queried to produce.
        # `nan`/`inf`/`1.5` are the trap values: `nan <= 0` and `inf <= 0` are
        # both False, so a bare positivity check passes the timeouts through to
        # `subprocess.run(timeout=…)` and lets a fractional limit reach `int()`.
        env_seams = (
            ("COLLISION_PREFLIGHT_TIMEOUT", "--timeout",
             ("abc", "nan", "inf", "", "0", "-1", "60s")),
            ("COLLISION_PREFLIGHT_PR_LIMIT", "--pr-limit",
             ("abc", "nan", "inf", "1.5", "", "0", "-1", "60s")),
            ("COLLISION_PREFLIGHT_CLOSED_PR_LIMIT", "--closed-pr-limit",
             ("abc", "nan", "inf", "1.5", "", "0", "-1", "60s")),
        )
        for env, flag, values in env_seams:
            for value in values:
                with self.subTest(env=env, value=value):
                    rc, out = self.run_tool(env_extra={env: value})
                    self.assertEqual(rc, 3, f"{env}={value!r}\n{out}")
                    self.assertIn(flag, out, f"{env}: message does not name {flag}")
                    self.assertNotIn("Traceback", out)
                    self.assertNotIn("VERDICT", out)

    def test_bad_timeout_flag_is_usage_not_collision(self):
        for bad in ("abc", "nan", "inf", "0", "-1", "60s"):
            with self.subTest(bad=bad):
                rc, out = self.run_tool(extra_args=[f"--timeout={bad}"])
                self.assertEqual(rc, 3, f"{bad}: {out}")
                self.assertIn("--timeout", out)
                self.assertNotIn("Traceback", out)

    def test_bad_limit_flags_are_usage_not_collision(self):
        # #3619 acceptance, the CLI half: "a test per flag per form (CLI +
        # env)". The env form of these two seams is pinned above; the CLI form
        # was left uncovered by the #5129 merge.
        #
        # What this pins is the SEMANTIC arm of the validator, not argparse.
        # `0` and `-1` are the load-bearing cases: both are syntactically valid
        # integers, so argparse accepts them (with or without `type=int`) and
        # only the explicit `value < 1` arm rejects them — measured: deleting
        # that arm turns this test red on exactly those four subtests, because a
        # zero or negative cap is otherwise not a usage error at all, just a
        # silently different measurement.
        #
        # By contrast `abc` / `nan` / `inf` / `1.5` / `""` would STILL exit 3
        # even if the validator were bypassed, because argparse's own error path
        # goes through `_UsageParser.error`, which exits EXIT_USAGE (measured
        # with `type=int` reintroduced). Passing on those alone proves nothing.
        #
        # The empty form (`--pr-limit=`) is included deliberately: it is the
        # CLI analogue of the empty-forwarded-variable case that motivates the
        # issue, and it is the one an `or default` idiom would silently swallow.
        for flag in ("--pr-limit", "--closed-pr-limit"):
            for bad in ("abc", "nan", "inf", "1.5", "", "0", "-1", "60s"):
                with self.subTest(flag=flag, bad=bad):
                    rc, out = self.run_tool(extra_args=[f"{flag}={bad}"])
                    self.assertEqual(rc, 3, f"{flag}={bad!r}\n{out}")
                    self.assertIn(flag, out, f"{flag}: message does not name it")
                    self.assertNotIn("Traceback", out)
                    self.assertNotIn("VERDICT", out)

    def test_remote_branch_hit(self):
        _git(self.repo, "update-ref", "refs/remotes/origin/fix/3061-collision", "HEAD")
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("[remote branches]", out)
        self.assertIn("refs/remotes/origin/fix/3061-collision", out)

    def test_merge_queue_hash_branch_is_not_a_claim(self):
        # #3611 (escalation to a BLOCKING surface): `mergify/merge-queue/<hash>`
        # is a GENERATED branch — created and deleted by the merge queue for
        # each queued PR — so its name is a hash that can begin with the issue
        # number. Measured instance: `mergify/merge-queue/6160bad001` made
        # `collision_preflight 6160` report exit 1 with that queue branch as its
        # ONLY hit, so an issue nobody held was reported as held and dropped
        # from the queue.
        #
        # The number here LEADS the hex run, which the digest guard deliberately
        # does NOT exclude (`3061cafe` must stay a live reference), so ORIGIN is
        # the only thing that can separate these two cases — which is why the
        # fix tests the namespace rather than loosening the number rule.
        _git(self.repo, "update-ref",
             "refs/remotes/origin/mergify/merge-queue/3061bad001", "HEAD")
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertNotIn("do NOT dispatch", out)
        # The ref WAS present, so a green run is not a fixture artefact.
        self.assertNotIn("mergify/merge-queue", out)

    def test_lane_branch_leading_a_hex_run_still_collides(self):
        # Control for the test above: excluding a GENERATED namespace must not
        # exclude a lane's branch that merely LOOKS hex-ish. `3061cafe` is a
        # name a lane can write, so it must remain a blocking hit — this pins
        # the fix against being widened into "nothing hex-looking counts".
        _git(self.repo, "update-ref",
             "refs/remotes/origin/fix/3061cafe", "HEAD")
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("refs/remotes/origin/fix/3061cafe", out)

    def test_a_lane_branch_under_mergify_not_merge_queue_still_collides(self):
        # ⛔ THE WIDTH OF THE FILTER IS A FAIL-CLOSED DECISION. A lane CAN create
        # `mergify/<issue>-name` locally, and any namespace wider than
        # `mergify/merge-queue/` renders that branch invisible on a BLOCKING
        # surface: a false CLEAN, which for this tool is strictly worse than a
        # false COLLISION. This pins the narrower namespace so the filter cannot
        # be widened again without a decision.
        for ref in (f"mergify/{ISSUE}-lane", f"mergify/{ISSUE}-lane-local"):
            with self.subTest(ref=ref):
                _git(self.repo, "update-ref", f"refs/remotes/origin/{ref}", "HEAD")
                rc, out = self.run_tool()
                self.assertNotEqual(rc, 0, f"ref={ref!r}\n{out}")
                self.assertIn("VERDICT: COLLISION", out)
                self.assertIn(f"refs/remotes/origin/{ref}", out)

    def test_second_mergify_namespace_and_ci_bot_branch_are_not_claims(self):
        # Two further generated namespaces exist in this repo's real refs.
        # `mq/merge-queue/` is a second Mergify merge-queue namespace;
        # `chore/ci-timing-refresh-` is minted by this repo's own workflow from
        # `git rev-parse --short HEAD`, so a short SHA can LEAD with an issue
        # number exactly like the hash this filter exists for.
        for ref in (f"mq/merge-queue/{ISSUE}bad001",
                    f"chore/ci-timing-refresh-{ISSUE}bad001"):
            with self.subTest(ref=ref):
                _git(self.repo, "update-ref", f"refs/remotes/origin/{ref}", "HEAD")
                rc, out = self.run_tool()
                self.assertEqual(rc, 0, f"ref={ref!r}\n{out}")
                self.assertIn("VERDICT: CLEAN", out)
                self.assertNotIn("do NOT dispatch", out)
                self.assertNotIn(ref, out)

    def test_worktree_on_a_generated_branch_is_still_found_by_its_path(self):
        # ⛔ THE WORKTREE HALF IS THE LOAD-BEARING ONE. The filter skips only the
        # BRANCH; the PATH must still be matched in full, or a real worktree on a
        # generated branch becomes invisible on a BLOCKING surface.
        self.add_worktree(f"{ISSUE}-queue-wt",
                          branch=f"mergify/merge-queue/{ISSUE}bad001")
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("[local worktrees]", out)
        self.assertIn(f"{ISSUE}-queue-wt", out)

    def test_ci_timing_branch_with_a_descriptive_tail_is_still_a_claim(self):
        # `chore/ci-timing-refresh-` sits under the LANE-OWNED `chore/`
        # namespace, so the bare prefix would be too wide — a lane could write a
        # descriptive tail. The generator mints a lowercase short SHA, so the
        # tail is anchored to lowercase hex; anything else stays a claim, because
        # hiding one on a BLOCKING surface is worse than a false COLLISION.
        for ref in (f"chore/ci-timing-refresh-{ISSUE}-manual",
                    f"chore/ci-timing-refresh-{ISSUE}",
                    f"chore/ci-timing-refresh-{ISSUE}BAd001"):
            with self.subTest(ref=ref):
                _git(self.repo, "update-ref", f"refs/remotes/origin/{ref}", "HEAD")
                rc, out = self.run_tool()
                self.assertNotEqual(rc, 0, f"ref={ref!r}\n{out}")
                self.assertIn("VERDICT: COLLISION", out)
                self.assertIn(ref, out)

    def test_local_branch_hit(self):
        _git(self.repo, "branch", "fix/3061-collision-preflight")
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("[local branches]", out)
        self.assertIn("refs/heads/fix/3061-collision-preflight", out)

    def test_worktree_hit_untruncated(self):
        # The hit sorts FIRST among worktrees; 8 later worktrees follow it, so
        # a `git worktree list | tail -8` window (the #3061 failure mode) hides
        # it. The tool must still find it and report the full enumeration count.
        self.add_worktree("000-w3061-hit")
        for i in range(8):
            self.add_worktree(f"zzz-{i:02d}-unrelated")
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("[local worktrees]", out)
        self.assertIn("000-w3061-hit", out)
        # 1 main worktree + 1 hit + 8 fillers = 10, all enumerated.
        self.assertIn("10 worktree(s) enumerated (untruncated)", out)

    # ── #9540: a PATH SUBSTRING is a spelling, not a claim ──────────────────

    def test_worktree_path_substring_with_a_foreign_branch_is_not_a_claim(self):
        # #9540, measured on the real fleet: `/tmp/wt-3718` held lane work for
        # #9397 (`fix/9397-about-edge-properties`) and matched #3718 only
        # because the DIRECTORY NAME contains it. The gate has no dismissal
        # switch by design (#8175), so a stale directory name froze the highest
        # value un-gated product defect permanently. The branch is the field a
        # lane deliberately named; the path is a leftover.
        self.add_worktree(f"wt-{ISSUE}-stale-name",
                          branch="fix/9397-about-edge-properties")
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertNotIn("do NOT dispatch", out)
        # Reported, never silent: the reader must see WHAT was demoted and why.
        self.assertIn(f"wt-{ISSUE}-stale-name", out)
        self.assertIn(f"its PATH contains {ISSUE} but its BRANCH claims #9397", out)
        self.assertIn("[local worktrees]", out)

    def test_worktree_path_substring_still_blocks_when_the_branch_names_this_issue(self):
        # CONTROL for the test above: the demotion must fire ONLY when the branch
        # names something else. A branch that names THIS issue is the claim, and
        # the path merely agrees with it.
        #
        # ⛔ ROW-SCOPED (review cycle 4): `add_worktree(branch=...)` ALSO creates a
        # local branch `fix/3061-lane`, and the branch surface blocks it — so a
        # whole-output assertion was satisfied by the BRANCH row even if the
        # WORKTREE row had been demoted. The label is unique per row, so this
        # asserts the row under test.
        branch = f"fix/{ISSUE}-lane"
        name = f"wt-{ISSUE}-stale-name"
        self.add_worktree(name, branch=branch)
        rc, out = self.run_tool()
        label = f"{name} [refs/heads/{branch}]"
        line = next((row for row in out.splitlines() if label in row), "")
        self.assertTrue(line, f"no row for {label!r}\n{out}")
        self.assertIn(f"matched issue-number ({ISSUE})", line)
        self.assertNotIn("its PATH contains", line)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)

    def test_worktree_path_substring_still_blocks_for_a_non_claim_branch_name(self):
        # ⛔ THE NARROWNESS IS THE SAFETY PROPERTY. Only the shape a lane uses to
        # CLAIM an issue (`fix/<n>-<slug>` / `feat/<n>-<slug>`) can contradict
        # the path. A number that is not a claim — a date, a version, a metric —
        # must not cancel it, or a reused worktree whose branch says
        # `chore/bump-2024-10` would read a live lane as free.
        #
        # ⛔ EVERY CASE IS ASSERTED ON ITS OWN LINE, because worktrees ACCUMULATE
        # in this fixture repo: a cumulative `assertNotEqual(rc, 0)` passed off an
        # EARLIER subtest's blocking hit, so the widening it was supposed to catch
        # was invisible (found in #9540's review cycle 2). The label line is
        # unique per case, so this pins the CURRENT one.
        for i, branch in enumerate(("chore/bump-2024-10", "release-1.2.3",
                                    "perf/1200-rps-cache", "ci/1800-timeout",
                                    "chore/5000-graph-cleanup",
                                    f"feat/{ISSUE}0-lookalike",
                                    "feat/2048-batch")):
            with self.subTest(branch=branch):
                name = f"wt-{ISSUE}-nonclaim-{i}"
                self.add_worktree(name, branch=branch)
                rc, out = self.run_tool()
                label = f"{name} [refs/heads/{branch}]"
                line = next((row for row in out.splitlines() if label in row), "")
                self.assertTrue(line, f"no row for {label!r}\n{out}")
                self.assertIn(f"matched issue-number ({ISSUE})", line)
                self.assertNotIn("its PATH contains", line)
                self.assertNotEqual(rc, 0, f"branch={branch!r}\n{out}")
                self.assertIn("VERDICT: COLLISION", out)

    def test_foreign_claim_branch_that_also_MENTIONS_this_issue_still_blocks(self):
        # ⛔ THIS PINS THE `not branch_hit` CONJUNCT, which nothing else did
        # (found in #9540 review cycle 3). A branch can name a foreign issue in
        # the CLAIM position and still mention this one in its slug —
        # `fix/9397-depends-on-3061`. The loose `number_present(branch, …)` arm is
        # what makes that a hit today, and `_branch_names_other_issue` alone
        # would answer 9397. So if a later reader decides the conjunct is
        # redundant (it LOOKS redundant: the helper already refuses a branch that
        # names this issue) and deletes it, this worktree is demoted and the lane
        # that names 3061 goes free — a FALSE CLEAN, the dangerous direction.
        branch = f"fix/9397-depends-on-{ISSUE}"
        name = f"wt-{ISSUE}-mention"
        self.add_worktree(name, branch=branch)
        rc, out = self.run_tool()
        label = f"{name} [refs/heads/{branch}]"
        line = next((row for row in out.splitlines() if label in row), "")
        self.assertTrue(line, f"no row for {label!r}\n{out}")
        self.assertIn(f"matched issue-number ({ISSUE})", line)
        self.assertNotIn("its PATH contains", line)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)

    def test_generated_branch_with_a_claim_shaped_tail_still_blocks(self):
        # ⛔ THE GENERATED EXCLUSION APPLIES TO BOTH HALVES (review cycle 4).
        # `branch_matchable` gated the HIT test only, so a bot-minted name with a
        # claim-shaped tail (`mergify/merge-queue/fix/9397-x`) had
        # `branch_hit = False` by construction and the helper read its raw name
        # and answered 9397 — demoting the path hit while the docstring promised
        # the PATH still blocks. `scan_branch_surface` skips the same ref, so
        # nothing else would have blocked this worktree.
        branch = "mergify/merge-queue/fix/9397-x"
        name = f"wt-{ISSUE}-generated-tail"
        self.add_worktree(name, branch=branch)
        rc, out = self.run_tool()
        label = f"{name} [refs/heads/{branch}]"
        line = next((row for row in out.splitlines() if label in row), "")
        self.assertTrue(line, f"no row for {label!r}\n{out}")
        self.assertIn(f"matched issue-number ({ISSUE})", line)
        self.assertNotIn("its PATH contains", line)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)

    def test_a_branch_number_that_is_not_the_issue_does_not_cancel_the_path(self):
        # `fix/3061cafe-mine` NAMES this issue (`number_present` matches 3061 —
        # the digits LEAD a hex-looking run, #3611), so the branch itself is the
        # claim and the path hit stands. Same line-scoped assertion as above.
        branch = f"fix/{ISSUE}cafe-mine"
        name = f"wt-{ISSUE}-lead-hex"
        self.add_worktree(name, branch=branch)
        rc, out = self.run_tool()
        label = f"{name} [refs/heads/{branch}]"
        line = next((row for row in out.splitlines() if label in row), "")
        self.assertTrue(line, f"no row for {label!r}\n{out}")
        self.assertIn(f"matched issue-number ({ISSUE})", line)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)

    def test_worktree_path_substring_demotes_for_any_other_issue_claim(self):
        # The demotion is a POSITIVE rule, not a special case for one example:
        # any `fix/<n>-<slug>` naming a DIFFERENT issue contradicts the path, even
        # a LOOKALIKE number (`fix/30611-…` claims 30611, not 3061). Pinned
        # per-line, plus `rc == 0`, so the strength cannot be flipped silently.
        for i, branch in enumerate(("fix/9397-about-edge-properties",
                                    f"fix/{ISSUE}1-lookalike")):
            with self.subTest(branch=branch):
                name = f"wt-{ISSUE}-claim-{i}"
                self.add_worktree(name, branch=branch)
                rc, out = self.run_tool()
                label = f"{name} [refs/heads/{branch}]"
                line = next((row for row in out.splitlines() if label in row), "")
                self.assertTrue(line, f"no row for {label!r}\n{out}")
                self.assertIn("its PATH contains", line)
                self.assertIn("non-blocking", line)
                # a weak hit CANNOT contribute to the verdict — pin that too, or
                # flipping the strength to "strong" would keep these subtests
                # green (review cycle 3, secondary finding).
                self.assertEqual(rc, 0, out)
                self.assertIn("VERDICT: CLEAN", out)

    def test_branch_names_other_issue_reads_only_the_claim_shape(self):
        # Unit pin on the helper, so a later refactor of the worktree scan cannot
        # widen it unnoticed. Each case is a decision, not a spelling test.
        mod = _tool_module()
        cases = [
            ("fix/9397-about-edge-properties", 9397),
            ("refs/heads/fix/9397-about-edge-properties", 9397),
            # `feat/` is NOT a claim namespace (#9540 review cycle 4:
            # `feat/2048-batch` — a batch size, not an issue). Excluding it costs
            # a residual over-block, the safe direction.
            ("feat/9397-x", None),
            ("feat/2048-batch", None),
            # `test/` `docs/` `chore/` also carry `<n>-<slug>` names here, but in
            # those namespaces the number can be a metric or a size, and
            # EXCLUDING them costs only a residual over-block — the safe
            # direction — while including them costs a false CLEAN.
            ("test/9397-x", None),
            ("docs/9397-x", None),
            # the branch names THIS issue → no contradiction
            ("fix/3061-mine", None),
            # …ANYWHERE in the name, even after a foreign claim: the order the
            # two are found must not decide the verdict (fail-closed).
            ("feat/9397-x/fix/3061-y", None),
            # not a claim shape → a version/date is not an issue reference
            ("chore/bump-2024-10", None),
            ("release-1.2.3", None),
            # `3061cafe` is not the claim SHAPE (`<n>-<slug>`), so it cannot
            # contradict a path — and it names THIS issue anyway, so the path
            # keeps blocking either way (the fail-closed outcome).
            ("fix/3061cafe-mine", None),
            # generated worktree checkout, detached: nothing to contradict
            ("mergify/merge-queue/6160bad001", None),
            ("(detached)", None),
            ("", None),
            # this issue inside a hex digest is NOT a reference (#3611/#4935), so
            # the branch's foreign claim is the only claim present → demotes the
            # PATH hit, deliberately and documented in the helper's docstring.
            ("fix/9397-abc3061def", 9397),
        ]
        for branch, expected in cases:
            with self.subTest(branch=branch):
                self.assertEqual(
                    mod._branch_names_other_issue(branch, ISSUE), expected
                )

    # ── #6622: a RECORD of work is not a WORKER ─────────────────────────────

    def test_unconfigured_remote_namespace_is_a_fetch_cache_not_a_holder(self):
        # #6622 case 3. `git fetch <url> refs/pull/N/head:refs/remotes/pr/N`
        # writes a ref whose name is the CACHE KEY, and NO configured remote's
        # refspec targets `pr`, so the ref records that the PR head was FETCHED
        # — not that a lane holds the work. Measured on the real repo:
        # `refs/remotes/pr/*` holds 2,810 refs named `pr/1 … pr/2810`, every one
        # a PR number. This was a STRONG blocking hit even though the surface's
        # own note says a remote-tracking ref "is a local fetch cache" and
        # `_branch_terminal_state` refuses to judge one.
        #
        # ⛔ BOTH SHAPES ARE PINNED. `pr/3061` has a namespace segment and a
        # tail; `pr3061` (ten of which exist on the real repo — `pr2996` …
        # `pr5343`) is a SINGLE segment. A namespace+tail split demotes only the
        # first, which left the #6622 class half-closed for #2996/#5327/#5343.
        for ref in (f"refs/remotes/pr/{ISSUE}", f"refs/remotes/pr{ISSUE}"):
            with self.subTest(ref=ref):
                _git(self.repo, "update-ref", ref, "HEAD")
                rc, out = self.run_tool()
                self.assertEqual(rc, 0, out)
                self.assertIn("VERDICT: CLEAN", out)
                self.assertNotIn("do NOT dispatch", out)
                # The ref WAS present, so the green verdict is not a fixture
                # artefact: it is reported, at weak strength, with its basis.
                self.assertIn(ref, out)
                self.assertIn("fetch-cache ref", out)
                self.assertIn("fetch refspec targets", out)
                # …and the weak summary must NAME this category, not describe it
                # as "your own work" / "a terminal branch" — a report that
                # misdescribes what it measured is the class this fix removes.
                self.assertIn(
                    "a fetch-cache ref under a namespace no configured remote",
                    out,
                )

    def test_a_configured_remotes_branch_still_blocks(self):
        # CONTROL for the test above, and the guard rail the issue says NOT to
        # delete: `origin`'s refspec is
        # `+refs/heads/*:refs/remotes/origin/*`, so a ref there IS a real
        # remote-tracking BRANCH and must keep blocking. Widening the fix to
        # "any refs/remotes/* ref is a cache" would be a fail-OPEN on a blocking
        # surface — the dangerous direction.
        _git(self.repo, "update-ref",
             f"refs/remotes/origin/fix/{ISSUE}-configured-control", "HEAD")
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn(f"refs/remotes/origin/fix/{ISSUE}-configured-control", out)

    def test_a_namespace_a_non_standard_refspec_fetches_into_still_blocks(self):
        # ⛔ THE REFSPEC IS THE DISCRIMINATOR, NOT THE REMOTE'S NAME. This pins
        # the gap a name-comparison leaves open: `origin` is configured, but its
        # refspec writes `refs/remotes/upstream/*`, so `upstream` IS a live
        # remote-tracking namespace even though no remote is NAMED `upstream`.
        # Comparing the path segment to `git remote` would demote a genuine
        # branch here — a false CLEAN on a blocking surface.
        _git(self.repo, "config", "remote.origin.fetch",
             "+refs/heads/*:refs/remotes/upstream/*")
        _git(self.repo, "update-ref",
             f"refs/remotes/upstream/fix/{ISSUE}-refspec", "HEAD")
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn(f"refs/remotes/upstream/fix/{ISSUE}-refspec", out)

    def test_a_non_glob_refspec_namespace_still_blocks(self):
        # ⛔ THE NON-GLOB TRAP, pinned. `git remote set-branches origin main`
        # writes `+refs/heads/main:refs/remotes/origin/main` — a destination with
        # NO `*`. A parser that skipped the non-glob form left the namespace set
        # EMPTY, and an empty set demoted EVERY `refs/remotes/…` ref, so this
        # genuine remote-tracking branch came back CLEAN — a fail-open on a
        # blocking surface. Taking the PARENT of the destination fixes it, and it
        # errs WIDE, which on this surface is the safe direction.
        _git(self.repo, "config", "remote.origin.fetch",
             "+refs/heads/main:refs/remotes/origin/main")
        _git(self.repo, "update-ref",
             f"refs/remotes/origin/fix/{ISSUE}-nonglob", "HEAD")
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn(f"refs/remotes/origin/fix/{ISSUE}-nonglob", out)

    def test_a_glob_refspec_whose_star_is_not_last_still_blocks(self):
        # ⛔ A `*` IS NOT ALWAYS THE LAST SEGMENT OF A DESTINATION.
        # `+refs/heads/*:refs/remotes/x/*/y` is accepted by git and creates real
        # tracking refs like `refs/remotes/x/fix/3061-mine/y`. A prefix taken as
        # the PARENT of that destination keeps the literal `*`
        # (`refs/remotes/x/*/`), which prefixes NO real ref, so the branch was
        # demoted to a "fetch-cache ref" — a false CLEAN, and the dangerous
        # direction. The prefix must stop at the FIRST `*`.
        _git(self.repo, "config", "remote.origin.fetch",
             "+refs/heads/*:refs/remotes/x/*/y")
        _git(self.repo, "update-ref",
             f"refs/remotes/x/fix/{ISSUE}-midstar/y", "HEAD")
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn(f"refs/remotes/x/fix/{ISSUE}-midstar/y", out)

    def test_an_empty_namespace_set_blocks_rather_than_demoting(self):
        # ⛔ AN EMPTY SET IS NOT "EVERYTHING IS A CACHE" — it is an INABILITY TO
        # TELL, and it FAILS CLOSED. This is a UNIT pin rather than a CLI one
        # because the fixture configures `origin`, so the CLI cannot reach the
        # empty set at all — and that is exactly why the rule needs its own pin.
        # The empty set arises in a repo configuring NO remote, where a
        # `refs/remotes/origin/…` ref is indistinguishable between a leftover of
        # an earlier configuration and a deliberately created ref. Reading the
        # empty set as a cache demoted all of them — a fail-open on a blocking
        # surface, and the dangerous direction.
        mod = _tool_module()
        self.assertIsNone(
            mod._fetch_cache_remote(f"refs/remotes/pr/{ISSUE}", set()))
        self.assertIsNone(
            mod._fetch_cache_remote(f"refs/remotes/origin/fix/{ISSUE}", set()))
        self.assertIsNone(
            mod._fetch_cache_remote(f"refs/remotes/pr{ISSUE}", set()))
        # ...while a NON-empty set still demotes a namespace OUTSIDE it, so the
        # fail-closed arm is a floor and not a blanket refusal to demote at all.
        self.assertEqual(
            mod._fetch_cache_remote(f"refs/remotes/pr/{ISSUE}",
                                    {"refs/remotes/origin/"}), "pr")

    def test_an_unreadable_refspec_list_leaves_a_fetch_cache_ref_blocking(self):
        # FAIL-CLOSED unit pin. `_remote_tracking_namespaces` answers None when
        # the refspec list cannot be read, and `_fetch_cache_remote` must then
        # answer None — the BLOCKING answer. Reading an unreadable list as "no
        # namespaces, so every ref is a cache" would convert every remote hit
        # into a false CLEAN.
        mod = _tool_module()
        live = {"refs/remotes/origin/"}
        self.assertIsNone(
            mod._fetch_cache_remote(f"refs/remotes/pr/{ISSUE}", None))
        self.assertEqual(
            mod._fetch_cache_remote(f"refs/remotes/pr/{ISSUE}", live), "pr")
        self.assertEqual(
            mod._fetch_cache_remote(f"refs/remotes/pr{ISSUE}", live), f"pr{ISSUE}")
        # A ref INSIDE a live namespace, and a LOCAL branch, are not fetch caches.
        self.assertIsNone(mod._fetch_cache_remote(
            f"refs/remotes/origin/fix/{ISSUE}", live))
        self.assertIsNone(
            mod._fetch_cache_remote(f"refs/heads/fix/{ISSUE}", live))

    def test_the_entry_point_keeps_a_fetch_cache_ref_blocking_when_git_config_fails(self):
        # ENTRY-POINT fail-closed coverage. The unit pin above feeds `None`
        # straight to `_fetch_cache_remote`; this one makes the real
        # `_remote_tracking_namespaces` call FAIL, so a changed failure return is
        # caught by the COMPOSITION the tool actually performs rather than by a
        # test that never reaches it.
        _git(self.repo, "update-ref", f"refs/remotes/pr/{ISSUE}", "HEAD")
        stub = _write_exec(self.tmp / "git-stub-no-refspec", (
            "#!/usr/bin/env bash\n"
            "set -u\n"
            'if [ "${1:-}" = "config" ] && [ "${2:-}" = "--get-regexp" ]; then\n'
            '  echo "git-stub: refusing config --get-regexp" >&2\n'
            "  exit 128\n"
            "fi\n"
            'exec "${REAL_GIT:?REAL_GIT unset}" "$@"\n'
        ))
        rc, out = self.run_tool(git_bin=stub,
                                env_extra={"REAL_GIT": shutil.which("git")})
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn(f"refs/remotes/pr/{ISSUE}", out)
        # …and the surface says the read failed, rather than presenting a strict
        # answer as a measured one.
        self.assertIn("NO fetch-cache ref was demoted", out)

    def test_a_dead_record_is_demoted_only_on_gits_own_lock(self):
        # #6622 case 1, REVISED after a fresh review found a false CLEAN.
        #
        # `git worktree add` registers the record; removing the directory does
        # NOT deregister it. But a directory that is not on disk is NOT proof the
        # record is dead: `mv <worktree> <elsewhere>` leaves a fully working
        # worktree (a linked worktree's `.git` holds an ABSOLUTE `gitdir:`), so
        # git keeps working from the new path while this listing still reports
        # the OLD one. Demoting on absence alone therefore reported a LIVE lane as
        # non-blocking and let the gate exit 0 — the duplicate-ownership
        # direction this gate exists to prevent.
        #
        # The real dead shape, MEASURED in this repo, is an aborted
        # `git worktree add` left as `locked initializing`. That lock is git's own
        # "this registration cannot hold work" signal, and it is what demotes.
        mod = _tool_module()
        absent = str(self.tmp / "does-not-exist-3061")
        live, basis = mod._worktree_liveness(absent, "initializing")
        self.assertFalse(live, basis)
        self.assertIn("locked=initializing", basis)
        self.assertIn("cannot hold work", basis)
        # ⛔ A LOCK IS PRUNE-PROTECTION, NOT LIVENESS. `git worktree lock` exists
        # for a worktree whose path is EXPECTED to be absent (a portable device, a
        # network share), and `--porcelain` emits `locked` INSTEAD OF `prunable` —
        # so a MOVED worktree its owner also locked reports only `locked portable`
        # and would be demoted if ANY lock counted. It must block.
        for other_lock in ("portable", "yes", "reason: on a USB stick"):
            live, basis = mod._worktree_liveness(absent, other_lock)
            self.assertTrue(live, basis)
            self.assertIn("prune-protection", basis)
            self.assertIn("NOT demoted", basis)
        # ⛔ A LOCK-FREE `prunable` RECORD IS THE MOVED WORKTREE AND MUST BLOCK.
        live, basis = mod._worktree_liveness(
            absent, None, "gitdir file points to non-existent location")
        self.assertTrue(live, basis)
        self.assertIn("prunable=", basis)
        self.assertIn("NOT demoted", basis)
        # ⛔ AND A BARE ABSENCE FAILS CLOSED TOO — no lock, no demotion.
        live, basis = mod._worktree_liveness(absent)
        self.assertTrue(live, basis)
        self.assertIn("no lock, NOT demoted", basis)

    def test_a_moved_live_worktree_still_blocks(self):
        # ⛔ THE FALSE-CLEAN REGRESSION PIN (fresh review, P1). `mv` a worktree and
        # it KEEPS WORKING — git resolves it from the new path — while
        # `git worktree list --porcelain` goes on reporting the OLD path and tags
        # the record `prunable`. The lane is alive; the gate must say COLLISION.
        path = self.add_worktree(f"{ISSUE}-moved")
        moved = path.parent / f"{ISSUE}-moved-elsewhere"
        path.rename(moved)
        mod = _tool_module()
        live, basis = mod._worktree_liveness(str(path))
        self.assertTrue(live, basis)
        self.assertIn("NOT demoted", basis)
        # A plain `mv` leaves a WORKING worktree: its `.git` pointer survives and
        # still names an absolute gitdir, which is why git keeps working from the
        # new path while `git worktree list` reports the old one.
        marker = moved / ".git"
        self.assertTrue(marker.exists(), "moved worktree lost its marker")
        self.assertIn("gitdir:", marker.read_text())

    def test_the_entry_point_does_not_demote_an_unlocked_absent_record(self):
        # ENTRY-POINT polarity: an `rm -rf`'d record that git does NOT report as
        # locked is NOT demoted, so the pre-dispatch gate still refuses. This is
        # the direction that must fail CLOSED.
        path = self.add_worktree(f"{ISSUE}-absent-unlocked")
        shutil.rmtree(path)
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("exists=no", out)
        self.assertIn("NOT demoted", out)

    def test_a_worktree_whose_git_marker_is_gone_blocks_without_a_lock(self):
        # #6622 case 2, REVISED: a `.git` marker that is gone is evidence the
        # record is not a usable worktree — but it is NOT a lock, so it does not
        # by itself license a demotion. Same polarity as the moved case.
        path = self.add_worktree(f"{ISSUE}-no-gitdir")
        (path / ".git").unlink()
        mod = _tool_module()
        live, basis = mod._worktree_liveness(str(path))
        self.assertTrue(live, basis)
        self.assertIn("gitdir=no", basis)
        self.assertIn("NOT demoted", basis)
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("gitdir=no", out)

    def test_an_unreadable_worktree_is_not_demoted(self):
        # ⛔ UNREADABLE IS NOT ABSENT. `os.path.isdir` answers False for EACCES
        # and ESTALE as well as for a missing directory, so a demotion keyed on
        # it would turn a live-but-unreadable worktree into a false CLEAN on a
        # BLOCKING surface. Pinned here because the shape cannot be reproduced
        # as the directory's own owner.
        mod = _tool_module()
        with mock.patch("os.stat", side_effect=PermissionError("EACCES")):
            live, basis = mod._worktree_liveness("/some/worktree/3061-x")
        self.assertTrue(live, basis)
        self.assertIn("unreadable", basis)
        # A genuinely ABSENT path is only demoted when git reports it LOCKED.
        live, basis = mod._worktree_liveness(
            str(self.tmp / "does-not-exist-3061"), "initializing")
        self.assertFalse(live)
        self.assertIn("exists=no", basis)
        self.assertIn("locked=initializing", basis)

    def test_a_live_worktree_still_blocks_and_reports_its_basis(self):
        # The guard rail: a LIVE worktree is a real competing claim and must
        # keep blocking. It now carries its EVIDENCE, so a false hold is visible
        # at a glance instead of having to be re-derived by hand.
        self.add_worktree(f"{ISSUE}-live")
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("[local worktrees]", out)
        self.assertIn("exists=yes gitdir=yes age=", out)

    def test_issue_assignee_hit(self):
        self.gh_fixtures(issue=self.issue_payload(assignees=("other-agent",)))
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("[issue assignee/holder]", out)
        self.assertIn("assignee:other-agent", out)
        self.assertIn("a DIFFERENT account", out)

    def test_owner_as_assignee_is_triage_not_contention(self):
        # #3504 class 3, and its ruling is explicit: "the repo owner as assignee
        # is triage". Measured on the live repo: the owner is assignee on 86 of
        # 622 open issues, so treating that as a competing claim permanently
        # blocks 86 issues that nothing is working on.
        #
        # NOTE THE POLARITY CHANGE, because the old test asserted the opposite.
        # It argued "suppressing it blinds the surface to every lane on the
        # fleet, so keep it blocking (fail closed)". That reasoning was sound
        # about the ACCOUNT and wrong about the EVENT: assigning an issue is a
        # triage ACT, and it cannot distinguish "a lane has started" from "the
        # owner filed it" — the ambiguity is total, so the hit carries no
        # information about contention. The live-lane catch is not lost: a lane
        # that really started has a branch naming the issue number, and the
        # BRANCH surface still blocks on that (see
        # test_live_branch_blocks_dispatch below).
        self.gh_fixtures(issue=self.issue_payload(assignees=("test-agent",)))
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertIn("assignee:test-agent", out)
        self.assertIn("SHARED fleet account", out)
        self.assertIn("(non-blocking)", out)

    def test_issue_assignee_agent_account_still_blocks(self):
        # The other half of #3504's class-3 ruling: "narrow the assignee class
        # to AGENT accounts". A DIFFERENT login is attributable to a specific
        # lane and keeps blocking.
        self.gh_fixtures(issue=self.issue_payload(assignees=("other-agent",)))
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("assignee:other-agent", out)


    # ── the claim gate: the HOLDER, never prose (#8175) ──────────────────────

    def test_a_comment_claim_never_blocks_no_matter_what_it_says(self):
        # THE DEFECT'S REPRO. On origin/main this shape returned COLLISION: the
        # recall pre-filter matched the body and the typed-ownership call read it
        # as a lane taking the work. Two live reproductions: the operator's own
        # ruling comment refused the dispatch of the work it ruled on
        # ("claim-style comment (uncertain ownership: p=0.54)"), and #4665's
        # consolidation/duplicate cross-links did the same.
        #
        # The body is put IN THE PAYLOAD on purpose. The contract is that the
        # tool does not read comment bodies — it does not even request them — and
        # a field that is not fetched cannot be read. So the claim text is inert.
        for body in (
            "Claiming this.",
            "I'll claim this.",
            "Working on this.",
            "/claim",
            "On it.",
            "Consolidated under #5063 — duplicate of #4603.",
        ):
            with self.subTest(body=body):
                self._write_issue_payload({
                    "number": ISSUE, "title": "t", "state": "OPEN",
                    "stateReason": None,
                    "url": f"https://example.invalid/issues/{ISSUE}",
                    "assignees": [],
                    "comments": [{"author": {"login": "test-agent"},
                                  "body": body}],
                })
                rc, out = self.run_tool()
                self.assertEqual(rc, 0, f"{body!r}\n{out}")
                self.assertIn("VERDICT: CLEAN", out)
                self.assertNotIn("VERDICT: COLLISION", out)
        # ⛔ THE PROJECTION ITSELF IS PINNED. `comments` is not merely IGNORED,
        # it is not FETCHED: `gh issue view` receives an explicit `--json` field
        # list, and re-adding `comments` to it is the first step of any
        # prose-path revival. A fixture that carries a body would keep passing
        # either way (the stub serves the whole file regardless of `--json`), so
        # the argv is the only place this can be caught.
        self.assertNotIn("comments",
                         (self.gh_dir / "issue-argv.txt").read_text())
        self.assertIn("assignees",
                      (self.gh_dir / "issue-argv.txt").read_text())

    def test_a_live_holder_blocks_and_names_the_lane(self):
        # The direction that must NEVER weaken: a lane the BOARD mechanically
        # attributes to this issue, and which the board reports as still
        # running, blocks the dispatch.
        self.fleet_board(
            issues={ISSUE: [("lane-live", "branch slug fix/3061-live")]},
            lanes=[{"label": "lane-live", "live": True}],
        )
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("[issue assignee/holder]", out)
        self.assertIn("holder:lane-live", out)
        self.assertIn("LIVE pi process", out)
        self.assertIn("REMEDY: live HOLDER(s)", out)
        self.assertIn("1 still holding", out)

    def test_a_stopped_holder_is_reported_and_does_not_block(self):
        # THE MOTIVATING CASE (#6151): the lane that abandoned the work is
        # RECOVERABLE — its cmux workspace is gone, and the board measured NO live
        # pi process for it. `AGENTS.md` is explicit that "idle usually means
        # mid-task", so the demotion is NOT "no PID right now": the board must
        # MEASURE liveness and find no live pi. ⛔ The terminal workspace alone is
        # NOT the reading — a terminal lane's process can outlive its workspace
        # (see `test_a_terminal_lane_whose_process_is_still_alive_is_LIVE`), so
        # the `pid_alive: False` here is what decides this case.
        self.fleet_board(
            issues={ISSUE: [("π - land-lane-1", "branch slug fix/3061-x")]},
            lanes=[{"label": "π - land-lane-1", "live": False,
                    "terminal": "RECOVERABLE",
                    "reason": "cmux workspace 6AC480DF no longer exists"}],
        )
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertIn("holder:π - land-lane-1", out)
        self.assertIn("has STOPPED", out)
        self.assertIn("reported, non-blocking", out)
        self.assertIn("1 stopped", out)
        self.assertNotIn("do NOT dispatch", out)

    def test_a_lane_label_containing_prose_is_not_reported_as_prose_only(self):
        # ⛔ A WEAK HIT'S KIND IS STRUCTURED (`Hit.kind`), NEVER A SUBSTRING of its
        # human-readable detail — that detail EMBEDS the lane label. The shape
        # here is a real one in this fleet (lanes are named for their objective,
        # e.g. "1 Capture write doors"): a STOPPED holder whose label contains
        # "prose" made the report print "1 a prose-only cross-reference(s)" and
        # never mention the non-prose kinds, i.e. it named a kind the run did not
        # hit and hid the one it did (#8175 review).
        self.fleet_board(
            issues={ISSUE: [("12 Prose naturalisation",
                             "branch slug fix/1-prose")]},
            lanes=[{"label": "12 Prose naturalisation", "live": False}],
        )
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("has STOPPED", out)
        self.assertIn(_tool_module().WEAK_HIT_KINDS, out)
        self.assertNotIn(_tool_module().WEAK_KIND_PROSE_ONLY, out)

    def test_a_terminal_holder_whose_process_is_alive_still_blocks(self):
        # ⛔ THE REGRESSION THE REVIEW FOUND, end to end. `lane_live_map` folds a
        # terminal verdict into "dead" (#7738), but a terminal workspace's
        # PROCESS can outlive it — the live board's `π - land-lane-1` (#6151) is
        # exactly that. Reading the terminal verdict as "stopped" would free a
        # RUNNING lane's issue: the one forbidden direction.
        self.fleet_board(
            issues={ISSUE: [("π - land-lane-1", "branch slug fix/3061-x")]},
            lanes=[{"label": "π - land-lane-1", "live": True,
                    "terminal": "RECOVERABLE",
                    "reason": "cmux workspace 6AC480DF no longer exists"}],
        )
        rc, out = self.run_tool()
        self.assertEqual(rc, 1, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("holder:π - land-lane-1", out)
        self.assertIn("LIVE pi process", out)
        self.assertIn("1 still holding", out)
        self.assertNotIn("has STOPPED", out)

    def test_a_measured_dead_lane_with_no_terminal_still_stops(self):
        # The SECOND stop path: the board MEASURED liveness and found no live pi
        # for the lane. No terminal workspace is needed — a measured "not
        # running" is the writer having stopped.
        self.fleet_board(
            issues={ISSUE: [("lane-parked", "worktree path /tmp/wt/parked")]},
            lanes=[{"label": "lane-parked", "live": False}],
        )
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertIn("holder:lane-parked", out)
        self.assertIn("found no live pi", out)

    def test_unmeasured_liveness_is_incomplete_not_clean(self):
        # FAIL-CLOSED. A lane is attributed but the board has NO liveness reading
        # for it. "I could not measure" is not "it is dead", so reporting CLEAN
        # would be the false negative this gate exists to prevent. exit 2 is a
        # distinct outcome from exit 1 for exactly this reason.
        self.fleet_board(
            issues={ISSUE: [("lane-unknown", "worktree path /tmp/wt/unknown")]},
            lanes=[{"label": "lane-unknown", "live": None}],
        )
        rc, out = self.run_tool()
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        self.assertIn("[issue assignee/holder]", out)
        self.assertIn("liveness unmeasured", out)

    def test_a_truncated_open_issue_read_blocks_rather_than_reading_free(self):
        # ⛔ THE COVERAGE FAIL-OPEN, end to end. An issue the board's open-issue
        # window never saw cannot be attributed, so its absence from the index is
        # not a measured "nobody" — answering CLEAN there is a live lane read
        # free, the one direction the ruling forbids.
        self.fleet_board(open_issues_truncated=True)
        rc, out = self.run_tool()
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        self.assertIn("OPEN-ISSUE read hit its cap", out)

    def test_graphql_exhaustion_falls_back_to_rest_instead_of_incomplete(self):
        # ⛔ THE INSTRUMENT HOLDING THE DATUM AND NOT READING IT (#3435).
        # `gh issue view` is GraphQL-only. When THAT CALL is refused, the tool
        # used to report the issue surface INCOMPLETE — and because the landed
        # surface reads the issue's `state`, it went INCOMPLETE too — while
        # `gh api` answers the identical question on REST's separate budget. A
        # refusal it could have measured is not a refusal, it is a blind spot.
        #
        # SCOPE: this fixture models ONE refused GraphQL call (a secondary /
        # per-query limit), not a spent primary bucket — see
        # test_shared_graphql_exhaustion_rescues_the_issue_reads_but_not_the_closing_test
        # for the shared-bucket shape. There `open PRs` is ENUMERATED over REST
        # too (#9402) and still ends INCOMPLETE, because the GraphQL-only
        # `closingIssuesReferences` test cannot be measured over REST — it is
        # the CLOSING TEST that is GraphQL-only, not the surface.
        rc, out = self.run_tool(env_extra={"GH_STUB_ISSUE_VIEW_RATE_LIMITED": "1"})
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertNotIn("issue-metadata-unavailable", out)
        self.assertNotIn("VERDICT: INCOMPLETE", out)
        # The REST payload says "open" (lowercase) where GraphQL says "OPEN".
        # The landed surface must still read it as OPEN and consult the
        # timeline; a case-sensitive compare would silently skip that query.
        self.assertIn("issue state=OPEN", out)

    def test_a_bare_http_429_also_triggers_the_rest_fallback(self):
        # ⛔ THE CANONICAL EXHAUST FORM (#3435 review, P2). `gh` renders a non-2xx
        # as `HTTP <code>: <status> (<url>)`, so a 429 with no body prose carries
        # NO "rate limit" substring. A bare substring test therefore under-
        # triggers on exactly the form the premise calls routine, and deleting
        # the `"too many requests"` / `"http 429"` arms used to keep the suite
        # green — so this case pins them.
        rc, out = self.run_tool(env_extra={"GH_STUB_ISSUE_VIEW_HTTP_429": "1"})
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertIn("issue state=OPEN", out)
        self.assertNotIn("issue-metadata-unavailable", out)

    def test_shared_graphql_exhaustion_rescues_the_issue_reads_but_not_the_closing_test(self):
        # ⛔ THE REAL SHAPE OF THE FLEET SYMPTOM (#3435 review, P2). GitHub's
        # PRIMARY GraphQL budget is ONE shared bucket, so a genuine exhaustion
        # kills `gh pr list` as well as `gh issue view`. The fallback does NOT
        # make that run CLEAN — it makes the issue surfaces stop lying about
        # why, and it makes `open PRs` say WHICH test it could not run.
        #
        # ⛔ THE NAME IS PART OF THE PIN (#9423 review, P3). It used to be
        # `…_but_not_open_prs`, and its docstring said `open PRs` "stays a
        # fail-closed refusal" / was "GraphQL-only BY DESIGN" — true before
        # #9402, FALSE after it: that surface is now ENUMERATED over REST (a
        # real title/head-ref collision still decides exit 1). Only its
        # closing-reference test is unmeasured, because REST has no
        # `closingIssuesReferences`. A lane citing the old NAME as evidence that
        # the surface is GraphQL-only is exactly the reader that would delete
        # `_open_prs_rest` — and this file's own standard says a defence no test
        # exercises can be deleted with nothing going red.
        #
        # So: the ISSUE surfaces are READ, `open PRs` is READ TOO but says
        # `closing-references-unmeasured`, and the run is still INCOMPLETE.
        # `test_open_pr_graphql_exhaustion_is_enumerated_over_rest` proves the
        # rescue; `test_rest_rescued_open_prs_still_report_the_unmeasured_closing_test`
        # pins that the rescue never pretends to have proved the negative.
        rc, out = self.run_tool(env_extra={
            "GH_STUB_ISSUE_VIEW_RATE_LIMITED": "1",
            "GH_STUB_PR_LIST_RATE_LIMITED": "1",
        })
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertIn("[open PRs]", out)
        # The ISSUE surfaces were rescued: read, not refused, and not blamed on
        # the budget — which is the whole point of the fallback.
        self.assertRegex(out, r"(?m)^issue assignee/holder\s+CLEAN")
        self.assertNotIn("issue-metadata-unavailable", out)
        self.assertIn("issue state=OPEN", out)
        # The open-PR half was READ OVER REST as well — the enumeration is not
        # what is missing; the GraphQL-only closing test is.
        argv = (self.gh_dir / "open-prs-rest-argv.txt").read_text()
        self.assertIn("repos/test-owner/test-repo/pulls?state=open", argv)
        self.assertIn("closing-references-unmeasured", out)
        self.assertNotIn("VERDICT: CLEAN", out)

    def test_open_pr_graphql_exhaustion_is_enumerated_over_rest(self):
        # #9402: with the fleet-shared GraphQL budget spent, the open-PR surface
        # used to be unqueryable, so every candidate read exit 2 and NO lane could
        # pass the mandated dispatch gate — including for an issue that has an
        # open PR titled with its own number, which is the duplicate this gate
        # exists to catch.
        #
        # The rescue must recover the TESTS REST CAN SERVE. A title match is the
        # strongest of them, and this fixture carries nothing else (no closing
        # reference, no branch, no worktree), so exit 1 here can only come from the
        # REST-enumerated list.
        self.gh_fixtures(open_prs=[{
            "number": 9410, "title": f"fix(dispatch): undo the duplicate ({ISSUE})",
            "body": "", "headRefName": "feat/9999-other", "state": "open",
        }])
        rc, out = self.run_tool(env_extra={"GH_STUB_PR_LIST_RATE_LIMITED": "1"})
        self.assertEqual(rc, 1, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("fix(dispatch): undo the duplicate", out)
        # It really went over REST (not a cached/GraphQL path), and the request
        # carries `state=open` — a rescue that forgot the filter would enumerate
        # every PR in the repo.
        argv = (self.gh_dir / "open-prs-rest-argv.txt").read_text()
        self.assertIn("repos/test-owner/test-repo/pulls?state=open", argv)
        # The refusal line still reports the surface as incomplete, because the
        # closing-reference test could not run — a collision outranks it, but the
        # reader must not be told the surface was fully READ.
        self.assertIn("ALSO INCOMPLETE", out)

    def test_rest_rescued_open_prs_still_report_the_unmeasured_closing_test(self):
        # ⛔ THE HONEST HALF OF #9402. REST has NO `closingIssuesReferences`, so a
        # rescue can never prove that no open PR CLOSES the issue. Reading the
        # absent key as "closes nothing" is the fail-open direction this whole
        # file exists to refuse, so the surface must say which test it could not
        # run — and ``VERDICT: CLEAN`` must remain unreachable.
        rc, out = self.run_tool(env_extra={"GH_STUB_PR_LIST_RATE_LIMITED": "1"})
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertRegex(out, r"(?m)^open PRs\s+INCOMPLETE")
        self.assertIn("closing-references-unmeasured", out)
        self.assertIn("closingIssuesReferences", out)
        self.assertIn("0 PR(s) WERE read", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        # And the remedy names the CAUSE: the fleet-shared GraphQL budget, not
        # `gh auth`/a board rebuild (which is #9402's stated defect).
        self.assertIn("GRAPHQL BUDGET EXHAUSTION", out)
        self.assertIn("refused read", out.lower())
        self.assertNotIn("fix gh auth/network", out)
        # ⛔ THE RESET SIGNAL MUST BE ONE THAT CAN SHOW THE REFUSAL (#9423 review,
        # P2). The remedy states — correctly — that
        # `rate_limit.resources.graphql` reads thousands remaining DURING this
        # outage, and then used to tell the reader to decide when to re-run by
        # reading exactly that counter: a lane following it polls a number that
        # never moves and concludes the cause is not the one named. The signal
        # must be the failing read itself, which is the only thing whose answer
        # changes.
        verdict = self._verdict_line(out)
        self.assertNotIn("gh api rate_limit", verdict)
        self.assertIn("probe the read that failed", verdict)
        self.assertIn("gh pr list --state open --limit 1 --json number", verdict)

    def test_open_pr_rest_rescue_failure_stays_fail_closed(self):
        # The rescue must never become a fail-open. With BOTH budgets gone the
        # tool has read nothing about open PRs, so it must still refuse — and it
        # must NAME the REST refusal, or this test would also pass on a tool with
        # no rescue at all (the GraphQL read fails first either way).
        rc, out = self.run_tool(env_extra={
            "GH_STUB_PR_LIST_RATE_LIMITED": "1",
            "GH_STUB_OPEN_PR_REST_FAIL": "1",
        })
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        self.assertIn("gh api repos/test-owner/test-repo/pulls", out)
        self.assertIn("could not connect to api.github.com", out)
        # The budget exhaustion stays visible alongside the REST failure so the
        # reader is not sent after the wrong cause.
        self.assertIn("GraphQL read:", out)

    def test_a_long_stderr_does_not_hide_the_exhaustion_when_the_rescue_fails(self):
        # ⛔ THE FAILING-RESCUE BRANCH CARRIES THE CAUSE TOO (#9423 review, P3).
        # The successful rescue's note got the untruncated cause first; this
        # raise did not, and its message becomes the surface note that
        # `_incomplete_remedy` classifies. So with a stderr that chatters past
        # the 200-character display cut AND a REST request that refuses for its
        # own reason, the note carried no budget signature, no surface was
        # classified as exhausted, and the report ended with
        # `fix gh auth/network (or rebuild the fleet board) and re-run` — the
        # exact remedy #9402 files, on the one branch of the fix that dropped it.
        #
        # ⛔ NON-VACUOUS, MEASURED: pre-fix the note embeds only
        # `… failed (exit 1): gh-stub: request trace: xxx…` (cut at 200, the
        # signature at offset 262), so BOTH the `BUDGET EXHAUSTION` assertion and
        # the `assertNotIn("fix gh auth/network")` red. Neither seam is new:
        # the suite just never combined them.
        rc, out = self.run_tool(env_extra={
            "GH_STUB_PR_LIST_LONG_STDERR": "1",
            "GH_STUB_OPEN_PR_REST_FAIL": "1",
        })
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        # Both failures are still named — this widens the text, it does not
        # replace the REST reason with the budget one.
        self.assertIn("could not connect to api.github.com", out)
        self.assertIn("GraphQL read:", out)
        # …and the remedy names the cause the rescue already matched on.
        verdict = self._verdict_line(out)
        self.assertIn("BUDGET EXHAUSTION", verdict)
        self.assertNotIn("fix gh auth/network", out)

    def test_a_long_stderr_does_not_hide_the_exhaustion_when_the_issue_rescue_fails(self):
        # The ISSUE-metadata rescue has the same shape, and the same one-line
        # change covers it (#9423 review, P3): its failing branch composed
        # `SurfaceError(f"{rest_why} (GraphQL read: {exc})")` from the display
        # message, and the surface note is what the remedy is derived from. A
        # fix at the open-PR site alone would leave this branch reproducing the
        # #9402 defect, so the seam (`GH_STUB_ISSUE_VIEW_LONG_STDERR`) is the
        # ISSUE read's own — not a re-use of the PR one.
        rc, out = self.run_tool(env_extra={
            "GH_STUB_ISSUE_VIEW_LONG_STDERR": "1",
            "GH_STUB_ISSUE_REST_FAIL": "1",
        })
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        # The issue surface is the one that refused, and it names BOTH reads —
        # with the budget signature past the 200-char cut still visible, which
        # is exactly what the display message could not carry pre-fix.
        self.assertIn("[issue assignee/holder]", out)
        self.assertIn("Resource not accessible", out)
        self.assertIn("GraphQL read:", out)
        self.assertIn("API rate limit already exceeded", out)
        verdict = self._verdict_line(out)
        self.assertIn("BUDGET EXHAUSTION", verdict)
        self.assertIn("issue assignee/holder:", verdict)
        # ⛔ THE GENERIC ADVICE IS STILL PRESENT HERE, AND THAT IS NOT THIS BUG.
        # `issue landed` is a SECOND incomplete surface whose note records the
        # derived cause (`issue-metadata-unavailable: …`) and carries no failure
        # text at all, so the mixed arm rightly keeps the generic remedy for the
        # remainder instead of claiming the budget explains everything. What
        # this test pins is that the surface which DID measure the exhaustion is
        # classified and named — pre-fix NO surface was, so the report printed
        # the generic advice alone and never mentioned the budget.
        self.assertIn("[issue landed]", out)

    def test_a_rest_open_pr_element_with_a_blank_state_is_refused(self):
        # A blank `state` is the fail-OPEN value: `_pr_terminal_state` strips it,
        # `""` is not in ("open", "opened"), and a truthy `mergedAt` then reads a
        # LIVE PR as immutable history — every hit demotes to `weak` and the run
        # can report CLEAN. Two layers refuse it (the wire contract and
        # `_require_pr_dicts` on the composed record); what this pins is the
        # OUTCOME, whichever layer answers: a NAMED INCOMPLETE on the surface,
        # not a traceback and not a reading.
        #
        # WHAT THIS IS, MEASURED (not asserted): against `origin/main` there is
        # no REST path at all, so the run still refuses but the wording this test
        # asserts is absent — it reds there. Against a rescue WITHOUT the
        # projection's `try` (this PR's earlier head) it reds harder: the
        # `SurfaceError` was raised from inside the caller's `except SurfaceError`
        # body and escaped as a traceback with exit 1. So the test pins the
        # OUTCOME across both layers rather than a single guard.
        self.gh_fixtures(open_prs=[{
            "number": 9418, "title": "chore: an unrelated open PR",
            "body": "", "headRefName": "chore/9999-unrelated", "state": "open",
        }])
        rest = json.loads((self.gh_dir / "open_prs_rest.json").read_text())
        rest[0]["state"] = ""
        (self.gh_dir / "open_prs_rest.json").write_text(json.dumps(rest))
        rc, out = self.run_tool(env_extra={"GH_STUB_PR_LIST_RATE_LIMITED": "1"})
        self.assertNotIn("Traceback", out)
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertIn("blank or non-string 'state'", out)
        self.assertIn("[open PRs]", out)

    def test_a_rest_open_pr_element_without_its_body_is_refused(self):
        # ⛔ THE BODY IS THE ONE KEY WHOSE ABSENCE IS NOT "empty" (#9423 review).
        # REST sends `"body": null` for a PR with no description, so NULL must be
        # read as empty — but a MISSING KEY is a malformed element, and on the
        # REST-rescued BLOCKING surface the body regex is the ONLY
        # closing-reference test left. Reading absence as "" would silently drop
        # the one test that can still refuse a duplicate.
        #
        # Non-vacuous: pre-fix the element is projected with `body: ""`, the run
        # reports only the unmeasured-closing note, and the assertion on `with no
        # 'body'` fails.
        self.gh_fixtures(open_prs=[{
            "number": 9419, "title": "chore: an unrelated open PR",
            "body": "", "headRefName": "chore/9999-unrelated", "state": "open",
        }])
        rest = json.loads((self.gh_dir / "open_prs_rest.json").read_text())
        del rest[0]["body"]
        (self.gh_dir / "open_prs_rest.json").write_text(json.dumps(rest))
        rc, out = self.run_tool(env_extra={"GH_STUB_PR_LIST_RATE_LIMITED": "1"})
        self.assertNotIn("Traceback", out)
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        self.assertIn("with no 'body'", out)

    def test_the_rest_open_pr_rescue_is_not_attempted_when_graphql_answers(self):
        # The rescue is scoped to a BUDGET EXHAUSTION of THIS read. Rescuing any
        # other failure would turn a genuine "could not read" into a reading from
        # a second endpoint — the fail-open direction — so the happy path must
        # make no REST call at all.
        #
        # ⛔ WHAT THIS TEST IS, STATED ACCURATELY (#9423 review, P3). It is a
        # SCOPE REGRESSION PIN, not evidence of the fix: on the pre-fix tool there
        # is no REST path at all, so every assertion here passes there too. The
        # PR body's "each fails without the fix" was wrong about this one, and
        # this docstring is the correction — the NON-VACUOUS half of the pair is
        # `test_open_pr_graphql_exhaustion_is_enumerated_over_rest`.
        #
        # The second half is what a mutation CAN red: an exhaustion on a
        # DIFFERENT surface (`gh issue view` refused, `pr list` answering) must
        # not drag the open-PR surface onto REST either. A gate keyed to "any
        # exhaustion anywhere" would pass the first half and fail here.
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertFalse((self.gh_dir / "open-prs-rest-argv.txt").exists(), out)
        # ⛔ AND THE GATE IS THE CAUSE, NOT "the read failed" (#9423 review, P3).
        # The arm above passes on the PRE-FIX tool as well (pre-fix there is no
        # REST path at all), so on its own it pins nothing about the gate. This
        # arm fails the same `gh pr list` with a NON-exhaustion error: a rescue
        # gated on the exception instead of on `_is_budget_exhaustion` answers it
        # over REST — hiding a real "could not read" — and a rescue gated
        # correctly makes no `gh api …/pulls` call and reports the read as
        # gh-unavailable. Dropping the exhaustion test from the gate REDS this.
        (self.gh_dir / "open-prs-rest-argv.txt").unlink(missing_ok=True)
        self.gh_fixtures(open_prs=[{
            "number": 9414, "title": "chore: an unrelated open PR",
            "body": "", "headRefName": "chore/9999-unrelated", "state": "open",
        }])
        (self.gh_dir / "open-prs-rest-argv.txt").unlink(missing_ok=True)
        rc, out = self.run_tool(env_extra={"GH_STUB_PR_LIST_FAIL": "1"})
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertIn("gh-unavailable", out)
        self.assertNotIn("closing-references-unmeasured", out)
        self.assertFalse((self.gh_dir / "open-prs-rest-argv.txt").exists(), out)
        # …and the exhaustion must be THIS surface's. A budget refusal on a
        # DIFFERENT surface (`gh issue view`) is rescued on its own path and must
        # not move the open-PR enumeration onto REST, which a gate keyed to "some
        # exhaustion happened in this run" would do.
        (self.gh_dir / "open-prs-rest-argv.txt").unlink(missing_ok=True)
        rc, out = self.run_tool(env_extra={"GH_STUB_ISSUE_VIEW_RATE_LIMITED": "1"})
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertFalse((self.gh_dir / "open-prs-rest-argv.txt").exists(), out)

    def test_a_malformed_rest_open_pr_element_is_a_named_incomplete_not_a_collision(self):
        # #9423 review (P2). `_open_prs_rest` fabricated the `gh pr list --json`
        # projection and only THEN ran `_require_pr_dicts` on the records it had
        # just built — so the element-type contract never saw the wire. A body of
        # `[null]` raised `AttributeError` on `element.get("head")` INSIDE the
        # `except SurfaceError` body that calls the rescue, which no handler takes:
        # traceback, NO `VERDICT` line, exit 1 — the code this module documents as
        # COLLISION, i.e. "another lane is on it, do not dispatch".
        self.gh_fixtures(open_prs=[{
            "number": 9415, "title": "chore: an unrelated open PR",
            "body": "", "headRefName": "chore/9999-unrelated", "state": "open",
        }])
        (self.gh_dir / "open_prs_rest.json").write_text("[null]")
        rc, out = self.run_tool(env_extra={"GH_STUB_PR_LIST_RATE_LIMITED": "1"})
        self.assertNotIn("Traceback", out)
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: COLLISION", out)
        self.assertRegex(out, r"(?m)^open PRs\s+INCOMPLETE")
        self.assertIn("non-object element at index 0", out)
        self.assertIn("NoneType", out)

    def test_a_rest_open_pr_element_missing_a_key_is_refused_not_filled_in(self):
        # #9423 review (P2), SECOND manifestation. Synthesising every key
        # (`element.get("title") or ""`) turned ABSENCE into EMPTINESS, so the
        # presence checks in `_require_pr_dicts` could never fire for this
        # transport and a `/pulls` element without `title` (or `head.ref`)
        # silently DROPPED that test on the BLOCKING surface — the fail-OPEN
        # direction the contract refuses. Neither shape raised anything, so this
        # test is the only thing that can red the guards.
        for key, needle in (("title", "no 'title'"),
                            ("head", "non-object 'head'")):
            with self.subTest(missing=key):
                self.gh_fixtures(open_prs=[{
                    "number": 9416, "title": "chore: an unrelated open PR",
                    "body": "", "headRefName": "chore/9999-unrelated",
                    "state": "open",
                }])
                rest = json.loads(
                    (self.gh_dir / "open_prs_rest.json").read_text())
                del rest[0][key]
                (self.gh_dir / "open_prs_rest.json").write_text(json.dumps(rest))
                rc, out = self.run_tool(
                    env_extra={"GH_STUB_PR_LIST_RATE_LIMITED": "1"})
                self.assertNotIn("Traceback", out)
                self.assertEqual(rc, 2, out)
                self.assertIn("VERDICT: INCOMPLETE", out)
                self.assertIn(needle, out)
                # The discriminator: pre-fix the run still refused, but for the
                # WRONG reason (the computed closing test it never ran), because
                # the absent key had been filled in and swallowed.
                self.assertNotIn("closing-references-unmeasured", out)

    def test_a_rest_open_pr_element_without_a_head_ref_is_refused(self):
        # The same guard one level down: `head` present but `ref` absent (or not a
        # string) is the head-ref test's ONLY input. Filling it in with "" skipped
        # that test on a BLOCKING surface without saying so.
        self.gh_fixtures(open_prs=[{
            "number": 9417, "title": "chore: an unrelated open PR",
            "body": "", "headRefName": "chore/9999-unrelated", "state": "open",
        }])
        rest = json.loads((self.gh_dir / "open_prs_rest.json").read_text())
        rest[0]["head"] = {"sha": "deadbeef"}
        (self.gh_dir / "open_prs_rest.json").write_text(json.dumps(rest))
        rc, out = self.run_tool(env_extra={"GH_STUB_PR_LIST_RATE_LIMITED": "1"})
        self.assertNotIn("Traceback", out)
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertIn("no string 'head.ref'", out)

    def test_rest_rescued_body_closing_reference_is_not_described_as_advisory(self):
        # #9423 review (P2, finding 1). `use_closing_field=False` used to mean
        # ONLY the advisory closed-PR sample, so the regex-fallback detail
        # hardcoded "this surface is advisory". #9402 made the BLOCKING open-PR
        # surface pass False as well (its REST rescue carries no
        # `closingIssuesReferences`), and a NON-TERMINAL hit there is `strong` —
        # it DECIDES the refusal. The report then told the reader, under
        # `VERDICT: COLLISION`, that a blocking surface was advisory: two false
        # statements (the authority, and which surface produced the refusal) in
        # the one line a human reads at the point of refusal.
        #
        # The fixture states the closing reference in the BODY ONLY — no number
        # in the title, none in the head ref — so the fallback arm is the only
        # branch that can fire, and the hit can only be `strong`.
        self.gh_fixtures(open_prs=[{
            "number": 9411, "title": "fix(dispatch): unrelated wording",
            "body": f"Closes #{ISSUE}.", "headRefName": "feat/9999-other",
            "state": "open",
        }])
        rc, out = self.run_tool(env_extra={"GH_STUB_PR_LIST_RATE_LIMITED": "1"})
        self.assertEqual(rc, 1, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn(f"closing reference to #{ISSUE}", out)
        # The detail names the REAL cause — the computed field was not requested
        # on this call — instead of mislabelling the surface.
        self.assertIn("closingIssuesReferences was not requested on this call", out)
        self.assertNotIn("regex fallback — this surface is advisory", out)

    def test_advisory_closed_pr_body_closing_reference_still_says_advisory(self):
        # The other arm of #9423 finding 1, pinned so the fix cannot be "delete
        # the advisory wording": on the ADVISORY closed-PR surface the regex
        # fallback really IS advisory, it can never refuse, and the detail must
        # keep saying so. Without this test a silent reversion to the blocking
        # wording on the advisory call site would pass the suite.
        #
        # ⛔ IT IS A NON-DISCRIMINATING REGRESSION PIN, AND THAT IS STATED HERE ON
        # PURPOSE (#9423 review, P3): it passes on the pre-fix tool as well, because
        # the advisory wording used to be HARDCODED on every `use_closing_field=False`
        # call. Its job is the direction this fix could break — the blocking surface
        # getting the advisory sentence — not evidence that the fix works. The
        # discriminating half is
        # `test_rest_rescued_body_closing_reference_is_not_described_as_advisory`,
        # which is red pre-fix.
        self.gh_fixtures(closed_prs=[{
            "number": 9412, "title": "fix(dashboard): plaintext mint",
            "body": f"Closes #{ISSUE}.", "headRefName": "fix/unrelated-slug",
            "state": "closed",
        }])
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertIn("regex fallback — this surface is advisory", out)
        self.assertNotIn("closingIssuesReferences was not requested on this call", out)

    def test_open_pr_number_refusal_survives_a_rest_rescued_list(self):
        """The `unreadable` arm is LIVE on the rescue path (#9423 review, P3-2).

        The code comment at that arm used to say "the two agree at both call
        sites today, so this is the drift arm, not the live one", and the unit
        test above said "no production call site reaches this arm today". #9402
        makes both false: the BLOCKING open-PR surface is scanned with
        `use_closing_field=not via_rest`, and a REST-rescued list has no
        `closingIssuesReferences` — so a pre-flight on an OPEN PR's OWN NUMBER
        during a GraphQL exhaustion reaches the arm for real and refuses with
        "its closing-reference field could not be read".

        That wording is the right one (the caller never requested the field), so
        what this pins is that the live route keeps it — and that the route is a
        NAMED refusal, not a traceback and not a VERDICT line mis-remedied as a
        broken surface. Nothing exercised this shape before, which is why the
        stale claims could sit next to a green suite.

        ⛔ THE HEAD REF IS NUMBER-FREE (#9957): a `fix/2712-…` branch would now
        RESOLVE the number and the run would stop being a refusal at all. The
        property this test pins is the residual one, and it still holds on this
        route — the rescue cannot supply the closing field, so a PR that names no
        issue anywhere cannot be resolved, and "could not be read" must never
        become a claim about a field that was never requested.
        """
        self.gh_fixtures(open_prs=[{
            "number": ISSUE, "title": "fix(battery): restore the pin test",
            "body": "no closing reference in this body", "state": "open",
            "headRefName": "chore/record-reads",
        }])
        rc, out = self.run_tool(env_extra={"GH_STUB_PR_LIST_RATE_LIMITED": "1"})
        self.assertEqual(rc, 2, out)
        # The list really came from the REST rescue (not the GraphQL read), so
        # the arm is reached through the path the claim is about.
        argv = (self.gh_dir / "open-prs-rest-argv.txt").read_text()
        self.assertIn("repos/test-owner/test-repo/pulls?state=open", argv)
        self.assertIn("is an OPEN PULL REQUEST, not a work item", out)
        # The UNREAD wording, never a claim about a field that was not requested.
        self.assertIn("closing-reference field could not be read", out)
        self.assertNotIn("It names no closing issue", out)
        # #9957: the resolver repeats the cause in its own words, and it must
        # ALSO not invent a reading of the field it never got.
        self.assertIn("the computed field could not be read", out)
        self.assertNotIn("closing references name nothing", out)
        # A refusal, not a crash and not a surface-level INCOMPLETE: `main()`
        # prints the refusal and returns BEFORE `format_report`, so the caller
        # keys its remedy on the cause instead of on a broken read.
        self.assertNotIn("Traceback", out)
        self.assertNotIn("VERDICT", out)

    def test_rest_truncation_note_names_the_requested_page_size(self):
        # #9423 review (P2, finding 2). `--pr-limit` has NO lower bound, and the
        # REST rescue requests `per_page = min(100, --pr-limit)`. This note is the
        # ONLY truncation note the REST path can emit (`len(prs) > limit` is
        # unreachable there by construction), so a hardcoded "GitHub's maximum of
        # 100" is the sole thing a human reads — and it is FALSE below 100, where
        # the actual remedy is the very `--pr-limit` flag the note withheld.
        prs = [{"number": 9000 + n, "title": f"chore: unrelated {n}",
                "body": "", "headRefName": f"chore/9{n}-unrelated",
                "state": "open"}
               for n in range(20)]
        self.gh_fixtures(open_prs=prs)
        rc, out = self.run_tool(extra_args=["--pr-limit", "20"],
                                env_extra={"GH_STUB_PR_LIST_RATE_LIMITED": "1"})
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        # The request really asked for the smaller page, so the note's claim has
        # to match it — this is the fact the pre-fix note contradicted.
        mod = _tool_module()
        argv = (self.gh_dir / "open-prs-rest-argv.txt").read_text()
        self.assertIn("per_page=20", argv)
        self.assertNotIn("per_page=100", argv)
        self.assertIn("requested per_page of 20", out)
        self.assertNotIn(
            f"per_page of {mod._REST_PULLS_PAGE} and returned a FULL page", out)
        # …and the remedy it implies is the one that works at this setting — on
        # the VERDICT line, which is what a caller keys its next action on.
        self.assertIn("Widen with --pr-limit", out)
        self.assertIn("widen with --pr-limit", self._verdict_line(out).lower())
        # ⛔ THE NOTE MUST READ AS ONE SENTENCE (#9423 review, P3). The aside
        # ("… because --pr-limit asked for 20") used to run straight into the
        # main verb, so the report said a LARGER page "was requested and
        # returned" — the opposite of what happened.
        self.assertIn("— and returned a FULL page", out)
        self.assertNotIn("asked for 20 and returned a FULL page", out)

        # The >= 100 arm: GitHub's maximum really WAS the requested size there,
        # so the note may say so — and must NOT offer a widening that does not
        # exist.
        cap = mod._REST_PULLS_PAGE
        self.gh_fixtures(open_prs=[{
            "number": 9500 + n, "title": f"chore: unrelated {n}", "body": "",
            "headRefName": f"chore/95{n}-unrelated", "state": "open",
        } for n in range(cap)])
        rc, out = self.run_tool(extra_args=["--pr-limit", str(cap)],
                                env_extra={"GH_STUB_PR_LIST_RATE_LIMITED": "1"})
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertIn(f"requested per_page of {cap}, GitHub's maximum", out)
        # ⛔ CASE-INSENSITIVE ON PURPOSE (#9423 review, P3): the surface note says
        # "Widen with --pr-limit" (capital W) while the VERDICT remedy said
        # "widen with --pr-limit" (lowercase) — so the case-sensitive assertion
        # here passed on a report that prescribed the widening twenty lines below
        # the note saying no larger page could be requested. Asserting on the
        # report text, case-proof, makes the missing cure actually pinned.
        self.assertNotRegex(out, r"(?i)widen with --pr-limit")
        # …and the VERDICT states the cap as a cap, not as a setting to raise.
        verdict = self._verdict_line(out)
        self.assertIn("TRUNCATED list", verdict)
        self.assertIn("NO SETTING to widen", verdict)
        self.assertNotRegex(verdict, r"(?i)widen with --pr-limit")
        # The aside is CLOSED before the main verb resumes: "no larger page can
        # be requested — and returned a FULL page" (#9423 review, P3). The
        # broken sentence said the opposite of what happened.
        self.assertIn("— and returned a FULL page", out)
        self.assertNotIn("can be requested and returned a FULL page", out)

    def test_truncation_remedy_names_the_pr_limit_not_gh_auth(self):
        # #9423 review (P3). `_incomplete_remedy` reads `s.note`, but
        # `mark_truncated` writes `truncation_note` and never `note` — so the
        # PURE-GraphQL truncation case (no exhaustion, no note) took the generic
        # arm and ended the report with "fix gh auth/network (or rebuild the fleet
        # board)" for a cause the report had just measured as a completeness CAP,
        # while the surface's own truncation note says to widen `--pr-limit`.
        self.gh_fixtures(open_prs=[
            {"number": 1, "title": "a", "body": "", "headRefName": "chore/a"},
            {"number": 2, "title": "b", "body": "", "headRefName": "chore/b"},
        ])
        rc, out = self.run_tool(extra_args=["--pr-limit", "1"])
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        # The cause is named, and it is the one the surface measured.
        self.assertIn("TRUNCATED list", out)
        self.assertIn("--pr-limit", out)
        # …and the remedy is NOT the re-authenticate/rebuild advice, which cannot
        # help with a cap.
        self.assertNotIn("fix gh auth/network", out)
        self.assertNotIn("rebuild the fleet board and re-run", out)

    @staticmethod
    def _verdict_line(out: str) -> str:
        """The ONE `VERDICT:` line of a report, so a cause is asserted on the
        line a caller actually keys its remedy on (not merely somewhere in the
        report, where the surface rows repeat it)."""
        lines = [
            line for line in out.splitlines()
            if line.startswith("VERDICT: INCOMPLETE")
        ]
        if len(lines) != 1:
            raise AssertionError(f"expected exactly one VERDICT line: {out}")
        return lines[0]

    def test_both_causes_on_one_surface_are_both_named(self):
        # #9423 review (P3-1). ONE surface can carry BOTH measured causes at
        # once, and the REST rescue makes that routine rather than a corner:
        # `--pr-limit 20` + 20 open PRs + an exhausted GraphQL budget leaves the
        # open-PR surface `incomplete()` (closing-references-unmeasured) AND
        # `truncated` (the REST page came back FULL). The remedy tested "all
        # incomplete surfaces are exhausted" FIRST, so it returned the budget
        # cause alone and dropped the truncation cause — the cause whose cure is
        # a SETTING the operator controls, so re-running after the budget resets
        # at `--pr-limit 20` truncates again.
        #
        # The `assertIn("Widen with --pr-limit")` in the test above cannot red
        # this, because that string comes from the SURFACE's truncation note; the
        # cause is only stated in the VERDICT line, which is what a caller keys
        # its remedy on.
        prs = [{"number": 9000 + n, "title": f"chore: unrelated {n}",
                "body": "", "headRefName": f"chore/9{n}-unrelated",
                "state": "open"}
               for n in range(20)]
        self.gh_fixtures(open_prs=prs)
        rc, out = self.run_tool(extra_args=["--pr-limit", "20"],
                                env_extra={"GH_STUB_PR_LIST_RATE_LIMITED": "1"})
        self.assertEqual(rc, 2, out)
        verdict = self._verdict_line(out)
        self.assertIn("BUDGET EXHAUSTION", verdict)
        self.assertIn("TRUNCATED list", verdict)
        # …and the surface is named ONCE as a cause-carrier (`open PRs:`), not
        # twice. The "(open PRs)" earlier in the line is the surface LIST from
        # the verdict prefix, not a contributor mention.
        self.assertEqual(verdict.count("open PRs:"), 1, verdict)

    def test_a_long_stderr_does_not_hide_the_exhaustion_from_the_rescue(self):
        """The rescue's gate sees the WHOLE failure, not a display cut (P1).

        `_is_budget_exhaustion` is a substring test. It used to be run over the
        `SurfaceError` MESSAGE, and the only untruncated text available was the
        one inside it — so whatever the message cut away, the classifier could
        not see. `_gh_json` one-lines the STDERR at 200 characters, and `gh`
        prints transport chatter before the GraphQL error on a verbose/retrying
        run: past 200 characters of it, the canonical `GraphQL: API rate limit
        already exceeded for user ID …` is gone from everything the gate reads.
        The rescue then never fired and the report prescribed `gh auth/network`
        for a spent budget — the exact defect #9402 files, on the exact surface
        this PR fixes.

        ⛔ MEASURED SCOPE, so the next reader does not over-claim it: the cut is
        applied to the STDERR, NOT to the assembled message — the command line
        is prefixed afterwards. So a long `--repo` slug alone does NOT hide the
        signature (verified against this head: a 40-character slug still
        rescued), and the finding's "slug >= 36" table measured the wrong
        string. The hazard is real all the same, and this seam is the string it
        applies to: 240 characters of stderr, discriminator at offset 262.
        """
        # The long slug stays in the fixture: it is what made the original
        # report's arithmetic look like the cause, and it must not start
        # mattering again.
        long_slug = "some-organisation/a-long-repository-name"
        self.assertEqual(len(long_slug), 40)
        _git(self.repo, "remote", "set-url", "origin",
             f"https://github.com/{long_slug}.git")
        # The board's `repo` stamp is compared against the queried slug, so it
        # has to name the new one — otherwise this would measure the claim
        # surface, not the rescue.
        self.fleet_board(repo=long_slug)
        rc, out = self.run_tool(env_extra={"GH_STUB_PR_LIST_LONG_STDERR": "1"})
        self.assertEqual(rc, 2, out)
        # THE RESCUE FIRED, and it asked for the LONG slug's own pulls.
        argv_path = self.gh_dir / "open-prs-rest-argv.txt"
        self.assertTrue(argv_path.exists(), out)
        self.assertIn(f"repos/{long_slug}/pulls?state=open", argv_path.read_text())
        # …so the refusal names the UNMEASURED test, and the remedy names the
        # BUDGET — never the re-authenticate/rebuild advice that cannot help.
        self.assertIn("closing-references-unmeasured", out)
        self.assertIn("BUDGET EXHAUSTION", out)
        self.assertNotIn("fix gh auth/network", out)

    def test_a_long_slug_alone_does_not_hide_the_exhaustion(self):
        """The boundary the P1 report measured — and it is NOT a boundary.

        Keeping this as an explicit arm is deliberate: P1 as filed said the
        rescue is gated on a signature the message has already cut off for any
        `--repo` slug of 36+ characters, and computed a table of slug lengths.
        That table measured a truncation the tool never performs — `_gh_json`
        one-lines the STDERR at 200 characters and then PREFIXES the command
        line, so the message is longer than the cut and the discriminator is
        always inside it. A 40-character slug therefore rescued even on the
        pre-fix tool.

        So the substantive fix is the STDERR arm above (`SurfaceError.detail`);
        this arm pins the measured NON-boundary, so a future narrowing of
        `_gh_json` — moving the cut onto the assembled message — reds here
        instead of silently re-introducing the filed defect at 36 characters.
        """
        long_slug = "some-organisation/a-long-repository-name"
        self.assertEqual(len(long_slug), 40)
        _git(self.repo, "remote", "set-url", "origin",
             f"https://github.com/{long_slug}.git")
        self.fleet_board(repo=long_slug)
        rc, out = self.run_tool(env_extra={"GH_STUB_PR_LIST_RATE_LIMITED": "1"})
        self.assertEqual(rc, 2, out)
        self.assertTrue((self.gh_dir / "open-prs-rest-argv.txt").exists(), out)
        self.assertIn("BUDGET EXHAUSTION", out)
        self.assertNotIn("fix gh auth/network", out)

    def test_a_both_cause_surface_is_named_once_in_the_mixed_remedy(self):
        # #9423 review (P3-1), the MIXED arm of the same root cause. With another
        # incomplete surface present, `parts` named the SAME surface twice — once
        # as exhausted and once as truncated — so the report said
        # "open PRs: <budget>; PART is open PRs: <truncation>". Folding a
        # both-flagged surface into one contributor list is what makes the list
        # of causes read as a list of CAUSES rather than of flags.
        self.gh_fixtures(open_prs=[{
            "number": 9000 + n, "title": f"chore: unrelated {n}",
            "body": "", "headRefName": f"chore/9{n}-unrelated",
            "state": "open",
        } for n in range(20)])
        rc, out = self.run_tool(
            extra_args=["--pr-limit", "20"],
            env_extra={"GH_STUB_PR_LIST_RATE_LIMITED": "1",
                       "GH_STUB_TIMELINE_FAIL": "1"},
        )
        self.assertEqual(rc, 2, out)
        verdict = self._verdict_line(out)
        self.assertIn("PART of this is", verdict)
        self.assertEqual(verdict.count("open PRs:"), 1, verdict)
        self.assertEqual(verdict.count("TRUNCATED list"), 1, verdict)
        self.assertEqual(verdict.count("BUDGET EXHAUSTION"), 1, verdict)
        # The second cause-carrier is the landed surface, whose failure IS a
        # read failure — so the generic remedy is kept for it, not dropped.
        self.assertIn("issue landed", verdict)
        self.assertIn("fix gh auth/network", verdict)

    def test_graphql_exhaustion_with_rest_also_down_still_fails_closed(self):
        # The fallback must never become a fail-open. With BOTH budgets gone the
        # tool has genuinely read nothing, so it must still refuse — otherwise
        # "try REST" would convert an unmeasured issue into a CLEAN verdict.
        # ⛔ AND IT MUST NAME THE REST REFUSAL (#3435 review, P2): without that
        # assertion this test also passes on a tool with NO fallback at all (the
        # stub's `issue view` fails first), so it would pin nothing.
        rc, out = self.run_tool(env_extra={
            "GH_STUB_ISSUE_VIEW_RATE_LIMITED": "1",
            "GH_STUB_ISSUE_REST_FAIL": "1",
        })
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        self.assertIn("issue-metadata-unavailable", out)
        self.assertIn("gh api repos/test-owner/test-repo/issues/3061 failed", out)
        self.assertIn("Resource not accessible", out)

    def test_a_rest_200_that_is_a_pull_request_is_not_read_as_an_issue(self):
        # ⛔ THE TRANSPORT MUST NOT DECIDE THE ANSWER (#3435 review, P2).
        # `/repos/<slug>/issues/<N>` is the ISSUE-OR-PULL-REQUEST endpoint, and a
        # PR object carries `number/title/state/assignees/html_url` — every key
        # the normaliser reads — so it satisfied the payload contract and was
        # read as an ISSUE. Pre-fix this run came back `VERDICT: CLEAN` (the
        # fail-open direction) while `gh issue view` refuses the same input.
        rc, out = self.run_tool(env_extra={
            "GH_STUB_ISSUE_VIEW_RATE_LIMITED": "1",
            "GH_STUB_ISSUE_REST_PULL_REQUEST": "1",
        })
        self.assertNotIn("Traceback", out)
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        self.assertIn("is a PULL REQUEST, not an issue", out)

    def test_a_rest_body_about_another_issue_is_refused(self):
        # A misrouted body must not be read as THIS issue's state and assignees:
        # an `OPEN` with no assignees reads free. Pre-fix the answer was CLEAN.
        rc, out = self.run_tool(env_extra={
            "GH_STUB_ISSUE_VIEW_RATE_LIMITED": "1",
            "GH_STUB_ISSUE_REST_WRONG_NUMBER": "1",
        })
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        self.assertIn("answered for #3062, not #3061", out)

    def test_a_non_utf8_rest_body_is_a_named_incomplete_not_a_traceback(self):
        # ⛔ `subprocess.run(text=True)` decodes in STRICT mode (#3435 review,
        # P2), so one bad byte raised `UnicodeDecodeError` — a ValueError, NOT a
        # `SurfaceError` — which passed every handler and escaped as a traceback
        # with no VERDICT line and exit 1 (COLLISION).
        #
        # Refusing beats `errors="replace"` HERE: replacement characters would
        # manufacture a payload no endpoint sent (the same reasoning that makes a
        # substituted slug a silent wrong answer). The identical hole on the
        # PRIMARY reads is pre-existing, lives at the `_run` boundary, and is NOT
        # yet covered by any test — tortoise#8881 needs this case written for the
        # primary surfaces too.
        rc, out = self.run_tool(env_extra={
            "GH_STUB_ISSUE_VIEW_RATE_LIMITED": "1",
            "GH_STUB_ISSUE_REST_INVALID_UTF8": "1",
        })
        self.assertNotIn("Traceback", out)
        self.assertNotIn("UnicodeDecodeError", out)
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertIn("not decodable as text", out)

    def test_a_non_utf8_rest_open_pr_body_is_a_named_incomplete_not_a_traceback(self):
        # #9423 review (P3): the REST rescue's `except ValueError` arm is the
        # strict-decoding refusal `_issue_view_rest` has carried since #3435, and
        # it was UNPINNED — no seam wrote a bad byte to the open-PR REST fixture,
        # so DELETING the arm left the whole suite green. That matters because
        # the rescue is called from INSIDE `run_preflight`'s `except
        # SurfaceError` body: an uncaught `UnicodeDecodeError` (a ValueError, not
        # a SurfaceError) bypasses that handler AND `main()`'s refusal pair, and
        # escapes as a traceback with no VERDICT line at exit 1 — the code this
        # module documents as COLLISION, which callers read as "another lane is
        # on it".
        rc, out = self.run_tool(env_extra={
            "GH_STUB_PR_LIST_RATE_LIMITED": "1",
            "GH_STUB_OPEN_PR_REST_INVALID_UTF8": "1",
        })
        self.assertNotIn("Traceback", out)
        self.assertNotIn("UnicodeDecodeError", out)
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertIn("not decodable as text", out)

    def test_a_malformed_rest_payload_is_a_named_incomplete_not_a_collision(self):
        # ⛔ #3435 review cycle 2, P1. `_require_issue_payload` lives INSIDE the
        # caller's `except SurfaceError` handler, and `main()` catches only
        # NotAWorkItem / RuntimeError (`SurfaceError` is a plain Exception). So a
        # malformed REST body must never RAISE from there: doing so escaped as a
        # traceback with no VERDICT line and exited 1 — the code this tool
        # defines as COLLISION, so an operator reads a false collision and waits
        # for a lane that never appears.
        #
        # Pre-fix this test fails twice over: `rescued` was set to None in the
        # handler and the very next statement called `rescued.get(...)`, giving
        # `AttributeError` -> exit 1; and the issue surface was never marked
        # INCOMPLETE, so the reason vanished with it.
        rc, out = self.run_tool(env_extra={
            "GH_STUB_ISSUE_VIEW_RATE_LIMITED": "1",
            "GH_STUB_ISSUE_REST_MALFORMED": "1",
        })
        self.assertNotIn("Traceback", out)
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        # The issue surface is the one that must be NAMED incomplete — the
        # landed surface's note alone would leave the actual cause unrecorded.
        self.assertIn("[issue assignee/holder]", out)
        self.assertIn("non-list 'assignees'", out)
        self.assertIn("issue-metadata-unavailable", out)

    def test_a_rest_transport_failure_names_the_transport_not_a_missing_issue(self):
        # ⛔ THE STUB'S SLUG GUARD THAT NOTHING COULD FIRE (#3435 review, P2).
        # `probe_unqueried.txt` had exactly ONE writer — the cross-repo CANDIDATE
        # (`other-owner/other-repo`) — while the REST branch is reached only for
        # the TARGET slug, so `grep -qx "$_slug"` could never match: the guard,
        # and the path-derived `$_slug` it exists for, were dead code that merely
        # LOOKED like protection. That is the same defect the branch's own `$repo`
        # note names, left unexamined three lines below it.
        #
        # Reachable, and this is the fixture that reaches it: `--repo` is given
        # (so the ambiguity probe runs no `--json number` read at all and the
        # target slug in `probe_unqueried.txt` has no other reader) while
        # `issue view` is rate-limited, so the fallback runs and the endpoint
        # fails for the target slug specifically.
        #
        # WHAT IT PINS, in both directions: the guard must FIRE (deleted or
        # re-keyed on the empty `$repo`, the valid REST body is read and the run
        # goes CLEAN at rc 0), and the refusal must be classified as a TRANSPORT
        # failure — `gh-unavailable`, the pre-existing fail-closed wording — and
        # not mistaken for `issue-absent`, which would report a live issue as
        # nonexistent.
        (self.gh_dir / "probe_unqueried.txt").write_text("test-owner/test-repo\n")
        rc, out = self.run_tool(env_extra={"GH_STUB_ISSUE_VIEW_RATE_LIMITED": "1"})
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        self.assertIn("could not connect to api.github.com", out)
        self.assertIn("gh-unavailable", out)
        self.assertNotIn("issue-absent", out)

    def test_a_rest_404_during_exhaustion_reads_as_issue_absent(self):
        # #3435 review (minor): a REST 404 used to be swallowed into `None`, so
        # the fall-through classified the RATE-LIMIT exception and a genuinely
        # absent issue reported `gh-unavailable`. Same exit code, wrong reason —
        # and "the budget is spent" is not why the issue was not found.
        rc, out = self.run_tool(env_extra={
            "GH_STUB_ISSUE_VIEW_RATE_LIMITED": "1",
            "GH_STUB_ISSUE_REST_ABSENT": "1",
        })
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertIn("issue-absent", out)
        self.assertNotIn("gh-unavailable", out)

    def test_a_closed_issue_rescued_over_rest_keeps_its_reason(self):
        # ⛔ THE ONE KEY REST SPELLS DIFFERENTLY (#3435 review, P2).
        # `_issue_view_rest` maps REST's `state_reason` onto the `stateReason`
        # key `gh issue view --json` returns. Every other REST fixture here
        # answers an OPEN issue with `state_reason: null`, so deleting that
        # mapping kept the whole suite green while a CLOSED issue rescued over
        # REST silently lost its reason and printed the generic "the work item
        # is TERMINAL" wording instead of naming the closure that happened —
        # the #8497 wording defect (`state_reason=not_planned` read as a
        # shipment), reached through a second key spelling.
        #
        # It is also the only case that decides LANDED from a REST body ALONE:
        # a terminal state skips the timeline query, so no other test reaches
        # that branch with the rescue in play (they are all OPEN).
        rc, out = self.run_tool(env_extra={
            "GH_STUB_ISSUE_VIEW_RATE_LIMITED": "1",
            "GH_STUB_ISSUE_REST_CLOSED": "not_planned",
        })
        self.assertNotIn("Traceback", out)
        self.assertEqual(rc, 4, out)
        self.assertIn("VERDICT: LANDED (exit 4)", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        # REST says `closed`/`not_planned`; both consumers upper-case and
        # `-`->`_`-fold, so the reason must reach the wording intact.
        self.assertIn("issue state=closed", out)
        self.assertIn("closed as NOT PLANNED", out)
        self.assertIn("was CLOSED AS NOT PLANNED", out)
        self.assertNotIn("ALREADY SHIPPED", out)
        # A terminal issue must not pay for a timeline query it cannot need.
        self.assertFalse((self.gh_dir / "timeline-calls.log").exists(), out)

    def test_a_non_object_rest_body_is_a_named_incomplete_not_a_traceback(self):
        # ⛔ `if not isinstance(raw, dict)` had NO fixture (#3435 review, P2):
        # every other refusal in the REST reader was pinned, this one was not.
        # Unpinned it is untested code on the path whose whole job is that a
        # malformed body can never become exit 1 — without the guard,
        # `raw.get(...)` on a list is an `AttributeError`, which `main()` does
        # not catch, so it escapes as a traceback with no VERDICT line and
        # exits 1 (the code this tool documents as COLLISION).
        rc, out = self.run_tool(env_extra={
            "GH_STUB_ISSUE_VIEW_RATE_LIMITED": "1",
            "GH_STUB_ISSUE_REST_NON_OBJECT": "1",
        })
        self.assertNotIn("Traceback", out)
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        self.assertIn("non-object JSON (list)", out)
        self.assertIn("issue-metadata-unavailable", out)

    def test_a_truncated_board_still_blocks_a_live_holder(self):
        # The truncation refusal is scoped to ABSENCE: a board that DID attribute a
        # live lane to this issue still blocks. Otherwise "the read was narrow"
        # would itself become a way to clear a known holder.
        self.fleet_board(issues={ISSUE: [("lane-live", "branch slug")]},
                         lanes=[{"label": "lane-live", "live": True}],
                         open_issues_truncated=True)
        rc, out = self.run_tool()
        self.assertEqual(rc, 1, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("holder:lane-live", out)
        self.assertNotIn("VERDICT: CLEAN", out)

    def test_a_lane_with_no_session_record_blocks_rather_than_reading_free(self):
        # ⛔ THE FALSE-CLEAN, END TO END. The real board's sessionless shape:
        # `pid_alive` present and False by DEFAULT, `liveness_measured` False.
        # Reading the default as "stopped" would print CLEAN for a lane whose
        # writer was never observed.
        self.fleet_board(
            issues={ISSUE: [("sessionless", "branch slug")]},
            lanes=[{"label": "sessionless", "live": False, "pid_alive": False,
                    "pi_pid": None, "liveness_measured": False}],
        )
        rc, out = self.run_tool()
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        self.assertIn("DEFAULT rather than a measurement", out)

    def test_a_malformed_attribution_entry_blocks_rather_than_reading_free(self):
        state = _fleet_board_state(
            issues={ISSUE: [("lane-live", "e")]},
            lanes=[{"label": "lane-live", "live": True}],
        )
        state["index"]["issues"][str(ISSUE)] = {"lanes": "lane-live"}
        _write_board(self.home / ".pi" / "agent" / "state" / "fleet-state.json",
                     state)
        rc, out = self.run_tool()
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        self.assertIn("no usable `lanes` list", out)

    def test_lane_attributed_but_not_enumerated_is_incomplete(self):
        # A missing lane RECORD cannot be read as "stopped" — the board named a
        # holder it cannot describe, so the question is unanswered. Fail closed.
        self.fleet_board(issues={ISSUE: [("ghost-lane", "branch slug")]},
                         lanes=[])
        rc, out = self.run_tool()
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        self.assertIn("does not enumerate", out)

    def test_ambiguous_attribution_is_incomplete_even_when_live(self):
        # An ambiguous attribution is decided BEFORE liveness: the board refused
        # to guess whose session the lane is, so no reading of that lane's
        # liveness can be trusted.
        #
        # ⛔ ONLY AN *UNRESOLVED* `guess_refused` IS AMBIGUOUS. `resolve_sessions`
        # copies a dangling pane-binding note onto a resolution a LATER rule then
        # RESOLVED (`bound=True`), so the note alone does not mean "refused" —
        # see `test_a_resolved_lane_with_a_dangling_binding_note_is_not_ambiguous`.
        # `shared_with` is a genuine partition violation and is ambiguous even for
        # a lane that otherwise looks live.
        for spec in ({"live": None, "bound": False,
                      "guess_refused": "two sessions resolve to this lane"},
                     {"live": True, "shared_with": ["lane-b"]}):
            with self.subTest(spec=spec):
                lane = {"label": "lane-amb"}
                lane.update(spec)
                self.fleet_board(
                    issues={ISSUE: [("lane-amb", "worktree path /tmp/wt/amb")]},
                    lanes=[lane],
                )
                rc, out = self.run_tool()
                self.assertEqual(rc, 2, f"{spec}\n{out}")
                self.assertIn("VERDICT: INCOMPLETE", out)
                self.assertIn("ambiguous", out)
                self.assertNotIn("VERDICT: CLEAN", out)

    def test_a_live_holder_cannot_be_dismissed_by_env_or_flag(self):
        # NO DISMISSAL SWITCH. Every historical escape hatch is dead, and none of
        # them may clear a live holder: the deleted claim path's JEV switch and
        # claim cache, plus the env var names a caller might GUESS at for a board
        # redirect (none is read — the board is `fleet_state.DEFAULT_OUT`, and
        # `test_the_board_is_read_from_default_out_not_from_a_redirected_home`
        # pins the read to that constant rather than to a re-derived `$HOME`).
        self.fleet_board(issues={ISSUE: [("lane-live", "branch slug")]},
                         lanes=[{"label": "lane-live", "live": True}])
        empty = _write_board(self.tmp / "empty.json", _fleet_board_state())
        hostile = {
            "COLLISION_PREFLIGHT_JEV": "off",
            "COLLISION_PREFLIGHT_CLAIM_CACHE": str(self.tmp / "c.json"),
            "COLLISION_PREFLIGHT_FLEET_STATE": str(empty),
            "FLEET_STATE_PATH": str(empty),
            "FLEET_STATE_OUT": str(empty),
        }
        with mock.patch.dict(os.environ, hostile):
            rc, out = self.run_tool(env_extra=hostile)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("holder:lane-live", out)
        # A dismissal-looking FLAG is a usage error, not a silent acceptance.
        rc, out = self.run_tool(extra_args=["--ignore-holder"])
        self.assertNotEqual(rc, 0, out)
        self.assertIn("ignore-holder", out)

    def test_zero_ahead_branch_still_blocks_the_branch_surface(self):
        # #6108 (REOPENED 2026-10-24): a 0-ahead branch is demoted ONLY when every mechanical
        # piece of holder evidence is absent. This fixture has NO `origin/main`, so the
        # distance read fails — and an UNREADABLE reading is not an empty claim, so the ref
        # keeps BLOCKING (the run even says so: `UNREADABLE … NOT treated as an empty
        # claim`). The demotion direction is pinned by
        # `test_a_zero_ahead_clean_unowned_unclaimed_branch_is_advisory`; this test pins the
        # fail-closed half, which is why it must not be deleted as "the old behaviour".
        #
        # The deleted artifact-verifier used to demote a CLAIM whose named branch existed
        # with 0 commits ahead; deleting the prose path must not make the BRANCH surface
        # start ignoring one. The branch here IS the tip of main — zero commits of its own.
        branch = f"fix/{ISSUE}-zero-ahead"
        _git(self.repo, "branch", branch)
        ahead = subprocess.run(
            ["git", "rev-list", "--count", f"main..{branch}"],
            cwd=self.repo, capture_output=True, text=True, check=True,
        ).stdout.strip()
        self.assertEqual(ahead, "0", f"the branch is not zero-ahead: {ahead}")
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("[local branches]", out)

    def test_report_states_the_claim_gate_it_actually_ran(self):
        # The report names its own mechanism. A report that still described a
        # `_CLAIM_RE` pre-filter and a JEV typed decision would be a claim about
        # a reader that no longer exists — the unverifiable-verdict class this
        # change removes.
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("claim gate: HOLDER liveness from the FLEET BOARD", out)
        self.assertIn("NO dismissal switch", out)
        self.assertNotIn("JEV", out)
        self.assertNotIn("_CLAIM_RE", out)
        # ⛔ AND IT MUST NOT NAME `lane_live_map`. The gate deliberately does NOT
        # read it (it folds a terminal workspace to `False` even when a process
        # outlived it, and returns `None` for a sessionless lane) — naming it in
        # the report sends a debugging reader to the wrong function, with the
        # opposite polarity at both edges.
        self.assertNotIn("lane_live_map", out)

    # ── keyword matching ────────────────────────────────────────────────────

    def test_repo_generic_vocabulary_never_blocks(self):
        # #3325 / #4375 / #3504 class 5, now STRUCTURAL rather than stoplist-
        # driven. The old fix (a stoplist of "generic" terms, plus a >= 2
        # distinctive-term gate) was proven unfalsifiable: `capture`, `verify`,
        # `retrieval` and `temporal` are all absent from the stoplist and all
        # >= 5 chars, so all were labelled "distinctive" — the test could never
        # fail no matter which words were added.
        #
        # The lexical arm is DELETED, so this is no longer a property of a word
        # list but of the absence of one: shared domain vocabulary is never
        # consulted, in any quantity, at any threshold.
        title = ("fix(tests): unscoped wipe_server guard has a TOCTOU window "
                 "(peer graph minted between the protection snapshot and the delete)")
        self.gh_fixtures(
            issue=self.issue_payload(title=title),
            open_prs=[{
                "number": 3354,
                "title": "fix(infra): race-safe GRAPH.DELETE — stop poisoning "
                         "the shared FalkorDB AOF (#2961)",
                "body": "", "headRefName": "fix/2961-graph-delete-race",
            }],
            closed_prs=[{
                "number": 2704,
                "title": "feat(graphs): rename graphs (pencil) + type-to-confirm "
                         "delete (#2701)",
                "body": "", "headRefName": "feat/2701-graphs-rename-delete",
            }],
        )
        self.add_worktree("feat-2701-graphs-rename-delete",
                          branch="feat/2701-graphs-rename-delete")
        _git(self.repo, "branch", "fix/2961-graph-delete-race")
        # ...and the DISTINCTIVE terms too, which USED to be enough to block.
        _git(self.repo, "branch", "fix/toctou-window-guard")
        rc, out = self.run_tool(issue=3214)
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        # `VERDICT:`-scoped: the report's `claim gate:` rules line legitimately
        # names the COLLISION band and its threshold (#5070), so a bare
        # substring check would read the RULES as a verdict.
        self.assertNotIn("VERDICT: COLLISION", out)
        self.assertNotIn("do NOT dispatch", out)

    def test_generic_only_title_is_clean_and_a_number_still_blocks(self):
        # #3325 latent, re-pointed at the new mechanism. A title made entirely
        # of shared vocabulary used to be an EVALUATED-but-EMPTY keyword
        # dimension, and conflating "no distinctive keyword" with "unqueryable
        # surface" turned a clean run into exit 2.
        #
        # That whole failure mode is gone with the lexical arm: the title is no
        # longer an input to any decision. It is still PRINTED, because naming
        # the target in full is what makes a wrong-target read visible (#4027).
        self.gh_fixtures(issue=self.issue_payload(title="fix graph delete error"))
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertNotIn("VERDICT: INCOMPLETE", out)
        self.assertIn("fix graph delete error", out)

        # ... and a NUMBER hit under that generic-only title is still a hit. The
        # number is the reference; the prose is not.
        _git(self.repo, "branch", "fix/3061-generic-title")
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("matched issue-number (3061)", out)

    def test_worktree_structural_token_is_not_a_collision(self):
        # Title contains "worktree"; the worktree lives under a `.worktrees/`
        # parent. The structural directory token must not fabricate a hit.
        self.gh_fixtures(issue=self.issue_payload(title="investigate worktree enumeration"))
        self.add_worktree(".worktrees/plain-1")
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)

    def test_number_boundary_no_false_positive(self):
        # 30610 must NOT match 3061 — no substring collisions.
        _git(self.repo, "branch", "fix/30610-other")
        self.gh_fixtures(open_prs=[{
            "number": 1, "title": "30610 unrelated PR", "body": "see 30610",
            "headRefName": "fix/30610-other",
        }])
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)

    def test_own_pushed_branch_is_not_a_claim(self):
        # P2-2. A lane that pushes has its own work in TWO namespaces: the local
        # `refs/heads/<branch>` it declared, and the remote-tracking
        # `refs/remotes/origin/<branch>` the push created. Stripping only
        # `refs/heads/` left the remote copy comparing unequal, so it still
        # blocked STRONGLY and the lane refused its own dispatch — #3504 class 4
        # surviving in the one namespace every lane actually populates.
        ref = f"fix/{ISSUE}-pushed"
        _git(self.repo, "branch", ref)
        _git(self.repo, "update-ref", f"refs/remotes/origin/{ref}", "HEAD")

        # (a) UNDECLARED: both namespaces are hits.
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("[remote branches]", out)

        # (b) DECLARED by its SHORT name: BOTH namespaces must demote.
        rc, out = self.run_tool(self_branches=(ref,))
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertIn("your own branch", out)

        # (c) ...and a FULL ref declaration must match the short candidate too —
        # the asymmetry the old docstring claimed away.
        rc, out = self.run_tool(self_branches=(f"refs/heads/{ref}",))
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)

    def test_remote_branch_number_match_still_blocks(self):
        # The remote-branch surface is number-matched ONLY, and that is now the
        # only matching that exists anywhere. Two properties, both pinned:
        # (a) a remote ref carrying the issue number DOES block, so the surface
        #     is not inert; and
        # (b) a remote ref carrying only shared vocabulary does NOT — the shape
        #     that produced 878 false hits on a real run.
        _git(self.repo, "update-ref",
             "refs/remotes/origin/fix/florfenicol-dosing", "HEAD")
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)

        _git(self.repo, "update-ref",
             f"refs/remotes/origin/fix/{ISSUE}-remote-live", "HEAD")
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("[remote branches]", out)

    # ── self-identity: the caller's own work is not a competing claim ───────
    #
    # #3504 classes 1 and 4. The fix is an INPUT rather than a heuristic — the
    # caller declares its own refs, because "is this mine?" is not a property of
    # any string.
    #
    # EVERY guard below is pinned in BOTH directions, but not always inside one
    # method — the pairing is structural, so it is stated once here rather than
    # claimed per test:
    #
    #   · the two SELF-IDENTITY tests assert both halves in one method (they
    #     call `run_tool` twice: undeclared must COLLIDE, declared must not);
    #   · the terminal-predicate and closing-field guards are SENSITIVITY
    #     PAIRS of sibling tests, each differing from its partner in exactly one
    #     fixture field — merged vs merely closed, ancestor vs ahead, field
    #     absent vs present-but-empty;
    #   · the two verdict/count tests cover the class-2 axis from opposite
    #     sides (only-weak must stay CLEAN; one-strong-plus-weak must count 1).
    #
    # An earlier version of this comment claimed "EVERY test below asserts BOTH
    # halves". That was FALSE — eleven of them assert a single verdict and rely
    # on a sibling for the opposite one — and a VERIFIER CAUGHT IT. The lesson
    # generalises: a universal claim about a set is checkable, so it gets checked
    # and it breaks; describe the structure instead of universalising over it.

    def _defaults_seen_by_the_tool(self) -> set:
        """What the tool's own refusal set computes for this repo.

        Reaches into the module rather than re-deriving the rule in the test: a
        test that re-implemented the parse could pass while the tool's parse was
        broken — which is exactly the failure mode P1-A was.
        """
        return _tool_module()._default_branch_names("git", str(self.repo), 30)

    def _git_out(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.repo, check=True,
                              capture_output=True, text=True).stdout.strip()

    def test_own_branch_declared_is_not_a_claim_but_undeclared_still_blocks(self):
        _git(self.repo, "branch", f"fix/{ISSUE}-mine")
        # (a) UNDECLARED — a branch naming the issue IS a competing claim.
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn(f"matched issue-number ({ISSUE})", out)

        # (b) DECLARED — the same branch, named as ours, cannot block.
        rc, out = self.run_tool(self_branches=(f"fix/{ISSUE}-mine",))
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertIn("your own branch", out)
        # The strength marker on this line is `(weak)`; "(non-blocking)" is the
        # WEAK SIGNALS heading's wording, not this line's.
        self.assertIn("(weak)", out)
        self.assertIn("not a competing claim", out)

    def test_current_branch_is_declared_automatically(self):
        # `--self-branch` is authoritative, but the checkout's own branch is
        # added best-effort too: a lane that forgets the flag must still not
        # collide with the branch it is standing on. Without the auto-detection
        # this is a COLLISION — the guard is what makes it CLEAN.
        _git(self.repo, "checkout", "-q", "-b", f"fix/{ISSUE}-auto")
        # `cwd=self.repo`: auto-detection now requires that the process is
        # STANDING IN the target checkout, so the test must actually run from it.
        rc, out = self.run_tool(cwd=self.repo)
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertIn("your own branch", out)

    def test_own_worktree_declared_is_not_a_claim_but_undeclared_still_blocks(self):
        # ⛔ A DETACHED worktree, deliberately. `scan_worktree_surface` suppresses
        # on `owns_worktree(path) OR owns_branch(branch)`, so a worktree on a
        # branch would be suppressed by the BRANCH check even with
        # `owns_worktree` disabled — the test would then pass while proving
        # nothing about it. That is not hypothetical: it is exactly how an
        # earlier version of this test survived its own mutation (M3). Detached
        # means no branch ref exists anywhere, so the worktree PATH is the only
        # match and `owns_worktree` is the only thing that can suppress it.
        wt = self.add_worktree(f"fix-{ISSUE}-mine")
        # (a) UNDECLARED.
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("[local worktrees]", out)
        self.assertIn(f"matched issue-number ({ISSUE})", out)

        # (b) DECLARED — and nothing else, so the suppression can only come
        # from the worktree identity.
        rc, out = self.run_tool(self_worktrees=(str(wt),))
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertIn("your own worktree", out)

    def test_self_pr_is_decided_before_any_match_test(self):
        # #4567's ordering root cause, SELF arm. The suppression used to sit
        # after the keyword test, which `continue`d unconditionally, so it was
        # DEAD CODE whenever both matched.
        #
        # ⛔ The head branch is DECLARED here. An earlier version of this test
        # left it undeclared, so the self arm never fired and the PR was decided
        # by the "PR *is* the issue" arm instead — the test passed while covering
        # the wrong branch, and its comment ("the self message wins") was false.
        # Found by review. The sibling test below pins the OTHER arm.
        self.gh_fixtures(open_prs=[{
            "number": 5150, "title": f"feat: do the thing (#{ISSUE})",
            "body": f"Closes #{ISSUE}",
            "headRefName": f"feat/{ISSUE}-self",
            "state": "open",
            "closingIssuesReferences": [{"number": ISSUE}],
        }])
        rc, out = self.run_tool(self_branches=(f"feat/{ISSUE}-self",))
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertIn("your own PR", out)
        # ORDER IS OBSERVABLE: the self decision wins over the three match tests
        # that would otherwise have fired (title number, head number, closing
        # reference). A re-ordering mutant makes one of these appear.
        hits_block = out.split("HITS", 1)[1].split("VERDICT", 1)[0]
        self.assertNotIn("matched issue-number", hits_block)
        self.assertNotIn("closingIssuesReferences", hits_block)

    def test_pr_that_is_the_issue_is_decided_before_any_match_test(self):
        # A PR whose NUMBER is the issue is ambiguous input: the number names BOTH
        # an open PR and the issue, and the tool cannot tell which object the
        # caller meant. It must therefore NOT answer CLEAN — exit 0 is this
        # protocol's instruction to DISPATCH, so a silent CLEAN authorises
        # starting work the PR may already belong to (the #7009 fail-open).
        #
        # It is still decided BEFORE any match test, which is the ordering this
        # test exists for (#4567): an undeclared head branch must not turn it into
        # a COLLISION. Exit 2 is neither CLEAN nor COLLISION — it is "could not be
        # completed, do not start on a guess" — so both properties hold at once.
        self.gh_fixtures(open_prs=[{
            "number": ISSUE, "title": f"feat: do the thing (#{ISSUE})",
            "body": f"Closes #{ISSUE}",
            "headRefName": f"feat/{ISSUE}-self",
            "state": "open",
            "closingIssuesReferences": [{"number": ISSUE}],
        }])
        rc, out = self.run_tool()
        self.assertEqual(rc, 2, out)
        self.assertIn("is an OPEN PULL REQUEST, not a work item", out)
        # The ordering property this test exists for (#4567) is pinned by
        # `rc == 2` alone. The old `HITS`-block assertions were removed after
        # review found them VACUOUS: the refusal never calls `format_report`, so
        # there is no report to inspect and those `assertNotIn`s could not fail
        # once `assertEqual(rc, 2)` passed. The earlier comment here claimed the
        # refusal "short-circuits before any surface is scanned" — also false:
        # the issue surface is queried first; only the PR loop refuses.
        #
        # The ONE non-vacuous assertion at this seam is the ABSENCE of a
        # `VERDICT` line: the documented promise is that this cause emits no
        # report at all (stderr only), which is precisely what makes a caller
        # that keys its remedy on `VERDICT: INCOMPLETE` miss it. "not CLEAN"
        # would pass a regression ending in `VERDICT: INCOMPLETE` (still exit
        # 2) — the exact mis-remedy the distinction exists to prevent.
        self.assertNotIn("VERDICT", out)
        # ⛔ AND IT STAYS A REFUSAL AFTER #9957, because every reference this PR
        # carries is ITS OWN number. Resolution must not hand the refused number
        # back: it would either loop (the re-run refuses again) or, worse, gate a
        # "work item" that is the PR itself. It also must not be re-resolved
        # recursively — `_verdict_for_pull_request` refuses a resolved number that
        # is itself an open PR.
        self.assertIn("could not resolve #3061 to a work item", out)
        self.assertIn("is #3061 — the PR's OWN number", out)
        self.assertIn("NEVER clean", out)

    def test_found_clone_is_not_the_callers_checkout(self):
        # P1-2, and it is the FAIL-OPEN direction. With `--repo owner/name` and a
        # cwd inside a DIFFERENT repo, `_resolve_target` SEARCHES for a clone of
        # the requested slug instead of using the cwd. The checkout it finds is
        # typically the canonical hub clone sitting on `main`, and its current
        # branch is NOT the caller's. Auto-declaring that branch suppresses a PR
        # whose head branch happens to be `main` — the ordinary shape for a fork
        # PR — and reports CLEAN on real in-flight work.
        #
        # `other-repo` is a SIBLING of `self.repo` under the same temp root, so
        # the search finds `self.repo` while the cwd's own slug is different.
        # That is exactly the production mechanism, reproduced.
        # ⛔ THE FOUND CLONE'S BRANCH MUST NOT BE THE DEFAULT, or this test
        # cannot fail for the reason it names. A verifier proved it: the fixture
        # used to leave `self.repo` on `main`, and `main` is refused
        # INDEPENDENTLY by `_default_branch_names`/`_is_default_branch` — so
        # breaking `_standing_in` (auto-declare any found clone) left the test
        # PASSING, its assertion satisfied by the default-branch refusal rather
        # than by the gate under test. Non-default, and deliberately WITHOUT the
        # issue number, so the hit comes from `closingIssuesReferences` and only
        # the self-identity gate can suppress it.
        _git(self.repo, "checkout", "-q", "-b", "release/hub-work")
        # ⛔ USE THE SHARED FIXTURE HELPER — do not hand-roll this repo. This
        # block was an inline copy of `_sibling_repo` that had dropped its two
        # identity lines (`git config user.email` / `git config user.name`),
        # which the seed commit below needs. Going through the helper keeps that
        # in one place, so a hand-rolled copy cannot drop it again.
        other = self._sibling_repo("other-repo", "other-owner/other-repo")

        self.gh_fixtures(open_prs=[{
            "number": 5199, "title": "fix: land the thing",
            "body": "", "headRefName": "release/hub-work", "state": "open",
            "closingIssuesReferences": [{"number": ISSUE}],
        }])
        rc, out = self.run_tool(repo_arg="test-owner/test-repo", cwd=other)
        # A PR on that branch that closes the issue is REAL work, and the found
        # clone was never the caller's checkout, so it must not be suppressed.
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertNotIn("your own PR", out)

    def test_standing_in_the_checkout_still_auto_declares(self):
        # The other half: the checkout the process is STANDING IN keeps the
        # best-effort auto-detection. Without this, the fix could be "disable
        # auto-detection entirely" and the suite would not notice.
        _git(self.repo, "checkout", "-q", "-b", f"fix/{ISSUE}-mine")
        rc, out = self.run_tool(cwd=self.repo)
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertIn("your own branch", out)

    def test_another_lanes_worktree_named_with_repo_is_not_mine(self):
        # P2-1, the fail-open it fixes: `--repo PATH` naming ANOTHER lane's
        # worktree used to declare THAT lane's branch and worktree as the
        # caller's own — reporting CLEAN on its live work. Naming a path says
        # where to LOOK, not whose work lives there. The caller must say so
        # explicitly with --self-branch/--self-worktree.
        other = self.tmp / "other-lane"
        other.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "worktree", "add", "-q", "-b", f"fix/{ISSUE}-theirs",
                        str(other), "HEAD"], cwd=self.repo, check=True,
                       capture_output=True, text=True)
        (other / "wip.txt").write_text("wip\n")
        _git(other, "add", "wip.txt")
        _git(other, "commit", "-qm", "their live work")

        self.gh_fixtures(closed_prs=[])
        # (a) The caller is elsewhere (the pytest cwd), so this is NOT its
        #     checkout and the branch must block.
        rc, out = self.run_tool(repo_arg=str(other))
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertNotIn("your own", out)

        # (b) Standing IN it does make it the caller's, so the convenience
        #     returns — proving the rule is about standing in, not a blanket
        #     refusal.
        rc, out = self.run_tool(repo_arg=str(other), cwd=other)
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertIn("your own branch", out)

    def test_default_branch_is_never_auto_declared(self):
        # ⛔ THE P0, AND IT IS THE *DEFAULT* INVOCATION. `setUp` leaves the
        # fixture checkout on `main`, and `run_tool()` passes `--repo <path>` —
        # the process stands in it. So auto-detection sees `main` and declares
        # it, and because the self arm runs FIRST for EVERY PR, an open PR whose
        # head branch is `main` (the ordinary shape for a fork PR) is demoted to
        # weak before the closing-reference test is ever reached: COLLISION
        # becomes CLEAN on real in-flight work.
        #
        # An earlier guard fixed only the SEARCHED-clone case
        # and left this one open, which is why the rule is now "never the DEFAULT
        # branch" rather than "only a caller's checkout".
        self.assertEqual(self._git_out("rev-parse", "--abbrev-ref", "HEAD"), "main")
        self.gh_fixtures(open_prs=[{
            "number": 5199, "title": "fix: land the thing", "body": "",
            "headRefName": "main", "state": "open",
            "closingIssuesReferences": [{"number": ISSUE}],
        }])
        # `cwd=self.repo` is load-bearing: auto-detection only runs when the
        # process stands in the checkout, so without it this test would pass
        # because NO detection happened, not because the default was refused.
        rc, out = self.run_tool(cwd=self.repo)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertNotIn("your own", out)

    def test_case_variant_default_branch_is_refused(self):
        # ⛔ P1-A, the case half, driven at the UNIT level because the scenario
        # is not constructible here: this box's filesystem is case-INSENSITIVE,
        # so a repo holding both `main` and `Main` cannot exist, and
        # `git branch -m Main` dies with 128 on the collision. The rule is a
        # pure function of (name, defaults), so test it as one — and pin BOTH
        # the widening AND the residual gap, so neither can drift unnoticed.
        mod = _tool_module()
        seeds = {"main", "master"}
        for variant in ("Main", "MAIN", "mAiN", "Master", "MASTER"):
            self.assertTrue(
                mod._is_default_branch(variant, seeds),
                f"{variant!r} must be refused as a default-branch spelling",
            )
        # The resolved `origin/HEAD` name is added to the same set, so a
        # slash-bearing default is covered by the same comparison.
        self.assertTrue(mod._is_default_branch("release/2024",
                                               seeds | {"release/2024"}))
        # THE HONEST HALF: a default branch outside the seed set is still
        # auto-declared when `origin/HEAD` is unresolvable. This is a KNOWN,
        # bounded gap (a normal clone resolves the symref, which covers every
        # real default name); asserting it means the limitation is recorded
        # rather than assumed away.
        self.assertFalse(mod._is_default_branch("trunk", seeds),
                         "if this ever becomes True, the seeds grew — update "
                         "the docstring that documents the gap")

    def test_default_branch_names_come_from_origin_head(self):
        # The mechanism, not just the comparison: `origin/HEAD` must be READ
        # (full ref, not `--short`) and the remote component dropped. Without
        # this, the parser could regress to `rsplit("/", 1)[-1]` again and only
        # the slash test would notice.
        _git(self.repo, "update-ref", "refs/remotes/origin/release/2024", "HEAD")
        _git(self.repo, "symbolic-ref", "refs/remotes/origin/HEAD",
             "refs/remotes/origin/release/2024")
        self.assertEqual(
            self._defaults_seen_by_the_tool(),
            {"main", "master", "release/2024"},
        )

    def test_default_branch_with_a_slash_is_never_auto_declared(self):
        # ⛔ P1-A. The default branch is read from
        # `git symbolic-ref --short refs/remotes/origin/HEAD`, which prints
        # `origin/<branch>`. Parsing that with `rsplit("/", 1)[-1]` registered a
        # default branch named `release/2024` as `2024` — so the REAL name was
        # missing from the refusal set and `release/2024` was auto-declared,
        # re-opening the P0 through a parse. Exactly one component (the remote)
        # may be dropped.
        _git(self.repo, "checkout", "-q", "-b", "release/2024")
        _git(self.repo, "update-ref", "refs/remotes/origin/release/2024", "HEAD")
        _git(self.repo, "symbolic-ref", "refs/remotes/origin/HEAD",
             "refs/remotes/origin/release/2024")
        # The FULL ref is what the tool reads; `_short_branch` (already tested)
        # drops the one remote component. `--short` would print
        # `origin/release/2024` and is deliberately NOT used, because
        # `_short_branch` refuses to rewrite a bare `origin/x`.
        self.assertEqual(
            self._git_out("symbolic-ref", "refs/remotes/origin/HEAD"),
            "refs/remotes/origin/release/2024",
        )
        self.gh_fixtures(open_prs=[{
            "number": 5199, "title": "fix: land the thing", "body": "",
            "headRefName": "release/2024", "state": "open",
            "closingIssuesReferences": [{"number": ISSUE}],
        }])
        # `cwd=self.repo`: the process must be standing in the checkout for
        # auto-detection to be attempted at all — otherwise this would pass for
        # the wrong reason (no detection, rather than a correct refusal).
        rc, out = self.run_tool(cwd=self.repo)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertNotIn("your own", out)

    def test_default_branch_is_still_honoured_when_declared_EXPLICITLY(self):
        # The refusal applies to what the tool INFERS, never to what the caller
        # ASSERTS. `--self-branch main` is a statement by the caller about its own
        # work, and the tool has no business overriding it.
        self.gh_fixtures(open_prs=[{
            "number": 5199, "title": "fix: land the thing", "body": "",
            "headRefName": "main", "state": "open",
            "closingIssuesReferences": [{"number": ISSUE}],
        }])
        rc, out = self.run_tool(self_branches=("main",), cwd=self.repo)
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertIn("not a competing claim", out)

    def test_a_non_default_branch_is_still_auto_declared(self):
        # The sensitivity half: the fix must not be "stop auto-declaring". A
        # lane branch (the fleet's normal shape) still gets the convenience.
        _git(self.repo, "checkout", "-q", "-b", f"fix/{ISSUE}-lane")
        rc, out = self.run_tool(cwd=self.repo)
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertIn("your own branch", out)

    def test_remote_branch_note_does_not_report_a_terminal_test(self):
        # P2-3. The `--merged` walk is meaningless for `refs/remotes`, so
        # reporting its count claimed that N remote refs had been found merged
        # and downgraded when the surface refused to judge a single one.
        # ⛔ `origin/main` MUST exist for the negative assertion to mean
        # anything. Without it the `--merged` walk fails, and the PRE-change tool
        # emitted the "detection UNAVAILABLE" note — which ALSO lacks the count
        # phrase, so `assertNotIn` passed for the wrong reason and the defect it
        # names was uncatchable.
        #
        # Scope, stated exactly: the `assertIn` is the load-bearing half — the
        # note text is what a regression to the pre-fix shape changes first. The
        # `assertNotIn` now guards the COUNT path, which a single mutation cannot
        # reach (the walk is skipped for `refs/remotes` AND the note branch is
        # keyed on the namespace); it fires if both halves are reverted together,
        # which is what reverting the P2-3 commit would do.
        _git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        _git(self.repo, "update-ref", "refs/remotes/origin/fix/9999-x", "HEAD")
        self.gh_fixtures(open_prs=[])
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        remote_row = next(ln for ln in out.splitlines() if ln.startswith("remote branches"))
        # #7693: the terminal tests still do not run on a remote ref's NAME — and
        # now NOTHING clears a remote ref, because the demotion was REMOVED after
        # eight review rounds found seven live false-CLEANs in it. This note is
        # emitted on every run and sits next to a blocking HIT, so it must not
        # claim a demotion; asserting on it is also what stops the stale sentence
        # from being restored (the previous assertion here matched `#7693` and so
        # passed against BOTH the demoting and the non-demoting tool — vacuous).
        self.assertIn("terminal tests are not applied to these refs by name", remote_row)
        self.assertIn("no remote ref is cleared as the terminal TWIN", remote_row)
        self.assertNotIn("IS demoted", remote_row)
        self.assertNotIn("already merged into main", remote_row)

    def test_remote_tracking_ref_is_not_judged_terminal(self):
        # C2-5. A remote-tracking ref is a local CACHE of the last fetch, not the
        # remote's state: a branch that was squash-merged and then REUSED for new
        # work still reads as its old, merged sha until someone fetches. Demoting
        # on that would be a false ACCEPT — a LIVE branch read as free — so the
        # terminal predicates apply to LOCAL branches, and the remote-tracking
        # copy keeps blocking.
        #
        # Note there is deliberately NO local branch of this name, so the only
        # candidate is the remote ref.
        ref = f"fix/{ISSUE}-reused"
        _git(self.repo, "update-ref", f"refs/remotes/origin/{ref}", "HEAD")
        sha = self._git_out("rev-parse", f"refs/remotes/origin/{ref}")
        self.gh_fixtures(closed_prs=[{
            "number": 4242, "title": "land it", "body": "", "state": "closed",
            "headRefName": ref, "headSha": sha,
            "mergedAt": "2026-09-01T00:00:00Z",
        }])
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("[remote branches]", out)
        self.assertNotIn("squash-merged", out)

    def test_remote_twin_of_a_terminal_local_branch_is_not_demoted(self):
        # #7693. The remote twin of a branch this run PROVED terminal sits at the
        # same immutable commit. A now-REMOVED demotion used to clear it, and only
        # after confirming the sha against the remote itself; that confirmation is
        # GONE with the demotion (see the removal block in `run_preflight`), so the
        # ref BLOCKS even though `origin` here is a real bare repo holding that sha.
        # Kept as the reverse-pin: this is the case a re-added demotion would clear
        # first.
        ref = f"fix/{ISSUE}-landed"
        bare = self.tmp / "bare.git"
        _git(self.tmp, "init", "-q", "--bare", "-b", "main", str(bare))
        _git(self.repo, "remote", "set-url", "origin", str(bare))
        _git(self.repo, "branch", "-q", ref)
        _git(self.repo, "push", "-q", "origin", f"refs/heads/{ref}:refs/heads/{ref}")
        _git(self.repo, "update-ref", f"refs/remotes/origin/{ref}", "HEAD")
        sha = self._git_out("rev-parse", "HEAD")
        self.gh_fixtures(closed_prs=[{
            "number": 4243, "title": "land it", "body": "", "state": "closed",
            "headRefName": ref, "headSha": sha,
            "mergedAt": "2026-09-01T00:00:00Z",
        }])
        rc, out = self.run_tool()
        # ⛔ INVERTED: the #7693 remote-ref demotion was REMOVED after eight review
        # rounds found seven live false-CLEANs in it (see the block in
        # `run_preflight`). A terminal local twin does NOT demote its remote twin
        # any more, even when the remote genuinely holds that tip — an over-block,
        # taken over a gate that can silently let two lanes into one checkout. This
        # test is kept as the REVERSE assertion so the demotion cannot be re-added
        # without deliberately deleting it.
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("[remote branches]", out)

    def test_remote_twin_with_no_confirmable_live_tip_still_blocks(self):
        # #7693. A remote ref that cannot be READ keeps blocking. This was the
        # fail-closed half of a demotion that had a live-tip confirmation; the
        # demotion is GONE (see the removal block in `run_preflight`), so the
        # assertion now holds for a simpler reason — nothing clears a remote ref.
        # Kept because it is the cheapest test that a remote ref still blocks, and
        # it is what would fail if the demotion were re-added returning on an
        # unreadable remote.
        ref = f"fix/{ISSUE}-unverifiable"
        _git(self.repo, "remote", "set-url", "origin", str(self.tmp / "missing.git"))
        _git(self.repo, "update-ref", f"refs/heads/{ref}", "HEAD")
        _git(self.repo, "update-ref", f"refs/remotes/origin/{ref}", "HEAD")
        sha = self._git_out("rev-parse", "HEAD")
        self.gh_fixtures(closed_prs=[{
            "number": 4245, "title": "land it", "body": "", "state": "closed",
            "headRefName": ref, "headSha": sha,
            "mergedAt": "2026-09-01T00:00:00Z",
        }])
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("[remote branches]", out)

    def test_remote_twin_of_a_reused_branch_still_blocks(self):
        # #7693, the SHARPEST test in this file, and the reverse-pin for the
        # removed demotion. It was written to kill the live false-CLEAN a REUSE
        # produces — the local twin and the CACHED remote ref both sit at the
        # terminal sha S (nothing here fetches; that is the whole hazard) while the
        # REMOTE has moved to T, so only a live read could tell them apart. The
        # live read is GONE, so this now asserts the simpler truth — a remote ref
        # whose branch moved on must BLOCK — and it is the test that fails if
        # anyone re-adds a demotion that reads the cache instead of the remote.
        ref = f"fix/{ISSUE}-reused"
        bare = self.tmp / "bare.git"
        _git(self.tmp, "init", "-q", "--bare", "-b", "main", str(bare))
        _git(self.repo, "remote", "set-url", "origin", str(bare))
        _git(self.repo, "branch", "-q", ref)
        terminal_sha = self._git_out("rev-parse", f"refs/heads/{ref}")
        _git(self.repo, "push", "-q", "origin", f"refs/heads/{ref}:refs/heads/{ref}")
        # The branch is REUSED elsewhere and the remote moves on. NOTE: a push
        # advances the local remote-tracking ref too, so the cache is reset to the
        # stale sha AFTER the second push — that is what "nobody has fetched since
        # S" actually looks like, and it is the whole hazard.
        _git(self.repo, "commit", "--allow-empty", "-m", "reuse")
        moved = self._git_out("rev-parse", "HEAD")
        _git(self.repo, "push", "-q", "origin", f"{moved}:refs/heads/{ref}")
        _git(self.repo, "update-ref", f"refs/remotes/origin/{ref}", terminal_sha)
        self.assertEqual(
            self._git_out("rev-parse", f"refs/remotes/origin/{ref}"), terminal_sha,
            "precondition: the local cache must still show the terminal sha",
        )
        self.gh_fixtures(closed_prs=[{
            "number": 4246, "title": "land it", "body": "", "state": "closed",
            "headRefName": ref, "headSha": terminal_sha,
            "mergedAt": "2026-09-01T00:00:00Z",
        }])
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("[remote branches]", out)

    def test_remote_ref_in_a_namespace_two_remotes_share_blocks(self):
        # #7693 P1. `refs/remotes/origin/X` need not cache ORIGIN's branch: with
        # `remote.fork.fetch=+refs/heads/*:refs/remotes/origin/*` it caches FORK's.
        # Deriving the remote from the ref's first path segment queried `origin`,
        # which held that branch at the stale sha, and demoted a LIVE branch —
        # the wrong-remote false-CLEAN. A namespace owned by two remotes is
        # AMBIGUOUS, and ambiguity on a blocking surface is not resolved by
        # guessing: the ref must keep blocking.
        ref = f"fix/{ISSUE}-shared-ns"
        bare_origin = self.tmp / "bare-origin.git"
        bare_fork = self.tmp / "bare-fork.git"
        _git(self.tmp, "init", "-q", "--bare", "-b", "main", str(bare_origin))
        _git(self.tmp, "init", "-q", "--bare", "-b", "main", str(bare_fork))
        _git(self.repo, "remote", "set-url", "origin", str(bare_origin))
        _git(self.repo, "remote", "add", "fork", str(bare_fork))
        _git(self.repo, "config", "remote.fork.fetch",
             "+refs/heads/*:refs/remotes/origin/*")
        _git(self.repo, "branch", "-q", ref)
        terminal_sha = self._git_out("rev-parse", f"refs/heads/{ref}")
        _git(self.repo, "push", "-q", "origin", f"refs/heads/{ref}:refs/heads/{ref}")
        # fork holds the same branch LIVE, moved. The cache is reset afterwards for
        # the same reason as in the reuse test above (a push moves it).
        _git(self.repo, "commit", "--allow-empty", "-m", "reuse")
        moved = self._git_out("rev-parse", "HEAD")
        _git(self.repo, "push", "-q", "fork", f"{moved}:refs/heads/{ref}")
        _git(self.repo, "update-ref", f"refs/remotes/origin/{ref}", terminal_sha)
        self.gh_fixtures(closed_prs=[{
            "number": 4247, "title": "land it", "body": "", "state": "closed",
            "headRefName": ref, "headSha": terminal_sha,
            "mergedAt": "2026-09-01T00:00:00Z",
        }])
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("[remote branches]", out)

    def test_narrow_glob_refspec_does_not_demote_a_live_branch(self):
        # #7693 round 3, the defect that killed the "longest prefix" design.
        # `git remote set-branches origin 'fix/*'` writes
        # `+refs/heads/fix/*:refs/remotes/origin/fix/*`, so
        # `refs/remotes/origin/fix/X` caches the branch `fix/X` — but the
        # remainder after the prefix reads just `X`. Reconstructing the branch
        # from the ref name therefore queried `refs/heads/X`, which sat at the
        # stale sha, and DEMOTED a live branch (measured through the real CLI:
        # `VERDICT: CLEAN (exit 0)` while `refs/heads/fix/X` was at T).
        #
        # The forward mapping has no such freedom: `fix/X` is substituted into
        # the destination and lands on `refs/remotes/origin/fix/X` exactly.
        ref = f"refs/remotes/origin/fix/{ISSUE}-narrow"
        # A sibling that genuinely sits at the old sha — the thing the broken
        # reconstruction latched onto.
        _git(self.repo, "branch", "-q", f"{ISSUE}-narrow", "HEAD")
        terminal_sha = self._git_out("rev-parse", "HEAD")
        _git(self.repo, "branch", "-q", f"fix/{ISSUE}-narrow", "HEAD")
        bare = self.tmp / "narrow.git"
        _git(self.tmp, "init", "-q", "--bare", "-b", "main", str(bare))
        _git(self.repo, "remote", "set-url", "origin", str(bare))
        _git(self.repo, "config", "remote.origin.fetch",
             "+refs/heads/fix/*:refs/remotes/origin/fix/*")
        _git(self.repo, "push", "-q", "origin",
             f"refs/heads/fix/{ISSUE}-narrow:refs/heads/fix/{ISSUE}-narrow")
        # The branch this ref caches is REUSED and moves on; the sibling stays put.
        _git(self.repo, "commit", "--allow-empty", "-m", "reuse")
        moved = self._git_out("rev-parse", "HEAD")
        _git(self.repo, "push", "-q", "origin",
             f"{moved}:refs/heads/fix/{ISSUE}-narrow")
        _git(self.repo, "update-ref", ref, terminal_sha)
        self.gh_fixtures(closed_prs=[{
            "number": 4248, "title": "land it", "body": "", "state": "closed",
            "headRefName": f"fix/{ISSUE}-narrow", "headSha": terminal_sha,
            "mergedAt": "2026-09-01T00:00:00Z",
        }])
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)

    def test_narrowed_refspec_leftover_ref_is_not_demoted(self):
        # #7693 round 4 P1. A remote whose CURRENT refspec cannot write the ref is
        # SKIPPED by the prefilter — but the ref can be a LEFTOVER from before the
        # refspec was NARROWED (`git remote set-branches origin main`, the code's
        # own non-glob trap) and still sits in a LIVE namespace, so the remote can
        # hold it at a different sha. The first cut returned `False` without
        # reading anything and printed "CONFIRMED as this remote branch's CURRENT
        # tip" for a branch it never looked at (reproduced through the real CLI).
        ref = f"fix/{ISSUE}-narrowed"
        bare = self.tmp / "narrowed.git"
        _git(self.tmp, "init", "-q", "--bare", "-b", "main", str(bare))
        _git(self.repo, "remote", "set-url", "origin", str(bare))
        _git(self.repo, "branch", "-q", ref)
        terminal_sha = self._git_out("rev-parse", f"refs/heads/{ref}")
        # The remote holds it LIVE at a different sha ...
        _git(self.repo, "commit", "--allow-empty", "-m", "reuse")
        moved = self._git_out("rev-parse", "HEAD")
        _git(self.repo, "push", "-q", "origin", f"{moved}:refs/heads/{ref}")
        # ... and the refspec is then narrowed so it no longer covers this branch,
        # while the stale cache ref survives.
        _git(self.repo, "config", "remote.origin.fetch",
             "+refs/heads/main:refs/remotes/origin/main")
        _git(self.repo, "update-ref", f"refs/remotes/origin/{ref}", terminal_sha)
        self.gh_fixtures(closed_prs=[{
            "number": 4251, "title": "land it", "body": "", "state": "closed",
            "headRefName": ref, "headSha": terminal_sha,
            "mergedAt": "2026-09-01T00:00:00Z",
        }])
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)

    def test_refspec_sourced_outside_refs_heads_does_not_demote(self):
        # #7693 round 4 P1. `ls-remote --heads` LISTS ONLY `refs/heads/*`, but a
        # refspec's SOURCE need not live there: with
        # `+refs/pull/*/head:refs/remotes/origin/pr/*` the live holder is
        # `refs/pull/N/head`, which `--heads` never lists — so the forward map was
        # applied to a FILTERED list, concluded "no holder", and demoted a live
        # ref (reproduced through the real CLI).
        ref = f"refs/remotes/origin/pr/{ISSUE}"
        bare = self.tmp / "pullref.git"
        _git(self.tmp, "init", "-q", "--bare", "-b", "main", str(bare))
        _git(self.repo, "remote", "set-url", "origin", str(bare))
        _git(self.repo, "config", "remote.origin.fetch",
             "+refs/pull/*/head:refs/remotes/origin/pr/*")
        # A terminal local twin, kept only to preserve the demotion-era fixture:
        # nothing matches a witness against the remote any more.
        _git(self.repo, "branch", "-q", f"fix/{ISSUE}-prsource")
        terminal_sha = self._git_out("rev-parse", f"refs/heads/fix/{ISSUE}-prsource")
        _git(self.repo, "push", "-q", "origin",
             f"{terminal_sha}:refs/pull/{ISSUE}/head")
        _git(self.repo, "commit", "--allow-empty", "-m", "reuse")
        moved = self._git_out("rev-parse", "HEAD")
        _git(self.repo, "push", "-q", "origin", f"{moved}:refs/pull/{ISSUE}/head")
        _git(self.repo, "update-ref", ref, terminal_sha)
        self.gh_fixtures(closed_prs=[{
            "number": 4252, "title": "land it", "body": "", "state": "closed",
            "headRefName": f"fix/{ISSUE}-prsource", "headSha": terminal_sha,
            "mergedAt": "2026-09-01T00:00:00Z",
        }])
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)

    def test_remote_branch_deleted_after_merge_still_blocks(self):
        # #7693 round 6 — DELIBERATELY REVERTED, and inverted from the round-3
        # version that expected a demotion. A branch deleted on the remote offers
        # NO mapping, and "no advertised head maps onto this ref" cannot tell
        # DELETED from LEFTOVER-UNDER-A-DIFFERENT-SOURCE. Round 6 reproduced a
        # live false-CLEAN from exactly that conflation, so absence no longer
        # demotes: a same-sha mapping is required. The deleted case therefore
        # BLOCKS — the fail-closed annoyance the round-5 record named as the
        # acceptable trade. Pinned so nobody re-adds the demotion without reading
        # why it was removed.
        ref = f"fix/{ISSUE}-deleted"
        bare = self.tmp / "del.git"
        _git(self.tmp, "init", "-q", "--bare", "-b", "main", str(bare))
        _git(self.repo, "remote", "set-url", "origin", str(bare))
        _git(self.repo, "branch", "-q", ref)
        terminal_sha = self._git_out("rev-parse", f"refs/heads/{ref}")
        _git(self.repo, "push", "-q", "origin", f"refs/heads/{ref}:refs/heads/{ref}")
        _git(self.repo, "push", "-q", "origin", f":refs/heads/{ref}")
        _git(self.repo, "update-ref", f"refs/remotes/origin/{ref}", terminal_sha)
        self.gh_fixtures(closed_prs=[{
            "number": 4249, "title": "land it", "body": "", "state": "closed",
            "headRefName": ref, "headSha": terminal_sha,
            "mergedAt": "2026-09-01T00:00:00Z",
        }])
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)

    def test_unreadable_closing_reference_element_is_incomplete_not_dropped(self):
        # C2-2. The absent-field contract is applied PER ELEMENT too. A field
        # that IS present and claims to close N must not lose N because the
        # element is shaped differently than expected — that is the same
        # fail-open drop as reading an absent field as empty. The body-regex
        # union hides it for a body reference, but GitHub also derives closing
        # references from the title and commit messages, where the body need not
        # contain a closing keyword at all.
        for bad in (["3061"], [{"number": "3061"}], [{"number": None}], ["x"],
                    [{"number": True}]):
            with self.subTest(element=bad[0]):
                self.gh_fixtures(open_prs=[{
                    "number": 5150, "title": "unrelated", "body": "",
                    "headRefName": "feat/5150-other", "state": "open",
                    "closingIssuesReferences": bad,
                }])
                rc, out = self.run_tool()
                self.assertEqual(rc, 2, f"element={bad[0]!r}\n{out}")
                self.assertIn("VERDICT: INCOMPLETE", out)
                self.assertIn("closing-reference-source-unavailable", out)

    def test_clean_note_does_not_call_every_weak_hit_prose(self):
        # C2-3. The WEAK SIGNALS block was corrected to stop describing every
        # weak hit as prose, but the CLEAN-path NOTE eight lines below it still
        # said "weak prose signal(s)". A report contradicting its own contents is
        # the unverifiable-verdict class this change removes (#3504 class 2).
        self.gh_fixtures(issue=self.issue_payload(assignees=("test-agent",)))
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("weak, non-blocking signal(s)", out)
        self.assertNotIn("weak prose signal(s)", out)

    # ── a PR whose head branch the caller DECLARED is its own work ──────────

    def test_declared_own_pr_branch_suppresses_the_pr(self):
        # The PR-side half of self-identity: a hit on a PR whose head branch the
        # caller owns is the caller's own PR. A DIFFERENT PR in the same list
        # must still block, so this is not a blanket exemption.
        self.gh_fixtures(open_prs=[
            {"number": 5150, "title": "unrelated",
             "body": "", "headRefName": f"feat/{ISSUE}-mine", "state": "open"},
            {"number": 5151, "title": "fix: sibling cleanup",
             "body": "no number here at all",
             "headRefName": "feat/9999-other", "state": "open",
             "closingIssuesReferences": [{"number": ISSUE}]},
        ])
        rc, out = self.run_tool(self_branches=(f"feat/{ISSUE}-mine",))
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("your own PR", out)
        self.assertIn("closingIssuesReferences includes", out)

    # ── terminal branches: squash-merge residue cannot block (#5186) ────────

    def _merged_pr_fixture(self, ref: str) -> dict:
        """A closed PR payload in the shape the tool ACTUALLY receives.

        ⛔ `headSha`, not a nested `head.sha`. `_closed_pr_sample` runs the REST
        response through a `gh api --jq` projection, and that filter is what
        re-keys the payload — so the only shape the scanner can ever see is the
        projected one. This fixture previously supplied `head: {ref, sha}`, a
        nested object the projection never produces: the harvest read
        `_pr.get("head")`, always got None, and `merged_heads` was ALWAYS
        EMPTY IN PRODUCTION. #5186's primary predicate was therefore dead code,
        and these tests passed anyway because the `gh` stub cats its fixture and
        never executes `--jq`. A fixture must model the wire, not the wish.
        """
        return {
            "number": 4242, "title": f"land {ref}",
            "body": "", "state": "closed",
            "headRefName": ref,
            "headSha": self._git_out("rev-parse", ref),
            "mergedAt": "2026-09-01T00:00:00Z",
        }

    def test_squash_merged_branch_is_terminal_and_cannot_block(self):
        # #5186, the primary predicate. A squash merge means the branch tip is
        # NEVER an ancestor of main (GitHub's own docs: the original SHAs are
        # lost), so ancestry cannot detect it — the test is tip-SHA == a merged
        # PR's `headRefOid`. D1 forbids patch-id (its whitespace modes produce
        # a false ACCEPT, which is this issue's dangerous direction).
        #
        # ⛔ IT PINS THE "UNRESOLVED TIP STILL DEMOTES" HALF, deliberately:
        # `refs/remotes/origin/main` is never set here, so `main_tip` is `None`.
        # Predicate 1 is the EXACT arm (D4 / correction #2 — a merged-PR record
        # names the branch head as GitHub computed it), so an unresolvable tip
        # does not make the record less exact. Its partner,
        # `test_fresh_branch_at_main_tip_matching_a_merged_pr_head_still_blocks`,
        # covers the case where the tip IS known and equals the branch's.
        ref = f"docs/research-{ISSUE}-4333"
        _git(self.repo, "branch", ref)
        self.gh_fixtures(closed_prs=[self._merged_pr_fixture(ref)])
        rc, out = self.run_tool(issue=ISSUE)
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertIn("squash-merged", out)

    def test_weak_advisory_hit_still_carries_the_advisory_marker(self):
        # ⛔ The advisory marker was gated on `hit.strength != "weak"`, which
        # excluded the case this surface USUALLY produces: a match on a CLOSED PR
        # is `weak` whenever that PR is terminal (merged), i.e. nearly always. So
        # the advisory row printed the bare `(weak)` tag — which is equally what a
        # weak hit on a BLOCKING surface prints — and the comment's stated reason
        # ("an untagged advisory hit is indistinguishable from the one that caused
        # a refusal") was false for exactly the hits the surface produces. The
        # exemption protected against nothing.
        ref = f"docs/research-{ISSUE}-advisory"
        _git(self.repo, "branch", ref)
        self.gh_fixtures(closed_prs=[self._merged_pr_fixture(ref)])
        rc, out = self.run_tool(issue=ISSUE)
        self.assertEqual(rc, 0, out)
        # `PR #` discriminates the HITS line from the ADVISORY SURFACES summary
        # line, which also begins with the same surface label.
        rows = [ln for ln in out.splitlines()
                if ln.strip().startswith("[recently-closed PRs]") and "PR #" in ln]
        self.assertTrue(rows, out)
        for row in rows:
            self.assertIn("(weak)", row)
            self.assertIn("(advisory — cannot block)", row)

    def test_fresh_branch_at_main_tip_matching_a_merged_pr_head_still_blocks(self):
        # ⛔ THE P0 GUARD. The main-tip exclusion belongs on BOTH predicates.
        # A branch created at `origin/main`'s tip with NO COMMITS OF ITS OWN has
        # main's SHA — and when main's tip is itself a merged PR's `head.sha`
        # (a fast-forward / rebase landing, or an empty-diff PR), the PRIMARY
        # predicate matched it, called it "squash-merged", and returned CLEAN on
        # a lane that had only just CLAIMED the issue by creating the branch.
        # The two SHAs are indistinguishable, so the safe reading is to block.
        ref = f"fix/{ISSUE}-fresh-ff"
        _git(self.repo, "branch", ref)
        # The tip IS known here, and equals the branch's — the whole point.
        _git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.gh_fixtures(closed_prs=[self._merged_pr_fixture(ref)])
        rc, out = self.run_tool(issue=ISSUE)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertNotIn("squash-merged", out)

    def test_same_branch_without_a_merge_record_still_blocks(self):
        # SENSITIVITY GUARD for the test above, and the mutation it is built to
        # catch: if the terminal predicate were `return True` (or ignored
        # `merged_at`), the previous test would still pass. Here the ONLY
        # difference is `merged_at: None` — an open PR carrying the same head
        # sha — so a live branch must still block.
        ref = f"docs/research-{ISSUE}-4333"
        _git(self.repo, "branch", ref)
        pr = self._merged_pr_fixture(ref)
        # The PROJECTED key is `mergedAt` (camelCase) — that is what the jq
        # filter emits and therefore all the harvest can ever read. Clearing the
        # snake_case name instead left `mergedAt` set, so the fixture was still
        # merged and the test asserted COLLISION on a CLEAN run.
        pr["mergedAt"] = None
        self.gh_fixtures(closed_prs=[pr])
        rc, out = self.run_tool(issue=ISSUE)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("matched issue-number", out)

    # ── MERGED LEFTOVERS: the ref IS the merged artifact (#10174) ───────────
    #
    # #5186/#5485 already demote a squash-merged ref. What was MISSING is the
    # report a reader needs to act on it: WHICH pull request landed it, and
    # whether the remote still holds the branch. Without those two data the row
    # reads as "a branch with a matching number that cannot block" — auditable
    # by nobody, which is the property that gets a gate overridden.

    def _diverged_squash_merged_ref(self, ref: str,
                                    pr_number: int = 4242) -> str:
        """A local ref whose tip is a MERGED PR's head and is NOT an ancestor.

        The shape #10174 measured on the owner's box: the PR was SQUASH-merged,
        so its commits never entered main, the branch is `diverged` (ahead of
        main by its own commits, behind by main's), and no ancestry test can see
        the landing. Returns the tip sha, which the merged-PR fixture must name.
        """
        _git(self.repo, "checkout", "-q", "-b", ref)
        (self.repo / "the-work.txt").write_text("landed by the PR\n")
        _git(self.repo, "add", "the-work.txt")
        _git(self.repo, "commit", "-qm", "the work the PR landed")
        tip = self._git_out("rev-parse", ref)
        # main moves on WITHOUT this commit — a squash merge's signature. The
        # branch is therefore DIVERGED, not "behind": no ancestor test can demote it.
        _git(self.repo, "checkout", "-q", "main")
        (self.repo / "main-moves.txt").write_text("main moved\n")
        _git(self.repo, "add", "main-moves.txt")
        _git(self.repo, "commit", "-qm", "main moves on")
        _git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.assertNotIn(
            tip, self._git_out("rev-list", "--first-parent", "origin/main").splitlines(),
        )
        self.gh_fixtures(closed_prs=[{
            "number": pr_number, "title": "the PR that landed it", "body": "",
            "state": "closed", "headRefName": ref, "headSha": tip,
            "mergedAt": "2026-10-27T04:44:04Z",
        }])
        return tip

    @staticmethod
    def _branch_rows(out: str, ref: str) -> list[str]:
        return [ln for ln in out.splitlines()
                if ln.strip().startswith("[local branches]") and ref in ln]

    def test_diverged_squash_merged_leftover_is_advisory_and_names_the_pr(self):
        # THE EXACT SHAPE #10174 REPRODUCED, asserted end to end: a `diverged`
        # local ref whose merged PR has the SAME head SHA must not block, must
        # still be REPORTED, and must NAME the pull request — the datum that
        # turns "some branch cannot block" into "this ref is the artifact of
        # PR #4242, which is already in main; clean it up".
        ref = f"feat/{ISSUE}-consent-token-as-half"
        self._diverged_squash_merged_ref(ref)
        rc, out = self.run_tool(issue=ISSUE)
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        rows = self._branch_rows(out, ref)
        self.assertTrue(rows, out)
        row = rows[0]
        # Non-blocking, and reported rather than dropped.
        self.assertIn("(weak)", row)
        self.assertNotIn("(number)", row)
        # …and it says what it is, by name, so a reader can act.
        self.assertIn("MERGED LEFTOVER", row)
        self.assertIn("PR #4242", row)
        self.assertIn("squash-merged", row)
        # The remote counterpart is gone (the stub's default 404), which is the
        # pair of facts that makes "no lane is live" the strongest reading.
        self.assertIn("remote counterpart", row)
        self.assertIn("GONE", row)
        # ⛔ THE COST BOUND (#10174 item 4): the remote read is paid ONCE for the
        # one demoted ref — never once per local branch. A per-branch read is the
        # shape this test exists to refuse: ~1,250 refs before every dispatch.
        calls = (self.gh_dir / "remote-branch-calls.log").read_text().splitlines()
        self.assertEqual(len(calls), 1, calls)
        self.assertIn(f"branches/feat/{ISSUE}-consent-token-as-half", calls[0])

    def _remote_branch_fixture(self, ref: str, sha: str) -> None:
        """The POST-`--jq` shape the branch API read actually returns.

        ⛔ BOTH KEYS, and `sha` is not decoration: the reader compares the remote tip
        with the demoted local tip, so a fixture carrying only `name` models a payload
        the projection cannot produce AND would leave the comparison permanently
        unverifiable — hiding the reused-name case below behind a passing test.
        """
        (self.gh_dir / "remote_branch.json").write_text(
            json.dumps({"name": ref, "sha": sha}))

    def test_merged_leftover_whose_remote_branch_still_exists_says_so(self):
        # The OTHER remote state, and the reason the note must read the remote
        # rather than assume: a leftover whose branch is still on `origin` AT THE
        # MERGED TIP needs a remote delete as well, and a note that printed "GONE"
        # unconditionally would send the reader looking for nothing.
        ref = f"feat/{ISSUE}-still-on-the-remote"
        tip = self._diverged_squash_merged_ref(ref)
        self._remote_branch_fixture(ref, tip)
        rc, out = self.run_tool(issue=ISSUE)
        self.assertEqual(rc, 0, out)
        row = self._branch_rows(out, ref)[0]
        self.assertIn("MERGED LEFTOVER", row)
        self.assertIn("remote counterpart", row)
        self.assertIn("STILL EXISTS", row)
        self.assertIn("SAME tip", row)
        self.assertNotIn("GONE", row)
        # The delete advice is EARNED here: the remote tip IS the merged tip.
        self.assertIn("delete both", row)

    def test_a_reused_branch_name_on_the_remote_is_never_advised_for_deletion(self):
        # ⛔ THE REVIEW FINDING ON #10205, and it is the dangerous direction. The
        # demotion proves the LOCAL tip is a merged PR's head. It says NOTHING about
        # what `origin/<branch>` holds: a lane can reuse the name and push NEW work
        # from another machine or a cell, and this clone never fetches. A note built
        # from the name alone ("STILL EXISTS → delete the remote one too") would tell
        # a reader to delete the only remaining holder of that lane's unlanded work.
        # The remote tip is on the same response, so the comparison is free — and the
        # advice must flip on it.
        ref = f"feat/{ISSUE}-name-reused-on-the-remote"
        tip = self._diverged_squash_merged_ref(ref)
        self._remote_branch_fixture(ref, "b" * 40)  # a DIFFERENT tip
        self.assertNotEqual("b" * 40, tip)
        rc, out = self.run_tool(issue=ISSUE)
        self.assertEqual(rc, 0, out)
        row = self._branch_rows(out, ref)[0]
        self.assertIn("MERGED LEFTOVER", row)
        self.assertIn("NAME WAS REUSED", row)
        self.assertIn("do NOT delete the remote ref", row)
        self.assertNotIn("delete both", row)
        self.assertNotIn("GONE", row)
        # The LOCAL demotion is unchanged — only the advice about the REMOTE flips.
        self.assertIn("(weak)", row)

    def test_a_remote_branch_with_no_comparable_tip_is_unverified_not_a_delete(self):
        # The third state of the same comparison: the remote branch exists but the
        # payload carried no tip. UNVERIFIED is its own answer — it may not inherit
        # the same-tip arm's "delete both" (which would be advice from an absent
        # measurement) nor the gone arm's corroboration.
        ref = f"feat/{ISSUE}-remote-tip-unknown"
        self._diverged_squash_merged_ref(ref)
        self._remote_branch_fixture(ref, "")
        rc, out = self.run_tool(issue=ISSUE)
        self.assertEqual(rc, 0, out)
        row = self._branch_rows(out, ref)[0]
        self.assertIn("MERGED LEFTOVER", row)
        self.assertIn("UNVERIFIED", row)
        self.assertIn("do NOT delete the remote ref", row)
        self.assertNotIn("delete both", row)

    def test_merged_leftover_with_an_unreadable_remote_read_is_still_advisory(self):
        # ⛔ THE DEMOTION DOES NOT DEPEND ON THIS READ. Tip equality is the
        # load-bearing fact (it is the MERGED PR's own record); the remote state
        # is corroboration. So an unreadable remote read must neither undo the
        # demotion nor be narrated as "gone" — two opposite failures from one
        # datum, and the note has to say which one happened.
        ref = f"feat/{ISSUE}-remote-unreadable"
        self._diverged_squash_merged_ref(ref)
        rc, out = self.run_tool(
            issue=ISSUE, env_extra={"GH_STUB_REMOTE_BRANCH_FAIL": "1"})
        self.assertEqual(rc, 0, out)
        row = self._branch_rows(out, ref)[0]
        self.assertIn("MERGED LEFTOVER", row)
        self.assertIn("PR #4242", row)
        self.assertIn("(weak)", row)
        self.assertIn("could not be read", row)
        self.assertNotIn("GONE", row)

    def test_branch_advanced_past_its_merged_pr_head_still_blocks(self):
        # ⛔ THE FALSE NEGATIVE THIS CHANGE INVITES, and the brief names it: a
        # branch merged at X and then ADVANCED to Y is live-adjacent work. The
        # naive implementation of the demotion — "a merged PR exists for this
        # branch name" — demotes it, because the PR lookup is scoped by NAME.
        # Tip equality is what refuses: the ref no longer holds what landed.
        ref = f"fix/{ISSUE}-reused-after-merge"
        tip = self._diverged_squash_merged_ref(ref)
        # The branch moves PAST the merged head. The PR record still names the
        # OLD tip (`tip`), and the ref now points at work that landed nowhere.
        _git(self.repo, "checkout", "-q", ref)
        (self.repo / "more-work.txt").write_text("work after the merge\n")
        _git(self.repo, "add", "more-work.txt")
        _git(self.repo, "commit", "-qm", "work the PR never saw")
        advanced = self._git_out("rev-parse", ref)
        self.assertNotEqual(advanced, tip)
        rc, out = self.run_tool(issue=ISSUE)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        row = self._branch_rows(out, ref)[0]
        # ⛔ THE STRENGTH TAG IS THE ASSERTION, not the verdict: a `weak` tag is
        # what a demoted ref prints, and `(number)` is what a BLOCKING number hit
        # prints (see `format_report`'s tag map) — so the negative assertion is the
        # one that carries meaning, and the positive one pins that the row is still
        # a blocking-strength hit rather than a dropped row.
        self.assertIn("(number)", row)
        self.assertNotIn("(weak)", row)
        self.assertNotIn("MERGED LEFTOVER", row)
        self.assertNotIn("squash-merged", out)
        # ⛔ AND THE OTHER HALF OF THE COST BOUND: a run with NO merged leftover
        # asks the remote about NOTHING. Without this, a read that ran for every
        # matching ref would still satisfy the test above.
        self.assertFalse((self.gh_dir / "remote-branch-calls.log").exists())

    def test_squash_merge_witness_unavailable_keeps_blocking_and_says_so(self):
        # ⛔ FAIL-CLOSED, item 5 of #10174. When the merged-PR history cannot be
        # read, the demotion DOES NOT APPLY: the branch keeps blocking. And the
        # inability must be NAMED, because "this ref is a live holder" and "we
        # could not ask GitHub" are different sentences with different remedies
        # — the module's oldest measured defect is presenting the second as the
        # first.
        ref = f"feat/{ISSUE}-witness-unavailable"
        self._diverged_squash_merged_ref(ref)
        # BOTH witnesses die here. (1) The closed-PR sample — predicate 1's
        # evidence — fails after emitting a body, which is the shape a
        # rate-limited or half-read response has. (2) The targeted fallback
        # cannot run either: the stub refuses that endpoint (`unexpected argv`),
        # which is the real transport's failure mode for an unmodelled request.
        rc, out = self.run_tool(
            issue=ISSUE, env_extra={"GH_STUB_API_FAIL_AFTER_OUTPUT": "1"})
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        row = self._branch_rows(out, ref)[0]
        self.assertIn("(number)", row)
        self.assertNotIn("(weak)", row)
        self.assertNotIn("MERGED LEFTOVER", row)
        # The INABILITY is named on the surface, not the branch presented as a
        # live holder with no explanation.
        note = next(ln for ln in out.splitlines()
                    if ln.strip().startswith("local branches"))
        self.assertIn("could not be read", note)
        self.assertIn("fail-closed", note)

    def test_branch_merged_into_origin_main_is_terminal(self):
        # The second, independent arm: a branch whose work LANDED and which main
        # has since advanced PAST is immutable history, not in-flight work.
        #
        # ⛔ THIS TEST USED TO PIN A FAIL-OPEN DEFECT. It created the branch AT
        # main's tip with NO COMMITS and asserted CLEAN. That is not a landing —
        # it is the ordinary state of a lane between `git worktree add` and its
        # first commit, which is EXACTLY when a second lane is most likely to
        # dispatch the same issue. Plain ancestry called it "merged into main"
        # and demoted it. The arm is now STRICT (tip != main's tip), and this
        # test builds a genuine landing: a commit on the branch, then main
        # advanced past it by a merge.
        ref = f"fix/{ISSUE}-ancestor-landed"
        _git(self.repo, "checkout", "-q", "-b", ref)
        (self.repo / "landed.txt").write_text("landed\n")
        _git(self.repo, "add", "landed.txt")
        _git(self.repo, "commit", "-qm", "the work")
        _git(self.repo, "checkout", "-q", "main")
        _git(self.repo, "merge", "--no-ff", "-q", "-m", "land it", ref)
        _git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.gh_fixtures(closed_prs=[])
        rc, out = self.run_tool(issue=ISSUE)
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertIn("merged into main", out)

    def test_worktree_on_a_fresh_branch_still_blocks(self):
        # ⛔ THE P1-B GUARD, worktree side. `add_worktree` is exactly the state
        # between `git worktree add` and the first commit: the branch tip IS
        # origin/main's tip, so plain ancestry calls it "merged into main".
        #
        # ⛔ THE ASSERTION IS ON THE `local worktrees` ROW, NOT THE VERDICT, and
        # that is the whole point. `add_worktree` creates a BRANCH as well as a
        # worktree, so the local-branches surface raises the same hit, and the
        # verdict stays COLLISION even with the worktree surface broken entirely
        # — a verifier PROVED it against the PREVIOUS revision of this test,
        # which asserted only `VERDICT: COLLISION` and still passed with
        # `scan_worktree_surface` disabled. An assertion the wrong code path can
        # satisfy is the own-masking failure this file has already produced once
        # (a worktree check masked by a branch check).
        #
        # What each assertion pins, stated exactly, because a later verifier
        # checked this paragraph and found the stronger reading unsupported:
        #   * the ROW assertion catches a DISABLED worktree surface, which the
        #     verdict alone cannot (mutation-verified);
        #   * the VERDICT assertion catches a broken terminal predicate, which
        #     demotes BOTH surfaces and so needs no row-level assertion;
        #   * NOTHING here proves the `main_tip` argument is threaded, and that is
        #     not claimed: feeding the scanner `None` disables predicate 2 and can
        #     only leave the row BLOCKING (fail-closed), and OMITTING the argument
        #     is a `TypeError` now that the parameter is required.
        _git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.add_worktree("fresh", f"fix/{ISSUE}-fresh-wt")
        self.gh_fixtures(closed_prs=[])
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        wt_row = next(ln for ln in out.splitlines()
                      if ln.startswith("local worktrees"))
        self.assertIn("HIT", wt_row, wt_row)
        self.assertNotIn("merged into origin/main", wt_row)

    def test_unresolvable_main_tip_never_downgrades(self):
        # ⛔ THE FAIL-CLOSED CORE, and the sensitivity partner of
        # `test_branch_merged_into_origin_main_is_terminal`: the SAME repo state
        # (a genuine landing, strictly behind main, so predicate 2 would fire)
        # but with the tip lookup broken.
        #
        # Predicate 2 can only ever DOWNGRADE a hit, so an unknown tip makes
        # "just created" and "landed behind main" indistinguishable, and the
        # safe answer to "I cannot tell" is to leave the ref BLOCKING. Getting
        # this backwards is a fail-OPEN: the tool reports CLEAN on a ref it did
        # not manage to clear. A false COLLISION costs a re-check; a false CLEAN
        # costs a duplicated dispatch.
        ref = f"fix/{ISSUE}-landed-no-tip"
        _git(self.repo, "checkout", "-q", "-b", ref)
        (self.repo / "landed.txt").write_text("landed\n")
        _git(self.repo, "add", "landed.txt")
        _git(self.repo, "commit", "-qm", "the work")
        _git(self.repo, "checkout", "-q", "main")
        _git(self.repo, "merge", "--no-ff", "-q", "-m", "land it", ref)
        _git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        # Precondition: the ancestor walk STILL SEES this branch, so predicate 2
        # is genuinely in play and only the tip is missing.
        merged = self._git_out("for-each-ref", "--format=%(refname)",
                               "--merged=origin/main", "refs/heads")
        self.assertIn(f"refs/heads/{ref}", merged)

        self.gh_fixtures(closed_prs=[])
        git_stub = _write_exec(self.tmp / "git-stub-no-tip", GIT_STUB_NO_MAIN_TIP)
        rc, out = self.run_tool(git_bin=git_stub,
                                env_extra={"REAL_GIT": shutil.which("git")})
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertNotIn("merged into origin/main", out)

    def test_fresh_branch_at_a_PREVIOUS_merged_head_still_blocks(self):
        # ⛔ THE CYCLE-6 P0 GUARD, and it is the SAME defect cycle 5 fixed on
        # predicate 2 — left behind on predicate 1. `sha != main_tip` is ONE COMMIT
        # DEEP: it refuses to call a fresh branch landed only while its tip is
        # exactly the CURRENT tip. As soon as main advances past a commit that is
        # also a merged PR's `head.sha` (a fast-forward / rebase landing, or an
        # empty-diff PR), a fresh branch sitting on that commit satisfies
        # `sha != main_tip`, matches the merged-PR record, and is read as
        # "squash-merged" — CLEAN on a lane that has already claimed the issue by
        # creating the branch.
        #
        # The two states are indistinguishable from the SHAs alone, so the
        # discriminator is the same one predicate 2 uses: a tip on main's own
        # FIRST-PARENT chain is a commit main already contains (exactly what a
        # fresh branch points at), NOT an absorbed branch head, which entered main
        # as a MERGE parent.
        ref = f"fix/{ISSUE}-prev-merged-head"
        _git(self.repo, "branch", ref)                      # branch at H
        head_sha = self._git_out("rev-parse", ref)
        (self.repo / "later.txt").write_text("later\n")
        _git(self.repo, "add", "later.txt")
        _git(self.repo, "commit", "-qm", "main advances past the merged head")
        _git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")

        # Preconditions, so this cannot pass for the wrong reason: H is now
        # STRICTLY BEHIND main's tip (so `sha != main_tip` is satisfied), and H IS
        # on main's first-parent chain (so only the first-parent exclusion can
        # refuse the demotion).
        tip = self._git_out("rev-parse", "origin/main")
        self.assertNotEqual(head_sha, tip)
        self.assertIn(head_sha, self._git_out(
            "rev-list", "--first-parent", "origin/main").splitlines())

        self.gh_fixtures(closed_prs=[{
            "number": 4242, "title": "the earlier landing", "body": "",
            "state": "closed", "url": "https://example.invalid/4242",
            "headRefName": "fix/earlier",
            # H is the merged PR's head, so predicate 1 would fire on it.
            "headSha": head_sha,
            "mergedAt": "2026-09-01T00:00:00Z",
        }])
        rc, out = self.run_tool(issue=ISSUE)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertNotIn("squash-merged", out)

    def test_fresh_branch_left_behind_by_main_still_blocks(self):
        # ⛔ THE CYCLE-5 P0 GUARD, and the defect it closes was the WIDE window,
        # not an exotic edge. `sha != main_tip` protected a fresh branch only
        # until `origin/main` advanced ONE commit; after that the branch was a
        # STRICT ancestor of main and plain ancestry called it landed. The
        # ordinary fleet flow hits this every time: `git worktree add -b
        # fix/N-slug` (nothing committed yet), then some OTHER PR merges — and any
        # lane running the pre-flight for N in that window got CLEAN on an issue
        # another lane had already claimed by creating the branch. Not maskable by
        # the remote surface, because the branch has not been pushed.
        #
        # The discriminator is membership of main's FIRST-PARENT chain: a fresh
        # branch's tip IS a main-line commit however far main has moved, whereas
        # an absorbed branch head entered main as a MERGE parent.
        #
        # BOTH surfaces are asserted as ROWS: `add_worktree` also creates a
        # branch, so the branch surface would supply the verdict on its own and a
        # verdict-level assertion could not tell whether the worktree path (and
        # its `first_parent` threading) works at all.
        branch = f"fix/{ISSUE}-fresh-behind"
        self.add_worktree("behind", branch)
        (self.repo / "later.txt").write_text("later\n")
        _git(self.repo, "add", "later.txt")
        _git(self.repo, "commit", "-qm", "another lane lands")
        _git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")

        # Preconditions, so this test cannot pass for the wrong reason:
        # the ref IS an ancestor of main (the state plain ancestry misreads)...
        tip = self._git_out("rev-parse", branch)
        merged = self._git_out("for-each-ref", "--format=%(refname)",
                               "--merged=origin/main", "refs/heads")
        self.assertIn(f"refs/heads/{branch}", merged)
        # ...and its tip IS on main's first-parent chain (the state that must
        # make the exclusion fire, rather than the branch merely being unmerged).
        self.assertIn(tip, self._git_out(
            "rev-list", "--first-parent", "origin/main").splitlines())

        self.gh_fixtures(closed_prs=[])
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        for row_prefix in ("local branches", "local worktrees"):
            row = next(ln for ln in out.splitlines()
                       if ln.startswith(row_prefix))
            self.assertIn("HIT", row, row)
        self.assertNotIn("merged into origin/main", out)

    def test_branch_created_at_main_tip_with_no_commits_still_blocks(self):
        # ⛔ THE P1-B GUARD, and the mutation the test above cannot catch: if the
        # arm were non-strict (plain ancestry), this branch — created at
        # origin/main's tip, no commits of its own — is trivially "an ancestor
        # of main" and would be demoted by the ancestry arm. The assertion below is
        # that it is NOT demoted as "merged into origin/main".
        #
        # ⚠️ Since #6108 was reopened (2026-10-24) it can ALSO be demoted, by the
        # EMPTY-CLAIM leg — and it still BLOCKS here, for a reason that is the leg's own
        # contract: `setUp`'s `origin` is an `https://github.com/…` URL, so the claim
        # registry read fails, an unreadable registry is LIVE, and the ref is kept blocking.
        # That is the fail-closed posture that makes the window this test was written for
        # (`git worktree add`, no commit yet) safe on any box whose claim registry cannot be
        # read — the demotion needs a POSITIVE "no claim" answer, never an absent one.
        _git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        _git(self.repo, "branch", f"fix/{ISSUE}-fresh")
        self.gh_fixtures(closed_prs=[])
        rc, out = self.run_tool(issue=ISSUE)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertNotIn("merged into origin/main", out)

    def test_branch_hit_reports_its_ahead_count_and_still_blocks(self):
        # #6108: an EMPTY branch named after an issue and a branch holding real
        # WIP printed IDENTICALLY, so the operator could not tell "a lane is
        # working this" from "a branch whose name contains this number exists".
        # Measured on four P0 issues, two of them empty; the difference was
        # discoverable only by running `git rev-list --count origin/main..<b>`
        # BY HAND afterwards. The count is now in the hit line.
        #
        # ⛔ THE COUNT IS REPORTED ON EVERY BLOCKING HIT, and this test pins that half.
        # The other half — that a 0-ahead ref is demoted — is #6108 REOPENED (2026-10-24)
        # and is pinned in both directions by the EMPTY-CLAIM matrix
        # (`test_a_zero_ahead_clean_unowned_unclaimed_branch_is_advisory` for the
        # demotion; `..._one_commit_ahead_still_blocks`, `..._with_uncommitted_work_still
        # _blocks`, `..._with_a_live_process_in_its_worktree_still_blocks`,
        # `..._with_a_live_claim_ref_still_blocks` and
        # `test_an_unreadable_distance_blocks_a_zero_ahead_branch_as_UNREADABLE` for the
        # refusals). This fixture keeps blocking because its claim registry is unreadable
        # (`setUp`'s origin is an https URL), which is itself one of the refusals.
        _git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        _git(self.repo, "branch", f"fix/{ISSUE}-empty")
        self.gh_fixtures(closed_prs=[])
        rc, out = self.run_tool(issue=ISSUE)
        self.assertIn(f"refs/heads/fix/{ISSUE}-empty", out)
        self.assertIn("0 commit(s) ahead", out)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)

    def test_branch_hit_reports_a_nonzero_ahead_count(self):
        # The other half of the datum, and the one that makes it useful: a branch
        # with real WIP says so, and keeps blocking. TWO commits, so the count
        # cannot be confused with the empty branch's 0 above.
        _git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        _git(self.repo, "checkout", "-q", "-b", f"fix/{ISSUE}-wip")
        for i in (1, 2):
            (self.repo / f"w{i}.txt").write_text("w\n")
            _git(self.repo, "add", f"w{i}.txt")
            _git(self.repo, "commit", "-q", "-m", f"wip {i}")
        _git(self.repo, "checkout", "-q", "main")
        self.gh_fixtures(closed_prs=[])
        rc, out = self.run_tool(issue=ISSUE)
        self.assertIn("2 commit(s) ahead", out)
        self.assertNotEqual(rc, 0, out)

    def test_remote_branch_hit_also_reports_its_ahead_count(self):
        # #6108 asks for the same treatment off the local surface, and the
        # remote refs are where the OVER-BLOCK actually lands: the live refusal
        # list for the measured issue named two remote refs, and #7693 keeps
        # them blocking by design. A count here is REPORTING only — it does not
        # change the verdict — which is exactly what makes the over-block
        # auditable without reversing that decision.
        _git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        _git(self.repo, "update-ref", f"refs/remotes/origin/fix/{ISSUE}-rc", "HEAD")
        self.gh_fixtures(closed_prs=[])
        rc, out = self.run_tool(issue=ISSUE)
        self.assertIn(f"refs/remotes/origin/fix/{ISSUE}-rc", out)
        self.assertIn("0 commit(s) ahead", out)
        self.assertNotEqual(rc, 0, out)

    def test_unmeasured_ahead_count_is_omitted_not_zeroed(self):
        # ⛔ THE CALLER HALF OF THE "never fabricated" PROPERTY, which the unit
        # test above cannot reach: it asserts `_ahead_behind` returns None, but
        # the NON-FABRICATION decision lives in the CALLER (`if measured is not
        # None:` / `if ahead is not None:`). A mutant that defaulted a missing
        # measurement to `(0, 0)` — `ahead, behind = measured or (0, 0)` — passed
        # the whole file otherwise, because every other matching-branch test
        # asserts only `assertIn("matched issue-number …")`, a prefix the
        # fabricated text still satisfies.
        #
        # That mutant is exactly the failure #6108's fail-closed paragraph names:
        # a fabricated `0` reads as "empty branch, safe to ignore" on a ref whose
        # distance from main was never measured — a false CLEAN manufactured out
        # of a missing measurement.
        #
        # `setUp` never creates `origin/main`, so the matching branch here is
        # UNMEASURABLE by construction and the count must be absent — not zero.
        _git(self.repo, "branch", f"fix/{ISSUE}-unmeasured")
        self.gh_fixtures(closed_prs=[])
        rc, out = self.run_tool(issue=ISSUE)
        self.assertIn(f"refs/heads/fix/{ISSUE}-unmeasured", out)
        self.assertIn(f"matched issue-number ({ISSUE})", out)
        self.assertNotIn("commit(s) ahead", out)
        self.assertNotIn("0 commit(s) ahead", out)
        # ...and the unmeasured ref still BLOCKS: an absent measurement is not a
        # downgrade.
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)

    def test_unreadable_ahead_count_returns_none_and_is_never_fabricated(self):
        # ⛔ THE FAIL-CLOSED HALF OF #6108's DATUM. `_ahead_behind` returns None
        # when git cannot answer, and the caller must print NO count rather than
        # "0": a fabricated zero reads as "empty branch, safe to ignore" on a
        # ref whose distance from main was never measured — a false CLEAN
        # manufactured out of a missing measurement.
        bogus = self.tmp / "not-a-git"
        bogus.write_text("#!/bin/sh\nexit 128\n")
        bogus.chmod(0o755)
        mod = _tool_module()
        self.assertIsNone(
            mod._ahead_behind(str(bogus), str(self.repo), "refs/heads/main", 10.0)
        )
        # And a code path that cannot even be EXECUTED is None too, not a raise.
        self.assertIsNone(
            mod._ahead_behind("/nonexistent/git", str(self.repo), "refs/heads/main", 10.0)
        )

    def test_unresolvable_first_parent_never_downgrades(self):
        # ⛔ THE `first_parent is not None` GUARD AND `_first_parent_shas`'s
        # `return None`, both of which were unfalsifiable before this stub: no
        # test could make `first_parent` None while `main_tip` was readable, so
        # deleting the guard broke nothing. Deleting it yields
        # `TypeError: argument of type 'NoneType' is not iterable` at
        # `sha not in first_parent` — a traceback, exit 1, NO `VERDICT` line,
        # which a caller reads as COLLISION.
        #
        # ⛔ Making `_first_parent_shas` return an empty SET instead of None is
        # NOT the harmless half, and an earlier version of this comment said it
        # was ("would do nothing"). A mutation disproved that: an empty set
        # satisfies `first_parent is not None`, so predicate 2 FIRES, an absorbed
        # branch is called landed ("merged into origin/main"), the hit is
        # demoted, and the run ends CLEAN (exit 0) on work the caller owns. That
        # is why this test fails on the mutation at `assertNotEqual(rc, 0)`,
        # rather than merely reporting a read that could not be trusted.
        #
        # The branch is genuinely absorbed (a real `--no-ff` merge put it on main
        # as a merge parent), and `closed_prs=[]` removes predicate 1, so
        # predicate 2 is the ONLY arm that could downgrade — and it must decline.
        ref = f"fix/{ISSUE}-absorbed"
        _git(self.repo, "checkout", "-q", "-b", ref)
        (self.repo / f"{ISSUE}.txt").write_text("work\n")
        _git(self.repo, "add", f"{ISSUE}.txt")
        _git(self.repo, "commit", "-q", "-m", f"work on {ISSUE}")
        _git(self.repo, "checkout", "-q", "main")
        _git(self.repo, "merge", "--no-ff", "-q", "-m", "land it", ref)
        _git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")

        self.gh_fixtures(closed_prs=[])
        git_stub = _write_exec(self.tmp / "git-stub-no-fp",
                               GIT_STUB_NO_FIRST_PARENT)
        rc, out = self.run_tool(git_bin=git_stub, issue=ISSUE,
                                env_extra={"REAL_GIT": shutil.which("git")})
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertNotIn("merged into origin/main", out)
        self.assertNotIn("Traceback", out)

    def test_branch_at_main_tip_matching_a_merged_head_blocks_without_first_parent(self):
        # ⛔ THE `sha != main_tip` GUARD ON PREDICATE 1, which the review found was
        # called "the only guard left" in a comment and verified by nothing.
        #
        # With `first_parent` UNREADABLE the first-parent exclusion cannot fire,
        # so predicate 1 falls back to the exact-tip comparison — and this is
        # exactly the payload that needs it: a branch created at origin/main's tip
        # (no commits) whose SHA happens to equal a merged PR's `head.sha`. Without
        # `sha != main_tip` this is called "squash-merged", demoted, and the lane
        # that owns the branch is told CLEAN on an issue it has already claimed.
        #
        # `test_fresh_branch_at_a_PREVIOUS_merged_head_still_blocks` cannot reach
        # this: there `first_parent` IS readable, so the other exclusion covers it.
        _git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        tip = self._git_out("rev-parse", "HEAD")
        _git(self.repo, "branch", f"fix/{ISSUE}-fresh")
        self.gh_fixtures(closed_prs=[{
            "number": 4242, "title": "an unrelated landing",
            "body": "", "state": "closed",
            "headRefName": "feat/9999-other", "headSha": tip,
            "mergedAt": "2026-09-01T00:00:00Z",
        }])
        git_stub = _write_exec(self.tmp / "git-stub-no-fp",
                               GIT_STUB_NO_FIRST_PARENT)
        rc, out = self.run_tool(git_bin=git_stub, issue=ISSUE,
                                env_extra={"REAL_GIT": shutil.which("git")})
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertNotIn("squash-merged", out)
        self.assertNotIn("Traceback", out)

    def test_fresh_branch_behind_main_at_a_merged_head_blocks_without_first_parent(self):
        # ⛔ THE CROSS-PRODUCT CYCLE 7 LEFT OPEN. Its stub — `rev-list
        # --first-parent` failing while `rev-parse` and the `--merged` walk both
        # succeed — was the right instrument, but it was pointed only at the
        # CURRENT-TIP variant, where `sha != main_tip` happens to refuse the
        # demotion. One commit further back that guard is satisfied and the
        # exclusion degrades to exactly the one-commit-deep test cycle 6
        # replaced, so a branch created at main's tip and left behind by ONE
        # commit was demoted to CLEAN on an issue it had already claimed.
        #
        # The fallback discriminator is the `--merged` walk: a ref merged into
        # main has a tip main already contains, which is what a fresh branch
        # points at, so it must NOT be called landed.
        _git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        behind = self._git_out("rev-parse", "HEAD")
        _git(self.repo, "branch", f"fix/{ISSUE}-behind")
        # Advance main by ONE commit, so `sha != main_tip` no longer protects.
        (self.repo / "advance.txt").write_text("later work\n")
        _git(self.repo, "add", "advance.txt")
        _git(self.repo, "commit", "-q", "-m", "unrelated later work")
        _git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.assertNotEqual(
            self._git_out("rev-parse", "HEAD"), behind,
            "precondition: main must have MOVED, or this test collapses into "
            "the current-tip variant it exists to go beyond",
        )
        self.gh_fixtures(closed_prs=[{
            "number": 4242, "title": "an unrelated landing", "body": "",
            "state": "closed", "headRefName": "feat/9999-other",
            "headSha": behind, "mergedAt": "2026-09-01T00:00:00Z",
        }])
        git_stub = _write_exec(self.tmp / "git-stub-no-fp",
                               GIT_STUB_NO_FIRST_PARENT)
        rc, out = self.run_tool(git_bin=git_stub, issue=ISSUE,
                                env_extra={"REAL_GIT": shutil.which("git")})
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertNotIn("squash-merged", out)
        self.assertNotIn("Traceback", out)

    def test_unmerged_branch_off_main_still_blocks(self):
        # ...and here the only difference is that the branch carries a commit
        # `origin/main` does NOT have, so it is genuinely in flight.
        _git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        ref = f"fix/{ISSUE}-ancestor-live"
        _git(self.repo, "checkout", "-q", "-b", ref)
        (self.repo / "live.txt").write_text("live\n")
        _git(self.repo, "add", "live.txt")
        _git(self.repo, "commit", "-qm", "work in progress")
        # Back to main, so the branch is NOT the current branch — otherwise it
        # would be auto-declared as self-identity and the test would pass for
        # the wrong reason (the auto-declaration is pinned separately by
        # test_current_branch_is_declared_automatically).
        _git(self.repo, "checkout", "-q", "main")
        self.gh_fixtures(closed_prs=[])
        rc, out = self.run_tool(issue=ISSUE)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)

    # ── the closed reference field is REQUIRED, never assumed empty ─────────

    def test_missing_closing_reference_field_is_incomplete_not_clean(self):
        # #3504. A payload without `closingIssuesReferences` is a payload whose
        # closing references are UNKNOWN. Reading the absence as "this PR closes
        # nothing" would DROP a blocking signal — the fail-OPEN direction — so
        # the surface goes INCOMPLETE (exit 2) instead. The harness normalizes
        # the field in, so the key is deleted here explicitly to reach this path.
        self.gh_fixtures(open_prs=[{
            "number": 5150, "title": "unrelated", "body": "",
            "headRefName": "feat/5150-other", "state": "open",
        }])
        prs = json.loads((self.gh_dir / "open_prs.json").read_text())
        del prs[0]["closingIssuesReferences"]
        (self.gh_dir / "open_prs.json").write_text(json.dumps(prs))

        rc, out = self.run_tool()
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertIn("closing-reference-source-unavailable", out)

    def test_closing_field_present_but_empty_is_clean_not_incomplete(self):
        # The other side of the same coin, and the mutation guard: a field that
        # IS present and empty is a KNOWN "closes nothing" — an evaluated,
        # empty dimension, not an unreadable one. If the check were `if not
        # field: raise`, this CLEAN run would wrongly become exit 2.
        self.gh_fixtures(open_prs=[{
            "number": 5150, "title": "unrelated", "body": "",
            "headRefName": "feat/5150-other", "state": "open",
            "closingIssuesReferences": [],
        }])
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)

    # ── class 2: a hit the tool calls non-blocking must not block ───────────

    def test_nonblocking_hits_do_not_set_the_verdict_but_are_reported(self):
        # #3504 class 2 / #4224. Live bug: `VERDICT: COLLISION — 8 hit(s)` where
        # six of the eight were labelled `(non-blocking) (weak)`. The verdict
        # counts BLOCKING hits only; the weak ones are still listed and COUNTED
        # in a separate line, so the demotion is never silent.
        self.gh_fixtures(
            issue=self.issue_payload(assignees=("test-agent",)),
            open_prs=[{
                "number": 5150, "title": "unrelated cleanup",
                "body": f"prose mention of #{ISSUE} here", "headRefName":
                "feat/5150-other", "state": "open",
            }],
        )
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertNotIn("VERDICT: COLLISION", out)
        self.assertIn("non-blocking", out)
        # The table still reports BOTH hits — the fix is about the VERDICT, not
        # about hiding evidence. A mutant that suppressed the hits instead of
        # demoting them would fail here.
        #
        # ⛔ Matched on the CELL SEPARATION, not a fixed padding: #8629 widened
        # the SURFACE column to the longest real surface name (31 chars, the
        # new ADVISORY row) so every row's STATUS/HITS stay aligned, and a
        # hardcoded 24-char pad would have pinned the cosmetic width instead of
        # the property this test is about.
        self.assertRegex(out, r"(?m)^open PRs\s+HIT\s+1")
        self.assertRegex(out, r"(?m)^issue assignee/holder\s+HIT\s+1")
        self.assertEqual(out.count("(non-blocking)"), 2, out)

    def test_collision_count_names_only_blocking_hits(self):
        # #3504 class 2, the COUNT half — and the half nothing pinned. The
        # verdict line used to print `len(hits)`, so #2573 read
        # `COLLISION — 8 hit(s)` while its actual blocking content was smaller:
        # the labels said non-blocking and the count contradicted them. A
        # refusal whose stated reason is not what caused it cannot be verified.
        #
        # ONE strong hit beside TWO weak ones: the count must be 1, and the
        # remainder must be EXPLAINED rather than silently dropped.
        self.gh_fixtures(
            issue=self.issue_payload(assignees=("test-agent",)),   # weak
            open_prs=[
                {"number": 5150, "title": "unrelated",       # weak (prose)
                 "body": f"prose mention of #{ISSUE}", "headRefName":
                 "feat/5150-other", "state": "open"},
                {"number": 5151, "title": "fix: sibling cleanup",   # STRONG
                 "body": "", "headRefName": "feat/9999-other",
                 "state": "open",
                 "closingIssuesReferences": [{"number": ISSUE}]},
            ],
        )
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("1 blocking hit(s) across 1 surface(s)", out)
        self.assertIn("2 further hit(s) were reported but are non-blocking", out)

    # ── the deleted lexical arm is gone ON PURPOSE ─────────────────────────

    def test_keyword_flags_are_gone_not_merely_ignored(self):
        # #3504's Stage 0 deletes the lexical arm. A flag that still PARSED but
        # did nothing would be worse than one that errors: a caller would keep
        # passing it, believe the dimension was completed, and never learn the
        # gate is no longer consulting vocabulary. So it must be a usage error.
        for flag in ("--keywords", "--min-keywords"):
            with self.subTest(flag=flag):
                proc = subprocess.run(
                    [PYTHON, str(TOOL), str(ISSUE), "--repo", str(self.repo),
                     flag, "alpha,beta"], capture_output=True, text=True,
                    check=False,
                )
                self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
                self.assertIn("unrecognized arguments", proc.stderr)
                self.assertIn("--self-branch", proc.stderr)

    # ── incomplete, never silently clean ────────────────────────────────────

    def test_gh_unavailable_is_incomplete_not_clean(self):
        self.clear_fixtures()
        rc, out = self.run_tool()
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        self.assertIn("[open PRs]", out)
        self.assertIn("[recently-closed PRs]", out)
        self.assertIn("[issue assignee/holder]", out)
        self.assert_all_surface_rows(out)

    def test_git_surface_unavailable_is_incomplete(self):
        git_stub = _write_exec(self.tmp / "git-stub", GIT_STUB)
        rc, out = self.run_tool(git_bin=git_stub,
                                env_extra={"REAL_GIT": shutil.which("git")})
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        self.assertIn("[local branches]", out)
        # Other git surfaces remain queryable and clean — the worktree scan
        # still enumerated (its note only appears on a successful scan).
        self.assertIn("1 worktree(s) enumerated (untruncated)", out)

    def test_unavailable_title_is_no_longer_a_missing_dimension(self):
        # The title used to feed the keyword arm, so a failed `gh issue view`
        # left a keyword dimension INCOMPLETE. That dimension no longer exists:
        # the title is decoration in the report, not an input to a decision.
        #
        # The run still exits 2 — but for the SURFACE that failed, not a
        # dimension that no longer exists. That distinction is the point: an
        # INCOMPLETE run says WHICH surface could not be read.
        self.clear_fixtures()
        self.gh_fixtures(issue=None)
        rc, out = self.run_tool()
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertIn("[issue assignee/holder]", out)
        self.assertNotIn("[issue keywords]", out)
        self.assertNotIn("keyword-source-unavailable", out)

    # ── truncation: blocking -> INCOMPLETE; advisory sample -> reported ──────

    def test_open_pr_list_truncation_is_incomplete(self):
        # A PR list longer than its cap is a PARTIAL query — the fail-open
        # class this tool exists to prevent. It must read INCOMPLETE (exit 2).
        self.gh_fixtures(open_prs=[
            {"number": 1, "title": "a", "body": "", "headRefName": "chore/a"},
            {"number": 2, "title": "b", "body": "", "headRefName": "chore/b"},
        ])
        rc, out = self.run_tool(extra_args=["--pr-limit", "1"])
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        self.assertIn("TRUNCATED", out)
        self.assertIn("[open PRs]", out)

    def test_closed_pr_partial_sample_is_reported_but_not_incomplete(self):
        # The response's own Link header advertises more pages than the bounded
        # sample covers, so the sample MUST NOT read as the whole list — that is
        # the fail-open this tool exists to prevent. Because the surface is
        # ADVISORY, though, its partiality must NOT force INCOMPLETE either.
        # `~N` is the upper bound derived from the last page number; the `~`
        # says it is an estimate, not a measured total. Before #5251 this exact
        # shape was exit 2.
        self.gh_fixtures(closed_prs=[
            {"number": 3, "title": "a", "body": "", "headRefName": "chore/a"},
            {"number": 4, "title": "b", "body": "", "headRefName": "chore/b"},
        ])
        rc, out = self.run_tool(
            extra_args=["--closed-pr-limit", "1"],
            env_extra={"GH_STUB_CLOSED_PR_TOTAL_PAGES": "5"},
        )
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        # NOT INCOMPLETE: the words appear only inside the advisory NOTE that
        # says the run is NOT incomplete, so assert against the verdict and the
        # section header rather than the bare token.
        self.assertNotIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("INCOMPLETE SURFACES", out)
        self.assertIn("[recently-closed PRs]", out)
        self.assertIn("⚠ PARTIAL — advisory sample, cannot block", out)
        self.assertIn("sampled the most recent 2 of ~5 closed PR(s)", out)

    def test_closed_pr_next_relation_beats_a_flattering_last(self):
        # ⛔ THE TWO RELATIONS CAN DISAGREE, AND `last` MUST NOT WIN. The
        # invariant stated in `_closed_pr_sample` is that a `Link` carrying
        # `rel="next"` PROVES more pages exist and must never be reported as
        # "this page is the complete list" — "even when the rel=last entry is
        # absent or unparseable". The implementation only consulted `next` when
        # `last` was ABSENT, so a disagreeing pair resolved in favour of the
        # flattering one: a page whose own header proved a further page existed
        # was labelled complete.
        #
        # Exit code is unaffected (the surface is ADVISORY); the human reading
        # the report is the one misled — and "a partial list presented as
        # everything" is the failure this tool exists to prevent.
        #
        # `last` says ONE page (so the old arithmetic concluded "complete"),
        # while `next` proves page 2 exists.
        self.gh_fixtures(closed_prs=[
            {"number": 3, "title": "a", "body": "", "headRefName": "chore/a"},
            {"number": 4, "title": "b", "body": "", "headRefName": "chore/b"},
        ])
        rc, out = self.run_tool(
            extra_args=["--closed-pr-limit", "2"],
            env_extra={"GH_STUB_CLOSED_PR_TOTAL_PAGES": "1",
                       "GH_STUB_CLOSED_PR_FORCE_NEXT": "1"},
        )
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertIn("⚠ PARTIAL — advisory sample, cannot block", out)
        self.assertNotIn("(this page is the complete list)", out)

    def test_complete_pr_list_reports_the_count_and_stays_clean(self):
        self.gh_fixtures(open_prs=[
            {"number": 1, "title": "a", "body": "", "headRefName": "chore/a"},
        ])
        rc, out = self.run_tool(extra_args=["--pr-limit", "5"])
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertIn("1 PR(s) enumerated (complete, cap 5)", out)

    # ── closed-PR ONE-REQUEST sample, ADVISORY (#5251) ──────────────────────

    def test_closed_pr_surface_uses_rest_not_the_resetting_graphql_path(self):
        # REGRESSION GUARD (#3587). The stub reproduces production exactly: the
        # `gh pr list` GraphQL path FAILS for closed PRs while the REST endpoint
        # serves them. Since #5251 the closed-PR match is ADVISORY (reported,
        # never blocking), but the TRANSPORT guarantee is unchanged: it must
        # never regress to the GraphQL path that resets on this host.
        self.gh_fixtures(closed_prs=[{
            "number": 9998, "title": "time-dependent ranking",
            "body": "", "headRefName": "fix/3061-fts-determinism",
        }])
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertIn("[recently-closed PRs]", out)
        self.assertIn("matched issue-number (3061) in branch", out)
        self.assertNotIn("VERDICT: INCOMPLETE", out)
        argv = (self.gh_dir / "api-argv.txt").read_text()
        self.assertIn("-i", argv)
        self.assertIn("state=closed", argv)
        self.assertNotIn("pr list", argv)

    def test_closed_pr_rest_complete_enumeration_is_clean(self):
        # With NO `Link: rel="last"` header the single page IS the whole list,
        # so the surface is complete — reported as such and CLEAN (exit 0).
        self.gh_fixtures(closed_prs=[
            {"number": 3, "title": "a", "body": "", "headRefName": "chore/a"},
            {"number": 4, "title": "b", "body": "", "headRefName": "chore/b"},
        ])
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertNotIn("VERDICT: INCOMPLETE", out)
        self.assertIn(
            "2 closed PR(s) in one request (this page is the complete list)", out)
        self.assertNotIn("⚠ PARTIAL", out)

    def test_closed_pr_failure_is_reported_but_does_not_force_incomplete(self):
        # A non-zero exit on the single request leaves the surface UNQUERYABLE.
        # Nothing from the failed request is salvaged into hits (the transport
        # failed mid-stream), and the failure is REPORTED — but it cannot force
        # INCOMPLETE, because an advisory surface's failure conceals no
        # collision. Before #5251 this exact shape halted a dispatch.
        self.gh_fixtures(closed_prs=[{
            "number": 9998, "title": "fix: 3061 planted",
            "body": "", "headRefName": "fix/3061-planted",
        }])
        rc, out = self.run_tool(env_extra={"GH_STUB_API_FAIL_AFTER_OUTPUT": "1"})
        self.assertEqual(rc, 0, out)
        self.assertNotIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("INCOMPLETE SURFACES", out)
        self.assertIn("[recently-closed PRs]", out)
        self.assertIn("connection reset by peer", out)
        # The failure is REPORTED, never silent...
        self.assertIn("ADVISORY SURFACES", out)
        self.assertIn("gh-unavailable", out)
        # ...and the rows printed before the failure were NOT scanned into hits.
        self.assertNotIn("planted", out)

    def test_closed_pr_unparseable_output_is_reported_but_not_incomplete(self):
        # Two ways the `-i` response can be unusable: an empty body (a proxy
        # swallowed it) and a missing header/body separator (the total cannot be
        # derived from the headers). Neither may read as a COMPLETE scan, and
        # neither may force INCOMPLETE — both are reported in the advisory
        # section instead.
        (self.gh_dir / "closed_prs.json").write_text("")
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertNotIn("VERDICT: INCOMPLETE", out)
        self.assertIn("[recently-closed PRs]", out)
        self.assertIn("ADVISORY SURFACES", out)
        self.assertIn("non-JSON body", out)
        self.assertNotIn("this page is the complete list", out)

        self.gh_fixtures(closed_prs=[
            {"number": 3, "title": "a", "body": "", "headRefName": "chore/a"},
        ])
        rc, out = self.run_tool(env_extra={"GH_STUB_API_NO_SEPARATOR": "1"})
        self.assertEqual(rc, 0, out)
        self.assertNotIn("VERDICT: INCOMPLETE", out)
        self.assertIn("[recently-closed PRs]", out)
        self.assertIn("no header/body separator", out)
        self.assertNotIn("this page is the complete list", out)

    def test_closed_pr_malformed_json_body_is_reported_but_not_incomplete(self):
        # A truncated JSON body (partial write) is not a short-but-valid list.
        # It must be reported, must never be parsed as "fewer PRs", and must not
        # force INCOMPLETE — the advisory surface's failure cannot conceal a
        # collision. (The non-zero-exit half is pinned by the test above; this
        # pins the malformed-PARSE half.)
        (self.gh_dir / "closed_prs.json").write_text(
            '[{"number": 1, "title": "a", "body": "", "headRefName": "chore/a"},'
            '{"number": 2, "title": "b"'
        )
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertNotIn("VERDICT: INCOMPLETE", out)
        self.assertIn("[recently-closed PRs]", out)
        self.assertIn("non-JSON body", out)
        self.assertNotIn("this page is the complete list", out)

    def test_closed_pr_sample_is_one_bounded_request_and_still_scanned(self):
        # The whole point of #5251: the surface is fetched in EXACTLY ONE
        # request (no `--paginate` following rel="next"), and the hit inside that
        # single page is still found and reported. `--paginate` is asserted
        # absent from the argv and the stub's per-invocation call log must hold
        # exactly one line — a regressed pagination would append one per page.
        # Written DIRECTLY rather than through `gh_fixtures`, because this test
        # controls the `Link` header, so the normalizer does not supply `state` —
        # and `state` is now REQUIRED. `"OPEN"` is the GraphQL casing `gh pr list
        # --json state` returns and `_pr_terminal_state` does not read as
        # terminal, so the hit keeps its non-terminal detail, which is what the
        # assertion below is about. A missing `state` would be a REFUSAL, and the
        # surface would report no hit at all.
        (self.gh_dir / "closed_prs.json").write_text(json.dumps([
            {"number": 1, "title": "a", "body": "", "headRefName": "chore/a",
             "state": "OPEN", "mergedAt": None, "headSha": ""},
            {"number": 9998, "title": "b", "body": "", "headRefName": "fix/3061-page2",
             "state": "OPEN", "mergedAt": None, "headSha": ""},
        ]))
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)  # advisory: reported, never blocking
        self.assertIn("matched issue-number (3061) in branch", out)
        self.assertIn("[recently-closed PRs]", out)
        self.assertIn(
            "2 closed PR(s) in one request (this page is the complete list)", out)
        argv = (self.gh_dir / "api-argv.txt").read_text()
        self.assertIn("-i", argv)
        self.assertNotIn("--paginate", argv)
        calls = (self.gh_dir / "api-calls.log").read_text().splitlines()
        self.assertEqual(len(calls), 1, calls)

    def test_advisory_closed_pr_strong_shape_cannot_block_but_is_reported(self):
        # #5251, the STRUCTURAL guarantee. A payload that is NOT terminal by
        # `_pr_terminal_state` has a `strong`-shaped branch match, so the
        # demotion must come from the SURFACE's authority and not from a payload
        # field (#5129's data-dependent shape is exactly what that cannot give).
        # It must be CLEAN (exit 0) with the match reported.
        #
        # ⛔ NON-TERMINAL IS NOW EXPRESSED BY A STATE, NOT BY ITS ABSENCE. This
        # fixture used to omit `state` entirely, which got the non-terminal read
        # by accident: a payload missing `state` is REFUSED now (it was the
        # fail-open where a truthy `mergedAt` plus no `state` demoted a live
        # open PR). `"OPEN"` is the GraphQL casing `gh pr list --json state`
        # actually returns and `_pr_terminal_state` does not read as terminal, so
        # this is both a real shape and a non-terminal one — the property the
        # test needs, obtained deliberately rather than from malformation.
        self.gh_fixtures(closed_prs=[{
            "number": 9998, "title": "time-dependent ranking",
            "body": "", "headRefName": "fix/3061-fts-determinism",
            "state": "OPEN",
        }])
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertNotIn("VERDICT: COLLISION", out)
        self.assertNotIn("do NOT dispatch", out)
        self.assertIn("[recently-closed PRs]", out)
        self.assertIn("matched issue-number (3061) in branch", out)
        self.assertRegex(out, r"recently-closed PRs\s+ADVISORY")
        self.assertIn("ADVISORY SURFACES", out)

    def test_clean_report_counts_only_surfaces_actually_queried(self):
        # Review cycle 2 (P2-7): the CLEAN line counts surfaces actually READ.
        # Before #5251 it was reachable only when every surface had been
        # queried, so a full count was literally true; with an advisory surface
        # it can now mean 6 of 7 were read. A silent revert of that count would
        # otherwise pass the whole suite, because the pre-existing full-count
        # assertion still matches a fully-queried run.
        #
        # (The totals were 6 before #8560 added the `issue landed` row, and were
        # themselves 7 -> 6 when #3504 deleted the `issue keywords` row.
        # Recomputing them here from SURFACE_ROWS instead of hardcoding them
        # would have hidden BOTH deletions, so they stay literal — and two
        # literals now pin the count.)
        self.gh_fixtures(open_prs=[])
        # Fail ONLY the (advisory) closed-PR request: every blocking surface
        # stays clean, so the run must still reach CLEAN.
        rc, out = self.run_tool(env_extra={"GH_STUB_API_FAIL_AFTER_OUTPUT": "1"})
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        # 7, not 8 — and the shortfall is NAMED, not hidden.
        self.assertIn("7/8 surfaces queried", out)
        self.assertNotIn("8/8 surfaces queried", out)
        self.assertIn("advisory surface(s) partial or unqueried", out)

    def test_advisory_failure_coexists_with_a_collision_without_escalating(self):
        # The #5251 OVERRIDE: a surface that can never produce a blocking hit has
        # no ability to prevent a duplicate, so its failure must not escalate the
        # run. This pins the COEXISTENCE — an ADVISORY surface can be unqueryable
        # while a BLOCKING surface collides, the advisory failure is still
        # REPORTED, and the verdict stays COLLISION (exit 1) rather than becoming
        # INCOMPLETE (exit 2). The pre-#5251 behaviour escalated it.
        #
        # ⛔ THE PREVIOUS VERSION OF THIS TEST COULD NOT FAIL FOR THE PROPERTY IT
        # NAMED. It asserted "COLLISION despite an advisory failure" (decided by
        # the strong open-PR hit alone) and "INCOMPLETE despite an advisory
        # failure" (decided by the unqueryable BLOCKING surface alone), so a
        # verifier deleted the advisory/blocking split entirely
        # (`ADVISORY_SURFACES = frozenset()`) and the test still PASSED. The
        # distinguishing facts are that the advisory failure was PRESENT and did
        # NOT escalate — so both are asserted here: the advisory surface IS named
        # as failed, and the exit code is 1.
        #
        # ⛔ WHAT THIS TEST DOES *NOT* CATCH, stated because an earlier version of
        # this comment claimed it did and a verifier disproved it by mutation: it
        # does NOT catch a regression that puts advisory surfaces into the
        # INCOMPLETE list (`for s in blocking` -> `for s in ordered`). Here the
        # verdict is COLLISION, decided by `if strong:` BEFORE the incomplete list
        # is consulted, so that regression cannot escalate this run. The test that
        # catches it is
        # `test_closed_pr_failure_is_reported_but_does_not_force_incomplete`,
        # which has an advisory failure and nothing else.
        #
        # What THIS test is non-inert for is the advisory TIER itself: with
        # `ADVISORY_SURFACES = frozenset()` the surface stops being labelled
        # ADVISORY and the ADVISORY SURFACES section disappears (mutation-verified).
        self.gh_fixtures(open_prs=[{
            "number": 9999, "title": "fix: guard retrieval (#3061)",
            "body": "closes it", "headRefName": "fix/guard",
        }])
        rc, out = self.run_tool(env_extra={"GH_STUB_API_FAIL_AFTER_OUTPUT": "1"})
        self.assertEqual(rc, 1, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("[open PRs]", out)
        self.assertIn("do NOT dispatch", out)
        # (a) the advisory surface really WAS unqueryable in this run. The
        # ADVISORY SURFACES section carries it AND the note that states the rule
        # this test exists to pin — "partial or unqueryable is NOT an INCOMPLETE
        # run". (The `advisory surface(s) partial or unqueried` phrasing lives on
        # the CLEAN path's counted line, which a COLLISION run does not reach, so
        # asserting it here would have been asserting a string the run cannot
        # produce.)
        self.assertIn("ADVISORY SURFACES", out)
        self.assertIn(
            "an advisory surface being partial or unqueryable is NOT an "
            "INCOMPLETE run", out,
        )
        self.assertIn("gh-unavailable", out)
        # (b) it did not escalate the verdict to INCOMPLETE.
        self.assertNotIn("VERDICT: INCOMPLETE", out)

    def test_blocking_surface_incomplete_coexists_with_a_collision(self):
        # The complementary half, unchanged in intent: a BLOCKING surface that
        # could not be queried is REPORTED alongside the COLLISION rather than
        # suppressed by it. Drop the (blocking) open-PR hit so INCOMPLETE
        # surfaces on its own.
        self.gh_fixtures(open_prs=[])
        rc, out = self.run_tool(env_extra={
            "GH_STUB_API_FAIL_AFTER_OUTPUT": "1",
            "GH_STUB_ISSUE_ABSENT": "1",
        })
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertIn("INCOMPLETE SURFACES", out)
        self.assertIn("issue-absent", out)

    def test_an_absent_issue_names_the_target_not_gh_auth(self):
        # #9423 review (P3): `_incomplete_remedy` is now the ONE place that
        # chooses a remedy per cause, so it must not answer an `issue-absent` /
        # `repo-unresolved` surface with "fix gh auth/network (or rebuild the
        # fleet board)" — the surface's own note says the target does not exist
        # here, and neither remedy can change that. Pre-fix the generic sentence
        # was printed for every cause; it is only a real defect once the function
        # claims to describe the cause it measured.
        self.gh_fixtures(open_prs=[])
        rc, out = self.run_tool(env_extra={"GH_STUB_ISSUE_ABSENT": "1"})
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertIn("issue-absent", out)
        # The verdict line now names the TARGET for the absent surface, and says
        # the generic remedies cannot help — the fact the surface note already
        # stated. ⛔ This fixture produces the MIXED arm on purpose: with the
        # issue unreadable, `issue landed` is separately incomplete for its own
        # (non-target) cause, and the generic remedy is correctly kept for THAT
        # remainder — so the assertion is that the absent cause is NAMED AND
        # SCOPED, not that the generic sentence is gone from the line.
        self.assertIn("a TARGET the tool cannot resolve (issue assignee/holder", out)
        self.assertIn("re-authenticating cannot change it", out)
        self.assertIn("and PART is:", out)

    def test_the_issue_metadata_surface_records_a_budget_exhaustion_as_data(self):
        # #9423 review round 12 (P3): three rescue sites know the cause from the
        # SAME predicate, and two of them had stopped re-parsing prose. The third —
        # the issue-metadata rescue — still relied on the note. This pins the
        # NEGATIVE half, which is the discriminating one: a NON-exhaustion failure
        # must NOT be flagged, or the remedy would name the budget for a cause the
        # run never measured (the mirror-image of #9402's defect).
        # ⛔ THE NEGATIVE HALF MUST DRIVE THE NON-EXHAUSTION PATH, not a dataclass
        # default (#9423 review round 14, P3 — the previous version asserted only
        # `Surface(...).budget_exhausted is False`, which no code path can change,
        # and that vacuity is why round 13's regression passed green). Here the
        # issue read fails with a PLAIN transport error (the fixture is gone), so
        # no exhaustion was ever measured and the remedy must keep the generic
        # sentence.
        self.clear_fixtures()
        rc, out = self.run_tool()
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("GRAPHQL BUDGET EXHAUSTION", out)
        self.assertIn("fix gh auth/network", out)
        # Restore the default fixtures: `clear_fixtures` removed ALL of them, and
        # the positive half below must fail on the ONE surface it is about.
        self.gh_fixtures(open_prs=[], closed_prs=[], issue=self.issue_payload())
        self.timeline_fixture()
        # The positive half is asserted end-to-end: with the GraphQL read
        # exhausted and the REST rescue ALSO down (so the surface is INCOMPLETE and
        # the remedy is printed), the verdict line names the budget rather than
        # `gh auth`.
        # ⛔ A VERBOSE stderr whose signature falls PAST the note's truncation
        # point, plus a failed rescue — the only shape that discriminates "records
        # the cause as DATA" from "re-parses the cut note". Measured: with the
        # `budget_exhausted` flag site removed, this test fails.
        rc, out = self.run_tool(env_extra={
            "GH_STUB_ISSUE_VIEW_VERBOSE_EXHAUSTION": "1",
            "GH_STUB_ISSUE_REST_FAIL": "1",
        })
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        # The MIXED arm: the exhausted surface is named with the budget cause, and
        # the remaining surface (`issue landed`, which could not read the issue at
        # all) keeps the generic remedy — the two are SCOPED, not merged.
        self.assertIn("PART of this is issue assignee/holder: a GRAPHQL BUDGET", out)
        self.assertIn("while the remaining surface(s) (issue landed) need", out)

    def test_a_rescued_issue_that_goes_incomplete_for_the_board_does_not_blame_the_budget(self):
        # ⛔ THE FLAG MUST NAME THE CAUSE OF *THIS* INCOMPLETE, NOT ANY EXHAUSTION
        # THE RUN SAW (#9423 review round 13, P3). Set before the rescue attempt,
        # `budget_exhausted` was also true when the issue read was RESCUED and the
        # surface then went INCOMPLETE for an unrelated reason — the holder gate's
        # "the fleet board could not answer". `_incomplete_remedy` classifies on the
        # flag alone, so the verdict line printed "the cause is a GRAPHQL BUDGET
        # EXHAUSTION … re-run when it resets" directly under a row naming the board:
        # a remedy contradicting the same report, which is #9402's own defect.
        #
        # ⛔ HOW THE BOARD IS MADE UNANSWERABLE — STATED AS THE CODE DOES IT
        # (#9423 review, P3). The gate does NOT refuse an other-repo board: a
        # fresh, answerable board stamped for another slug only gets a `why` from
        # `_board_is_for_repo`, and `_read_board` then FALLS THROUGH to a rebuild
        # ("A stale, other-repo, or unstamped board falls through to the same
        # rebuild"). The board is unanswerable because that REBUILD cannot
        # answer — which is why `build_state` is replaced here. The real rebuild
        # spends `fleet_state.DEFAULT_SAMPLE_S` sleeping and shells out to bare
        # `gh`/`cmux` from PATH, so reaching this state through it made the pin
        # cost ~4.5 s and depend on the operator's machine (see `fleet_board`'s
        # own docstring, and how `HolderGateTests` mocks this to stay hermetic).
        # Driving `run_preflight` in-process over the SAME gh stub keeps the
        # assertion on the REAL report and removes the live read.
        cp = _tool_module()
        board = self.fleet_board(repo="someone/else")
        target = cp.RepoTarget(
            slug=SLUG, path=str(self.repo), source="explicit", requested=True,
        )
        # Same filter `run_tool` applies: the cache seams are STATEFUL, so an
        # exported `COLLISION_PREFLIGHT_BOARD_CACHE` must not let one run's
        # rebuild answer this one.
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("COLLISION_PREFLIGHT_")}
        env.update({
            "HOME": str(self.home),
            "GH_STUB_DIR": str(self.gh_dir),
            "GH_STUB_ISSUE_VIEW_RATE_LIMITED": "1",
        })
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(cp.fleet_state, "DEFAULT_OUT", board), \
                mock.patch.object(
                    cp.fleet_state, "build_state",
                    side_effect=RuntimeError("no live read in this test"),
                ) as build:
            ordered, title, number = cp.run_preflight(
                ISSUE, target, str(self.gh), "git", 10.0,
            )
            report, code = cp.format_report(ordered, number, target, title)
        # The other-repo board really did FALL THROUGH to a rebuild — this is the
        # mechanism the paragraph above claims, and the pre-correction text ("the
        # gate refuses it") described a branch `_read_board` does not have.
        build.assert_called_once()
        # The issue read is exhausted AND rescued, so the report's incomplete row
        # is the board's, not the budget's.
        self.assertEqual(code, 2, report)
        self.assertIn("VERDICT: INCOMPLETE", report)
        self.assertIn("could not answer", report)
        # …and the remedy does NOT claim the budget is the cause.
        self.assertNotIn("the cause is a GRAPHQL BUDGET EXHAUSTION", report)

    def test_a_verbose_exhaustion_still_yields_the_budget_remedy_when_rest_fails(self):
        # #9423 review round 11 (P3): the round-10 fix recorded the cause as DATA
        # on the SUCCESSFUL-rescue branch only. The FAILED-rescue branch is inside
        # the same `_is_budget_exhaustion` gate — it knows the cause just as well —
        # but left `_incomplete_remedy` to re-parse a note whose gh error is cut at
        # 600 chars. The old pin drove 240 chars of chatter there, well inside the
        # cut, so the >cut case was unpinned on the one branch that could still
        # misname the cause. Now both branches set `surface.budget_exhausted`.
        rc, out = self.run_tool(env_extra={
            "GH_STUB_PR_LIST_VERBOSE_EXHAUSTION": "1",
            "GH_STUB_OPEN_PR_REST_FAIL": "1",
        })
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        # The REST failure is named (so this is not passing on an unrescued run)…
        self.assertIn("could not connect to api.github.com", out)
        # …and the remedy still names the BUDGET, not the generic sentence.
        self.assertIn("GRAPHQL BUDGET EXHAUSTION", out)
        self.assertNotIn("fix gh auth/network", out)

    def test_a_verbose_exhaustion_still_yields_the_budget_remedy(self):
        # ⛔ THE REMEDY MUST NOT DEPEND ON A SIGNATURE SURVIVING TRUNCATION
        # (#9423 review round 10, P3). The surface note embeds the gh error line
        # after `_one_line(..., limit=...)`, which keeps the FIRST N characters —
        # so a verbose stderr can push "API rate limit already exceeded" out of
        # the note entirely. `_incomplete_remedy` re-parsing that note then told
        # the reader to re-authenticate and rebuild the fleet board for a budget
        # exhaustion THIS RUN had already established — the exact #9402 defect
        # surviving at whatever the cut happens to be.
        #
        # The fix is structural: the rescue records `Surface.budget_exhausted`
        # because it only runs when the predicate matched, so no prose has to
        # survive. This fixture makes the signature fall past the cut: 120 chunks
        # of "chatter-" (~960 chars) precede it.
        rc, out = self.run_tool(env_extra={
            "GH_STUB_PR_LIST_VERBOSE_EXHAUSTION": "1",
        })
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertIn("closing-references-unmeasured", out)
        self.assertIn("GRAPHQL BUDGET EXHAUSTION", out)
        self.assertNotIn("fix gh auth/network", out)

    def test_the_landed_summary_does_not_deny_the_read_it_reports(self):
        # #9423 review round 9 (P3): the LANDED branch has its OWN `ALSO
        # INCOMPLETE` summary, and the previous commit fixed only the COLLISION
        # and INCOMPLETE sites. It is reachable — a CLOSED issue with the GraphQL
        # budget spent still rescues the open-PR surface over REST — and it printed
        # "could not be queried" a few lines under a row saying "N PR(s) WERE
        # read", i.e. the same contradiction.
        self.gh_fixtures(open_prs=[], issue=self.issue_payload(state="CLOSED",
                                                              state_reason="COMPLETED"))
        _rc, out = self.run_tool(env_extra={"GH_STUB_PR_LIST_RATE_LIMITED": "1"})
        self.assertIn("VERDICT: LANDED", out)
        self.assertIn("could not be FULLY queried", out)
        self.assertNotIn("surface(s) could not be queried", out)

    def test_the_summary_does_not_say_a_partially_read_surface_was_unqueryable(self):
        # #9423 review round 8 (P3): with the list rescued over REST, the open-PR
        # surface DID answer — only the computed closing-reference test is
        # unmeasured. The summary used to say "could not be queried (open PRs)"
        # directly beneath a hit `open PRs` had produced, contradicting both the
        # surface note ("N PR(s) WERE read") and the remedy. The verb is now
        # "could not be FULLY queried", which is true for all three incomplete
        # causes and does not deny the read that happened.
        self.gh_fixtures(open_prs=[{
            "number": 9416, "title": f"fix(dispatch): names the issue ({ISSUE})",
            "body": "", "headRefName": "feat/9999-other", "state": "open",
        }])
        rc, out = self.run_tool(env_extra={"GH_STUB_PR_LIST_RATE_LIMITED": "1"})
        self.assertEqual(rc, 1, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("could not be FULLY queried", out)
        # The false absolute is gone from BOTH summary lines.
        self.assertNotIn("surface(s) could not be queried", out)
        # …while the read itself is still reported as evidence.
        self.assertIn("1 PR(s) WERE read", out)
        # The cause LEADS the note (#9423 review round 9, P3), so the one
        # truncation point can never cut the signature `_incomplete_remedy`
        # classifies on: this report's remedy must still name the budget.
        note = out.split("closing-references-unmeasured:", 1)[1]
        self.assertLess(
            note.index("refused read"), note.index("PR(s) WERE read"),
            "the refused cause must precede the prose that can be truncated",
        )
        self.assertIn("GRAPHQL BUDGET EXHAUSTION", out)

    def test_a_null_rest_body_is_accepted_and_the_enumeration_still_runs(self):
        # #9423 review round 7 (P3): `body: null` is the REAL REST shape for a PR
        # with no description, and it is the COMMON production shape. The wire
        # check must ACCEPT it — otherwise the rescue refuses the entire
        # enumeration on any such element, silently disabling the mechanism this
        # PR exists to add. Pinned because the fixture used to derive `""` and so
        # could never produce the case.
        self.gh_fixtures(open_prs=[{
            "number": 9415, "title": "chore: describes nothing", "body": "",
            "headRefName": "chore/9999-other", "state": "open",
        }])
        raw = json.loads((self.gh_dir / "open_prs_rest.json").read_text())
        self.assertIsNone(raw[0]["body"], "the REST projection must send null, not ''")
        raw[0]["body"] = None
        (self.gh_dir / "open_prs_rest.json").write_text(json.dumps(raw))
        rc, out = self.run_tool(env_extra={"GH_STUB_PR_LIST_RATE_LIMITED": "1"})
        self.assertEqual(rc, 2, out)
        # ACCEPTED means READ: one PR reached the reader, and nothing was refused
        # for the body's shape.
        self.assertIn("1 PR(s) WERE read", out)
        self.assertNotIn("non-string 'body'", out)

    def test_falsy_non_string_rest_title_and_body_are_refused_not_laundered(self):
        # #9423 review round 6 (P3): the projection writes `title or ""` /
        # `body or ""`, so a FALSY non-string wire value was laundered into the
        # empty string before `_require_pr_dicts`' type loop could see it — the
        # "non-string title/body is a REFUSAL" contract held for TRUTHY
        # non-strings only. `null` is legal for `body` (a PR with no
        # description) and illegal for `title`.
        for key, bad in (("title", 0), ("title", None), ("body", False),
                         ("body", [])):
            with self.subTest(field=key, value=bad):
                self.gh_fixtures(open_prs=[{
                    "number": 9414, "title": "chore: unrelated wording", "body": "",
                    "headRefName": "chore/9999-other", "state": "open",
                }])
                raw = json.loads((self.gh_dir / "open_prs_rest.json").read_text())
                raw[0][key] = bad
                (self.gh_dir / "open_prs_rest.json").write_text(json.dumps(raw))
                rc, out = self.run_tool(
                    env_extra={"GH_STUB_PR_LIST_RATE_LIMITED": "1"})
                self.assertEqual(rc, 2, out)
                self.assertIn("VERDICT: INCOMPLETE", out)
                self.assertNotIn("VERDICT: CLEAN", out)
                self.assertIn(f"non-string '{key}' at index 0", out)

    def test_a_rest_open_pr_element_with_a_non_string_state_is_refused(self):
        # #9423 review (P3): the projection coerces `state`
        # (`str(...).upper()`), which made `_require_pr_dicts`' non-string refusal
        # UNREACHABLE on the REST transport — a wire `state` of `123` became the
        # string `"123"` and was READ as a live, non-terminal PR instead of being
        # refused. The direction is the safe one (non-terminal ⇒ strong ⇒
        # COLLISION, never CLEAN), but the documented contract has to hold on BOTH
        # transports. `_rest_pr_wire_reason` now types `state` before the
        # normalisation can launder it.
        self.gh_fixtures(open_prs=[{
            "number": 9413, "title": "fix(dispatch): unrelated wording", "body": "",
            "headRefName": "feat/9999-other", "state": "open",
        }])
        raw = json.loads((self.gh_dir / "open_prs_rest.json").read_text())
        raw[0]["state"] = 123
        (self.gh_dir / "open_prs_rest.json").write_text(json.dumps(raw))
        rc, out = self.run_tool(env_extra={"GH_STUB_PR_LIST_RATE_LIMITED": "1"})
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        self.assertIn("non-string 'state' at index 0 (int)", out)

    def test_closed_pr_rest_requests_the_projected_fields(self):
        # The REST response nests the branch under `head.ref` while the scanner
        # reads `headRefName`; the re-keying lives ONLY in the `--jq` filter.
        # The stub cannot run jq, so assert the filter actually asks for it —
        # otherwise a typo would silently disable ALL closed-PR branch matching
        # while every other test stayed green.
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        argv = (self.gh_dir / "api-argv.txt").read_text()
        self.assertIn("state=closed", argv)
        self.assertIn("-i", argv)
        self.assertNotIn("--paginate", argv)
        self.assertIn("per_page=100", argv)
        self.assertIn("headRefName: .head.ref", argv)
        # The terminal-PR rule reads BOTH fields, so the projection must carry
        # both: REST reports `state: "closed"` for merged and unmerged PRs
        # alike, and `mergedAt` is the only field that names a merge.
        self.assertIn("mergedAt: .merged_at", argv)
        # ⛔ AND THE HEAD SHA. #5186's squash-merge predicate compares a branch
        # tip against a merged PR's head sha. The projection is the ONLY place
        # that re-keys the REST payload, so dropping `.head.sha` from it makes
        # the predicate silently inert — every other test stays green, because
        # the stub never executes `--jq` and feeds its fixture straight to the
        # scanner. It happened once; this assertion is what makes it loud.
        self.assertIn("headSha: (.head.sha", argv)

    # ── target repo: never certify a scope you did not establish (#4027) ────

    def test_output_always_names_resolved_repo_and_full_title(self):
        # The cheap high-value half of #4027: a verdict that does not name what
        # it measured cannot be trusted, so the repo AND the full title appear
        # on every path, including the verdict line itself.
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("repo: test-owner/test-repo", out)
        self.assertIn("title: florfenicol dosing audit", out)
        self.assertIn("for #3061 in test-owner/test-repo", out)

        # The second half used to prove the header survives a `--keywords`
        # override. That flag is gone; the equivalent repeatable input is
        # `--self-branch`, and the property under test (the header names the
        # repo and the FULL title on every path) is unchanged.
        rc, out = self.run_tool(self_branches=("fix/3061-extra",))
        self.assertIn("repo: test-owner/test-repo", out)
        self.assertIn("title: florfenicol dosing audit", out)

    def test_repo_scoped_gh_calls_carry_the_resolved_slug(self):
        # The exact mechanism of the cross-repo false CLEAN: gh resolving the
        # repo from the CWD instead of the intended target. Every
        # REPOSITORY-SCOPED gh surface must carry the explicit selector; the
        # REST path must be literal (gh api has no --repo flag, so
        # `{owner}/{repo}` placeholders would again resolve from the cwd).
        # The two deliberate non-repo-scoped calls are `gh repo view` (which
        # DISCOVERS the slug, so it cannot carry it) and `gh api user` (the
        # lane's account identity, not a repository); the claim in AGENTS.md is
        # scoped to repository-scoped calls precisely because of them.
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("test-owner/test-repo",
                      (self.gh_dir / "issue-argv.txt").read_text())
        self.assertIn("test-owner/test-repo",
                      (self.gh_dir / "pr-list-argv.txt").read_text())
        api = (self.gh_dir / "api-argv.txt").read_text()
        self.assertIn("repos/test-owner/test-repo/pulls", api)
        self.assertNotIn("{owner}", api)
        self.assertNotIn("{repo}", api)

    def test_absent_issue_fails_closed_exit_2_not_clean(self):
        # "Not found here" is NOT "no in-flight work". Before #4027 an absent
        # issue was indistinguishable from CLEAN.
        rc, out = self.run_tool(env_extra={"GH_STUB_ISSUE_ABSENT": "1"})
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        self.assertIn("issue-absent", out)
        self.assertIn("does not exist in test-owner/test-repo", out)

    def _sibling_repo(self, name: str, slug: str) -> Path:
        other = self.tmp / name
        other.mkdir()
        _git(other, "init", "-q", "-b", "main", "--template=")
        _git(other, "config", "user.email", "test@example.com")
        _git(other, "config", "user.name", "Test")
        _git(other, "remote", "add", "origin", f"https://github.com/{slug}.git")
        (other / "seed.txt").write_text("seed\n")
        _git(other, "add", "seed.txt")
        _git(other, "commit", "-qm", "seed")
        return other

    def test_owner_name_selector_targets_that_repo_and_its_local_clone(self):
        other = self._sibling_repo("other-repo", "other-owner/other-repo")
        # The claim gate's board is SINGLE-REPO evidence, so the fixture must
        # carry a board for the repo being queried — otherwise the gate rebuilds
        # for a slug this harness has no fleet state for (and blocks on the
        # unmeasurable read).
        self.fleet_board(repo="other-owner/other-repo")
        rc, out = self.run_tool(repo_arg="other-owner/other-repo", cwd=self.repo)
        self.assertEqual(rc, 0, out)
        self.assertIn("repo: other-owner/other-repo", out)
        # The git surfaces describe the TARGET repo, never the cwd's repo.
        # (tmpdir paths are symlinked on macOS; compare real paths.)
        self.assertIn(f"local checkout: {os.path.realpath(other)}", out)
        self.assertIn("VERDICT: CLEAN", out)

    def test_selector_without_a_local_clone_leaves_git_surfaces_incomplete(self):
        # The board must be stamped for the SAME slug the tool is queried with,
        # or the claim gate rebuilds (a real `gh`/`cmux` sample) instead of
        # answering — the fixture is what keeps this hermetic.
        self.fleet_board(repo="other-owner/no-such-clone")
        rc, out = self.run_tool(repo_arg="other-owner/no-such-clone", cwd=self.repo)
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        self.assertIn("no-local-clone", out)

    def test_repo_selector_is_case_insensitive_for_the_clone(self):
        # GitHub and `gh --repo` accept `Owner/Name`, so a case-different
        # selector must still find the local clone rather than leaving every
        # git surface INCOMPLETE (a false exit 2 that blocks dispatch).
        other = self._sibling_repo("other-repo", "other-owner/other-repo")
        # A case-different selector is the SAME repo: `_board_is_for_repo` folds
        # case, so a lower-case board still answers here (a needless rebuild for
        # `Tortoise` vs `tortoise` would be a 26 s cost per pre-flight).
        self.fleet_board(repo="other-owner/other-repo")
        rc, out = self.run_tool(repo_arg="OTHER-OWNER/other-repo", cwd=self.repo)
        self.assertEqual(rc, 0, out)
        self.assertIn("repo: OTHER-OWNER/other-repo", out)
        self.assertIn(f"local checkout: {os.path.realpath(other)}", out)
        self.assertIn("VERDICT: CLEAN", out)

    def test_empty_repo_argument_is_usage_error(self):
        # Path("") IS a directory, so an empty --repo used to take the PATH
        # branch and run the git surfaces against the cwd while the report
        # printed "local checkout: (none — git surfaces INCOMPLETE)". An unset
        # shell variable must be a usage error, never the cwd.
        for bad in ("", "   "):
            with self.subTest(bad=bad):
                rc, out = self.run_tool(repo_arg=bad)
                self.assertEqual(rc, 3, out)
                self.assertNotIn("VERDICT: CLEAN", out)
                self.assertIn("empty/whitespace", out)

    def test_omitted_repo_refuses_when_number_resolves_in_two_repos(self):
        self._sibling_repo("other-repo", "other-owner/other-repo")
        (self.gh_dir / "probe_holds.txt").write_text(
            "test-owner/test-repo\nother-owner/other-repo\n")
        rc, out = self.run_tool(repo_arg=None, cwd=self.repo)
        self.assertEqual(rc, 2, out)
        self.assertIn("AMBIGUOUS", out)
        self.assertIn("refusing to guess", out)
        self.assertNotIn("VERDICT: CLEAN", out)

    def test_omitted_repo_refuses_when_number_resolves_elsewhere_only(self):
        self._sibling_repo("other-repo", "other-owner/other-repo")
        (self.gh_dir / "probe_holds.txt").write_text("other-owner/other-repo\n")
        rc, out = self.run_tool(repo_arg=None, cwd=self.repo)
        self.assertEqual(rc, 2, out)
        self.assertIn("does NOT exist in test-owner/test-repo", out)
        self.assertIn("re-run with --repo other-owner/other-repo", out)
        self.assertNotIn("VERDICT: CLEAN", out)

    def test_omitted_repo_refuses_when_a_candidate_cannot_be_probed(self):
        self._sibling_repo("other-repo", "other-owner/other-repo")
        (self.gh_dir / "probe_unqueried.txt").write_text("other-owner/other-repo\n")
        rc, out = self.run_tool(repo_arg=None, cwd=self.repo)
        self.assertEqual(rc, 2, out)
        self.assertIn("could not be probed", out)
        self.assertNotIn("VERDICT: CLEAN", out)

    # ── terminal-injection hardening (#4027 P2-J) ──────────────────────────

    def test_control_sequences_in_untrusted_fields_are_stripped(self):
        # The report IS the artifact a human reads to decide "do NOT dispatch".
        # ESC/CSI/OSC from a GitHub title, login or PR field can blank or
        # overwrite the VERDICT line (and OSC 52 rewrites the clipboard) — a
        # fail-open class. No control character may reach stdout.
        #
        # ⛔ COMMENT BODIES ARE NOT ONE OF THESE FIELDS ANY MORE (#8175): the
        # prose reader is deleted and `comments` is not fetched, so the hostile
        # fields on this surface are the title and the assignee login.
        evil = "normal\x1b[2K\x1b[1A title"
        self.gh_fixtures(
            issue=self.issue_payload(
                title=evil, assignees=("other\x1b[2K-agent",)),
            open_prs=[{
                "number": 1, "title": "evil\x1b[2K pr", "body": "",
                "headRefName": "fix/evil",
            }],
        )
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertNotIn("\x1b", out)
        self.assertNotIn("\x07", out)
        self.assertIn("VERDICT: COLLISION", out)
        # `_sanitize` removes the ESC/BEL bytes; the printable residue of the
        # sequence (`[2K`) may remain, which is why this asserts on the control
        # bytes above and on the hit's own text here.
        self.assertIn("assignee:other", out)
        self.assertIn("[issue assignee/holder]", out)

    def test_control_sequences_in_issue_state_are_stripped(self):
        # `state` is the one GitHub-sourced field that reached a note RAW
        # (`f"issue state={state} …"`), and `format_report` prints notes
        # verbatim — so `_sanitize` never saw it. The same fail-open class as
        # the title/comment fields: an ESC can blank the VERDICT line and OSC
        # 52 can rewrite the clipboard. Sanitised at CONSTRUCTION, so every
        # consumer of `surface.note` sees a clean string.
        evil = "CLOSED\x1b[2K\x1b]52;c;AAAA\x07"
        self.gh_fixtures(issue=self.issue_payload(state=evil))
        rc, out = self.run_tool()
        # #8560: the sanitised state reads as CLOSED, which is now DECISIVE — the
        # terminal answer is carried by the `issue landed` surface and the
        # verdict is LANDED (exit 4), not CLEAN. The sanitization contract is
        # unchanged: no control byte reaches stdout.
        self.assertEqual(rc, 4, out)
        self.assertNotIn("\x1b", out)
        self.assertNotIn("\x07", out)
        self.assertIn("issue state=CLOSED", out)
        self.assertIn("VERDICT: LANDED", out)

    def test_invalid_remote_slug_is_rejected_not_printed(self):
        # A git-remote-derived slug is untrusted AND printed. A slug that is not
        # exactly owner/name is rejected, leaving the surfaces INCOMPLETE rather
        # than spraying control bytes into the report.
        evil = self.tmp / "evil-repo"
        evil.mkdir()
        _git(evil, "init", "-q", "-b", "main", "--template=")
        _git(evil, "config", "user.email", "test@example.com")
        _git(evil, "config", "user.name", "Test")
        _git(evil, "remote", "add", "origin",
             "https://github.com/evil\x1b[2K/wat.git")
        rc, out = self.run_tool(repo_arg=str(evil), cwd=self.repo)
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        self.assertNotIn("\x1b", out)

    # ── partial-run prevention ──────────────────────────────────────────────

    def test_every_surface_is_always_evaluated(self):
        _rc, out = self.run_tool()
        self.assert_all_surface_rows(out)
        self.assertIn("8/8 surfaces queried", out)

    def test_the_exit_2_header_gives_each_roster_cause_its_own_remedy(self):
        # #9657, raised by the fresh-context review of PR #9571 @ 5a22ed54e. The
        # exit-2 contract is the spec a lane reads after `exit 2` (the header's
        # own test above says so), and it answered the PRESENT-BUT-UNREADABLE
        # roster with "rebuild the board" — the exact sentence the same PR
        # removed for the ABSENT roster, because `fleet_state.py build` builds
        # the board FROM that file and fails on the same read. It also called
        # the cause "unrebuildable" while prescribing a rebuild in the same
        # breath. Nothing pinned the block, which is why half of it survived
        # the fix; this is the pin.
        cp = _tool_module()
        doc = cp.__doc__ or ""
        # Flatten the WRAPPED prose so the assertions bind to sentences, not to
        # where the 80-column wrap happens to fall.
        flat = " ".join(doc.split())
        self.assertNotIn(
            "unrebuildable",
            flat,
            "the header must not call a cause unrebuildable while prescribing a rebuild for it",
        )
        absent = flat.find("LANE ROSTER is ABSENT")
        # ⛔ ACCEPT EITHER PHRASING FOR ARM 2 (review, cycle 3, re-raised as a
        # P3 at cycle 4). The header's cause clause and the sentence the tool
        # actually PRINTS (`fleet_state.registry_remedy`) differ in wording —
        # "the roster EXISTS but could not be read" — and so does the REMEDY
        # ("fix the file itself"). A future lane aligning the header to the
        # shipped message is a consistency edit, not a regression, so both
        # halves accept either spelling.
        present = max(
            flat.find("roster is PRESENT but unreadable"),
            flat.find("roster EXISTS but could not be read"),
        )
        # ⛔ FOUR ARMS, NOT THREE (review, cycle 4 P2). `registry_remedy` shipped
        # three roster arms — PROVISION / fix the file / NO CAUSE RECORDED — and
        # `test_registry_remedy_has_three_arms_and_never_lists_the_rebuild`
        # pins all three. A header that names only two sends the no-cause reader
        # to the nearest clause, which is the board rebuild that fails on the
        # same read: the same dead end #9392 files, one arm narrower.
        # Every anchor is widened the same way arm 2's is (review, cycle 5):
        # `registry_remedy` PRINTS its own wording for this arm too ("the cause
        # of the failed roster read was not recorded"), so a lane aligning the
        # header to the shipped sentence must not redden the pin.
        nocause = max(
            flat.find("recorded NO CAUSE"),
            flat.find("the cause of the failed roster read was not recorded"),
        )
        board = flat.find("the board is unreadable")
        self.assertNotEqual(absent, -1, "the ABSENT-roster arm must stay stated")
        self.assertNotEqual(present, -1, "the unreadable-roster arm needs its own clause")
        self.assertNotEqual(nocause, -1, "the no-cause-recorded arm needs its own clause")
        self.assertNotEqual(board, -1, "the ambiguous-attribution arm must stay stated")
        self.assertLess(absent, present)
        self.assertLess(present, nocause)
        self.assertLess(nocause, board)
        # ⛔ ONE TABLE FOR ALL FOUR ARMS (review, cycles 3-7). Every cycle found
        # the same class of defect "one arm over" — a remedy asserted in one arm
        # and not the next, a phrase forbidden in one and not the next, a
        # prohibition that was case-sensitive while its siblings were not. Per-arm
        # ad-hoc asserts kept leaving a leg out, so the checks are structural
        # instead: EVERY arm must state its own instruction, and every arm EXCEPT
        # the board arm must refuse both forms of the dead end #9392 files —
        # `fleet_state.py build` builds the board FROM the roster and fails on the
        # same read, so prescribing it is never an answer to a roster cause.
        # Each requirement is a tuple of accepted SPELLINGS: `registry_remedy`
        # prints its own wording for these arms, and a lane aligning the header to
        # it is a consistency edit, not a regression. The bare
        # `fleet_state.py build` stays allowed — it appears inside the arms' own
        # negations, so only the `tools/`-prefixed (runnable) form is a dead end.
        arms = {
            "ABSENT": (flat[absent:present], [("PROVISION",)]),
            "unreadable": (
                flat[present:nocause],
                [("FIX THE FILE", "fix the file itself")],
            ),
            "no-cause": (
                flat[nocause:board],
                [
                    ("start from the FILE", "start from the file"),
                    ("cannot create or repair", "cannot repair or create"),
                ],
            ),
            "board": (
                flat[board:],
                [("rebuild the board",), ("tools/fleet_state.py build",)],
            ),
        }
        # ⛔ THE DEAD END IS BOTH PHRASINGS AND THE COMMAND (review, cycle 8).
        # #9392's sentence is `_incomplete_remedy`'s "fix gh auth/network (or
        # rebuild the fleet board) and re-run", and the sibling pin above forbids
        # it by THAT wording — a list carrying only "rebuild the board" would let
        # a lane reintroduce the archived sentence into a roster arm and stay
        # green. The runnable `tools/fleet_state.py build` is the form a lane is
        # most likely to name explicitly, so it is a dead end too.
        dead_ends = (
            "rebuild the fleet board",
            "rebuild the board",
            "tools/fleet_state.py build",
        )
        # ⛔ PRESCRIPTIVE, NOT MENTIONED — and GOVERNED, not merely present
        # (review, cycles 8-9). A bare substring test forbids NAMING the dead end,
        # so the strongest warning a header author has — "never run `tools/
        # fleet_state.py build`", "do not rebuild the board" — is the one that
        # fails. A negation anywhere in the SENTENCE is not enough either: these
        # arms are one long sentence each and already contain "a rebuild cannot
        # help", so #9392's exact sentence could ride in as a later clause. The
        # negation must GOVERN the occurrence, i.e. sit immediately before it.
        # `instead` is deliberately NOT a negation token — it marks an ALTERNATIVE
        # offer ("rebuild the board instead"), which is the prescription itself.
        # The NEGATION TOKENS cover the contractions a lane actually writes, and
        # the filler class excludes the punctuation that starts a NEW object
        # (`:`, `;`, `.`, `,` and the em/en dash this header uses), so "the fix is
        # not gh auth — rebuild the board" cannot borrow its negation. A negation
        # may also FOLLOW the dead end (`tools/fleet_state.py build cannot help`),
        # which is the construction `registry_remedy` itself ships.
        neg = r"(?:not|never|cannot|can'?t|do(?:es)?n'?t|won'?t)"
        governs = re.compile(rf"(?:\b{neg}\s+[^,.;:—–]{{0,30}}|\binstead of\s+[^,.;:—–]{{0,20}})$")
        # The POST-position window is deliberately looser: it allows a dash,
        # because "rebuild the board — it is not the answer" is a natural
        # negation, and the narrow escape it leaves ("rebuild the board — gh auth
        # is not the fix") still names the phrase inside an arm whose whole job is
        # to say what to do. A pin that reddens the natural phrasing is the
        # over-pin this file's own siblings avoid; the PRESCRIPTIVE forms it
        # exists to catch are all covered below.
        follows = re.compile(rf"^\s*[^,.;:]{{0,30}}\b{neg}\b")
        for arm, (region, requirements) in arms.items():
            low = region.lower()
            for accepted in requirements:
                self.assertTrue(
                    any(k.lower() in low for k in accepted),
                    f"the {arm} arm's remedy must state {' or '.join(accepted)}",
                )
            if arm == "board":
                continue
            for dead_end in dead_ends:
                for hit in re.finditer(re.escape(dead_end), low):
                    window = low[max(0, hit.start() - 45) : hit.start()]
                    after = low[hit.end() : hit.end() + 34]
                    self.assertTrue(
                        governs.search(window) or follows.search(after),
                        f"the {arm} arm offers {dead_end!r} un-negated "
                        f"({low[max(0, hit.start() - 45) : hit.end() + 34]!r}): that "
                        "rebuild fails on the same read it is prescribed for",
                    )

    def test_the_header_contract_matches_the_surface_set(self):
        # #8629 review (P2, claim mismatch). This tool states its contract in the
        # module header, and that header is what the next lane reads before
        # changing a surface — #8560's own verification checklist is exactly
        # "the new verdict is in the docstring table and the surface set matches
        # `ALL_SURFACES`". #8625 added `SURFACE_UNCLOSED_REF` and updated only
        # the inline `ALL_SURFACES` comment, so the file disagreed with itself
        # five lines apart about how many surfaces it has, and the new ADVISORY
        # row was invisible to every reader of the header.
        cp = _tool_module()
        doc = cp.__doc__ or ""
        declared = re.search(r"Surfaces \((\d+) rows", doc)
        self.assertIsNotNone(declared, "the header must declare the row count")
        self.assertEqual(
            int(declared.group(1)), len(cp.ALL_SURFACES),
            "the header's `Surfaces (N rows)` must equal len(ALL_SURFACES)")
        # ⛔ #8629 review (F4). The per-row assertion must bind to the TABLE
        # REGION, not the whole docstring. `assertIn(name, doc)` was satisfied by
        # any prose mention: the wrapped row label `merged PRs naming an OPEN
        # issue` occurs exactly ONCE in the docstring and that occurrence is the
        # FOLLOW-ON paragraph (`The \`issue keywords\` row was removed …`), so
        # renaming or deleting the row itself left the test green — the row this
        # test exists to pin was never actually checked.
        #
        # ⛔ The table cannot be checked by flattening the WHOLE block. It is
        # COLUMN-MAJOR once newlines are removed: a surface name that wraps shares
        # its physical lines with the other columns, so `" ".join(block.split())`
        # interleaves them (`merged PRs naming an no query of its own — it (MERGED
        # PR whose OPEN issue …`) and the wrapped name is NOT recoverable that way.
        # Extract the FIRST COLUMN instead — the column that holds every surface
        # name AND the ADVISORY marker — so a wrapped name is rejoined (wrapping-
        # tolerant) while a renamed/deleted row still fails.
        table_lines = doc[declared.start():].splitlines()
        table_end = next(
            (i for i, ln in enumerate(table_lines) if i > 1 and not ln.strip()),
            len(table_lines),
        )
        # Drop the `Surfaces (N rows …)` header and the rule beneath it: both are
        # single-cell lines that contribute nothing to the first column.
        row_lines = table_lines[2:table_end]
        boundaries = [
            2 + m.end()
            for ln in row_lines
            if (m := re.search(r" {2,}(?=\S)", ln[2:])) is not None
        ]
        self.assertTrue(
            boundaries,
            "the header table must have parseable column boundaries")
        col = min(boundaries)  # the SECOND column starts here on every row
        first_column = " ".join(
            " ".join(ln[2:col].split()) for ln in row_lines
        ).strip()
        # Locate each name IN ORDER (the table is written in `ALL_SURFACES`
        # order), so a name can only match its own row — an earlier row's prose
        # cannot stand in for a missing later row.
        starts: list[tuple[str, int]] = []
        cursor = 0
        for name in cp.ALL_SURFACES:
            idx = first_column.find(name, cursor)
            self.assertNotEqual(
                idx, -1,
                f"surface {name!r} is missing from the header table — a row "
                "that ships without a contract entry is a contract the next "
                "reader cannot see")
            starts.append((name, idx))
            cursor = idx + len(name)
        # The ADVISORY marker is the only place a reader learns that a hit on a
        # row cannot block, so it must sit in THAT surface's row — a docstring
        # `count(...) == len(ADVISORY_SURFACES)` cannot enforce that.
        for i, (name, idx) in enumerate(starts):
            row = (first_column[idx:starts[i + 1][1]]
                   if i + 1 < len(starts) else first_column[idx:])
            if name in cp.ADVISORY_SURFACES:
                self.assertIn(
                    "· ADVISORY ·", row,
                    f"advisory surface {name!r} has no `· ADVISORY ·` marker in its row")
            else:
                self.assertNotIn(
                    "· ADVISORY ·", row,
                    f"non-advisory surface {name!r} is marked `· ADVISORY ·` in its row")
        self.assertEqual(
            first_column.count("· ADVISORY ·"), len(cp.ADVISORY_SURFACES),
            "the table must carry exactly one `· ADVISORY ·` marker per "
            "advisory surface")
        # The exit-code table may not advertise an arm that cannot reach it:
        # after #8625 the timeline title arm is an ADVISORY, so only the issue's
        # own state may be named as a source of exit 4 ("a verdict must not
        # state a cause it did not observe").
        self.assertNotIn("or a MERGED PR the paginated", doc)

    def test_the_retired_shipped_kind_is_gone_not_left_as_a_dead_tier(self):
        # #8629 review (P2). #8625 moved the only producer of the `shipped` hit
        # kind to the ADVISORY surface (which emits `unclosed`), so the kind and
        # its branch in `_landed_wording` became unreachable — a described tier
        # that no code path can produce. The file's own standard at the hit-tag
        # site refuses exactly this ("a tag entry for it would be dead code that
        # reads as a live tier").
        import inspect
        cp = _tool_module()
        # The documented vocabulary is the kind entries of `Hit.kind`'s comment
        # (`#   "<kind>"   — <meaning>`), so prose that MENTIONS a retired kind
        # cannot satisfy or defeat this.
        documented = set(re.findall(
            r'^\s*#\s+"([a-z-]+)"\s+—', inspect.getsource(cp.Hit), re.M))
        self.assertEqual(
            documented, {"completed", "not-planned", "closed", "unclosed",
                        "prose-body"},
            "`Hit.kind`'s documented vocabulary must be exactly the kinds this "
            "module can produce (no `shipped`: #8625 left it unreachable)")
        self.assertNotIn(
            '"shipped"', inspect.getsource(cp._landed_wording),
            "`_landed_wording` must not branch on the retired `shipped` kind")

    # ── #8560: the LANDED answer (authoritative, paginated) ──────────────────

    def test_closed_issue_is_landed_never_clean(self):
        # A CLOSED issue is TERMINAL. The tool used to print it
        # "(closed issues are warn-only, not a hit)" and return CLEAN — the
        # exact false-CLEAN that dispatched a lane onto shipped code. It must be
        # refused, with its OWN verdict (not COLLISION, whose remedy differs).
        self.gh_fixtures(issue=self.issue_payload(state="CLOSED",
                                                  state_reason="COMPLETED"))
        rc, out = self.run_tool()
        self.assertEqual(rc, 4, out)
        self.assertIn("VERDICT: LANDED (exit 4)", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        self.assertNotIn("VERDICT: COLLISION", out)
        self.assertIn("[issue landed]", out)
        self.assertIn("issue state=CLOSED", out)
        # A COMPLETED closure IS a shipment, and the wording may say so.
        self.assertIn("closed as COMPLETED", out)
        self.assertIn("ALREADY SHIPPED", out)

    def test_closed_as_not_planned_is_not_reported_as_shipped(self):
        # ⛔ THE REASON, NOT JUST THE DIRECTION (#8560 review). `NOT_PLANNED`
        # means the issue was DECLINED or SUPERSEDED — its work did not ship.
        # The refusal is identical (LANDED, exit 4, do NOT dispatch), but
        # "the work appears ALREADY SHIPPED" invents a merge that does not exist
        # (measured live on #8497, `state_reason=not_planned`). The tool must
        # name the reason that actually caused the refusal.
        self.gh_fixtures(issue=self.issue_payload(state="CLOSED",
                                                  state_reason="NOT_PLANNED"))
        rc, out = self.run_tool()
        self.assertEqual(rc, 4, out)
        self.assertIn("VERDICT: LANDED (exit 4)", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        self.assertIn("closed as NOT PLANNED", out)
        self.assertIn("NOT PLANNED", out)
        # The VERDICT's own conclusion, not only the hit detail: the mutation
        # that forces `_landed_conclusion` to "ALREADY SHIPPED" must fail here.
        self.assertIn("was CLOSED AS NOT PLANNED", out)
        self.assertNotIn("ALREADY SHIPPED", out)

    def test_closed_with_an_unknown_reason_makes_no_shipment_claim(self):
        # The refusal direction never depends on the reason, and an unavailable
        # or unrecognised `stateReason` must not be read as a shipment either.
        # The wording stays neutral rather than inventing a cause.
        self.gh_fixtures(issue=self.issue_payload(state="CLOSED"))
        rc, out = self.run_tool()
        self.assertEqual(rc, 4, out)
        self.assertIn("VERDICT: LANDED (exit 4)", out)
        self.assertIn("the work item is TERMINAL (closed)", out)
        self.assertNotIn("ALREADY SHIPPED", out)
        self.assertNotIn("NOT PLANNED", out)

    def test_7174_merged_pr_on_an_open_issue_is_an_advisory_not_a_landing(self):
        # THE REAL HISTORICAL INSTANCE, with #8625's rule applied to it. #7174
        # stayed OPEN for eight days after its fix merged as PR #7403
        # (2026-10-05). #7403 *referenced* #7174 without a closing keyword, and
        # it sat outside the bounded 100-PR ADVISORY closed-PR sample — so the
        # old tool returned CLEAN and a lane re-implemented landed code.
        #
        # The targeted, PAGINATED timeline must still FIND it — that is #8560's
        # fix and it is preserved. What changed is its WEIGHT: a reference is
        # not a closure (GitHub closes an issue only on a closing keyword), so a
        # merged title-naming PR on an OPEN issue is an ADVISORY, reported with
        # a remedy, and never LANDED. Reading it as terminal re-created #4886 on
        # a new surface — measured, it refused dispatch on #7951 for the
        # remainder of that lane on the strength of three PRs (#8127, #8315,
        # #8389) that each said in their own body that the issue stays open.
        #
        # (The title is the real #7403 title; the closed-PR sample is EMPTY, so
        # the finding is produced by the timeline and by nothing else.)
        self.gh_fixtures(
            issue=self.issue_payload(
                title="arch(durability): the #4647 numeric guard is a "
                      "maintained call-site list",
                state="OPEN"),
            closed_prs=[],
        )
        self.timeline_fixture(events=[self.merged_cross_reference(
            7403,
            "refactor(durability): the numeric guard is a param-boundary seam, "
            "not a call-site list (#7174)",
        )])
        rc, out = self.run_tool(issue=7174)
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertNotIn("VERDICT: LANDED", out)
        self.assertIn("[merged PRs naming an OPEN issue]", out)
        self.assertIn("PR #7403", out)
        self.assertIn("names this issue in its TITLE", out)
        self.assertIn("NOT a landing", out)
        self.assertIn("advisory — cannot block", out)

    def test_landed_reference_on_a_later_timeline_page_is_reached(self):
        # "PAGINATED to exhaustion" must be a property of the TOOL, not only of
        # gh. `--slurp` hands back an array of pages, and the scan must read
        # EVERY page: a page-0-only loop would read a merged PR on a later page
        # as absent — the bounded-sample defect returning one level down, which
        # is exactly what this change exists to close. Issue #3061's own timeline
        # is one page, so only a multi-page fixture can pin this.
        self.gh_fixtures(issue=self.issue_payload(state="OPEN"))
        self.timeline_fixture(pages=[
            [{"event": "labeled"}],
            [{"event": "commented"}],
            [self.merged_cross_reference(7403, f"fix: shipped (#{ISSUE})")],
        ])
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertIn("[merged PRs naming an OPEN issue]", out)
        self.assertIn("PR #7403", out)

    def test_landed_does_not_depend_on_the_advisory_closed_sample(self):
        # "Do not add a second bounded sample and call it fixed": the OPEN-issue
        # advisory must still be PRODUCED even when the ADVISORY closed-PR
        # request FAILS. The timeline is the source; the sample is not consulted
        # for this decision.
        self.gh_fixtures(issue=self.issue_payload(state="OPEN"))
        self.timeline_fixture(events=[self.merged_cross_reference(
            999, f"fix: the thing (#{ISSUE})")])
        rc, out = self.run_tool(
            env_extra={"GH_STUB_API_FAIL_AFTER_OUTPUT": "1"})
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertIn("[merged PRs naming an OPEN issue]", out)
        self.assertIn("PR #999", out)
        # ⛔ THE PREMISE, not just the verdict (#8560 review): without these two
        # the test still passes when the failure seam is neutered, so it would
        # not prove the landed answer is independent of the advisory sample.
        self.assertIn("gh-unavailable", out)
        self.assertIn("ADVISORY SURFACES", out)

    def test_landed_verdict_names_the_issue_state_as_the_only_authority(self):
        # #8629 review (P2). The LANDED preamble is printed AT THE POINT OF
        # REFUSAL to the agent that must act on it, and it used to advertise the
        # merged-PR timeline arm as an AUTHORITATIVE source of the verdict — an
        # arm #8625 demoted to an ADVISORY, so on a LANDED verdict (reachable
        # only from a CLOSED issue) the sentence named a cause it cannot observe
        # (the #3504 class-2 standard this file's own comments were written to
        # remove). The replacement must name the issue's own state as the sole
        # authority AND point at where a title-naming PR is reported instead.
        self.gh_fixtures(issue=self.issue_payload(state="CLOSED",
                                                  state_reason="COMPLETED"))
        rc, out = self.run_tool()
        self.assertEqual(rc, 4, out)
        self.assertIn("The authority here is the issue's OWN STATE", out)
        self.assertNotIn("or a MERGED PR the PAGINATED", out)
        self.assertIn(
            "reported on the ADVISORY `merged PRs naming an OPEN issue` surface",
            out)

    def test_merged_pr_that_only_body_references_the_issue_is_clean(self):
        # A merged PR that mentions #N only in its BODY is not a landing: the
        # tool reads the TITLE only. The body here really does name the issue
        # (the real REST payload carries `source.issue.body`), so CLEAN depends
        # on the implementation ignoring it — without the body this test would
        # only prove "a title naming ANOTHER issue is not a landing", and a
        # regression that started scanning bodies would stay green.
        self.gh_fixtures(issue=self.issue_payload(state="OPEN"))
        event = self.merged_cross_reference(
            4242, "fix(projection): degrade corrupt journal values (#7369)")
        event["source"]["issue"]["body"] = f"refs #{ISSUE} only in prose"
        self.timeline_fixture(events=[event])
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertIn("1 merged cross-reference(s), none naming", out)

    def test_unmerged_cross_reference_is_not_a_landing(self):
        # An OPEN (unmerged) PR cross-referencing the issue is in-flight work,
        # judged by the open-PR / branch surfaces — not a landing.
        self.gh_fixtures(issue=self.issue_payload(state="OPEN"))
        event = self.merged_cross_reference(4242, f"fix: the thing (#{ISSUE})")
        event["source"]["issue"]["pull_request"]["merged_at"] = None
        self.timeline_fixture(events=[event])
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        # ⛔ THE NOTE, not just the verdict (#8560 review): `rc == 0` alone is
        # what the PRE-FIX tool returns for an OPEN issue (it never fetched the
        # timeline), so without this the test cannot tell "the unmerged event was
        # read and correctly rejected" from "the timeline was never consulted".
        # The event COUNT pins the read; `0 merged cross-reference(s)` pins that
        # the unmerged event was not counted as a landing.
        self.assertIn("1 timeline event(s)", out)
        self.assertIn("0 merged cross-reference(s), none naming", out)

    def test_landed_query_failure_is_incomplete_never_clean(self):
        # A MISSING CHECK IS NEVER A GREEN CHECK. The landed surface is
        # BLOCKING, so an unqueryable timeline is INCOMPLETE (exit 2), never
        # CLEAN — the guarantee the header pins for every blocking surface.
        self.gh_fixtures(issue=self.issue_payload(state="OPEN"))
        rc, out = self.run_tool(env_extra={"GH_STUB_TIMELINE_FAIL": "1"})
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        self.assertIn("issue landed", out)
        # The REASON, not just the status: with `events is None` the scan would
        # emit the generic `issue-timeline-unmeasured` note and still return
        # INCOMPLETE, so without this the test cannot tell "the query FAILED"
        # from "the query never ran".
        self.assertIn("gh-unavailable", out)
        # ⛔ #8629 review (F2): the ADVISORY `merged PRs naming an OPEN issue`
        # surface is fed by the SAME timeline read, so on this path it was never
        # measured — and it used to print as a clean `0 hit(s)` with no note: a
        # printed ABSENCE for a read that never happened (the defect class the
        # `recently-closed PRs` row is already guarded against at the report
        # site). The verdict is unchanged (exit 2 either way — an ADVISORY
        # surface's own status never enters the incomplete set), but the row must say
        # UNQUERIED, not `0`.
        self.assertIn(
            "[merged PRs naming an OPEN issue] 0 hit(s) — gh-unavailable", out)
        self.assertNotIn("[merged PRs naming an OPEN issue] 0 hit(s)\n", out)

    def test_landed_outranks_an_incomplete_blocking_surface(self):
        # A terminal signal is decisive even when ANOTHER blocking surface could
        # not be read: the verdict is LANDED (the fact is established) and the
        # report says the run is also incomplete. Without this the precedence
        # the module claims is asserted only in prose.
        self.gh_fixtures(issue=self.issue_payload(state="CLOSED"))
        # A malformed open-PR payload makes the open-PR (BLOCKING) surface
        # INCOMPLETE while the issue state still answers the landed question.
        (self.gh_dir / "open_prs.json").write_text("{not json")
        rc, out = self.run_tool()
        self.assertEqual(rc, 4, out)
        self.assertIn("VERDICT: LANDED", out)
        self.assertIn("ALSO INCOMPLETE", out)
        self.assertNotIn("VERDICT: INCOMPLETE", out)

    def test_malformed_timeline_page_is_incomplete_not_clean(self):
        # A page the tool cannot read must never be scavenged into "no landing
        # PR": the fail-OPEN direction this surface exists to close.
        self.gh_fixtures(issue=self.issue_payload(state="OPEN"))
        self.timeline_fixture(pages=[[None]])
        rc, out = self.run_tool()
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: CLEAN", out)

    def test_empty_slurp_body_is_incomplete_not_clean(self):
        # A bare `[]` is not a valid `--paginate --slurp` body — an empty
        # result is `[[]]`, ONE empty page. Read as zero pages it becomes a
        # measured, EMPTY timeline, and the surface reports a COMPLETE
        # measurement ("0 timeline event(s)") on an input it never read: the
        # fail-OPEN direction this surface exists to close. Mirrors
        # `branch_reaper.fetch_prs`'s guard on the identical shape.
        self.gh_fixtures(issue=self.issue_payload(state="OPEN"))
        self.timeline_fixture(pages=[])
        rc, out = self.run_tool()
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: CLEAN", out)

    def test_scalar_timeline_body_is_incomplete_not_clean(self):
        # ⛔ THE `isinstance(pages, list)` GUARD IS NOT REDUNDANT (#8560 review).
        # A scalar body has no page to iterate, so WITHOUT the guard the run dies
        # as `TypeError: 'int' object is not iterable` — exit 3 (internal), not
        # exit 2 — and a scalar is exactly what a truncated/hostile response
        # gives. Pinned here because the guard's removal is otherwise silent.
        self.gh_fixtures(issue=self.issue_payload(state="OPEN"))
        (self.gh_dir / "timeline.json").write_text("5")
        rc, out = self.run_tool()
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        self.assertNotIn("Traceback", out)

    def test_non_json_timeline_body_is_incomplete_not_clean(self):
        # A body that is not JSON is a FAILED read, never "no landing PR".
        self.gh_fixtures(issue=self.issue_payload(state="OPEN"))
        (self.gh_dir / "timeline.json").write_text("{not json")
        rc, out = self.run_tool()
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        self.assertIn("non-JSON", out)

    def test_uninterpretable_issue_state_is_incomplete_not_clean(self):
        # A state that is neither OPEN nor CLOSED leaves the landed question
        # unanswerable — reading it as free work is the fail-OPEN direction.
        # The tool must refuse rather than guess.
        self.gh_fixtures(issue=self.issue_payload(state="MERGED"))
        rc, out = self.run_tool()
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        self.assertIn("issue-state-uninterpretable", out)

    def test_cross_reference_number_equal_to_the_issue_is_not_a_landing(self):
        # The #7009 wrong-object input: an event whose `source.issue.number` IS
        # this issue (not a separate PR). It is refused as a landing rather than
        # read as "the PR that shipped it". The merged-cross-reference COUNT
        # still includes it (it is a merged cross-reference), but nothing NAMES
        # the issue, so the answer is CLEAN and the note proves the event was
        # read — not skipped.
        self.gh_fixtures(issue=self.issue_payload(state="OPEN"))
        self.timeline_fixture(events=[self.merged_cross_reference(
            ISSUE, f"fix: the thing (#{ISSUE})")])
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertIn("1 merged cross-reference(s), none naming", out)

    def test_landed_outranks_a_collision_and_names_both(self):
        # A terminal signal is DECISIVE. If a lane is ALSO on it, the verdict is
        # LANDED (the terminal fact) and the collision is still reported.
        self.gh_fixtures(
            issue=self.issue_payload(state="CLOSED"),
            open_prs=[{
                "number": 9999, "title": f"fix: guard retrieval (#{ISSUE})",
                "body": "", "headRefName": "fix/guard",
            }],
        )
        rc, out = self.run_tool()
        self.assertEqual(rc, 4, out)
        self.assertIn("VERDICT: LANDED", out)
        self.assertNotIn("VERDICT: COLLISION", out)
        self.assertIn("ALSO present", out)

    def test_not_planned_with_a_coexisting_holder_makes_no_shipment_claim(self):
        # The coexisting-holder parenthetical is part of the LANDED verdict, so
        # it must not reintroduce the shipment claim a NOT_PLANNED closure
        # already avoided (#8560 review).
        self.gh_fixtures(
            issue=self.issue_payload(state="CLOSED", state_reason="NOT_PLANNED"),
            open_prs=[{
                "number": 9999, "title": f"fix: guard retrieval (#{ISSUE})",
                "body": "", "headRefName": "fix/guard",
            }],
        )
        rc, out = self.run_tool()
        self.assertEqual(rc, 4, out)
        self.assertIn("ALSO present", out)
        self.assertIn("the work item is TERMINAL AND another lane", out)
        self.assertNotIn("ALREADY SHIPPED", out)
        self.assertNotIn("appears shipped AND", out)

    def test_closed_issue_landed_arm_does_not_need_the_timeline(self):
        # A decisive state answer must not depend on (or pay for) the timeline.
        # GH_STUB_TIMELINE_FAIL proves the timeline was NOT consulted: the
        # CLOSED state lands anyway.
        self.gh_fixtures(issue=self.issue_payload(state="CLOSED"))
        rc, out = self.run_tool(env_extra={"GH_STUB_TIMELINE_FAIL": "1"})
        self.assertEqual(rc, 4, out)
        self.assertIn("VERDICT: LANDED", out)
        # The stub's timeline FAILURE seam was armed and yet was never reached:
        # a CLOSED issue must not pay for (or depend on) the timeline query.
        self.assertFalse((self.gh_dir / "timeline-calls.log").exists())

    def test_timeline_query_is_paginated_slurped_and_targeted(self):
        # The ARGUMENT CONTRACT is what makes this query authoritative (reach a
        # merge older than any sample) and correctly TARGETED (#4027: the
        # resolved slug literally, never gh's `{owner}/{repo}` placeholders,
        # which resolve from the cwd). The stub serves the fixture for ANY
        # `api --paginate`, so only an argv assertion pins it.
        self.gh_fixtures(issue=self.issue_payload(state="OPEN"))
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        argv = (self.gh_dir / "timeline-argv.txt").read_text()
        self.assertIn("--paginate", argv)
        self.assertIn("--slurp", argv)
        self.assertIn(
            f"repos/test-owner/test-repo/issues/{ISSUE}/timeline", argv)
        self.assertNotIn("{owner}", argv)
        self.assertNotIn("{repo}", argv)
        calls = (self.gh_dir / "timeline-calls.log").read_text().splitlines()
        self.assertEqual(len(calls), 1, calls)

    def test_open_issue_with_no_landing_reference_is_clean(self):
        self.gh_fixtures(issue=self.issue_payload(state="OPEN"))
        self.timeline_fixture(events=[])
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertIn("issue state=OPEN; 0 timeline event(s)", out)

    def test_usage_error_on_bad_issue(self):
        proc = subprocess.run(
            [PYTHON, str(TOOL), "0", "--repo", str(self.repo)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)

    def test_usage_error_on_bad_closed_pr_timeout(self):
        proc = subprocess.run(
            [PYTHON, str(TOOL), str(ISSUE), "--repo", str(self.repo),
             "--closed-pr-timeout", "0"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertIn("--closed-pr-timeout must be > 0", proc.stderr)

    def test_usage_error_on_non_finite_closed_pr_timeout(self):
        # `nan` / `inf` defeat a bare positivity check (`nan <= 0` and
        # `inf <= 0` are BOTH False) and then raise inside
        # subprocess.run(timeout=…) — an uncaught ValueError/OverflowError, so
        # the run ends exit 1 (the COLLISION code) with a traceback and no
        # VERDICT at all. They must be EXIT_USAGE.
        # `--closed-pr-timeout -inf` (space-separated) is rejected by argparse
        # itself as an option-like token, so the `=` form is used to reach the
        # validator under test.
        for bad in ("nan", "inf", "-inf", "abc", "60s"):
            with self.subTest(bad=bad):
                rc, out = self.run_tool(extra_args=[f"--closed-pr-timeout={bad}"])
                self.assertEqual(rc, 3, f"{bad}: {out}")
                self.assertIn("--closed-pr-timeout", out)
                self.assertNotIn("Traceback", out)

    def test_usage_error_on_bad_closed_pr_timeout_env(self):
        # The env seam is a documented input too. The old eager
        # `float(os.environ[...])` ran at add_argument time, so a typo'd or
        # empty variable raised an uncaught ValueError -> exit 1 + traceback,
        # i.e. a misconfiguration reported as a phantom COLLISION.
        for bad in ("abc", "nan", "inf", "0", ""):
            with self.subTest(bad=bad):
                rc, out = self.run_tool(
                    env_extra={"COLLISION_PREFLIGHT_CLOSED_PR_TIMEOUT": bad})
                self.assertEqual(rc, 3, f"{bad}: {out}")
                self.assertIn("--closed-pr-timeout", out)
                self.assertNotIn("Traceback", out)

    def test_a_pre_3_12_interpreter_is_usage_not_collision(self):
        # #4053: the module-level version guard is a PRE-QUERY failure — it runs
        # before any surface is touched. `SystemExit(<str>)` exits 1, which is
        # EXIT_COLLISION, so an interpreter mismatch read as "another lane is on
        # this issue": no VERDICT line, and nothing in the output saying the
        # check never ran. That is exactly the class the env-typo case above was
        # fixed for (#3619), left behind on the one path that cannot be reached
        # by importing the module.
        #
        # The guard sits at module scope and deliberately runs before the imports
        # (#5128), so it cannot be exercised by importing. Execute the source
        # under a faked `sys.version_info` in a subprocess instead.
        shim = (
            "import sys\n"
            "sys.version_info = (3, 11, 0, 'final', 0)\n"
            f"exec(compile(open({str(TOOL)!r}).read(), {str(TOOL)!r}, 'exec'))\n"
        )
        proc = subprocess.run(
            [PYTHON, "-c", shim], capture_output=True, text=True, cwd=str(ROOT),
        )
        self.assertEqual(proc.returncode, 3, proc.stderr)
        # 1 is the specific wrong answer: it is indistinguishable from a real
        # collision to anything gating on `must exit 0`.
        self.assertNotEqual(proc.returncode, 1)
        self.assertIn("requires Python >= 3.12", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        # No VERDICT may be printed. No surface was queried, so emitting one
        # would be the "confident verdict about a scope it did not establish"
        # bug this exit-code contract exists to prevent.
        self.assertNotIn("VERDICT", proc.stdout)


    # ── #9806: the STALE HOLDER (a branch whose evidence expired) ───────────
    #
    # THE MEASURED DEFECT. A controller with 24 candidates dispatched NOTHING because
    # every one answered `collision — a peer owns it`: the holders were branches whose
    # names carry the issue number and whose last commit was 8.6 to 54.8 days old, at
    # 457 to 3732 commits behind `origin/main`, with no live process, no uncommitted
    # work and no claim ref. The gate read the branch's NAME as a live claim and never
    # asked again.
    #
    # ⛔ EVERY TEST BELOW IS IN THE DEMOTION DIRECTION OR THE KEEP-BLOCKING DIRECTION,
    # AND THE SECOND HALF IS THE ONE THAT MATTERS. A rule that demotes a far-behind old
    # branch is worthless if it ALSO demotes a branch with a recent commit, a live
    # process standing in its worktree, uncommitted work in that worktree, or a live
    # claim on the issue — so each of those four is proven to still produce COLLISION,
    # one test per direction.
    #
    # The histories are REAL (fast-import writes 350 commits; the tip commit is genuinely
    # dated 12 days ago), because the thresholds being tested are the ones the code ships
    # and a fixture that mocked `judge` would prove nothing about them.

    #: Commits main gains after the abandoned branch is cut. Must exceed
    #: `STALE_BEHIND_MIN` (300) by a margin, so the divergence test is not read as
    #: satisfied-by-accident at the boundary.
    BEHIND = 350
    #: The abandoned branch's tip age. Above `STALE_TIP_DAYS` (7.0) and below the
    #: 54.8 and 12.4 day holders the incident measured.
    STALE_DAYS = 12

    # ── fixtures ────────────────────────────────────────────────────────────

    def bare_origin(self) -> Path:
        """A LOCAL bare origin the tool can really `ls-remote`/`fetch`.

        ⛔ NECESSARY FOR EVERY TEST IN THIS CLASS, not only the claim ones. The
        stale-holder test's fourth condition reads `refs/claims/issue-<N>` from
        `origin`; against the `https://github.com/…` URL `setUp` installs, that read
        fails (no network in the suite), and an unreadable registry is LIVE — so the
        demotion tests would fail for a reason that looks like the rule not working.

        The path deliberately ends `<owner>/<repo>.git`: `_remote_slug` derives the gh
        target from the LAST TWO path components, so `…/test-owner/test-repo.git`
        resolves to the fixture's own `test-owner/test-repo` and the fleet board stays
        answerable (a different slug would refuse the board and rebuild → INCOMPLETE).
        """
        path = self.tmp / "remotes" / "test-owner" / "test-repo.git"
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", "--bare", str(path)], check=True,
                       capture_output=True, text=True)
        _git(self.repo, "remote", "set-url", "origin", str(path))
        return path

    def _fast_forward_main(self, n: int) -> None:
        """Write `n` commits onto `refs/heads/main` in ONE `git fast-import`.

        `behind` is a COUNT of commits, so a fixture for it needs a real history. One
        fast-import (~30 ms) rather than `n` subprocesses keeps the class cheap; the
        commits carry real trees so `rev-list`, `--merged` and `status` all behave
        normally on them.
        """
        tip = self._git_out("rev-parse", "HEAD").strip()
        lines = []
        for i in range(1, n + 1):
            msg = f"main advance {i}".encode()
            body = f"{i}\n".encode()
            lines.append(b"commit refs/heads/main\n")
            lines.append(b"committer Test <test@example.com> 1792789000 +0000\n")
            lines.append(b"data %d\n" % len(msg) + msg + b"\n")
            # ⛔ `from` COMES AFTER the message data, not before `committer`: the
            # fast-import grammar is `commit` → optional author → committer → data →
            # `from` → filemodify commands. Emitting it first makes fast-import fail
            # with "Expected committer but didn't get one".
            if i == 1:
                lines.append(f"from {tip}\n".encode())
            lines.append(b"M 100644 inline advance.txt\n")
            lines.append(b"data %d\n" % len(body) + body)
            lines.append(b"\n")
        # ⛔ `--force` IS REQUIRED: `main` is the CHECKED-OUT branch of this fixture, and
        # fast-import refuses to move a checked-out branch without it ("use -f"). A
        # pytest run is not the harness's git gate, so this is a fixture, not a merge.
        stored = subprocess.run(["git", "fast-import", "--quiet", "--force"],
                                cwd=self.repo, input=b"".join(lines),
                                capture_output=True)
        self.assertEqual(stored.returncode, 0, stored.stderr.decode())

    def abandoned_branch(self, branch: str, *, age_days: int | None = None) -> str:
        """A branch with ONE commit of its own, dated `age_days` ago, far behind main.

        ⛔ IT MUST HAVE A COMMIT OF ITS OWN. A branch pointing at main's own ancestor
        is TERMINAL (the existing `_branch_terminal_state` demotes it as merged
        history), so a test using one would pass without the stale-holder rule ever
        running — the vacuity this class exists to avoid. One commit ahead, 350 behind
        and 12 days silent is the measured shape of the incident's holders.

        Main is advanced AFTER the branch is cut, so the branch is genuinely behind by
        `BEHIND` commits rather than by construction of the assertion.
        """
        self.bare_origin()
        base = self._git_out("rev-parse", "HEAD").strip()
        tree = self._git_out("rev-parse", f"{base}^{{tree}}").strip()
        days = self.STALE_DAYS if age_days is None else age_days
        when = int(time.time()) - days * 86400
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com",
            "GIT_AUTHOR_DATE": f"{when} +0000",
            "GIT_COMMITTER_DATE": f"{when} +0000",
        }
        sha = subprocess.run(
            ["git", "commit-tree", tree, "-p", base, "-m", "abandoned work"],
            cwd=self.repo, check=True, capture_output=True, text=True, env=env,
        ).stdout.strip()
        _git(self.repo, "update-ref", f"refs/heads/{branch}", sha)
        _git(self.repo, "update-ref", f"refs/remotes/origin/{branch}", sha)
        self._fast_forward_main(self.BEHIND)
        _git(self.repo, "update-ref", "refs/remotes/origin/main", "refs/heads/main")
        # The bare origin gets the same main, so a `fetch` of a claim ref has a valid
        # remote and cannot be confused with an unreachable one.
        _git(self.repo, "push", "-q", "origin", "refs/heads/main:refs/heads/main")
        # ⛔ THE FIXTURE IS ASSERTED AGAINST THE SHIPPED THRESHOLDS. A history that came
        # out shorter than `BEHIND`, or a commit date the fixture failed to backdate, would
        # make every demotion below pass or fail for a reason unrelated to the rule — the
        # same class as a test that asserts a constant against itself. Measured with the
        # tool's OWN helpers, so the fixture and the rule cannot drift.
        mod = _tool_module()
        ref = f"refs/remotes/origin/{branch}"
        counts = mod._ahead_behind("git", str(self.repo), ref, 60)
        self.assertIsNotNone(counts, "the fixture branch's distance from main is unreadable")
        _ahead, behind = counts
        self.assertGreaterEqual(behind, mod.STALE_BEHIND_MIN,
                                f"fixture is only {behind} behind main")
        age = mod._ref_tip_age_days("git", str(self.repo), ref, 60)
        self.assertIsNotNone(age, "the fixture branch's tip date is unreadable")
        if age_days is None:
            # Skipped for the deliberate FRESH-tip fixture (the keep-blocking direction),
            # which must fail this test rather than pass it.
            self.assertGreaterEqual(age, mod.STALE_TIP_DAYS,
                                    f"fixture tip is only {age:.1f} days old")
        else:
            self.assertLess(age, mod.STALE_TIP_DAYS,
                            f"the fresh fixture's tip is {age:.1f} days old — a "
                            "keep-blocking fixture must be SILENT for the rule to be what "
                            "holds it")
        return sha

    def checked_out_worktree(self, branch: str, name: str) -> Path:
        """A worktree with a REAL checkout (not `--no-checkout`).

        ⛔ `--no-checkout` (what `add_worktree` above uses for other tests) leaves an
        empty index beside a full HEAD tree, so `git status --porcelain` reports every
        file as deleted — i.e. "uncommitted work" — and the stale-holder rule would
        never demote anything here. This fixture checks the tree out so a CLEAN
        worktree reads as clean.
        """
        path = self.tmp / "wt" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        _git(self.repo, "worktree", "add", "-q", str(path), branch)
        return path

    def live_claim(self, lease_until: int | None, *, message: str | None = None) -> None:
        """Write a real `refs/claims/issue-<ISSUE>` into the bare origin.

        The commit's MESSAGE is the lease (`claims.sh:_claim_commit`), and the empty
        tree is the one git defines for exactly this shape. `message` overrides the
        lease-shaped body, which is how the unparseable-lease polarity is exercised.
        """
        origin = self.tmp / "remotes" / "test-owner" / "test-repo.git"
        empty_tree = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com",
        }
        # ⛔ THE COMMIT IS BUILT IN THE ORIGIN, not in the clone: `update-ref` in a bare
        # repo cannot point at an object its own odb does not have, and `commit-tree` in
        # a bare repo needs no worktree at all.
        body = message if message is not None else (
            f"claim owner=box-a lease_until={lease_until} nonce=9806")
        sha = subprocess.run(
            ["git", "-C", str(origin), "commit-tree", empty_tree, "-m", body],
            check=True, capture_output=True, text=True, env=env,
        ).stdout.strip()
        subprocess.run(
            ["git", "-C", str(origin), "update-ref",
             f"refs/claims/issue-{ISSUE}", sha],
            check=True, capture_output=True, text=True, env=env,
        )

    def row(self, out: str, prefix: str) -> str:
        """The surface-table row for `prefix` — its NOTE lives here, not in the hit list."""
        return next(ln for ln in out.splitlines() if ln.startswith(prefix))

    def hit(self, out: str, needle: str, surface: str | None = None) -> str:
        """The HIT line naming `needle` (`  [surface] ref — detail (number)`).

        ⛔ ASSERTING ON THE ROW WOULD BE VACUOUS. The row's note mentions the words
        ADVISORY and "demoted" even when nothing was demoted, so a `assertNotIn` there
        would pass for the wrong reason; the strength of a hit is only visible on its own
        line.

        ⛔ AND THE SURFACE IS SELECTABLE, because a worktree's PATH also appears in the
        BRANCH hit's detail (its owner basis names the checkout). Searching by path alone
        finds the branch row, so a test would assert about the wrong surface.
        """
        prefix = f"  [{surface}] " if surface else "  ["
        lines = [ln for ln in out.splitlines()
                 if ln.startswith(prefix) and needle in ln]
        self.assertTrue(lines, f"no {surface or ''} hit line names {needle!r}:\n{out}")
        return lines[0]

    # ── the DEMOTION direction: provably abandoned → ADVISORY ───────────────

    def test_a_far_behind_silent_unowned_unclaimed_branch_is_advisory(self):
        """The incident's own shape: 1 ahead, 350+ behind, 12 days silent, nobody home.

        It must be CLEAN **and** the row must still be PRINTED — "reported, does not
        block, never silently dropped" is the whole contract: the cleanup (deleting the
        branch) is a separate, evidence-based action that needs the row visible.
        """
        self.gh_fixtures(closed_prs=[])
        branch = f"feat/{ISSUE}-stale"
        self.abandoned_branch(branch)
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        line = self.hit(out, f"origin/{branch}")
        self.assertIn("ADVISORY", line)
        self.assertIn("350 behind origin/main", line)
        self.assertIn("no commit in 12.0 days", line)
        self.assertIn("no worktree is checked out on it", line)
        self.assertIn(f"no `refs/claims/issue-{ISSUE}` on origin", line)
        # The note reports the MEASUREMENT, so a reader can tell a demotion happened
        # rather than the ref simply not being enumerated.
        self.assertIn("1 demoted to ADVISORY", out)

    def test_a_worktree_record_on_an_abandoned_branch_is_advisory_too(self):
        """The #2446 angle: the HOLDER was the worktree, not only the branch.

        Demoting the branch alone would leave the issue un-dispatchable through the
        worktree row — the two are one claim seen twice, so both must answer the same
        way. The worktree here is clean and no process stands in it, which is exactly
        what the incident's `tm-2446` checkout looked like.
        """
        self.gh_fixtures(closed_prs=[])
        branch = f"fix/{ISSUE}-stale-wt"
        self.abandoned_branch(branch)
        wt = self.checked_out_worktree(branch, f"wt-{ISSUE}")
        self.assertTrue((wt / "seed.txt").exists(), "the worktree must be checked out")
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        line = self.hit(out, str(wt), surface="local worktrees")
        self.assertIn("ADVISORY", line)
        # The basis names the checkout's PATH, so the reader can see which directory was
        # measured rather than a rule firing at them from nowhere.
        self.assertIn(f"{wt}: no live pi process and no uncommitted work", line)
        # ...and the BRANCH hit for the same claim is advisory too, not merely masked.
        self.assertIn("ADVISORY", self.hit(out, f"refs/heads/{branch}"))

    def test_an_expired_claim_ref_does_not_keep_a_stale_branch_blocking(self):
        """Condition 4 reads the LEASE, not the ref's existence.

        The incident measured 60 of 60 sampled claim refs EXPIRED. Reading presence as
        liveness would leave every one of them blocking — the rule would be inert on
        exactly the box it was measured on.
        """
        self.gh_fixtures(closed_prs=[])
        self.abandoned_branch(f"feat/{ISSUE}-stale-claim")
        self.live_claim(int(time.time()) - 3600)
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        line = self.hit(out, "origin/feat/" + f"{ISSUE}-stale-claim")
        self.assertIn("ADVISORY", line)
        self.assertIn("EXPIRED at", line)

    # ── the KEEP-BLOCKING direction: one test per non-vacuity condition ─────

    def test_a_recent_commit_on_a_far_behind_branch_still_blocks(self):
        """Condition 2 (silence). A long-lived branch 350 behind that is STILL being
        committed to is live work — divergence alone must never demote.

        ⛔ IT IS ALSO THE PROOF THAT `behind` IS NOT DECISIVE: measured across this
        repo's 1507 number-naming refs, 98 are 300+ behind with a tip under 7 days old.
        """
        self.gh_fixtures(closed_prs=[])
        self.abandoned_branch(f"feat/{ISSUE}-recent", age_days=0)
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        line = self.hit(out, f"origin/feat/{ISSUE}-recent")
        self.assertNotIn("ADVISORY", line)
        self.assertIn("350 behind origin/main", line)
        self.assertIn("0 demoted to ADVISORY", self.row(out, "remote branches"))

    def test_an_old_commit_that_is_not_far_behind_still_blocks(self):
        """Condition 1 (divergence). Silence alone must never demote.

        This branch's tip is 12 days old — the silence test passes — but `origin/main` has
        not moved past it, so it is NOT behind. A ref that main has not passed is not
        stranded work, and demoting on age alone would turn this rule into a liveness
        timer for every idle lane on a quiet day.
        """
        self.gh_fixtures(closed_prs=[])
        branch = f"feat/{ISSUE}-idle"
        self.bare_origin()
        base = self._git_out("rev-parse", "HEAD").strip()
        tree = self._git_out("rev-parse", f"{base}^{{tree}}").strip()
        when = int(time.time()) - self.STALE_DAYS * 86400
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com",
            "GIT_AUTHOR_DATE": f"{when} +0000",
            "GIT_COMMITTER_DATE": f"{when} +0000",
        }
        sha = subprocess.run(
            ["git", "commit-tree", tree, "-p", base, "-m", "idle work"],
            cwd=self.repo, check=True, capture_output=True, text=True, env=env,
        ).stdout.strip()
        _git(self.repo, "update-ref", f"refs/remotes/origin/{branch}", sha)
        _git(self.repo, "update-ref", "refs/remotes/origin/main", "refs/heads/main")
        _git(self.repo, "push", "-q", "origin", "refs/heads/main:refs/heads/main")
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        line = self.hit(out, f"origin/{branch}")
        self.assertNotIn("ADVISORY", line)
        self.assertIn("0 behind origin/main", line)

    def test_a_live_process_in_the_worktree_still_blocks(self):
        """Condition 3a (process cwd). A long test run is a live owner.

        The worktree is old, the branch is 350 behind and its tip is 12 days old — every
        OTHER condition holds. What keeps it blocking is a live `pi` process whose cwd is
        inside the checkout, which is the discriminator the rule rests on (age is not).
        """
        self.gh_fixtures(closed_prs=[])
        branch = f"fix/{ISSUE}-busy"
        self.abandoned_branch(branch)
        wt = self.checked_out_worktree(branch, f"busy-{ISSUE}")
        fake_bin = self.tmp / "fakebin"
        fake_bin.mkdir(exist_ok=True)
        # ⛔ A COPY OF A REAL BINARY, NOT A SHELL SCRIPT. `fleet_state.pi_processes`
        # matches `basename(argv[0]) == "pi"`, and the kernel rewrites argv[0] to the
        # INTERPRETER for a `#!` script — `ps` then reads `bash /…/pi 120` and the census
        # legitimately does not see a lane named `pi`. An ELF copy keeps argv[0].
        pi = fake_bin / "pi"
        shutil.copy("/bin/sleep", pi)
        proc = subprocess.Popen([str(pi), "120"], cwd=str(wt),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            # Precondition: the census the tool uses can SEE it, so a green
            # assertion below cannot be a subprocess race.
            self._wait_for_pi_cwd(wt)
            rc, out = self.run_tool()
        finally:
            proc.terminate()
            proc.wait(timeout=10)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertNotIn("ADVISORY", self.hit(out, str(wt), surface="local worktrees"))
        self.assertIn("live pi process standing in it",
                      self.row(out, "local worktrees"))

    def test_uncommitted_work_in_the_worktree_still_blocks(self):
        """Condition 3b (uncommitted work). A lane's unsaved changes are its work."""
        self.gh_fixtures(closed_prs=[])
        branch = f"fix/{ISSUE}-dirty"
        self.abandoned_branch(branch)
        wt = self.checked_out_worktree(branch, f"dirty-{ISSUE}")
        (wt / "wip.txt").write_text("unsaved work\n")
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("the worktree has uncommitted work",
                      self.row(out, "local worktrees"))
        self.assertNotIn("ADVISORY", self.hit(out, str(wt), surface="local worktrees"))

    def test_a_live_claim_ref_still_blocks(self):
        """Condition 4 (a live claim). This is the dimension #6108 is actually about:
        a lane that has CLAIMED the work — here an unexpired `refs/claims/issue-N` —
        keeps the issue blocked, whatever the branch's age."""
        self.gh_fixtures(closed_prs=[])
        self.abandoned_branch(f"feat/{ISSUE}-claimed")
        self.live_claim(int(time.time()) + 3600)
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertNotIn("ADVISORY", self.hit(out, f"origin/feat/{ISSUE}-claimed"))
        self.assertIn("is a LIVE claim", self.row(out, "remote branches"))

    def test_a_claim_ref_with_an_unparseable_lease_never_demotes(self):
        """The lease polarity is `claims.sh`'s, and it is the fail-CLOSED direction.

        A claim whose message carries no parseable `lease_until` is LIVE, never expired:
        reading it as free would let a malformed (or truncated) lease CLEAR a blocking
        holder — the fail-open direction of the one condition that touches the network.
        """
        self.gh_fixtures(closed_prs=[])
        self.abandoned_branch(f"feat/{ISSUE}-badlease")
        self.live_claim(None, message="claim owner=box-a nonce=9806")
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertNotIn("ADVISORY", self.hit(out, f"origin/feat/{ISSUE}-badlease"))
        self.assertIn("lease this reader cannot parse", self.row(out, "remote branches"))

    def test_an_unreadable_distance_from_main_never_demotes(self):
        """Fail-closed on condition 1's MEASUREMENT (not on its threshold).

        A branch whose distance from main cannot be counted is UNMEASURED, and an
        unmeasured datum must never become a downgrade: that is the class of every
        false CLEAN this module has historically shipped.
        """
        self.gh_fixtures(closed_prs=[])
        self.abandoned_branch(f"feat/{ISSUE}-nodistance")
        stub = _write_exec(self.tmp / "git-stub-no-distance", GIT_STUB_NO_DISTANCE)
        rc, out = self.run_tool(git_bin=stub,
                                env_extra={"REAL_GIT": shutil.which("git")})
        self.assertNotEqual(rc, 0, out)
        self.assertNotIn("ADVISORY", self.hit(out, f"origin/feat/{ISSUE}-nodistance"))
        self.assertIn("could not be measured", self.row(out, "remote branches"))

    def test_an_unreadable_tip_date_never_demotes(self):
        """Fail-closed on condition 2's MEASUREMENT (not on its threshold)."""
        self.gh_fixtures(closed_prs=[])
        self.abandoned_branch(f"feat/{ISSUE}-nodate")
        stub = _write_exec(self.tmp / "git-stub-no-date", GIT_STUB_NO_TIP_DATE)
        rc, out = self.run_tool(git_bin=stub,
                                env_extra={"REAL_GIT": shutil.which("git")})
        self.assertNotEqual(rc, 0, out)
        self.assertNotIn("ADVISORY", self.hit(out, f"origin/feat/{ISSUE}-nodate"))
        self.assertIn("tip commit's date could not be read", self.row(out, "remote branches"))

    def test_a_detached_worktree_is_never_matched_by_name(self):
        """`(detached)` is not a branch name, and treating it as one was a fail-closed trap.

        A detached checkout of an UNRELATED commit, carrying uncommitted work, must not
        keep an abandoned branch blocking: matching `(detached)` by name would make every
        detached worktree "the same holder" as every ref under judgement, and dirty work in
        one unrelated checkout would hold an arbitrary issue forever.
        """
        self.gh_fixtures(closed_prs=[])
        branch = f"feat/{ISSUE}-detached-neighbour"
        self.abandoned_branch(branch)
        # ⛔ THE PATH MUST NOT NAME THE ISSUE. A path that does is a hit in its own right
        # (#9540: a detached worktree leaves no branch to contradict the path), which would
        # make this test pass or fail for a reason that has nothing to do with the owner read.
        other = self.tmp / "wt" / "unrelated-checkout"
        other.parent.mkdir(parents=True, exist_ok=True)
        _git(self.repo, "worktree", "add", "-q", "--detach", str(other), "HEAD")
        (other / "unrelated.txt").write_text("someone else's work\n")
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertIn("ADVISORY", self.hit(out, f"origin/{branch}"))

    def test_an_unreadable_claim_registry_never_demotes(self):
        """Fail-closed on the one condition that costs network I/O.

        An `origin` that cannot be consulted (rc != 0) is NOT "nobody holds it": it is
        an unread registry, and the branch keeps blocking.
        """
        self.gh_fixtures(closed_prs=[])
        self.abandoned_branch(f"feat/{ISSUE}-noregistry")
        _git(self.repo, "remote", "set-url", "origin",
             str(self.tmp / "does-not-exist.git"))
        rc, out = self.run_tool()
        # The slug is still derived from the OTHER components of the URL, so the run is
        # a real verdict; only the claim read fails.
        self.assertIn("claim registry could not be read",
                      self.row(out, "remote branches"))
        self.assertNotIn("ADVISORY", self.hit(out, f"origin/feat/{ISSUE}-noregistry"))
        self.assertNotEqual(rc, 0, out)

    # ── #6108, REOPENED 2026-10-24: the EMPTY CLAIM ────────────────────────────
    #
    # THE MEASURED DEFECT. `refs/heads/feat/9567-tm` was 0 commits ahead / 121 behind
    # `origin/main` — a branch a lane created and never used — and it froze #9567 (a
    # P0-blocker) permanently: every agent's pre-flight refused, for every agent, with no
    # dismissal path. `fix/9097-protocol-precedence` (0/75) froze an open P1 the same way.
    # #6108 asked for exactly this demotion in 2026-09; it was REFUSED then because a lane
    # between `git worktree add` and its first commit is ALSO 0 ahead, and demoting that
    # would let two lanes write to one branch (#4256). The owner reopened it with the
    # narrow condition: demote ONLY when every mechanical piece of holder evidence is
    # also absent.
    #
    # ⛔ THE MATRIX BELOW IS THE CONTRACT, ONE TEST PER CELL:
    #
    #     (a) 0 ahead, clean, unowned, unclaimed        -> ADVISORY, exit 0, STILL PRINTED
    #     (b) >=1 commit ahead                          -> BLOCKING (unchanged)
    #     (c) 0 ahead BUT uncommitted work in a worktree -> BLOCKING
    #     (c2) 0 ahead BUT a live pi process in it      -> BLOCKING (the #6108 window)
    #     (c3) 0 ahead BUT a live claim ref             -> BLOCKING
    #     (d) any reading that fails (distance / status)-> BLOCKING, reported UNREADABLE
    #
    # ⛔ EVERY FIXTURE ASSERTS ITSELF against the shipped readers (`_ahead_behind`), so a
    # fixture that silently stopped being 0-ahead could not make a demotion test pass for
    # the wrong reason — the same rule the #9806 fixtures follow.

    def empty_branch(self, branch: str) -> str:
        """A LOCAL branch at `origin/main`'s tip: 0 commits of its own.

        The bare origin is REQUIRED: without it the claim-registry read
        (`git ls-remote origin refs/claims/issue-N`) fails, an unreadable registry is
        LIVE, and the demotion tests would pass or fail for a reason that has nothing to
        do with the rule (the same trap `StaleHolderTests`' docstring records).
        """
        self.bare_origin()
        _git(self.repo, "update-ref", "refs/remotes/origin/main", "refs/heads/main")
        _git(self.repo, "push", "-q", "origin", "refs/heads/main:refs/heads/main")
        _git(self.repo, "branch", branch)
        mod = _tool_module()
        counts = mod._ahead_behind("git", str(self.repo), f"refs/heads/{branch}", 60)
        self.assertIsNotNone(counts, "the fixture branch's distance from main is unreadable")
        ahead, _behind = counts
        self.assertEqual(ahead, 0, f"the empty-claim fixture is {ahead} ahead, not 0")
        return f"refs/heads/{branch}"

    def commit_on(self, branch: str) -> str:
        """ONE commit of its own on `branch`, built with `commit-tree` so the checked-out
        tree is never touched (the fixture's `main` stays where it is)."""
        base = self._git_out("rev-parse", f"refs/heads/{branch}").strip()
        tree = self._git_out("rev-parse", f"{base}^{{tree}}").strip()
        sha = subprocess.run(
            ["git", "commit-tree", tree, "-p", base, "-m", f"work on {branch}"],
            cwd=self.repo, check=True, capture_output=True, text=True,
        ).stdout.strip()
        _git(self.repo, "update-ref", f"refs/heads/{branch}", sha)
        return sha

    def test_a_zero_ahead_clean_unowned_unclaimed_branch_is_advisory(self):
        """(a) THE MEASURED CASE: 0 ahead, nothing dirty, no owner, no claim.

        It must be CLEAN **and** the row must still be PRINTED — "reported, does not
        block, never silently dropped" is the whole contract, because reaping the branch
        is a separate, evidence-based action this gate deliberately does not take.
        """
        self.gh_fixtures(closed_prs=[])
        branch = f"fix/{ISSUE}-empty"
        ref = self.empty_branch(branch)
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        line = self.hit(out, ref)
        self.assertIn("0 commit(s) ahead", line)
        self.assertIn("ADVISORY", line)
        self.assertIn("EMPTY-CLAIM", line)
        # The BASIS is printed, so the demotion is auditable rather than implied.
        self.assertIn("no worktree is checked out on it", line)
        self.assertIn(f"no `refs/claims/issue-{ISSUE}` on origin", line)
        # ...and the measurement is reported on the surface row too.
        self.assertIn("1 0-ahead local branch(es) judged as EMPTY CLAIMS, 1 demoted", out)
        self.assertIn("reported, non-blocking", self.row(out, "local branches"))

    def test_a_branch_one_commit_ahead_still_blocks(self):
        """(b) The dangerous direction: real WIP is never demoted.

        ONE commit, so the count cannot be confused with the empty fixture's 0.
        """
        self.gh_fixtures(closed_prs=[])
        branch = f"fix/{ISSUE}-one-ahead"
        ref = self.empty_branch(branch)
        self.commit_on(branch)
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        line = self.hit(out, ref)
        self.assertIn("1 commit(s) ahead", line)
        self.assertNotIn("ADVISORY", line)
        self.assertNotIn("EMPTY-CLAIM", line)

    def test_a_zero_ahead_branch_with_uncommitted_work_still_blocks(self):
        """(c) A lane's UNCOMMITTED work is work, even with nothing committed.

        This is the shape a plain "0 ahead ⇒ advisory" rule would have freed — the
        fail-open the narrow condition exists to prevent.
        """
        self.gh_fixtures(closed_prs=[])
        branch = f"fix/{ISSUE}-empty-dirty"
        ref = self.empty_branch(branch)
        wt = self.checked_out_worktree(branch, f"empty-dirty-{ISSUE}")
        (wt / "wip.txt").write_text("unsaved work\n")
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertNotIn("ADVISORY", self.hit(out, ref))
        self.assertIn("the worktree has uncommitted work",
                      self.row(out, "local branches"))
        self.assertIn("kept blocking by a live owner or claim",
                      self.row(out, "local branches"))

    def test_a_zero_ahead_branch_with_a_live_process_in_its_worktree_still_blocks(self):
        """(c2) THE #6108 WINDOW: a lane between `git worktree add` and its first commit.

        The checkout is CLEAN and the branch is 0 ahead — the exact shape the reopened
        rule touches — and what keeps it blocking is the live `pi` process standing in it.

        ⛔ WHAT THIS DOES **NOT** PROVE: a checkout created BEFORE the lane's process starts
        (a controller running `git worktree add`, then dispatching) has no process in it yet
        and is demoted by `test_a_worktree_record_on_a_zero_ahead_branch_is_advisory_too` —
        that is the stated PREPARE-THEN-DISPATCH residual in `judge_empty`. The mechanism
        that holds such an issue is the CLAIM REF, and
        `test_a_zero_ahead_branch_with_a_live_claim_ref_still_blocks` is the test that pins
        it. So this test proves the ALREADY-RUNNING case; it must not be read as proof that
        #4256's "two lanes, one branch" cannot happen in the pre-start window.
        """
        self.gh_fixtures(closed_prs=[])
        branch = f"fix/{ISSUE}-empty-busy"
        ref = self.empty_branch(branch)
        wt = self.checked_out_worktree(branch, f"empty-busy-{ISSUE}")
        fake_bin = self.tmp / "fakebin-empty"
        fake_bin.mkdir(exist_ok=True)
        # ⛔ A COPY OF A REAL BINARY, NOT A SHELL SCRIPT: `fleet_state.pi_processes`
        # matches `basename(argv[0]) == "pi"`, and the kernel rewrites argv[0] to the
        # interpreter for a `#!` script (the trap the stale-holder test documents).
        #
        # ⛔ AND IT IS INVOKED BY NAME THROUGH `PATH`, so argv[0] is exactly `pi`. Launched
        # as an absolute path, its first token is 60+ characters in the `command` column,
        # and `ps`'s default output width truncates that column — the census then reads a
        # basename that is not `pi` and the lane is invisible (`fleet_state.pi_processes`
        # passes `-ww` for exactly this reason; a test must not depend on its fixture's
        # path length to be seen).
        pi = fake_bin / "pi"
        shutil.copy("/bin/sleep", pi)
        env = {**os.environ, "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"}
        proc = subprocess.Popen(["pi", "120"], cwd=str(wt), env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            self._wait_for_pi_cwd(wt)
            rc, out = self.run_tool()
        finally:
            proc.terminate()
            proc.wait(timeout=10)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertNotIn("ADVISORY", self.hit(out, ref))
        self.assertIn("live pi process standing in it", self.row(out, "local branches"))

    def test_a_zero_ahead_branch_with_a_live_claim_ref_still_blocks(self):
        """(c3) A CLAIMED issue is not free, whatever the branch contains.

        This is #6108's own reasoning ("never read a lane that has CLAIMED an issue as
        free") applied in the one dimension that is actually about the claim.
        """
        self.gh_fixtures(closed_prs=[])
        branch = f"fix/{ISSUE}-empty-claimed"
        ref = self.empty_branch(branch)
        self.live_claim(int(time.time()) + 3600)
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertNotIn("ADVISORY", self.hit(out, ref))
        self.assertIn("is a LIVE claim", self.row(out, "local branches"))

    def test_a_worktree_record_on_a_zero_ahead_branch_is_advisory_too(self):
        """The measured block named BOTH rows for one claim — so both must answer the same.

        Demoting the branch alone would leave #9567 un-dispatchable through the worktree
        row; that is why `judge_empty` runs on both surfaces.

        ⚠️ THIS IS THE PREPARE-THEN-DISPATCH WINDOW, ASSERTED ON PURPOSE. The checkout is
        clean, registered, and has nothing running in it, so every mechanical signal this
        gate can read agrees with "abandoned" — and the owner's decision distinguishes by
        DIRT (a dirty checkout blocks; a clean one does not). A flow that creates the
        checkout before dispatching is therefore held by `refs/claims/issue-<N>`, not by the
        checkout: see `judge_empty`'s STATED RESIDUALS. If that trade is ever revisited,
        THIS is the test that has to change, and the residual note with it.
        """
        self.gh_fixtures(closed_prs=[])
        branch = f"fix/{ISSUE}-empty-wt"
        ref = self.empty_branch(branch)
        wt = self.checked_out_worktree(branch, f"empty-wt-{ISSUE}")
        self.assertTrue((wt / "seed.txt").exists(), "the worktree must be checked out")
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertIn("ADVISORY", self.hit(out, ref))
        wt_line = self.hit(out, str(wt), surface="local worktrees")
        self.assertIn("ADVISORY", wt_line)
        self.assertIn("EMPTY-CLAIM", wt_line)
        self.assertIn("no live pi process and no uncommitted work", wt_line)

    def test_a_generated_branch_worktree_still_blocks_on_its_path(self):
        """⛔ #3611 MUST NOT BE REOPENED BY THE EMPTY LEG (review finding, 2026-10-24).

        A bot-minted branch is not a lane's claim, and the module promises that "a bot-minted
        name is not evidence about the PATH either" — so a worktree record reached by its
        PATH alone keeps BLOCKING. The empty leg therefore runs only when the record's BRANCH
        names this issue (`branch_hit`); the branch surface never even considers a generated
        ref (`_is_generated_branch` skips it), and the two surfaces must not disagree.

        The fixture makes the demotion ELIGIBLE on every other axis (0 ahead, clean checkout,
        no process, a readable claim registry with no claim), so without the `branch_hit`
        guard this record would be ADVISORY and the run would exit 0.
        """
        self.gh_fixtures(closed_prs=[])
        # A readable claim registry: without it the run would block for an unrelated reason
        # (an unreadable registry is LIVE) and this test would pass vacuously.
        self.empty_branch("feat/9999-unrelated-base")
        _git(self.repo, "branch", f"mergify/merge-queue/{ISSUE}bad001")
        wt = self.checked_out_worktree(f"mergify/merge-queue/{ISSUE}bad001",
                                       f"{ISSUE}-queue-wt")
        self.assertTrue((wt / "seed.txt").exists(), "the worktree must be checked out")
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        line = self.hit(out, str(wt), surface="local worktrees")
        self.assertNotIn("ADVISORY", line)
        self.assertNotIn("EMPTY-CLAIM", line)

    def test_an_unreadable_distance_blocks_a_zero_ahead_branch_as_UNREADABLE(self):
        """(d) A failed READ is not an empty claim.

        The distance is the one datum the rule rests on, so when `rev-list --count`
        cannot answer, the ref keeps BLOCKING and the report says `UNREADABLE` — a
        missing measurement must never read as "free" (the rule that made #1185 refuse).
        """
        self.gh_fixtures(closed_prs=[])
        branch = f"fix/{ISSUE}-empty-nodistance"
        ref = self.empty_branch(branch)
        stub = _write_exec(self.tmp / "git-stub-no-distance-2", GIT_STUB_NO_DISTANCE)
        rc, out = self.run_tool(git_bin=stub,
                                env_extra={"REAL_GIT": shutil.which("git")})
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        line = self.hit(out, ref)
        self.assertNotIn("ADVISORY", line)
        self.assertIn("UNREADABLE", line)
        self.assertIn("NOT treated as an empty claim", line)
        self.assertIn("NOT demoted on an unreadable measurement",
                      self.row(out, "local branches"))
        self.assertIn("0 demoted", self.row(out, "local branches"))

    def test_a_missing_worktree_directory_keeps_the_claim_blocking(self):
        """(d) STATED RESIDUAL: absence is not evidence of death.

        `_worktree_liveness` documents why a registered worktree whose directory is gone
        is NOT demoted (a `mv` keeps a worktree working through its absolute `gitdir:`),
        and the owner read then cannot run `git status` in it. So the ref stays blocking
        and is reported UNREADABLE — the gate reports, it never reaps.
        """
        self.gh_fixtures(closed_prs=[])
        branch = f"fix/{ISSUE}-empty-moved"
        ref = self.empty_branch(branch)
        wt = self.checked_out_worktree(branch, f"empty-moved-{ISSUE}")
        shutil.rmtree(wt)
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertNotIn("ADVISORY", self.hit(out, ref))
        self.assertIn("not on disk", self.row(out, "local branches"))
        # ⛔ AND THE READING IS NAMED AS A FAILED READ, on BOTH surfaces (review finding
        # 2026-10-24). The first version derived "was this unreadable?" by matching the
        # basis TEXT against a set — but the owner reader PREFIXES the basis with the
        # checkout's path, so the two could never be equal and the `UNREADABLE` wording
        # was silently lost (the verdict was still fail-closed, which is why nothing else
        # caught it). These two assertions are what pin the tri-state the readers return.
        branch_line = self.hit(out, ref)
        self.assertIn("UNREADABLE", branch_line,
                      "the branch row must say the read FAILED, not merely that it blocked")
        self.assertIn("NOT treated as an empty claim", branch_line)
        wt_line = self.hit(out, str(wt), surface="local worktrees")
        self.assertIn("UNREADABLE", wt_line)
        self.assertNotIn("ADVISORY", wt_line)

    def test_an_empty_claim_is_revalidated_at_the_moment_of_the_gate(self):
        """The rule is COMPUTED per run, never cached from a previous answer.

        Same branch, two runs: CLEAN while it is empty, COLLISION the moment a commit
        lands on it. A cached verdict (or a cached `ahead` count) would answer the second
        run with the first run's reading — the stale-flag class this whole change is
        about.
        """
        self.gh_fixtures(closed_prs=[])
        branch = f"fix/{ISSUE}-empty-then-work"
        ref = self.empty_branch(branch)
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("ADVISORY", self.hit(out, ref))
        self.commit_on(branch)
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("1 commit(s) ahead", self.hit(out, ref))
        self.assertNotIn("ADVISORY", self.hit(out, ref))

    def test_a_zero_ahead_remote_ref_is_never_an_empty_claim(self):
        """CONDITION 1's scope, pinned in the direction that matters (review, 2026-10-24).

        A remote-tracking ref is a FETCH CACHE: `refs/remotes/origin/x` is 0 ahead of
        `origin/main` whenever the last fetch saw it at main's tip, which says nothing
        about the branch that exists on the remote right now (#7693's class). Deleting the
        `refs/heads/` guard left the ENTIRE suite green — verified — while shipping exactly
        that, so this test exists to make the scope testable: the ref must stay BLOCKING
        and must not be reported as an empty claim.
        """
        self.gh_fixtures(closed_prs=[])
        # A readable registry and a real bare origin, so the run cannot block for a reason
        # that has nothing to do with this ref (see `empty_branch`).
        self.empty_branch(f"fix/{ISSUE}-remote-base")
        main_tip = self._git_out("rev-parse", "refs/heads/main").strip()
        ref = f"refs/remotes/origin/fix/{ISSUE}-cached"
        _git(self.repo, "update-ref", ref, main_tip)
        mod = _tool_module()
        counts = mod._ahead_behind("git", str(self.repo), ref, 60)
        self.assertIsNotNone(counts)
        self.assertEqual(counts[0], 0, "the fixture remote ref must be 0 ahead")
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        line = self.hit(out, ref)
        self.assertNotIn("EMPTY-CLAIM", line)
        self.assertNotIn("ADVISORY", line)
        # And it is a BLOCKING row, not merely an absent demotion.
        self.assertIn("0 commit(s) ahead", line)
        self.assertIn("(number)", line)

    def test_an_unreadable_process_census_is_never_no_owner(self):
        """CONDITION 3's fail-closed arm: a census that cannot ANSWER is not "nobody there".

        Flipping this arm to "no owner" left the whole suite green (verified in review),
        which would have made the PR's headline safety claim untested. Called at the
        decision level because the census is a subprocess read that no CLI fixture can fail
        on demand.
        """
        self.empty_branch(f"fix/{ISSUE}-census-error")
        holder = self.stale_holder()
        mod = _tool_module()
        with mock.patch.object(mod.fleet_state, "pi_processes",
                               return_value=([], "ps: cannot execute")):
            empty, basis, unreadable = holder.judge_empty(
                f"refs/heads/fix/{ISSUE}-census-error", (0, 120))
        self.assertFalse(empty, "an unanswered census must never demote")
        self.assertTrue(unreadable, "the reading failed, and the report must say so")
        self.assertIn("could not be read", basis)
        self.assertIn("UNREGISTERED checkout", basis)
        self.assertEqual(holder.empty_unmeasured, [basis],
                         "an unreadable reading belongs under `unmeasured`, not `kept`")
        self.assertEqual(holder.empty_kept, [])

    def test_a_live_pi_whose_cwd_cannot_be_read_is_never_no_owner(self):
        """⛔ THE CENSUS'S OWN BLIND SPOT (review, 2026-10-24).

        `fleet_state.process_cwds` SKIPS a pid whose cwd it cannot resolve and reports no
        error — correct for a pid that has exited, a fail-open for a pid that is alive
        (hidepid, a removed directory, an `lsof` placeholder). Taken as a complete census,
        "no cwd matched" reads as "no live owner" about a lane the reader could not see.
        A LIVE unresolved pid therefore makes the empty test UNREADABLE, and this pins it:
        without the gap check the demotion below is `True`.
        """
        self.empty_branch(f"fix/{ISSUE}-census-gap")
        holder = self.stale_holder()
        mod = _tool_module()
        with mock.patch.object(mod.fleet_state, "pi_processes",
                               return_value=([{"pid": os.getpid()}], "")), \
             mock.patch.object(mod.fleet_state, "process_cwds", return_value=({}, "")):
            empty, basis, unreadable = holder.judge_empty(
                f"refs/heads/fix/{ISSUE}-census-gap", (0, 120))
        self.assertFalse(empty, "an unresolvable-cwd census must never demote")
        self.assertTrue(unreadable)
        self.assertIn("could not resolve the cwd", basis)
        self.assertIn(str(os.getpid()), basis,
                      "the refusal must name WHICH process it could not read")

    def test_a_failed_git_status_in_the_worktree_reads_UNREADABLE(self):
        """CONDITION 3's other fail-closed arm: a dirt read that fails is not "clean".

        The worktree record exists and its directory exists, so the `isdir` guard does not
        apply — and `git status` then fails, which must NOT be read as an empty tree. The
        fixture breaks the checkout's `.git` pointer, which is a real git failure (rc 128),
        not a stubbed one.
        """
        self.gh_fixtures(closed_prs=[])
        branch = f"fix/{ISSUE}-empty-broken-git"
        ref = self.empty_branch(branch)
        wt = self.checked_out_worktree(branch, f"empty-broken-{ISSUE}")
        (wt / ".git").write_text("gitdir: /nonexistent/gitdir-for-this-test\n")
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        line = self.hit(out, ref)
        self.assertNotIn("ADVISORY", line)
        self.assertIn("UNREADABLE", line)
        self.assertIn("git status` in the worktree failed", self.row(out, "local branches"))

    def test_a_pruned_moved_checkout_with_a_live_lane_is_not_demoted(self):
        """⛔ THE LIST IS NOT THE WORLD (review finding, reproduced end to end).

        A lane whose checkout was MOVED and whose stale registration was then PRUNED has no
        worktree record at all, so `_name_matched_owner` answers "no worktree is checked out
        on it" — and before `_unregistered_checkout_owner` the ref was DEMOTED while a live
        `pi` stood in the moved tree writing it (`VERDICT: CLEAN`, exit 0). That is the
        #4256 direction this rule must never open.

        It is not demoted now, and the reason is named: the moved checkout's `gitdir:` is
        gone with its registration, so its branch cannot be NAMED — an unreadable reading,
        which blocks by construction.
        """
        self.gh_fixtures(closed_prs=[])
        branch = f"fix/{ISSUE}-empty-pruned"
        ref = self.empty_branch(branch)
        wt = self.checked_out_worktree(branch, f"empty-pruned-{ISSUE}")
        proc = self._start_fake_pi(wt)
        moved = self.tmp / f"moved-away-{ISSUE}"
        try:
            self._wait_for_pi_cwd(wt)
            os.rename(wt, moved)
            self._wait_for_pi_cwd(moved)
            # The registration now points at a path that no longer exists, so this is the
            # module's OWN advice for a dead record — and it is what erases the last trace.
            _git(self.repo, "worktree", "prune")
            rc, out = self.run_tool()
        finally:
            proc.terminate()
            proc.wait(timeout=10)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        line = self.hit(out, ref)
        self.assertNotIn("ADVISORY", line)
        self.assertNotIn("EMPTY-CLAIM", line)
        self.assertIn("UNREADABLE", line)
        self.assertIn("UNREGISTERED checkout", line)

    def test_a_live_lane_in_ANOTHER_repository_is_not_a_claim_on_this_one(self):
        """PRECISION ARM: a foreign checkout is not a holder of this repository's refs.

        A clone has its OWN `refs/heads/*`: a lane on `fix/N` in a clone is not a lane on
        this checkout's `fix/N`, and reading it as one would block every issue in every
        repo on a box where some other repository happens to carry the same branch name —
        the over-block that gets a gate turned off. So the probe asks each unregistered
        `pi` cwd for its `--git-common-dir` and skips anything that is not THIS repository's
        (removing that comparison turns this test red: the clone's branch name matches).
        """
        self.gh_fixtures(closed_prs=[])
        branch = f"fix/{ISSUE}-empty-elsewhere"
        ref = self.empty_branch(branch)
        clone = self.tmp / f"unregistered-{ISSUE}"
        subprocess.run(["git", "clone", "-q", str(self.bare_origin()), str(clone)],
                       check=True, capture_output=True, text=True)
        _git(clone, "config", "user.email", "test@example.com")
        _git(clone, "config", "user.name", "Test")
        _git(clone, "switch", "-q", "-c", branch, "origin/main")
        head = subprocess.run(
            ["git", "-C", str(clone), "symbolic-ref", "--short", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        self.assertEqual(head, branch, "the fixture checkout must be on the judged branch")
        proc = self._start_fake_pi(clone)
        try:
            self._wait_for_pi_cwd(clone)
            rc, out = self.run_tool()
        finally:
            proc.terminate()
            proc.wait(timeout=10)
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        line = self.hit(out, ref)
        self.assertIn("ADVISORY", line)
        self.assertNotIn("UNREGISTERED checkout", line)

    def stale_holder(self):
        mod = _tool_module()
        return mod.StaleHolder("git", str(self.repo), ISSUE, 60, [])

    def _start_fake_pi(self, cwd: Path) -> subprocess.Popen:
        """A process the census will see as `pi`, standing in `cwd` (see test (c2) above).

        A COPY OF A REAL BINARY invoked by NAME through `PATH`: a `#!` script would carry
        the interpreter as argv[0], and an absolute path would put the matcher at the mercy
        of the `ps` column width.
        """
        fake_bin = self.tmp / "fakebin-empty"
        fake_bin.mkdir(exist_ok=True)
        pi = fake_bin / "pi"
        if not pi.exists():
            shutil.copy("/bin/sleep", pi)
        env = {**os.environ, "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"}
        return subprocess.Popen(["pi", "120"], cwd=str(cwd), env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # ── helpers ─────────────────────────────────────────────────────────────

    def _wait_for_pi_cwd(self, wt: Path, timeout: float = 10.0) -> None:
        """Block until `fleet_state` can see the fake lane, then assert it did.

        Without this the census can run before the child has exec'd, and the test would
        read a real race as a rule failure — a green-below / red-above flake is the
        worst kind of test.
        """
        mod = _tool_module()
        deadline = time.time() + timeout
        want = os.path.realpath(str(wt))
        while time.time() < deadline:
            procs, err = mod.fleet_state.pi_processes()
            if not err:
                cwds, cerr = mod.fleet_state.process_cwds(
                    [int(p["pid"]) for p in procs])
                if not cerr and any(
                        os.path.realpath(c) == want for c in cwds.values()):
                    return
            time.sleep(0.1)
        self.fail("the process census never saw the fake pi lane — the test cannot "
                  "discriminate process-cwd liveness")


class HolderGateTests(unittest.TestCase):
    """The claim gate's board read (#8175), at the LOADER level.

    `CollisionPreflightTest` covers a fresh, answerable board end to end through
    the CLI. These cover the states that cannot be reached hermetically that way:
    an aged board (which must trigger a REBUILD), a board that cannot answer, a
    rebuild that fails, and the classification of one lane.
    """

    def setUp(self) -> None:
        self.cp = _tool_module()
        self._tmp = tempfile.TemporaryDirectory(prefix="holder-gate-")
        self.tmp = Path(self._tmp.name)
        self.board = self.tmp / "fleet-state.json"
        patcher = mock.patch.object(self.cp.fleet_state, "DEFAULT_OUT", self.board)
        patcher.start()
        self.addCleanup(patcher.stop)
        # ⛔ THE CACHE SEAMS ARE STATEFUL, SO THEY ARE REMOVED FOR THE WHOLE CLASS
        # (review P2). `COLLISION_PREFLIGHT_BOARD_CACHE` exported into the suite
        # would make these tests order-dependent, provably: the first subtest of
        # `test_a_fresh_but_unanswerable_board_is_rebuilt_not_believed` rebuilds (and
        # CACHES) a board under this test's key, so every later subtest in the same
        # test would be served `rebuilt-cache` with `build_state` never called. A
        # test that wants the cache sets it itself, with `mock.patch.dict`.
        env_patch = mock.patch.dict(
            os.environ,
            {k: v for k, v in os.environ.items()
             if not k.startswith("COLLISION_PREFLIGHT_")},
            clear=True,
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    @staticmethod
    def _aged() -> dict:
        return _fleet_board_state(
            generated_at=(datetime.now(UTC) - timedelta(hours=1)).isoformat())

    def test_a_fresh_answerable_board_is_served_from_cache(self):
        _write_board(self.board, _fleet_board_state(
            issues={ISSUE: [("lane-live", "branch slug")]},
            lanes=[{"label": "lane-live", "live": True}],
        ))
        with mock.patch.object(self.cp.fleet_state, "build_state") as build:
            state, source, why = self.cp._read_board(SLUG, str(self.tmp))
        self.assertEqual(source, "cache")
        self.assertEqual(why, "")
        build.assert_not_called()
        self.assertEqual(
            [h.lane for h in self.cp._holders_from_board(state, ISSUE)],
            ["lane-live"],
        )

    def test_a_missing_board_is_rebuilt(self):
        self.assertFalse(self.board.exists())
        fresh = _fleet_board_state()
        with mock.patch.object(self.cp.fleet_state, "build_state",
                               return_value=fresh) as build:
            state, source, why = self.cp._read_board(SLUG, str(self.tmp))
        self.assertEqual(source, "rebuilt")
        self.assertEqual(why, "")
        self.assertIs(state, fresh)
        build.assert_called_once()

    # The gate's own cache directory, DERIVED from `fleet_state.DEFAULT_OUT` (patched to
    # `self.tmp/fleet-state.json` in `setUp`) — never named by the caller.
    @property
    def cache_dir(self) -> Path:
        return self.tmp / self.cp.BOARD_CACHE_DIRNAME

    def _cached_files(self) -> list[Path]:
        return sorted(self.cache_dir.glob("*.json")) if self.cache_dir.is_dir() else []

    def test_every_cache_seam_is_a_documented_row_in_the_header(self):
        # The module header's "Env seams" table IS the contract for every other
        # seam this tool reads, so the two cache seams are rows in it and are
        # reachable as module constants (an inline literal lets no test patch one
        # name and pin the contract).
        header = self.cp.__doc__
        for name in (self.cp.BOARD_CACHE_ENV, self.cp.BOARD_CACHE_TTL_ENV):
            self.assertIn(name, header, f"{name} is missing from the header's Env seams table")
        self.assertIn("OVERRIDES:", header,
                      "the derived-not-settable directory is a departure worth marking")

    def test_the_harness_neutralises_the_stateful_cache_seams(self):
        # Review P2, the harness half: the cache seams are the first STATEFUL
        # `COLLISION_PREFLIGHT_*` seams (the rest are a binary, a timeout or a limit),
        # so an inherited value makes the suite order-dependent. Exported into the
        # suite, `test_a_fresh_but_unanswerable_board_is_rebuilt_not_believed`'s first
        # subtest caches its rebuild under this test's key and every later subtest is
        # then served `rebuilt-cache` with `build_state` never called. `setUp` strips
        # them; this fails if that filter is removed while the var is set.
        for name in (self.cp.BOARD_CACHE_ENV, self.cp.BOARD_CACHE_TTL_ENV,
                     "COLLISION_PREFLIGHT_CACHE"):
            self.assertNotIn(name, os.environ, f"{name} leaked into the test environment")

    def test_a_rebuild_is_cached_for_the_pass_and_reused(self):
        # ⛔ THE PASS-SCOPED CACHE (agent-infra #1985 follow-up). A refresh pass runs this tool once
        # PER CANDIDATE, and the board rebuild measured 15.9 s of a 19 s run. Six candidates paid it
        # six times, which pushed passes into contention with the controller's own sweep; a contended
        # GitHub read is an UNQUERYABLE SURFACE — not a verdict — so the backlog never filled and
        # CREATE ACTIVITY STOPPED with no error anywhere. Measured after the fix: 16.7 s -> 3.4 s per
        # candidate, identical verdicts, one rebuild per pass.
        self.assertFalse(self.board.exists())
        fresh = _fleet_board_state()
        with mock.patch.dict(os.environ, {"COLLISION_PREFLIGHT_BOARD_CACHE": "1",
                                          "COLLISION_PREFLIGHT_BOARD_CACHE_TTL": "300"}), \
             mock.patch.object(self.cp.fleet_state, "build_state", return_value=fresh) as build:
            first_state, first_source, _ = self.cp._read_board(SLUG, str(self.tmp))
            second_state, second_source, _ = self.cp._read_board(SLUG, str(self.tmp))
        self.assertEqual(first_source, "rebuilt")
        self.assertEqual(second_source, "rebuilt-cache")
        self.assertEqual(second_state, first_state)
        build.assert_called_once()          # ← the whole point: ONE rebuild for the pass
        # ...and the entry lives in the DERIVED directory, beside the board the gate reads.
        self.assertEqual(len(self._cached_files()), 1)

    def test_the_cache_directory_is_derived_and_a_caller_cannot_name_the_board(self):
        # ⛔ REVIEW P1 — THE INVARIANT THAT MAKES THIS CACHE SAFE. A caller-selectable cache directory
        # is a caller-selectable BOARD: `_board_can_answer` / `_board_is_for_repo` / `state_freshness`
        # are fields read OUT of that same file, so a crafted board — fresh, answerable, correctly
        # stamped, listing no holder — would be served as a MEASURED "nobody holds it" and clear a
        # live holder (#3061 / #4256). This is the inverse of
        # `test_the_board_is_read_from_default_out_not_from_a_redirected_home`: here the hostile
        # artifact is the CACHE, and the live holder must win anyway.
        hostile = self.tmp / "hostile-cache"
        hostile.mkdir()
        # Exactly the path the caller-named version computed: sha1(key)[:16] + '.json'.
        key = f"partial_board|{SLUG}|{self.tmp}"
        planted = hostile / (hashlib.sha1(key.encode()).hexdigest()[:16] + ".json")
        planted.write_text(json.dumps(_fleet_board_state()))    # answerable, stamped, NO holder
        live = _fleet_board_state(
            issues={ISSUE: [("lane-live", "branch slug")]},
            lanes=[{"label": "lane-live", "live": True}],
        )
        with mock.patch.dict(os.environ, {"COLLISION_PREFLIGHT_BOARD_CACHE": "1",
                                          "COLLISION_PREFLIGHT_CACHE": str(hostile)}), \
             mock.patch.object(self.cp.fleet_state, "build_state", return_value=live) as build:
            state, source, why = self.cp._read_board(SLUG, str(self.tmp))
            # The pass cache DOES work — just not over the caller's board.
            _s2, source2, _ = self.cp._read_board(SLUG, str(self.tmp))
        self.assertEqual(why, "")
        self.assertEqual(source, "rebuilt",
                         "the caller-named cache directory must not answer the gate")
        self.assertEqual(source2, "rebuilt-cache")
        build.assert_called_once()
        self.assertEqual([h.lane for h in self.cp._holders_from_board(state, ISSUE)],
                         ["lane-live"])
        # ...and nothing was written into the caller-named directory, either.
        self.assertEqual([p.name for p in hostile.iterdir()], [planted.name])

    def test_the_board_key_is_per_clone_not_per_repo(self):
        # Review P3: the key carries repo_root as well as the slug. Two CLONES of one repo sharing a
        # cache directory must not serve each other's board — the holder attribution is computed
        # against THAT clone's local branches and worktrees, so clone A's board can hide clone B's
        # local-only branch (#4256).
        fresh = _fleet_board_state()
        with mock.patch.dict(os.environ, {"COLLISION_PREFLIGHT_BOARD_CACHE": "1"}), \
             mock.patch.object(self.cp.fleet_state, "build_state", return_value=fresh) as build:
            _s, first, _ = self.cp._read_board(SLUG, str(self.tmp))
            _s, other, _ = self.cp._read_board(SLUG, str(self.tmp / "other-clone"))
            _s, again, _ = self.cp._read_board(SLUG, str(self.tmp))
        self.assertEqual((first, other, again), ("rebuilt", "rebuilt", "rebuilt-cache"))
        self.assertEqual(build.call_count, 2)
        self.assertEqual(len(self._cached_files()), 2)

    def test_a_raised_ttl_cannot_serve_an_aged_board(self):
        # Review P2: the cache hit must be bounded by the BOARD'S OWN clock, not only by the TTL. With
        # TTL raised past fleet_state.STATE_FRESH_S, an aged board would otherwise be served as
        # `rebuilt-cache` — a lane that began work after the cached build is invisible in it, which
        # reads as "nobody is working this" about work that started minutes ago.
        fresh = _fleet_board_state()
        with mock.patch.dict(os.environ, {"COLLISION_PREFLIGHT_BOARD_CACHE": "1",
                                          "COLLISION_PREFLIGHT_BOARD_CACHE_TTL": "1e9"}), \
             mock.patch.object(self.cp.fleet_state, "build_state", return_value=fresh) as build:
            self.cp._read_board(SLUG, str(self.tmp))               # fills the cache
            # ⛔ POSITIVE CONTROL (review P2): without this, "the freshness clause works" and "the
            # cache was never written" are the SAME observation — the aging loop below would be a
            # silent no-op over zero files, and this test would pass on code with no cache at all.
            self.assertEqual(len(self._cached_files()), 1,
                             "the rebuild was not cached, so this test cannot be evidence")
            _s, hit_source, _ = self.cp._read_board(SLUG, str(self.tmp))
            self.assertEqual(hit_source, "rebuilt-cache")
            # age the CACHED BOARD itself past the freshness window (the rewrite also refreshes the
            # mtime, so ONLY the board's own `generated_at` can reject the hit)
            for f in self._cached_files():
                payload = json.loads(f.read_text())
                payload["generated_at"] = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
                f.write_text(json.dumps(payload))
            _state, source, _why = self.cp._read_board(SLUG, str(self.tmp))
        self.assertEqual(source, "rebuilt",
                         "an aged cached board must be REBUILT, never served, whatever the TTL says")
        self.assertEqual(build.call_count, 2)

    def test_the_ttl_seam_cannot_be_made_immortal(self):
        # Review P1: `nan`/`inf` are the trap values for a numeric seam — `inf <= 0` is False and
        # `x > nan` is False, so a mistyped export would otherwise make a cache entry immortal — and
        # a TTL ABOVE the board's own window would let the gate serve a board `fleet_state` itself
        # calls STALE. Neither is reachable: the effective TTL is finite, positive, and capped.
        window = float(self.cp.fleet_state.STATE_FRESH_S)
        for raw in ("inf", "-inf", "nan", "1e9", "86400"):
            with self.subTest(ttl=raw), \
                 mock.patch.dict(os.environ, {self.cp.BOARD_CACHE_TTL_ENV: raw}):
                ttl = self.cp._cache_ttl()
                self.assertTrue(math.isfinite(ttl))
                self.assertGreater(ttl, 0)
                self.assertLessEqual(ttl, window, f"TTL {raw!r} outlived the board's own window")
        # <= 0 still means OFF, and a malformed value falls back to the tightest accepted default.
        with mock.patch.dict(os.environ, {self.cp.BOARD_CACHE_TTL_ENV: "0"}):
            self.assertEqual(self.cp._cache_ttl(), 0.0)
        with mock.patch.dict(os.environ, {self.cp.BOARD_CACHE_TTL_ENV: "30m"}):
            self.assertEqual(self.cp._cache_ttl(), self.cp.BOARD_CACHE_TTL_DEFAULT)

    def test_without_the_env_the_cache_is_inert(self):
        # Default behaviour must be BYTE-IDENTICAL to before the change: no env, no caching.
        self.assertFalse(self.board.exists())
        fresh = _fleet_board_state()
        with mock.patch.object(self.cp.fleet_state, "build_state", return_value=fresh) as build:
            _s1, src1, _ = self.cp._read_board(SLUG, str(self.tmp))
            _s2, src2, _ = self.cp._read_board(SLUG, str(self.tmp))
        self.assertEqual((src1, src2), ("rebuilt", "rebuilt"))
        self.assertEqual(build.call_count, 2)
        self.assertEqual(self._cached_files(), [], "no env => nothing may be written at all")

    def test_the_cache_never_writes_the_shared_board(self):
        # The partial-board write-back stays forbidden: the cache is a PRIVATE artifact of the gate,
        # and `fleet_state.DEFAULT_OUT` must not appear or change.
        self.assertFalse(self.board.exists())
        fresh = _fleet_board_state()
        with mock.patch.dict(os.environ, {"COLLISION_PREFLIGHT_BOARD_CACHE": "1"}), \
             mock.patch.object(self.cp.fleet_state, "build_state", return_value=fresh):
            self.cp._read_board(SLUG, str(self.tmp))
            self.cp._read_board(SLUG, str(self.tmp))
        self.assertFalse(self.board.exists(),
                         "the gate must never write fleet_state.DEFAULT_OUT")

    def test_a_failed_cache_write_is_not_silent(self):
        # Review P2: a non-serialisable board would otherwise restore one rebuild per candidate — the
        # pre-fix cost — with nothing anywhere saying so. Dropping the cache is the safe direction;
        # doing it invisibly is not, so the put names itself on stderr.
        fresh = _fleet_board_state()
        fresh["unserialisable"] = object()
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, {"COLLISION_PREFLIGHT_BOARD_CACHE": "1"}), \
             mock.patch.object(self.cp.fleet_state, "build_state", return_value=fresh), \
             redirect_stderr(stderr):
            state, source, why = self.cp._read_board(SLUG, str(self.tmp))
        self.assertEqual((source, why), ("rebuilt", ""))       # this run's verdict is unaffected
        self.assertIs(state, fresh)
        self.assertIn("board cache write failed", stderr.getvalue())
        self.assertEqual(self._cached_files(), [])
        self.assertEqual([p for p in self.cache_dir.iterdir() if p.name.endswith(".tmp")], [],
                         "a failed put must not leave its temp file behind")

    def test_an_aged_board_is_rebuilt(self):
        # Serving an aged cache is the unsafe direction: a lane that began work
        # after the last build is invisible in it, which reads as "nobody is
        # working this" about work that started minutes ago.
        _write_board(self.board, self._aged())
        fresh = _fleet_board_state()
        with mock.patch.object(self.cp.fleet_state, "build_state",
                               return_value=fresh) as build:
            _state, source, _why = self.cp._read_board(SLUG, str(self.tmp))
        self.assertEqual(source, "rebuilt")
        build.assert_called_once()
        # ⛔ AND THE AGED CACHE IS LEFT ALONE. The gate is READ-ONLY: its own
        # rebuild is a PARTIAL board (no per-PR CI verdicts or review bindings),
        # so writing it back would degrade a shared artifact for its other
        # consumers. The stale stamp staying in place is the observable proof
        # that no write-back crept back in.
        self.assertNotEqual(json.loads(self.board.read_text())["generated_at"],
                            fresh["generated_at"])

    def test_a_fresh_but_unanswerable_board_is_rebuilt_not_believed(self):
        # Freshness is not the same fact as answerability. An unread registry
        # enumerates ZERO lanes and an unread GitHub side yields no open-issue
        # set, so both would answer "no holder" from a read that never happened.
        for label, kwargs in (("registry", {"registry_measured": False}),
                              ("github", {"github_measured": False}),
                              ("no reconciliation", {"reconciliation": False})):
            with self.subTest(case=label):
                _write_board(self.board, _fleet_board_state(**kwargs))
                with mock.patch.object(self.cp.fleet_state, "build_state",
                                       return_value=_fleet_board_state()) as build:
                    _state, source, why = self.cp._read_board(SLUG, str(self.tmp))
                self.assertEqual(source, "rebuilt")
                self.assertEqual(why, "")
                build.assert_called_once()

    def test_a_rebuild_that_still_cannot_answer_is_unmeasured(self):
        _write_board(self.board, _fleet_board_state(registry_measured=False))
        with mock.patch.object(self.cp.fleet_state, "build_state",
                               return_value=_fleet_board_state(github_measured=False)):
            state, source, why = self.cp._read_board(SLUG, str(self.tmp))
            self.assertIsNone(state)
            self.assertEqual(source, "unmeasured")
            self.assertIn("GitHub read failed", why)
            # `read_holders` is the single entry point, and it must carry the
            # unmeasured state through rather than answer "no holder".
            read = self.cp.read_holders(ISSUE, slug=SLUG, repo_root=str(self.tmp))
        self.assertFalse(read.measured)
        self.assertEqual(read.holders, ())
        self.assertIn("GitHub read failed", read.unanswerable)

    def test_a_rebuild_that_raises_is_unmeasured_never_no_holder(self):
        with mock.patch.object(self.cp.fleet_state, "build_state",
                               side_effect=RuntimeError("cmux vanished")):
            state, source, why = self.cp._read_board(SLUG, str(self.tmp))
        self.assertIsNone(state)
        self.assertEqual(source, "unmeasured")
        self.assertIn("could not be rebuilt", why)
        self.assertIn("RuntimeError", why)

    def test_a_corrupt_board_file_is_rebuilt_not_a_crash(self):
        self.board.parent.mkdir(parents=True, exist_ok=True)
        self.board.write_text("{ this is not json")
        with mock.patch.object(self.cp.fleet_state, "build_state",
                               return_value=_fleet_board_state()) as build:
            _state, source, _why = self.cp._read_board(SLUG, str(self.tmp))
        self.assertEqual(source, "rebuilt")
        build.assert_called_once()

    def test_an_unresolvable_repo_makes_no_board_at_all(self):
        # No target slug => no repo to build a board FOR. Unmeasured, and the
        # expensive rebuild is never attempted.
        with mock.patch.object(self.cp.fleet_state, "build_state") as build:
            state, source, why = self.cp._read_board(None, str(self.tmp))
        self.assertIsNone(state)
        self.assertEqual(source, "unmeasured")
        self.assertIn("could not be resolved", why)
        build.assert_not_called()

    def test_the_gate_never_writes_the_board_it_reads(self):
        # ⛔ A READ-ONLY GATE. `_read_board` rebuilds in memory when the cache is
        # stale, absent, unstamped or for another repo — and in EVERY case it
        # must leave the file byte-identical. The rebuilt board here is a
        # PARTIAL one (no CI verdicts / review bindings), so a write-back would
        # silently degrade a shared artifact (`fleet_state lane` prints
        # `ci=`/`review=`) for its other consumers.
        def snapshot() -> str | None:
            return self.board.read_text() if self.board.exists() else None

        other = _fleet_board_state(repo="daniel-ospina/agent-infra")
        fresh = _fleet_board_state(repo=SLUG)
        cases = (
            ("aged", self._aged()),
            ("absent", None),
            ("unstamped", _fleet_board_state(repo=None)),
            ("other repo", other),
        )
        for label, incumbent in cases:
            with self.subTest(case=label):
                if incumbent is None:
                    self.board.unlink(missing_ok=True)
                else:
                    _write_board(self.board, incumbent)
                before = snapshot()
                with mock.patch.object(self.cp.fleet_state, "build_state",
                                       return_value=fresh):
                    _state, source, _why = self.cp._read_board(SLUG, str(self.tmp))
                self.assertEqual(source, "rebuilt")
                self.assertEqual(snapshot(), before)

    def test_holder_classification_is_fail_closed(self):
        state = _fleet_board_state(
            issues={ISSUE: [("live", "e"), ("dead", "e"), ("unknown", "e"),
                            ("terminal-alive", "e"), ("gone", "e"), ("amb", "e")]},
            lanes=[
                {"label": "live", "live": True},
                {"label": "dead", "live": False},
                {"label": "unknown", "live": None},
                # A terminal workspace whose process OUTLIVED it is still a
                # RUNNING writer — the pid reading decides, not the workspace.
                {"label": "terminal-alive", "live": True,
                 "terminal": "RECOVERABLE"},
                {"label": "amb", "live": None, "bound": False,
                 "guess_refused": "shared"},
            ],
        )
        verdicts = {h.lane: h.verdict
                    for h in self.cp._holders_from_board(state, ISSUE)}
        self.assertEqual(verdicts["live"], self.cp.HOLDER_LIVE)
        self.assertEqual(verdicts["terminal-alive"], self.cp.HOLDER_LIVE)
        self.assertEqual(verdicts["dead"], self.cp.HOLDER_STOPPED)
        self.assertEqual(verdicts["unknown"], self.cp.HOLDER_UNMEASURED)
        self.assertEqual(verdicts["gone"], self.cp.HOLDER_UNMEASURED)
        self.assertEqual(verdicts["amb"], self.cp.HOLDER_AMBIGUOUS)

    def test_a_terminal_lane_whose_process_is_still_alive_is_LIVE(self):
        # ⛔ THE REGRESSION A REVIEW ROUND FOUND. `lane_live_map` marks a terminal
        # lane False EVEN WHEN A PROCESS OUTLIVED ITS WORKSPACE (#7738), and
        # `fleet_state.orphan_report` says so explicitly: "a terminal lane's
        # cmux workspace is GONE, which says nothing about whether a pi process
        # outlived it — a terminal lane CAN still carry `pid_alive: true`."
        #
        # On 2026-10-13 the live board held that exact shape: `π - land-lane-1`
        # (holder of #6151, the case this change exists to unfreeze) was terminal
        # RECOVERABLE with `pid_alive: True`. Reading the terminal verdict as
        # "stopped" would have reported a RUNNING lane as free, the one direction
        # the owner's ruling forbids. The pid reading is the evidence; the missing
        # workspace is not. This synthetic case is what pins the rule — the live
        # lane has since exited and now reports STOPPED.
        state = _fleet_board_state(
            issues={ISSUE: [("π - land-lane-1", "branch slug")]},
            lanes=[{"label": "π - land-lane-1", "live": True,
                    "terminal": "RECOVERABLE",
                    "reason": "cmux workspace 6AC480DF no longer exists"}],
        )
        (holder,) = self.cp._holders_from_board(state, ISSUE)
        self.assertEqual(holder.verdict, self.cp.HOLDER_LIVE)
        self.assertIn("LIVE pi process", holder.reason)

    def test_a_terminal_lane_with_no_pid_key_at_all_is_unmeasured(self):
        # The WIRE-IMPOSSIBLE shape: `build_state` always writes `session.pid_alive`,
        # so a session with NO pid key is a malformed board, and a malformed read
        # is UNMEASURED — never a stop.
        state = _fleet_board_state(
            issues={ISSUE: [("t", "e")]},
            lanes=[{"label": "t", "live": None, "absent_pid_key": True,
                    "terminal": "RETIRED", "reason": "no session file resolves"}],
        )
        (holder,) = self.cp._holders_from_board(state, ISSUE)
        self.assertEqual(holder.verdict, self.cp.HOLDER_UNMEASURED)

    def test_a_terminal_lane_measured_dead_is_stopped(self):
        # The real retired-lane shape: `pid_alive: False` measured, workspace
        # gone. That IS a positive "no live pi process" reading.
        state = _fleet_board_state(
            issues={ISSUE: [("t", "e")]},
            lanes=[{"label": "t", "live": False, "terminal": "RETIRED",
                    "reason": "no session file resolves"}],
        )
        (holder,) = self.cp._holders_from_board(state, ISSUE)
        self.assertEqual(holder.verdict, self.cp.HOLDER_STOPPED)
        self.assertIn("RETIRED", holder.reason)
        self.assertIn("NO live pi process", holder.reason)

    def test_a_board_that_could_not_read_the_session_store_cannot_answer(self):
        state = _fleet_board_state(liveness_unmeasured=True)
        why = self.cp._board_can_answer(state)
        self.assertIn("live-session store", why)
        # ... and a healthy board is unaffected.
        self.assertEqual(self.cp._board_can_answer(_fleet_board_state()), "")

    def test_the_refusal_names_which_live_session_store_failed(self):
        # #8999: the reader tries the plural cmux grammar, the guest/singular one and
        # the process table. When none answered, the refusal carries their causes, so
        # an operator is not left guessing which verb this cmux does not have.
        state = _fleet_board_state(liveness_unmeasured=True)
        state["reconciliation"]["liveness_error"] = (
            "`cmux sessions list --json --all` produced nothing; "
            "`ps -axo pid=,ppid=,command=` produced nothing")
        why = self.cp._board_can_answer(state)
        self.assertIn("live-session store", why)
        self.assertIn("cmux sessions list", why)
        self.assertIn("ps -axo", why)
        # An older board has no such field and must keep the bare sentence.
        self.assertNotIn("tried:", self.cp._board_can_answer(
            _fleet_board_state(liveness_unmeasured=True)))

    def test_a_cell_lane_started_outside_cmux_is_a_live_holder(self):
        # ⛔ THE #8999 ACCEPTANCE CRITERION — DRIVEN THROUGH THE REAL WRITER.
        # On a cell every lane is started by the `nohup` launch path, so no cmux
        # store lists it; the reader must publish a PROCESS lane (`PROC:<pid>`) from
        # the process table and the gate must read that lane as a LIVE holder — not
        # as an unreadable board — or the gate can never authorise anything on a
        # cell.
        #
        # ⛔ AND THE BOARD IS BUILT BY `build_state` ITSELF, NOT HAND-WRITTEN. The
        # previous version of this test FED the gate a `fleet-state.json` assembled
        # by a fixture: it asserted against a shape no PRE-FIX writer could produce
        # (`PROC:` lanes, `liveness_measured`), so it passed unchanged with the whole
        # fix reverted and proved nothing about it (review, cycle 4). Here the only
        # replaced pieces are the OS/host reads — the process table, `lsof`, cmux, the
        # registry, the board, the worktree list — so the lane minting, the
        # attribution and the verdict are the code under test.
        fs = self.cp.fleet_state
        cell_cwd = "/tmp/cell-t3/wt-3051"
        for name, fn in {
            "cmux_session_records": lambda: (
                [], "", "`cmux sessions list --json --all` produced nothing"),
            "pi_processes": lambda: ([{"pid": 4242, "ppid": 1}], ""),
            "process_cwds": lambda pids: ({4242: cell_cwd}, ""),
            # Not a real pid on the machine running the suite: the READ is the input.
            "pid_alive": lambda pid: pid == 4242,
            "load_registry": lambda: [],
            "cmux_workspaces": lambda: fs.WorkspaceMap(error="cmux: command not found"),
            "pane_bindings": lambda: {},
            "load_overrides": lambda: {},
            "session_index": lambda: ({}, {}),
            "worktrees": lambda _root: {
                cell_cwd: {"branch": "refs/heads/fix/3061-cell-lane", "head": "a" * 40}},
            "open_prs": lambda _repo, limit=400: fs.GitHubRows([], measured=True),
            "open_issues": lambda _repo, limit=fs.OPEN_ISSUES_LIMIT: fs.GitHubRows(
                [{"number": ISSUE}], measured=True),
        }.items():
            patcher = mock.patch.object(fs, name, fn)
            patcher.start()
            self.addCleanup(patcher.stop)
        state = fs.build_state(repo=SLUG, repo_root="/tmp", orch_ws="",
                               sample_s=0.0, do_pane=False)
        # The lane exists AT ALL — the half the hand-written fixture skipped.
        proc = [lane for lane in state["lanes"]
                if str(lane["identity"]["workspace"]).startswith(fs.PROC_WS_PREFIX)]
        self.assertEqual(len(proc), 1, "the nohup lane is enumerated exactly once")
        self.assertEqual(proc[0]["session"]["pid_alive"], True)
        self.assertEqual(proc[0]["identity"]["branch"], "fix/3061-cell-lane")
        self.assertEqual(state["index"]["issues"][str(ISSUE)]["lanes"],
                         [proc[0]["identity"]["lane"]])
        self.assertEqual(state["reconciliation"]["liveness_measured"], True)
        self.assertEqual(state["reconciliation"]["liveness_source"],
                         fs.LIVE_STORE_PROCESS)
        self.assertFalse([o for o in state["orphans"]
                          if o.get("kind") == "liveness-unmeasured"])
        # … and the GATE reads it as a live holder, at the loader level.
        self.assertEqual(self.cp._board_can_answer(state), "")
        (holder,) = self.cp._holders_from_board(state, ISSUE)
        self.assertEqual(holder.verdict, self.cp.HOLDER_LIVE)
        self.assertIn("proc-", holder.lane)

    def test_a_partially_read_process_store_makes_the_board_unanswerable(self):
        # ⛔ THE MIXED CASE, THROUGH THE GATE. `liveness_measured` used to be "at
        # least one store answered", so a cmux store that answered EMPTY outranked a
        # process store that had failed: the process lanes were dropped, no
        # `liveness-unmeasured` orphan was published, and this gate answered
        # "nobody holds it" — authorising a dispatch into a live checkout.
        #
        # Here `ps` saw two pids, one cwd was read, the other is still ALIVE and
        # unattributable. That lane may hold ISSUE, so no lane is a measured holder.
        fs = self.cp.fleet_state
        cell_cwd = "/tmp/cell-t3/wt-3051"
        for name, fn in {
            "cmux_session_records": lambda: (
                [], fs.LIVE_STORE_CMUX_PLURAL, ""),   # answered, and empty
            "pi_processes": lambda: (
                [{"pid": 4242, "ppid": 1}, {"pid": 4243, "ppid": 1}], ""),
            "process_cwds": lambda pids: ({4242: cell_cwd}, ""),
            "pid_alive": lambda pid: pid == 4243,
            "load_registry": lambda: [],
            "cmux_workspaces": lambda: fs.WorkspaceMap(error="cmux: command not found"),
            "pane_bindings": lambda: {},
            "load_overrides": lambda: {},
            "session_index": lambda: ({}, {}),
            "worktrees": lambda _root: {
                cell_cwd: {"branch": "refs/heads/fix/3061-cell-lane", "head": "a" * 40}},
            "open_prs": lambda _repo, limit=400: fs.GitHubRows([], measured=True),
            "open_issues": lambda _repo, limit=fs.OPEN_ISSUES_LIMIT: fs.GitHubRows(
                [{"number": ISSUE}], measured=True),
        }.items():
            patcher = mock.patch.object(fs, name, fn)
            patcher.start()
            self.addCleanup(patcher.stop)
        state = fs.build_state(repo=SLUG, repo_root="/tmp", orch_ws="",
                               sample_s=0.0, do_pane=False)
        rec = state["reconciliation"]
        self.assertEqual(rec["liveness_measured"], False)
        self.assertEqual(rec["liveness_source"], fs.LIVE_STORE_CMUX_PLURAL)
        self.assertIn("4243", rec["liveness_error"])
        self.assertTrue([o for o in state["orphans"]
                         if o.get("kind") == "liveness-unmeasured"])
        # The lane that WAS attributed is still evidence on the board …
        self.assertTrue([lane for lane in state["lanes"]
                         if str(lane["identity"]["workspace"]).startswith(
                             fs.PROC_WS_PREFIX)])
        # … and the gate refuses: an incomplete measurement is not a "nobody".
        why = self.cp._board_can_answer(state)
        self.assertIn("live-session store", why)
        self.assertIn("4243", why)

    def test_a_board_level_orphan_reaches_the_surface_as_unmeasured(self):
        # board that cannot answer is REBUILT by design, so a subprocess test for
        # this shape would run the board's own bare `gh`/`cmux` — a real, ~12 s,
        # machine-dependent rebuild that does not even exercise the orphan. Here
        # the rebuild is mocked and still orphaned, so the read must be
        # UNMEASURED and the surface note must say the board could not answer.
        orphan = _fleet_board_state(
            issues={ISSUE: [("lane-live", "branch slug")]},
            lanes=[{"label": "lane-live", "live": True}],
            liveness_unmeasured=True,
        )
        _write_board(self.board, orphan)
        with mock.patch.object(self.cp.fleet_state, "build_state",
                               return_value=orphan):
            read = self.cp.read_holders(ISSUE, slug=SLUG, repo_root=str(self.tmp))
        self.assertFalse(read.measured)
        self.assertEqual(read.holders, ())
        self.assertIn("live-session store", read.unanswerable)
        self.assertIn("could not answer", self.cp._holder_gate_note(read))

    def test_a_board_with_no_orphan_report_cannot_answer(self):
        # ⛔ THE P1 THE REVIEW FOUND. The board-level liveness guard used to be
        # `isinstance(orphans, (list, tuple)) and any(...)` — so a board that simply
        # OMITS `orphans` skipped it, leaving the gate to read per-lane
        # `pid_alive` values (which `build_state` writes as False BY DEFAULT when
        # the live-session store could not be read) as measurements. The key is
        # required now, the same polarity `open_issues_truncated` gets one field
        # up; and the per-lane rule needs no terminal arm for it to matter — a
        # terminal lane whose liveness is unmeasured is UNMEASURED either way.
        base = _fleet_board_state(
            issues={ISSUE: [("lane-t", "e")]},
            lanes=[{"label": "lane-t", "live": False, "pid_alive": False,
                    "pi_pid": None, "liveness_measured": False,
                    "terminal": "RECOVERABLE"}],
        )
        for bad in ("absent", None, {}, "", 0):
            with self.subTest(orphans=bad):
                state = json.loads(json.dumps(base))
                if bad == "absent":
                    state.pop("orphans")
                else:
                    state["orphans"] = bad
                why = self.cp._board_can_answer(state)
                self.assertIn("no usable orphan report", why)
        # The guarded shape is unchanged: no orphan objects (a readable session
        # store) answers, and the terminal lane is then a genuinely measured
        # "stopped" ONLY IF the board measured its liveness — which this lane's
        # `liveness_measured: False` never claims, so it stays UNMEASURED.
        self.assertEqual(self.cp._board_can_answer(base), "")
        (holder,) = self.cp._holders_from_board(base, ISSUE)
        self.assertEqual(holder.verdict, self.cp.HOLDER_UNMEASURED)
        # And with the writer's real orphan, the same lane is UNMEASURED for the
        # board-level reason instead.
        with_orphan = json.loads(json.dumps(base))
        with_orphan["orphans"] = [{"kind": "liveness-unmeasured",
                                   "reason": "live_sessions() raised"}]
        self.assertIn("live-session store", self.cp._board_can_answer(with_orphan))

    def test_a_lane_with_no_session_record_is_unmeasured_not_stopped(self):
        # ⛔ THE FALSE-CLEAN THE REVIEW FOUND. `build_state` writes
        # `session.pid_alive = bool(alive_pids)`, so a lane with NO session
        # record carries `pid_alive: False` BY DEFAULT while its
        # `liveness.liveness_measured` stays False. `fleet_state.lane_live_map`
        # reads that exact shape as `None` ("a lane with no pid evidence is
        # UNMEASURED here, not dead: there is nothing to call dead"), and
        # `free_reasons` says "no session record for this lane — liveness
        # UNMEASURED, not dead". Demoting on the default would report a writer
        # that was never observed as a stopped writer. The live board already
        # carries lanes in this shape (14 of 27 on 2026-10-14 — a snapshot, not a
        # pinned count).
        state = _fleet_board_state(
            issues={ISSUE: [("sessionless", "e")]},
            lanes=[{"label": "sessionless", "live": False, "pid_alive": False,
                    "pi_pid": None, "liveness_measured": False}],
        )
        (holder,) = self.cp._holders_from_board(state, ISSUE)
        self.assertEqual(holder.verdict, self.cp.HOLDER_UNMEASURED)
        self.assertIn("DEFAULT rather than a measurement", holder.reason)
        # The board itself agrees this lane is not dead.
        self.assertIsNone(self.cp.fleet_state.lane_live_map(state)["sessionless"])

    def test_a_terminal_lane_with_no_session_record_is_unmeasured_not_stopped(self):
        # ⛔ THE TERMINAL VERDICT IS NOT EVIDENCE ABOUT THE PROCESS. A terminal
        # workspace is GONE — `fleet_state.orphan_report`: "a terminal lane's cmux
        # workspace is GONE, which says nothing about whether a pi process
        # outlived it". A lane the board keeps NO session record for carries
        # `pid_alive: False` by DEFAULT, so demoting on the terminal verdict plus
        # that default would report an unmeasured writer as stopped: a wrong
        # CLEAN, and exactly the shape #7738 describes. (`lane_live_map` reads
        # this shape as `None`, not False.) The lane necessarily BLOCKS now — the
        # surface is INCOMPLETE, never CLEAN.
        state = _fleet_board_state(
            issues={ISSUE: [("retired", "e")]},
            lanes=[{"label": "retired", "live": False, "pid_alive": False,
                    "pi_pid": None, "liveness_measured": False,
                    "terminal": "RETIRED", "reason": "workspace gone"}],
        )
        (holder,) = self.cp._holders_from_board(state, ISSUE)
        self.assertEqual(holder.verdict, self.cp.HOLDER_UNMEASURED)
        self.assertIn("DEFAULT rather than a measurement", holder.reason)
        # Even with the board-level orphan absent (the P1 a review round found:
        # the guard used to be skippable by omitting the key), the lane does not
        # become a stop.
        stripped = json.loads(json.dumps(state))
        stripped.pop("orphans")
        self.cp._board_can_answer(stripped)  # must refuse, but not raise
        (holder,) = self.cp._holders_from_board(stripped, ISSUE)
        self.assertEqual(holder.verdict, self.cp.HOLDER_UNMEASURED)

    def test_a_terminal_lane_measured_dead_reports_the_workspace_in_the_reason(self):
        # The one terminal shape that IS a stop: the board MEASURED the lane's
        # liveness (a recorded `pi_pid` that is no longer alive) and the workspace
        # verdict then supplies the reason's detail — never the decision.
        state = _fleet_board_state(
            issues={ISSUE: [("gone", "e")]},
            lanes=[{"label": "gone", "live": False,
                    "terminal": "RETIRED", "reason": "workspace gone"}],
        )
        (holder,) = self.cp._holders_from_board(state, ISSUE)
        self.assertEqual(holder.verdict, self.cp.HOLDER_STOPPED)
        self.assertIn("MEASURED", holder.reason)
        self.assertIn("RETIRED", holder.reason)

    def test_a_non_boolean_pid_reading_is_unmeasured(self):
        # A malformed value must not be truthy-tested into a verdict. Note this
        # goes through `_lane_pid_alive`'s isinstance check, not `bool()`.
        state = _fleet_board_state(
            issues={ISSUE: [("odd", "e")]},
            lanes=[{"label": "odd", "live": False, "pid_alive": "yes"}],
        )
        (holder,) = self.cp._holders_from_board(state, ISSUE)
        self.assertEqual(holder.verdict, self.cp.HOLDER_UNMEASURED)

    def test_a_malformed_terminal_state_is_never_narrated_as_a_dead_workspace(self):
        # The verdict comes from the MEASUREMENT, never from the terminal verdict,
        # so a state the board does not emit must not be narrated as a dead
        # workspace either — a malformed or newer-schema read is not a fact.
        # `"ACTIVE"` is the case a value-shape check would miss, and `"retired"`
        # the case a case-insensitive one would.
        for bad in (True, 1, ["RECOVERABLE"], "   ", "ACTIVE", "retired"):
            with self.subTest(state=bad):
                state = _fleet_board_state(
                    issues={ISSUE: [("t", "e")]},
                    lanes=[{"label": "t", "live": False,
                            "terminal": "RECOVERABLE"}],
                )
                state["lanes"][0]["terminal"]["state"] = bad
                (holder,) = self.cp._holders_from_board(state, ISSUE)
                self.assertEqual(holder.verdict, self.cp.HOLDER_STOPPED)
                self.assertIn("MEASURED", holder.reason)
                self.assertNotIn("workspace is", holder.reason)
        # ...and a state the board DOES emit is narrated.
        good = _fleet_board_state(
            issues={ISSUE: [("t", "e")]},
            lanes=[{"label": "t", "live": False, "terminal": "RETIRED",
                    "reason": "workspace gone"}],
        )
        (holder,) = self.cp._holders_from_board(good, ISSUE)
        self.assertIn("its workspace is RETIRED: workspace gone", holder.reason)

    def test_a_malformed_bound_does_not_resolve_an_ambiguous_attribution(self):
        # `bound` is a real boolean on every board `build_state` writes (it is a
        # computed property), so only the literal `True` counts as RESOLVED. A
        # truthy non-bool must not skip the ambiguity check and let the lane
        # fall through to a demotion.
        state = _fleet_board_state(
            issues={ISSUE: [("t", "e")]},
            lanes=[{"label": "t", "live": False, "pid_alive": False,
                    "pi_pid": None, "liveness_measured": False,
                    "guess_refused": "dangling pane binding"}],
        )
        state["lanes"][0]["session"]["bound"] = "false"
        (holder,) = self.cp._holders_from_board(state, ISSUE)
        self.assertEqual(holder.verdict, self.cp.HOLDER_AMBIGUOUS)

    def test_a_rebuilt_board_for_another_repo_is_not_served_either(self):
        # The REBUILD path carries the same single-repo guard as the cache path:
        # a `build_state` that returns a board stamped for another repo must not
        # be believed. Unreachable under `build_state`'s own contract, which is
        # exactly why it is pinned - otherwise it reads as dead code.
        with mock.patch.object(self.cp.fleet_state, "build_state",
                               return_value=_fleet_board_state(repo="someone/else")):
            state, source, why = self.cp._read_board(SLUG, str(self.tmp))
        self.assertIsNone(state)
        self.assertEqual(source, "unmeasured")
        self.assertIn("stamped", why)

    def test_a_malformed_attribution_entry_is_not_a_clean_no_holder(self):
        # ⛔ ONE KEY BELOW `_board_can_answer`'s SHAPE CHECK. Absence is a
        # measured "nobody"; a MALFORMED entry is not, and
        # `_holders_from_board` collapses every unreadable entry to `()`. These
        # are the shapes a different writer (or a hand-edited cache) could leave.
        for entry in (["lane-live"], {}, {"lanes": "lane-live"},
                      {"lanes": [7]}, {"lanes": None}, {"lanes": []}):
            with self.subTest(entry=entry):
                state = _fleet_board_state()
                state["index"]["issues"][str(ISSUE)] = entry
                self.assertTrue(self.cp._issue_entry_problem(state, ISSUE))
        # A well-formed entry and a genuinely absent one are both READABLE.
        ok = _fleet_board_state(issues={ISSUE: [("lane", "e")]},
                                lanes=[{"label": "lane", "live": True}])
        self.assertEqual(self.cp._issue_entry_problem(ok, ISSUE), "")
        self.assertEqual(self.cp._issue_entry_problem(ok, ISSUE + 1), "")

    def test_read_holders_refuses_a_malformed_entry_rather_than_answering_nobody(self):
        # The wiring from the shape check to the surface: an unreadable entry must
        # arrive as an UNANSWERABLE read, not as `measured=True, holders=()`.
        state = _fleet_board_state()
        state["index"]["issues"][str(ISSUE)] = {"lanes": []}
        _write_board(self.board, state)
        read = self.cp.read_holders(ISSUE, slug=SLUG, repo_root=str(self.tmp))
        self.assertFalse(read.measured)
        self.assertEqual(read.holders, ())
        self.assertIn("no usable `lanes` list", read.unanswerable)

    def test_a_measured_lane_with_no_pid_evidence_is_unmeasured(self):
        # ⛔ `liveness_measured` ALONE IS NOT ENOUGH. `build_state` derives
        # `lane_measured` from the RECORDS but `pid_alive` from the pids that are
        # ALIVE, so a lane whose records carry no pid gets `liveness_measured:
        # True` with `pid_alive: False` and `pi_pid: None` — "no live pid" there
        # means there was no pid to be alive, not that a process stopped.
        # `lane_live_map` reads this shape as `None`; so must the gate.
        state = _fleet_board_state(
            issues={ISSUE: [("pidless", "e")]},
            lanes=[{"label": "pidless", "live": False, "pid_alive": False,
                    "pi_pid": None, "liveness_measured": True}],
        )
        (holder,) = self.cp._holders_from_board(state, ISSUE)
        self.assertEqual(holder.verdict, self.cp.HOLDER_UNMEASURED)
        self.assertIn("NO pid for it", holder.reason)
        self.assertNotIn("the board MEASURED", holder.reason)
        self.assertIsNone(self.cp.fleet_state.lane_live_map(state)["pidless"])

    def test_duplicate_lane_labels_are_unmeasured(self):
        # Two board records sharing a label make "which one holds the issue?"
        # unreadable. Reading the surviving (last) record would let a LIVE record
        # followed by a STOPPED one answer "stopped" — a false CLEAN.
        state = _fleet_board_state(issues={ISSUE: [("dupe", "e")]},
                                   lanes=[{"label": "dupe", "live": True}])
        stopped = _fleet_board_state(
            lanes=[{"label": "dupe", "live": False}])["lanes"][0]
        state["lanes"].append(stopped)
        (holder,) = self.cp._holders_from_board(state, ISSUE)
        self.assertEqual(holder.verdict, self.cp.HOLDER_UNMEASURED)
        self.assertIn("MORE THAN ONE lane record", holder.reason)

    def test_a_truncated_open_issue_read_cannot_prove_nobody(self):
        # ⛔ THE COVERAGE FAIL-OPEN. `index.issues` lists only issues WITH an
        # attribution, and attribution is filtered by the board's open-issue read
        # — so if that read hit its cap, an absent entry is not a measured "nobody"
        # for an issue outside the window (#8175).
        truncated = _fleet_board_state(open_issues_truncated=True)
        self.assertIn("OPEN-ISSUE read hit its cap",
                      self.cp._issue_entry_problem(truncated, ISSUE))
        # ...and the board is still ANSWERABLE (it is a complete board whose read
        # was narrow), so the refusal is scoped to the issues it could not cover
        # rather than forcing a rebuild on every run.
        self.assertEqual(self.cp._board_can_answer(truncated), "")
        _write_board(self.board, truncated)
        read = self.cp.read_holders(ISSUE, slug=SLUG, repo_root=str(self.tmp))
        self.assertFalse(read.measured)
        self.assertEqual(read.holders, ())
        self.assertIn("OPEN-ISSUE read hit its cap", read.unanswerable)
        # A PRESENT entry is still readable — truncation only affects ABSENCE.
        present = _fleet_board_state(
            issues={ISSUE: [("lane", "e")]},
            lanes=[{"label": "lane", "live": True}],
            open_issues_truncated=True,
        )
        self.assertEqual(self.cp._issue_entry_problem(present, ISSUE), "")

    def test_a_board_that_does_not_stamp_its_open_issue_coverage_is_rebuilt(self):
        # ⛔ THE ABSENT FLAG IS THE UNSAFE READING, not the safe one. The writer
        # this change replaces capped the same read at 800 while this repo has 989
        # open issues, so an UNSTAMPED board's coverage is UNKNOWN — reading its
        # absent entry as a measured "nobody" is the fail-open. Such a board is
        # refused (and rebuilt) rather than served; a legacy board with no
        # `reconciliation` at all is refused for the same reason, one key up.
        legacy = _fleet_board_state()
        legacy["reconciliation"].pop("open_issues_truncated")
        self.assertIn("does not record whether its OPEN-ISSUE read was truncated",
                      self.cp._board_can_answer(legacy))
        # The per-issue coverage reader refuses it too — this is the function whose
        # contract is coverage, so it does not depend on the caller having run the
        # board-level gate first.
        self.assertIn("does not record whether its OPEN-ISSUE read was truncated",
                      self.cp._issue_entry_problem(legacy, ISSUE))
        # A NON-BOOL flag is the same class: `is True` alone would read `1`/`"yes"`
        # as "not truncated" — the fail-open polarity in the opposite direction to
        # every other field in this gate.
        for bad in (1, "yes", [], [1], {}, None):
            with self.subTest(bad=bad):
                malformed = _fleet_board_state()
                malformed["reconciliation"]["open_issues_truncated"] = bad
                self.assertNotEqual(self.cp._board_can_answer(malformed), "")
                self.assertTrue(self.cp._issue_entry_problem(malformed, ISSUE))
        # An unstamped board reaches the surface as INCOMPLETE, and a rebuild that
        # cannot answer keeps it that way.
        _write_board(self.board, legacy)
        with mock.patch.object(self.cp.fleet_state, "build_state",
                               return_value=legacy):
            read = self.cp.read_holders(ISSUE, slug=SLUG, repo_root=str(self.tmp))
        self.assertFalse(read.measured)
        self.assertIn("does not record whether its OPEN-ISSUE read was truncated",
                      read.unanswerable)

    def test_the_board_is_read_from_default_out_not_from_a_redirected_home(self):
        # ⛔ THE REAL "NO DISMISSAL SWITCH" PIN, AND WHY IT CAN FAIL.
        # `fleet_state.DEFAULT_OUT` is `$HOME`-derived, so `$HOME` DOES move where
        # a board is looked for — the gate just adds no override OF ITS OWN. What
        # the code must never do is re-derive that path (or read one of the
        # guessed env var names) at READ time: a caller could then hand it a fresh
        # answerable board with no attribution and clear a live holder. Here
        # `DEFAULT_OUT` holds a LIVE lane while a redirected `HOME` holds a
        # complete-but-empty board — the verdict must still be the live holder.
        _write_board(self.board, _fleet_board_state(
            issues={ISSUE: [("lane-live", "branch slug")]},
            lanes=[{"label": "lane-live", "live": True}],
        ))
        home = self.tmp / "redirected-home"
        empty = _write_board(home / ".pi" / "agent" / "state" / "fleet-state.json",
                             _fleet_board_state())
        hostile = {
            "HOME": str(home),
            "COLLISION_PREFLIGHT_FLEET_STATE": str(empty),
            "FLEET_STATE_PATH": str(empty),
            "FLEET_STATE_OUT": str(empty),
        }
        with mock.patch.dict(os.environ, hostile):
            read = self.cp.read_holders(ISSUE, slug=SLUG, repo_root=str(self.tmp))
        self.assertTrue(read.measured)
        (holder,) = read.holders
        self.assertEqual(holder.verdict, self.cp.HOLDER_LIVE)
        self.assertEqual(holder.lane, "lane-live")

    def test_a_rebuild_that_returns_a_non_object_is_unmeasured(self):
        # The last fail-closed arm on the rebuild path: a build_state that
        # returns something that is not a board must not read as "no holders".
        with mock.patch.object(self.cp.fleet_state, "build_state",
                               return_value=[]):
            state, source, why = self.cp._read_board(SLUG, str(self.tmp))
        self.assertIsNone(state)
        self.assertEqual(source, "unmeasured")
        self.assertIn("no state object", why)

    def test_an_issue_with_no_attributed_lane_has_no_holder(self):
        state = _fleet_board_state(
            issues={ISSUE: [("lane", "e")]},
            lanes=[{"label": "lane", "live": True}],
        )
        self.assertEqual(self.cp._holders_from_board(state, ISSUE + 1), ())
        self.assertEqual(len(self.cp._holders_from_board(state, ISSUE)), 1)

    def test_board_answerability_requires_both_reads(self):
        self.assertEqual(self.cp._board_can_answer(_fleet_board_state()), "")
        # A truthy NON-BOOLEAN is not a measurement — the same rule the lane
        # fields use (`bound`, `liveness_measured`, `terminal.state`).
        for field in ("registry_measured", "github_measured"):
            with self.subTest(field=field):
                malformed = _fleet_board_state()
                malformed["reconciliation"][field] = "yes"
                self.assertTrue(self.cp._board_can_answer(malformed))
        for kwargs in ({"reconciliation": False},
                       {"registry_measured": False},
                       {"github_measured": False}):
            with self.subTest(kwargs=kwargs):
                self.assertTrue(self.cp._board_can_answer(
                    _fleet_board_state(**kwargs)))
        self.assertIn("lane registry", self.cp._board_can_answer(
            _fleet_board_state(registry_measured=False)))
        self.assertIn("GitHub read failed", self.cp._board_can_answer(
            _fleet_board_state(github_measured=False)))

    def test_the_holder_gate_note_says_what_was_read(self):
        note = self.cp._holder_gate_note(self.cp.HolderRead(True, "cache"))
        self.assertIn("NO lane is mechanically attributed", note)
        note = self.cp._holder_gate_note(
            self.cp.HolderRead(False, "unmeasured", unanswerable="board is gone"))
        self.assertIn("could not answer", note)
        self.assertIn("board is gone", note)
        note = self.cp._holder_gate_note(self.cp.HolderRead(
            True, "cache",
            holders=(self.cp.Holder("L", "e", self.cp.HOLDER_UNMEASURED,
                                    "why"),)))
        self.assertIn("1 liveness unmeasured", note)

    # ── #9392: an unprovisioned lane roster is a PROVISIONING gap ───────────

    def _roster_surface(self, error: str = ABSENT_ROSTER):
        """The claim-gate surface as the real run builds it, note included."""
        surface = self.cp.Surface("issue assignee/holder")
        surface.incomplete(self.cp._board_can_answer(
            _fleet_board_state(registry_measured=False, registry_error=error)))
        # …and the CAUSE beside the note, the way `read_holders` carries it
        # (`None` when the board recorded none — #9392 review P2).
        surface.registry_error = self.cp._board_registry_error(
            _fleet_board_state(registry_measured=False, registry_error=error))
        return surface

    def test_the_roster_marker_is_the_note_the_gate_emits(self):
        """The remedy partitions on this marker, so the two must be ONE string.

        A remedy keyed on prose that the note no longer contains would silently
        fall through to the generic arm — the defect itself, restored by a
        harmless-looking reword (#9392).
        """
        self.assertIn(self.cp._LANE_ROSTER_NOTE,
                      self._roster_surface().note)

    def test_an_unprovisioned_roster_remedy_names_provisioning_not_a_rebuild(self):
        """#9392: the exit-2 remedy must name the cause's owner.

        Measured pre-fix on the real tool (fresh HOME, no roster): the verdict
        line read `fix gh auth/network (or rebuild the fleet board) and re-run`
        — while `gh auth` answered every other surface in the same run and
        `fleet_state.py build` refused on the SAME read. Both remedies are dead
        ends for this cause, so the reader looped.
        """
        remedy = self.cp._incomplete_remedy([self._roster_surface()])
        self.assertIn("LANE ROSTER is UNKNOWN", remedy)
        self.assertIn("lane-registry.tsv", remedy)
        self.assertIn("NEVER PROVISIONED", remedy)
        self.assertIn("CANNOT create it", remedy)
        self.assertIn("PROVISIONING", remedy)
        self.assertIn("CELL step", remedy)
        # …and the two dead ends are NOT offered.
        self.assertNotIn("fix gh auth/network", remedy)
        self.assertNotIn("rebuild the fleet board", remedy.lower())

    def test_an_unreadable_roster_is_not_called_unprovisioned(self):
        """The second cause gets its own sentence: the file is there and the
        READ failed, so the fix is the file, not the cell (#9392)."""
        remedy = self.cp._incomplete_remedy([self._roster_surface(
            "PermissionError: [Errno 13] Permission denied: "
            "'/tmp/cell-home/.pi/agent/state/lane-registry.tsv'")])
        self.assertIn("EXISTS but could not be read", remedy)
        self.assertNotIn("NEVER PROVISIONED", remedy)
        self.assertNotIn("fix gh auth/network", remedy)

    def test_a_roster_with_no_recorded_cause_is_not_called_an_existing_file(self):
        """Three causes, three sentences — and the third one was unreachable
        (#9392 review P2).

        `_incomplete_remedy` handed the whole composed NOTE back to
        `registry_remedy`, and that note always ends with a remedy sentence, so
        the helper's `if error.strip()` arm always matched: a board that recorded
        NO cause still produced "the roster EXISTS but could not be read — fix the
        file itself (permissions/encoding)". That asserts a file that was never
        observed, and sends the reader to chmod something that may not exist —
        the same dead end the issue files, one cause over. The cause now travels
        as DATA (`Surface.registry_error`), so `None` reaches the third arm.
        """
        surface = self._roster_surface("")
        self.assertIsNone(surface.registry_error, (
            "the helper must say 'no cause recorded' when the board recorded none"))
        remedy = self.cp._incomplete_remedy([surface])
        self.assertIn("LANE ROSTER is UNKNOWN", remedy)
        self.assertIn("was not recorded", remedy)
        self.assertNotIn("EXISTS but could not be read", remedy)
        self.assertNotIn("NEVER PROVISIONED", remedy)

    def test_a_non_string_recorded_cause_takes_the_same_arm_everywhere(self):
        """One value, one answer — including when the value is not a cause.

        `_board_registry_error` collapses anything that is not a non-blank string
        to "no cause recorded", so a board carrying e.g. a dict must reach the
        SAME arm as a board carrying nothing. Without this pin the fixture
        coerced the value with `str()`, no test could construct a non-string
        cause, and the one-binding fix could be reverted silently (#9392
        review P3).
        """
        surface = self._roster_surface({"weird": 1})
        self.assertIsNone(surface.registry_error, (
            "a non-string cause is not a cause, and the exit-2 line and the note "
            "must agree that none was recorded"))
        remedy = self.cp._incomplete_remedy([surface])
        self.assertIn("was not recorded", remedy)
        self.assertNotIn("EXISTS but could not be read", remedy)

    def test_a_mixed_run_keeps_the_roster_cause_and_the_other_remedy(self):
        """Partitioned, not replaced: a run whose roster AND a list are
        unreadable must name BOTH, or the reader fixes one and returns (#9392)."""
        other = self.cp.Surface("open PRs")
        other.mark_truncated("the list was capped", widen=True)
        remedy = self.cp._incomplete_remedy([self._roster_surface(), other])
        self.assertIn("LANE ROSTER is UNKNOWN", remedy)
        self.assertIn("TRUNCATED list", remedy)

    def test_the_holder_gate_note_publishes_the_roster_size(self):
        """#9392's silent half. `board source` is the READ's provenance, never
        its coverage: a hand-written ONE-ROW roster printed exactly like a full
        one, so 11 live lanes went invisible and an INCOMPLETE flipped to CLEAN
        with no code change, silently. Publishing the counts makes a roster of
        one visibly a roster of one — disclosure, not a dismissal switch: the
        verdict still keys on `registry_measured`."""
        note = self.cp._holder_gate_note(self.cp.HolderRead(
            True, "cache", roster_rows=1, workspaces_count=11,
            workspaces_measured=True))
        self.assertIn("lane roster: 1 row(s); live cmux workspaces: 11", note)
        # An OLDER board publishes no counts. Absent is not zero: saying
        # "0 row(s)" would report an unmeasured roster as an empty one.
        self.assertNotIn("lane roster:",
                         self.cp._holder_gate_note(self.cp.HolderRead(True, "cache")))
        self.assertIsNone(self.cp._board_count({}, "registry_rows"))
        for bad in (True, -1, "3", 3.0, None):
            with self.subTest(value=bad):
                self.assertIsNone(
                    self.cp._board_count({"registry_rows": bad}, "registry_rows"))

    def test_an_unmeasured_workspace_read_never_prints_a_live_count(self):
        """⛔ EVERY failure path of `cmux_workspaces` returns an EMPTY map, so the
        count beside a FAILED read is `0` — an artefact, not a measurement.

        Printing it said "lane roster: 12 row(s) vs 0 live workspace(s)" in the
        documented fail-open state (#7738: the launchd beat cannot reach cmux),
        i.e. it asserted an EMPTY live fleet from a read that never happened and
        invited the reader to conclude all 12 rows are ghost lanes — the exact
        class #9392 exists to fix, in the sibling field (review P1).
        """
        unmeasured = self.cp.HolderRead(
            True, "cache", roster_rows=12, workspaces_count=0,
            workspaces_measured=False)
        note = self.cp._holder_gate_note(unmeasured)
        self.assertIn("lane roster: 12 row(s)", note)
        self.assertNotIn("live cmux workspaces", note)
        self.assertNotIn("vs 0", note)
        # The count itself is still threaded (the board MEASURED the roster); the
        # published NUMBER is what the failed read cannot support.
        self.assertEqual(unmeasured.workspaces_count, 0)

    def test_read_holders_threads_the_roster_size_from_the_board(self):
        _write_board(self.board, _fleet_board_state(registry_rows=1,
                                                    workspaces_count=11))
        read = self.cp.read_holders(ISSUE, slug=SLUG, repo_root=str(self.tmp))
        self.assertTrue(read.measured)
        self.assertEqual((read.roster_rows, read.workspaces_count), (1, 11))
        self.assertTrue(read.workspaces_measured)
        # An unmeasured board never carries a count to print.
        self.assertIsNone(self.cp.HolderRead(False, "unmeasured").roster_rows)

    def test_read_holders_carries_the_workspace_read_provenance(self):
        """The count is only as good as the read behind it (review P1)."""
        _write_board(self.board, _fleet_board_state(
            registry_rows=12, workspaces_count=0, workspaces_measured=False))
        read = self.cp.read_holders(ISSUE, slug=SLUG, repo_root=str(self.tmp))
        self.assertTrue(read.measured)
        self.assertFalse(read.workspaces_measured)
        self.assertNotIn("live cmux workspaces", self.cp._holder_gate_note(read))
        # An OLDER board that carries a count but NOT the flag is UNMEASURED:
        # the strict-true polarity, so the gate prints one fewer number rather
        # than a fact it cannot support.
        _write_board(self.board, _fleet_board_state(
            registry_rows=12, workspaces_count=3,
            workspaces_measured=None))
        stale = self.cp.read_holders(ISSUE, slug=SLUG, repo_root=str(self.tmp))
        self.assertTrue(stale.measured)
        self.assertFalse(stale.workspaces_measured)
        self.assertNotIn("live cmux workspaces", self.cp._holder_gate_note(stale))


    def test_the_claim_gate_never_clobbers_an_existing_incomplete_reason(self):
        # `scan_issue_surface` APPENDS to an existing INCOMPLETE note rather than
        # overwriting it, because `incomplete()` replaces `note` outright. Today's
        # call site cannot pass an already-incomplete surface (both other
        # `incomplete()` sites leave `issue_data` None, so this function is not
        # called on them), but the invariant is what stops a future caller from
        # losing the first reason the run is incomplete.
        surface = self.cp.Surface(self.cp.SURFACE_ISSUE)
        surface.incomplete("target-repo-unresolved: no owner/name")
        read = self.cp.HolderRead(False, "unmeasured", unanswerable="board is gone")
        self.cp.scan_issue_surface(surface, {"assignees": [], "state": "OPEN"},
                                   self.cp.Identity(), read)
        self.assertEqual(surface.status, self.cp.STATUS_INCOMPLETE)
        self.assertIn("target-repo-unresolved", surface.note)
        self.assertIn("ALSO", surface.note)
        self.assertIn("board is gone", surface.note)

    def test_a_fresh_board_for_another_repo_is_rebuilt_not_served(self):
        # ⛔ THE BOARD IS SINGLE-REPO EVIDENCE. `index.issues` is keyed by a BARE
        # issue number and attributed against THAT board's own open-issue set, so
        # serving a fresh board built for another repo could both FABRICATE a
        # holder (the other repo's #N) and HIDE one (this repo's #N, whose only
        # signal is a lane's own session text). `--repo owner/name` cross-repo
        # pre-flights are a supported invocation, so this is reachable.
        _write_board(self.board, _fleet_board_state(
            repo="daniel-ospina/agent-infra",
            issues={ISSUE: [("lane-other", "branch slug")]},
            lanes=[{"label": "lane-other", "live": True}],
        ))
        fresh = _fleet_board_state(repo=SLUG)
        with mock.patch.object(self.cp.fleet_state, "build_state",
                               return_value=fresh) as build:
            state, source, why = self.cp._read_board(SLUG, str(self.tmp))
        self.assertEqual(source, "rebuilt")
        self.assertEqual(why, "")
        build.assert_called_once()
        # The other repo's holder is NOT attributed to this repo's #N.
        self.assertEqual(self.cp._holders_from_board(state, ISSUE), ())
        # ⛔ AND THE OTHER REPO'S CACHE IS LEFT ALONE. The cache's OTHER readers
        # (`fleet_state.py who`, `_load_or_build`, the orchestrator's tooling) do
        # not check the `repo` stamp — they read `index.issues` by bare number —
        # so overwriting it would trade one wrong-repo answer for another's. The
        # rebuilt board is served from memory instead.
        self.assertEqual(
            json.loads(self.board.read_text())["repo"],
            "daniel-ospina/agent-infra",
        )

    def test_a_board_without_a_repo_stamp_is_rebuilt(self):
        # No stamp is the same answer as a wrong one: rebuild, which is the
        # fail-closed direction.
        _write_board(self.board, _fleet_board_state(repo=None))
        with mock.patch.object(self.cp.fleet_state, "build_state",
                               return_value=_fleet_board_state(repo=SLUG)) as build:
            _state, source, _why = self.cp._read_board(SLUG, str(self.tmp))
        self.assertEqual(source, "rebuilt")
        build.assert_called_once()
        # ... and the unstamped cache is NOT rewritten (see
        # `test_the_gate_never_writes_the_board_it_reads`).
        self.assertIsNone(json.loads(self.board.read_text())["repo"])

    def test_a_board_whose_answer_shape_is_missing_cannot_answer(self):
        # The answer is read OUT of `index.issues`; a "measured" board without a
        # usable one would make `_holders_from_board` return `()` — absence read
        # as emptiness, one key over from the provenance checks.
        cases = {
            "no index": lambda s: s.pop("index"),
            "index not a dict": lambda s: s.__setitem__("index", []),
            "index.issues not a dict": lambda s: s["index"].__setitem__("issues", []),
            "no lanes": lambda s: s.pop("lanes"),
        }
        for label, mutate in cases.items():
            with self.subTest(case=label):
                state = _fleet_board_state()
                mutate(state)
                self.assertTrue(self.cp._board_can_answer(state), label)
        # ...and the untouched shape still answers.
        self.assertEqual(self.cp._board_can_answer(_fleet_board_state()), "")

    def test_a_resolved_lane_with_a_dangling_binding_note_is_not_ambiguous(self):
        # `resolve_sessions` copies a DANGLING pane-binding note onto a
        # resolution a LATER rule then RESOLVED (`bound=True`, a measurable
        # liveness), so the note alone does NOT mean the board refused to guess.
        # Reading it as ambiguous would turn a definite holder into exit 2 and
        # print a false reason ("refused to guess").
        state = _fleet_board_state(
            issues={ISSUE: [("resolved", "e"), ("refused", "e")]},
            lanes=[
                {"label": "resolved", "live": True,
                 "guess_refused": "pane binding deadbeef… has NO file on disk"},
                {"label": "refused", "live": None, "bound": False,
                 "guess_refused": "2 disk candidates"},
            ],
        )
        verdicts = {h.lane: h.verdict
                    for h in self.cp._holders_from_board(state, ISSUE)}
        self.assertEqual(verdicts["resolved"], self.cp.HOLDER_LIVE)
        self.assertEqual(verdicts["refused"], self.cp.HOLDER_AMBIGUOUS)


class EscapeStripperTests(unittest.TestCase):
    """`_strip_control_sequences` directly (#4027 P2-J).

    It is still load-bearing after #8175 deleted the prose path: it decides the
    issue's TERMINAL reading (`scan_landed_surface` and `run_preflight` compare
    the de-sequenced `state` to `OPEN`), so a stripper regression can make the
    landed row and the issue row disagree about the same value.
    """

    def test_it_removes_whole_sequences_and_keeps_what_follows_a_bare_esc(self):
        cp = _tool_module()
        cases = {
            # ⛔ THE LOAD-BEARING CASE: a bare ESC must not CONSUME the next
            # character. An `\x1b.` fallback alternative did exactly that, so a
            # body's or a value's first real character vanished.
            "bare-ESC before a space": ("\x1b now", " now"),
            "bare-ESC before a letter": ("\x1bon it now", "on it now"),
            "bare-ESC between word chars": ("x\x1by", "xy"),
            "CSI removed whole": ("a\x1b[2Kb", "ab"),
            "OSC 52 removed whole": ("a\x1b]52;c;AAAA\x07b", "ab"),
            "BEL removed": ("a\x07b", "ab"),
            "tab/newline/CR are KEPT": ("a\tb\nc\rd", "a\tb\nc\rd"),
        }
        for name, (raw, want) in cases.items():
            with self.subTest(case=name):
                self.assertEqual(cp._strip_control_sequences(raw), want, name)


class BranchTerminalLookupTests(unittest.TestCase):
    """#5485: the TARGETED terminal lookup, and the two arms that close the gap.

    Why it exists: every terminal test in the tool read data already in hand, so
    a squash-merge older than `CLOSED_PR_LIMIT` (100) was invisible — and
    `refs/heads/fix/6134-6146-6151-graph-correctness` therefore blocked #6151
    permanently, because its PR #6156 merged 2026-09-28 (measured 2026-10-06).
    The branch had 1 commit ahead of main, 0 worktrees, no open PR, and no
    assignee: it was the one row the priority stack labels UNHELD, held by
    nothing but a stale ref.
    """

    SHA = "adcfedc4221329a0f5ae9079248b3b0261134a73"
    PR_HEAD = "a749c662d075a5dc0c96560b21b784578e5c1d63"
    MAIN_TIP = "b9c1a1323000000000000000000000000000000000"
    BRANCH = "refs/heads/fix/6134-6146-6151-graph-correctness"
    SHORT = "fix/6134-6146-6151-graph-correctness"

    @classmethod
    def setUpClass(cls):
        import importlib.util
        path = Path(__file__).resolve().parents[1] / "tools" / "collision_preflight.py"
        spec = importlib.util.spec_from_file_location("cp_under_test", path)
        cls.mod = importlib.util.module_from_spec(spec)
        sys.modules["cp_under_test"] = cls.mod
        spec.loader.exec_module(cls.mod)

    def _call(self, prs, status="ahead", sha=None, main_tip=None, raises=False,
              first_parent=None, ancestor_merged=frozenset(), branch=None):
        """Drive the real function with `_gh_json` stubbed at the boundary.

        `ancestor_merged` defaults to an EMPTY frozen set, i.e. "the `--merged`
        walk RAN and this ref was not in it" — the ordinary production state.
        `None` is the different, deliberate state where that walk could NOT run,
        and the function must then refuse the demotion (fail-closed); pass it
        explicitly to pin that arm. (`frozenset()` rather than `set()` only to
        avoid a mutable default.)

        The stub records every `args` list it is called with (`self.seen`) so a
        test can pin the REQUEST, not just the response: a projection or filter
        regression would otherwise make these arms silently inert while every
        response-shaped assertion still passed (the class PR #5595's cycle 1
        raised on this same file).
        """
        self.seen = []

        def fake(_gh, args, _cwd, _timeout):
            self.seen.append(list(args))
            if raises:
                raise self.mod.SurfaceError("gh failed")
            if any("compare" in a for a in args):
                return {"status": status}
            return prs

        with mock.patch.object(self.mod, "_gh_json", side_effect=fake):
            return self.mod._branch_terminal_state_from_prs(
                "gh", "daniel-ospina/tortoise", ".", 30.0,
                self.BRANCH if branch is None else branch,
                self.SHA if sha is None else sha,
                self.MAIN_TIP if main_tip is None else main_tip,
                first_parent=first_parent,
                ancestor_merged=ancestor_merged,
            )

    def _merged_pr(self, head_sha):
        return [{"number": 6156, "state": "closed", "headSha": head_sha,
                 "mergedAt": "2026-09-29T00:03:52Z"}]

    def test_exact_tip_match_demotes(self):
        # Predicate 1, carried past the sample's edge.
        reason = self._call(self._merged_pr(self.SHA))
        self.assertIsNotNone(reason, "an exact head match must demote")
        self.assertIn("#6156", reason)

    def test_a_branch_advanced_past_its_merged_pr_head_keeps_blocking(self):
        # ⛔ THE FALSE NEGATIVE THIS ARM INVITES, and the one #10174's brief says
        # to pin: the lookup is scoped by BRANCH NAME (`head=<owner>:<branch>`),
        # so "a merged PR exists for this ref" is TRUE for a branch that was
        # merged at X and has since ADVANCED to Y. Tip equality is what refuses,
        # and containment cannot rescue it either: Y is a DESCENDANT of the PR
        # head, so `compare/{tip}...{head}` reports `behind` — the opposite of the
        # `ahead` containment accepts (see the polarity test above).
        #
        # SENSITIVITY: mutation-verified — replacing the tip equality + containment
        # with "the first MERGED PR this name-scoped request returned" makes this
        # test fail while every other test in the class stays green.
        older_tip = "aa" * 20
        for status in ("behind", "diverged"):
            with self.subTest(status=status):
                self.assertIsNone(
                    self._call(self._merged_pr(older_tip), status=status),
                    "a ref that moved PAST its merged head is not the merged artifact",
                )

    def test_containment_demotes_when_the_pr_head_is_not_the_tip(self):
        # ⛔ THE CASE #6151 ACTUALLY HIT, and the reason equality alone does not
        # close #5485's half: a merged PR's head is often a MERGE COMMIT (main
        # merged into the branch) or an amend, so the local tip is its ANCESTOR.
        # Equality refuses it; containment is what proves the work landed.
        reason = self._call(self._merged_pr(self.PR_HEAD))
        self.assertIsNotNone(reason, "a contained tip must demote")
        self.assertIn("contained in the MERGED PR #6156", reason)

    def test_compare_polarity_is_ahead_not_behind(self):
        # ⛔ THE TRAP THAT MADE THE FIRST IMPLEMENTATION SILENTLY INERT: for
        # `compare/{base}...{head}` the status describes the HEAD relative to the
        # BASE, so `tip` being an ancestor of `head_sha` is reported as AHEAD.
        # Reading it as `behind` meant every containment failed, the arm never
        # fired, and the run looked like a pass (#6151 still returned COLLISION).
        for status in ("behind", "diverged"):
            with self.subTest(status=status):
                self.assertIsNone(
                    self._call(self._merged_pr(self.PR_HEAD), status=status),
                    f"status={status} is NOT containment and must keep blocking",
                )
        self.assertIsNotNone(
            self._call(self._merged_pr(self.PR_HEAD), status="ahead")
        )

    def test_fresh_branch_sitting_on_a_merged_head_never_demotes(self):
        # The fail-OPEN this test exists to prevent: a lane that has just created
        # its worktree sits on main's tip, and if that tip is itself a merged
        # PR's head (fast-forward or empty-diff landing) an unguarded arm would
        # call a branch with NO COMMITS OF ITS OWN terminal — a live lane read as
        # free, which is the dangerous direction for every demotion here.
        self.assertIsNone(
            self._call(self._merged_pr(self.SHA), sha=self.SHA, main_tip=self.SHA),
            "tip == main's tip must never demote",
        )

    def test_closed_unmerged_pr_does_not_demote(self):
        # An abandoned PR is not a landing. `_pr_terminal_state` names it
        # "closed"; only "merged" may demote.
        prs = [{"number": 6156, "state": "closed", "headSha": self.SHA,
                "mergedAt": ""}]
        self.assertIsNone(self._call(prs))

    def test_missing_head_sha_does_not_crash_and_does_not_demote(self):
        self.assertIsNone(self._call(self._merged_pr("")))

    def test_tip_on_mains_first_parent_chain_never_demotes(self):
        # ⛔ THE P0 BOTH REVIEWERS FOUND, and the reason `sha != main_tip` is not
        # enough: `_branch_terminal_state_from_prs` is reached precisely when
        # `_branch_terminal_state` has ALREADY refused, so any guard omitted here
        # is bypassed BY CONSTRUCTION. A lane that merely CLAIMED the issue by
        # creating `fix/<N>-…` sits on a main-line commit; that commit can be a
        # merged PR's head (fast-forward / rebase / empty-diff landing); and ONE
        # commit of drift makes the weak guard pass. Predicate 1 refuses on the
        # first-parent witness for exactly this state, so this arm must too —
        # otherwise a branch with no commits of its own is read terminal, i.e. a
        # live lane reported free.
        self.assertIsNone(
            self._call(self._merged_pr(self.SHA), first_parent={self.SHA}),
            "a tip on main's first-parent chain must never demote",
        )

    def test_tip_in_the_merged_fallback_set_never_demotes(self):
        # The second half of predicate 1's exclusion: with the first-parent
        # witness UNAVAILABLE, a tip in the `--merged` walk's set is still a
        # commit main already contains, so it cannot be an absorbed branch head.
        # `ancestor_merged` holds FULL refnames (`for-each-ref %(refname)`), which
        # is the shape the production call site passes — pinned here, and in both
        # spellings below, so the guard cannot be dodged by how a caller spells
        # the ref.
        for spelling in (self.BRANCH, self.SHORT):
            with self.subTest(branch=spelling):
                self.assertIsNone(
                    self._call(
                        self._merged_pr(self.SHA),
                        first_parent=None,
                        ancestor_merged={self.BRANCH},
                        branch=spelling,
                    ),
                    "a tip in the --merged set must never demote",
                )

    def test_the_lookup_is_shape_agnostic_for_the_ref_name(self):
        # ⛔ Review round 2: the guard compares against `ancestor_merged`, which is
        # a set of REFNAMES, while the URL needs the SHORT branch name. The one
        # production caller happens to pass the full refname, so the equivalence
        # rested on an undocumented coincidence — and a caller (or refactor) passing
        # the natural short name would have made the guard compare a short name
        # against refnames, never match, and re-open the fail-open with a GREEN
        # suite. The function now normalises, so BOTH spellings must behave
        # identically on the wire AND behind the guard.
        for spelling in (self.BRANCH, self.SHORT):
            with self.subTest(branch=spelling):
                self.assertIsNotNone(
                    self._call(self._merged_pr(self.SHA), branch=spelling),
                    "the demotion must not depend on how the ref is spelled",
                )
                url = next(a for a in self.seen[0] if "pulls?" in a)
                self.assertIn("head=daniel-ospina:fix/6134-", url)
                self.assertNotIn("refs%2Fheads", url)
                self.assertNotIn("refs/heads", url)

    def test_a_full_page_is_refused_rather_than_read_as_not_found(self):
        # `sort=created desc` puts the OLDEST PR last — so the merged PR this
        # lookup exists to find is exactly what falls off a full page. Reading
        # that as "not found" would re-create the very bug #5485 removes, while
        # looking like a completed lookup.
        page = [self._merged_pr("aa" * 20)[0]] * self.mod._TARGETED_PR_PAGE
        with self.assertRaises(self.mod.SurfaceError):
            self._call(page)

    def test_a_missing_merged_walk_refuses_rather_than_demoting(self):
        # The third clause of predicate 1's exclusion, and the state it exists for:
        # main's tip is known, the first-parent witness is NOT readable, and the
        # `--merged` walk could not run either. Then `sha != main_tip` is the only
        # witness left — one commit deep — so the demotion is REFUSED. This is the
        # fail-closed direction and it must survive any refactor of the guards.
        self.assertIsNone(
            self._call(
                self._merged_pr(self.SHA),
                first_parent=None,
                ancestor_merged=None,
            ),
            "with no witness available the demotion must be refused",
        )

    def test_gh_failure_propagates_so_the_caller_can_report_it(self):
        # ⛔ THE DEAD-CODE DEFECT THE REVIEW FOUND: swallowing `SurfaceError` here
        # made the caller's `except` unreachable, so `targeted_failures` was always
        # 0 and the promised "⚠ … could not run" note could never print — leaving
        # "I could not ask" indistinguishable from "there is no such PR". The
        # failure must PROPAGATE; the caller counts it and the ref keeps blocking.
        with self.assertRaises(self.mod.SurfaceError):
            self._call(self._merged_pr(self.SHA), raises=True)

    def test_a_non_list_payload_is_refused_not_demoted(self):
        with self.assertRaises(self.mod.SurfaceError):
            self._call("not-a-list")

    def test_the_request_is_scoped_sorted_and_percent_encoded(self):
        # Pins the WIRE, not the response. Both halves are load-bearing:
        #  * a ref may legally contain `&`, `#`, `+`, `%` (git check-ref-format),
        #    so an unencoded name like `fix/N&head=owner:other` would append a
        #    SECOND `head=` parameter — honored LAST by the API — and the lookup
        #    would be answered by another branch's PRs, breaking the scoping the
        #    demotion rests on;
        #  * `sort=created desc` is what makes the page cap meaningful.
        hostile = "fix/6151&head=daniel-ospina:some-other-branch#frag"
        self._call(self._merged_pr(self.SHA), branch=hostile)
        url = next(a for a in self.seen[0] if "pulls?" in a)
        self.assertNotIn("&head=daniel-ospina:some-other-branch", url)
        self.assertIn("%26", url)
        self.assertIn("%23", url)
        self.assertIn("state=all", url)
        self.assertIn("sort=created", url)
        self.assertIn("direction=desc", url)
        # ⛔ `per_page` is UNPINNED COUPLING unless asserted here: the same constant
        # is both the page size and the truncation bound, so a request that drifts
        # below it can never fill the page, `test_a_full_page_is_refused…` would
        # still pass on its fabricated fixture, and the refusal would be dead code
        # while an off-page merged PR silently read as "not found" (#5485 again).
        self.assertIn(f"per_page={self.mod._TARGETED_PR_PAGE}", url)
        # And the projection the arms read is still requested.
        self.assertIn("headSha", " ".join(self.seen[0]))


class GitRepoDirsUnreadableEntryTests(unittest.TestCase):
    """#8650: an unreadable SIBLING must be skipped, not abort the scan.

    `_git_repo_dirs` already `except OSError: continue`d around `iterdir()`, so an
    unreadable ROOT was skipped — but the per-ENTRY `.git` probe was unguarded, and
    `Path.exists()` propagates `EACCES` (it swallows ENOENT/ENOTDIR/EBADF/ELOOP
    only). A stat-able-but-untraversable sibling — the root-owned `drwx------`
    `/tmp/cmux-blesh-runtime-0` on a cmux cloud cell, where `repo_roots()` is
    `/tmp` — therefore raised out of the scan and aborted the pre-flight on the
    `--repo owner/name` slug form: `find_local_clone` exited 1 (the COLLISION
    code) with EMPTY stdout and no `VERDICT:` line. (The controller's
    `refresh-candidates.sh` uses `--repo .`, the PATH branch, which never reaches
    `find_local_clone` — measured, not exposed to this crash.)

    Both of the first two tests FAIL against the pre-fix code (the probe raises).
    The last two pin the boundary from both sides: a non-`OSError` still propagates,
    and a non-`EACCES` `OSError` is still swallowed (the guard is `except OSError`,
    not `except PermissionError`).
    """

    def test_an_unreadable_entry_is_skipped_and_readable_clones_survive(self):
        with tempfile.TemporaryDirectory(prefix="cp-roots-") as tmp:
            root = Path(tmp)
            good = root / "good-repo"
            (good / ".git").mkdir(parents=True)
            locked = root / "locked-repo"
            (locked / ".git").mkdir(parents=True)
            if os.geteuid() == 0:
                self.skipTest("root ignores mode bits; the mock test covers it")
            locked.chmod(0o000)
            try:
                dirs = _tool_module()._git_repo_dirs([str(root)])
            finally:
                locked.chmod(0o755)          # let TemporaryDirectory clean up
            self.assertEqual(dirs, [str(good)])

    def test_a_permission_error_on_the_entry_probe_is_skipped_not_raised(self):
        """Root-proof twin of the test above: the guard must catch `OSError`
        regardless of whether the filesystem enforces mode bits for this uid."""
        with tempfile.TemporaryDirectory(prefix="cp-roots-") as tmp:
            root = Path(tmp)
            (root / "good-repo" / ".git").mkdir(parents=True)
            (root / "locked-repo" / ".git").mkdir(parents=True)
            real_exists = Path.exists

            def raising_exists(self, *args, **kwargs):
                if self.name == ".git" and self.parent.name == "locked-repo":
                    raise PermissionError(13, "Permission denied", str(self))
                return real_exists(self, *args, **kwargs)

            with mock.patch.object(Path, "exists", raising_exists):
                dirs = _tool_module()._git_repo_dirs([str(root)])
            self.assertEqual(dirs, [str(root / "good-repo")])

    def test_a_non_oserror_failure_is_not_swallowed(self):
        """The guard is `except OSError` — a genuine defect in the probe (here a
        `RuntimeError`) must still surface, so the skip cannot hide a bug."""
        with tempfile.TemporaryDirectory(prefix="cp-roots-") as tmp:
            root = Path(tmp)
            (root / "good-repo" / ".git").mkdir(parents=True)
            real_exists = Path.exists

            def exploding_exists(self, *args, **kwargs):
                if self.name == ".git" and self.parent.name == "good-repo":
                    raise RuntimeError("bug in the probe")
                return real_exists(self, *args, **kwargs)

            with (
                mock.patch.object(Path, "exists", exploding_exists),
                self.assertRaises(RuntimeError),
            ):
                _tool_module()._git_repo_dirs([str(root)])

    def test_a_non_permission_oserror_on_the_entry_probe_is_also_skipped(self):
        """The other side of the same boundary: the guard is `except OSError`, not
        `except PermissionError`. #8650's recorded decision is to treat EVERY
        `OSError` on the entry probe as "not a clone" (fail-closed downstream),
        so narrowing the guard to `PermissionError` must fail this test."""
        with tempfile.TemporaryDirectory(prefix="cp-roots-") as tmp:
            root = Path(tmp)
            (root / "good-repo" / ".git").mkdir(parents=True)
            (root / "locked-repo" / ".git").mkdir(parents=True)
            real_exists = Path.exists

            def io_error_exists(self, *args, **kwargs):
                if self.name == ".git" and self.parent.name == "locked-repo":
                    raise OSError(5, "Input/output error", str(self))
                return real_exists(self, *args, **kwargs)

            with mock.patch.object(Path, "exists", io_error_exists):
                dirs = _tool_module()._git_repo_dirs([str(root)])
            self.assertEqual(dirs, [str(root / "good-repo")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
