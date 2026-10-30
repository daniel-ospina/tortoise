"""Tortoise SDK exceptions."""
from __future__ import annotations


class CalibrationError(Exception):
    """Raised when require_calibration=True and the graph is uncalibrated."""
    pass


class UnaddressableClaimError(ValueError):
    """EP refused the run: a claim it selected carries no usable ``id`` (#9275).

    EP keys every claim on ``id`` — the node cache, the message cache, and the
    posterior write-back's ``WHERE id = $id`` — so a ``:Point`` with no ``id``
    wired as an operator's claim has no address in the run. It can be neither
    hydrated nor stamped, and no SDK caller can repair it: ``set_point_baseline``
    needs the id to name the node, which is why the #9063 calibration gate
    excludes such a node rather than demanding a prior it could never satisfy.

    The run refuses BEFORE it stamps any posterior and says which repair the
    graph needs. It used to do the whole EP pass, flush the posteriors of every
    NAMED claim, and only then die in ``sorted(affected_claims)`` with a bare
    ``TypeError`` — so the caller saw a sort error while half the pass had
    already been written, and a retry stamped the same claims a second time.
    """
    pass


class ControlPlaneError(ValueError):
    """Registry operation failed — duplicate, not found, invalid role, etc."""
    pass


class AuditLogError(RuntimeError):
    """Fatal audit log failure — Postgres unreachable and fallback exhausted."""
    pass


class EmbeddedStoreBusyError(Exception):
    """Another PROCESS already holds this embedded FalkorDBLite store.

    Epic #900 §5.3 cross-process embedded overlap pin (cycle-21): the
    embedded topology is SINGLE-WRITER (#6761); on open, the SDK probes the
    redislite pid-registry (``<db_path>.settings`` + the recorded daemon
    pidfile with a liveness probe) and fails fast with this class when a
    LIVE holder exists — never a silent second daemon (two in-memory copies
    = split-brain; each process's writes land only in its own, last-save-wins).
    Same-process threads reuse the daemon by construction and never trip the
    probe (the holder pid is the caller's own).
    """

    def __init__(self, db_path: str, holder_pid: int):
        self.db_path = db_path
        self.holder_pid = holder_pid
        super().__init__(
            f"Embedded store busy: {db_path!r} is held by a live process "
            f"(pid {holder_pid}). FalkorDBLite is single-writer — run the "
            f"operation from that process, or point TORTOISE_DB_URI at a "
            f"server-mode FalkorDB (bolt://) for concurrent writers."
        )


class BundleValidationError(ValueError):
    """Epic #902 A2 — Phase-1 bundle validation failure (plan §5.2/§6.3).

    Carries ALL violations (not just the first) so agents can fix the whole
    bundle in one pass. ``.violations`` is the list of
    ``{"section", "index", "message"}`` dicts; ``.as_dict()`` returns
    ``{"code": "ERR_BUNDLE_INVALID", "violations": [...]}`` for the MCP wire.
    Subclasses ValueError so pre-A2 callers catching ValueError keep working.
    """

    code = "ERR_BUNDLE_INVALID"

    def __init__(self, violations: list[dict]):
        self.violations = list(violations)
        # The shipped message contract: the str() carries the FIRST
        # violation's message (Phase-2 parity); .violations has them all.
        super().__init__(self.violations[0]["message"] if self.violations else "bundle validation failed")

    def as_dict(self) -> dict:
        # Plan §6.3 pins {error, code: "ERR_BUNDLE_INVALID", violations} —
        # the error key carries the first violation's message (REVIEW-FIX P2).
        return {"error": str(self), "code": self.code,
                "violations": self.violations}


class BudgetExceededError(RuntimeError):
    """Raised when a dream pass exhausts its operator budget (epic 903-C6,
    #1244).

    The SDK-side raise site is the full-mode pass: a full pass is
    contractually complete-in-one-pass (J3), so when an EXPLICIT ``budget``
    is set and the graph requires more operators than the budget, the pass
    fails loudly instead of silently truncating (truncation is only legal
    for stale-first window passes, where deferral is the design — truncated
    claims stay stale and re-enter the staleness ranking next pass).
    ``budget=None`` keeps the existing 200_000-op DoS guard (warn +
    truncate, never raise).

    Carries the budget and the required operator count so hosted wiring
    (epic 903-C8, ``/v1/dream`` 429 + Retry-After per I4) and the MCP layer
    (903-C11, ``ERR_QUOTA``) can build their wire responses from one raise
    site.
    """

    def __init__(self, message: str, budget: int | None = None,
                 required: int | None = None):
        self.budget = budget
        self.required = required
        super().__init__(message)


