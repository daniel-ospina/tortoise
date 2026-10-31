"""Per-org quota enforcement shared by REST and MCP (#329, #683, #686).

Design: limits are resolved ONCE by the authenticated caller
(``hosted_api.get_current_org`` / MCP ``OrgResolutionMiddleware``) via
``resolve_org_limits`` and passed to ``enforce_org_limit`` — never re-fetched
per write.

Fail-closed decision (#686)
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Counting is **fail-closed**: any counting exception raises
``QuotaCheckError`` (server error), never a silent pass.

Rationale:
- **Money at stake.** Fail-open would let free-tier orgs exceed paid limits
  undetected during a DB outage — direct revenue risk and abuse vector.
- **Fail-closed is the secure default.** When you can't verify, don't grant.
- **Customer harm from fail-closed is bounded.** A DB outage that breaks
  count queries typically also breaks the actual write (same store) — we're
  failing fast, not adding net-new unavailability.
- **Alerting mitigates ops risk.** Every count failure is logged at ERROR
  level with org_id and resource, so operators see the outage immediately
  and can decide whether to temporarily disable enforcement.

Import topology: stdlib-only at module level; ``tortoise.sdk`` imported
function-level inside the helpers to avoid any cycle (hosted_api → mcp_server
→ mcp_auth → sdk is the canonical direction; quota is a leaf consumer).

No org context (stdio/operator) → ``enforce_org_limit(None, ...)`` returns
cleanly (skip) — mirrors REST ``_check_org_limit``'s ``if not org_id: return``.
Batch caps are unconditional in both modes.

Downgrade-over-limit decision (#683)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
When an org downgrades (e.g. Pro→Free) while over the NEW tier's limits
(e.g. 2 memberships but Free allows only 1), the downgrade MUST be BLOCKED
with a clear error message listing which limits the org exceeds.

Rationale and decisions:
- **Block downgrade (not graceful-degrade).** Allowing a downgrade that
  immediately locks out members or breaks graphs creates a trust-destroying
  experience: the dashboard shows Free features, but the org has 2 members
  and 2 graphs — inconsistent and confusing.
- **No silent pass.** Trust requires that published limits are real limits.
  If an org can be over-limit on a lower tier, the limits aren't real.
- **No auto-delete.** Never delete data to fit a downgrade. The org must
  explicitly remove members/graphs before the downgrade can proceed.
- **Stripe webhook downgrade path (future, #310).** When the
  ``customer.subscription.updated`` webhook processes a tier downgrade, the
  ``mirror_subscription`` handler should check limits via this module BEFORE
  applying the new tier: if the org exceeds the new tier's limits, the
  downgrade must be rejected (log + alert), keeping the org at its current
  tier until the over-limit condition is resolved by the org owner.

  Until #310 lands the Stripe integration, there is no user-facing tier-change
  REST endpoint — the decision is a documented policy, not a live code path.
  The ``org_update`` SDK method (registry-level, no REST surface) used to allow
  tier/limit writes for operational relief, without checking downgrade
  preconditions (operator intent overrides). #4282 **deleted that method**, so
  there is now no SDK affordance for the relief this paragraph describes — a
  reader must not treat this policy as an available lever.
"""
from __future__ import annotations

import logging
import os
import tempfile
from datetime import UTC, datetime

_logger = logging.getLogger(__name__)


def _make_sdk(*, namespace: str | None = None):
    """Build a TortoiseSDK with hosted_api._make_sdk's precedence (inline copy —
    quota is a leaf consumer and must not import hosted_api).

    URI mode (docker:// / redis:// / rediss://) when TORTOISE_DB_URI is set;
    else embedded via TORTOISE_DB_PATH (default /data/tortoise.db) with a
    tempfile fallback when the volume is unwritable (test env).
    """
    from tortoise.sdk import TortoiseSDK  # function-level: avoid cycles
    if os.environ.get("TORTOISE_DB_URI"):
        return TortoiseSDK(namespace=namespace)
    db_path = os.environ.get("TORTOISE_DB_PATH", "/data/tortoise.db")
    try:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
    except OSError:
        db_path = os.path.join(tempfile.gettempdir(), "tortoise.db")
    return TortoiseSDK(db_path=db_path, namespace=namespace)


# ── Batch-cap constants (unconditional — both modes) ───────────────────────
MAX_CHECKPOINT_ITEMS = 500
MAX_FILE_DECISION_OPTIONS = 50
MAX_FILE_DECISION_EVIDENCE = 100
MAX_TAGS_PER_POINT = 50
MAX_OPERATOR_TARGETS = 500
MAX_SESSION_TURNS = 500
MAX_EXTRACTIONS_PER_TURN = 200
MAX_ANALYZE_LLM_PER_MIN = 60
#: #1987 Task 6: per-org per-minute LLM-call budget for the ask lane
#: (mirrors ``MAX_ANALYZE_LLM_PER_MIN`` — the #329 pattern). Per-process
#: (in-memory) scoping: a multi-worker uvicorn deployment scales the
#: 60/min bound ×workers.
MAX_ASK_LLM_PER_MIN = 60
MAX_DREAM_FULL_PER_HOUR = 6

# ── Value-first mining budget + Layer-1 payload caps (epic #909 §4.4) ────
# Per-session CUMULATIVE budget: net-new non-episodic delta, post-
# reconciliation (consumed by the commit endpoint, epic #909 slice 5).
# soft → WARN telemetry at 15; hard 25 → hold (PL3); ceiling 50 → 402.
MAX_VALUE_POINTS_PER_SESSION = {"soft": 15, "hard": 25, "ceiling": 50}
# Layer-1 RAW payload point count cap → 422. Deliberately NAMED differently
# from the budget ceiling (also 50) to prevent wiring the wrong 50 (plan
# R-decoupling, §4.4): the raw cap is independent of the budget check.
MAX_PAYLOAD_POINTS = 50
# Layer-1 per-type payload caps → 422 (independent, not summed).
MAX_ENTITIES = 500
MAX_OPERATORS = 500
# #4051: the three COLLECTION caps the per-type set was missing. These
# arrays are what make a single request's stored-growth unbounded; the write
# path (``_execute_commit_writes``) consumes them ONE ENTRY at a time:
#   ``events[]``           → one ``:Event`` node + its ``produces`` edge (+ one
#                            ``MERGE`` per ``about_entities`` name) PER ENTRY
#   ``sources[]``          → one ``:Source`` PER ENTRY
#   ``provenance_refs[]``  → ONE session ``:Source`` regardless of count, but
#                            its stored ``provenance_spans`` property is EVERY
#                            ref's ``spans`` concatenated and its basenames
#                            fill the ``session_ref_urls`` extraction map, so
#                            the array still grows stored data per entry
# With no count cap the only bound was the 8 MiB request body: at the 1000-
# char event-content cap that is ~8k event nodes from ONE legal request, at
# 300 requests/min/key. Same number as MAX_ENTITIES/MAX_OPERATORS — and the
# same KIND of bound: a request-SHAPE bound (Layer-1 → 422), never a
# retention bound. It does not cap what a tenant may keep, so the unlimited-
# retention rulings (#4010 sessions, #3853 no eviction window) stand.
#
# RESIDUAL (known, deliberate): ``MAX_PROVENANCE_REFS`` bounds the NUMBER of
# refs, not the SIZE each carries — ``ProvenanceRef.spans`` has no item cap
# (``commit_schema.py`` ``class ProvenanceRef``), so ``provenance_spans`` is
# still bounded only by the 8 MiB body. Capping ``spans`` would reject a
# legitimately long document's span list, so the count cap is the honest bound
# here: it grows ONE node's property, not the node-minting multiplier the
# events/sources caps close.
# TWO further per-entry growths are deliberately NOT bounded here, because
# neither adds a graph node and both are pre-existing on surfaces of their own:
# ``Point.slots`` nested lists grow the EVENT JOURNAL (one
# EntityBindingRefused/EntityLinked record per slot entry,
# ``bind_point_subjects``), and ``ProvenanceRef.spans`` grows the property
# above. (``CommitEvent.slots`` is validated but never written — the write
# phase consumes ``Point.slots`` only, ``hosted_api.py:16461``.) Recorded so
# the next reader does not mistake this cap set for "the commit payload is
# fully bounded".
MAX_EVENTS = 500
MAX_SOURCES = 500
MAX_PROVENANCE_REFS = 500

