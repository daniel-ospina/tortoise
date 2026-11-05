#!/usr/bin/env python3
"""gh_read_cache — a TTL cache for DECLARED-INVARIANT `gh` reads ONLY.

WHY THIS EXISTS (#1910 class)
-----------------------------
The account's 5,000-call/hour pool emptied FOUR times in one night. The gated shim (`gh-gate`)
already paces calls (250 ms spacing, 2 in flight), serialises them, honours `retry-after`, shares a
one-budget cooldown across the box, and re-derives a stale window against the exempt `/rate_limit`
read. It is a good gate — and it has **no cache**, so every poll is a live call.

GitHub's own best practice says, under *Avoid polling*:

    "Make authenticated conditional requests, so that unchanged data does not count against your
     primary rate limit." … "keep responses stable, so that more of your polls return 304 Not
     Modified."
    — docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api

The pool did not empty because the gate refused. It emptied because the FLEET ASKED 5,000 TIMES an
hour for answers that had not changed: a repo's identity, its required-check set, its runner
INVENTORY, its label set.

WHAT THIS IS NOT
----------------
* **Not a general `gh` cache.** A key outside :data:`INVARIANTS` is REFUSED
  (:class:`MutableReadRefused`); there is no pass-through.
* **Not `gh-gate`.** The gate decides WHEN a call may happen; this decides WHETHER it needs to.
  A hit never reaches the gate at all.
* **Not a stale-serving cache.** See FAIL-CLOSED.

⛔ WHY THE PROJECTION LIVES IN PYTHON AND NOT IN `--jq`
------------------------------------------------------
Cycle 4 of review measured, against the installed `gh 2.103.3`:

    $ gh api repos/o/r --paginate --slurp --jq '.[].name'
    the `--slurp` option is not supported with `--jq` or `--template`

So "assemble one document" (`--slurp`) and "project the invariant fields" (`--jq`) are **jointly
unsatisfiable** — every paginated key was dead, and the test that "pinned" `--slurp` was pinning an
argv `gh` rejects. A second defect rode on the same flag: `pflag` takes the LAST `--jq`, while the
guard validated the FIRST, so `--jq '{id}' --jq '.'` passed validation and cached the whole body.

Both are structural, so the fix is structural: **the registry owns the projection as a Python
callable, and the argv carries no `--jq`/`--slurp`/`--template` at all.** The reader decodes the
(possibly multi-document) paginated stream and applies the projection itself. Guard 2 is then a
property of OUR code, checked by a BEHAVIOURAL test (feed a body with volatile fields; assert they
do not survive), not by inspecting a vendor flag string.

THE FOUR STRUCTURAL GUARDS
--------------------------
Each is enforced in code. Where a guard can be removed with the suite still green, that is stated
HERE rather than claimed away — cycle 5 of review measured exactly which ones are inert, and the
earlier blanket claim ("each is pinned by a test that fails when the guard is removed") was false.

1. **SCOPE IS CACHE IDENTITY.** ``read()`` requires ``scope`` and it is folded into the filename
   digest AND re-verified in the envelope. Without it one repo's branch-protection answer was
   served as another's — valid envelope, valid digest, fresh age, wrong subject. Global keys
   (``api user``) are scoped by a **credential fingerprint**, and ``read()`` recomputes it rather
   than trusting a caller-supplied string.
2. **NO KEY CACHES A WHOLE RESPONSE BODY.** The READER supplies raw response bytes and
   ``read()`` stores only ``spec.project(decode_pages(raw))`` — so the guard holds for every
   caller, including one that supplies its own ``reader`` (it used to hold only on the CLI's
   private helper, and the documented library usage cached whole bodies). Enforced at import as
   well; argv may not carry ``--jq``/``--slurp``, and the argv must be ONE ``gh api`` READ of an
   endpoint on the invariant allow-list (:data:`_INVARIANT_ENDPOINTS`) — never a deny-list of
   mutable spellings, which cannot be complete. Stated honestly: this pins the SIX SHIPPED
   KEYS, not the concept — a ``project=lambda d: d`` passes every guard, so a future key still
   needs a reviewer. What IS mechanical is that a projection cannot silently return an empty or
   all-``None`` answer from a body it does not recognise (see :func:`_require_shape`).
3. **FRESHNESS ARITHMETIC CANNOT BE BYPASSED.** ``now`` and ``stored_at`` must be finite and the
   test is a single ``0 <= age < ttl``. Two separate returns let a NaN ``now`` make both false and
   an arbitrarily old entry was served as provably fresh.
4. **THE PROJECTION + TTL ARE PART OF THE IDENTITY.** A maintenance edit to a shipped projection
   would otherwise serve old-SCHEMA entries as fresh hits for the whole TTL. The projection digest
   is taken over the projection's **SOURCE**, not over a hand-written id: an id is only a
   rename-detector, and editing ``_project_repo_metadata``'s body while leaving ``“…-v1”`` in place
   served the old-schema entry as a fresh hit.
   Two layers carry it — the filename digest and the envelope stamp — and they are **deliberately
   redundant**. For the PROJECTION both layers are now INDIVIDUALLY KILLED (removing the
   filename's component reds the thrash test; removing the envelope's reds the
   projection-mismatch test). For the ARGV the envelope is authoritative and the filename only
   prevents thrashing (removing the filename's component alone survives). For the TTL the
   FILENAME is authoritative (removing the envelope's ``ttl_s`` check alone survives). A later reader must not "simplify" either layer away believing the other covers the same
   direction.

FAIL-CLOSED ON SHAPE, NOT ONLY ON TIME
--------------------------------------
A fresh entry can still be a LIE if the body it was built from was not the body we expected. An
error document cached at a 6-hour TTL is a complete-looking identity whose every field is ``None``.
So each projection goes through :func:`_require_shape` and RAISES ``Unreadable`` rather than
projecting nothing from a body it does not recognise — "I could not read" must never become
"I read an empty answer".

FAIL-CLOSED
-----------
A cached value is served ONLY when freshness is *provable*: the envelope parses; its ``key``,
``scope``, ``projection`` and ``ttl_s`` equal the ones asked for; ``sha256(payload)`` matches;
``now`` and ``stored_at`` are finite; and ``0 <= now - stored_at < ttl`` (so ``age == ttl`` is
EXPIRED, and a future stamp is a MISS). Anything else is a MISS — and on a MISS the reader runs.

**A stale entry is NEVER a fallback for a failed read.** Any reader failure raises
:class:`Unreadable`. "I could not read" must stay distinguishable from "I read X": collapsing those
two is exactly how a drained pool produced artifacts that looked like measurements.

USAGE
    import gh_read_cache as C

    body = C.read("repo.required_set", scope="owner/name@main",
                  cache_dir=PATH, now=time.time(), reader=my_reader)

    uv run python tools/gh_read_cache.py --list
    uv run python tools/gh_read_cache.py --key repo.required_set --repo owner/name --branch main
    uv run python tools/gh_read_cache.py --key repo.labels --repo owner/name --ttl 900   # tighten only
    uv run python tools/gh_read_cache.py --purge

Stdlib only. No network beyond the reader the caller supplies.
"""

from __future__ import annotations

import sys

# ⛔ #5128 / D9: the interpreter guard goes BEFORE every other import. The documented invocation is
# `uv run python tools/…`, but a bare/ambient interpreter can be older than the repo's 3.12 floor,
# and a 3.11+ stdlib name then dies with an UNATTRIBUTED error that reads as "this tool is broken".
# `tools/longmem_eval/run.py` is the canonical shape. Pinned by
# tests/test_entry_point_python_guard.py.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/gh_read_cache.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/gh_read_cache.py`"
    )

import argparse
import base64
import contextlib
import hashlib
import inspect
import itertools
import json
import os
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

__all__ = [
    "GLOBAL_KEYS",
    "INVARIANTS",
    "MutableReadRefused",
    "TtlNotPermitted",
    "Unreadable",
    "declare_invariant",
    "kind_of",
    "read",
    "scope_for",
    "ttl_for",
]


