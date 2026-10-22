#!/usr/bin/env python3
"""work_claim — the CLAIM on a unit of work, stored in the Tortoise graph (#5052, #7871).

WHY THIS EXISTS (measured 2026-10-21, all three cells)
-----------------------------------------------------
Two boxes were handed the SAME fix and produced two PRs (#2247, #2250) and nothing
noticed. The coordination state that should have prevented it does not exist in any
store a peer can read: claims live in `refs/claims/*` on a git origin (delete-on-release,
so no history), lane/capacity state in `~/.pi/agent/state/fleet-state.json` and
`/tmp/cell-home/orchestrator/state/*` (one box's disk), and the worklist in the
orchestrator's own memory. `tools/who_is_on.py` can only enumerate what a *remote* ref
happens to name, which is the class #4256 recorded.

This tool moves exactly ONE slice into the graph: **who holds a unit of work, right now**.

WHAT IT STORES
--------------
One `:Point` per held unit, id = a DETERMINISTIC digest of `(repo, key)`, and every write
is ONE atomic `MERGE`-on-id statement carrying a per-call token. `MERGE` applies its
`ON CREATE` exactly once, so only one contender can create the id; the token reports whose
create ran (a consistency check), and the CALLER's verdict is the content comparison — a
byte-identical payload is the same identity, not a second holder.

    work-claim:<sha256(repo + key)>  pointKind=work-claim
      props: repo, key, owner, session, branch, head, lease_until, epoch,
             purpose, claimed_at, host, ncpu, lpc, lanes
      content: the same fields in ONE canonical line (the CAS compares CONTENT)

⛔ `create_point` IS NOT A CAS, and the first version of this tool was wrong to say it was.
Measured on a live FalkorDB (this branch): two `create_point(id='probe-dup-1')` calls left
TWO `:Point` nodes with the same id — contents `A` and `B` — and `get_point` returned A. The
loser's node is created and invisible to any read by id, so a property query reports one
unit of work TWICE with two owners. `MERGE (n:Point {id:$id}) ON CREATE SET … , n.__cas=$token`
keeps the id unique and returns `n.__cas = $token`, which only the creator can see: the
loser's statement matches the existing node and returns the winner's content.

⚠️ THE ID MUST NOT CONTAIN THE BOX. `claims.sh` learned this the hard way and says so:
"a KEY CONTAINING THE BOX ID IS NOT A CAS. Two boxes mint two DISTINCT refs and BOTH
pushes succeed". Two boxes must compute the SAME id for the same unit of work, or this
is a ledger and not a lock.

THE FIVE PROPERTIES (each one is a copy of something `claims.sh` already proved)
--------------------------------------------------------------------------------
1. **Content-addressed key** — `issue-<N>` / `pr-<N>`, never a box, never a random id.
2. **Create = CAS** — an atomic `MERGE`-on-id with a per-call token; the loser's statement
   matches the existing node and REFUSES (rc 1).
3. **Read back, never trust the write** — the write's return value is re-read by id and
   the content compared. A store that answers with something else is a lost race.
4. **The lease is DATA, not liveness** (#1709 rec 3) — a remote pid cannot be probed, so
   `lease_until` in the record is what frees a claim, and an expired claim is reclaimable.
5. **Fencing** (#1709 rec 6) — a reclaim bumps `epoch`, and the bump is conditional: the write
   applies only while the stored epoch is the one we read (`replace_if_epoch`). A stale holder's
   release is conditional the same way, so it cannot erase a peer's newer record. A store that
   cannot express the precondition (a `update_point`-style blind write) makes `acquire` REFUSE to
   reclaim (rc 2) rather than double-granting — a capability the store does not have is not a
   gap to paper over.

⛔ FAIL-CLOSED, AND THAT IS A DELIBERATE DEPARTURE — SEE `OVERRIDES:` BELOW
-------------------------------------------------------------------------
`claims.sh`'s merge path is **fail-open on purpose** (recorded 2026-10-14 on
agent-infra#1709): "duplicate execution is the assumption … every side effect on this
path is idempotent per head, and failing closed would stall the whole fleet on one
transient network error". That reasoning holds for a review/merge claim, whose duplicate
cost is one wasted review. It does NOT hold at DISPATCH, which is where #2247/#2250 were
produced: a dispatch that proceeds while the claim store is unreadable is the exact
failure this tool exists to prevent, and its duplicate cost is a whole lane.

    OVERRIDES: the fleet claim registry is fail-OPEN when its store is unreachable
    (agent-infra#1709, 2026-10-14 — chosen because review/merge duplicates are
    idempotent per head) — this dispatch-time claim is fail-CLOSED instead, because
    the measured cost of proceeding blind at dispatch is a duplicated lane and PR
    (#2247/#2250), not a repeated idempotent side effect.

So `rc 2` is a REFUSAL, never a warning: no claim, no dispatch. And rc 2 is returned for
every shape of "I cannot answer", including the one #7871 measured as silently wrong —
an UNSET store URI. The SDK falls back to a default/embedded target when `TORTOISE_DB_URI`
is unset, and an empty read from the wrong target is indistinguishable from "nobody holds
this". An unset store is therefore refused before any query is attempted.

CALLER CONTRACT (drop-in vocabulary with `claims.sh`, so a caller needs no new branch)
------------------------------------------------------------------------------------
    rc 0  acquired (or reclaimed an EXPIRED claim, or `already-held` — the caller already
          holds it, so a retry is not a second grant — or a release that succeeded, or a
          `status` that ANSWERED — including `status: held`, see below)
    rc 1  held by a LIVE claim (or a release refused: not the caller's / epoch moved)
    rc 2  the store could not answer — REFUSE the dispatch, do not proceed blind
    rc 3  the INVOCATION was malformed (no number where one is required, `--force` without
          `--reason`, an unparseable argument list). Deliberately distinct from rc 2: a
          controller retries an unanswerable store, and a retry of a malformed command can
          never succeed.

⚠️ `status` ANSWERS, so its rc is the answer's deliverability, not the claim's liveness:
`free`, `expired` and `held` are all rc 0 (the JSON `status` field carries which, and
`--json` is how a controller branches). `claims.sh`'s own status verb is the OPPOSITE
polarity — rc 1 when UNHELD, rc 0 when held (`scripts/orchestrator-harness/claims.sh`,
`_claim_status_at`) — so a caller porting from it must branch on `acquire`/`release` rc
(the same in both tools) or on the status FIELD, never on `status`'s exit code. Naming it
here is what makes the table usable; a silent difference is what made it a finding
(review of #9487).

`--json` emits one object with `status`, `owner`, `lease_until`, `epoch`, `reason` so a
controller can log the measured reason instead of guessing.

WHY THE CAPACITY FIELDS ARE HERE (and why this is NOT a second probe)
--------------------------------------------------------------------
The in-flight `capacity.sh` (`refs/boxes/<host>/<record>`) already MEASURES each box —
`host`, `ncpu`, `load1`, `lpc`, `lanes`, disk, memory. This tool never measures anything:
it accepts those fields on the command line and persists them WITH the claim, so the
fleet's capacity map and its claim map are the same record and a reader can answer "who
holds this, and on how loaded a box" from one store. Passing them is optional, and a field
that was NOT supplied is stored as `absent` — an explicit marker, never a zero. That
distinction is the whole point: `ncpu=0` is a MEASURED idle box, and a claim taken without a
measurement must not assert one (review of #9487). `status --repo --json` carries `host`,
`ncpu`, `lpc` and `lanes` through, so the shipped reader answers that question without
hand-written Cypher.

USAGE
-----
⛔ The file is committed `100644` (not executable), so every invocation goes through an
explicit interpreter: a bare `tools/work_claim.py …` is `Permission denied` — shell exit 126,
outside the `rc 0/1/2/3` contract below (review of #9487). The repo's sibling tools document
the same form.

    uv run python tools/work_claim.py acquire --repo daniel-ospina/tortoise --issue 5052 \
        --session cell-t1 --branch feat/x --purpose "claim registry pilot"
    uv run python tools/work_claim.py status --repo daniel-ospina/tortoise [--json]
    uv run python tools/work_claim.py status --repo daniel-ospina/tortoise --issue 5052 --json
    uv run python tools/work_claim.py release --repo daniel-ospina/tortoise --issue 5052 \
        --session cell-t1
    uv run python tools/work_claim.py release --repo daniel-ospina/tortoise --issue 5052 \
        --session cell-t9 --force --reason "holder crashed, lease expired 2h ago"
    uv run python tools/work_claim.py check   # is the store reachable AND shared? prints the
                                              # target; rc 2 = no (a box-local target is rc 2
                                              # unless --allow-box-local acknowledges a trial)

`--repo` is HALF OF THE CLAIM IDENTITY, so it is validated and canonicalised before any key is
built: `owner/name`, exactly one `/`, both halves non-empty, lowercased because GitHub repo names
are case-insensitive. A bare name (no `/`) is `EXIT_USAGE` (3); a CASE VARIANT of a valid
`owner/name` is accepted and canonicalised onto the same claim — never a second id, and never a
silent empty answer. The `--issue`/`--pr` key half is canonicalised the same way (see
`canonical_key`), because an unvalidated spelling on EITHER half gave the same unit of work two
ids (both boxes reported `acquired`) and made `status --repo` answer `0 claim record(s)` at rc 0
over a store that held claims.

STORE
-----
`TORTOISE_DB_URI` — the shared graph (docker://… or bolt://…) — and NOTHING ELSE. The SDK
reads only that variable; `TORTOISE_API_URL` names a hosted HTTP surface this tool has no
client for, so accepting it as proof of a store disarmed the guard and let the SDK fall back
to this box's EMBEDDED file. Measured on this branch: `check` printed `ok: claim store
reachable` and exited 0 while the claim lived in a box-local file that every peer reads as
free — the duplicate-dispatch failure, reported as a success.

⚠️ The guard validates that a store is CONFIGURED, not that it is SHARED. A loopback URI
(`docker://…@127.0.0.1:16379/…`) is exactly as invisible to a peer as the embedded file, and
every cell's own graph looks like that (see the evidence README §1). So EVERY verb that
consults the store — `acquire`, `status`, `release`, and `check` (which a dispatch flow
gates on) — refuses a loopback target unless `--allow-box-local` acknowledges a single-box
trial (`status` included: its `free` is what a controller dispatches on). `check` prints the
target with the password REDACTED, and returns rc 2 — not a WARNING on stderr — when that
target is box-local.

There is deliberately no default: see the fail-closed note.
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/work_claim.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/work_claim.py`"
    )

import argparse
import hashlib
import json
import os
import socket
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

# The kind is registered by `register_claim_kind()` below — NOT by importing this module.
# The registry is per-process (`domain_loader.register_kind` mutates an in-memory set), and
# loading it pulls the pack/ontology tree in and prints its boundary warnings, so registering
# at import time would put that noise on `--json`'s stdout. `SDKClaimStore.__init__` calls it,
# so the write path needs nothing from a caller; a second consumer (a reader) calls it
# explicitly, because a reader that forgot would get a warn-not-block unknown-kind warning on
# every query and could sort the claim out of a kind-filtered read. Promoting it into
# `packs/agent-ops/manifest.yaml` is the follow-up once the pilot has a second consumer — that
# is a pack/ontology change and this module does not make it.
CLAIM_KIND = "work-claim"

#: The content-line marker for a capacity field that was NEVER supplied. Deliberately not `0`:
#: this record doubles as the fleet's capacity map, so "not measured" and "measured idle" must
#: be different values (review of #9487).
CAP_ABSENT = "absent"


def register_claim_kind() -> str:
    """Register `CLAIM_KIND` in THIS process' kind registry; returns the kind.

    Idempotent (`register_kind` adds to a set) and explicit rather than an import-time side
    effect — see the comment on `CLAIM_KIND` for why. `SDKClaimStore.__init__` calls it, so
    the writer needs no extra step; a reader that imports this module for `claim_id()` /
    `_parse()` / `CLAIM_KIND` must call it before a kind-filtered read.

    Review of #9487: the comment here used to say the kind "is registered at import", which
    was false — importing the module registered nothing — so exactly the forgotten-
    registration failure that comment described was reachable by the reader it was about.
    """
    from tortoise.domain_loader import register_kind

    register_kind(CLAIM_KIND)
    return CLAIM_KIND


def _cap_token(value: int | None) -> str:
    """A capacity field as it appears in the canonical content line (see `CAP_ABSENT`)."""
    return CAP_ABSENT if value is None else str(int(value))

#: Seconds a claim is held before a peer may reclaim it. Mirrors `claims.sh`'s issue-claim
#: default (3 h) and is clamped by the caller, never extended implicitly: a lease that grows
#: on its own is a claim nobody can expire.
DEFAULT_TTL = 3 * 60 * 60

#: A key longer than this is a caller bug (a branch or a session id pasted into the key),
#: and the length is part of the identity — refuse rather than truncate, because truncating
#: would make two different units collide on one claim.
MAX_KEY_LEN = 64

EXIT_OK = 0
EXIT_HELD = 1
EXIT_UNANSWERABLE = 2
# A distinct code for a malformed invocation (review of #9487 P2). `status --repo` with no
# number used to reach `normalise_key(0)` → rc 2, which a controller reads as "the store is
# unanswerable" and retries forever; the invocation was wrong and nothing was unreachable.
EXIT_USAGE = 3


def _audit_text(value: str) -> str:
    """Keep a caller-supplied audit reason verbatim INSIDE the bracketed `[released: …]`
    marker that `release --force` writes into the claim's purpose.

    The marker is fenced by `[` and `]`, so an unescaped `]` in the reason used to close it
    early — and the old fix for that (`reason.replace("]", ")")`) silently rewrote the
    operator's words, `a]b` becoming `a)b`, in the one field that exists to record WHY a
    claim was taken from its holder (review of #9487 nit). Escaping keeps the text readable
    and the fence unambiguous.
    """
    return value.replace("\\", "\\\\").replace("]", "\\]")


class StoreUnanswerable(RuntimeError):
    """The store could not answer. Always a REFUSAL (rc 2), never a default answer.

    The message is REDACTED at construction (review of #9487 P2). A refusal embeds the text
    of the driver exception that produced it (`_rows`, and the store-open path in `main`),
    and a connection error from a `docker://`/`bolt://` DSN routinely quotes the DSN —
    password included — at whichever layer failed. `store.target()` was the only site that
    called `_redact_uri`, so the one path a reviewer looks at was safe while the ERROR path
    printed the credential into `--json` on stdout, exactly where a controller captures it.
    Redacting here covers every site that interpolates `str(exc)` without each having to
    remember, and `_redact_uri` is a pure string operation with no userinfo-free rewrite.
    """

    def __init__(self, message: str = "") -> None:
        super().__init__(_redact_uri(str(message)))


@dataclass
class ClaimRecord:
    """The claim as it is stored — one canonical line of content plus flat props.

    `content` is the CAS payload: the whole record in a fixed field order, so two boxes
    computing the same claim produce byte-identical content and a read-back comparison is
    an equality test rather than a semantic one.
    """

    repo: str
    key: str
    owner: str
    session: str = ""
    branch: str = ""
    head: str = ""
    lease_until: int = 0
    epoch: int = 1
    purpose: str = ""
    claimed_at: int = 0
    host: str = ""
    #: `None` means NOT SUPPLIED — serialised as `CAP_ABSENT`, never as `0`. An unmeasured
    #: claim and a measured idle box (`ncpu=0`) are different facts, and this record is the
    #: fleet's capacity map too (review of #9487).
    ncpu: int | None = None
    lpc: int | None = None
    lanes: int | None = None

    def content(self) -> str:
        # Field order is part of the contract: a reordering would make every historical
        # claim unreadable by identity. Values are quoted where they may contain spaces.
        def q(v: Any) -> str:
            # ALWAYS quoted (review of #9487): the conditional form left a value containing a
            # quote but no space unbalanced, and `_parse` then swallowed every field after it
            # (purpose absorbed claimed_at/host/ncpu/lpc/lanes). Round-trip stability is the
            # contract, so the escape is unconditional.
            s = str(v)
            escaped = s.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{escaped}"'

        return " ".join(
            [
                f"work-claim repo={q(self.repo)}",
                f"key={q(self.key)}",
                f"owner={q(self.owner)}",
                f"session={q(self.session)}",
                f"branch={q(self.branch)}",
                f"head={q(self.head)}",
                f"lease_until={int(self.lease_until)}",
                f"epoch={int(self.epoch)}",
                f"purpose={q(self.purpose)}",
                f"claimed_at={int(self.claimed_at)}",
                f"host={q(self.host)}",
                f"ncpu={_cap_token(self.ncpu)}",
                f"lpc={_cap_token(self.lpc)}",
                f"lanes={_cap_token(self.lanes)}",
            ]
        )

    def props(self) -> dict[str, Any]:
        props: dict[str, Any] = {
            "repo": self.repo,
            "claimKey": self.key,
            # ⚠️ NOT `owner`: the SDK reserves `owner` for tenant ownership and SILENTLY
            # ignores a client-supplied value ("ignoring client-supplied 'owner' on tenant
            # props", verified on a live write). A claim whose owner prop is dropped is a
            # claim nobody can query by holder, so the prop is namespaced like `claim_epoch`.
            "claim_owner": self.owner,
            "session": self.session,
            "branch": self.branch,
            "head": self.head,
            "lease_until": int(self.lease_until),
            "claim_epoch": int(self.epoch),
            "purpose": self.purpose,
            "claimed_at": int(self.claimed_at),
            "host": self.host,
        }
        # A capacity field that was never supplied is OMITTED from the props, not written as
        # 0: the graph is the fleet's capacity map, and a fabricated zero there reads as a
        # measured idle box (review of #9487).
        for name in ("ncpu", "lpc", "lanes"):
            value = getattr(self, name)
            if value is not None:
                props[name] = int(value)
        return props


class ClaimStore(Protocol):
    """The calls the CAS needs, plus two OPTIONAL capabilities.

    `create` is the atomic "create only if absent" and `replace_if_epoch` the fenced update;
    both return whether THIS caller won. `replace` is a release: it must be conditional on the
    epoch the caller observed (a blind overwrite can erase a peer's reclaim).

    Two capabilities are optional because a store may not be able to express them, and their
    ABSENCE is an answer rather than a default:

    - `replace_if_epoch(cid, record, expected_epoch) -> str | None` — the fenced reclaim. `None`
      means the precondition failed (a peer moved the epoch) and the caller lost the race. A
      store without it cannot reclaim: two boxes would both report `acquired`.
    - `query_repo(repo) -> list[dict]` — the repo-wide listing. A store without it must make
      `status --repo` REFUSE (rc 2), never answer `0 claim records`.
    - `require_shared() -> str | None` — a refusal reason when the target is not readable by a
      peer (box-local). Absent means "no opinion", which is not the same as "shared".
    """

    def read(self, claim_id: str) -> str | None: ...

    def create(self, claim_id: str, record: ClaimRecord) -> str: ...

    def replace(self, claim_id: str, record: ClaimRecord) -> str: ...


def _redact_uri(uri: str) -> str:
    """`docker://:s3cret@host:6379/g` → `docker://***@host:6379/g`.

    The documented URI form carries a password, and `check` runs in routine diagnostics whose
    output is pasted into issues and controller logs.

    The userinfo is everything up to the LAST `@` before the path — not the first. The earlier
    `//[^/@\\s]*@` stopped at the first `@`, so a password CONTAINING one (`:p@ssw0rd@host`) was
    printed with its tail intact (`docker://***@ssw0rd@host`) by `check` and `store.target()`,
    in the very output this function exists to make safe to paste (review of #9487).
    ``urlsplit`` does the same split (`netloc.rpartition('@')`), so this matches it.
    """
    import re

    return re.sub(r"//[^/\s]*@", "//***@", uri)


def _uri_host(uri: str) -> str:
    from urllib.parse import urlsplit

    try:
        return (urlsplit(uri).hostname or "").strip().lower()
    except ValueError:
        return ""


def normalise_repo(repo: str) -> str:
    """The repo half of a claim identity: validated, and CANONICALISED to one spelling.

    ⛔ `--repo` used to be used verbatim, and it is half of `claim_id`'s digest — so two boxes
    that spelled the same repo differently (`daniel-ospina/tortoise` vs `TORTOISE`, or a bare
    name) computed DIFFERENT ids for the same unit of work: both won their `MERGE`, both were
    told `acquired`, and the duplicate dispatch this tool exists to prevent happened anyway.
    The same variance made `query_repo`'s exact `n.repo = $repo` match return nothing, so
    `status --repo` answered `0 claim record(s)` at rc 0 over a store that held claims —
    precisely the "an empty answer from an unread store looks exactly like 'nobody holds
    anything'" defect the refusal branch exists to remove (review of #9487 P2).

    GitHub repo names are case-insensitive, so the canonical form is lowercased, and the shape
    is enforced (exactly one `/`, both halves non-empty) rather than assumed: the key half is
    canonicalised at the same boundaries (`canonical_key`), so neither half of the digest can be
    spelled two ways into two claims. `ValueError` is the caller's to turn into `EXIT_USAGE`.
    """
    text = (repo or "").strip()
    owner, sep, name = text.partition("/")
    if not sep or not owner or not name or "/" in name:
        raise ValueError(
            f"--repo must be owner/name (exactly one '/', both halves non-empty), got {repo!r} "
            "— the repo is half of the claim identity, so a different spelling is a different "
            "claim"
        )
    return f"{owner.lower()}/{name.lower()}"


def canonical_key(key: str) -> str:
    """The KEY half of a claim identity: validated, and CANONICALISED to one spelling.

    ⛔ The key is the other half of `claim_id`'s digest and used to be digested AS TYPED, so
    `issue-5052`, `ISSUE-5052`, `issue-5052 ` (trailing space) and `""` were FOUR ids for ONE
    unit of work — four `acquired` outcomes, the same double grant the repo half of this PR
    removes, reachable from a library caller that never passes through `main()` (which is why
    the CLI alone was not enough: `acquire`, `release` and `status` are the public entry points
    a store split or a peer script calls, and `tools/ci_selection.py`'s carve-out comment
    already anticipates that split). So the shape is enforced at every boundary and the
    spelling is collapsed: lowercased, surrounding whitespace stripped.

    The BODY is opaque, not numeric: the live legs mint a per-run `issue-<12 hex>` key on
    purpose (a fixed key let concurrent runs fence each other — see `_probe_key` in the test
    file), so a numeric-only rule would delete that property, and a leading-zero rule would
    reject a legitimate hex body one run in ~200. The numeric form is produced by
    `normalise_key` from an int; a caller that invents a body has invented a DIFFERENT key,
    which is a legitimate thing to do — what may not happen is two spellings of the SAME key
    becoming two claims. `ValueError` is the caller's to turn into `EXIT_USAGE`.
    """
    text = (key or "").strip().lower()
    kind, sep, body = text.partition("-")
    if (
        not sep
        or kind not in ("issue", "pr")
        or not body
        or "/" in body
        or any(c.isspace() for c in body)
    ):
        raise ValueError(
            f"claim key must be <issue|pr>-<body> (one '-', a non-empty body with no spaces), "
            f"got {key!r} — the key is half of the claim identity, so a different spelling is "
            "a different claim"
        )
    if len(text) > MAX_KEY_LEN:
        raise ValueError(f"claim key {text!r} is longer than {MAX_KEY_LEN} characters")
    return text


def claim_id(repo: str, key: str) -> str:
    """The deterministic point id. Canonical `<hex>-<12 hex>` shape so the SDK does not warn
    on a non-ULID id, and derived ONLY from (repo, key) so two boxes compute the same one —
    the property that makes this a CAS instead of a ledger.

    BOTH halves are normalised INSIDE the digest rather than only at the CLI boundary (review
    of #9487 P2): the identity must be one id per unit of work no matter which entry point
    (CLI, a library caller, a peer box) asked for it, and a caller that passed either half
    straight through spelled the CAS away. A malformed half raises here (the caller's
    contract); it never mints a second id."""
    digest = hashlib.sha256(f"{normalise_repo(repo)}\x00{canonical_key(key)}".encode()).hexdigest()
    return f"{digest[:20]}-{digest[20:32]}"


def normalise_key(kind: str, number: int) -> str:
    """`issue-<N>` / `pr-<N>`. Disjoint by prefix, like `claims.sh`'s two keyings, so an
    issue claim never blocks a PR claim for the same number."""
    if kind not in ("issue", "pr"):
        raise ValueError(f"unknown claim kind {kind!r} (expected 'issue' or 'pr')")
    if number <= 0:
        raise ValueError(f"claim number must be positive, got {number!r}")
    # One definition of the key contract: the builder canonicalises through the same validator
    # every entry point uses, so a key `main()` mints and a key `acquire()` accepts cannot
    # disagree about what is canonical (review of PR #9537).
    return canonical_key(f"{kind}-{number}")


def _require_store_configured(environ: dict[str, str] | None = None) -> str:
    """Refuse an UNSET store before touching it.

    This is the one branch that must not be reachable by accident: with no URI the SDK
    answers from a default target, and "0 rows" from the wrong graph is indistinguishable
    from "nobody holds this" (#7871 gap 1 measured this as the real read defect — "an
    unset URI returning 0 rows silently"). A caller that genuinely wants the embedded
    target must name it.
    """
    env = os.environ if environ is None else environ
    uri = (env.get("TORTOISE_DB_URI") or "").strip()
    api = (env.get("TORTOISE_API_URL") or "").strip()
    if not uri:
        detail = ""
        if api:
            # Measured (review of #9487): accepting the API URL as proof of a store armed the
            # guard and then let `TortoiseSDK()` ignore it — the SDK reads ONLY
            # TORTOISE_DB_URI and falls back to this box's embedded file. The claim landed
            # locally, the read-back confirmed the local write, and the tool reported
            # `acquired` while every peer read `free` — the duplicate-dispatch failure this
            # tool exists to prevent, reported as a win. Naming the API URL again must not
            # re-open that, so the message says why instead of just what.
            detail = (
                " TORTOISE_API_URL is set, but this tool has no hosted client: the SDK consumes "
                "ONLY TORTOISE_DB_URI and would silently fall back to the BOX-LOCAL embedded "
                "database, where a claim reads as acquired here and FREE on every peer. Point "
                "TORTOISE_DB_URI at the shared graph instead."
            )
        raise StoreUnanswerable(
            "no claim store configured: set TORTOISE_DB_URI (the shared graph). An unset "
            "store is refused rather than answered from a default target — an empty read "
            "from the wrong graph looks exactly like 'nobody holds this' (#7871)." + detail
        )
    return uri


class SDKClaimStore:
    """The shipped store: `:Point` nodes in the Tortoise graph, written atomically.

    Every write is ONE Cypher statement, so the graph applies it atomically (FalkorDB runs a
    query single-threaded):

        MERGE (n:Point {id:$id}) ON CREATE SET n += $props, n.content=$content, n.__cas=$token

    `MERGE` applies its `ON CREATE` clause exactly once, so two boxes cannot both create the
    id; the loser's statement matches the existing node and returns the winner's content. The
    token reports whether THIS call ran the create, which `create` uses as a consistency check
    — the verdict belongs to the content comparison, because a contender that writes a
    byte-identical record is the same identity, not a second holder.

    ⛔ `create_point` is NOT a CAS and is deliberately NOT used here. Measured on a live graph
    (this worktree, `falkordb`): two `create_point(id='probe-dup-1')` calls left TWO `:Point`
    nodes with the same id and contents `A` and `B`, and `get_point` returned A — the loser's
    node is created but invisible to every read by id. A listing that queries by property then
    reports one unit of work TWICE with two owners. `MERGE` on the id is the only primitive
    here that keeps id unique, and it carries the token test above.

    State transitions (acquire, reclaim, release) are conditional on `claim_epoch`, so a
    stale holder cannot overwrite a peer's newer record: the statement touches 0 rows and the
    caller loses.
    """

    def __init__(self, sdk: Any = None, *, allow_box_local: bool = False) -> None:
        # Register the kind HERE rather than at import (see the comment on `CLAIM_KIND`): the
        # writer's own raw-Cypher path never needed it, but every later kind-filtered read of
        # these points does, and an injected client (tests) needs it just the same.
        register_claim_kind()
        if sdk is None:
            from tortoise.sdk import TortoiseSDK

            sdk = TortoiseSDK()
        # Belt and braces over `_require_store_configured` (review of #9487 P0), and it applies
        # to an INJECTED client too: if a caller reaches this class directly with a target that
        # resolved to the box-local file, the write would be confirmed by its own read-back and
        # reported as an acquisition while every peer reads FREE — the duplicate-dispatch
        # failure in its purest form. An embedded target is not a shared claim store.
        if getattr(sdk, "_db_uri", None) is None:
            raise StoreUnanswerable(
                "the SDK resolved to this box's EMBEDDED database, not a shared graph — "
                "a claim written there reads as acquired here and free on every peer. "
                "Set TORTOISE_DB_URI to the shared store (docker://… or bolt://…)."
            )
        self._sdk = sdk
        self._allow_box_local = allow_box_local

    def _rows(self, cypher: str, **params: Any) -> list[list[Any]]:
        """One statement in, rows out. Any failure is UNANSWERABLE — never an empty answer."""
        try:
            return self._sdk._get_proj().g.query(cypher, params=params).result_set or []
        except Exception as exc:
            # REDACT BEFORE TRUNCATING (review of #9487 P2): `_redact_uri` needs the `@` that
            # ends the userinfo, so a driver message longer than the cut whose DSN ends past it
            # was truncated into `docker://:<partial-password>` — no `@`, nothing to redact, and
            # the credential tail printed into `--json`. Redaction first, then the cut.
            raise StoreUnanswerable(
                f"the claim store could not answer "
                f"({type(exc).__name__}: {_redact_uri(str(exc))[:200]})"
            ) from exc

    def read(self, cid: str) -> str | None:
        # ORDER BY epoch: a graph written by the pre-MERGE version may hold duplicate nodes for
        # one id (that version used create_point, which does not enforce uniqueness). Reading the
        # highest epoch is the one answer that both versions agree on.
        rows = self._rows(
            "MATCH (n:Point {id:$id}) RETURN n.content AS content "
            "ORDER BY n.claim_epoch DESC LIMIT 1",
            id=cid,
        )
        if not rows:
            return None
        content = rows[0][0]
        # A property the store hands back with a non-string type is not content this parser can
        # read: `_parse`/`_tokens` iterate it character by character, so an int/list raised
        # outside every handler and left the process on exit 1 = EXIT_HELD (review of #9487 P2).
        if content is not None and not isinstance(content, str):
            raise StoreUnanswerable(
                f"the claim store returned {type(content).__name__} content for id {cid} — "
                "refusing to parse it as a claim record"
            )
        return content or ""

    def _transition(
        self, cid: str, record: ClaimRecord, *, expected_epoch: int | None
    ) -> str | None:
        """Atomically set the record, optionally only when `claim_epoch` is still what we read.

        `None` means the precondition failed (a peer moved it) — the caller lost the race.
        `count(n) > 1` is a store we refuse to interpret: two nodes for one id means a write
        bypassed the MERGE, and picking one would be a guess.
        """
        props = {**record.props(), "pointKind": CLAIM_KIND}
        match = "MATCH (n:Point {id:$id})"
        if expected_epoch is not None:
            match += " WHERE n.claim_epoch = $expected"
        # A capacity field that was NOT supplied is REMOVED, never left standing (review of
        # #9487 P1). `props()` omits an absent field on purpose — a fabricated `0` reads as a
        # measured idle box — but `SET n += $props` can only ADD keys: a reclaim or a release
        # over a node a PREVIOUS holder wrote kept that holder's `ncpu`/`lpc`/`lanes` while
        # the content line this same statement writes says `ncpu=absent`. The two halves of
        # the record then disagree, and the prop half asserts a peer's measurement as this
        # claim's capacity — the fabricated-capacity defect the `CAP_ABSENT` design exists to
        # prevent, with a stale number instead of a zero. `REMOVE` before the `SET` makes the
        # abstention hold for BOTH readers (`query_repo` parses content, the docstring tells
        # others to query props). Verified on a live graph: `MATCH … REMOVE … SET n += $props`
        # is accepted by FalkorDB and the props are gone afterwards.
        absent = [f"n.{name}" for name in ("ncpu", "lpc", "lanes") if getattr(record, name) is None]
        remove = (" REMOVE " + ", ".join(absent)) if absent else ""
        rows = self._rows(
            match + remove + " SET n += $props, n.content = $content RETURN count(n) AS c",
            id=cid,
            expected=expected_epoch,
            props=props,
            content=record.content(),
        )
        moved = rows[0][0] if rows else 0
        if moved == 0:
            # 0 rows is the fence doing its job — UNLESS the store does not hold the epoch we
            # fenced on, in which case the caller would be told "a peer moved it" (rc 1,
            # retryable) about a record that never moved, on a retry that can never succeed
            # (review of #9487 P2). Ask what the epoch ACTUALLY is now: a node whose epoch
            # equals the one we expected should have matched our `WHERE`, so it is a store we
            # refuse to interpret rather than a race we lost. An absent node is a real move.
            if expected_epoch is not None:
                for row in self._rows(
                    "MATCH (n:Point {id:$id}) RETURN n.claim_epoch AS epoch", id=cid
                ):
                    # The epoch is read straight off the node, so its TYPE is the store's to
                    # decide: a list-valued or textual `claim_epoch` from a foreign writer
                    # made `int(seen)` raise, which escaped `main()` as exit 1 — the rc this
                    # contract defines as EXIT_HELD ("held by a live claim"), the same
                    # recurring defect class the `query_repo` guard closes (review of #9487
                    # P2). An unreadable epoch on the fence path is a refusal, not a race.
                    try:
                        seen = row[0]
                        seen_epoch = None if seen is None else int(seen)
                    except (IndexError, TypeError, ValueError) as exc:
                        raise StoreUnanswerable(
                            f"the claim store returned an unreadable claim_epoch for id {cid} "
                            f"({type(exc).__name__}) — refusing to guess which claim moved"
                        ) from exc
                    if seen_epoch is None or seen_epoch == int(expected_epoch):
                        raise StoreUnanswerable(
                            f"the claim store holds id {cid} at an epoch this fence cannot "
                            f"match (expected {int(expected_epoch)}, found "
                            f"{'absent' if seen_epoch is None else seen_epoch}) — refusing to "
                            "report a moved claim that did not move"
                        )
            return None
        if moved != 1:
            raise StoreUnanswerable(
                f"the claim store holds {moved} nodes for id {cid} — refusing to pick one"
            )
        return record.content()

    def create(self, cid: str, record: ClaimRecord) -> str:
        """`MERGE` + a per-call token: only one contender's `ON CREATE` runs, and the token
        lets this call verify it is the one that wrote the content it returns."""
        import uuid

        props = {**record.props(), "pointKind": CLAIM_KIND}
        rows = self._rows(
            "MERGE (n:Point {id:$id}) "
            "ON CREATE SET n += $props, n.content = $content, n.__cas = $token "
            "RETURN count(n) AS c, collect(n.content) AS contents, "
            "collect(n.__cas = $token) AS wins",
            id=cid,
            props=props,
            content=record.content(),
            token=uuid.uuid4().hex,
        )
        if not rows:
            raise StoreUnanswerable("the claim store returned no row for the claim write")
        # EVERY returned value is an aggregate on purpose: a bare `n.content` alongside
        # `count(n)` groups BY that column, so a legacy graph holding two nodes for one id
        # returned one row per duplicate with `c = 1` and the duplicate guard never fired
        # (measured in review of #9487 P3).
        found = int(rows[0][0] or 0)
        contents = rows[0][1] or []
        wins = rows[0][2] or []
        if found != 1:
            raise StoreUnanswerable(
                f"the claim store holds {found} nodes for id {cid} — refusing to pick one"
            )
        content = (contents[0] if contents else "") or ""
        # `wins` is the statement's own report of whether OUR `ON CREATE` ran. It is a
        # consistency check, not the verdict: a contender that lands a byte-identical payload
        # is the SAME identity rather than a second holder, so content equality decides (and
        # `acquire` reads before it creates, so a retry never reaches this branch). A statement
        # that claims it created while returning someone else's content means the graph did
        # something this store does not model — refuse instead of reporting it as a win.
        if bool(wins and wins[0] is True) and content != record.content():
            raise StoreUnanswerable(
                "the claim store reported its own create but returned different content — "
                "refusing to treat that as an acquisition"
            )
        return content

    def replace(self, cid: str, record: ClaimRecord) -> str:
        """Release: conditional on the epoch the caller OBSERVED, so a release cannot
        overwrite a peer's newer reclaim (the residual TOCTOU window of a blind write)."""
        return self._transition(cid, record, expected_epoch=int(record.epoch)) or ""

    def replace_if_epoch(self, cid: str, record: ClaimRecord, *, expected_epoch: int) -> str | None:
        return self._transition(cid, record, expected_epoch=int(expected_epoch))

    def target(self) -> str:
        """Which store answered — printed by `check` so a human sees it rather than trusting.

        The password is REDACTED: the documented URI form carries one
        (`docker://:<password>@host:port/graph`), and `check` runs in routine diagnostics whose
        output lands in controller logs.
        """
        uri = getattr(self._sdk, "_db_uri", None)
        if not uri:
            return "embedded (box-local — NOT a shared claim store)"
        return _redact_uri(str(uri))

    def require_shared(self) -> str | None:
        """Is this a store a PEER can read? A box-local target is not, and the guard above
        cannot tell: it validates that a URI is SET, not that the graph is shared."""
        if self._allow_box_local:
            return None
        host = _uri_host(str(getattr(self._sdk, "_db_uri", "") or ""))
        if host in ("", "localhost", "::1", "0.0.0.0") or host.startswith("127."):
            return (
                f"the claim store target is BOX-LOCAL ({host or 'no host'}) — a claim written "
                "there reads as acquired here and free on every peer, which is the duplicate-"
                "dispatch failure this tool exists to prevent. Point TORTOISE_DB_URI at the "
                "shared graph, or pass --allow-box-local to acknowledge a single-box trial."
            )
        return None

    def query_repo(self, repo: str) -> list[dict]:
        """Every claim record for one repo, ONE ROW PER UNIT OF WORK (highest epoch wins).

        Grouping by id is not cosmetic: a graph written by the pre-MERGE version holds
        duplicate nodes for one id, so a raw property query reports the same unit twice with
        two owners — the ledger would contradict itself about who holds it.

        The match is on the CANONICAL spelling, in the statement and in the re-filter below
        (review of #9487 P2): an exact `n.repo = $repo` comparison turned a differently-spelled
        `--repo` into `0 claim record(s)` at rc 0 — an empty answer indistinguishable from
        "nobody holds anything". `toLower` also reads rows written before the spelling was
        canonicalised, and the Python-side re-filter uses the same form so the two agree.
        """
        repo = normalise_repo(repo)
        rows = self._rows(
            "MATCH (n:Point) WHERE n.pointKind = $kind AND toLower(n.repo) = $repo "
            "RETURN n.id AS id, n.content AS content, n.claim_epoch AS epoch",
            kind=CLAIM_KIND,
            repo=repo,
        )
        best: dict[str, tuple[str, int]] = {}
        for row in rows:
            # A row of unexpected arity, or a `claim_epoch` that is not a number, is a store
            # we refuse to interpret — NOT an empty listing and NOT a crash (review of #9487
            # P2). This walk used to sit outside every handler, so a ValueError escaped
            # `main()` as an uncaught traceback, which exits 1: the rc this contract defines
            # as EXIT_HELD. A `status --repo` crash then read as "busy" and an `acquire`
            # crash as "held by a live claim" — an undocumented rc no caller can tell from a
            # real answer.
            try:
                cid, content, epoch = row
                if content is not None and not isinstance(content, str):
                    raise ValueError(f"content is {type(content).__name__}, not a string")
                epoch_i = int(epoch or 0)
            except (TypeError, ValueError) as exc:
                raise StoreUnanswerable(
                    f"the claim store returned a listing row this tool cannot read "
                    f"({type(exc).__name__}: {_redact_uri(str(exc))[:200]})"
                ) from exc
            current = best.get(str(cid))
            if current is None or epoch_i > current[1]:
                best[str(cid)] = (content or "", epoch_i)
        out: list[dict] = []
        for content, _epoch in best.values():
            rec = _parse(content)
            if (rec.repo or "").strip().lower() != repo:
                continue
            out.append(
                {
                    "key": rec.key,
                    "owner": rec.owner,
                    "lease_until": rec.lease_until,
                    "epoch": rec.epoch,
                    "session": rec.session,
                    # Carried through so `status --repo --json` answers "who holds this, and on
                    # how loaded a box" from the SHIPPED reader instead of hand-written Cypher
                    # (review of #9487). `None` = never measured, which is not `0`.
                    "host": rec.host,
                    "ncpu": rec.ncpu,
                    "lpc": rec.lpc,
                    "lanes": rec.lanes,
                }
            )
        return out


@dataclass
class Outcome:
    """What the caller is told. `status` is the vocabulary `claims.sh` already uses."""

    rc: int
    status: str
    reason: str
    owner: str = ""
    lease_until: int = 0
    epoch: int = 0
    claim_id: str = ""
    record: ClaimRecord | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "rc": self.rc,
            "reason": self.reason,
            "owner": self.owner,
            "lease_until": self.lease_until,
            "epoch": self.epoch,
            "claim_id": self.claim_id,
            "record": self.record.content() if self.record else "",
            **({"extra": self.extra} if self.extra else {}),
        }


def _now() -> int:
    return int(time.time())


def acquire(
    store: ClaimStore,
    record: ClaimRecord,
    *,
    now: int | None = None,
    hostname: str | None = None,
) -> Outcome:
    """Take the claim, or refuse. Never blocks, never retries blind.

    Four shapes: created (0), **`already-held`** (0 — the SAME caller already holds it, so a
    resumed lane is not told a peer has its unit), held by a LIVE claim (1), unanswerable (2).
    The read-back is the authority in all four — `create`'s return value is compared to what we
    meant to write, so a store that silently hands back someone else's point is a lost race
    rather than an acquisition.

    ⛔ `already-held` is rc 0 and is NOT `acquired`: a controller that branches on the status
    vocabulary instead of on rc must accept BOTH, or it refuses its own retry (review of #9487).
    """
    now = _now() if now is None else now
    # Canonicalise the repo at THIS boundary too, not only at the CLI's (review of #9487 P2):
    # the stored `repo` property and the id digest are the two halves of the identity, and a
    # library caller that spelled the repo differently would write a row its peers' `status
    # --repo` cannot match. A bad shape raises here (the caller's contract), it never writes.
    # The KEY half is canonicalised at this boundary too (review of PR #9537): it was digested
    # as typed, so `issue-5052`, `ISSUE-5052`, `issue-5052 ` and `""` were four ids — and four
    # `acquired` outcomes — for one unit of work, reachable from any caller that bypasses
    # `main()`. Only the CLI was canonicalised, and the CLI is not the only door.
    record.repo = normalise_repo(record.repo)
    record.key = canonical_key(record.key)
    cid = claim_id(record.repo, record.key)
    record.claimed_at = record.claimed_at or now
    record.lease_until = record.lease_until or (now + DEFAULT_TTL)
    record.host = record.host or hostname or _host_name()

    # Is this a store a PEER can read? (review of #9487 P2.) The env guard above can only prove
    # that a URI is set: the README's own inventory records that every cell writes to its own
    # BOX-LOCAL graph, and a loopback docker:// target passes every check while a claim there is
    # visible to this box alone. That is the same failure the embedded fallback had, one
    # variable away, so it is a refusal unless the caller acknowledges a single-box trial.
    shared = getattr(store, "require_shared", None)
    if shared is not None:
        refusal = shared()
        if refusal:
            return Outcome(
                rc=EXIT_UNANSWERABLE,
                status="REFUSED",
                reason=f"dispatch refused: {refusal}",
                claim_id=cid,
                record=record,
            )

    mine = record.content()

    # Read BEFORE the create (review of #9487): `create_point` hands back the EXISTING content
    # for an id that already exists, so a retry whose payload happened to be byte-identical to
    # the stored claim compared equal and was reported `acquired` by a box that wrote nothing.
    # The read also makes re-entrancy decidable without inferring identity from content.
    try:
        existing = store.read(cid) or ""
    except StoreUnanswerable as exc:
        return Outcome(
            rc=EXIT_UNANSWERABLE,
            status="REFUSED",
            reason=f"the claim store could not be read — dispatch refused: {exc}",
            claim_id=cid,
            record=record,
        )

    if existing:
        got = None  # nothing was created; the tail decides held / reclaim
    else:
        try:
            got = store.create(cid, record)
        except StoreUnanswerable as exc:
            return Outcome(
                rc=EXIT_UNANSWERABLE,
                status="REFUSED",
                reason=f"claim store unanswerable — dispatch refused: {exc}",
                claim_id=cid,
                record=record,
            )

        if got == mine:
            # Confirm by an independent read: a create that echoed our own payload without
            # persisting it would otherwise read as a win (the class of defect where the
            # instrument reports success and the state never landed).
            try:
                back = store.read(cid)
            except StoreUnanswerable as exc:
                return Outcome(
                    rc=EXIT_UNANSWERABLE,
                    status="REFUSED",
                    reason=f"claim written but the read-back failed — dispatch refused: {exc}",
                    claim_id=cid,
                    record=record,
                )
            if back == mine:
                return Outcome(
                    rc=EXIT_OK,
                    status="acquired",
                    reason=f"acquired {record.key} until {record.lease_until}",
                    owner=record.owner,
                    lease_until=record.lease_until,
                    epoch=record.epoch,
                    claim_id=cid,
                    record=record,
                )
            return Outcome(
                rc=EXIT_HELD,
                status="held",
                reason=(
                    "lost the race: the store returned our payload but the read-back differs "
                    "(another box wrote first)"
                ),
                owner=_field(back or "", "owner"),
                lease_until=_int_field(back or "", "lease_until"),
                epoch=_int_field(back or "", "epoch"),
                claim_id=cid,
            )

        # Somebody else's record came back, or nothing at all.
        existing = got or ""

    holder = _field(existing, "owner")
    until = _int_field(existing, "lease_until")
    epoch = _int_field(existing, "epoch")

    if not existing:
        return Outcome(
            rc=EXIT_UNANSWERABLE,
            status="REFUSED",
            reason=(
                "the store neither created nor returned a claim — dispatch refused rather "
                "than proceeding without a decision"
            ),
            claim_id=cid,
        )

    if until > now:
        # Re-entrancy (review of #9487 P2): a retry by the SAME caller must not be told the
        # unit is held by a peer. `claims.sh` never expressed "already mine", so a resumed
        # lane used to refuse work it owned — and for a CLI caller the reported holder was a
        # different `<host>:<pid>`, so even a human could not recognise their own claim.
        if holder and holder == record.owner:
            return Outcome(
                rc=EXIT_OK,
                status="already-held",
                reason=(
                    f"{record.key} is already held by this caller ({holder}) until {until} "
                    f"({until - now}s left, epoch {epoch}) — no new claim taken"
                ),
                owner=holder,
                lease_until=until,
                epoch=epoch,
                claim_id=cid,
            )
        return Outcome(
            rc=EXIT_HELD,
            status="held",
            reason=(
                f"{record.key} is held by {holder or 'an unnamed owner'} until {until} "
                f"({until - now}s left, epoch {epoch})"
            ),
            owner=holder,
            lease_until=until,
            epoch=epoch,
            claim_id=cid,
        )

    # Expired: reclaim with a fencing bump — but ONLY through a store that can express the
    # precondition (review of #9487 P1). A blind `replace` here let two boxes observe the same
    # expired epoch, both write, and both return `acquired`: one holder in the graph, two lanes
    # believing they own the unit. Absence of the capability is a REFUSAL, not a fallback.
    reclaim = ClaimRecord(**{**record.__dict__})
    reclaim.epoch = epoch + 1
    reclaim.claimed_at = now
    # The requested window, not a constant: `--ttl` is the caller's statement of how long the
    # unit is theirs, and a reclaim that silently swapped in DEFAULT_TTL would give the next
    # holder a different lease than it asked for (review of #9487 P2).
    ttl = record.lease_until - now if record.lease_until > now else DEFAULT_TTL
    reclaim.lease_until = now + ttl
    mine = reclaim.content()
    fenced = getattr(store, "replace_if_epoch", None)
    if fenced is None:
        return Outcome(
            rc=EXIT_UNANSWERABLE,
            status="REFUSED",
            reason=(
                f"{record.key} expired at {until} (holder {holder or 'unknown'}), but this "
                "store cannot express the reclaim precondition (an epoch-CAS), so a reclaim "
                "could double-grant. Dispatch refused; the claim must be released explicitly "
                "(`release --force`) or reclaimed by a store that supports the fence."
            ),
            owner=holder,
            lease_until=until,
            epoch=epoch,
            claim_id=cid,
        )
    try:
        got = fenced(cid, reclaim, expected_epoch=epoch)
        if got is None:
            # A peer moved the epoch between our read and our write: we lost, and we say so
            # rather than retrying into a claim we do not hold.
            winner = store.read(cid) or ""
            return Outcome(
                rc=EXIT_HELD,
                status="held",
                reason=(
                    "lost the reclaim race: the record moved to "
                    f"{_field(winner, 'owner') or 'another box'} at epoch "
                    f"{_int_field(winner, 'epoch')}"
                ),
                owner=_field(winner, "owner"),
                lease_until=_int_field(winner, "lease_until"),
                epoch=_int_field(winner, "epoch"),
                claim_id=cid,
            )
        back = store.read(cid)
    except StoreUnanswerable as exc:
        return Outcome(
            rc=EXIT_UNANSWERABLE,
            status="REFUSED",
            reason=f"reclaim of an expired claim could not be confirmed — dispatch refused: {exc}",
            claim_id=cid,
            record=reclaim,
        )
    if got == mine and back == mine:
        return Outcome(
            rc=EXIT_OK,
            status="acquired",
            reason=(
                f"reclaimed {record.key} (previous holder {holder or 'unknown'} expired at "
                f"{until}); epoch {epoch} -> {reclaim.epoch}"
            ),
            owner=reclaim.owner,
            lease_until=reclaim.lease_until,
            epoch=reclaim.epoch,
            claim_id=cid,
            record=reclaim,
        )
    winner = back or got or ""
    return Outcome(
        rc=EXIT_HELD,
        status="held",
        reason=(
            "lost the reclaim race: the record moved to "
            f"{_field(winner, 'owner') or 'another box'} at epoch {_int_field(winner, 'epoch')}"
        ),
        owner=_field(winner, "owner"),
        lease_until=_int_field(winner, "lease_until"),
        epoch=_int_field(winner, "epoch"),
        claim_id=cid,
    )


def release(
    store: ClaimStore,
    repo: str,
    key: str,
    *,
    owner: str,
    now: int | None = None,
    session: str = "",
    force: bool = False,
    reason: str = "",
) -> Outcome:
    """Release a claim this owner holds. A release by a non-holder is REFUSED (rc 1), never
    a silent no-op: releasing someone else's claim is how a peer's lane gets duplicated.

    `force=True` is the audited operator route for a crashed holder: the lease is expired and
    `reason` is recorded IN the claim, so the override is legible to the next reader. It never
    bypasses the identity check silently — the tombstone carries the reason and the releasing
    identity, and the refusal message (absent force) names the real holder.
    """
    now = _now() if now is None else now
    repo = normalise_repo(repo)
    # Canonicalised here, not only in `main()` (review of PR #9537): `release` is a public
    # entry point, and a differently-spelled key addressed a DIFFERENT claim — the release
    # then answered "was not claimed — nothing to release" at rc 0 while the claim lived on.
    key = canonical_key(key)
    cid = claim_id(repo, key)
    shared = getattr(store, "require_shared", None)
    if shared is not None:
        refusal = shared()
        if refusal:
            return Outcome(
                rc=EXIT_UNANSWERABLE,
                status="REFUSED",
                reason=f"release refused: {refusal}",
                claim_id=cid,
            )
    try:
        existing = store.read(cid) or ""
    except StoreUnanswerable as exc:
        return Outcome(
            rc=EXIT_UNANSWERABLE,
            status="REFUSED",
            reason=f"claim store unanswerable — release not attempted: {exc}",
            claim_id=cid,
        )
    if not existing:
        return Outcome(
            rc=EXIT_OK,
            status="released",
            reason=f"{key} was not claimed — nothing to release",
            claim_id=cid,
        )
    holder = _field(existing, "owner")
    if holder != owner and not force:
        return Outcome(
            rc=EXIT_HELD,
            status="refused",
            reason=f"{key} is held by {holder or 'an unnamed owner'}, not by {owner} — refused",
            owner=holder,
            lease_until=_int_field(existing, "lease_until"),
            epoch=_int_field(existing, "epoch"),
            claim_id=cid,
        )
    # Release = expire it in place, not delete it. A deleted claim has no history and no
    # fencing epoch, which is exactly the defect that makes `refs/claims/*` unable to answer
    # "who held this yesterday" (the refs are deleted on release).
    tomb = _parse(existing)
    tomb.lease_until = now
    if force:
        actor = f" (forced by {owner}" + (f" from session {session}" if session else "") + ")"
        tomb.purpose = (tomb.purpose + " [released:" + _audit_text(reason) + "]" + actor).strip()
    else:
        tomb.purpose = (tomb.purpose + " [released]").strip()
    # The write is conditional on the epoch we OBSERVED, so it cannot erase a peer's newer
    # reclaim — the store enforces the fence, and the read-back below reports the loss. A
    # release that landed against a moved record returns its content and is refused here.
    try:
        store.replace(cid, tomb)
        back = store.read(cid)
    except StoreUnanswerable as exc:
        return Outcome(
            rc=EXIT_UNANSWERABLE,
            status="REFUSED",
            reason=f"release could not be confirmed — dispatch state unknown: {exc}",
            claim_id=cid,
        )
    if back != tomb.content():
        return Outcome(
            rc=EXIT_HELD,
            status="refused",
            reason="release lost a race: the claim moved while being released",
            owner=_field(back or "", "owner"),
            claim_id=cid,
        )
    return Outcome(
        rc=EXIT_OK,
        status="released",
        reason=f"released {key} (held by {owner})",
        owner=owner,
        lease_until=tomb.lease_until,
        epoch=tomb.epoch,
        claim_id=cid,
        record=tomb,
    )


def status(store: ClaimStore, repo: str, key: str | None, *, now: int | None = None) -> Outcome:
    """Who holds this right now. A reachable store with no claim is a real answer (rc 0 with
    `status=free`), and is never confused with an unanswerable one (rc 2).

    A box-local store is refused here too: `free` from a graph no peer can read is the same
    defect as an empty read from the wrong graph, and `status` is what a controller dispatches
    on (review of #9487 P3)."""
    now = _now() if now is None else now
    repo = normalise_repo(repo)
    shared = getattr(store, "require_shared", None)
    if shared is not None:
        refusal = shared()
        if refusal:
            return Outcome(rc=EXIT_UNANSWERABLE, status="REFUSED", reason=f"refused: {refusal}")
    if key:
        # Canonicalised here too (review of PR #9537): a spelling variant named a different id
        # and this verb answered `free` — the answer a controller dispatches on.
        key = canonical_key(key)
        cid = claim_id(repo, key)
        try:
            existing = store.read(cid) or ""
        except StoreUnanswerable as exc:
            return Outcome(
                rc=EXIT_UNANSWERABLE,
                status="REFUSED",
                reason=f"claim store unanswerable — state unknown: {exc}",
                claim_id=cid,
            )
        if not existing:
            return Outcome(rc=EXIT_OK, status="free", reason=f"{key} is unclaimed", claim_id=cid)
        until = _int_field(existing, "lease_until")
        live = until > now
        return Outcome(
            rc=EXIT_OK,
            status="held" if live else "expired",
            reason=(
                f"{key} is held by {_field(existing, 'owner')} until {until} "
                f"({until - now}s {'left' if live else 'ago'})"
            ),
            owner=_field(existing, "owner"),
            lease_until=until,
            epoch=_int_field(existing, "epoch"),
            claim_id=cid,
            record=_parse(existing),
        )
    # Repo-wide listing (review of #9487 P1): a store that cannot list must REFUSE. The old
    # `hasattr(store, "query_repo")` fallback turned "this store cannot list" into "there is
    # nothing to list" and answered rc 0 `0 claim record(s)` while the store held claims —
    # the exact default-answer-without-a-read that this tool exists to remove.
    lister = getattr(store, "query_repo", None)
    if lister is None:
        return Outcome(
            rc=EXIT_UNANSWERABLE,
            status="REFUSED",
            reason=(
                "this store cannot list claims for a repo — refusing rather than reporting an "
                "empty ledger (an empty answer from an unread store looks exactly like 'nobody "
                "holds anything')"
            ),
        )
    try:
        rows = lister(repo)
    except StoreUnanswerable as exc:
        return Outcome(rc=EXIT_UNANSWERABLE, status="REFUSED", reason=f"claim store unanswerable: {exc}")
    return Outcome(
        rc=EXIT_OK,
        status="listed",
        reason=f"{len(rows)} claim record(s) for {repo}",
        extra={"claims": rows},
    )


def _field(content: str, name: str) -> str:
    """Read a field out of the canonical content line, tolerating a quoted value."""
    marker = f"{name}="
    for token in _tokens(content):
        if token.startswith(marker):
            return token[len(marker) :]
    return ""


def _int_field(content: str, name: str) -> int:
    raw = _field(content, name)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _opt_int_field(content: str, name: str) -> int | None:
    """A capacity field, with `absent`/missing preserved as `None` — never coerced to `0`
    (review of #9487: `0` is a measurement of an idle box, not the absence of one)."""
    raw = _field(content, name)
    if raw in ("", CAP_ABSENT):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _tokens(content: str) -> list[str]:
    """Split the canonical line into `k=v` tokens, keeping quoted values whole."""
    out: list[str] = []
    buf = ""
    in_quotes = False
    escape = False
    for ch in content:
        if escape:
            buf += ch
            escape = False
        elif ch == "\\" and in_quotes:
            escape = True
        elif ch == '"':
            in_quotes = not in_quotes
        elif ch == " " and not in_quotes:
            if buf:
                out.append(buf)
            buf = ""
        else:
            buf += ch
    if buf:
        out.append(buf)
    return out


def _parse(content: str) -> ClaimRecord:
    """Rebuild a record from content. Unknown/absent fields become empty, never a guess."""
    return ClaimRecord(
        repo=_field(content, "repo"),
        key=_field(content, "key"),
        owner=_field(content, "owner"),
        session=_field(content, "session"),
        branch=_field(content, "branch"),
        head=_field(content, "head"),
        lease_until=_int_field(content, "lease_until"),
        epoch=_int_field(content, "epoch") or 1,
        purpose=_field(content, "purpose"),
        claimed_at=_int_field(content, "claimed_at"),
        host=_field(content, "host"),
        ncpu=_opt_int_field(content, "ncpu"),
        lpc=_opt_int_field(content, "lpc"),
        lanes=_opt_int_field(content, "lanes"),
    )


def _host_name() -> str:
    """This box's name — one resolver, so the owner token and the stored `host` field can
    never disagree (review of #9487 P3: the token used `socket.gethostname()` and the record
    kept the HOSTNAME-only fallback, so under cron/systemd the stored host was empty)."""
    return os.environ.get("HOSTNAME") or socket.gethostname() or "box"


def _owner_token(session: str = "", pid: int | None = None) -> str:
    """The caller's identity, STABLE across processes.

    `claims.sh` writes `box:<pid>`; this tool keeps that shape only when no session is named.
    A pid is not an identity for a claim (review of #9487 P1): `acquire` and `release` are
    different processes, so a pid-derived owner made every claim unreleasable — the release
    was always refused as "held by <a different pid>". A session id is what the caller already
    has and what survives a retry, so it is preferred whenever it is given.

    The lease (not `kill -0`) is still what expires a claim: a session id is a name, not a
    liveness proof.
    """
    host = _host_name()
    if session:
        return f"{host}:{session}"
    pid = os.getpid() if pid is None else pid
    return f"{host}:{pid}"


def main(argv: list[str] | None = None, store: ClaimStore | None = None) -> int:
    # `--json` must work on BOTH sides of the subcommand: a controller writes
    # `work_claim acquire … --json` (the natural order) and a human often writes
    # `work_claim --json status …`. The subcommand's copy uses SUPPRESS so it cannot
    # overwrite a value already given before the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="machine-readable outcome on stdout")
    common.add_argument(
        "--allow-box-local",
        action="store_true",
        default=False,
        help="acknowledge a single-box trial: a loopback claim store is invisible to peers, so "
        "`acquire` refuses it unless this flag is given",
    )

    parser = argparse.ArgumentParser(
        prog="work_claim",
        description="The claim on a unit of work, in the Tortoise graph. rc 2 = REFUSED.",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable outcome on stdout")
    sub = parser.add_subparsers(dest="cmd", required=True)

    acq = sub.add_parser("acquire", parents=[common], help="claim a unit of work (CAS; refuses if held)")
    acq.add_argument("--repo", required=True, help="owner/name whose issue/PR number is claimed")
    acq.add_argument("--kind", default="issue", choices=("issue", "pr"))
    acq.add_argument("--issue", type=int, default=None, help="number (issue or PR per --kind)")
    acq.add_argument("--pr", type=int, default=None, help="alias for --kind pr --issue N")
    acq.add_argument("--session", default="", help="the lane/session id taking the claim (required)")
    acq.add_argument("--owner", default="", help="explicit owner token; overrides <host>:<session>")
    acq.add_argument("--branch", default="")
    acq.add_argument("--head", default="", help="head sha, for a content-addressed key")
    acq.add_argument("--purpose", default="")
    acq.add_argument("--ttl", type=int, default=DEFAULT_TTL)
    # Capacity fields: MEASURED BY capacity.sh, never by this tool (see the module docstring).
    acq.add_argument("--host", default="")
    # `default=None` = NOT SUPPLIED, stored as `absent` rather than a fabricated 0 (review of
    # #9487): the record is the fleet's capacity map, where 0 claims a measured idle box.
    acq.add_argument("--ncpu", type=int, default=None)
    acq.add_argument("--lpc", type=int, default=None, help="load-per-cpu x100, as capacity.sh emits it")
    acq.add_argument("--lanes", type=int, default=None)

    rel = sub.add_parser("release", parents=[common], help="release a claim this owner holds")
    rel.add_argument("--repo", required=True)
    rel.add_argument("--kind", default="issue", choices=("issue", "pr"))
    rel.add_argument("--issue", type=int, default=None)
    rel.add_argument("--pr", type=int, default=None)
    rel.add_argument("--owner", default="", help="defaults to <host>:<session> when --session is given, else <host>:<pid>")
    rel.add_argument("--session", default="", help="the lane/session id that took the claim")
    rel.add_argument(
        "--force",
        action="store_true",
        help="expire a claim this caller does NOT hold (requires --reason); the audited route "
        "for a crashed holder when the store cannot reclaim an expired claim itself",
    )
    rel.add_argument("--reason", default="", help="why --force is justified (recorded with the claim)")

    st = sub.add_parser("status", parents=[common], help="who holds it right now")
    st.add_argument("--repo", required=True)
    st.add_argument("--kind", default="issue", choices=("issue", "pr"))
    st.add_argument("--issue", type=int, default=None)
    st.add_argument("--pr", type=int, default=None)

    sub.add_parser("check", parents=[common], help="is the store reachable? rc 2 = no")

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # ⛔ `argparse` exits **2** from `error()` — the code this tool reserves for "the
        # store could not answer", i.e. RETRY the dispatch. Every parse failure went through
        # it: a missing `--repo` (required on all three verbs), a bad `--kind` choice, or
        # `--allow-box-local` written BEFORE the subcommand (it is registered on the
        # subcommand's parent). A controller retrying a command that can never succeed is
        # the same defect the `status --repo` / `--ttl 0` fixes closed for the validated
        # path; this closes it on the parse path, so a malformed invocation is ALWAYS
        # EXIT_USAGE (review of #9487 P2).
        code = exc.code if isinstance(exc.code, int) else EXIT_USAGE
        if code == 0:  # `--help`: argparse already printed the help we asked for
            return EXIT_OK
        return EXIT_USAGE
    args.json = getattr(args, "json", False)

    if args.cmd == "check":
        def _refuse(reason: str) -> int:
            reason = _redact_uri(reason)
            if args.json:
                print(json.dumps({"status": "REFUSED", "reachable": False, "reason": reason}, sort_keys=True))
            print(f"REFUSED: {reason}", file=sys.stderr)
            return EXIT_UNANSWERABLE

        try:
            _require_store_configured()
            if store is None:
                store = SDKClaimStore(allow_box_local=getattr(args, "allow_box_local", False))
            # ⛔ Reachable is NOT shared, and this verb is what a dispatch flow GATES on
            # ("`check` … rc 2 = no", the USAGE block and the evidence README §6). A target
            # every sibling refuses — `acquire`/`status`/`release` all rc 2 on a loopback
            # graph — used to print `ok: claim store reachable` and exit 0 here, with the
            # disqualification only as a stderr WARNING: the one-variable-away repeat of the
            # local-write-reported-as-success failure this tool exists to prevent (review of
            # #9487 P2). So the verdict is the siblings' verdict — rc 2 unless
            # `--allow-box-local` acknowledges a single-box trial — and rc 0 cannot coexist
            # with a target the tool will refuse to write.
            shared = getattr(store, "require_shared", None)
            refusal = shared() if shared is not None else None
            if refusal:
                return _refuse(refusal)
            # A definite read, not a write: `check` must be safe to run on any box at any
            # time, and an empty answer still proves the store answered.
            # A CANONICAL key, because `claim_id` now validates its shape (`probe` is not
            # `<issue|pr>-<body>`). This is a READ under a repo no box claims, so it mints
            # nothing; a hit still proves the store answered.
            store.read(claim_id("probe/probe", "issue-1"))
        except StoreUnanswerable as exc:
            return _refuse(str(exc))
        except Exception as exc:
            return _refuse(f"claim store unanswerable ({type(exc).__name__}: {exc})")
        where = getattr(store, "target", None)
        target = where() if where else "unknown target"
        if args.json:
            print(json.dumps({"status": "ok", "reachable": True, "target": target}, sort_keys=True))
        else:
            print(f"ok: claim store reachable ({target})")
        return EXIT_OK

    # `status --repo` with NO number is the repo-wide listing, not a malformed single-key
    # query (review of #9487 P2). Any other command still needs a number, an EXPLICIT 0 is a
    # usage error wherever it appears, and a bad invocation is EXIT_USAGE — never
    # EXIT_UNANSWERABLE, which means "the store could not answer" and is retried.
    try:
        for name in ("issue", "pr"):
            if getattr(args, name, None) == 0:
                raise ValueError(f"--{name} must be a positive number (omit it for a listing)")
        # `--ttl 0` used to mint a claim that was ALREADY expired and report `acquired` rc 0;
        # a peer then reclaimed it at once and ALSO reported `acquired` — the double grant the
        # fence exists to prevent, reachable from one flag (review of #9487 P2).
        if args.cmd == "acquire" and int(getattr(args, "ttl", 0)) <= 0:
            raise ValueError("--ttl must be a positive number of seconds")
        # A negative capacity reading is a caller bug, and the record is the fleet's capacity
        # map too — persisting `ncpu=-8` would make that map assert something impossible
        # (review of #9487 P3).
        if args.cmd == "acquire":
            for flag in ("ncpu", "lpc", "lanes"):
                raw = getattr(args, flag, None)
                if raw is not None and int(raw) < 0:
                    raise ValueError(f"--{flag} must not be negative (it is a measurement)")
        number = int(getattr(args, "pr", None) or 0) or int(getattr(args, "issue", None) or 0)
        kind = "pr" if int(getattr(args, "pr", None) or 0) else args.kind
        key = None if args.cmd == "status" and number == 0 else normalise_key(kind, number)
        # The repo is the OTHER half of the claim identity (review of #9487 P2): validated and
        # canonicalised once, here, BEFORE any key or ClaimRecord is built, so everything
        # downstream (the id digest, the written `repo` property, the listing match) sees one
        # spelling. A bare name or a case variant used to be accepted verbatim, which gave the
        # same unit two ids (both boxes "acquired") and made `status --repo` answer
        # `0 claim record(s)` at rc 0 over a store full of claims — a silent empty answer.
        # A bad spelling is EXIT_USAGE, never a refusal a controller would retry.
        args.repo = normalise_repo(args.repo)
    except (ValueError, TypeError) as exc:
        print(f"usage: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if args.cmd == "acquire" and not (args.session.strip() or args.owner.strip()):
        # A pid-shaped owner cannot be reproduced by a later process, so the claim could never be
        # released (measured: acquire stored box:42368; the separate release process compared
        # box:61443 and refused). Requiring an identity is what makes the release path reachable.
        print(
            "usage: acquire needs --session (or an explicit --owner) — a process-scoped owner "
            "cannot be reproduced by the release that follows it",
            file=sys.stderr,
        )
        return EXIT_USAGE

    # EVERY invocation-shape check runs BEFORE any store work (review of #9487 P2). `release
    # --force` with no `--reason` was validated after the store was opened, so with an
    # unset/unreachable/box-local store it returned rc 2 ("the store could not answer",
    # retryable) instead of rc 3 (malformed invocation, never retry) — a controller would
    # retry a command that can never succeed.
    if args.cmd == "release" and getattr(args, "force", False) and not args.reason.strip():
        print("usage: --force requires --reason (an audited override, not a quiet one)", file=sys.stderr)
        return EXIT_USAGE

    if store is None:
        try:
            _require_store_configured()
            store = SDKClaimStore(allow_box_local=getattr(args, "allow_box_local", False))
        except StoreUnanswerable as exc:
            print(f"REFUSED: {_redact_uri(str(exc))}", file=sys.stderr)
            return EXIT_UNANSWERABLE
        except Exception as exc:
            print(
                f"REFUSED: claim store could not be opened "
                f"({type(exc).__name__}: {_redact_uri(str(exc))})",
                file=sys.stderr,
            )
            return EXIT_UNANSWERABLE

    if args.cmd == "acquire":
        owner = args.owner or _owner_token(args.session)
        record = ClaimRecord(
            repo=args.repo,
            key=key,
            owner=owner,
            session=args.session,
            branch=args.branch,
            head=args.head,
            lease_until=_now() + args.ttl,
            purpose=args.purpose,
            host=args.host,
            ncpu=args.ncpu,
            lpc=args.lpc,
            lanes=args.lanes,
        )
        outcome = acquire(store, record)
    elif args.cmd == "release":
        # Identity must be reproducible across processes (review of #9487 P1): `release` runs
        # in a DIFFERENT process from `acquire`, so a pid-derived default made every claim
        # unreleasable. `--session` names the same lane the acquire named. The `--force` /
        # `--reason` shape is validated above, before the store is opened.
        owner = args.owner or _owner_token(args.session)
        outcome = release(
            store,
            args.repo,
            key,
            owner=owner,
            session=args.session,
            force=getattr(args, "force", False),
            reason=args.reason.strip(),
        )
    else:
        outcome = status(store, args.repo, key) if key is not None else status(store, args.repo, None)

    if args.json:
        # Redaction at the OUTPUT boundary as well as in `StoreUnanswerable` (review of #9487
        # P2): `reason` can carry a driver message that quotes the DSN, and this is the stream
        # a controller captures. A future exception path cannot reintroduce the leak without
        # passing through here.
        print(_redact_uri(json.dumps(outcome.as_dict(), sort_keys=True)))
    else:
        print(_redact_uri(f"{outcome.status}: {outcome.reason}"))
    if outcome.rc == EXIT_UNANSWERABLE:
        # The reason is already on stdout/stderr; rc is the contract the caller branches on.
        print(
            "REFUSED: no claim decision was reached, so the dispatch must not proceed blind",
            file=sys.stderr,
        )
    return outcome.rc


if __name__ == "__main__":
    sys.exit(main())