# ── #4051 §3.1: the per-ORG ingest budget on the commit lane ───────────────
# WHAT THIS IS. A per-ORG, rolling-60s allowance on the STORED BYTES a tenant's
# ``POST /v1/sessions/commit`` requests may add, refused with 429 +
# ``Retry-After``. It is a THROTTLE (bounds the rate), NOT a cap (bounds the
# total) — see the RESIDUAL block below for what it deliberately leaves open.
#
# WHY IT EXISTS. The commit lane's only org-limit gate used to be the flat
# ``max_sessions``; #4010 made sessions unlimited for every tier (owner ruling,
# not to be reversed), and the replacement gates bound the REQUEST, not the
# total: the payload's collections are capped (#9425), the pre-write
# ``max_points`` gate is present (#5637) — but a ``points: []`` commit mints
# :Session + transcript :Source + AgentSession :Event and
# ``_count_resource("points")`` matches none of those labels, so that count
# never moves and its gate can never trip. Measured pre-fix: a free-tier key
# looping the endpoint with a fresh ``session_id`` minted unbudgeted nodes
# forever. The only per-key bound on the lane was 300 req/min
# (``RateLimitMiddleware.PATH_LIMITS``) — and a team plan allows 20 keys, so a
# PER-KEY bound multiplies with the number of keys the tenant can mint.
#
# WHY THE UNIT IS BYTES (and the floor is nodes). The unit the owner ruled for
# storage is MB/GB, not nodes (#4495/#5331: "we shouldn't measure 'per node'"),
# and the repo's own cost basis prices one node at ``bytes_per_node`` = 1024
# (``product/pricing.json`` ``cost_basis``; tied to that file by
# ``tests/test_commit_endpoint.py::test_the_node_floor_is_the_pricing_cost_basis``).
# So a request is charged ``max(payload bytes, nodes it will mint × 1024)`` — the
# payload it actually sent, never less than the nodes it will leave behind. That
# floor is what makes the ``points: []`` loop visible at all (its ~1 KB body
# mints ~4 KB of nodes), and the payload term is what stops the reverse bypass:
# ``summary`` / ``story_arc`` carry no field cap (Layer-1 caps content/quote
# only), so without it a commit of a few nodes and an 8 MiB summary would add
# megabytes for a few hundred bytes of budget.
#
# ⚠️ ``OVERRIDES:`` THE ALLOWANCE IS IN BYTES BUT ITS CHARGE CARRIES A
# NODE-COUNT FLOOR (nodes × 1024). #5331 rules the storage UNIT is bytes — "any
# node count × a per-node constant is not the model" — and this keeps the unit:
# the allowance, the comparison and the reported ``used``/``limit`` are all
# BYTES. The floor is a lower bound on the byte charge, not a re-denomination,
# and it exists because the payload alone under-counts exactly the shape #4051
# is about (a ~800-byte body that mints 4 KB of nodes). A lane that deletes it
# by citing #5331 re-opens the hole; the departure is recorded on #4051 itself
# (a comment carrying the same ``OVERRIDES:`` line), which is where a reader
# holding #5331 will look.
#
# WHY NOT ``nodes_written``. The PL4 metering figure (``plan.reconcile.
# net_new``) is points+entities+operators ONLY, so a payload whose ``events[]``
# / ``sources[]`` are large while its points all reconcile as merges meters 0
# while minting a node per entry — the budget would be bypassable by the very
# hole it bounds. The charge is therefore COMPUTED FROM THE PAYLOAD before the
# write (``commit_schema.commit_ingest_charge_bytes``), never read back from
# metering.
#
# WHY IT IS NOT A DECISION REVERSAL. It adds no session cap (#4010 stands), no
# retention/eviction window (#3853 stands), and no node-count ceiling and no
# change to ``max_points``' meaning (#4495/#5331 stand). The enforcement
# instrument is a transient 429 that self-heals in ≤60s — the instrument #5425
# rules for ("we should just have rate-limits and if they want more they need
# to speak with us"), and the same class the lane already answers on this path
# (``RateLimitMiddleware``'s 300/min/key). A throttle DELAYS a capture; it never
# refuses the customer's data.
#
# CALIBRATION (a tuning parameter, not a price): 10 MiB/min/org is roughly one
# free tier's whole node capacity — 10,000 nodes × 1024 B = 9.77 MiB, so the
# allowance is 2.40% larger than that capacity, per minute. An empty
# commit is charged its 4-node floor = 4,096 B (pinned by
# ``tests/test_commit_endpoint.py::test_the_charge_is_the_node_floor_for_a_
# small_payload``), so the allowance admits 2,560 of them per minute —
# 10,485,760 B / 60 s = 174,763 B/s sustained on the cheapest possible loop,
# and the same 10 MiB/min (14,400 MiB = 14.06 GiB/day) on payload-shaped
# traffic. That is well above a real client-side capture loop and far BELOW
# the ~24 MB/min a 20-key team could reach through the per-key buckets alone
# (6,000 req/min × 4,096 B = 24,576,000 B). Override with
# ``TORTOISE_COMMIT_INGEST_BYTES_PER_MIN`` when a real workload argues for a
# different number. A knob set BELOW the largest possible single request does
# not refuse that request for ever: ``commit_ingest_admit`` admits one
# over-sized request per EMPTY window (see its docstring), so the worst case is
# one commit per window — throttling, never an unbreakable retry loop.
#
# RESIDUAL (stated so this is not over-read): it bounds the RATE. A tenant
# writing at the allowed rate forever still grows without limit, and that is BY
# DECISION (retention unlimited, #4010/#3853). The TOTAL bound on the two
# session write lanes is #4051 §4 and turns on ``max_graph_nodes`` vs the
# recorded "storage is free" position — an owner ruling (#4495/#5331), not
# something this lane may take. #4051's own triage names this rate bound the
# only legitimate slice that needs no ruling.
#: The SHIPPED default allowance — the figure the public contract states
#: (``docs/INGEST_CONTRACT.md`` §9, ``docs/capacity.md`` §3: 10 MiB / 60 s / org).
#: Named separately from the resolved constant for ONE reason: the knob below is
#: read at import, and the contract ADVERTISES it — so an operator shell or CI
#: job that exports ``TORTOISE_COMMIT_INGEST_BYTES_PER_MIN`` must not red a test
#: that is asserting the default it never changed. The tests pin THIS constant
#: (so a silent retune of the documented figure still fails) and opt the
#: endpoint check onto it explicitly.
COMMIT_INGEST_DEFAULT_BYTES_PER_MIN = 10 * 1024 * 1024

MAX_COMMIT_INGEST_BYTES_PER_MIN_PER_ORG = max(1, int(
    os.environ.get("TORTOISE_COMMIT_INGEST_BYTES_PER_MIN", "") or
    COMMIT_INGEST_DEFAULT_BYTES_PER_MIN))

# ── Default limits ──────────────────────────────────────────────────────────
# max_points/max_api_keys have NO constant here — they resolve from
# tortoise.pricing.tier_limits (product/pricing.json) so a legacy org without
# stored limits gets pricing-correct caps, never the stale 1000/20 consts that
# contradicted pricing.json (#310 GAP-B, review fix 2).
#
# max_sessions has NO constant here and NO pricing.json field either. The
# flat 1000 was an INHERITED CODE FALLBACK, never a ratified product cap —
# and the reason is checkable, not reconstructed: the plan that carried it
# also designated its OWN canonical limits source, and that source has no
# session field at all. `product/pricing.json` (canonical single source,
# decision 1d: "product/pricing.json ... is canonical; pricing.md is
# doc-generated from it") contains ZERO occurrences of "session" and no
# sessions row in its tier table. What the plan recorded was KEEPING THE
# EXISTING FALLBACK — as a fallback: 05-plan.md:570, "keep flat fallbacks
# (1000/1000) in v1 OR fold into ops_allowance — decision: keep
# points/sessions flat in v1; ops_allowance (write ops) is the billing
# metric", restated at :598 ("points/sessions stay flat 1000/1000 in v1").
# Keeping a fallback is not ratifying the value, and 05-plan.md:18's "human
# gate #2 approved" reads in full "all 8 substeps, coherence CLEAN; human
# gate #2 approved 2026-08-07; decomposed into #568-#578" — it approved the
# plan's coherence to decompose, not a constant inside a tier table. The
# default pre-dates the #329 security commit (f6ca5ebdb), whose scoping doc
# only instructed preserving the existing resource in the shared helper
# (docs/plans/scoping-329-problem.md:17 — a refactor-safety instruction, not
# a cap ratification). It became a production ceiling because the limits
# resolvers substituted the constant as their fallback wherever a stored
# value was absent, and the lenient `if resource == "sessions"` branch in
# enforce_org_limit supplied it to callers whose limits dict lacked the key
# entirely (the MCP capture bridge).
#
# #4010 REMOVES that fallback (the 2026-09-19 correction on the issue
# withdraws the earlier "recorded v1 decision / REOPEN" framing). Sessions
# are UNLIMITED for every tier, and unlike every other limit a STORED
# max_sessions value is deliberately NOT honoured as a cap (see
# resolve_org_limits): a stored 1000 would otherwise keep the org capped
# after the constant was deleted. This removes an unratified fallback that
# should never have been enforcement; it is NOT a reopen of a v1 cap
# decision, because nothing that RATIFIES a cap ever named it — no owner
# ruling, no decision record, no `product/pricing.json` field. Approved docs
# do carry 1000 forward as a default (`git grep -n max_sessions -- '*.md'`);
# carrying a default forward is the inheritance this comment describes, not a
# ratification. The owner confirms he never approved a 1k cap. Stale
# 1000-as-cost-bound framing elsewhere: #4052. The P2-7 billing-metric
# decision is untouched — write-ops remains the billing metric.

# ── Documents cap: DERIVED-CONSTANT (T2-P2a, #1726 Slice 1) ────────────────
# max_documents is DERIVED from max_points with a documented conversion
# factor — deliberately NOT a pricing.json field (a pricing field would ripple
# through tier_limits/_REQUIRED_LIMIT_KEYS and every tier's resolved limits;
# the issue pins the derived-constant option to avoid that KeyError surface).
# Rationale for 10×: docs are content nodes WITHOUT claim extraction or EP
# churn (Sources only, #1726 slice 1) — an order of magnitude cheaper than
# points; the gate still bounds runaway corpus growth.
_DOCUMENTS_FROM_POINTS_FACTOR = 10

_RESOURCE_LIMIT_KEYS = {
    "points": "max_points",
    "api_keys": "max_api_keys",
    # #4010: the key is still carried so the resolved dict holds an EXPLICIT
    # None for sessions ("present but unlimited") rather than a missing key
    # (which is fail-closed). No constant ever supplies a value for it.
    "sessions": "max_sessions",
    "users": "max_users",
    "graphs": "max_graphs",
}


# #4614: the machine-readable CATEGORY of a quota refusal — the one value a
# caller branches on. The message text is for humans only; nothing may key on
# it (the clients ship independently of the server's wording).
QUOTA_REFUSAL_CODE = "quota_exceeded"


#: #5425 owner ruling (2026-10-08): "we should just have rate-limits and if they
#: want more they need to speak with us". The route for a customer at a ceiling
#: is a CONVERSATION, so every refusal that tells them about a limit must also
#: tell them they can talk to us. It lives HERE, in the leaf module both the REST
#: layer and the quota gate import, because `quota` is a leaf by contract
#: (``quota must not import hosted_api``) — while the constant living in
#: ``hosted_api`` is precisely why the shared gate below was missed (#5425
#: review round 1). ONE constant, so the surfaces cannot drift apart (#1611).
LIMIT_CONTACT = " Need more? Contact support@premiselabs.co."


def with_limit_contact(message: str) -> str:
    """Terminate ``message`` as a sentence and append the #5425 contact path.

    The seam exists because appending a fragment by hand gets the PUNCTUATION
    wrong: a message that does not end a sentence renders as
    ``"...upgrade to invite more Need more? Contact support@premiselabs.co."``
    — malformed customer-facing prose that the source guard cannot see, because
    the guard can only check that the constant was *mentioned*. 15 of the 22
    call sites were in exactly that state. Making the join impossible to get
    wrong by hand is cheaper than fixing 15 strings and hoping the 16th is not
    added by hand later.
    """
    text = (message or "").rstrip()
    if text and text[-1] not in ".!?":
        text += "."
    # NB: the one place in this module that must NOT route through the seam it
    # defines — the source-rewrite that introduced the seam rewrote it into a
    # recursive call, which is exactly why the guard below asserts the seam is
    # a terminal append and not a self-call.
    return text + LIMIT_CONTACT


class RefusalPayload(dict):
    """The structured 402 ``detail`` — a dict that STRINGIFIES to its message.

    #4614: the payload must survive as a structured object (REST returns it as
    the 402 ``detail`` and callers branch on ``code``), but the MCP capture twin
    reads ``getattr(e, "detail", ...)`` and stringifies it. It cannot be fixed
    there: the change would land inside a registered tool's handler and red
    ``surface-guard`` (CONTRIBUTING: *"Add response fields in the SDK or
    assembly layer, not inside a tool function"*). Answering the human message
    from ``__str__`` fixes every ``str(detail)`` consumer at the assembly layer
    instead, while ``json.dumps`` (and therefore FastAPI) still serializes it as
    a JSON object.
    """

    def __str__(self) -> str:
        message = self.get("message")
        return message if isinstance(message, str) else super().__str__()