# PROJECTIONS — pure functions over the DECODED PAGES. Each must drop every volatile field, and
# each must REFUSE a body it does not recognise.
#
# ⛔ WHY THE SHAPE CHECK EXISTS. Without it, a wrong-shaped page projected to a plausible-looking
# answer: an error document (`{"message": "Server Error"}`) became
# `{"id": None, "node_id": None, "full_name": None, …}` — a complete-looking identity, cached as
# provably fresh for SIX HOURS — and a page `{"message": "oops"}` inside a `repo.labels` response was
# silently DROPPED (`['a']` from a two-page reply). Both are the "I could not read became I read X"
# class this module exists to prevent, and `last.get(f)` cannot tell an absent field from a JSON
# `null`.
# ═══════════════════════════════════════════════════════════════════════════════════════════════
def _require_shape(docs: Sequence[Any], key: str, *, want: str) -> None:
    """Raise ``Unreadable`` when a page is not the shape this endpoint returns.

    ``want`` is ``"dict"`` (an object page) or ``"list"`` (an array page). An error document IS a
    dict, so the type check alone is not enough — that is why the ``message`` test is here too.
    """
    if not docs:
        raise Unreadable(f"{key}: no page to project")
    for i, page in enumerate(docs):
        ok = isinstance(page, dict) if want == "dict" else isinstance(page, list)
        if not ok:
            raise Unreadable(
                f"{key}: page {i} is {type(page).__name__}, expected {want} — refusing to project "
                f"an unrecognised body (an error document must never be cached as an answer)"
            )
        # ⛔ An error document IS a dict, and GitHub's is 3 keys
        # (`message` + `documentation_url` + `status`), so a `len <= 2` test missed the real one.
        # None of these six endpoints ever returns a top-level string `message` on success, so its
        # presence is an error marker — and this is the ONLY guard that catches an error body which
        # also happens to carry the collection key (`{"message": …, "workflows": []}`), which would
        # otherwise project to a cached "empty answer". Pinned by M22.
        if want == "dict" and isinstance(page.get("message"), str):
            raise Unreadable(f"{key}: page {i} looks like an error document: {page['message']!r}")


def _pick_last(
    docs: Sequence[Any],
    fields: Sequence[str],
    *,
    key: str,
    required: Sequence[str] = (),
    str_fields: Sequence[str] = (),
    int_fields: Sequence[str] = (),
) -> dict[str, Any]:
    """The identity projection: a named subset of the last document, never the whole body.

    Refuses TWO ways, both of which used to yield a complete-looking answer with ``null``s:
    * a body carrying NONE of ``fields`` (an error document), and
    * a body missing any field in ``required`` — the identity-bearing ones. ``fields`` still
      decides what is KEPT/emitted; ``required`` decides what must be THERE. ``[{"id": 1}]`` used
      to project ``user.identity`` to ``{"login": None, "id": 1}``: an identity with no account.
    """
    _require_shape(docs, key, want="dict")
    last = docs[-1]
    if not any(f in last for f in fields):
        raise Unreadable(
            f"{key}: the body carries none of {list(fields)} — it is not this endpoint's answer"
        )
    missing = [f for f in required if last.get(f) is None]
    if missing:
        # ⛔ NULL is as useless as ABSENT: `{"id":1,"full_name":null,"default_branch":null}` used to
        # cache a six-hour identity naming no repository.
        raise Unreadable(f"{key}: the body has null {missing} — refusing to project nulls")
    out = {f: last.get(f) for f in fields}
    # ⛔ TYPE AND EMPTINESS, not just presence and null. `{"full_name": ""}` and
    # `{"login": {"unexpected": 1}, "id": true}` both passed the null test and cached — the
    # first a 1-hour identity naming no repository, the second a 12-hour identity whose `login`
    # is not even a string. Every other projection in this module type-checks its fields; so
    # does this one.
    for f in str_fields:
        if f in out and out[f] is not None and not (isinstance(out[f], str) and out[f]):
            raise Unreadable(f"{key}: {f!r} is not a non-empty string ({out[f]!r})")
    for f in int_fields:
        if (
            f in out
            and out[f] is not None
            and (not isinstance(out[f], int) or isinstance(out[f], bool))
        ):
            raise Unreadable(f"{key}: {f!r} is not an int ({out[f]!r})")
    if all(v is None for v in out.values()):
        raise Unreadable(f"{key}: every projected field is null — the body was not an answer")
    return out


def _expand(docs: Sequence[Any], key: str, container: str) -> list[Any]:
    """Flatten a paginated object page's ``container`` list across every page.

    A page without the container is REFUSED rather than skipped: skipping is what silently dropped
    page 2 of a two-page response. This is strict on purpose — the real endpoint ALWAYS sends the
    list (an empty collection is ``{"total_count": 0, "runners": []}``), so an absent list means the
    body is not this endpoint's answer, and treating it as empty is the false-PASS direction.
    """
    _require_shape(docs, key, want="dict")
    rows: list[Any] = []
    for i, page in enumerate(docs):
        inner = page.get(container)
        if not isinstance(inner, list):
            raise Unreadable(
                f"{key}: page {i} has no {container!r} list — refusing to read a missing collection "
                f"as an empty one"
            )
        # ⛔ EXTEND, NEVER REPLACE. `gh api --paginate` hands every page to the projection, so a
        # `rows = list(inner)` regression caches the LAST page as the whole inventory — this repo
        # has 41 workflow files, so `repo.workflows` genuinely pages. Pinned by the two-page test.
        rows.extend(inner)
    return rows


def _project_repo_metadata(docs: Sequence[Any]) -> Any:
    # ⛔ `visibility` IS DELIBERATELY NOT PROJECTED. It flips public -> private, and serving a stale
    # "public" for an hour turns a "may I quote this repo into a public artifact?" gate into a
    # false PASS — the only registry field whose staleness is a LEAK rather than a wait. A caller
    # that needs it must read it live.
    return _pick_last(
        docs,
        ("id", "node_id", "full_name", "default_branch"),
        key="repo.metadata",
        # `node_id` is what a GraphQL caller addresses the repo by; an explicit null must not
        # survive as a complete-looking identity (the `{"id": null, …}` note above).
        required=("id", "node_id", "full_name", "default_branch"),
        str_fields=("node_id", "full_name", "default_branch"),
        int_fields=("id",),
    )


def _project_required_set(docs: Sequence[Any]) -> Any:
    _require_shape(docs, "repo.required_set", want="dict")
    # ⛔ ABSENT IS NOT NULL, and this is the MERGE-GATING key: an empty required set reads as "this
    # branch requires no checks". Measured before this check — `[{}]` and a BRANCH OBJECT
    # (`{"id":1,"name":"main","protection":{}}`) both projected to `{"contexts": [], "checks": []}`
    # and were cached for 30 minutes. `required_status_checks: null` IS the legitimate "no required
    # checks configured" answer, so only the ABSENT key is refused.
    if "required_status_checks" not in docs[-1]:
        raise Unreadable(
            "repo.required_set: the body has no `required_status_checks` key — it is not a branch "
            "protection answer, and an empty required set must never be inferred from its absence"
        )
    rsc = docs[-1]["required_status_checks"]
    if rsc is None:
        # No required checks configured. That IS an answer, and it is invariant.
        return {"contexts": [], "checks": []}
    if not isinstance(rsc, dict):
        raise Unreadable("repo.required_set: required_status_checks is not an object")
    # The same rule one level deeper: `{"strict": false}` names no check list, so reading it as an
    # EMPTY required set would infer "this branch requires no checks" from an absence.
    if "contexts" not in rsc and "checks" not in rsc:
        raise Unreadable(
            "repo.required_set: required_status_checks has neither `contexts` nor `checks` — "
            "refusing to infer an empty required set from the inner object's shape"
        )
    contexts = rsc.get("contexts")
    checks = rsc.get("checks")
    # ⛔ AN EXPLICIT `null` IS NOT AN ANSWER. Refusing ABSENT and WRONG-TYPED but letting
    # `{"checks": null}` through read as "this branch requires no checks" — the same false PASS the
    # absent check closes, one spelling along. GitHub sends `[]`, never `null`, so a null here means
    # the body is unrecognised.
    if ("contexts" in rsc and contexts is None) or ("checks" in rsc and checks is None):
        raise Unreadable(
            "repo.required_set: a null `contexts`/`checks` reads as an empty required set — "
            "refusing to infer 'no required checks' from an explicit null"
        )
    # ⛔ TYPE AND NULL-NESS, not presence. `{"contexts": "docs"}` used to project to
    # `["d","o","c","s"]` and `{"checks": {"docs": {"app_id": 1}}}` to `[]` — an empty required set
    # inferred from a shape the code did not understand, on the MERGE-GATING key.
    if contexts is not None and not (
        isinstance(contexts, list) and all(isinstance(c, str) for c in contexts)
    ):
        raise Unreadable("repo.required_set: `contexts` is not a list of strings")
    if checks is not None and not (
        isinstance(checks, list)
        and all(isinstance(c, dict) and isinstance(c.get("context"), str) for c in checks)
    ):
        raise Unreadable("repo.required_set: `checks` is not a list of {context} objects")
    # ⛔ NEVER FABRICATE A KEY THE BODY DID NOT CARRY. Returning `"contexts": []` for a body that
    # only sent `checks` is an empty required set inferred from an absence — and a merge gate
    # reading `contexts` (the field legacy protection uses) then sees "no required checks" for a
    # branch that requires `docs`. An OMITTED key cannot be misread as "none".
    out: dict[str, Any] = {}
    if contexts is not None:
        out["contexts"] = list(contexts)
    if checks is not None:
        out["checks"] = [c["context"] for c in checks]
    return out


