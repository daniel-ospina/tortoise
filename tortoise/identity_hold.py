"""#10997 — S3's ``HOLD``: an unresolved identity is HELD, never minted.

WHY THIS MODULE EXISTS
======================
``docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md`` §4.4 states invariant **I1**:

    "An unresolved identity is NEVER asserted ``new``."

and its failure policy for S3 RESOLVE is *"a candidate whose identity is
unresolved is ``HOLD``-ed"* — **not** minted (I1) and **not** dropped (I2: *"a
failure never silently drops memory; it holds the item or records the loss"*).
§4.4 goes on to define ``HOLD`` as a **run-level disposition**: *"the set of
candidates a run did not commit and did not discard — carried forward for a
later run against the same graph"*.

``#5095`` landed the RECORD half of the mechanism (the lookup's state is read off
the queries that actually ran, the renderer stopped inviting ``new``, every
``link_before_create`` record states ``found: null`` for a candidate that was
never checked). This module is the **MINT** half for the one seam ``#10997``
owns — ``extractor_v2._mint_endpoint``, the pre-pass that fabricates a
``statement`` Point out of a model ref — and the store that makes the disposition
honest: **idempotent hold**, **exactly-once drain**.

WHAT IS HELD, AND WHERE
=======================
The held item is the **edge together with its endpoint**, not the endpoint alone
(``#2552``): dropping the endpoint to hold it would trade a duplicate for a lost
operator edge, which is precisely the loss I2 forbids. So a held record carries
the operator that referenced the unresolved ref, and the drain re-emits both.

⛔ **Every** operator, not the first one (#10997 review round 4, P1). The hold
key is content-addressed on the *ref* (``hold_key``), so two operators naming one
unresolved ref land on ONE record; storing a single ``operator`` kept the first
edge and silently discarded the second, while the caller reported BOTH as
carried. The record therefore carries ``operators`` — a list, deduped on
``(src, dst, op_type[, target])`` — and the drain replays every distinct one.
A legacy record carrying the old singular ``operator`` is still read (see
``operators_of``), so a store written by an earlier run does not lose its edge.

**The home is a durable local store, and it is not the graph** — decided here and
recorded, because the two obvious alternatives are both wrong:

* **Not the graph.** A held candidate is *not a belief*. Writing it as a node
  would make it recallable — and, worse, it would make the NEXT run's S3 lookup
  resolve the ambiguity **against the held copy of itself**, turning the store
  that exists to prevent duplicates into their manufacturer. That self-poisoning
  loop is the strongest of the reasons the graph is the wrong home.
* **Not the journal.** ``derived = replay(journal)`` is the rebuild contract, so a
  journal event exists to be folded into graph state. A held item is deliberately
  NOT graph state; it would need a fold that does nothing, which is a rebuild
  source carrying records that must never become nodes.
* **Postgres is where D1 puts the narrative and the raw span**, because the graph
  must not hold text. A held candidate is claim text of exactly that class — but
  the extraction seam is SDK-side and must answer synchronously on the next run,
  so the durable home is the SDK's own local state directory, the same convention
  ``tortoise/capture_spool.py`` already uses for pre-commit state (``#3963``:
  cheap append frequently, costly action deferred, content-addressed idempotency
  key).

The store is **opt-in by the caller** (``open_store``), because a library that
picks a directory for you is a library that writes to the user's disk on import.
A run with no store still HOLDS — the invariant is enforced on the payload — it
simply cannot persist, and it says so loudly instead of pretending the item was
carried forward.

THE TWO OPERATIONS THAT MAKE IT NOT A LEAK
==========================================
A held identity with no way to become resolved is a leak, so the store names the
path out: **the next run against the same graph, when its identity lookup is
``resolved``.** The drain is exactly-once in both directions:

* ``hold`` on a key that is already present **never reopens** a RESOLVED one — a
  resolved identity is not held again by a later ambiguous run (it is already
  committed, and re-arming it would mint a second copy on the next drain);
* ``claim`` is the only way to mint: a key in the ``claimed`` or ``resolved``
  state cannot be claimed again, so two drains — sequential or interleaved — mint
  ONE item. ``confirm`` then binds the key to the id it minted, and a second
  confirm for a *different* id raises rather than silently forking one identity
  into two.

The record is an APPEND-ONLY log (one JSON object per line, last line per key
wins), so a crash mid-write loses at most the line being written and never
rewrites history; ``hold``/``claim``/``confirm``/``release`` are the four
transitions and nothing else mutates the file.

A CLAIMED RECORD IS RECLAIMABLE, NOT MERELY RECOVERABLE
======================================================
``claimed`` is a state, not a lock, and the promise that makes is only kept if
something actually acts on it: an in-process failure returns the claim through
``release``, and a claim left behind by a process that DIED (kill, OOM, a
container evicted mid-drain) is returned by :meth:`HoldStore.reclaim_stale` —
an idle scan over claims older than :data:`HOLD_CLAIM_IDLE_SECONDS`, the same
shape ``XAUTOCLAIM`` and the SQS visibility timeout converged on. Without that
scan the state would be honest and useless: ``pending()`` is the drain's entire
input, so a claim nothing releases is an item no later drain can see — the I2
loss this module exists to prevent, one state further along.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from threading import RLock
from typing import Any

__all__ = [
    "HOLD_CLAIMED",
    "HOLD_CLAIM_IDLE_SECONDS",
    "HOLD_ENDPOINT",
    "HOLD_PENDING",
    "HOLD_RESOLVED",
    "HOLD_STATES",
    "KINDS",
    "HoldStore",
    "hold_key",
    "open_store",
]

#: The candidate class a held record names. ``operator_endpoint`` is the one
#: ``#10997`` gates (a ``statement`` Point fabricated from a model ref); the
#: constant is a vocabulary, not a promise that every member exists today.
HOLD_ENDPOINT = "operator_endpoint"
KINDS: tuple[str, ...] = (HOLD_ENDPOINT,)

#: ``pending``   held, never minted. The population a drain may act on.
#: ``claimed``   a drain has taken it and is minting. Not actionable by a second
#:               drain — this is the exactly-once guard, and it is a STATE rather
#:               than a lock so a claim is recoverable instead of stuck forever.
#:               Two paths do the recovering, and BOTH exist (#10997 review round
#:               3): ``release`` when the drain itself knows the mint produced
#:               nothing, and :meth:`HoldStore.reclaim_stale` when the drain is
#:               GONE — a kill/OOM between ``claim`` and the settle leaves a
#:               record no in-process code will ever release, and a state that
#:               only ``pending`` is drained from would hide it permanently.
#: ``resolved``  minted, and bound to the id it minted. Terminal.
HOLD_PENDING = "pending"
HOLD_CLAIMED = "claimed"
HOLD_RESOLVED = "resolved"
HOLD_STATES: tuple[str, ...] = (HOLD_PENDING, HOLD_CLAIMED, HOLD_RESOLVED)
#: How long a CLAIMED record may stay claimed before a later drain may reclaim it.
#: The standard shape for a claim-then-acknowledge queue (SQS visibility timeout,
#: Redis Streams ``XAUTOCLAIM``): without an idle bound, a consumer that dies
#: between ``claim`` and ``confirm`` removes the item from the drainable
#: population FOREVER — the I2 loss this module exists to prevent, one state
#: further along. 15 minutes is deliberately far above any drain's own runtime (a
#: mint plus a settle), so a live drain is not raced; the store is per-PROCESS
#: anyway (see ``HoldStore``), so the bound only has to cover a restart.
HOLD_CLAIM_IDLE_SECONDS = 900.0
#: The bucket `summary()` uses for a record whose `state`/`kind` is missing — a
#: torn or hand-edited line is COUNTED, never raised over (a census must not be
#: able to sink the run that reports it).
HOLD_UNKNOWN = "unknown"

#: Collapse whitespace only — NOT case, and NOT punctuation. Two refs that differ
#: in case are not provably the same identity, and a store that merged them would
#: drop one of them; the conservative direction is a second held record, which is
#: visible, over a silent merge, which is not.
_WS = re.compile(r"\s+")
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def hold_key(kind: str, content: str) -> str:
    """The content-addressed identity of a held candidate.

    Stable across replays, so a re-emitted candidate lands on the SAME key and
    cannot become a second item — which is what makes the drain idempotent
    without a clock, a counter or a server round trip.
    """
    if not isinstance(kind, str) or kind not in KINDS:
        raise ValueError(f"unknown hold kind {kind!r}; expected one of {KINDS}")
    text = _WS.sub(" ", str(content or "").strip())
    if not text:
        raise ValueError("a held candidate needs content to identify it")
    return hashlib.sha256(f"{kind}\0{text}".encode()).hexdigest()


def operator_identity(op: object) -> tuple[str, str, str, str]:
    """The identity of ONE operator, for deduping the edges a hold carries.

    ``(src, dst, op_type)`` is the edge; a ``MITIGATES`` also names its
    ``target``, so that is folded in as a fourth element — two MITIGATES that
    dampen different edges are not the same operator and must both survive.

    Whitespace-normalised on the endpoint names exactly as ``hold_key`` is, so
    two spellings of one ref are ONE operator; case and punctuation are left
    alone for the same reason they are left alone in ``hold_key`` — a
    conservative second copy is visible, a silent merge is not.
    """
    if not isinstance(op, dict):
        return ("", "", "", "")
    target = op.get("target") or op.get("target_edge") or {}
    if not isinstance(target, dict):
        target = {}
    return (
        _WS.sub(" ", str(op.get("src", "") or "").strip()),
        _WS.sub(" ", str(op.get("dst", "") or "").strip()),
        str(op.get("op_type", "") or "").upper(),
        f"{_WS.sub(' ', str(target.get('src', '') or '').strip())}"
        f"\0{_WS.sub(' ', str(target.get('dst', '') or '').strip())}"
        f"\0{str(target.get('op_type', '') or '').upper()}",
    )


def drain_identity(op: object) -> tuple[str, str, str, str]:
    """The identity the DRAIN dedupes a re-emitted edge on — pass 2's own.

    ⛔ It is NOT `operator_identity` (#10997 review round 5, P2). That one is the
    STORE's identity: whitespace-collapsed, case preserved, and carrying the
    target's DECLARED `op_type`. The drain compares a restored edge against the
    operator the run re-emitted, so it has to fold exactly what pass 2 folds or
    it fails to recognise its own twin and emits it twice:

    * endpoints are case-folded (the operator pass resolves through
      `extractor_v2._norm`), so a re-emission spelled in a different case is the
      SAME edge — `operator_identity` keeps them apart, and both reached
      `payload_operators` byte-identical after resolution;
    * the target's declared `op_type` is NOT part of it, because pass 2 coerces
      every MITIGATES target to `IMPL` — two MITIGATES differing only there are
      the same edge in the payload.

    The target's endpoint PAIR still belongs in the identity: two MITIGATES on
    one (src, dst) that attack different edges are two edges, and collapsing them
    dropped the second plus its materialised target IMPL with no warning.
    """
    if not isinstance(op, dict):
        return ("", "", "", "")
    target = op.get("target") or op.get("target_edge") or {}
    if not isinstance(target, dict):
        target = {}

    def _fold(value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip().lower())

    return (_fold(op.get("src")), _fold(op.get("dst")),
            str(op.get("op_type", "") or "").upper(),
            f"{_fold(target.get('src'))}\0{_fold(target.get('dst'))}")


def operators_of(rec: dict[str, Any]) -> list[dict[str, Any]]:
    """Every operator a held record carries, legacy-singular included.

    The drain replays these, so this is the one reader: a record written before
    ``operators`` existed carries ``operator`` (one dict), and dropping it would
    lose the edge the store exists to keep.
    """
    if not isinstance(rec, dict):
        return []
    ops = [o for o in (rec.get("operators") or []) if isinstance(o, dict)]
    if not ops and isinstance(rec.get("operator"), dict) and rec["operator"]:
        ops = [dict(rec["operator"])]
    return ops


def open_store(graph: str, root: str | Path | None = None) -> HoldStore:
    """The caller's entry point: a store for ONE graph.

    ``TORTOISE_IDENTITY_HOLD_DIR`` overrides the root, so a deployment can place
    the store on its own volume without the library guessing. The default is the
    SDK's local state directory (``~/.tortoise/identity-hold``), NOT a temp dir —
    a hold that evaporates on reboot is memory loss wearing a hold's name.

    ⛔ The filename carries a DIGEST of the raw graph string (#10997 review): a
    slug alone mapped `graph/a`, `graph-a` and `graph a` onto one file, so a
    drain for one graph could commit another's held identity. The slug stays for
    readability; the digest is what identifies it.

    ⛔ ONE store per path PER PROCESS (#10997 review): the in-memory state is what
    `claim` decides against, so two instances over the same file would both see
    `pending` and both mint the same identity — the duplicate this module exists
    to prevent. Cross-PROCESS exclusion is still the caller's to arrange and is
    stated as such in the class docstring rather than pretended.
    """
    raw = str(graph or "").strip()
    base = root or os.environ.get("TORTOISE_IDENTITY_HOLD_DIR") or (
        Path.home() / ".tortoise" / "identity-hold")
    slug = _UNSAFE.sub("-", raw) or "default"
    digest = hashlib.sha256(raw.encode()).hexdigest()[:8]
    return HoldStore(Path(base) / f"{slug}-{digest}.jsonl")


#: One instance per resolved path, so a caller cannot accidentally hold two
#: competing in-memory views of one log (see `open_store`). Guarded by its own
#: lock; the values are `HoldStore`s, which are thread-safe internally.
_STORES: dict[str, HoldStore] = {}
_STORES_LOCK = RLock()


class HoldStore:
    """An append-only, per-graph log of held candidates and their outcomes.

    ⛔ NOT safe across PROCESSES: two writers to one file would interleave lines
    and — worse — two in-memory views would both `claim` the same key. Within a
    process, `open_store`/`HoldStore` for the same resolved path return the SAME
    instance (the module's `_STORES` registry), which is the exclusion this class
    can actually offer; a deployment that writes the same file from two processes
    must serialise them itself. Stated here rather than implied.

    A claim left by a process that DIED is recovered by :meth:`reclaim_stale` (an
    idle scan over the record's own ``claimed_at``), never by a second writer
    deciding on its own that the claim looks abandoned.
    """

    def __new__(cls, path: str | Path) -> HoldStore:
        key = str(Path(path).expanduser().resolve())
        with _STORES_LOCK:
            existing = _STORES.get(key)
            if existing is not None:
                return existing
            inst = super().__new__(cls)
            inst._initialised = False  # type: ignore[attr-defined]
            _STORES[key] = inst
            return inst

    def __init__(self, path: str | Path) -> None:
        if getattr(self, "_initialised", False):
            return  # a second construction for the same path is a no-op
        self.path = Path(path)
        self._lock = RLock()
        self._records: dict[str, dict[str, Any]] = {}
        self._load()
        self._initialised = True

    # ── persistence ────────────────────────────────────────────────────────
    def _load(self) -> None:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
            return
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                # A torn final line is the crash case the append-only form
                # tolerates; the rest still loads, and no judgement is made
                # about the fragment.
                continue
            if isinstance(rec, dict) and isinstance(rec.get("key"), str):
                self._records[rec["key"]] = rec

    def _append(self, rec: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
            fh.flush()

    def _transition(self, rec: dict[str, Any]) -> dict[str, Any]:
        self._records[rec["key"]] = rec
        self._append(rec)
        return dict(rec)

    # ── the four transitions ───────────────────────────────────────────────
    def hold(self, kind: str, content: str, *, where: str = "",
             session_ref: str = "", operator: dict | None = None,
             ref: str | None = None) -> dict[str, Any]:
        """Record an unresolved candidate as HELD. Idempotent, and never reopens.

        ``reopened`` is ``False`` for a key this store has already RESOLVED,
        which is the load-bearing half: a later ambiguous run must not re-arm an
        item that was already committed, or the next drain would mint it twice.
        The caller must READ it — the run that does not is the one that reports
        an already-committed identity as newly held (#10997 review round 4).

        ⛔ ``operator`` ACCUMULATES (review round 4, P1). The key is the REF, so
        this is called once per operator naming that ref; keeping only the first
        edge lost every edge after it while the caller reported them all as
        carried. The record now carries ``operators`` (deduped on
        ``operator_identity``) and mirrors the first into the legacy
        ``operator`` field, so an older reader still finds something it knows.
        """
        key = hold_key(kind, content)
        with self._lock:
            existing = self._records.get(key)
            if existing is not None:
                if existing.get("state") == HOLD_RESOLVED:
                    return {**existing, "reopened": False,
                            "note": "already resolved — a resolved identity is never re-held"}
                updated = {**existing, "runs_held": int(existing.get("runs_held", 1)) + 1}
                if operator:
                    _ops = operators_of(existing)
                    _seen = {operator_identity(o) for o in _ops}
                    if operator_identity(operator) not in _seen:
                        _ops.append(dict(operator))
                    updated["operators"] = _ops
                    updated.setdefault("operator", _ops[0])
                return {**self._transition(updated), "reopened": True}
            rec = {
                "key": key,
                "kind": kind,
                "content": str(content),
                # the UNCLAMPED ref the operator names — what a drain mints from
                "ref": str(ref or content),
                "where": where,
                "session_ref": session_ref,
                "operator": dict(operator or {}),
                "operators": [dict(operator)] if operator else [],
                "state": HOLD_PENDING,
                "minted_id": None,
                "runs_held": 1,
            }
            return {**self._transition(rec), "reopened": True}

    def claim(self, key: str) -> bool:
        """Take a PENDING item for minting. ``False`` = someone else has it.

        The exactly-once guard: only ``pending`` is claimable, so a second drain —
        or a second pass of this drain — cannot mint the same identity twice.

        ⛔ The claim is TIMESTAMPED, and that is what makes ``claimed`` a state
        rather than a lock (#10997 review round 3): without ``claimed_at`` no
        later run can tell a crashed drain's leftover from a live one, so the item
        stays invisible to every ``pending()`` forever. ``attempts`` is carried
        beside it so a census can show a key that keeps being claimed and dying.
        """
        with self._lock:
            rec = self._records.get(key)
            if rec is None or rec.get("state") != HOLD_PENDING:
                return False
            self._transition({**rec, "state": HOLD_CLAIMED,
                              "claimed_at": time.time(),
                              "attempts": int(rec.get("attempts", 0) or 0) + 1})
            return True

    def confirm(self, key: str, minted_id: str) -> dict[str, Any]:
        """Bind a claimed item to the id it minted. Refuses to fork an identity."""
        with self._lock:
            rec = self._records.get(key)
            if rec is None:
                raise KeyError(f"no held record for {key[:12]!r}")
            if rec.get("state") == HOLD_RESOLVED:
                if rec.get("minted_id") == minted_id:
                    return dict(rec)
                raise ValueError(
                    f"held identity {key[:12]!r} already resolved to "
                    f"{rec.get('minted_id')!r}; a second id {minted_id!r} would "
                    "fork one held identity into two nodes")
            return self._transition(
                {**rec, "state": HOLD_RESOLVED, "minted_id": minted_id})

    def release(self, key: str) -> bool:
        """Return a CLAIMED item to PENDING after a mint attempt that produced nothing.

        A claim whose mint produced nothing must not leak the item out of
        ``pending`` — that would be the leak this module exists to prevent, one
        state further along. This is the arm the DRAIN itself can take (it knows
        its mint produced nothing); a drain that is GONE cannot, which is what
        :meth:`reclaim_stale` is for.
        """
        with self._lock:
            rec = self._records.get(key)
            if rec is None or rec.get("state") != HOLD_CLAIMED:
                return False
            self._transition({**rec, "state": HOLD_PENDING})
            return True

    def stale_claims(
            self, min_idle: float | None = None) -> list[dict[str, Any]]:
        """CLAIMED records no live drain can still own — the reclaim population.

        A record is stale when its ``claimed_at`` is at least ``min_idle`` old, and
        ALSO when it carries no readable ``claimed_at`` at all: an un-datable claim
        cannot be shown to be live, and treating it as live is exactly the
        permanent leak this scan exists to end (a record written before claims
        were timestamped is the realistic instance).

        ``min_idle`` defaults to :data:`HOLD_CLAIM_IDLE_SECONDS` and is resolved at
        CALL time, not as a bound default argument — a caller (or a test) that
        adjusts the module constant must change this sweep, and a default
        parameter would silently freeze the value read at import.
        """
        idle = HOLD_CLAIM_IDLE_SECONDS if min_idle is None else float(min_idle)
        now = time.time()
        out: list[dict[str, Any]] = []
        with self._lock:
            for rec in self._records.values():
                if rec.get("state") != HOLD_CLAIMED:
                    continue
                try:
                    age = now - float(rec.get("claimed_at"))
                except (TypeError, ValueError):
                    age = None
                if age is None or age >= idle:
                    out.append(dict(rec))
        return out

    def reclaim_stale(self, min_idle: float | None = None) -> list[str]:
        """Release every stale claim back to PENDING; returns the keys released.

        The reclaim half of the ``claimed`` state — call it BEFORE reading
        ``pending()``, or a crashed drain's item stays invisible for another run.
        """
        released: list[str] = []
        for rec in self.stale_claims(min_idle):
            if self.release(rec["key"]):
                released.append(rec["key"])
        return released

    # ── reads ──────────────────────────────────────────────────────────────
    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            rec = self._records.get(key)
            return dict(rec) if rec else None

    def pending(self) -> list[dict[str, Any]]:
        """The drainable population, in the order they were first held."""
        with self._lock:
            return [dict(r) for r in self._records.values()
                    if r.get("state") == HOLD_PENDING]

    def summary(self) -> dict[str, Any]:
        """Counts by state and kind — the census the run reports, never silence.

        ⛔ Read through `.get()` (#10997 review round 2, P2): `_load` accepts any
        JSON object carrying a `key`, so a hand-edited or half-written record can
        lack `state`/`kind`. Indexing it raised `KeyError` out of a *reporting*
        method — inside `execute_embed` that replaced the whole S5 payload with
        the caller's error dict, i.e. a corrupt hold file could lose a session's
        points and operators. A census must not be able to sink the run.
        """
        with self._lock:
            by_state: dict[str, int] = {state: 0 for state in HOLD_STATES}
            by_kind: dict[str, int] = {}
            for rec in self._records.values():
                state = str(rec.get("state") or HOLD_UNKNOWN)
                kind = str(rec.get("kind") or HOLD_UNKNOWN)
                by_state[state] = by_state.get(state, 0) + 1
                by_kind[kind] = by_kind.get(kind, 0) + 1
            return {"path": str(self.path), "total": len(self._records),
                    "by_state": by_state, "by_kind": by_kind}