class QuotaExceededError(Exception):
    """Org is at/over its resource limit — the write must be rejected (402).

    #4614: the refusal carries the STRUCTURED facts of the refusal —
    ``resource``, ``used``, ``limit``, and (for the capture points gate)
    ``estimate`` — because a refusal that exists only as prose is not a
    distinguishable state: every consumer is pushed onto matching the message
    text, and our own clients are documented as forbidden from doing exactly
    that (``capture_spool.classify_failure``: *"a capacity/billing refusal is
    a category, not a string"*). ``quota_refusal_payload`` turns these fields
    into the house structured-detail shape (#2789, `_one_free_org_detail`);
    the prose survives as that payload's ``message``, so a human reader loses
    nothing.

    Every field is optional and keyword-only: a raise site that knows only its
    message (``QuotaExceededError("…")``) still works and yields a payload
    without numbers — the dashboard already falls back to ``/v1/team``'s
    allowance when a refusal carries none. Do NOT populate a field by
    re-counting: the values must be the ones THIS gate compared.

    ``code`` lives on the EXCEPTION rather than at each raise site, so a
    generic ``except QuotaExceededError: payload(e)`` cannot mislabel a
    subclass's refusal as the base category — ``CohortCostCapExceeded``
    overrides it, and a spend-cap refusal must not read as a plan-limit one
    (the same one-contract rule the payload exists to enforce).
    """

    def __init__(self, message: str, *, resource: str | None = None,
                 used: int | None = None, limit: int | None = None,
                 estimate: int | None = None,
                 code: str = QUOTA_REFUSAL_CODE) -> None:
        super().__init__(message)
        self.resource = resource
        self.used = used
        self.limit = limit
        self.estimate = estimate
        self.code = code


class QuotaCheckError(Exception):
    """Quota counting/config failed — fail closed (500/503), never pass."""


def quota_refusal_payload(exc: QuotaExceededError) -> dict:
    """The structured 402 ``detail`` for a quota refusal (#4614).

    House shape — a dict whose ``code`` drives client branching and whose
    ``message`` is the human sentence — matching ``_one_free_org_detail``
    (#2789) and the ``SUSPENDED`` detail (#308 R5). The quota refusal was the
    surviving instance of the contract #2789 abandoned: a bare string, so
    every consumer had to prose-match (`website/apps/dashboard/src/
    upsellGate.js` records that workaround).

    Only fields the raise site actually knew are emitted: a fabricated
    ``used``/``limit`` would be worse than an absent one. The ``code`` is read
    off the exception (never assumed here) so a subclass's category survives a
    generic caller.
    """
    payload: dict = RefusalPayload({
        "code": getattr(exc, "code", None) or QUOTA_REFUSAL_CODE,
        "message": str(exc),
    })
    for key in ("resource", "used", "limit", "estimate"):
        value = getattr(exc, key, None)
        if value is not None:
            payload[key] = value
    return payload


def derived_tier(org_row: dict) -> str:
    """Effective tier for an org row — the #1082 anon-ceiling derivation.

    A zero-email (anonymous) org whose owner membership has NO linked
    user_id runs the reduced ``anon`` tier until it is claimed (#1082 PR1
    links the verified OAuth identity to the owner row; claim raises the
    org to ``free``). The predicate is membership-based
    (``is_anon_org``) — NEVER the ``teams.email IS NULL`` proxy, which
    misclassifies reg- orgs (email set at mint, owner user_id still NULL)
    and legacy real-user rows.

    Registry mode: NO-OP (raw tier) — the anon ceiling is Supabase-mode
    only in v1 (selfhost is operator-controlled, not a farm surface).

    NOTE: distinct from billing.effective_tier(org, now) (grace-period
    logic, billing.py:382) — do not merge.

    Args:
        org_row: the orgs row / Org dict (must carry ``tier`` and,
            for Supabase mode, ``id``).
    Returns:
        The effective tier string.
    """
    tier = org_row.get("tier") or "free"
    org_id = org_row.get("id") or org_row.get("org_id")
    if not org_id:
        return tier
    try:
        from tortoise.supabase_control import (  # noqa: I001
            get_control_plane, is_anon_org, is_supabase_enabled,
        )
        if not is_supabase_enabled():
            return tier  # registry mode: no-op
        cp = get_control_plane()
        if tier == "free" and is_anon_org(cp, org_id):
            return "anon"
        return tier
    except Exception:
        # Fail-open to the stored tier on control-plane read errors — the
        # anon ceiling is a protection posture, never a reason to 500 the
        # auth path.
        return tier


def resolve_org_limits(org_id: str) -> dict:
    """Resolve an org's limits from the control plane.

    Supabase mode (post-#669 flip): the orgs row via the service-role
    seam — the registry is DELETED and querying it would auto-recreate the
    empty graph (post-flip verification finding, #669). Registry mode: the
    Org node, as before.

    Missing Org → QuotaCheckError (fail-closed; the auth layer should
    guarantee key→org mapping).

    CONTRACT (the return shape every caller must honour, #310 GAP-B / #4010):
    the returned dict carries EVERY value of ``_RESOURCE_LIMIT_KEYS``. A key
    that is PRESENT with value ``None`` means UNLIMITED (sessions is always
    this, for every tier — #4010); a MISSING key is fail-closed in
    ``enforce_org_limit`` (``QuotaCheckError`` → HTTP 500) for every resource
    EXCEPT the derived ``documents`` cap, whose branch reads ``max_points``
    through ``limits.get`` and therefore SKIPS an absent key (an absent key and
    an explicit ``None`` are indistinguishable there — this resolver always
    emits the key, so the case is unreachable from here; #4051).
    A missing ATTRIBUTE on the row/column is resolved to a value here — it
    never leaves the key absent. (The pre-#4010 rule of the same shape was
    "missing attributes → defaults"; sessions has no default any more.)
    """
    if not org_id:
        raise QuotaCheckError("resolve_org_limits requires a org_id")
    from tortoise.supabase_control import (  # noqa: I001
        _QUOTA_SELECT,
        _ORG_ADDITIVE_0015_TIER,
        _ORG_ADDITIVE_2040_TIER,
        _ORG_ADDITIVE_BILLING_TIER,
        _ORG_ADDITIVE_DKL_TIER,
        _ORG_ADDITIVE_IMPORT_TIER,
        _orgs_row_fail_soft,
        get_control_plane, is_supabase_enabled,
    )
    if is_supabase_enabled():
        # #1859 P3-2 review (P2): route through the #1096 fail-soft seam —
        # the max_points column (20260817000001) is ADDITIVE; a direct
        # select 400s on a schema one migration behind, which would turn a
        # degrade-to-tier-defaults into a hard failure. Same additive
        # ladder as resolve_api_key / _session_user_org, newest migration
        # dropped FIRST — incl. the #2040 marker tier so a schema missing
        # only the marker column degrades just the marker (quota keeps
        # reading max_points; a missing tier would 400 every rung down to
        # the terminal base-only read → hard failure on drift, the exact
        # #1832 class).
        row = _orgs_row_fail_soft(
            get_control_plane(), org_id, select=_QUOTA_SELECT,
            additive_tiers=[_ORG_ADDITIVE_2040_TIER,
                            _ORG_ADDITIVE_IMPORT_TIER,
                            _ORG_ADDITIVE_DKL_TIER,
                            _ORG_ADDITIVE_0015_TIER,
                            _ORG_ADDITIVE_BILLING_TIER],
        )
        if row is None:
            raise QuotaCheckError(f"Team {org_id!r} not found in control plane")
        # #1082 PR2: the anon ceiling is tier-DERIVED at resolution — an
        # unclaimed zero-email org (owner user_id NULL) resolves to the
        # reduced ``anon`` tier until claimed, then lifts to free. The row
        # needs its ``id`` for the is_anon_org predicate (already have
        # org_id).
        row = {**row, "id": org_id, "tier": derived_tier({**row, "id": org_id})}
        tier = row.get("tier") or "free"
        from tortoise.pricing import tier_limits
        lim = tier_limits(tier)
        # #1082 PR2: when the derived tier is anon, the STORED quota columns
        # (minted at free values by agent_signup/register) are overridden
        # read-time with the reduced anon caps — an unclaimed zero-email
        # org must never bind at free limits (indicator 4).
        anon_override = tier == "anon"
        # Mirror the registry shape EXACTLY (review P2, PR #911): NULL
        # max_users/max_graphs = UNLIMITED (Team tier) — preserve None,
        # never substitute pricing defaults (enforce_org_limit treats an
        # explicit None limit as unlimited; substituting finite caps would
        # hard-cap legacy/migrated rows). max_points override (GAP-B,
        # 20260817000001) takes precedence over graph_size_cap (the
        # fallback), then pricing; max_api_keys falls back to pricing.
        # #4010: max_sessions is UNLIMITED for every tier — no pricing field,
        # no constant, and (deliberately) no stored value honoured as a cap.
        # The Supabase orgs row has no max_sessions column at all, so there is
        # nothing to read even if we wanted to.
        mu = row.get("max_users")
        mg = row.get("max_graphs")
        # #1859 P3-2: max_points column (points-cap override, migration
        # 20260817000001) takes precedence; graph_size_cap is the legacy
        # fallback (GAP-B); pricing last — mirrors import_org.
        mp = row.get("max_points")
        if mp is None:
            mp = row.get("graph_size_cap")
        return {
            "org_id": org_id, "tier": tier,
            "max_users": (lim["max_users_per_team"] if anon_override
                           else (int(mu) if mu is not None else None)),
            "max_graphs": (lim["max_graphs_per_team"] if anon_override
                            else (int(mg) if mg is not None else None)),
            "max_points": (int(lim["max_graph_nodes"]) if anon_override
                            else (int(mp) if mp is not None else lim["max_graph_nodes"])),
            "max_api_keys": lim["max_api_keys"],
            "max_sessions": None,
        }
    reg = _make_sdk(namespace="registry")
    rows = reg._get_registry().query(
        "MATCH (t:Team {id:$id}) "
        "RETURN t.tier, t.max_users, t.max_graphs, "
        "t.max_points, t.max_api_keys, t.max_sessions",
        params={"id": org_id},
    ).result_set
    if not rows:
        raise QuotaCheckError(f"Team {org_id!r} not found in registry")
    tier, mu, mg, mp, mak, _ms = rows[0]
    tier = tier or "free"
    from tortoise.pricing import tier_limits
    lim = tier_limits(tier)
    return {
        "org_id": org_id,
        "tier": tier,
        # max_users/max_graphs: None means unlimited (Team tier); preserve it.
        "max_users": int(mu) if mu is not None else None,
        "max_graphs": int(mg) if mg is not None else None,
        "max_points": int(mp) if mp is not None else lim["max_graph_nodes"],
        "max_api_keys": int(mak) if mak is not None else lim["max_api_keys"],
        # #4010: sessions are unlimited for every tier — the flat 1000 was
        # an inherited code fallback, never a ratified cap (see the module
        # comment above). `_ms` (the stored t.max_sessions) is read so the
        # removal is VISIBLE at the exact site that could re-introduce the
        # cap — and then NOT honoured, because a stored 1000 must never
        # re-cap an org (the trap this issue names). Clearing the stored rows
        # is the defence-in-depth half; ignoring them here is the half that
        # actually decides.
        "max_sessions": None,
    }