class DreamNoOpError(RuntimeError):
    """Raised when a dream pass reports success while writing ZERO belief
    state on a graph that has EP factors to propagate (#3139).

    The silent-no-op failure class: the pass's operator/factor selection
    came back empty and the confidence write-back never ran, yet the pass's
    window contains derived-live operators or operator-less direct edges.
    Every mode's result shape reports ``converged``/``converged_all: True``
    and ``budget_used: 0`` in that state — indistinguishable from a
    legitimately empty graph — so the pass fails loudly instead.

    The known producer is a dropped boolean property index: ``GRAPH.COPY``
    drops the ``false`` entries of an ``is_operator`` range index (#3154), so
    ``X.is_operator = false`` filters yield ∅ over a populated graph. The
    dream/EP path uses the index-independent predicate form; this error is
    the fail-closed backstop for any future selection regression.

    A legitimately empty pass (no EP factors in the window) is NOT an error:
    it returns normally and records ``no_op_reason`` on the health surface
    (``dream_health_check``).
    """

    def __init__(self, message: str, mode: str | None = None,
                 eligible_factors: int | None = None):
        self.mode = mode
        self.eligible_factors = eligible_factors
        super().__init__(message)


class Phase2Error(ValueError):
    """Epic #902 A2 — Phase-2 write failure (post-validation, partial state
    may be committed). Carries the bundle's computed batch_id so the agent
    can audit what committed before re-sending (plan §6.4 Phase-2 row,
    cycle-23/24 pin). Surfaces as `{error, batch_id}` with NO code (distinct
    from Phase-1's ERR_BUNDLE_INVALID and quota codes — E2E-15(h))."""

    def __init__(self, message: str, batch_id: str | None = None):
        self.batch_id = batch_id
        super().__init__(message)


# ── Ask-lane typed exceptions (#1987 Task 5) ──────────────────────────────
# The eval-only ask lane (tortoise/ask_lane.py) maps validation/reader/
# retrieval failures to AskValidationError / AskReaderUnavailable /
# AskRetrievalUnavailable;
# each carries a ``code`` class attribute referencing the canonical vocabulary
# constants BELOW (single home — ``tortoise/schemas.py`` re-exports them).
# The wire-body half of that contract is gone: the hosted /v1/ask route and
# its path-scoped translation were removed in #3849.

# Canonical error-code vocabulary (10 codes; SIX are still carried by the
# eval-only lane — reader_unavailable, retrieval_unavailable, and the four
# validation codes invalid_question / invalid_question_type /
# invalid_question_date / question_too_long — and FOUR are the retired wire
# vocabulary with no raiser — unauthorized, quota_exceeded, in_flight_limit,
# timeout).
CODE_UNAUTHORIZED = "unauthorized"
CODE_QUOTA_EXCEEDED = "quota_exceeded"
CODE_IN_FLIGHT_LIMIT = "in_flight_limit"
CODE_READER_UNAVAILABLE = "reader_unavailable"
CODE_RETRIEVAL_UNAVAILABLE = "retrieval_unavailable"
CODE_TIMEOUT = "timeout"
CODE_INVALID_QUESTION = "invalid_question"
CODE_INVALID_QUESTION_TYPE = "invalid_question_type"
CODE_INVALID_QUESTION_DATE = "invalid_question_date"
CODE_QUESTION_TOO_LONG = "question_too_long"


class AskValidationError(ValueError):
    """Client-input validation failure on the eval-only ask lane. Carries the
    canonical ``code`` (``invalid_question``/``question_too_long``/
    ``invalid_question_type``/``invalid_question_date``). The hosted-lane
    400/401/403/422 mappings were removed with the REST surface (#3849)."""

    code = CODE_INVALID_QUESTION

    def __init__(self, message: str, *, code: str | None = None,
                 status_code: int | None = None):
        self.status_code = status_code
        if code is not None:
            self.code = code
        super().__init__(message)