def _project_labels(docs: Sequence[Any]) -> Any:
    _require_shape(docs, "repo.labels", want="list")
    out = []
    for page in docs:
        for item in page:
            if not isinstance(item, dict) or not (
                isinstance(item.get("name"), str) and item["name"]
            ):
                raise Unreadable(f"repo.labels: entry {item!r} is not a named label")
            out.append(item["name"])
    return out


def _project_workflows(docs: Sequence[Any]) -> Any:
    out = []
    for w in _expand(docs, "repo.workflows", "workflows"):
        if not isinstance(w, dict):
            raise Unreadable(f"repo.workflows: entry {w!r} is not a workflow")
        missing = [f for f in ("id", "path", "state") if w.get(f) is None]
        if missing:
            # `[{}]` AND `[{"id": null, "path": null, "state": null}]` both used to project to a
            # workflow list naming no workflow, cached for an hour. Null is as useless as absent.
            raise Unreadable(f"repo.workflows: entry has null {missing} — not a workflow")
        # ⛔ `id` is what a caller ADDRESSES a workflow by, so a string (or a bool) must not pass,
        # and an EMPTY path/state is as useless as a null one.
        if not isinstance(w["id"], int) or isinstance(w["id"], bool):
            raise Unreadable(f"repo.workflows: entry id {w['id']!r} is not an int")
        for f in ("path", "state"):
            if not isinstance(w[f], str) or not w[f]:
                raise Unreadable(f"repo.workflows: {f!r} is not a non-empty string")
        # `state` here is the DEFINITION's enabled/disabled state on the default branch —
        # invariant on this timescale. It is not a run's state.
        out.append({k: w.get(k) for k in ("id", "path", "state")})
    return out


def _project_runners_inventory(docs: Sequence[Any]) -> Any:
    """⛔ name + LABEL NAMES only. The body also carries `status` and `busy`, which move
    second-to-second; a caller needing liveness must read live.
    """
    out = []
    for r in _expand(docs, "runners.inventory", "runners"):
        if not isinstance(r, dict):
            raise Unreadable(f"runners.inventory: entry {r!r} is not a runner")
        # ⛔ TYPE **AND EMPTINESS**. `[{}]` and `[{"name": null}]` both used to project to an
        # inventory entry naming no runner, cached for 30 minutes; `{"name": ""}` is the THIRD
        # spelling of "names nothing" and `""` IS a `str`, so the type check alone let it through.
        # `_pick_last` (identity fields), `_project_workflows` and `_project_labels` in this same
        # module all refuse it; this projection was the one that did not.
        if not isinstance(r.get("name"), str) or not r["name"]:
            raise Unreadable(
                "runners.inventory: entry has a non-null, non-empty-string `name` — not a runner"
            )
        # ⛔ ABSENCE IS NOT AN EMPTY LABEL SET. A missing `labels` key read as "this runner carries
        # no labels" and cached that "no" for 30 minutes — the last spelling of the failure the
        # comment below closes, and the same rule `_expand` and `_project_required_set` already
        # apply. `labels: []` is the legitimate empty answer; ABSENCE is not.
        if "labels" not in r:
            raise Unreadable("runners.inventory: entry has no `labels` key — not a runner body")
        if not isinstance(r["labels"], list):
            raise Unreadable("runners.inventory: entry has a non-list `labels`")
        # ⛔ NO SILENT FILTER. `if isinstance(lbl, dict)` dropped every unrecognised label entry, so
        # a lane asking "is there a runner with label X" got a cached "no" from a body the
        # projection did not understand. `_project_labels` type-checks; so does this.
        labels = []
        for lbl in r["labels"]:
            # Type AND emptiness, for the reason the runner-name check above states: a label
            # naming `""` is a label naming nothing, and a lane asking "is there a runner with
            # label X" must not be answered from a body this projection did not understand.
            if not isinstance(lbl, dict) or not (
                isinstance(lbl.get("name"), str) and lbl["name"]
            ):
                raise Unreadable(f"runners.inventory: label entry {lbl!r} is not a named label")
            labels.append(lbl["name"])
        out.append({"name": r["name"], "labels": labels})
    return out


def _project_user_identity(docs: Sequence[Any]) -> Any:
    return _pick_last(
        docs,
        ("login", "id"),
        key="user.identity",
        required=("login", "id"),
        str_fields=("login",),
        int_fields=("id",),
    )


@dataclass(frozen=True)
class Invariant:
    """One cacheable read.

    ``projection`` is a STABLE ID used in the cache identity (change it when the projection's shape
    changes, and every old entry stops matching). ``project`` is the pure callable that does the
    work. ``argv`` must NOT carry ``--jq``/``--slurp``/``--template`` — see the module docstring.
    """

    ttl_s: int
    argv: tuple[str, ...]
    projection: str
    project: Callable[[Sequence[Any]], Any]


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# THE REGISTRY — an EXPLICIT allow-list. Every entry projects to invariant fields only.
# ═══════════════════════════════════════════════════════════════════════════════════════════════
INVARIANTS: dict[str, Invariant] = {
    # ONLY the identity/branch fields. ⛔ `pushed_at`, `updated_at`, `open_issues_count`,
    # `stargazers_count` and `visibility` are in this body; the first four change every push and the
    # last one is a security flag, so none is projected.
    # ⛔ 1 h, not the original 6 h. `default_branch` flips on a rename and a lane branches off it;
    # an hour bounds that wait while still removing the per-PR identity read from the pool.
    "repo.metadata": Invariant(
        ttl_s=60 * 60,
        argv=("api", "repos/{repo}"),
        projection="repo-identity-v1",
        project=_project_repo_metadata,
    ),
    # The REQUIRED SET only. Changes when a protection rule is edited: human-paced.
    "repo.required_set": Invariant(
        ttl_s=30 * 60,
        argv=("api", "repos/{repo}/branches/{branch}/protection"),
        projection="required-set-v1",
        project=_project_required_set,
    ),
    # The label NAMES. Human-paced.
    "repo.labels": Invariant(
        ttl_s=60 * 60,
        argv=("api", "repos/{repo}/labels", "--paginate"),
        projection="label-names-v1",
        project=_project_labels,
    ),
    # Workflow DEFINITIONS (id/path/state). A new workflow file on the default branch changes it.
    "repo.workflows": Invariant(
        ttl_s=60 * 60,
        argv=("api", "repos/{repo}/actions/workflows", "--paginate"),
        projection="workflow-defs-v1",
        project=_project_workflows,
    ),
    # The runner INVENTORY: name + LABEL NAMES only.
    "runners.inventory": Invariant(
        ttl_s=30 * 60,
        argv=("api", "repos/{repo}/actions/runners", "--paginate"),
        projection="runner-inventory-v1",
        project=_project_runners_inventory,
    ),
    # The authenticated identity. Changes on token rotation — which is why a global key is scoped
    # by a credential fingerprint rather than by repo/branch.
    "user.identity": Invariant(
        ttl_s=12 * 3600,
        argv=("api", "user"),
        projection="user-identity-v1",
        project=_project_user_identity,
    ),
}

# Keys whose answer does not depend on a repo/branch. Everything else must be scoped.
GLOBAL_KEYS: frozenset[str] = frozenset({"user.identity"})

# Flags the registry may not use: they are gh's job only if gh could do it, and it cannot (the
# `--slurp`/`--jq` incompatibility measured above). Projection is THIS module's job.
_FORBIDDEN_ARGV_FLAGS = ("--jq", "-q", "--slurp", "--template", "-t")