def api_key_occupies_slot(org_id: str, key_id: str, sdk=None) -> bool:
    """#4355: True iff `key_id` is a row that ``_count_resource(org_id,
    'api_keys')`` counts RIGHT NOW — i.e. it currently occupies exactly one
    ``max_api_keys`` slot.

    This is NOT a fourth count. It is the api_keys cap predicate applied to
    ONE id, and it exists for exactly one caller: the replacement-aware rotate
    primitive, which may credit the slot it is about to release only when the
    displaced row is one the cap actually charged. Without this proof a
    revoked / expired / bootstrap row id would buy a free slot (the count
    never held it) and rotate would become a cap hole.

    The two lanes read through the SAME sources ``_count_resource`` uses —
    Supabase: ``active_api_keys`` (the shared live-set reader) minus the
    ``created_via='bootstrap'`` exclusion; registry: the same WHERE clause the
    registry count carries — so the two can never disagree about what is LIVE
    or about what is cap-exempt. ``tests/test_quota.py`` pins the parity
    (the count equals the number of rows this predicate accepts).
    """
    if not org_id or not key_id:
        return False
    from tortoise.supabase_control import (  # noqa: I001
        active_api_keys, get_control_plane, is_supabase_enabled,
    )
    if is_supabase_enabled():
        cp = get_control_plane()
        return any(
            r.get("id") == key_id and r.get("created_via") != "bootstrap"
            for r in active_api_keys(cp, org_id)
        )
    reg = (sdk if sdk is not None and getattr(sdk, "_namespace", None) == "registry"
           else _make_sdk(namespace="registry"))
    rows = reg._get_registry().query(
        "MATCH (k:APIKey {org_id: $tid, id: $kid}) "
        "WHERE k.revoked_at IS NULL "
        "AND (k.created_via IS NULL OR k.created_via <> 'bootstrap') "
        "AND (k.expires_at IS NULL OR k.expires_at > $now) RETURN k.id",
        params={"tid": org_id, "kid": key_id,
                "now": datetime.now(UTC).isoformat()},
    ).result_set
    return bool(rows)


#: #5140: THE definition of "this `:Source` is a metered document" — the
#: `documents` resource's predicate (#1726 slice 1, re-pointed by D10).
#: `count_org_usage` counts with it, and the WRITE-side guards that refuse a
#: caller demotion out of this class test with :func:`is_metered_document_kind`: the two `:Source`
#: surfaces (`sdk._refuse_document_kind_demotion`) read the stored genre through
#: :func:`stored_source_document_kinds`, and the document writers
#: (`EventAPI.add_document`, `sdk._doc_write`) through
#: :func:`refuse_document_writer_demotion`. They are halves of one rule and must
#: be edited together — the escape in #5140 existed because the predicate had a
#: single consumer (the counter) and no defender.
DOCUMENT_METER_PREDICATE = (
    "s.documentKind IS NOT NULL AND s.documentKind <> 'transcript'"
)


def is_metered_document_kind(kind: str | None) -> bool:
    """Python half of :data:`DOCUMENT_METER_PREDICATE`.

    A `:Source` counts against the `documents` cap iff its `documentKind` is
    non-NULL and is not the `transcript` genre. ``None`` is NOT a document
    (no COALESCE-to-empty: that would meter every session/connector/provenance
    Source — the #1726 price change D10 forbids).
    """
    return kind is not None and kind != "transcript"


#: The ONE genre :data:`DOCUMENT_METER_PREDICATE` exempts. Named because the
#: WRITE side needs it too: it is the only value that moves a node out of the
#: metered class, so every defender gates on it rather than on a literal.
DOCUMENT_KIND_TRANSCRIPT = "transcript"


def stored_source_document_kinds(g, id_val: str):
    """The `documentKind`s the `:Source` nodes a write for ``id_val`` would hit.

    #5140: the meter's KEY has to be readable at every door that writes it,
    and read with the SAME key discipline as the write — the primary identity
    prop then the declared secondary (`_source_reachable_by`'s rule, #5196) —
    or a url-only STUB hides its stored genre from the guard while staying
    writable. That discipline lives in `tortoise.projection` (the writers' own
    declaration), so it is imported FUNCTION-LEVEL: `quota` stays a leaf
    module (stdlib-only at import time) and every door — `sdk.create_source`,
    `sdk._doc_write`, `EventAPI.add_document` — reads the genre the same way.

    Returns the LIST of stored kinds, because the write this guards is
    ``MATCH (n:Source {key:$id}) SET n += $p`` — it applies to EVERY node the
    key matches, not to one. An earlier revision returned ``rows[0][0]`` (the
    first row of the first matching key) and the guard therefore answered about
    a node the write might not be the relevant one: with two `:Source` nodes
    sharing the key and the non-metered one sorting first, the guard read
    ``None``, admitted the write, and the `SET` demoted the metered sibling out
    of the cap (#5140 review cycle 4 — reproduced, meter 1 -> 0). Reading ALL
    rows and refusing if ANY is metered makes the guard fail closed on the whole
    reachable set; the empty list means "no such `:Source` — nothing stored".
    """
    from tortoise.projection import _ENTITY_ID_PROP, secondary_entity_id_props

    keys = (_ENTITY_ID_PROP.get("Source", "id"),
            *secondary_entity_id_props("Source"))
    kinds: list = []
    for key in keys:
        rows = g.query(
            f"MATCH (n:Source {{{key}:$id}}) RETURN n.documentKind",
            params={"id": id_val},
        ).result_set
        # ⛔ NO early return: keep reading every key's rows (see the docstring).
        kinds.extend(r[0] for r in rows)
    return kinds


def refuse_document_writer_demotion(g, doc_id: str, document_kind,
                                    *, surface: str) -> None:
    """#5140 P1 (review cycle 3): the DOCUMENT writers refuse a demotion LOUDLY.

    THE RULE. `documentKind` is the meter key (`DOCUMENT_METER_PREDICATE`), so
    the two `DocumentCreated` producers that take the genre from a TENANT —
    `EventAPI.add_document(document_kind=...)` and
    `sdk._doc_write(frontmatter type=..., <doc id>)` (the `/v1/index/docs`
    re-index path) — may not move an already-metered document out of the
    counted class. Both merge a caller-chosen/derived `:Source` url, so both
    could demote a document one call at a time (MEASURED, 3 seeded documents:
    meter 3 -> 2 -> 1) and free a `documents` slot per call.

    WHY IT IS HERE AND NOT ONLY IN THE FOLD. The fold
    (`entities.py::_upsert_document`) DROPS such a demotion inside its own
    CASE clause, because that same statement is the journal REPLAY fold and a
    rebuild must stay total — a raise there would abort `rebuild_all` on a log
    that already holds a pre-guard record. Producers are NOT replay, so they
    raise: a silent no-op would leave the caller believing it wrote (the
    `raw_state`/`rawState` precedent `sdk._refuse_document_kind_demotion`
    cites), and `/v1/index/docs` would report a document as re-indexed while
    the graph kept its previous genre.

    THE GATE IS THE VALUE, not a query: `transcript` is the only genre the
    predicate exempts, so almost every document write returns here without
    touching the graph (the index path writes one document per file).

    NOT refused, deliberately: SETTING a genre where none is stored (that is
    how a document is minted — the caller-controlled `transcript` MINT is the
    remaining door recorded on #5140, where the genre vocabulary is the
    product decision), a metered -> metered retarget (`report` -> `brief`,
    both counted), and a write that repeats the genre already stored.
    """
    if document_kind != DOCUMENT_KIND_TRANSCRIPT:
        return
    stored_kinds = stored_source_document_kinds(g, doc_id)
    metered = [k for k in stored_kinds if is_metered_document_kind(k)]
    if not metered:
        return
    raise ValueError(
        f"`document_kind='transcript'` would move the document {doc_id!r} out "
        f"of the metered `documents` set (stored documentKind={metered[0]!r}; "
        f"the `documents` cap counts `{DOCUMENT_METER_PREDICATE}`). The genre "
        f"of an existing document is not caller-writable in the demotion "
        f"direction through `{surface}` (#5140 — metering a node a caller can "
        f"un-meter is not a cap). Delete the document and re-ingest it if it "
        f"really is a transcript; to record that a source became unavailable, "
        f"use `create_source(..., raw_state=...)`."
    )


def count_org_usage(org_id: str, resource: str, sdk=None) -> int:
    """Count current usage for a resource. Raises QuotaCheckError on failure.

    Public so callers needing the raw count (e.g. extraction-aware estimates)
    can use it without duplicating the fail-closed handling.

    Supported resources: points, api_keys, sessions, users, graphs,
    documents (#1726: the document-bearing :Source count with the transcript
    discriminator — D10, ONTOLOGY v3.15 §4.4).
    ``points`` counts non-episodic Points + Object/Subject nodes (#1911) +
    non-episodic Event nodes (#1977).
    """
    return _count_resource(org_id, resource, sdk=sdk)