class AskQuotaExceeded(RuntimeError):
    """429 ``quota_exceeded`` — the org's per-minute ask budget is spent.
    Carries ``retry_after`` (seconds) when the server provided one.

    RETIRED (#3849): no raiser. Its only producer was the removed SDK
    ``_post_ask`` status map; the ask budget itself is retained-but-uncalled
    by any product path (tests/test_quota.py still pins `run_ask_bounded`'s
    exec floor) pending the #3849 §7 D5 purge. Kept as vocabulary, not as
    live surface."""

    code = CODE_QUOTA_EXCEEDED

    def __init__(self, message: str, *, retry_after: float | None = None,
                 status_code: int | None = 429):
        self.retry_after = retry_after
        self.status_code = status_code
        super().__init__(message)


class AskInFlightLimit(RuntimeError):
    """429 ``in_flight_limit`` — the per-org in-flight ask cap is full.

    RETIRED (#3849): no raiser. Its only producer was the removed SDK
    ``_post_ask`` status map; the cap machinery is retained-but-uncalled by
    any product path (tests/test_quota.py still pins `run_ask_bounded`'s exec
    floor) pending the #3849 §7 D5 purge. Kept as vocabulary, not as live
    surface."""

    code = CODE_IN_FLIGHT_LIMIT

    def __init__(self, message: str, *, status_code: int | None = 429):
        self.retry_after = None
        self.status_code = status_code
        super().__init__(message)


class AskReaderUnavailable(RuntimeError):
    """502 ``reader_unavailable`` — the LLM reader failed with no surviving
    lane (the ask lane raises it on reader build failure, empty output after
    the bounded retry, or a reader exception).

    The code-less variants the removed SDK ``_post_ask`` client used to map
    here (a code-less 402 provider-billing condition, a code-less 404 for the
    gone hosted ask surface, and the connection-refused ``status_code=None``
    case) have NO raiser since #3849; ``status_code`` is kept for the
    vocabulary."""

    code = CODE_READER_UNAVAILABLE

    def __init__(self, message: str, *, status_code: int | None = 502):
        self.status_code = status_code
        super().__init__(message)


class AskRetrievalUnavailable(RuntimeError):
    """502 ``retrieval_unavailable`` — retrieval/annotation/context
    assembly failed wholesale, or (also raised by the lane) the eval-only
    entry point was handed a hosted client (``TORTOISE_API_URL`` set) and
    refuses it. No HTTP status ships it any more (#3849): the ``status_code``
    default is retained vocabulary."""

    code = CODE_RETRIEVAL_UNAVAILABLE

    def __init__(self, message: str, *, status_code: int | None = 502):
        self.status_code = status_code
        super().__init__(message)


class AskTimeout(RuntimeError):
    """504 ``timeout`` — the bounded ask section exceeded the server's
    ``_ASK_TIMEOUT_S`` (server-504-fired) OR the SDK client-side timeout
    fired (wire connect/read timeout — ``source`` marks which).

    RETIRED (#3849): no raiser. The server-504 half went with the hosted
    /v1/ask route and the client-side half with ``_post_ask`` /
    ``ASK_SDK_TIMEOUT_S`` (both deleted in #3849). Kept as vocabulary, not as
    live surface."""

    code = CODE_TIMEOUT

    def __init__(self, message: str, *, source: str = "server",
                 status_code: int | None = 504):
        self.source = source  # "server" (received 504 body) | "client"
        self.status_code = status_code
        super().__init__(message)


class HybridReadUnavailableError(RuntimeError):
    """(C) #2952 — a read that could not run its vector leg must not be
    labelled a hybrid read.

    Raised by ``tortoise.search_engine.require_hybrid_read`` when a
    real-lane measurement (or any fail-loud consumer) asks a read surface to
    prove it was hybrid and the vector (semantic) leg did not contribute a
    healthy result — including when the surface cannot report its legs at
    all. This is the product-side counterpart of the #2985 / PR #3005 battery
    capability gate: an FTS-only score is a degraded, keyword-only surface
    and must never be recorded as the product's hybrid retrieval.

    ``marker`` is the ``declared_degraded_read`` dict (or the
    ``leg_trace_unavailable`` variant); ``reason`` mirrors its reason.
    """

    def __init__(self, marker: dict | None, *, lane: str | None = None):
        self.marker = dict(marker or {})
        self.lane = lane
        self.reason = self.marker.get("reason")
        lane_part = f" on lane {lane!r}" if lane else ""
        super().__init__(
            f"hybrid read unavailable{lane_part}: the vector (semantic) leg "
            f"did not contribute a healthy result (reason={self.reason!r}) — a "
            f"single-leg (keyword-only) read is NOT the product's hybrid "
            f"retrieval and must not be labelled hybrid (#2952). "
            f"marker={self.marker!r}"
        )