# ⛔ THE MUTABLE VOCABULARY. Token-matched after a camelCase split, NEVER substring-matched.
#    A substring test on `"run"` matches `"runners"`; on `"check"` matches `"checked"`. A guard that
#    did both would refuse the reads it exists to allow AND admit a novel spelling — failing in both
#    directions, which is why this is spelled the way it is (the first green run of the suite was
#    23 passed / 2 failed, because `"run" in "runners.inventory"`).
_MUTABLE_TOKENS: frozenset[str] = frozenset(
    {
        "state",
        "status",
        "busy",
        "conclusion",
        "conclusions",
        "check",
        "checks",
        "mergeable",
        "mergeablestatus",
        "merged",
        "merge",
        "draft",
        "decision",
        "reviewdecision",
        "run",
        "runs",
        "job",
        "jobs",
        "commit",
        "commits",
        "sha",
        "diff",
        "body",
        "comment",
        "comments",
        "review",
        "reviews",
        "queue",
        "position",
        "updated",
        "created",
        "pushed",
        "modified",
        "count",
        "counts",
        "open",
        "prs",
        "push",
        "pushes",
        "oid",
        "total",
        # whole mutable CLASSES an inventory belongs to: a branch/tag set changes on every push,
        # and an approval set changes on every review.
        "branch",
        "branches",
        "branchlist",
        "tag",
        "tags",
        "release",
        "releases",
        "assignee",
        "assignees",
        "approved",
        "approvals",
        "approvedat",
        "headsha",
        "headoid",
        # moving refs and human-editable metadata: `pr.head`'s SHA changes on every push, and
        # `pr.title`/`repo.description`/`repo.topics` change without any invariant being involved.
        "head",
        "headref",
        "base",
        "title",
        "ref",
        "refs",
        "description",
        "topics",
        "visibility",
        "properties",
        "locked",
        "homepage",
        "permissions",
        "message",
        "milestone",
        "milestones",
        # counters/flags that ride the repo body (`repo.forks`, `repo.stargazers`, `repo.size`,
        # `repo.archived`, `pr.files`, `pr.additions`, `pr.rebaseable`, …).
        "forks",
        "stargazers",
        "watchers",
        "subscribers",
        "size",
        "files",
        "additions",
        "deletions",
        "changedfiles",
        "rebaseable",
        "conflicting",
        "archived",
        "disabled",
        "protected",
        "pending",
        "resetat",
        "remaining",
        "quota",
        "latency",
    }
)

# Glued phrases formed by an ADJACENT TOKEN PAIR (`rate_limit` → `ratelimit`).
# ⛔ PAIRS, NOT A SUBSTRING SCAN OVER THE WHOLE KEY. Scanning the whole glued key refused
# `x.accurateLimit` and `repo.generateLimit` as mutable, because "rate"+"limit" happens to appear
# across a word boundary — the same failure mode as `"run" in "runners"`, in the other direction.
_MUTABLE_PAIRS: tuple[str, ...] = (
    "ratelimit",
    "mergestatestatus",
    "statuscheckrollup",
)

#: ⛔ SCOPE-DEPENDENT WORDS. `repo.labels` is the repo's DEFINITION set (admin-paced,
#: invariant — it is a SHIPPED key); `pr.labels`/`issue.labels` is an ASSIGNMENT that changes on
#: every triage. A flat token list cannot express that, so these count as mutable only when the
#: key is not `repo.`-scoped. Without this, the honest fix for `pr.labels` would refuse the
#: shipped key, and the dishonest fix is to leave the assignment cacheable.
_CONTAINER_SCOPED_MUTABLE: frozenset[str] = frozenset({"label", "labels"})

# A single token that glues a mutable word on an edge (`prstate` → `state`) is mutable too.
# Only words of 4+ characters participate, so `run`/`tag`/`sha`/`job` cannot over-match (`runners`).
# ⛔ This OVER-refuses some legitimate names (`repo.checksum` via `check`, `repo.bodyTemplate`
#    via `body`). That is a DELIBERATE, fail-closed trade-off: over-refusal costs a rename and a
#    dispatch cycle; under-refusal caches mutable state and reports it as invariant. The rule errs
#    safe and this comment is here so a later reader does not "fix" it by loosening the guard.
_MIN_GLUED_WORD = 4

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_TOKEN_SPLIT = re.compile(r"[^A-Za-z0-9]+")


class MutableReadRefused(Exception):
    """The key is mutable, or undeclared — which is not the same as invariant.

    Raised BEFORE any reader call, so a refusal never becomes a labelled pass-through.
    """


class TtlNotPermitted(Exception):
    """A caller asked to hold an entry longer than the key's ceiling, or to widen it."""


class Unreadable(Exception):
    """The re-read failed and no provably-fresh value exists. Never answered from a stale entry."""


# ── the mutable vocabulary ───────────────────────────────────────────────────────────────────
def _tokens(key: str) -> list[str]:
    """camelCase-aware tokens: `pr.reviewDecision` → ['pr', 'review', 'decision']."""
    return [t.lower() for t in _TOKEN_SPLIT.split(_CAMEL.sub("-", key)) if t]


def _vocabulary_for(key: str) -> frozenset[str]:
    """The mutable-token vocabulary that applies to THIS key — the ONE set both the decision
    (`_is_mutable_name`) and the refusal message (`_mutable_words_in`) walk.

    ⛔ NEVER FORK THIS AGAIN. The decision set and the message set drifted for exactly one commit:
    `_is_mutable_name` added `_CONTAINER_SCOPED_MUTABLE`, `_mutable_words_in` did not, so
    `pr.labels` was refused (correctly) with an EMPTY reason list — `carries mutable-state
    vocabulary ()` — on the very key pair the container-scoped vocabulary was added for. Two
    functions that must agree, deriving their set independently, is how that happens; one helper
    is how it cannot.
    """
    tokens = _tokens(key)
    # ⛔ THE EXEMPTION IS EXACTLY `repo.labels`, NOT ANY `repo.`-PREFIXED NAME. A prefix test
    # admitted `repo.pr.labels` (argv `repos/{repo}/pulls/9/labels`) — a PR's triage ASSIGNMENT
    # cached for an hour, which is the case this exemption exists to keep OUT.
    if tokens == ["repo", "labels"]:
        return frozenset(_MUTABLE_TOKENS - _CONTAINER_SCOPED_MUTABLE)
    return frozenset(_MUTABLE_TOKENS | _CONTAINER_SCOPED_MUTABLE)


def _is_mutable_name(key: str) -> bool:
    """Token-exact match, plus adjacent-PAIR phrases, plus a glued suffix/prefix test."""
    tokens = _tokens(key)
    mutable_here = _vocabulary_for(key)
    if any(t in mutable_here for t in tokens):
        return True
    if any((a + b) in _MUTABLE_PAIRS for a, b in itertools.pairwise(tokens)):
        return True
    long_words = [w for w in mutable_here if len(w) >= _MIN_GLUED_WORD]
    for t in tokens:
        if t in mutable_here:
            continue
        # A single token that IS a glued phrase (`ratelimit`). Per-TOKEN, never across the whole
        # key: `accurate`+`limit` glue to the same string from two tokens, and a whole-key scan
        # refused that.
        if any(phrase in t for phrase in _MUTABLE_PAIRS):
            return True
        if any(t.endswith(w) or t.startswith(w) for w in long_words):
            return True
    return False


def _mutable_words_in(key: str) -> list[str]:
    """The words that made a name mutable — for the refusal message, never for the decision.

    Walks `_vocabulary_for(key)`, the SAME set `_is_mutable_name` decides from, so a refusal can
    never again name an empty reason list for a word the decision refused.
    """
    tokens = _tokens(key)
    mutable_here = _vocabulary_for(key)
    hits = [t for t in tokens if t in mutable_here]
    hits += [a + b for a, b in itertools.pairwise(tokens) if (a + b) in _MUTABLE_PAIRS]
    long_words = [w for w in mutable_here if len(w) >= _MIN_GLUED_WORD]
    for t in tokens:
        if t in mutable_here:
            continue
        hits += [ph for ph in _MUTABLE_PAIRS if ph in t]
        hits += [w for w in long_words if t.endswith(w) or t.startswith(w)]
    return sorted(set(hits))


def kind_of(key: str) -> str:
    """``"invariant"`` | ``"mutable"`` — and an UNDECLARED key raises rather than being guessed.

    Undeclared is deliberately NOT a third return value: a caller has no business deciding what to
    do about an unproven key, and returning a string invites one to default it to invariant.
    """
    if key in INVARIANTS:
        if _is_mutable_name(key):
            return "mutable"  # defence in depth — a registry edit cannot smuggle a mutable read in
        return "invariant"
    if _is_mutable_name(key):
        return "mutable"
    raise MutableReadRefused(
        f"{key!r} is not a declared invariant. An undeclared key is not provably invariant, so it "
        f"is refused rather than cached. Declare it with declare_invariant() if it truly cannot "
        f"change, or read it live."
    )