def _count_resource(org_id: str, resource: str, sdk=None) -> int:
    """Count current usage for a resource. Raises QuotaCheckError on failure.

    Supported resources:
    - points: non-episodic Points in tenant graph (the Point-level
      ``is_episodic`` flag is the quota discriminator — #947, epic #909
      §4.4; legacy Points without the flag count as non-episodic,
      fail-closed, until graph-scripts/backfill_is_episodic.py backfills
      them, R-18) PLUS all Object/Subject nodes (#1911 — the /v1/objects
      + /v1/subjects gates check this resource, and Object/Subject carry
      only their own labels, so they must be counted here or the cap is
      vacuous for those writes) PLUS non-episodic Event nodes (#1977 — the
      SAME vacuity on `tortoise_create_event`; the SAME fail-closed flag rule,
      so capture-minted Events stamped ``is_episodic: true`` stay excluded)
    - api_keys: LIVE (non-revoked, non-expired) API keys that COUNT against
      max_api_keys. A bootstrap (24h session) credential is cap-EXEMPT
      (R13/#4140) and excluded; a NULL/legacy ``created_via`` is a DURABLE
      row and COUNTS (fail-closed). In registry (Cypher) and Supabase.
    - sessions: Session nodes in tenant graph (MATCH (s:Session) — NOT the
      all-nodes count; #947 P0)
    - users: active Membership nodes in registry
    - graphs: Graph nodes in registry
    - documents (#1726): document-bearing :Source nodes in the tenant graph
      with the discriminator ``documentKind IS NOT NULL AND documentKind
      != 'transcript'``. D10 (ONTOLOGY v3.15 §4.4): a document is a :Source,
      so the cap is RE-POINTED at :Source — never retired (that would ungate
      /v1/index/docs) and never folded into the node cap (that would silently
      meter ~2,193 non-document sources). A NULL documentKind is NOT a
      document (no COALESCE-to-empty: that would meter every session/connector/
      provenance Source, the exact #1726 price change D10 forbids); session
      transcripts (documentKind='transcript', the /v1/sessions commit MERGE at
      hosted_api.py) are EXCLUDED so a captured session never consumes the docs
      gate.
    """
    try:
        # ── Registry-scoped counts (api_keys, users, graphs) ──
        if resource in ("api_keys", "users", "graphs"):
            # #765 (plan Task 8 quota paths): in Supabase control-plane mode
            # the count reads Supabase via the seam — post-flip the registry
            # is DELETED, so a registry count would fail-open (0 nodes) or
            # 500. Mirrors the registry predicates exactly:
            #   api_keys: revoked_at IS NULL AND not expired (#2426/#2481)
            #             AND not created_via='bootstrap' (#4140/R13 —
            #             a REVOKED row is an audit tombstone (retained for
            #             audit + swept later, #685) that must NEVER consume
            #             the plan's max_api_keys budget; an expired-but-
            #             unrevoked key must never wedge an org at its cap;
            #             the auth layer already refuses both, so counting
            #             them would hold a slot for a dead credential.
            #             Pre-#2426 durable keys never carried expiry. The
            #             expiry filter (expires_at IS NULL OR > now) mirrors
            #             the bootstrap cap queries' own predicate.
            #             #4140 (R13): a bootstrap (24h session) key is
            #             cap-EXEMPT — this count was the ONE outlier that
            #             omitted the exclusion every recovery-mint lane
            #             already carries (hosted_api.session_key both lanes,
            #             sdk.signup_token_recover, recover_team_key), so a
            #             session credential silently burned a paid durable
            #             slot. The exclusion is NULL-TOLERANT: a NULL/legacy
            #             created_via is a DURABLE row and COUNTS
            #             (fail-closed). #2481 audit: this predicate is the
            #             ONE count shared by the standalone max_api_keys
            #             mint gates (hosted_api._mint_key for POST
            #             /v1/team/keys + per-graph key mints, REST
            #             _check_org_limit / enforce_org_limit — MCP carries
            #             no api_keys gate).),
            #   users:    status IS NULL OR status = 'active'
            #   graphs:   the default graph derived from organizations.graph_name
            #             PLUS custom graph rows from the ``graphs`` table
            #             (20260901000001, C1 #2110) — custom ACTIVE graphs
            #             count toward max_graphs; deleted rows excluded.
            #             Pre-C1 schema (no graphs table) degrades to
            #             default-only via the graph_metadata drift path.
            #             NOTE: this means the EXISTING create_graph limit
            #             gate now counts custom graphs in Supabase mode —
            #             enforcement semantics shift the moment C2's
            #             provisioning INSERT lands (review P2, recorded).
            # Selfhost (registry mode) keeps the registry count.
            from tortoise.supabase_control import (  # noqa: I001
                active_api_keys, get_control_plane, graph_metadata,
                is_supabase_enabled,
            )
            if is_supabase_enabled():
                cp = get_control_plane()
                if resource == "api_keys":
                    # #4140 (R13): bootstrap (24h session) rows are
                    # cap-EXEMPT — the SAME predicate the recovery-mint lane
                    # applies (hosted_api._session_key_supabase). Liveness
                    # (non-revoked + non-expired) comes from the shared
                    # active_api_keys() reader, so this count and the
                    # recovery lane can never disagree on what is LIVE.
                    # The bootstrap exclusion is applied in PYTHON, never as
                    # a PostgREST `created_via=neq.bootstrap` filter: SQL
                    # `<>` drops NULL rows, and a legacy row with a NULL
                    # created_via is DURABLE and must still count
                    # (fail-closed — #4140 adversarial T4).
                    return len([r for r in active_api_keys(cp, org_id)
                                if r.get("created_via") != "bootstrap"])
                if resource == "users":
                    rows = cp.query(
                        "org_memberships", select=["status"],
                        filters=[("org_id", "eq", org_id)],
                    )
                    return len([r for r in rows
                                if r.get("status") in (None, "active")])
                # graphs: default graph exists whenever the org row does
                return len(graph_metadata(cp, org_id))
            reg = (sdk if sdk is not None and getattr(sdk, "_namespace", None) == "registry"
                   else _make_sdk(namespace="registry"))
            if resource == "api_keys":
                # #2426: expiry filter mirrors the supabase lane — expired
                # durable keys never count against max_api_keys.
                # #4140 (R13): bootstrap (24h session) rows are cap-EXEMPT
                # — the SAME predicate the recovery-mint lane uses
                # (hosted_api.session_key, registry lane). NULL created_via
                # (legacy selfhost) COUNTS: Cypher `NULL <> 'bootstrap'` is
                # NULL, so the explicit IS NULL arm is required (this is the
                # over-exemption direction the cap must fail closed on).
                rows = reg._get_registry().query(
                    "MATCH (k:APIKey {org_id: $tid}) WHERE k.revoked_at IS NULL "
                    "AND (k.created_via IS NULL OR k.created_via <> 'bootstrap') "
                    "AND (k.expires_at IS NULL OR k.expires_at > $now) RETURN count(k)",
                    params={"tid": org_id, "now": datetime.now(UTC).isoformat()},
                ).result_set
            elif resource == "users":
                rows = reg._get_registry().query(
                    "MATCH (m:Membership {org_id: $tid}) "
                    "WHERE m.status IS NULL OR m.status = 'active' RETURN count(m)",
                    params={"tid": org_id},
                ).result_set
            else:  # graphs
                rows = reg._get_registry().query(
                    "MATCH (g:Graph {org_id: $tid}) "
                    "WHERE g.status IS NULL OR g.status <> 'deleted' "
                    "RETURN count(g)",
                    params={"tid": org_id},
                ).result_set
            return int(rows[0][0])

        # ── Tenant-graph-scoped counts (sessions, points) ──
        if sdk is None:
            sdk = _make_sdk(namespace=org_id)
        if resource == "documents":
            # #1726 Slice 1, re-pointed by D10 (ONTOLOGY v3.15 §4.4): the
            # documents resource counts document-bearing :Source nodes with
            # the transcript discriminator (T2-P2a). Only a non-NULL
            # documentKind is a document — a session/connector/provenance
            # Source has no documentKind and must NOT be metered (that is the
            # #1726 price change D10 forbids). Session transcripts
            # (documentKind='transcript', hosted_api.py commit MERGE) are
            # excluded so capture never consumes the docs gate (the gate fires
            # on /v1/index/docs ONLY).
            rows = sdk._get_proj().g.query(
                "MATCH (s:Source) "
                f"WHERE {DOCUMENT_METER_PREDICATE} "
                "RETURN count(s)",
            ).result_set
            return int(rows[0][0])
        if resource == "sessions":
            # #947 (epic #909 §4.4, W-4): the P0 — count Session nodes, NOT
            # all nodes. Pre-fix this fell through to MATCH (n) (~25 nodes
            # per captured session) → false 402 after ~40 captures.
            rows = sdk._get_proj().g.query(
                "MATCH (s:Session) RETURN count(s)",
            ).result_set
            return int(rows[0][0])
        # points: non-episodic Points PLUS Object/Subject nodes (#1911). The
        # Point-level is_episodic flag is the Point discriminator — a MISSING
        # flag counts as non-episodic (fail-closed, R-18): legacy regex-path
        # captures lack it and must be backfilled episodic by
        # graph-scripts/backfill_is_episodic.py, else false 402s persist for
        # existing capture users. Object/Subject nodes are counted
        # UNCONDITIONALLY: they carry only their own labels (projection/
        # entities.py _upsert_object/_upsert_subject) and previously NEVER
        # appeared in this count — the /v1/objects + /v1/subjects gates
        # (hosted_api._check_org_limit resource="points") and the MCP
        # create_object/create_subject tools (_quota_gated "points") checked
        # a count that could only ever see Points, so a free org could write
        # unbounded objects/subjects without ever 402ing (bug-hunt 2026-08-28
        # server P2-1, #1911). max_points IS the pricing max_graph_nodes node
        # cap (tortoise.pricing tier_limits) — counting Object+Subject
        # against it applies the plan's real node cap, not a Point-only cap.
        # #1844 interplay (recorded intent): the GitHub indexer is an
        # UNGATED object writer (hosted_api.py:11097 — no points-quota
        # preflight, by design). Its minted Object nodes now count against
        # the cap, so an indexing-heavy org can be pushed past max_points,
        # after which all points-gated writes 402 until upgrade — the
        # intended "0 uncapped object/subject growth" posture. A future
        # indexer preflight (follow-up) would gate the job itself.
        #
        # #1977 (code-review gate on PR #1974): the SAME vacuity recurred on the
        # Event label — `tortoise_create_event` is `_quota_gated("points")`
        # (mcp_server.py), but a DIRECT call defaults to `is_episodic=None` and
        # mints a flag-less Event this query never saw, so a free org could mint
        # unbounded Events without ever 402ing (the gate was decoration — its
        # failure mode was indistinguishable from its success mode). Events are
        # graph NODES and ``max_points`` IS the pricing ``max_graph_nodes`` cap
        # (the recorded GAP-B / MAIN-DELTA 2 decision,
        # docs/plans/2026-08-08-310-stripe-billing.md — "the points counter
        # counts graph nodes"), and #1911 resolved the identical finding for
        # Object/Subject by ADDING the missing label rather than dropping the
        # gate — dropping it here would re-open the unbounded-growth hole for
        # one more writer. So the Event branch carries the SAME fail-closed
        # R-18 predicate as Point: a MISSING flag counts as non-episodic.
        #
        # Events reaching this branch carry ``is_episodic`` UNSET, which is
        # why they ARE charged. Only two writers stamp it true: the
        # ``sessionCaptured`` Event minted by the hosted capture path (the
        # SDK ``capture_session`` writer was deleted by #4282; both used to
        # pass ``is_episodic=True`` to ``create_event``), and the hosted
        # commit path, which sets
        # ``e.is_episodic=true`` on its AgentSession Event and on its
        # extracted occurrence Events. Those stay EXCLUDED — that exclusion is
        # what keeps this change from re-charging the capture users the #947
        # backfill exists for. It is NOT a kind allowlist: the discriminator
        # stays the ``is_episodic`` flag, exactly as for Points. Conversely,
        # ``_session_event_write`` (the session index path — its docstring
        # pins "no is_episodic on the index path") and a direct
        # ``create_event`` without the explicit kwarg (``tortoise_create_event``)
        # leave the flag unset and their Events therefore COUNT.
        rows = sdk._get_proj().g.query(
            "MATCH (n) "
            "WHERE (n:Point AND (n.is_episodic IS NULL OR n.is_episodic = false)) "
            "   OR n:Object OR n:Subject "
            "   OR (n:Event AND (n.is_episodic IS NULL OR n.is_episodic = false)) "
            "RETURN count(n)",
        ).result_set
        return int(rows[0][0])
    except QuotaCheckError:
        raise
    except Exception as e:
        from .security import redact_error
        redacted = redact_error(e)
        _logger.error(
            "quota count failed (fail-closed): team=%s resource=%s error=%s",
            org_id, resource, redacted,
        )
        raise QuotaCheckError(f"quota count failed for {resource}: {redacted}") from e