class EmbedderUnavailableError(RuntimeError):
    """(C) #4861 — a process that REQUIRED the embedder must not be handed
    ``None``.

    Raised by ``tortoise.embeddings.EmbeddingModel.get`` when
    ``TORTOISE_EMBEDDING_MODEL_REQUIRED`` is truthy and the model cannot be
    loaded, instead of returning ``None``. This is the **third surface of one
    invariant**: a lane that cannot run hybrid must not run (#2985,
    ``retrieval_preflight.require_hybrid_retrieval``), a read that could not
    run its vector leg must not be labelled hybrid (#2952,
    :class:`HybridReadUnavailableError`) — and this one, at the ``None``
    itself, where neither of the other two can see it. Without it a
    keyword-only (FTS-only) run is indistinguishable from a healthy one: the
    degrade is invisible, which is the defect #2898 describes.

    Unlike :class:`HybridReadUnavailableError` this carries no leg *trace* — a
    load failure has no legs to report, and synthesizing a marker would make
    that class mean two different things. It carries the **cause** instead:
    ``failure_kind`` is ``not_installed`` (the environment never had it — a
    runner-down or missing-extra run) or ``load_failed`` / ``load_timeout``
    (the environment had it and the load broke — a different thing to fix).
    ``model_unavailable`` is the fallback when no kind was recorded.

    ⛔ Its guarantee is only as wide as the narrowest ``except`` between the
    raise and the caller: #5275 found ``TortoiseSDK.tortoise_fts_query``
    catching it in a blanket ``except Exception`` and converting it into the
    designed-absence ``no_embedder`` degrade, so an ARMED process still got
    keyword-only results with no signal. The read path now catches this class
    BY NAME and re-raises it; ``fallback_tfidf`` does the same. Write paths
    (``encode_for_store`` callers) and the cached-snapshot fallback tier
    degrade on purpose and are enumerated in
    ``tests/test_embedding_model_required.py``'s module docstring — the claim
    is scoped to the READ path and to processes that armed the flag.
    """

    def __init__(self, *, failure_kind: str, model: str,
                 revision: str | None = None,
                 last_error: str | None = None,
                 context: str | None = None):
        self.failure_kind = failure_kind
        self.model = model
        self.revision = revision
        self.last_error = last_error
        self.context = context
        where = f" ({context})" if context else ""
        rev = f" @ {revision}" if revision else ""
        err = f"; last error: {last_error}" if last_error else ""
        super().__init__(
            f"embedding model REQUIRED but unavailable{where}: {model}{rev} "
            f"could not be loaded (failure_kind={failure_kind!r}){err} — this "
            f"process set TORTOISE_EMBEDDING_MODEL_REQUIRED, so any retrieval "
            f"result it produced would be keyword-only (FTS) while claiming to "
            f"be the product's hybrid retrieval. Install the embedder — "
            f"`uv sync --extra embeddings --extra parity` (an explicit --extra "
            f"list is EXACT and silently REMOVES any extra you omit — #2985; "
            f"`uv sync --all-extras` also works) — or unset the variable to "
            f"allow the documented keyword-only degrade (#4861)."
        )


class UnsupportedCypherOperatorError(ValueError):
    """#3595 — a Cypher query used an operator FalkorDB does not implement.

    FalkorDB has no ``=~`` regex-match operator, and it does not error: it
    prints ``FalkorDB does not currently support =~`` *in place of results*,
    so the surrounding query returns an **empty result set** — a confident
    false negative that is indistinguishable from "no matches". Two agents in
    one session read exactly that: an enumeration for legacy ``obj-<26hex>``
    ids returned 0 across every graph, where the supported ``STARTS WITH``
    found 5 nodes in 2 graphs.

    Raised by the guarded-handle seam (``tortoise.cypher_guard``) BEFORE the
    statement is sent, so a caller cannot mistake an unsupported operator for
    an empty answer.
    Subclasses ``ValueError``: an unsupported operator is a caller-side
    predicate bug, and the historical ``ValueError`` for invalid input keeps
    working.

    ``operator`` is the offending token (currently always ``"=~"``); the
    message names the supported alternatives.

    ``cypher`` is retained as an ATTRIBUTE for a caller that wants it, but is
    deliberately NOT interpolated into the message: this exception propagates
    through `mcp_server`'s ``_SafeError(_scrub_error(str(e)))`` to the tenant, and
    ``_scrub_error`` strips credentials and host:port but nothing else — so a
    query in the message would reach a tenant verbatim. That is exactly what
    `security.redact_error`'s contract forbids ("Never includes full Cypher,
    query text, or tracebacks … so tenants never see DB/query internals").
    """

    def __init__(self, operator: str, cypher: str = ""):
        self.operator = operator
        self.cypher = cypher
        super().__init__(
            f"FalkorDB does not support the Cypher {operator!r} regex-match "
            f"operator, and it fails SILENTLY — the query returns an EMPTY "
            f"result set indistinguishable from 'no matches' (#3595). "
            f"Use a supported operator instead: STARTS WITH / ENDS WITH / "
            f"CONTAINS."
        )