def ttl_for(key: str) -> int:
    """The declared ceiling, in seconds, for an invariant key."""
    if kind_of(key) != "invariant":
        raise MutableReadRefused(f"{key!r} is a mutable read and has no TTL")
    return INVARIANTS[key].ttl_s


# ⛔ THE DOOR IS AN ALLOW-LIST OF ENDPOINTS, NOT A DENY-LIST OF SPELLINGS — and this is the THIRD
# spelling of ONE mistake, so the history goes here so a fourth does not get written.
#
#   1. `_is_mutable_name` read the KEY, so `declare_invariant("repo.identity",
#      argv=("api", "repos/{repo}/pulls/9"))` was ACCEPTED — an invariant-sounding NAME over a
#      mutable READ, cached for an hour and served after the PR closed.
#   2. The next head added a deny-list: ten literal substrings matched with `marker in a` (whose
#      docstring claimed to be "segment-bounded" — it was not). A deny-list at a boundary CANNOT be
#      complete. Absent from it, and therefore ACCEPTED: `/git/refs/…` (a moving ref), `/branches`,
#      `/deployments`, `/rulesets`, `notifications`, `graphql` — measured, a moved ref was served as
#      a provably-fresh hit for the full hour with ZERO reader calls. Deeper hole in the same door:
#      `("api","repos/{repo}","-X","POST","-f","name=x")` passed every guard, so the MUTATION ran
#      once and every later caller was handed its cached response without the mutation running.
#   3. This one — the shape OWASP's Input Validation Cheat Sheet states and R4 already promised:
#      *"Define what the application accepts and reject values outside those rules. Do not try to
#      recognize every malicious string."*
#
# The match is EXACT TUPLE EQUALITY on the path's `/`-separated segments, deliberately NOT a prefix
# match: a prefix match on `repos/{repo}` would re-admit `repos/{repo}/pulls`, the hole this
# replaces. `{repo}`/`{branch}` must be spelled as PLACEHOLDERS — a concrete repo in a declared argv
# would read ONE fixed repo for every scope, which is guard 1's failure with the wrong subject.
_INVARIANT_ENDPOINTS: tuple[tuple[str, ...], ...] = (
    ("repos", "{repo}"),
    ("repos", "{repo}", "branches", "{branch}", "protection"),
    ("repos", "{repo}", "labels"),
    ("repos", "{repo}", "actions", "workflows"),
    ("repos", "{repo}", "actions", "runners"),
    ("user",),
)

# The ONLY element that may follow the path. Everything else — `-X`/`--method`/`-f`/`-F`/`--input`,
# or a SECOND path — is refused by OMISSION rather than enumerated, because enumerating the mutations
# somebody thought of is exactly the shape this replaces.
_ALLOWED_ARGV_FLAGS: frozenset[str] = frozenset({"--paginate"})


def _endpoint_refusal(argv: Sequence[str]) -> str | None:
    """The reason this argv is not a READ of a declared-invariant endpoint, or ``None``.

    One question, one function: the shape (`api` + ONE path + `--paginate` at most) AND the endpoint
    (exact allow-list membership) are the same decision, so a caller cannot satisfy one and slip
    past the other.
    """
    args = list(argv) if isinstance(argv, (list, tuple)) else None
    if args is None:
        return f"its argv is {argv!r}, not a list/tuple of arguments"
    if len(args) < 2 or args[0] != "api":
        return (
            f"its argv {tuple(args)!r} is not `('api', <one path>)` — this cache stores `gh api` "
            f"READS only"
        )
    path = args[1]
    if not isinstance(path, str) or not path:
        return f"its argv names no endpoint path ({path!r})"
    if path.startswith("-"):
        return f"its argv names the flag {path!r} where the endpoint path belongs"
    for extra in args[2:]:
        if not isinstance(extra, str) or extra not in _ALLOWED_ARGV_FLAGS:
            return (
                f"its argv carries {extra!r} after the path; only "
                f"{sorted(_ALLOWED_ARGV_FLAGS)} may follow it. `-X`/`--method`/`-f`/`-F`/`--input` "
                f"would cache a MUTATION's response and replay it to every later caller WITHOUT "
                f"running the mutation again"
            )
    segments = path.split("/")
    if not all(segments):
        return f"the endpoint {path!r} has an empty path segment"
    if tuple(segments) not in _INVARIANT_ENDPOINTS:
        return (
            f"the endpoint {path!r} is not one of the declared-invariant endpoints "
            f"({', '.join('/'.join(e) for e in _INVARIANT_ENDPOINTS)}). An endpoint outside that "
            f"list is not provably invariant, so it is refused rather than cached — enumerating "
            f"the BAD routes cannot be complete (`/git/refs/…` moves on every push and `graphql` "
            f"can ask for anything), so the door accepts only what is known to be stable"
        )
    return None


def _validate_spec(key: str, argv: Sequence[str], projection: str, project: Any) -> None:
    """Guard 2, at whichever door the spec came through."""
    if not callable(project):
        raise TtlNotPermitted(
            f"{key!r} declares no `project` callable. Caching a WHOLE response body replays "
            f"whatever mutable fields sit beside the invariant ones — project them explicitly."
        )
    if not isinstance(projection, str) or not projection.strip() or projection.strip() == ".":
        raise TtlNotPermitted(
            f"{key!r} declares no usable projection id ({projection!r}). It is the cache-identity "
            f"stamp, so it must name the projection's SHAPE."
        )
    # ⛔ THE gh PROJECTION FLAGS FIRST, so `--jq` is refused for the reason it is actually wrong
    # (projection is this module's job) rather than as an anonymous "unexpected flag" below.
    for a in argv:
        if a in _FORBIDDEN_ARGV_FLAGS or a.startswith(("--jq=", "--template=", "-q=")):
            raise TtlNotPermitted(
                f"{key!r} carries {a!r} in its argv. Projection is this module's job: `gh api "
                f"--paginate --slurp --jq` is REFUSED by gh 2.103.3 ('--slurp is not supported with "
                f"--jq'), and pflag takes the LAST --jq while a validator sees the FIRST."
            )
    refused = _endpoint_refusal(argv)
    if refused is not None:
        raise MutableReadRefused(
            f"{key!r} cannot be an invariant: {refused}. An invariant-sounding KEY is not an "
            f"invariant READ — caching this would replay a past state as the current one."
        )


def declare_invariant(
    key: str,
    *,
    ttl_s: int,
    argv: Sequence[str],
    projection: str,
    project: Callable[[Sequence[Any]], Any],
) -> None:
    """Add a key to the registry.

    Refuses: a mutable name, a non-positive TTL, a missing/non-callable ``project``, an argv
    carrying gh's projection flags, an argv that is not ONE ``gh api`` READ of an endpoint on the
    invariant allow-list (:data:`_INVARIANT_ENDPOINTS` — an unlisted endpoint, a second operand, or
    any ``-X``/``--method``/``-f``/``-F``/``--input`` is refused, because a cached MUTATION replays
    its response without running the mutation), and any attempt to WIDEN an existing key's ceiling.
    That last one matters because this function is the documented way to add a key — so without the
    check the ceiling that ``read()`` defends could be raised by a one-line call.
    """
    if _is_mutable_name(key):
        raise MutableReadRefused(
            f"{key!r} carries mutable-state vocabulary "
            f"({', '.join(_mutable_words_in(key))}). A mutable read cached at any TTL reports a past "
            f"state as the current one — that is a false PASS, not a saving."
        )
    if not isinstance(ttl_s, int) or isinstance(ttl_s, bool) or ttl_s <= 0:
        raise TtlNotPermitted(f"ttl_s must be a positive int, got {ttl_s!r}")
    _validate_spec(key, argv, projection, project)
    existing = INVARIANTS.get(key)
    if existing is not None and ttl_s > existing.ttl_s:
        raise TtlNotPermitted(
            f"{key!r} already declares a {existing.ttl_s}s ceiling; refusing to widen it to "
            f"{ttl_s}s. Tightening is allowed; widening is how a TTL quietly becomes a stale read."
        )
    INVARIANTS[key] = Invariant(
        ttl_s=ttl_s, argv=tuple(argv), projection=projection, project=project
    )