def enforce_org_limit(limits: dict | None, resource: str, sdk=None, *,
                      slot_credit: int = 0) -> None:
    """Reject a write when the org is at/over its resource limit.

    Args:
        limits: resolved org limits dict (from resolve_org_limits or the
            authenticated caller). None → skip (stdio/operator, no org).
            A key that is PRESENT and None means UNLIMITED (skip); a MISSING
            key is fail-closed (QuotaCheckError) — build the dict from
            _RESOURCE_LIMIT_KEYS (#310 GAP-B / #4010). ⚠️ The one resource that
            does NOT fail closed on a missing key is `documents`: its cap is
            DERIVED from `max_points` by the branch below, which reads it with
            `limits.get` and so cannot tell an absent key from an explicit
            None — both skip. That branch is reached only from
            `resolve_org_limits`, which always emits the key (#4051).
            A value that cannot be READ as a number is fail-closed, names the
            field, and is logged at ERROR (#4051) — a limit nobody can read is
            never a pass. A value that IS readable is COERCED with `int()` and
            then ENFORCED, so only an unreadable one is refused; the three
            shapes differ and the difference is stated here because it is the
            half a future reader would otherwise have to measure (#4051 review
            rounds 5 and 6): an INTEGER-FORMATTED string is honoured
            (`"1000"` → 1000), a float VALUE truncates (`10.9` → 10), and a
            float-FORMATTED string (`"1000.0"`, `"1e3"`) is UNREADABLE -
            `int()` refuses it and it is refused here too.
        resource: "points" | "api_keys" | "sessions" | "users" | "graphs"
            | "documents".
        sdk: pre-built org SDK (REST callers already hold one) — optional.
        slot_credit: #4355 — how many slots this write RELEASES as part of the
            same operation, so the admission check is evaluated against the
            post-release count. It exists for exactly ONE caller: the
            replacement-aware rotate primitive, which passes 1 after proving
            (``api_key_occupies_slot``) that the displaced row is counted once.
            MUST be 0 everywhere else, and MUST never become reachable from a
            client-supplied value — an unproven credit is a free slot.

    Raises:
        QuotaExceededError: org at/over limit (402-equivalent).
        QuotaCheckError: counting failed (fail-closed).
    """
    if limits is None:
        return  # stdio/operator — no org context
    org_id = limits.get("org_id")
    if not org_id:
        return
    # ── documents: DERIVED-CONSTANT cap (T2-P2a, #1726) — handled BEFORE the
    # pricing-keyed generic path. Deliberately NOT in _RESOURCE_LIMIT_KEYS: a
    # pricing.json max_documents field would ripple through tier_limits /
    # _REQUIRED_LIMIT_KEYS / every tier's resolved limits (the KeyError
    # surface the plan pins against). max_documents = max_points ×
    # _DOCUMENTS_FROM_POINTS_FACTOR; an explicitly-None max_points is
    # UNLIMITED (Team tier) — skip. The documents gate fires on the
    # /v1/index/docs job ONLY (cycle-3 P2: never an unpinned tenant-global
    # surprise).
    if resource == "documents":
        max_points = limits.get("max_points")
        if max_points is None:
            return  # explicitly-None = unlimited
        try:
            max_points = int(max_points)
        except (TypeError, ValueError, OverflowError):
            # OverflowError (#4051 review round 2): a non-finite stored
            # max_points is the SAME unreadable-limit class the generic path
            # below refuses — this branch derives the documents cap from it, so
            # it must not escape as an unmapped 500 either.
            # ERROR level for the same reason as the generic path (#686): a
            # money-gate data fault is not ops-invisible just because it was
            # caught by the derived branch instead — and `_check_org_limit`'s
            # own comment states that `quota` logged this refusal, which was
            # only true of the generic path until now (#4051 review round 3).
            _logger.error(
                "quota limit unreadable (fail-closed): team=%s resource=%s "
                "field=%s value=%r", org_id, "documents", "max_points",
                max_points)
            raise QuotaCheckError(
                f"team limits max_points invalid for documents resource "
                f"({max_points!r})") from None
        limit = max_points * _DOCUMENTS_FROM_POINTS_FACTOR
        count = _count_resource(org_id, "documents", sdk=sdk)
        if count >= limit:
            raise QuotaExceededError(
                with_limit_contact(
                    f"Team documents limit reached ({limit}). Upgrade your plan "
                    f"to increase it."),
                resource="documents", used=count, limit=limit,
            )
        return
    limit_key = _RESOURCE_LIMIT_KEYS.get(resource)
    if limit_key is None:
        raise QuotaCheckError(f"unknown quota resource: {resource!r}")
    limit = limits.get(limit_key)
    if limit is None:
        # An explicitly-None limit means UNLIMITED (Team tier: users/graphs
        # stored null) — skip enforcement (#683). Distinguish from a MISSING
        # key, which is fail-closed (#310 GAP-B): never silently fall back to
        # lenient caps.
        # #4010: sessions is no longer the exception to that rule. The flat
        # 1000 it fell back to was an inherited code fallback, never a
        # ratified cap (see the module comment above), so it has no constant
        # to fall back to and its resolved value is always the explicit None
        # — the lenient `if resource == "sessions": limit =
        # DEFAULT_MAX_SESSIONS` branch is deleted, not relocated.
        if limit_key in limits:
            return
        # ERROR level, for the reason the unreadable-limit refusal below states:
        # `_check_org_limit`'s docstring promises a data-fault refusal is logged
        # with org_id and resource, and this one raised SILENTLY, so that caller
        # demoted the whole class to DEBUG and the fault was ops-invisible. It
        # matters more since #4051 removed the commit lane's `max_sessions`
        # proxy: this is now the commit lane's ONLY presence gate, and a limits
        # dict built without the key refuses every write (a real path —
        # docs/plans/2026-09-11-2977-object-retraction.md records a live
        # "quota check failed: team limits missing max_points" 500 from a stub
        # team).
        _logger.error(
            "quota limit missing (fail-closed): team=%s resource=%s field=%s",
            org_id, resource, limit_key)
        raise QuotaCheckError(f"team limits missing {limit_key} for resource {resource!r}")
    # #4051: the limit is read from the org's OWN source (the resolved
    # `<limit_key>` value) — and a value that cannot be READ as a number is
    # refused, never treated as unlimited. Before this, a non-numeric limit
    # reached the comparison below and raised a bare `TypeError` OUT of the
    # gate: refused only by accident, with no message naming the field, and
    # invisible to every caller that handles the documented `QuotaCheckError`.
    # ⚠️ SCOPE — this validates the dict the GATE IS GIVEN. It cannot fix a
    # corrupt value that dies EARLIER or is coerced ELSEWHERE (both tracked as
    # #10136). THE LANE INVENTORY LIVES HERE, ONCE — do not restate it at a
    # caller or in a test docstring; copies drifted apart inside #4051's
    # own change.
    #   • ARRIVES RAW (this refusal owns it): `get_current_org_session` →
    #     `_session_user_org` takes the Org node's `max_api_keys` UNCOERCED
    #     (`_org_limits_from_node` passes `mak` straight through) into
    #     `_check_org_limit(org, "api_keys")` at the key-mint / rotate sites. So
    #     the gate must not assume its input is an int.
    #   • DOES NOT ARRIVE — `documents`: it is handled by the DERIVED branch
    #     above, which returns before this code, and its only caller feeds it
    #     `resolve_org_limits`, which already coerces with `int(mp)` — a corrupt
    #     stored value therefore dies in that resolver, before any gate.
    #   • DOES NOT ARRIVE — the commit/capture lanes' auth resolvers coerce
    #     `max_points` with `int(mp)` first, so a corrupt stored value dies in
    #     AUTH; `create_graph` alone does its own `int(max_graphs)` with no call
    #     to this helper; and `invite_to_org` reads NO limit at all (its seat
    #     gate hardcodes the tier's number and refuses with a NAMED message), so
    #     it has no unreadable-limit class to fail in.
    # OverflowError is the same class as TypeError/ValueError (`int(float('inf'))`).
    try:
        limit = int(limit)
    except (TypeError, ValueError, OverflowError):
        # ERROR level, like `_count_resource`'s own failure path (#686): this
        # raises BEFORE the count, so it must not rely on that log — and the
        # pre-change crash (a bare TypeError) DID reach `logger.exception` +
        # Sentry, so a silent refusal here would downgrade a money-gate data
        # fault to an ops-invisible 500 (#4051 review round 4).
        _logger.error(
            "quota limit unreadable (fail-closed): team=%s resource=%s "
            "field=%s value=%r", org_id, resource, limit_key, limit)
        raise QuotaCheckError(
            f"team limits {limit_key} unreadable for resource {resource!r} "
            f"({limit!r})") from None
    count = _count_resource(org_id, resource, sdk=sdk)
    # #4614: report what the gate COMPARED (post-credit), never a fresh count —
    # the refusal's numbers must be the ones that produced it.
    used = count - slot_credit
    if used >= limit:
        raise QuotaExceededError(
            with_limit_contact(
                f"Team {resource} limit reached ({limit}). Upgrade your plan "
                f"to increase it."),
            resource=resource, used=used, limit=limit,
        )


# ── Ask lane: shared budget bucket + bounded runner (#1987 Tasks 6/7/8) ────
#
# ⛔ RETIRED-BUT-RETAINED (#3849): every PRODUCT caller of this cluster — the
# hosted REST /v1/ask handler, the hosted MCP ask handler and the selfhost
# /ask handler — was removed with the ask product surface, so no product path
# reaches it today (the eval-only lane, tortoise/ask_lane.py, is unbudgeted).
# Its one remaining caller is a test: tests/test_quota.py pins
# `run_ask_bounded`'s exec-floor guarantee, so the #3849 §7 D5 purge has to
# move or drop that test with it. The comments below that name the removed
# handlers are kept as the record of what the bounds were.
#
# The ONE shared per-org per-minute LLM budget for the ask lane, used by
# BOTH the hosted REST handler and the hosted MCP handler (no duplicated
# prune/check/append logic — mcp_server.py imports ``tortoise.quota``). The
# bucket is bounded: idle org keys are pruned under a TTL + LRU cap so
# memory stays bounded under N-orgs-ask-once / M-orgs-idle, and a
# reactivated org starts clean (P2-15).

import collections  # noqa: E402
import contextlib  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

_ASK_BUDGET_TTL_S = 300.0       # idle org bucket expiry
_ASK_BUDGET_MAX_ORGS = 1024    # LRU bound on the bucket dict
_ask_llm_budget: collections.OrderedDict[str, list[float]] = \
    collections.OrderedDict()
_ask_budget_lock = threading.Lock()