class NameAlreadyHeld(ValueError):
    """#3590 — a rename would put two LIVE entities on one natural key.

    The name is a mutable NATURAL KEY (decision doc R2): not an identity, but
    still a key a by-name resolve has to answer for, and `entity_identity`
    refuses to guess when two or more live entities hold one name. Allowing a
    rename onto a held name would therefore not rename anything useful — it
    would make every later `create_object`/`create_subject` and by-name read of
    that name ambiguous, with no by-name repair path (the name is
    create-poisoned).

    Raised by ``TortoiseSDK._refuse_ambiguous_rename`` BEFORE any write, so a
    refusal leaves the graph untouched. A rename onto a TERMINAL holder's name
    is allowed — that holder is a different incarnation, not a live competitor.
    """

    def __init__(self, label: str, name: str, holder_ids, target_id: str):
        self.label = str(label)
        self.name = str(name)
        # ⛔ A competitor row may carry NO id: an id-less LIVE carrier is a real
        # holder (``entity_identity.live_name_holder_ids`` is row-per-holder and
        # yields ``None`` for it) and it is exactly the shape the create ADOPTS.
        # It is LABELLED here rather than filtered out (#9876 review F1) — the
        # count in this message is how many live rows are in the way, so
        # dropping one would report the graph as less ambiguous than it is
        # (``str(None)`` would instead read as a carrier whose id is "None").
        self.holder_ids = tuple(
            "<id-less live carrier>" if h is None else str(h)
            for h in holder_ids)
        self.target_id = str(target_id)
        super().__init__(
            f"cannot rename {self.label} {self.target_id!r} to {self.name!r}: "
            f"{len(self.holder_ids)} other live {self.label} "
            f"already hold(s) that name {list(self.holder_ids)} — a live "
            f"holder would make the name ambiguous (route-then-refuse) and "
            f"create-poison it. Rename the holder first, or use a free name "
            f"(#3590 D3).")


class UnrepresentableNumberError(ValueError):
    """#4647/#7174 — a Cypher PARAM holds a number the store would ALTER.

    FalkorDB stores an integer as INT64 and a number as a double. A Python
    ``int`` is unbounded and ``decimal.Decimal`` is arbitrary-precision, so a
    value outside those domains is SILENTLY ALTERED: ``SET n.v = $v`` reports
    success and stores a different number (``2**70`` is stored as
    ``9223372036854775807``; ``Decimal('0.12345678901234567890')`` is rounded).

    Raised by the numeric-domain seam (``tortoise.cypher_guard``) on every
    guarded handle BEFORE the statement is sent, so a writer no longer has to be
    on the SDK's hand-maintained call-site list to be covered (#7174). The seam
    covers TWO surfaces: a value in a ``params`` map (``SET n.v = $v``) and a
    number written as a LITERAL in the statement text (``CREATE (n {v: 2**70})``)
    or inlined into a ``CYPHER k=<v>`` header (#8479) — so a statement-text
    literal no longer reaches the store. What it still does NOT cover is a number
    sent through a raw vendor client the package did not build. The message names
    the param KEY, or the literal's OFFSET in the statement, and the reason, and
    never interpolates Cypher (same contract as
    :class:`UnsupportedCypherOperatorError`).

    Subclasses ``ValueError``: the SDK's #4647 call sites raise ``ValueError``
    for this same condition, and that call convention is kept.
    """