def _assert_every_key_projects() -> frozenset[str]:
    """Import-time guard (2): no key may cache a whole body. Returns the KEYS it checked.

    ⛔ Returning the checked-key SET is load-bearing, and a count was not enough: ``_IMPORT_CHECK``
    replaced by the literal ``6`` satisfied ``== len(INVARIANTS)`` and survived the whole suite
    (measured). The set can only be what this loop actually visited.
    """
    checked: set[str] = set()
    for key, spec in INVARIANTS.items():
        _validate_spec(key, spec.argv, spec.projection, spec.project)
        # ⛔ THE OBJECT ID IS THE PROOF. A literal `frozenset(INVARIANTS)` reproduces the key SET and
        # survived the whole suite (measured). It cannot reproduce `id(spec)` — so this value can
        # only be produced by a loop that actually visited the registry.
        checked.add(f"{key}#{id(spec)}")
    return frozenset(checked)


# ⛔ BOUND TO A NAME, not called bare. A bare `_assert_every_key_projects()` could be DELETED with
# the whole suite still green (measured — every test called the function directly). Deleting this
# line now removes `_IMPORT_CHECK` and the guard test reds on AttributeError.
_IMPORT_CHECK: frozenset[str] = _assert_every_key_projects()


# ── the on-disk format ────────────────────────────────────────────────────────────────────────
_CACHE_PREFIX = "gh-read-"


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text)


def _filename(key: str, scope: str, projection: str | None = None, ttl_s: int | None = None) -> str:
    """Deterministic, collision-resistant, readable — SCOPED, and keyed by projection + TTL.

    ⛔ THE PROJECTION AND THE EFFECTIVE TTL ARE PART OF THE IDENTITY.
    * Projection: a maintenance edit to a shipped projection would otherwise serve old-SCHEMA
      entries as fresh hits (`repo.metadata` holds 1 h, so a newly-added field would be missing
      for an hour).
    * TTL: `_load` rejects an entry stamped under a different ttl, so without this two callers at
      different TTLs thrashed ONE file — measured 12 live reads for 12 accesses where a working
      cache does 2, i.e. the cache silently disabled for that key.
    """
    spec = INVARIANTS[key]
    if projection is None:
        projection = spec.projection
    if ttl_s is None:
        ttl_s = spec.ttl_s
    # ⛔ THE SOURCE-BEARING DIGEST IS PART OF THE FILENAME. It was only in the envelope, so two
    # checkouts of this file at different projection revisions overwrote each other's entry on
    # every access — measured 8 alternating accesses -> 8 live gh reads, never converging, because
    # the shared default cache dir is box-wide. With it here, each revision owns its file.
    src_digest = _projection_digest(projection, INVARIANTS[key].project)
    # ⛔ THE ARGV IS PART OF THE IDENTITY. Without it, changing which ENDPOINT a key reads (same
    # scope, same key, same projection) served the previous endpoint's answer as a provably-fresh
    # hit — measured with 0 reader calls, for the whole 6-hour `repo.metadata` TTL. That is guard
    # 1's own failure mode with the REQUEST as the subject.
    argv_digest = hashlib.sha1("\x00".join(INVARIANTS[key].argv).encode()).hexdigest()[:12]
    payload_id = hashlib.sha1(
        f"{scope}\x00{key}\x00{src_digest}\x00{argv_digest}\x00{int(ttl_s)}".encode()
    ).hexdigest()[:12]
    return f"{_CACHE_PREFIX}{_slug(key)}-{_slug(scope)[:20]}-{payload_id}.json"


def _cache_path(
    key: str, scope: str, projection: str | None = None, ttl_s: int | None = None
) -> Path:
    """The entry's NAME (not joined to a directory — the caller supplies cache_dir)."""
    return Path(_filename(key, scope, projection, ttl_s))


def _projection_digest(projection: str, project: Any = None) -> str:
    """A stable identity for a projection: its declared id AND its SOURCE.

    ⛔ The id alone is a RENAME-DETECTOR, not a schema-detector. Measured: adding a field to
    ``_project_repo_metadata`` while leaving ``projection="repo-identity-v1"`` in place served the
    old-schema entry as a fresh hit, because nothing about the stored stamp had changed. Hashing the
    source means the SAME edit changes the identity and the stale entry can no longer match.

    TWO DECLARED RESIDUALS — neither is a wrong answer, both should be known before trusting this:

    (a) A callable whose source cannot be recovered (a C-level or ``exec``-built function) falls back
        to the id alone.
    (b) ⛔ THE DIGEST COVERS THE PROJECTION FUNCTION ONLY, NOT THE HELPERS IT CALLS. Measured: a
        semantic change to :func:`_expand` (e.g. reversing the flattened rows) leaves this digest
        BYTE-IDENTICAL, so old-schema entries keep matching. Closing that would mean hashing the
        module's own source, which changes on every unrelated edit and would discard the whole
        cache each time — a worse trade. The consequence is bounded: a helper edit must be paired
        with a ``-v2`` id bump, exactly like a body edit. If you change a projection HELPER, bump
        the affected keys' ``projection`` strings.
    """
    src = ""
    if project is not None:
        try:
            src = inspect.getsource(project)
        except (OSError, TypeError):  # builtins, lambdas from exec, C extension callables
            src = ""
    return hashlib.sha1(f"{projection}\x00{src}".encode()).hexdigest()[:12]


def _envelope(
    key: str,
    scope: str,
    payload: bytes,
    *,
    now: float,
    ttl_s: int,
    projection: str | None = None,
) -> bytes:
    """The stored bytes: digest over the PAYLOAD ONLY, freshness in the metadata.

    The split is deliberate. The digest answers *"are these the bytes that were stored?"*;
    ``stored_at`` + ``ttl_s`` answer *"are they still fresh?"*.

    ⛔ ``stored_at`` IS TRUSTED, and this docstring used to claim the opposite — that a digest over
    the metadata would "make a rewritten ``stored_at`` look intact, which is the failure
    :func:`_load` exists to catch". ``_load`` performs NO metadata-integrity check at all, so that
    sentence described a mechanism that is not there. Stated plainly: a local writer that can
    rewrite ``stored_at`` can make an old payload look fresh, and this is NOT a forgery defence —
    the cache dir is box-wide and writable by any local process. The payload digest is the same kind
    of control: it detects a corrupt or torn write, not a hostile one.
    """
    if projection is None:
        projection = INVARIANTS[key].projection
    return json.dumps(
        {
            "key": key,
            "scope": scope,
            "projection": _projection_digest(projection, INVARIANTS[key].project),
            "argv": list(INVARIANTS[key].argv),
            "stored_at": float(now),
            "ttl_s": int(ttl_s),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "payload": base64.b64encode(payload).decode("ascii"),
        },
        sort_keys=True,
    ).encode()


def _load(
    path: Path,
    key: str,
    scope: str,
    *,
    now: float,
    ttl_s: int,
    projection: str | None = None,
) -> bytes | None:
    """Return the payload IFF freshness is provable. Any doubt → ``None`` (a MISS)."""
    # ⛔ GUARD 3. A non-finite clock must never prove freshness. The single `0 <= age < ttl`
    # comparison below already rejects NaN and ±inf (each comparison is False), so there is
    # deliberately NO separate `math.isfinite` pre-check: it was dead code that implied protection
    # it did not provide. `test_non_finite_now_never_serves_a_hit` holds this line's behaviour.
    try:
        env = json.loads(path.read_bytes())
    # ⛔ CATCH BROADLY, ON PURPOSE — the SAME reasoning as the coercion block below, which this
    # one contradicted. A DENYLIST here is how a latch happens: `json.loads` on ~20 000 nested
    # arrays raises RecursionError, which is neither OSError nor ValueError, so it escaped
    # `_load`, `read()`'s contract and `main()`'s handler — and because the read never reached the
    # reader, the poison entry was never rewritten: every later read raised until `--purge`.
    except Exception:
        return None  # a killed writer's half entry, or corruption. It proves nothing.
    if not isinstance(env, dict):
        return None
    if env.get("key") != key or env.get("scope") != scope:
        return None  # guard 1: no cross-key, no cross-scope poaching
    if projection is None:
        projection = INVARIANTS[key].projection
    if env.get("projection") != _projection_digest(projection, INVARIANTS[key].project):
        return None  # guard 4: an entry selected by a DIFFERENT projection is a stale SCHEMA
    if env.get("argv") != list(INVARIANTS[key].argv):
        return None  # an entry read from a DIFFERENT endpoint is a different question
    try:
        payload = base64.b64decode(env["payload"], validate=True)
        digest = env["sha256"]
        stored_at = float(env["stored_at"])
        stored_ttl = int(env["ttl_s"])
    # ⛔ CATCH BROADLY, ON PURPOSE. This block only decodes and coerces OUR OWN stored fields, so
    # there is no user code to hide from — and a DENYLIST here is how the latch happened:
    # `int(inf)` raises OverflowError, which is NOT a ValueError, so a tampered envelope crashed out
    # of `_load`, past `read()`'s contract and past `main()`'s handlers. Because the read never
    # reached the reader, the bad entry was never rewritten: every later read of that key raised
    # too, until `--purge`. A malformed entry must be a MISS, which is self-healing.
    except Exception:
        return None
    if not isinstance(digest, str) or hashlib.sha256(payload).hexdigest() != digest:
        return None
    if stored_ttl != int(ttl_s):
        return None  # a longer-stamped entry must not satisfy a tighter ask
    age = now - stored_at
    # ONE comparison, not two: `0 <= age < ttl`. Two separate returns is how NaN slipped through.
    if not (0 <= age < ttl_s):
        return None
    return payload