def _selfhost_transport_active() -> bool:
    """The transport-keyed selfhost exemption channel (tortoise/transport.py
    — the SELFHOST_ORG_ID VALUE is never the exemption key)."""
    from tortoise.transport import _selfhost_transport
    return _selfhost_transport.get()


def ask_llm_budget_available(org_id: str | None) -> bool:
    """True if this org still has ask LLM budget this minute.

    Exemptions (always allowed): ``not org_id`` (stdio/None — mirroring
    ``_analyze_llm_budget_available``'s ``if not org_id: return True``,
    mcp_server.py) AND the selfhost-transport signal
    (``_selfhost_transport.get()`` — stdio + selfhost MCP modes are
    unbudgeted). A hosted org with the RAW id "selfhost" is budget-charged
    (the exemption is transport-keyed, never value-keyed).

    Prune → check → append (never pop between check and append — that
    orphans the appended timestamp and silently disables the budget).
    """
    if not org_id or _selfhost_transport_active():
        return True
    now_ts = time.monotonic()
    with _ask_budget_lock:
        bucket = _ask_llm_budget.get(org_id)
        if bucket is None:
            bucket = []
        # prune stale entries for THIS org (equal/sub-second timestamps
        # safe — monotonic, no index errors)
        bucket[:] = [ts for ts in bucket if now_ts - ts < 60.0]
        if len(bucket) >= MAX_ASK_LLM_PER_MIN:
            return False
        bucket.append(now_ts)
        _ask_llm_budget[org_id] = bucket
        _ask_llm_budget.move_to_end(org_id)
        # TTL + LRU bound: drop expired/least-recently-used org keys
        expired = [k for k, v in _ask_llm_budget.items()
                   if v and now_ts - v[-1] >= _ASK_BUDGET_TTL_S]
        for k in expired:
            del _ask_llm_budget[k]
        while len(_ask_llm_budget) > _ASK_BUDGET_MAX_ORGS:
            _ask_llm_budget.popitem(last=False)
    return True


def _reset_ask_budget_for_tests() -> None:
    """Test seam — clears the shared ask budget bucket."""
    with _ask_budget_lock:
        _ask_llm_budget.clear()


def ask_budget_retry_after(org_id: str | None) -> float:
    """Seconds until the org's ask budget window self-heals (Retry-After
    for the 429 ``quota_exceeded`` response — ≈ the prune delay; 0 when no
    budget was consumed)."""
    if not org_id:
        return 0.0
    now_ts = time.monotonic()
    with _ask_budget_lock:
        bucket = _ask_llm_budget.get(org_id) or []
        if not bucket:
            return 0.0
        oldest = min(bucket)
        return max(0.0, 60.0 - (now_ts - oldest))


# ── #4051 §3.1: the per-org commit ingest budget ────────────────────────
# A weighted (bytes) window, so it cannot reuse ``rate_buckets._bucket_route``
# as-is: that primitive compares ``len(bucket) >= limit`` on per-request
# timestamp entries, which cannot express "the SUM of these charges". What it
# CAN do — and what this store therefore copies rather than inventing — is its
# documented capacity policy (``tortoise/rate_buckets.py``, #3124/#2866): an
# ACTIVE key is NEVER evicted, because evicting one resets a victim's consumed
# budget (the fail-open shape #2866 rejects as alternative C); only buckets
# with no in-window charges are reclaimed, head-first; and a new key arriving
# at a full store is charged to ONE shared overflow bucket instead of growing
# the store, refused only when that overflow is spent too.
#
# The bucket stores ``(timestamp, org_id, charge_bytes)`` because the WINDOW
# TOTAL is the quantity compared against the allowance — both the sum and the
# oldest entry's age are needed on a refusal (``used`` and ``Retry-After``),
# and both come from the ONE locked read that refused (#4614). The org field is
# the PER-KEY OVERFLOW ATTRIBUTION ``rate_buckets.py`` records as closing a
# known fail-open regression (#3124 review): the shared overflow holds charges
# for several orgs, so without attribution (a) a refusal would report OTHER
# orgs' bytes as the caller's own ``used``, and (b) an org that spent the
# overflow could graduate to a fresh owned bucket the moment ANY slot freed and
# obtain a SECOND full allowance inside one window — the exact defect
# ``test_sticky_overflow_blocks_a_second_budget`` pins there, re-pinned here.
_COMMIT_INGEST_WINDOW_S = 60.0
#: Owned-key cap, matching ``RateLimitMiddleware.MAX_BUCKETS`` — the repo's own
#: bound on a per-process bucket store.
_COMMIT_INGEST_MAX_ORGS = 10_000
#: The ONE shared overflow bucket a new org is charged to when the owned store
#: is full of ACTIVE buckets. A sentinel that cannot collide with an org id
#: (those come from the auth layer; none contains a NUL).
_COMMIT_INGEST_OVERFLOW_KEY = "\x00commit-ingest-overflow"
_commit_ingest_budget: collections.OrderedDict[
    str, list[tuple[float, str, int]]] = collections.OrderedDict()
_commit_ingest_lock = threading.Lock()


class IngestAdmission(collections.namedtuple(
        "IngestAdmission", "allowed used limit retry_after scope")):
    """One per-org ingest-budget decision — allowed, and the numbers compared.

    A decision, not a predicate, because the 429 must report ``used``/``limit``
    and a ``Retry-After`` that come from the SAME read that refused (#4614:
    a refusal is a structured state; a second read could disagree with the
    first under concurrency).

    ``scope`` names WHICH allowance the decision was made against:

    * ``"org"`` — the org's own bucket. ``used`` is the org's own spend and the
      numbers in the refusal describe the caller's traffic.
    * ``"process"`` — the SHARED overflow bucket, used only when the owned
      store is full of active orgs (the fail-closed flood regime). ``used`` is
      still THIS org's own spend in it, which can legitimately be ``0``: the
      refusal is then about the instance's shared capacity, not about anything
      the caller wrote. A client branching on ``used >= limit`` would misread
      that as its own overrun, so the scope — not the number — is the signal.
    """

    __slots__ = ()


def _commit_ingest_owned_count(store) -> int:
    """Org-keyed buckets in ``store`` — the ONE shared overflow bucket does not
    consume a slot (``rate_buckets._bucket_owned_count``, same policy)."""
    return len(store) - (1 if _COMMIT_INGEST_OVERFLOW_KEY in store else 0)


def _commit_ingest_reclaim(store, now_ts: float) -> None:
    """Drop only buckets with no IN-WINDOW charges — the head, while it lasts.

    ``rate_buckets.py``'s documented policy, kept rather than re-invented: an
    active key is never evicted (that would hand a throttled org a fresh
    minute). Last-charge ordering (``move_to_end`` on every charge) makes the
    head the least-recently-charged key, so stopping at the first bucket that
    still holds an in-window charge is provably safe — every later key was
    charged at least as recently. Bounded work: no store-wide iteration on any
    charge.
    """
    while store:
        head_key = next(iter(store))
        head = store[head_key]
        head[:] = [e for e in head
                   if now_ts - e[0] < _COMMIT_INGEST_WINDOW_S]
        if head:
            return
        del store[head_key]


def commit_ingest_admit(org_id: str | None, charge_bytes: int) -> IngestAdmission:
    """Charge ``charge_bytes`` against the org's rolling-60s ingest allowance.

    Exemptions:

    * ``not org_id`` (stdio/operator) is unbounded — it mirrors
      ``enforce_org_limit``'s ``if not org_id: return`` and
      ``_check_org_limit``'s "internal/no-org context — skip".
    * a SINGLE request larger than the whole allowance is ADMITTED AND
      RECORDED when the window is EMPTY. Refusing it (with a Retry-After) would
      tell a client to retry a request that can never fit — an unbreakable loop
      that only appears if an operator sets
      ``TORTOISE_COMMIT_INGEST_BYTES_PER_MIN`` below the route's 8 MiB body cap.
      Admitting it once spends the window, so the NEXT request is throttled: at
      most one over-sized request per window, never an unlimited stream of
      them (which is why the ``used == 0`` condition is load-bearing, not a
      detail — without it every over-sized request would be admitted).

    A REFUSED charge is not appended, so a client parked at the boundary is not
    pushed further away from recovery by each futile retry.

    The SHARED-overflow regime keeps ``rate_buckets.py``'s per-key attribution
    (#3124 review), which is what makes the two numbers below honest rather
    than merely conservative:

    * the ADMISSION predicate compares the whole target bucket against the
      allowance, so the overflow cannot be over-subscribed by many orgs adding
      one charge each (the shared capacity ``rate_buckets`` documents);
    * ``used`` is what THIS ORG spent — an owned bucket is entirely its own,
      and in the shared overflow only entries attributed to this org count. A
      refusal therefore never names another tenant's bytes, and a fresh org
      refused because the shared bucket is spent reports ``used`` 0 (the truth:
      it spent nothing; the store is full, which is the fail-closed overflow
      class both stores accept) — and the returned ``scope`` is ``"process"``,
      so a client never reads ``used: 0`` as an overrun of its own;
    * an org is STICKY in the overflow while its own charges are in-window, so
      freeing one slot cannot hand it a second full allowance.
    """
    limit = MAX_COMMIT_INGEST_BYTES_PER_MIN_PER_ORG
    charge = max(0, int(charge_bytes))
    if not org_id:
        return IngestAdmission(True, 0, limit, 0.0, "org")
    now_ts = time.monotonic()
    with _commit_ingest_lock:
        store = _commit_ingest_budget
        key = org_id
        if key != _COMMIT_INGEST_OVERFLOW_KEY and key not in store:
            _commit_ingest_reclaim(store, now_ts)
            overflow = store.get(_COMMIT_INGEST_OVERFLOW_KEY)
            if overflow is not None:
                overflow[:] = [e for e in overflow
                               if now_ts - e[0] < _COMMIT_INGEST_WINDOW_S]
            # STICKY PER ORG, never store-wide: an org with its OWN in-window
            # overflow charges stays in the overflow until they expire (no
            # second allowance), while an org with none takes a free slot at
            # once (a store-wide rule would deny every fresh org for the rest
            # of the window once the overflow held any charge). Both halves are
            # ``rate_buckets._bucket_route``'s #3124 rule, restated for a
            # weighted bucket.
            sticky = overflow is not None and any(e[1] == org_id
                                                  for e in overflow)
            if sticky or (_commit_ingest_owned_count(store)
                          >= _COMMIT_INGEST_MAX_ORGS):
                key = _COMMIT_INGEST_OVERFLOW_KEY
        bucket = store.get(key)
        if bucket is None:
            bucket = []
        # prune THIS bucket's expired charges, then sum what is in-window
        bucket[:] = [e for e in bucket
                     if now_ts - e[0] < _COMMIT_INGEST_WINDOW_S]
        # ``bucket_used`` is the capacity predicate (for the shared overflow it
        # is EVERY org's charges: one shared bucket, one allowance). ``used`` is
        # what THIS org spent — the number a refusal reports, so it never names
        # another tenant's bytes.
        bucket_used = sum(e[2] for e in bucket)
        used = (bucket_used if key != _COMMIT_INGEST_OVERFLOW_KEY
                else sum(e[2] for e in bucket if e[1] == org_id))
        scope = ("process" if key == _COMMIT_INGEST_OVERFLOW_KEY else "org")
        # ADMIT when the window has room, and ALSO when this single request is
        # bigger than the whole allowance AND the window is empty (the
        # over-sized case: see the docstring — once per window, never forever).
        if bucket_used + charge <= limit or (bucket_used == 0
                                            and charge > limit):
            bucket.append((now_ts, org_id, charge))
            store[key] = bucket
            store.move_to_end(key)
            return IngestAdmission(True, used + charge, limit, 0.0, scope)
        # RETRY-AFTER: the wait until ENOUGH of the window has expired for
        # THIS charge to fit — not until the OLDEST entry expires. Expiry is
        # oldest-first and the charges are uneven, so `window - age(oldest)`
        # promises a wait after which the request still does not fit, and a
        # client honouring the header exactly is refused again (measured:
        # 10 B at t+0 and 900 B at t+2 against a 1,000 B allowance left a
        # 500 B request needing the t+2 entry to expire at t+62, while the
        # header said 2 s at t+60). Walk the bucket oldest-first, subtracting
        # each charge until the request fits, and answer for THAT entry. A
        # charge larger than the whole allowance never fits this way; it ages
        # out with the window, and the `used == 0` rule above admits it at
        # once when the window empties.
        remaining = bucket_used
        retry_ts = bucket[-1][0] if bucket else now_ts
        for entry_ts, _owner, entry_charge in bucket:
            remaining -= entry_charge
            retry_ts = entry_ts
            if remaining + charge <= limit:
                break
        retry_after = max(0.0, _COMMIT_INGEST_WINDOW_S - (now_ts - retry_ts))
        # Keep the pruned bucket so the org's remaining window still ages out.
        if bucket:
            store[key] = bucket
        return IngestAdmission(False, used, limit, retry_after, scope)