class WriteNotLandedError(RuntimeError):
    """#7209 — a write reported SUCCESS and is not in the graph.

    The defect this closes is the SILENT half of #7209: ``create_point``
    mints its Point id client-side, issues the ``CREATE``, and then reads the
    point back — but it returned whatever that read returned, including an
    EMPTY dict, raising nothing and logging nothing. A caller holding ``{}``
    cannot tell a discarded write from a successful one, so a finding the
    writer believes it recorded can be gone (the same "write that looks lost"
    shape as the abort path in #7405 / #7685, one layer up).

    The write path this guards already retries the engine's
    graph-deleted-or-replaced abort (``TortoiseSDK._graph_write_with_retry``),
    and that retry's own docstring records the residual it CANNOT close: the
    retry is gated on the graph KEY being present, so a rebuild whose recreate
    has already landed is retried INTO the rebuilt graph — and if the rebuild's
    replay then replaces the graph again, the statement is gone while the
    writer was told it succeeded. Separating those two fully needs an
    engine-level identity token (#7685's escalation line).

    What the SDK can do — and now does — is refuse to REPORT that success.
    ``create_point`` verifies its own write against the read-back it already
    performs: an unreadable Point raises this error naming the graph and the
    id.

    #7951 — TWO ARMS, and they must not be confused. Since the §17.1 outbox
    ordering, ``create_point`` appends its ``PointAdded`` record BEFORE the
    ``CREATE``:

    * **no record was committed** (``record_committed=False``, the default) —
      either the journal append failed (and the cache write was then skipped by
      the §17.1 ordering), or no journal is configured, in which case the
      read-back miss is a genuinely LOST write. Re-issuing is correct.
    * **read-back empty after a COMMITTED append** (``record_committed=True``)
      — the TRUTH holds the record and the cache merely has not materialised
      it. A replay heals it (the ordering contract's replay-healable window),
      so this must NOT be read as "the write is gone"; a blind re-issue would
      append a SECOND ``PointAdded``. Callers that distinguish the arms key on
      :attr:`record_committed`.

    Loud by construction: a caller that sees this error knows the cache did
    not materialise the write. It is never raised for a read-back that
    SUCCEEDED.

    #7951 — `update_point` RAISES IT TOO, with ``operation="update_point"``.
    The revision door took the same outbox ordering after ``create_point``
    (increment 2 of the plan of record), so its no-record arm is the same
    refusal: the append failed, the cache write was skipped, and the caller
    must not read the call as an applied update. Named so the two doors cannot
    drift into two messages for one condition — the failure mode the class
    exists to prevent is a caller that cannot tell which arm it is in.

    #7951 — `delete_point` RAISES IT TOO, with ``operation="delete_point"``.
    #9207 REVERSED that door's ordering (the `DETACH DELETE` is issued first and
    the record follows only a LANDED destroy), so the arm this door actually
    raises is the ``record_committed=False`` one below: the destroy landed and
    the record did not confirm. The COMMITTED arm is retained as the class's
    contract for that state but is no longer produced by this door — a record
    can no longer precede the destruction it describes.

    #9207 — the delete door's ``record_committed=False`` has a THIRD case for
    the same reason, and it is the one that can say the least: the ``DETACH
    DELETE`` raised and the read-back raised too (``destroy_verified=False``),
    so the destroy's landing is UNKNOWN and no record is written. It states
    that the destroy is unverified — not which store holds what — and tells the
    caller to establish the cache state first: a present node is re-issued, an
    absent one is repaired with a destructive ``rebuild_all``.

    #7951 — and the delete door's ``record_committed=False`` arm gets its own
    three clauses too (#7951 review round 3). ``False`` from `_emit_event`
    means only that the journal ``append`` RAISED, and an append can land the
    record and then raise (a partial `write(2)` stopped by ENOSPC/EIO, or a
    write whose close failed — #5612 removed the buffered flush that used to be
    the common case), so the shared create-shaped text was wrong
    twice over on this door: "no rebuild record was committed for it" when the
    record may be durably on disk, and "the write was not applied, so re-issue
    it" when a blind re-issue appends a SECOND delete record for one intent.
    The two doors state their own arms; every other door shares one message per
    condition.
    """

    def __init__(self, point_id: str, graph: str | None = None,
                 detail: str = "", *, record_committed: bool = False,
                 operation: str = "create_point",
                 record: str | None = None,
                 destroy_verified: bool = False) -> None:
        self.point_id = point_id
        self.graph = graph
        self.operation = operation
        # The default is derived from the DOOR, not hardcoded (#7951 review
        # round 4): "The PointAdded record WAS appended before the DETACH
        # DELETE", and "the PointAdded record's landing is UNCONFIRMED", both
        # name a record this door never writes. A caller that omits `record`
        # must still get the true noun.
        self.record = record or (
            "EntityMutated op=delete" if operation == "delete_point"
            else "PointRevised" if operation == "update_point"
            else "PointAdded")
        if record_committed and destroy_verified:
            # `record_committed=True` says the record PRECEDED the cache write;
            # `destroy_verified=True` says a read-back PROVED the destroy landed
            # before the record was written. No door can be in both, and
            # rendering the pair produced a message asserting "the Point was
            # DESTROYED in the record and not in the cache … no read-back is
            # involved on this arm" — a self-refuting refusal, which is the one
            # thing this class exists to prevent (#7951, #9207).
            raise ValueError(
                "WriteNotLandedError: record_committed=True contradicts "
                "destroy_verified=True — a committed record cannot precede the "
                "destroy it describes; pass at most one of them")
        self.detail = detail
        self.record_committed = record_committed
        # `delete_point` ONLY. `record_committed=False` has two sub-cases on
        # that door since #9207, and they must NOT share prose: the destroy
        # LANDED and only the record is unconfirmed, or the door could not
        # determine whether the destroy landed at all. The flag is therefore
        # OPT-IN (#9207 review): it defaults to the arm that CLAIMS NOTHING,
        # because a constructor that forgets it must not assert a cache state
        # nobody read — the door that knows the destroy landed passes True.
        # The single message the class used to emit on both cases asserted
        # "the `DETACH DELETE` did not report a failure of its own", which is the
        # OPPOSITE of the unverified case, where that raise IS why the arm was
        # reached — the same class of false claim #9207 exists to remove, one
        # layer up.
        self.destroy_verified = destroy_verified
        _where = f" on graph {graph!r}" if graph else ""
        if record_committed:
            if operation == "delete_point":
                # NOT PRODUCED BY `delete_point` SINCE #9207 — kept because the
                # class is the doors' shared contract and a caller may still
                # construct it. #9207 reversed this door's ordering (the
                # `DETACH DELETE` now runs first, so a committed record can no
                # longer precede it), which is why the text below describes the
                # state and not this door's sequence.
                # `_where` names the CACHE graph, so it attaches to the cache
                # clause: "in the record on graph X" pointed a caller at the
                # store that does NOT hold the destruction (#7951 review
                # round 4).
                _headline = (
                    f"the Point {point_id!r} was DESTROYED in the record and "
                    f"not in the cache{_where}"
                )
                _cause = (
                    "the destruction record is committed and the cache delete "
                    "did not land (the graph is being replaced/rebuilt) — no "
                    "read-back is involved on this arm"
                )
                _contract = (
                    f"The {self.record} record is COMMITTED, so the TRUTH no "
                    "longer holds this Point and a replay will REMOVE the "
                    "stale node from the cache — do NOT re-issue blindly "
                    "(that would append a second delete record for one "
                    "intent); treat the deletion as COMMITTED and let the "
                    "projection catch up. The cache is `extra_in_cache` "
                    "(tortoise/reconciliation.py), not merely lagging."
                )
            else:
                _headline = f"the Point {point_id!r} was not committed{_where}"
                _cause = (
                    "the cache did not materialise the write (the graph is "
                    "being replaced/rebuilt, or it was replaced between the "
                    "write and the read-back)"
                )
                _contract = (
                    f"The {self.record} record WAS appended before the cache "
                    "write (the #7951 §17.1 outbox ordering), so the TRUTH "
                    "holds this write and a replay will materialise it — do "
                    "NOT re-issue blindly (that would append a second "
                    "record); treat the write as COMMITTED and let the "
                    "projection catch up."
                )
        elif operation == "delete_point" and not destroy_verified:
            # The THIRD case (T3c): neither the cache state nor the record's
            # landing is knowable, so say exactly that and claim NOTHING about
            # either store. The landed-destroy arm below must not be used here:
            # its cause clause ("the `DETACH DELETE` did not report a failure of
            # its own") is false — that raise is how this arm is reached.
            _headline = (
                f"the destruction of Point {point_id!r} was NOT VERIFIED"
                f"{_where}"
            )
            _cause = (
                "the `DETACH DELETE` raised AND the cache could not be re-read "
                "afterwards, so whether the destroy landed is UNKNOWN. No "
                "record was written, because a record states what the cache "
                "DID and no read after the failure established that"
            )
            # ⚠️ Both branches below state ONLY what a later read makes true.
            # The first version of this clause said a present node means "the
            # cache is behind the TRUTH, which no longer records a destruction
            # for it" — self-contradictory (nothing is behind a truth that does
            # not claim the destruction) and false, since this arm writes no
            # record at all.
            _contract = (
                "Neither store is known to be consistent. Do NOT read this call "
                "as a completed delete. Establish the cache state first "
                "(`get_point`), then: node PRESENT ⇒ the destroy did not land, "
                "the cache and the TRUTH still agree, and the delete must be "
                "RE-ISSUED; node ABSENT with no "
                f"{self.record} record ⇒ the destroy landed unrecorded — the "
                "cache is BEHIND the TRUTH (`missing_in_cache`: the journal still "
                "holds the Point, the cache does not) — and the repair is a "
                "DESTRUCTIVE `rebuild_all`, which re-creates the Point from the "
                "journal; the delete must then be RE-ISSUED, or the Point the "
                "rebuild just restored stays live"
            )
        elif operation == "delete_point":
            # The DELETE door's unconfirmed arm, REWRITTEN FOR #9207's ORDER.
            # The `DETACH DELETE` has ALREADY LANDED when this is raised (it is
            # issued first, and the record only follows a landed destroy), so
            # the pre-#9207 text — "the ordering skipped the `DETACH DELETE` and
            # the cache still holds this Point" — was the opposite of the
            # truth. "No record was committed" is NOT the state either: an
            # append can land and then raise (#5612: `EventLog.append` writes
            # the whole record in ONE unbuffered `write(2)`, so what remains is
            # a partial write followed by an error (ENOSPC/EIO), or a full
            # write whose close then fails). Either way the destruction record
            # is UNCONFIRMED, not known-absent, so the caller must neither read
            # the call as done nor assume the truth still holds the Point.
            _headline = (
                f"the destruction of Point {point_id!r} did NOT confirm"
                f"{_where}"
            )
            # ⚠️ Do NOT say the `DETACH DELETE` "did not report a failure of
            # its own" (#9207 whole-diff review): this arm is ALSO reachable
            # when the statement raised a transport error over an APPLIED
            # destroy, the read-back proved it gone, and only the append then
            # failed — the door logs exactly that beside this message
            # (`delete_point: the DETACH DELETE raised (…) and the cache no
            # longer holds the Point — either this statement applied and the
            # reply was lost, or the Point was already gone`). What is true on
            # BOTH paths is the destroy: it landed.
            _cause = (
                "the destroy LANDED in the cache (the statement returned, or it "
                "reported a failure after the write had already applied and the "
                "read-back proved the Point gone), and the JSONL journal append "
                "for its record did not confirm (it raised) — the op=delete "
                "record MAY have landed anyway: the journal write can land "
                "partially and then raise (ENOSPC/EIO), or land and then fail "
                "on close. Nothing was written by this door that claims more "
                "than the cache state it read"
            )
            _contract = (
                f"The {self.record} record's landing is UNCONFIRMED rather "
                "than absent: if it did land, the deletion is COMMITTED and "
                "nothing further is needed. If it did not, the cache no longer "
                "holds this Point (or it was already absent when the statement "
                "ran) while the TRUTH still does, and NOTHING repairs that by "
                "itself — `recover_from_log` rebuilds only an EMPTY graph, and "
                "`reconcile` only counts the divergence. The repair is a "
                "DESTRUCTIVE `rebuild_all`, which re-creates the Point from the "
                "journal; the delete must then be re-issued (a re-issue before "
                "that returns `False` for the absent node and records nothing). "
                "Do not treat this call as a completed delete."
            )
        else:
            _headline = f"the Point {point_id!r} was not committed{_where}"
            _cause = (
                "no rebuild record was committed for it — the JSONL journal "
                "append FAILED (and the #7951 §17.1 ordering then skipped the "
                "cache write), or this SDK has no journal configured at all, so "
                "the write has no durable home"
            )
            _contract = (
                "The caller must not treat this call as successful: the "
                "write was not applied, so re-issue it once a rebuild record "
                "can be committed — every door this class guards is "
                "re-issue-safe (`create_point`'s `dedup=True` is content-hash "
                "idempotent, `update_point` re-states the same revision, and "
                "`delete_point` re-issues a `DETACH DELETE` by explicit id, "
                "which is a no-op when the node is already gone). (A "
                "`:GraphEvent` may have been written first by `_emit_event` — "
                "a declared residual, not a committed rebuild record.)"
            )
        super().__init__(
            f"{operation}: {_headline}: "
            f"{_cause}. {_contract} (#7209)"
            + (f" [{detail}]" if detail else "")
        )