def _store(path: Path, blob: bytes) -> None:
    """Atomic: a killed writer leaves the old entry or nothing, never a half one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        tmp.write_bytes(blob)
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()


def _project(key: str, raw: bytes) -> bytes:
    """Decode a raw response and project it to the declared invariant subset.

    Every failure is ``Unreadable``: a body the projection does not recognise must never reach the
    cache, because a fresh entry built from an unknown body is a LIE rather than a saving.
    """
    spec = INVARIANTS[key]
    try:
        return json.dumps(spec.project(decode_pages(raw)), sort_keys=True).encode()
    except Unreadable:
        raise
    except Exception as exc:  # a projection that cannot run must not yield a cached answer
        raise Unreadable(f"projection for {key!r} failed: {exc}") from exc


_WRITE_WARNED = False


def _warn_write_failed(path: Path, exc: OSError) -> None:
    """One line, once per process: the cache is not being written, so this is a pass-through."""
    global _WRITE_WARNED
    if _WRITE_WARNED:
        return
    _WRITE_WARNED = True
    import sys as _sys

    # ⛔ THE ANNOUNCE MUST NOT PROPAGATE. This runs inside `read()`'s `except OSError` handler, so
    # an exception raised here escapes `read()` and DISCARDS the payload that was just read live —
    # the exact conflation the comment at the call site forbids ("a full disk into a total gh
    # outage"). A closed stream raises `ValueError`, a broken pipe `BrokenPipeError`; a diagnostic
    # that cannot be written is swallowed, because the operation outranks its narration.
    with contextlib.suppress(Exception):
        print(
            f"gh_read_cache: WARNING — could not write the cache entry ({path.parent}): {exc}. "
            f"Every read will be a LIVE call until this is fixed (this tool is a pass-through, not "
            f"a cache) — the expected-live-call burn may return.",
            file=_sys.stderr,
        )


# ── the read ─────────────────────────────────────────────────────────────────────────────────
def _credential_fingerprint() -> str | None:
    """A NON-REVERSIBLE fingerprint of the token that will answer a global read.

    Only a truncated SHA-256 is used: it separates two credentials, and it goes into the filename
    AND the plaintext envelope — so the raw token must never be there. A cache directory is a
    world-readable place to leak a secret.

    ⛔ NO TOKEN VISIBLE IS NOT "ONE ACCOUNT". `gh` may authenticate from ~/.config/gh/hosts.yml, so
    a constant here would make every account on the box share one identity entry. Unknown identity
    FAILS CLOSED: the global key is simply not cacheable and the caller reads live.
    """
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    if not token:
        return None
    return "tok:" + hashlib.sha256(token.encode()).hexdigest()[:12]


def scope_for(key: str, repo: str, branch: str) -> str:
    """Global keys are scoped by CREDENTIAL (not branch); everything else by repo AND branch.

    ⛔ THE EMPTY-INPUT REFUSAL LIVES HERE, NOT ONLY IN ``read()``, because this is the ONE function
    BOTH live paths call. `main()`'s ``--no-cache`` branch calls ``reader()`` directly and never
    enters ``read()``, so `--repo '' --no-cache` still spent a call on
    `gh api repos//branches/main/protection` (a guaranteed 404, reported as a GitHub fault) — the
    exact call the guard was added to prevent. An empty BRANCH had the same shape:
    ``f"{repo}@{branch}"`` gives ``"o/r@"``, which is neither empty nor ``"@"``-leading, so every
    check downstream accepted it and the request became ``branches//protection``.
    """
    if key in GLOBAL_KEYS:
        cred = _credential_fingerprint()
        if cred is None:
            raise TtlNotPermitted(
                f"{key!r} is global and no GH_TOKEN/GITHUB_TOKEN is set, so the answering account "
                f"cannot be identified — refusing to cache it. Set a token, or read it live."
            )
        return f"global@{cred}"
    if not isinstance(repo, str) or not repo.strip():
        raise TtlNotPermitted(
            f"{key!r} is repo-scoped and was given {repo!r} as the repo. Pass --repo owner/name "
            f"(or GH_REPO): an empty repo addresses `repos/` — a guaranteed 404 spent from the "
            f"very pool this module exists to protect."
        )
    if not isinstance(branch, str) or not branch.strip():
        raise TtlNotPermitted(
            f"{key!r} is repo-scoped and was given {branch!r} as the branch. Pass a non-empty "
            f"branch (`branches//protection` is not a route)."
        )
    return f"{repo}@{branch}"


def read(
    key: str,
    *,
    scope: str,
    cache_dir: str | os.PathLike[str],
    now: float,
    reader,
    ttl_s: int | None = None,
) -> bytes:
    """Serve a provably-fresh cached value, else call ``reader(key)`` once and store it.

    ``scope`` is REQUIRED and is part of the cache identity. For a global key it must be exactly
    the credential-derived scope ``scope_for()`` produces — ``read()`` RE-COMPUTES it rather than
    trusting the caller, because a caller-supplied ``"global@"`` would let two accounts share one
    identity entry.

    ``reader`` is injected on purpose: correctness is testable without a network, and the CLI is
    the only place that knows about `gh`.

    Raises:
        MutableReadRefused: the key is mutable or undeclared — before the reader is touched.
        TtlNotPermitted: bad/absent scope, or ``ttl_s`` above the ceiling.
        Unreadable: propagated from ``reader`` on a MISS. A stale entry is NEVER served instead.
    """
    ceiling = ttl_for(key)  # raises MutableReadRefused for a mutable/undeclared key
    # A clock is a NUMBER. `read(now="1000")` used to reach `math.isfinite` and raise TypeError from
    # inside the freshness test — an unhandled crash where the contract promises "any doubt → MISS".
    try:
        now = float(now)
    # ⛔ OverflowError IS NOT A ValueError: `float(10**400)` escaped this and crashed out of
    # `read()`, past the contract, past `main()`'s handlers — the very denylist mistake `_load`
    # documents having fixed, re-committed here.
    except (TypeError, ValueError, OverflowError) as exc:
        raise TtlNotPermitted(f"now must be a number of seconds, got {now!r}") from exc
    # Defence in depth for guard 2: the IMPORT-TIME check runs once, so a spec inserted straight
    # into INVARIANTS afterwards (bypassing declare_invariant) would otherwise be read and cached
    # with no projection at all. Cheap, and it makes guard 2 hold on the path that actually runs.
    _spec = INVARIANTS[key]
    _validate_spec(key, _spec.argv, _spec.projection, _spec.project)
    # ⛔ A repo-scoped key derived from an EMPTY repo yields `"@main"`, which passes the
    # non-empty-scope test and then spends one call from the very pool this module exists to
    # protect, on `gh api repos/` (a guaranteed 404) reported as a GitHub fault.
    if isinstance(scope, str) and scope.startswith("@"):
        raise TtlNotPermitted(
            f"{key!r} was given the scope {scope!r}: no repo. Pass --repo owner/name (or GH_REPO)."
        )
    if not isinstance(scope, str) or not scope.strip():
        raise TtlNotPermitted(
            f"{key!r} requires a non-empty scope (e.g. 'owner/repo@main'); without one, one repo's "
            f"answer is served as another's."
        )
    # ⛔ R1's GLOBAL HALF, RECOMPUTED — NOT A PREFIX TEST. A prefix test accepted any `global@…`
    # string, so two lanes with different tokens could both pass `"global@"` and share one entry.
    if key in GLOBAL_KEYS:
        expected = scope_for(key, "", "")  # raises when no credential is identifiable
        if scope != expected:
            raise TtlNotPermitted(
                f"{key!r} is global: its answer depends on the TOKEN, not on a repo. Pass exactly "
                f"scope_for({key!r}, …) — got {scope!r}, expected {expected!r}. Otherwise one "
                f"account's identity is served as another's."
            )
    try:
        effective = ceiling if ttl_s is None else int(ttl_s)
    except (TypeError, ValueError, OverflowError) as exc:  # same conversion hole as `now`
        raise TtlNotPermitted(f"ttl_s must be a positive int, got {ttl_s!r}") from exc
    if effective <= 0:
        raise TtlNotPermitted(f"ttl_s must be positive, got {ttl_s!r}")
    if effective > ceiling:
        raise TtlNotPermitted(
            f"{key!r} declares a {ceiling}s ceiling; {effective}s was asked for. Widening a TTL to "
            f"make something pass is how a cache starts reporting a past state as the current one "
            f"(#1910)."
        )

    path = Path(cache_dir) / _cache_path(key, scope, ttl_s=effective).name
    hit = _load(path, key, scope, now=now, ttl_s=effective)
    if hit is not None:
        return hit

    # MISS. ⛔ THE STALE ENTRY IS NOT CONSULTED AGAIN. If the reader fails we raise; serving the
    # expired payload here would answer a question that was never asked of GitHub.
    try:
        payload = reader(key)
    except Unreadable:
        raise
    except Exception as exc:  # every reader failure is "could not read"
        raise Unreadable(f"re-read of {key!r} failed: {exc}") from exc
    if not isinstance(payload, (bytes, bytearray)):
        raise Unreadable(f"reader for {key!r} returned {type(payload).__name__}, not bytes")
    # ⛔ GUARD 2 LIVES HERE, ON THE PATH THAT ACTUALLY RUNS. The reader supplies RAW response
    # bytes; `read()` decodes the (possibly paginated) stream and stores only the PROJECTION. Doing
    # it before the store — not only in `_gh_reader` — is what makes "no key caches a whole body"
    # true for EVERY caller, and it means a hit and a miss return identically-shaped bytes.
    payload = _project(key, bytes(payload))
    # ⛔ A CACHE-WRITE FAILURE MUST NOT FAIL A SUCCESSFUL READ. The value in hand was just read from
    # GitHub and is as fresh as anything can be; failing to STORE it is a lost optimisation, not a
    # lost answer. Fail-closed governs what may be SERVED from disk, never what may be RETURNED
    # after a live read — conflating them would turn a full disk into a total gh outage.
    try:
        _store(path, _envelope(key, scope, payload, now=now, ttl_s=effective))
    except OSError as exc:
        # ⛔ A WRITE FAILURE STILL RETURNS THE VALUE (it was just read live), but it must NOT be
        # silent: an unwritable cache dir turns this tool into a PASS-THROUGH that burns the pool as
        # before, and a caller cannot tell that from a cold cache. Warn once per process, on stderr,
        # exactly as the other non-caching path does.
        _warn_write_failed(path, exc)
    return payload


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# CLI — the only part that knows `gh` exists.
# ═══════════════════════════════════════════════════════════════════════════════════════════════
def default_cache_dir() -> Path:
    base = os.environ.get("GH_READ_CACHE_DIR")
    if base:
        return Path(base)
    return Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")) / "gh-read-cache"


def decode_pages(raw: bytes) -> list[Any]:
    """Decode a `gh api` stream that may hold SEVERAL concatenated JSON documents.

    `gh api --paginate` without `--slurp` emits one document per page (`[…] [… ]`), which
    `json.loads` cannot read as a whole. This is the decode step that replaces `--slurp` — which gh
    refuses to combine with `--jq`.
    """
    text = raw.decode("utf-8", "replace").strip()
    if not text:
        raise Unreadable("empty response body")
    dec = json.JSONDecoder()
    docs: list[Any] = []
    i = 0
    while i < len(text):
        while i < len(text) and text[i] in " \t\r\n":
            i += 1
        if i >= len(text):
            break
        try:
            doc, i = dec.raw_decode(text, i)
        except ValueError as exc:
            raise Unreadable(f"response is not JSON: {exc}") from exc
        docs.append(doc)
    if not docs:
        raise Unreadable("response held no JSON document")
    return docs


def _gh_reader(repo: str, branch: str):
    def _read(key: str) -> bytes:
        spec = INVARIANTS.get(key)
        if spec is None:
            raise Unreadable(f"no argv is declared for {key!r}")
        # ⛔ `{branch}` IS A PATH SEGMENT and must be percent-encoded: `release/1.0` spelled raw
        # addresses a DIFFERENT route (`…/branches/release/1.0/protection`), so every read for a
        # slash-named protected branch was refused. `{repo}` keeps its `/` — that one IS the path.
        enc_branch = quote(branch, safe="")
        cmd = [
            "gh",
            *[a.replace("{repo}", repo).replace("{branch}", enc_branch) for a in spec.argv],
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=120, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise Unreadable(f"{' '.join(cmd)} could not run: {exc}") from exc
        if proc.returncode != 0:
            raise Unreadable(
                f"{' '.join(cmd)} exited {proc.returncode}: "
                f"{(proc.stderr or b'').decode('utf-8', 'replace').strip()[:300]}"
            )
        # ⛔ RAW BYTES. The PROJECTION is applied by `read()`, never here — so guard 2 holds on
        # the library path too, not only on the CLI's. This used to project here as well, which
        # meant a caller following the documented `reader=my_reader` usage cached the WHOLE body
        # (measured: `pushed_at` and `open_issues_count` served for a 6-hour TTL).
        return proc.stdout

    return _read


def main(argv: list[str] | None = None) -> int:
    import time

    ap = argparse.ArgumentParser(description="TTL cache for declared-invariant `gh` reads.")
    ap.add_argument("--key", help="an invariant key (see --list)")
    ap.add_argument("--ttl", type=int, default=None, help="tighten the TTL (never widen)")
    ap.add_argument("--repo", default=os.environ.get("GH_REPO", ""), help="owner/name for {repo}")
    ap.add_argument("--branch", default=os.environ.get("GH_BRANCH", "main"))
    ap.add_argument("--cache-dir", default=None)
    ap.add_argument("--no-cache", action="store_true", help="read live; bypass the cache entirely")
    ap.add_argument("--list", action="store_true", help="print the registry and exit")
    ap.add_argument("--purge", action="store_true", help="remove every entry and exit")
    a = ap.parse_args(argv)

    cache_dir = Path(a.cache_dir) if a.cache_dir else default_cache_dir()

    if a.list:
        for key in sorted(INVARIANTS):
            print(f"{key}\t{INVARIANTS[key].ttl_s}s\t{INVARIANTS[key].projection}")
        return 0

    if a.purge:
        n = 0
        if cache_dir.is_dir():
            for p in cache_dir.glob(_CACHE_PREFIX + "*"):
                try:
                    p.unlink()
                    n += 1
                except OSError:
                    pass
        print(f"purged {n} entr{'y' if n == 1 else 'ies'} from {cache_dir}", file=sys.stderr)
        return 0

    if not a.key:
        ap.error("--key is required (or --list / --purge)")

    reader = _gh_reader(a.repo, a.branch)

    # ⛔ A GLOBAL KEY WITH NO IDENTIFIABLE CREDENTIAL STILL HAS A LIVE ANSWER. `scope_for` refuses
    # (correctly — it must not guess an account), so this tool must NOT dead-end there: it reads
    # live and says so, which is exactly what the docstring promises. `--no-cache` is the explicit
    # spelling of the same path.
    try:
        scope = scope_for(a.key, a.repo, a.branch)
    except TtlNotPermitted as exc:
        if a.key in GLOBAL_KEYS:
            print(f"gh_read_cache: live read (not cached) — {exc}", file=sys.stderr)
            try:
                # ⛔ PROJECTED, like every other exit path. `--no-cache` means "bypass the cache",
                # NOT "change the document": emitting the raw body here gave one key two output
                # schemas, so a caller written against the documented shape read NO required checks
                # from a live body that named one.
                sys.stdout.buffer.write(_project(a.key, reader(a.key)))
            except Unreadable as r_exc:
                print(f"gh_read_cache: REFUSED — {r_exc}", file=sys.stderr)
                return 1
            return 0
        print(f"gh_read_cache: REFUSED — {exc}", file=sys.stderr)
        return 1

    if a.no_cache:
        try:
            sys.stdout.buffer.write(_project(a.key, reader(a.key)))
        except Unreadable as exc:
            print(f"gh_read_cache: REFUSED — {exc}", file=sys.stderr)
            return 1
        return 0

    try:
        body = read(
            a.key,
            scope=scope,
            cache_dir=cache_dir,
            now=time.time(),
            reader=reader,
            ttl_s=a.ttl,
        )
    except (MutableReadRefused, TtlNotPermitted, Unreadable) as exc:
        print(f"gh_read_cache: REFUSED — {exc}", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