def _reset_commit_ingest_budget_for_tests() -> None:
    """Test seam — clears the per-org commit ingest window.

    The window is PROCESS state keyed by org_id, so a test that spends an
    org's minute must not 429 the next test in the same process (the same
    hazard the ask lane's ``_reset_ask_budget_for_tests`` exists for).
    ``tests/conftest.py`` calls this per test.
    """
    with _commit_ingest_lock:
        _commit_ingest_budget.clear()


class AskInFlightLimitError(Exception):
    """Per-org in-flight cap hit (4 concurrent) — mapped to 429
    ``in_flight_limit`` by the ask handlers (removed in #3849 — no handler
    maps it any more, though the retained ``run_ask_bounded`` still raises
    it; see the RETIRED note on this cluster)."""


class AskBoundedTimeoutError(Exception):
    """The bounded ask section exceeded ``_ASK_TIMEOUT_S`` (semaphore queue
    OR the reader call) — mapped to 504 ``timeout`` by the ask handlers
    (removed in #3849 — no handler maps it any more, though the retained
    ``run_ask_bounded`` still raises it and tests/test_quota.py pins that;
    see the RETIRED note on this cluster)."""


#: Ask-lane bounds (#1987 Task 7): global semaphore, per-org in-flight cap,
#: and the injectable module-level timeout (monkeypatched in tests — no real
#: 60s sleeps).
_ASK_TIMEOUT_S = 60
_ASK_EXEC_FLOOR_S = 5.0     # a started ask is guaranteed >= this much execution time
_ASK_GLOBAL_SEMAPHORE_SIZE = 8
_ASK_ORG_IN_FLIGHT_CAP = 4

#: Loop-safe semaphore/in-flight state: keyed by the RUNNING loop object via
#: a weak-keyed dict (entries die with their loop — a recreated loop rebinds
#: cleanly; the size bound is one entry per live loop). Never a module-level
#: ``asyncio.Semaphore`` bound to the first loop it is awaited in (the
#: mcp_server.py hazard, P1-8/P2-8).
import weakref  # noqa: E402 — the state helpers import it lazily too

_ask_loop_state: weakref.WeakKeyDictionary = None  # type: ignore[assignment]
_ask_loop_state_lock = threading.Lock()


def _ask_state_for_loop(loop):
    import weakref
    global _ask_loop_state
    if _ask_loop_state is None:
        _ask_loop_state = weakref.WeakKeyDictionary()
    with _ask_loop_state_lock:
        st = _ask_loop_state.get(loop)
        if st is None:
            import asyncio
            st = {"sem": asyncio.Semaphore(_ASK_GLOBAL_SEMAPHORE_SIZE),
                  "in_flight": collections.Counter()}
            _ask_loop_state[loop] = st
        return st


def _reset_ask_loop_state_for_tests() -> None:
    """Test seam — drops the loop-keyed semaphore/in-flight state."""
    import weakref
    global _ask_loop_state
    with _ask_loop_state_lock:
        _ask_loop_state = weakref.WeakKeyDictionary()


def _call_sync(fn, args, kwargs):
    """Thread-pool trampoline: ``run_ask_bounded`` runs the SYNC ask lane
    (retrieval + annotation + reader) in an executor thread."""
    return fn(*args, **kwargs)


def ask_in_flight_capacity(org_id: str | None) -> bool:
    """True when the per-org in-flight ask cap still has room (or org_id
    is None). A cheap pre-check for the budget gates (#1987 P2): a request
    that ``run_ask_bounded`` will reject with 429 ``in_flight_limit`` must
    not burn a budget slot. Best-effort — a benign race can still charge a
    slot that later 429s, but the common full-cap case is skipped."""
    if not org_id:
        return True
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return True
    st = _ask_state_for_loop(loop)
    return st["in_flight"][org_id] < _ASK_ORG_IN_FLIGHT_CAP


async def run_ask_bounded(fn, org_id: str | None, *args, **kwargs):
    """Shared bounded ask runner (#1987 Task 7/8/9) — the ONE wrapper the
    hosted HTTP handler, the hosted MCP handler, and the selfhost REST
    handler all awaited (all three removed in #3849, so no product caller
    reaches it any more; its one remaining caller is ``tests/test_quota.py``,
    which pins the exec-floor guarantee — see the RETIRED note on this
    cluster).

    Bounds: global ``asyncio.Semaphore(8)`` + ``asyncio.wait_for(_ASK_TIMEOUT_S)``
    wrapping the FULL bounded section (semaphore acquire + the to_thread
    reader call) — total per-request latency is capped at ``_ASK_TIMEOUT_S``;
    a request queued behind 8 in-flight past the budget 504s (bounded, never
    an unbounded queue wait). The per-org in-flight cap (4) lives INSIDE
    this wrapper (shared by HTTP + MCP): the counter increments ON ENTRY
    (BEFORE ``Semaphore.acquire`` — queued asks count toward the org's cap
    while waiting) and is released on the ``to_thread`` future's COMPLETION
    (an ``add_done_callback`` — release on completion, NOT on ``wait_for``
    firing: a timed-out ask keeps its slot until the thread finishes, so a
    504 burst cannot leak counters/slots and the follow-up ask succeeds).

    The acquire-cancelled path (wait_for fires during the queue wait — no
    future exists) decrements the counter in the wrapper's finally.

    Raises ``AskInFlightLimitError`` (per-org cap), ``AskBoundedTimeoutError``
    (504), or the underlying exception from ``fn``.
    """
    import asyncio
    loop = asyncio.get_running_loop()
    st = _ask_state_for_loop(loop)
    sem = st["sem"]
    inflight = st["in_flight"]
    # ``_sdk_org_id`` is the bound SDK lane's metering org_id (hosted
    # HTTP/MCP handlers pass the org; selfhost passes None; that SDK entry
    # point was removed in #3849) — stripped here
    # so ``fn`` receives it WITHOUT colliding with this wrapper's
    # own ``org_id`` (the in-flight-cap key).
    fn_kwargs = dict(kwargs)
    sdk_org_id = fn_kwargs.pop("_sdk_org_id", org_id)
    if sdk_org_id is not None:
        fn_kwargs["org_id"] = sdk_org_id
    # contextvars do NOT propagate to run_in_executor worker threads (3.12,
    # empirically confirmed) — capture the selfhost-transport flag HERE (the
    # asyncio context) and thread it through so the executor-thread metering
    # exemption is honored (never a phantom :MeteringRecord).
    from tortoise.transport import _selfhost_transport
    if _selfhost_transport.get():
        fn_kwargs["_selfhost_transport"] = True
    if org_id:
        if inflight[org_id] >= _ASK_ORG_IN_FLIGHT_CAP:
            raise AskInFlightLimitError(
                f"team {org_id!r} in-flight ask cap reached "
                f"({_ASK_ORG_IN_FLIGHT_CAP})")
        inflight[org_id] += 1
    future = None
    t_start = time.monotonic()
    # The SEMAPHORE-ACQUIRE window is bounded by the REMAINDER of the total
    # budget after reserving the execution floor: a request that cannot be
    # acquired within ``_ASK_TIMEOUT_S - _ASK_EXEC_FLOOR_S`` 504s at acquire
    # WITHOUT starting (no wasted model call).
    acquire_timeout = max(0.0, _ASK_TIMEOUT_S - _ASK_EXEC_FLOOR_S)
    try:
        try:
            await asyncio.wait_for(sem.acquire(), timeout=acquire_timeout)
        except (TimeoutError, asyncio.CancelledError) as e:
            # wait_for fired during the queue wait — no future exists; the
            # counter decrements here (never a leaked counter).
            if org_id:
                inflight[org_id] -= 1
            if isinstance(e, asyncio.TimeoutError):
                raise AskBoundedTimeoutError(
                    f"ask queued past {acquire_timeout}s (8 in flight)") from e
            raise
        try:
            future = loop.run_in_executor(None, _call_sync, fn, args, fn_kwargs)
        except BaseException:
            # loop closed/shutting down → run_in_executor raises SYNCHRONOUSLY
            # (no future, no done-callback): release the slot + counter NOW.
            with contextlib.suppress(Exception):
                sem.release()
            if org_id:
                inflight[org_id] -= 1
            raise

        def _release(_fut) -> None:
            with contextlib.suppress(Exception):  # best-effort release — never blocks
                sem.release()
            if org_id:
                inflight[org_id] -= 1

        future.add_done_callback(_release)
        # The SECOND window uses the REMAINING budget (the acquire above may
        # have already consumed part of it) — total per-request latency is
        # truly capped at ``_ASK_TIMEOUT_S``.
        remaining = max(0.0, _ASK_TIMEOUT_S - (time.monotonic() - t_start))
        try:
            return await asyncio.wait_for(asyncio.shield(future),
                                          timeout=remaining)
        except TimeoutError as e:
            raise AskBoundedTimeoutError(
                f"ask exceeded {_ASK_TIMEOUT_S}s") from e
    except AskInFlightLimitError:
        raise
    except AskBoundedTimeoutError:
        raise
    except BaseException:
        # the future's done-callback releases slot + counter on completion
        raise
