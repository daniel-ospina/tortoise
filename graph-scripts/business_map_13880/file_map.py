#!/usr/bin/env python3
"""File the #13880 business map into the clean graph through the normal doors.

The map is `business_map.json` in this directory: subjects, objects, sources,
events, points and the edges between them, every item judged out of the four
legacy graphs (see JUDGEMENT.md).

WHY THIS SHAPE
--------------
* **The normal doors, not the database.** Every node is written through the
  hosted API's MCP tools, so the record is truth (`STORAGE-ARCHITECTURE.md`
  §2.0) and the graph is the derived cache. Nothing here writes FalkorDB
  directly — the Falkor handle is used for READ-BACK VERIFICATION only.
* **Ontology-faithful kinds, not convenience.** A decision is an `:Event`
  (`eventKind: decision`), a goal is an `:Object` with `goalState`, a person or
  role is a `:Subject` (`subjectKind`), a document is a `:Source`, and a claim
  is a `:Point` (`pointKind: statement`). The map declares those, this script
  re-checks them before it writes anything.
* **Edges are the difference between filed and findable.** Support and
  contradiction are OPERATORS (`IMPL`/`NAND`). ⛔ This surface has no `label`
  parameter: a Layer-2 `semantics` travels as the write's `semantics` argument,
  while the MECHANISM is what is filed — so `validate()` refuses a mechanism
  that contradicts the declared meaning (`IMPL` carrying `refutes` RAISES where
  it must LOWER) and a returned `mechanism_mismatch` is a failure.
  ⛔ AND THE WORD ITSELF IS NOT READABLE BACK, so `--verify` does not claim to
  check it: the door stores no operator `label`, and all 51 operator edges
  declare an explicit `direction`, so the meaning leaves no trace beyond the
  direction it resolved to. A label-only correction therefore cannot be
  re-filed in place — the existing operator must be deleted first, and this
  filer never deletes. That is a stated limitation (measured, #13913 review
  round 8), not a verified property;
  structural relations are plain typed edges (`memberOf`, `holdsRole`,
  `aboutObject`, `performs`, `produces`, …). A node with no edge is an orphan,
  so the filer reports any ref that ends up unconnected.

USAGE
-----
    uv run python graph-scripts/business_map_13880/file_map.py --dry-run
    uv run --with redis python graph-scripts/business_map_13880/file_map.py --verify
    uv run python graph-scripts/business_map_13880/file_map.py --write

`--dry-run` needs no credentials and no network. `--verify` needs
`FALKORDB_CLOUD_URI` (and the optional `redis` package). `--write` needs
`TORTOISE_MCP_API_KEY` in the environment (on the lane box: `set -a; .
/home/cmux/businessmap.env; set +a`).

RESUMING — what `state.json` actually promises. Every landed write is recorded
there, and the two NON-IDEMPOTENT doors (`create_operator`, `create_event`) are
recorded BEFORE the call: `create_operator` has no existence check and a second
one double-counts the claim's EP weight (#10849), so an interrupted run must
fail closed on the duplicate side. A definite refusal un-records it; an UNKNOWN
transport outcome leaves it recorded and is REPORTED — `--verify` is the
authority on what landed, and a re-run writes only what `state.json` does not
record. Ids are never looked up by name (there is no such door on this surface).

⛔ It never deletes, supersedes or mitigates anything. Mitigations are
0.10-0.50 and must be written one at a time; this map needs none.
"""
from __future__ import annotations

import sys

if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"graph-scripts/business_map_13880/file_map.py requires Python "
        f">= 3.12 (got {sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        "`uv run python graph-scripts/business_map_13880/file_map.py`"
    )

import argparse
import contextlib
import http.client
import json
import os
import pathlib
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
MAP_PATH = HERE / "business_map.json"
STATE_PATH = HERE / "state.json"

API = "https://api.premiselabs.co"
MCP_URL = f"{API}/mcp/"

# ── the vocabularies ──────────────────────────────────────────────────────
# ⛔ These are FALLBACKS, used only when the shipped registries cannot be
# imported (a bare box with no checkout). When the repo IS importable the
# validator asks the code that actually gates a write — `get_vocab()` for the
# point/event/source axes and `packs/*/manifest.yaml` for the object kinds —
# because a hand-copied vocabulary is a transcription of the law, and a
# transcription that drifts is exactly the mis-kind this check exists to catch.
POINT_KINDS = {"statement"}
EVENT_KINDS = {"meeting", "decision", "experiment", "deployment", "review",
               "friction", "extraction", "documentCreated", "roleCreated",
               "pointAdded", "sessionCaptured", "AgentSession",
               "humanApproval", "milestone", "occurrence", "turn"}
SUBJECT_KINDS = {"organization", "team", "role", "legalPerson", "naturalPerson", "other"}
OBJECT_KINDS = {
    "Project", "WorkItem", "Problem", "tag", "user", "skill", "tool", "agent",
    "workflow", "agreement", "standard", "other", "strategy", "plan", "goal", "target",
    # product-strategy pack
    "product-strategy:product", "product-strategy:feature", "product-strategy:customer",
    "product-strategy:competitor", "product-strategy:customerSegment",
    "product-strategy:market", "product-strategy:requirement",
    "product-strategy:architecture", "product-strategy:customerProblem",
    # venture pack
    "venture:investment", "venture:program", "venture:asset",
    "venture:fundingAgreement", "venture:tranche", "venture:condition",
    "venture:actionItem",
}
GOAL_STATES = {"met", "active", "blocked", "unmet", "superseded", "abandoned"}
SOURCE_KINDS = {"conversation", "document", "agentSession", "github_issue",
                "github_pr", "slack_message", "linear_card", "linear_cycle"}
STRUCTURAL_RELS = {"performs", "produces", "uses", "authoredBy", "ownedBy",
                   "managedBy", "hasMember", "holdsRole", "memberOf", "reportsTo",
                   "participatesIn", "hasPart", "related", "dependsOn",
                   "references", "wasDerivedFrom", "aboutObject", "aboutSubject",
                   "aboutEvent", "aboutDocument", "aboutSource"}
#: ⛔ TWO MORE TRANSCRIPTIONS THAT GATE A WRITE, READ FROM THE SHIPPED SOURCE.
#: `SEMANTICS` is the ONLY gate between a Layer-2 typo and a filed operator: the
#: door WARN-NOT-BLOCKS an out-of-vocabulary `semantics` (`undeclared_relation`,
#: #7852) and `--verify` cannot read the word back, so a drifted transcription
#: files an operator whose meaning is neither the judgement's nor recoverable
#: (measured: one label typo'd to `suports` passed `validate()`, was sent, and
#: the run printed `all nodes and edges written` with rc 0). `OP_TYPES` gates the
#: mechanism. Both now follow the shipped vocabularies; the literals are the
#: standalone-box fallback, the same shape `DIRECTIONS` uses (#13913 round 18).
_FALLBACK_OP_TYPES = {"IMPL", "NAND", "coherence"}
_FALLBACK_SEMANTICS = {"addresses", "supports", "refutes", "mutuallySupportive",
                       "mutuallyExclusive", "hasPart", "causes", "produces",
                       "dependsOn", "transacts", "related", "ownedBy", "managedBy"}
try:
    from tortoise.ep import MECHANISM_REL_TYPES as _SHIPPED_OP_TYPES
    from tortoise.pack_registry import SEMANTICS_VALUES as _SHIPPED_SEMANTICS

    OP_TYPES = set(_SHIPPED_OP_TYPES)
    SEMANTICS = set(_SHIPPED_SEMANTICS)
except Exception:  # standalone box: the literals, never a silent pass
    OP_TYPES = set(_FALLBACK_OP_TYPES)
    SEMANTICS = set(_FALLBACK_SEMANTICS)
#: ⛔ READ THE DIRECTION VOCABULARY FROM THE SHIPPED SOURCE, never transcribe
#: it. `validate()` refuses a direction this set does not hold, so a drifted
#: transcription is a validator that refuses a surface-LEGAL edge — the same
#: class as `STRUCTURAL_RELS` lagging `_VALID_EDGE_PREDICATES` (#13913 review).
#: Measured before this pin: `live.ACCEPTED_DIRECTION_VALUES - DIRECTIONS` was
#: `{'bidirectional', 'unidirectional'}`, so a door-legal
#: `direction: unidirectional` was refused. The fallback is the transcription,
#: taken only on a box with no checkout (`--dry-run`'s standalone promise).
try:
    from tortoise.live import ACCEPTED_DIRECTION_VALUES as _SHIPPED_DIRECTIONS

    DIRECTIONS = set(_SHIPPED_DIRECTIONS) | {"->", "<-", "<->", "-"}
except Exception:  # standalone box: the literal set, never a silent pass
    DIRECTIONS = {"->", "<-", "<->", "-"}
#: Fallback for `pack_registry.SEMANTICS_MECHANISMS` — used only on a bare box
#: with no checkout. The shipped mapping governs when the repo is importable.
SEMANTICS_MECHANISMS_FALLBACK = {
    "addresses": "IMPL", "causes": "IMPL", "dependsOn": "IMPL", "hasPart": "IMPL",
    "supports": "IMPL", "mutuallySupportive": "IMPL", "produces": "IMPL",
    "refutes": "NAND", "mutuallyExclusive": "NAND",
}


# ── validation ────────────────────────────────────────────────────────────
def load_map() -> dict:
    return json.loads(MAP_PATH.read_text())


def shipped_vocab() -> dict | None:
    """The vocabularies the WRITE GATE uses, or None when unavailable.

    Object kinds are the union of core's `KIND_OWNER_AXES['objectKinds']` and
    every `objectKinds` list declared by an installed pack manifest — which is
    precisely ONTOLOGY §5's rule ("core values are here, pack kinds are
    declared in the pack manifests").
    """
    # Imported inside the function on purpose: a bare box with no checkout can
    # still run `--dry-run`, and the shipped registries are simply unavailable
    # there rather than a hard module-level dependency.
    try:
        from tortoise.commit_schema import get_vocab
        from tortoise.pack_registry import KIND_OWNER_AXES
        from tortoise.sdk import GOAL_STATE_VALUES
    except Exception:  # any import failure means "no shipped vocab here"
        return None

    vocab = get_vocab()
    object_kinds = set(KIND_OWNER_AXES.get("objectKinds", ()))
    packs_dir = HERE.parents[1] / "packs"
    if packs_dir.is_dir():
        try:
            import yaml  # optional, only read when packs are installed
            for manifest in sorted(packs_dir.glob("*/manifest.yaml")):
                doc = yaml.safe_load(manifest.read_text()) or {}
                ns = doc.get("namespace")
                for kind in (doc.get("ontology") or {}).get("objectKinds") or []:
                    object_kinds.add(f"{ns}:{kind}" if ns else kind)
        except Exception:  # an unreadable pack contributes no kinds
            pass
    # The structural predicate set is the writer's own (`create_edge` gates on
    # `_VALID_EDGE_PREDICATES`). Reading it here is why this is not the hand
    # transcription above: `STRUCTURAL_RELS` lags the surface (it omits
    # `aboutAction`, #13913 review), and a validator that refuses a surface-legal
    # edge is the same class as one that accepts an illegal kind.
    try:
        from tortoise.projection.edges import _VALID_EDGE_PREDICATES
        relations = set(_VALID_EDGE_PREDICATES)
    except Exception:  # the fallback is the transcription, not a silent pass
        relations = set(STRUCTURAL_RELS)
    # The MECHANISM each Layer-2 meaning declares. The write gate COMPARES the two
    # and only warns (#9963, warn-not-block), so an `IMPL` wire carrying
    # `refutes` is filed as asked and INVERTS the meaning (it raises where the
    # judgement said it must lower). This validator is the last gate before the
    # write; the filer also treats the returned warning as a failure.
    try:
        from tortoise.pack_registry import SEMANTICS_MECHANISMS
        mechanisms = dict(SEMANTICS_MECHANISMS)
    except Exception:
        mechanisms = dict(SEMANTICS_MECHANISMS_FALLBACK)
    return {
        "point": set(vocab.point_kinds),
        "event": set(vocab.event_kinds),
        "source": set(vocab.source_kinds),
        "object": object_kinds,
        "goal_state": set(GOAL_STATE_VALUES),
        "relation": relations,
        "mechanism": mechanisms,
    }


def validate(doc: dict) -> list[str]:
    """Every rule the ontology makes, checked before a single write."""
    errs: list[str] = []
    vocab = shipped_vocab()
    point_kinds = vocab["point"] if vocab else POINT_KINDS
    event_kinds = vocab["event"] if vocab else EVENT_KINDS
    source_kinds = vocab["source"] if vocab else SOURCE_KINDS
    object_kinds = vocab["object"] if vocab else OBJECT_KINDS
    goal_states = vocab["goal_state"] if vocab else GOAL_STATES
    structural_rels = vocab["relation"] if vocab else STRUCTURAL_RELS
    mechanisms = vocab["mechanism"] if vocab else SEMANTICS_MECHANISMS_FALLBACK
    if vocab is None:
        print("! shipped vocabularies unavailable — validating against the "
              "transcription in this file", file=sys.stderr)
    else:
        print(f"vocabularies: shipped (point {len(point_kinds)}, event "
              f"{len(event_kinds)}, source {len(source_kinds)}, object "
              f"{len(object_kinds)}, goalState {len(goal_states)}, "
              f"relation {len(structural_rels)})")
    refs: dict[str, str] = {}   # ref -> layer

    def reg(ref: str, layer: str) -> None:
        if ref in refs:
            errs.append(f"duplicate ref {ref!r}")
        refs[ref] = layer

    for s in doc["subjects"]:
        reg(s["ref"], "subject")
        if s["subjectKind"] not in SUBJECT_KINDS:
            errs.append(f"{s['ref']}: subjectKind {s['subjectKind']!r} not in core §5")
    for o in doc["objects"]:
        reg(o["ref"], "object")
        if o["objectKind"] not in object_kinds:
            errs.append(f"{o['ref']}: objectKind {o['objectKind']!r} is not declared "
                        "by core or any installed pack")
        gs = o.get("goalState")
        if gs is not None:
            if gs not in goal_states:
                errs.append(f"{o['ref']}: goalState {gs!r} not in GOAL_STATES")
            if o["objectKind"] not in {"goal", "target", "strategy", "plan"}:
                errs.append(f"{o['ref']}: goalState is only meaningful on goal/target/strategy/plan")
    for s in doc["sources"]:
        reg(s["ref"], "source")
        if s["sourceKind"] not in source_kinds:
            errs.append(f"{s['ref']}: sourceKind {s['sourceKind']!r} not declared")
    for e in doc["events"]:
        reg(e["ref"], "event")
        if e["eventKind"] not in event_kinds:
            errs.append(f"{e['ref']}: eventKind {e['eventKind']!r} not in core §5")
        if len(e["content"]) > 1000:
            errs.append(f"{e['ref']}: event content {len(e['content'])} > 1000 chars")
    for p in doc["points"]:
        reg(p["ref"], "point")
        if p.get("kind", "statement") not in point_kinds:
            errs.append(f"{p['ref']}: pointKind {p.get('kind')!r} is not declared")
        if p.get("kind", "statement") != "statement":
            errs.append(f"{p['ref']}: pointKind {p.get('kind')!r} — the state-centric "
                        "model writes `statement` only (§5); a decision is an Event "
                        "and a goal is an Object")
        if len(p["content"]) > 1000:
            errs.append(f"{p['ref']}: point content {len(p['content'])} > 1000 chars")
        if p.get("source") and p["source"] not in {s["ref"] for s in doc["sources"]}:
            errs.append(f"{p['ref']}: source {p['source']!r} is not declared in sources[]")

    for i, e in enumerate(doc["edges"]):
        where = f"edge[{i}] {e.get('from')}->{e.get('to')}"
        for end in ("from", "to"):
            if e.get(end) not in refs:
                errs.append(f"{where}: {end} ref not declared")
        if "op" in e:
            if e["op"] not in OP_TYPES:
                errs.append(f"{where}: op_type {e['op']!r} not a mechanism")
            if e.get("label") and e["label"] not in SEMANTICS:
                errs.append(f"{where}: label {e['label']!r} not in the Layer-2 semantics vocabulary")
            if e.get("label") and e["label"] in mechanisms:
                _declared = mechanisms[e["label"]]
                if _declared != e["op"]:
                    errs.append(
                        f"{where}: op {e['op']} contradicts the meaning {e['label']!r}, "
                        f"which declares {_declared} — an {e['op']} wire carrying a "
                        f"{_declared} meaning INVERTS what the judgement said")
            if e.get("direction") and e["direction"] not in DIRECTIONS:
                errs.append(f"{where}: direction {e['direction']!r} not one of {sorted(DIRECTIONS)}")
            # a target must be epistemic: Point/Subject/Object/Event
            for end in ("from", "to"):
                if refs[e[end]] not in {"point", "subject", "object", "event"}:
                    errs.append(f"{where}: operator endpoint {e[end]} is a {refs[e[end]]}, not epistemic")
        elif "relation" in e:
            if e["relation"] not in structural_rels:
                errs.append(f"{where}: relation {e['relation']!r} not a valid structural predicate")
        else:
            errs.append(f"{where}: neither op nor relation")

    # orphan check — a filed node with no edge is invisible
    connected = set()
    for e in doc["edges"]:
        connected.add(e["from"])
        connected.add(e["to"])
    for ref in refs:
        if ref not in connected:
            errs.append(f"{ref}: no edges — an orphan node is invisible")

    # identity check — `--verify` reads the map back BY IDENTITY, so two items
    # sharing one (label, property, value) make one of them unverifiable: the
    # read-back cannot tell them apart and reports both present when one is
    # missing. The LABEL is part of the key, so an Event and an Object may share
    # a name — they are different nodes, and the read-back reads the label.
    seen: dict[tuple[str, str, str], int] = {}
    for ident in map_identities(doc):
        seen[ident] = seen.get(ident, 0) + 1
    for (label, prop, value), count in sorted(seen.items()):
        if count > 1:
            errs.append(f"{count} items share the identity {label}.{prop}="
                        f"{value[:60]!r} — the read-back cannot tell them apart")
    # the same rule for EDGES. `--verify` matches each declared edge against the
    # graph independently, so the same edge judged twice is certified by ONE
    # written edge — two judgements, one fact, and the duplicate is invisible.
    #    ⛔ Keyed on `edge_key`, NOT `edge_marker`: the marker also carries the
    #    direction and the label (so a CORRECTION re-files), while two judgements
    #    that differ only in spelling are still the same endpoints judged twice.
    edge_seen: dict[str, int] = {}
    for e in doc["edges"]:
        edge_seen[edge_key(e)] = edge_seen.get(edge_key(e), 0) + 1
    for marker, count in sorted(edge_seen.items()):
        if count > 1:
            errs.append(f"{count} edges are the same edge ({marker!r}) — judged "
                        "twice; one written edge would certify both")
    return errs


# ── MCP transport ─────────────────────────────────────────────────────────
class Mcp:
    """Minimal streamable-HTTP MCP client: initialize, then tools/call."""

    def __init__(self, key: str, timeout: float = 120.0):
        self.key = key
        self.timeout = timeout
        self.session: str | None = None
        self._next_id = 0

    def _post(self, body: dict) -> tuple[int, dict | None, dict]:
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            MCP_URL, data=data, method="POST",
            headers={
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                **({"Mcp-Session-Id": self.session} if self.session else {}),
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            raw = resp.read()
            hdrs = dict(resp.headers)
            ctype = resp.headers.get("Content-Type", "")
        payload = None
        if raw:
            text = raw.decode("utf-8", "replace").strip()
            if "text/event-stream" in ctype:
                # SSE: take the last `data:` frame — the response to our id
                frames = [ln[5:].strip() for ln in text.splitlines()
                          if ln.startswith("data:")]
                payload = json.loads(frames[-1]) if frames else None
            else:
                payload = json.loads(text)
        return (resp.status if hasattr(resp, "status") else 200), payload, hdrs

    def initialize(self) -> None:
        self._next_id += 1
        _code, payload, hdrs = self._post({
            "jsonrpc": "2.0", "id": self._next_id, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "business-map-13880", "version": "1"}},
        })
        sid = hdrs.get("Mcp-Session-Id") or hdrs.get("mcp-session-id")
        self.session = sid
        if payload and payload.get("error"):
            raise RuntimeError(f"initialize refused: {payload['error']}")
        # notifications/initialized is a notification: no id, no body expected
        with contextlib.suppress(urllib.error.HTTPError):
            # some transports answer 202/204 with an error-ish shape
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call(self, tool: str, args: dict, *, retry_safe: bool = True) -> dict:
        """Call one tool, refusing to retry a write that is not idempotent.

        `retry_safe=False` is for the tools whose registry entry says a repeat
        writes a SECOND node (`tortoise_create_operator`: "a retry of
        `create_operator` writes a SECOND operator over the same endpoints …
        the duplicate double-counts the claim's EP weight", #10849; and
        `tortoise_create_event`, risk=write with no idempotency declaration).
        On a transport death the outcome is UNKNOWN, so the call STOPS: a retry
        would duplicate a write the door cannot undo, and a silent success
        claim would be the false PASS again.

        ⛔ The two failure classes are DIFFERENT, and their caller must not
        confuse them (#13913 review round 5): a **definite refusal** (the door
        answered no) is a plain `RuntimeError` — nothing was written, so the
        caller un-records its intent — while an **UNCONFIRMED** outcome is
        `UnknownOutcome`, which the caller re-raises WITHOUT un-recording, because
        a re-issue could write the node twice. `--verify` is the authority on
        whether an unconfirmed write landed.
        """
        for attempt in range(6):
            self._next_id += 1
            # ⛔ BOUND BEFORE THE CALL, and re-bound on every attempt. The status
            # arm below reads `headers` on BOTH paths into it — the tuple return
            # (a 2xx/3xx) and the `HTTPError` handler (EVERY 4xx/5xx) — and an
            # unbound or leftover name there is not a small defect: on attempt 0
            # it is `UnboundLocalError`, which aborts the whole `--write` run on
            # the first transient 5xx; on a later attempt it would back off on
            # the PREVIOUS response's `Retry-After` (#13950 review).
            headers: dict = {}
            try:
                code, payload, headers = self._post({
                    "jsonrpc": "2.0", "id": self._next_id, "method": "tools/call",
                    "params": {"name": tool, "arguments": args},
                })
            except urllib.error.HTTPError as exc:
                # ⛔ READING THE BODY CAN FAIL TOO, and this runs INSIDE the
                # handler where no sibling `except` can see it: a truncated or
                # reset body (`IncompleteRead`) raised HERE escaped `call()` as
                # itself, so `write_once` read a 5xx as a definite REFUSAL and
                # retired a non-idempotent intent the server may have acted on —
                # the resume then re-issued `create_operator` (#13913 review
                # round 8, measured). No body is an UNKNOWN body, never a refusal.
                # ⛔ `headers` IS BOUND HERE BECAUSE THIS IS WHERE A REAL 5xx
                # LANDS: `urlopen` raises `HTTPError` for every non-2xx, so the
                # tuple-returning path above is 2xx/3xx only and the status arm's
                # `Retry-After` read would otherwise reach an unbound name on
                # exactly the responses it was written for (measured: a real
                # `HTTPError(503, …, {"Retry-After": "7"})` from `_post` raised
                # `UnboundLocalError` instead of backing off — one POST where
                # `main` made six; #13950 review).
                # ⛔ AND IT IS NORMALISED TO A `dict`: `HTTPError.headers` is an
                # `email.message.Message`, and `_retry_after_seconds` requires a
                # mapping — binding the Message directly would leave the header
                # unread, i.e. the same defect wearing the fix's clothes.
                code, payload = exc.code, None
                _hdrs = getattr(exc, "headers", None)
                try:
                    headers = dict(_hdrs) if _hdrs else {}
                except (TypeError, ValueError):
                    headers = {}
                # ⛔ THE BODY READ IS PART OF THE CLASSIFICATION. This read sits
                # inside `except HTTPError`, so an exception raised HERE is not
                # caught by the sibling `except` below — and it escaped all the
                # way to `write_once`'s refusal arm, which un-recorded a
                # NON-IDEMPOTENT intent the server may well have acted on.
                # Measured over a real socket (a 504 whose declared
                # Content-Length is never delivered): `call(retry_safe=False)`
                # raised `http.client.IncompleteRead`, the marker was
                # un-recorded, and the resume re-issued all 51 operators
                # (#13913 review round 8). A status line without a readable body
                # is an UNKNOWN outcome, never a refusal.
                try:
                    body = exc.read().decode("utf-8", "replace")
                except (OSError, ValueError, http.client.HTTPException) as read_exc:
                    # ⛔ THE STATUS CODE STILL DECIDES. A 4xx (other than 408/429)
                    # was refused BEFORE the handler ran, so an unreadable body
                    # does not make the outcome unknown — the intent may be
                    # retired. Only a 5xx/timeout/rate-limit is UNKNOWN without a
                    # body, because the door may have acted on it.
                    if 400 <= code < 500 and code not in (408, 429):
                        raise Refusal(
                            f"{tool} refused: HTTP {code} — the write did not land "
                            f"(body unreadable: {read_exc})") from read_exc
                    if not retry_safe:
                        raise UnknownOutcome(
                            _unknown(tool, f"HTTP {code} body unreadable: "
                                     f"{read_exc}")) from read_exc
                    if attempt == 5:
                        raise RuntimeError(
                            f"{tool}: transport died reading the error body — "
                            f"{read_exc}") from read_exc
                    time.sleep(min(60, 5 * (attempt + 1)))
                    continue
                try:
                    payload = json.loads(body)
                except json.JSONDecodeError:
                    payload = {"raw": body}
            # ⛔ A BODY WE CANNOT READ IS NOT A REFUSAL. `_post` parses the
            # response itself (JSON, or the last SSE frame), so a truncated body
            # (`IncompleteRead`) or a proxy error page on a 2xx raises `http.client`
            # / `JSONDecodeError` ONE FRAME BELOW this classification. Uncaught,
            # both escaped to `write_once`'s refusal arm, which un-recorded a
            # NON-IDEMPOTENT intent the server may well have acted on — measured:
            # a 2xx with a truncated body re-issued `create_operator` on resume,
            # the #10849 double-count (#13913 review round 7).
            except (urllib.error.URLError, TimeoutError, OSError,
                    http.client.HTTPException, json.JSONDecodeError) as exc:
                if not retry_safe:
                    raise UnknownOutcome(_unknown(tool, exc)) from exc
                if attempt == 5:
                    raise RuntimeError(f"{tool}: transport died — {exc}") from exc
                time.sleep(min(60, 5 * (attempt + 1)))
                continue

            # ⛔ A BODY THAT PARSES TO A NON-OBJECT IS STILL UNREADABLE. A proxy
            # error page can be valid JSON (`[]`, `"Bad Gateway"`, `502`), and
            # `payload.get` then raised `AttributeError` — again outside every
            # classification, again retiring the intent (#13913 review round 8).
            if payload is not None and not isinstance(payload, dict):
                raise UnknownOutcome(
                    f"{tool}: HTTP {code} body is not a JSON-RPC object "
                    f"({type(payload).__name__}) — the write is UNCONFIRMED")
            # ⛔ 408/429 ARE NOT PRE-HANDLER REFUSALS, AND THE STATUS SAYS SO —
            # NOT THE BODY SHAPE: a timeout may have been read and acted on, and
            # a rate limiter can sit INSIDE the door (measured: a 429
            # un-recorded an operator intent and the resume wrote a SECOND
            # operator). This arm used to sit BELOW the `error` arm, so a
            # 504/429/5xx carrying a JSON-RPC `error` envelope — which the
            # control-plane middleware emits (`mcp_auth`: `_jsonrpc_error(...,
            # status=503, headers={"Retry-After": …})`) — matched the refusal
            # channel by SHAPE and retired a non-idempotent intent: measured, a
            # 504 whose body is `{"error": "upstream request timeout"}` left the
            # marker out of `written` AND `unconfirmed`, and the next run POSTed
            # `create_operator` again over endpoints the upstream may have
            # written (#10849). Status first (#13913 review round 17).
            if code >= 500 or code in (408, 429):
                if not retry_safe:
                    raise UnknownOutcome(_unknown(tool, f"HTTP {code}"))
                if attempt < 5:
                    # ⛔ THE ADVICE CAN RIDE EITHER CHANNEL, SO READ BOTH. The
                    # control-plane middleware answers `Retry-After` in HEADERS
                    # and `call()` used to discard the third element of `_post`'s
                    # result; some surfaces answer only in the BODY
                    # (`error.data.retry_after`). Reading the header alone dropped
                    # a server's advertised 30 s back-off and retried 5 s later
                    # (#13950 review round 2, measured) — and the `error` arm that
                    # used to carry the body value sits BELOW this arm, so it can
                    # no longer speak for a non-2xx at all.
                    wait = (_retry_after_seconds(headers)
                            or _body_retry_after(payload))
                    # ⛔ A NON-POSITIVE VALUE IS TREATED AS NO USABLE ADVICE,
                    # DELIBERATELY — not as an accident of truthiness. The guard
                    # is the `if wait else` below (there is no separate clamping
                    # line: an earlier cut had one and a mutant showed it was
                    # DEAD, so it is gone). `Retry-After: 0` legally means "retry
                    # now", and honouring it literally turns this six-attempt loop
                    # into six back-to-back POSTs at a door that has just answered
                    # 429/503: a server-controlled value must not be able to make
                    # the client hammer. So a `0` from the higher-precedence
                    # channel is dropped to the next channel (the body) and the
                    # ramp is the fallback — the conservative reading, which waits
                    # LONGER than advised and never shorter, and never busy-loops
                    # (#13950 review round 3 measured the `0` case; the bounded
                    # over-wait is the accepted trade, stated here so the next
                    # reader does not "fix" the truthiness into a hammer).
                    time.sleep(min(120, wait) if wait else min(60, 5 * (attempt + 1)))
                    continue
                # ⛔ The SIXTH attempt used to FALL THROUGH: a gateway's error
                # body is not a JSON-RPC `result`, so `_refusal` found nothing
                # and the failure was returned as success — 101 edges marked
                # `written` while 50 were never created (#13913 review).
                raise UnknownOutcome(f"{tool}: HTTP {code} after 6 attempts — the "
                                   "write is UNCONFIRMED")
            # ⛔ A 4xx IS NOT AN UNKNOWN OUTCOME. The request was rejected BEFORE
            # the handler ran, so nothing landed — leaving the intent recorded
            # would make it permanent: an auth failure on the operator door
            # recorded 51 unconfirmed intents that no later run could clear, and
            # the only recovery was hand-editing `state.json` (#13913 review
            # round 6). A 2xx/3xx that carries no readable tool result is the
            # opposite case — the server accepted the call and the outcome is
            # genuinely unknown.
            err = (payload or {}).get("error")
            if err:
                retry_after = _body_retry_after(payload)
                if retry_after:
                    # "the work may still complete" — wait the advertised delay,
                    # then re-issue. Only for a write whose repeat converges.
                    if not retry_safe:
                        raise UnknownOutcome(_unknown(tool, err))
                    # ⛔ THE ATTEMPT GUARD EVERY SIBLING ARM HAS. Without it the
                    # SIXTH iteration slept and then `continue`d out of the loop,
                    # so a 120 s back-off was taken for a request no attempt would
                    # make, inside a run that was already ready to report
                    # (#13950 review round 2, measured: 6 posts, 6 sleeps against
                    # every other arm's 6 and 5).
                    if attempt < 5:
                        time.sleep(min(120, retry_after))
                        continue
                    raise UnknownOutcome(
                        f"{tool}: retry_after {retry_after:g}s after 6 attempts — "
                        "the write is UNCONFIRMED")
                raise Refusal(f"{tool} refused: {err}")
            if 400 <= code < 500:
                raise Refusal(
                    f"{tool} refused: HTTP {code} — the write did not land "
                    f"({json.dumps(payload)[:200]})")
            if not payload:
                raise UnknownOutcome(f"{tool}: empty response (HTTP {code})")
            result = payload.get("result", payload)
            # A `result` that is not a tool result at all (a 4xx JSON body such
            # as {"message": "Unauthorized"}) is a FAILURE, not a success: the
            # MCP wire always answers a `tools/call` with `content[]` and/or
            # `structuredContent`. Without this, `_refusal` has nothing to read
            # and the body is returned as if the write had landed.
            if not isinstance(result, dict) or not (
                isinstance(result.get("content"), list)
                or isinstance(result.get("structuredContent"), dict)
            ):
                raise UnknownOutcome(
                    f"{tool}: HTTP {code} carried no tool result — the write is "
                    f"UNCONFIRMED ({json.dumps(payload)[:200]})")
            # ⛔ A TOOL THAT FAILED IS NOT A SUCCESSFUL CALL. MCP reports a
            # handler failure as `result.isError: true` with the reason in
            # `content[]` — the JSON-RPC envelope is still a `result`, so
            # reading `payload["error"]` alone reports a refused write as done.
            # That is the false-PASS this filer exists downstream of: the first
            # run of this map against a degraded API "wrote" all 78 nodes by
            # falling back to each item's own name as its id, and only the
            # Falkor read-back (`--verify`: 0 nodes) showed nothing existed.
            msg = _refusal(result) if isinstance(result, dict) else None
            if msg:
                if ("wait budget" in msg or "retry" in msg.lower()) and attempt < 2:
                    if not retry_safe:
                        raise UnknownOutcome(_unknown(tool, msg))
                    # A degraded data plane does not heal inside one call: two
                    # retries, then abort the RUN so the caller can re-probe.
                    # Grinding through six 60s backoffs per node would spend the
                    # whole window proving the window is closed.
                    time.sleep(min(60, 10 * (attempt + 1)))
                    continue
                # ⛔ A TOOL-LEVEL FAILURE IS NOT A PRE-HANDLER REFUSAL — it is
                # `_safe`'s `_SafeError` (`mcp_server.py`: "Call fn; return an
                # `_SafeError` on exception instead of raising"), which wraps the
                # WHOLE SDK call. `create_operator` lands the node BEFORE the
                # event emit / dirty-mark / cache-invalidate that can raise, so
                # this payload can arrive over a write that SUCCEEDED. Retiring
                # the intent here re-issued all 51 operators on the resume and
                # the door did not stop it (`sdk.py`: a same-mechanism holder is
                # `continue`d past), so the run reported `all nodes and edges
                # written` over every operator priced twice (#10849) — measured
                # with a real `Mcp.call` and a stubbed `_post` (#13913 review
                # round 13). Only a repeat-safe door may treat it as a refusal.
                if not retry_safe:
                    raise UnknownOutcome(_unknown(tool, msg))
                raise Refusal(f"{tool} FAILED: {msg[:400]}")
            return result
        raise UnknownOutcome(f"{tool}: gave up after 6 attempts")


class UnknownOutcome(RuntimeError):
    """The transport died before the door answered — the write MAY have landed.

    A distinct type on purpose: the caller must NOT retire the intent marker for
    this class (a re-run would re-issue a non-idempotent write), while a definite
    refusal must (the write certainly did not land). It is a TYPE, not a message
    match, because the pre-fix `except Exception: unmark(marker)` fired on BOTH
    classes and the resume then made a second `create_operator` over the same
    endpoints — the #10849 EP double-count, printed as `all nodes and edges
    written` (#13913 review round 5).
    """


class Refusal(RuntimeError):
    """The door ANSWERED NO — nothing was written, so the intent may be re-issued.

    ⛔ THE INVERSE OF `UnknownOutcome`, AND THE DEFAULT IS THE OTHER WAY ROUND.
    `write_once` used to un-record its intent on "any exception that is not
    `UnknownOutcome`", so an UNCLASSIFIED exception (a body read that raised, a
    bug in this module) counted as a refusal and the resume re-issued a
    non-idempotent write the server may have acted on — measured: an
    `IncompleteRead` inside the `HTTPError` handler re-issued all 51 operators
    (#13913 review round 8). Only THIS type un-records; anything else leaves the
    intent recorded, because a wrongly-kept intent is recoverable (the remedy is
    printed and `--verify` decides) and a wrongly-retired one duplicates a node
    the door cannot dedup.
    """


# ⛔ THE SPELLINGS THIS MODULE'S OWN UNKNOWN/UNCONFIRMED RAISES PRODUCE — ONE
# definition, read by the exit summary. `Refusal` vs `UnknownOutcome` is this
# module's central distinction, and the summary must not bucket a
# MAY-HAVE-LANDED write as "not written": that invites a re-issue of a
# non-idempotent `create_operator` over endpoints that may already be joined,
# which is #10849's EP double-count. Three raise sites spell it three ways, so
# the list is named rather than inlined — a NEW raise site must add its spelling
# here instead of silently falling into the not-written bucket (#13950 review
# round 3, which caught the first cut matching only two of the three).
_UNKNOWN_OUTCOME_MARKERS = (
    "outcome UNKNOWN",       # `_unknown(...)`
    "outcome UNCONFIRMED",   # the `already(...) and marker in pending` arm
    "write is UNCONFIRMED",  # `UnknownOutcome` raised by `Mcp.call`
)


def _unknown(tool: str, reason: object) -> str:
    return (f"{tool}: outcome UNKNOWN ({reason}) — this write is NOT idempotent, so "
            "it is not retried; re-run and let --verify decide what landed")


def _seconds_or_none(value: object) -> float | None:
    """A `Retry-After` value in seconds, or None when it is not one.

    ⛔ ONE CONVERTER FOR BOTH CHANNELS — the header (`Retry-After`) and the body
    (`error.data.retry_after`). The module's fail-closed contract is decided by
    exception TYPE (`Refusal` vs `UnknownOutcome`), so an unclassified raise is
    the same defeat as a wrong verdict: a bare `float()` on a non-numeric body
    value (`"10s"`, `[30]`) raised `ValueError` straight out of `call()`, and the
    node loops call `mcp.call` with no `try`, so `--write` aborted on its FIRST
    write with `state.json` still `{}` — no summary, no ledger, no exit code
    (#13950 review round 2, measured). `_retry_after_seconds` already promised
    "never a raise"; the body path did not, because it used `float` directly.
    """
    # `bool` is an `int` in Python, and `Retry-After: True` is not a wait.
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError, OverflowError):
        # ⛔ `OverflowError` IS THE SAME DEFECT IN ANOTHER DRESS: a JSON integer
        # too large for a float (`10**400`) raises it from `float()`, and an
        # uncaught one escapes `call()` unclassified — aborting a `--write` run
        # on its first write with no summary, which is exactly what the
        # `ValueError` arm above closes. A STRING cannot reach it
        # (`float("1e400")` is `inf`, refused below); the integer path can
        # (#13950 review round 3).
        return None
    # ⛔ `nan`/`inf` ARE NOT A WAIT. `min(120, nan)` is NOT 120 — every
    # comparison against `nan` is False, so `min` returns its FIRST argument and
    # the cap silently does nothing — and an infinite back-off is not advice.
    if seconds != seconds or seconds in (float("inf"), float("-inf")):
        return None
    return max(0.0, seconds)


def _retry_after_seconds(headers: object) -> float | None:
    """The `Retry-After` a transport answered with, in seconds (or None).

    `mcp_auth`'s control-plane answers carry the back-off as a HEADER
    (`headers={"Retry-After": …}`) while their JSON-RPC body's `data` may hold
    none, so reading only `data["retry_after"]` ignored the server's own advice.

    ⛔ REQUIRES A `dict`, DELIBERATELY: `HTTPError.headers` is an
    `email.message.Message`, and a caller that binds that directly gets `None`
    back — the fix silently not fixing anything. `Mcp.call` normalises at the one
    place a real `HTTPError` is caught, and the strict guard is what makes that
    normalisation load-bearing rather than decorative.
    """
    if not isinstance(headers, dict):
        return None
    for key, value in headers.items():
        if str(key).lower() != "retry-after":
            continue
        return _seconds_or_none(value)
    return None


def _body_retry_after(payload: object) -> float | None:
    """The `retry_after` a JSON-RPC error body advertised, in seconds (or None).

    The SECOND channel for the same advice. It is read on the status arm as well
    as the `error` arm because the status arm sits ABOVE the `error` arm — so a
    non-2xx whose body names a delay but whose headers carry no `Retry-After`
    would otherwise reach the status arm and be retried on the client's own
    5-second ramp, silently dropping the server's advertised back-off (#13950
    review round 2).
    """
    if not isinstance(payload, dict):
        return None
    err = payload.get("error")
    data = err.get("data") if isinstance(err, dict) else None
    if not isinstance(data, dict):
        return None
    return _seconds_or_none(data.get("retry_after"))


def _mechanism_mismatch(result: dict) -> str | None:
    """The `mechanism_mismatch` warning a write returned, if any.

    ⛔ The write gate warns and PROCEEDS (#9963, warn-not-block), so an operator
    whose `op_type` contradicts its declared `semantics` is stored as asked and
    INVERTS what the meaning says (`sdk.py`: "An IMPL wire carrying a NAND
    meaning RAISES the target's confidence where the meaning says it must LOWER
    it"). `validate()` refuses the pair before the write; this catches the case
    where the surface disagrees with the map anyway — the one class a filer must
    never report as filed.
    """
    payload = _json_of(result)
    if not isinstance(payload, dict):
        return None
    for warn in payload.get("warnings") or []:
        if isinstance(warn, dict) and warn.get("code") == "mechanism_mismatch":
            return f"mechanism_mismatch — {str(warn.get('message'))[:300]}"
    return None


def _refusal(result: dict) -> str | None:
    """The reason, when a `tools/call` result reports a refusal — BOTH channels.

    The spec's `isError` flag is not the only one. This server's handlers return
    `_SafeError(...)` — a dict SUBCLASS whose wire shape is unchanged — so a
    validation or quota refusal arrives as a SUCCESSFUL result whose payload is
    `{"error": …}` with `isError: false` (`mcp_server.py`'s `_SafeError`
    docstring: "the wire shape is unchanged"). Reading `isError` alone therefore
    marked refused EDGE writes as filed, which is #13880's false PASS one layer
    down.
    """
    if result.get("isError"):
        return _text(result) or "no reason given"
    payload = _json_of(result)
    if isinstance(payload, dict) and payload.get("error"):
        return str(payload["error"])
    return None


def _text(result: dict) -> str:
    """MCP tool results carry a content[] of text blocks."""
    parts = []
    for blk in result.get("content") or []:
        if isinstance(blk, dict) and blk.get("type") == "text":
            parts.append(blk.get("text", ""))
    return "\n".join(parts)


def _json_of(result: dict) -> dict:
    """Prefer structuredContent; fall back to parsing the text block."""
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    txt = _text(result)
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        return {"_text": txt}


def _id_of(result: dict) -> str | None:
    d = _json_of(result)
    for key in ("id", "node_id", "entity_id", "object_id", "subject_id", "point_id"):
        v = d.get(key)
        if isinstance(v, str) and v:
            return v
    # nested shapes: {"point": {"id": ...}} / {"result": {...}}
    for key in ("point", "object", "subject", "source", "event", "result", "data"):
        v = d.get(key)
        if isinstance(v, dict):
            got = _id_of({"structuredContent": v})
            if got:
                return got
    return None


# ── the writer ────────────────────────────────────────────────────────────
def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"ids": {}, "written": [], "failed": []}


def save_state(st: dict) -> None:
    """Write the resume record ATOMICALLY.

    The record is what the duplicate-safety argument rests on: a kill inside a
    plain `write_text` truncates it, and the next run loses the recorded intents
    — which is how a truncated state file re-issues the non-idempotent writes the
    record exists to protect.
    """
    tmp = STATE_PATH.with_name(STATE_PATH.name + ".tmp")
    tmp.write_text(json.dumps(st, indent=1, sort_keys=True) + "\n")
    os.replace(tmp, STATE_PATH)


def write_map(doc: dict) -> int:
    key = os.environ.get("TORTOISE_MCP_API_KEY")
    if not key:
        print("TORTOISE_MCP_API_KEY is not set — load /home/cmux/businessmap.env first",
              file=sys.stderr)
        return 2
    mcp = Mcp(key)
    mcp.initialize()
    st = load_state()
    st.setdefault("unconfirmed", [])
    ids: dict[str, str] = st["ids"]
    failures: list[str] = []
    ambiguous: list[str] = []
    # ⛔ SNAPSHOT THE LEDGER AS LOADED, because a mid-run arm MUTATES `st`.
    # The `mechanism_mismatch` arm saves `st["failed"] = old ∪ failures` while it
    # runs (it must — a run killed before the final save would lose the only
    # record of an inverted operator), so by the time the remedy block computes
    # `carried` from `st["failed"]`, that list already holds THIS RUN's own
    # declined/unbound lines. Their markers never enter `done`, so they were then
    # re-labelled as "marker(s) from an earlier run" — a directive to go looking
    # for prior-run state that does not exist (#13950 review round 3, measured on
    # an 11-mismatched + 9-declined run with `failed: []` on disk: it printed
    # `9 marker(s) from an earlier run are STILL unresolved`). `carried` is a
    # statement about what was on disk BEFORE this run, so it reads that.
    failed_before: list[str] = list(st.get("failed") or [])

    # ⛔ A NAME IS NOT AN ID, and `state.json` is the one place that claim is
    # taken on trust. The pre-fix run recorded every item's own name/url/content
    # as its "id" (that is what the false PASS looked like on disk); a resume
    # over that file finds every marker present, prints "all nodes and edges
    # written" and exits 0 with nothing filed. So the resume state is CHECKED
    # before it is believed — the read-back's discipline applied to the only
    # input it cannot see.
    named = [(ref, got) for ref, got in ids.items() if _is_identity_of(doc, ref, got)]
    if named:
        ref, got = named[0]
        print(f"state.json is not a filing record: {len(named)} id(s) are the "
              f"item's OWN identity, not an id the door returned "
              f"(e.g. {ref!r} -> {got[:60]!r}). Delete {STATE_PATH} and re-run "
              f"--write; resuming over it would report success over an empty "
              f"graph.", file=sys.stderr)
        return 2

    declared_markers = {edge_marker(e) for e in doc["edges"]}
    for section in NODE_MARKER_SECTIONS:
        declared_markers.update(item_marker(section, item)
                                for item in doc[section])
    orphan_markers = sorted(m for m in st["written"] if m not in declared_markers)
    if orphan_markers:
        edge_orphans = [m for m in orphan_markers if m.startswith("edge:")]
        node_orphans = [m for m in orphan_markers if not m.startswith("edge:")]
        print(f"state.json names {len(orphan_markers)} marker(s) this map does not "
              f"declare (e.g. {orphan_markers[0]!r}) — a node's declared identity, "
              f"or an edge's endpoints/mechanism/direction/label, changed since it "
              f"was written, or the markers are an older spelling. Resuming would "
              f"skip the edited items and re-issue the rest, so the run stops.",
              file=sys.stderr)
        if edge_orphans:
            print("  STRUCTURAL edges: remove those entries from "
                  f"{STATE_PATH} (KEEP the ids) and re-run.\n"
                  "  ⛔ OPERATOR edges: removing the marker RE-ISSUES "
                  "`tortoise_create_operator`, and the door has no existence "
                  "check — it would write a SECOND operator over endpoints that "
                  "already carry one, double-counting the claim's EP weight "
                  "(#10849). The existing operator must be deleted first; this "
                  "filer never deletes.", file=sys.stderr)
        if node_orphans:
            print("  NODES: this filer never updates or deletes a node, so an "
                  "edited identity cannot be repaired from here — restore the "
                  "declaration, or delete the node in the graph first. Removing "
                  "the marker re-creates the node (a duplicate) for every kind "
                  "without a dedup key.", file=sys.stderr)
        return 2

    #: Markers whose write was ATTEMPTED and whose outcome is still unknown:
    #: recorded in `state.json` so a resume can REPORT them (an unconfirmed
    #: write is neither re-issued nor claimed as filed).
    pending: set[str] = set(st.setdefault("unconfirmed", []))

    def already(marker: str) -> bool:
        return marker in st["written"]

    #: Markers THIS run confirmed. A prior failure clears only against one of
    #: these — see the `failed` ledger at the end of this function.
    done: list[str] = []

    def mark(marker: str) -> None:
        if marker not in st["written"]:
            st["written"].append(marker)
        if marker not in done:
            done.append(marker)
        save_state(st)

    def unmark(marker: str) -> None:
        if marker in st["written"]:
            st["written"].remove(marker)
            save_state(st)

    def write_once(marker: str, tool: str, args: dict) -> dict:
        """A NON-IDEMPOTENT write: the intent is recorded BEFORE the call.

        `create_operator` and `create_event` have no existence check, so a
        retry — or a run killed between the write and its response — writes a
        SECOND node, and a duplicate operator double-counts the claim's EP
        weight (#10849). Recording the intent first makes that window fail
        CLOSED on the duplicate side.

        ⛔ THE TWO EXITS ARE NOT THE SAME EXIT. A **definite refusal** (the door
        answered no) means nothing landed, so the intent is un-recorded and the
        write may be re-issued. An **UNCONFIRMED** outcome (`UnknownOutcome`) may
        have landed, so the intent STAYS recorded and is reported as unresolved:
        un-recording it there is what re-issued a second operator on resume
        (#13913 review round 5). `--verify` is the authority on what landed.
        """
        # ⛔ PERSISTED BEFORE THE CALL, IN ONE SAVE. `pending` is a COPY of the
        # saved list, so saving `st` without updating it left the intent only in
        # memory (round 6); and calling `mark()` — which saves — before updating
        # it left a WINDOW in which the disk held `written` = +marker with
        # `unconfirmed` = [] (round 9, measured by killing inside that save): a
        # resume read the intent as a COMPLETED write and skipped the operator
        # forever. Both fields go down together, once.
        pending.add(marker)
        if marker not in st["written"]:
            st["written"].append(marker)
        st["unconfirmed"] = sorted(pending)
        save_state(st)
        try:
            res = mcp.call(tool, args, retry_safe=False)
        except UnknownOutcome:
            # It MAY have landed: the marker STAYS, so the next run does not
            # re-issue a write this door cannot dedup (`create_operator` has no
            # existence check). `--verify` is the authority on whether it did.
            # `pending` is what tells a resume this marker is an UNKNOWN
            # outcome rather than a completed write — without it the resume
            # skipped it silently and printed `all nodes and edges written`.
            raise
        except Refusal:
            pending.discard(marker)
            unmark(marker)      # the door answered no: the write certainly did not land
            raise
        # ⛔ NO GENERIC `except Exception` ARM, ON PURPOSE. An UNCLASSIFIED
        # exception leaves the intent recorded: a wrongly-kept intent is
        # recoverable (the report names the remedy and `--verify` decides), while
        # a wrongly-retired one re-issues a non-idempotent write over endpoints
        # the door cannot dedup (#10849) — measured on an `IncompleteRead` that
        # escaped the classification and re-issued all 51 operators (#13913
        # review round 8). Only a `Refusal` proves nothing was written.
        pending.discard(marker)
        st["unconfirmed"] = sorted(pending)
        save_state(st)
        return res

    def _resolve(item_ref: str, name: str, res: dict, what: str) -> str:
        """The id the write returned — or a refusal, never a plausible stand-in.

        A name is not an id: binding an edge endpoint to a name writes an edge
        between nodes that do not exist (or none at all), which reads as filed
        and is not. So a missing id stops the run and says which item.
        """
        got = _id_of(res)
        if not got:
            raise RuntimeError(
                f"{what} {item_ref!r} ({name[:40]!r}) returned no id — "
                f"response: {json.dumps(_json_of(res))[:300]}"
            )
        return got

    # 1. Subjects — the actors.
    for s in doc["subjects"]:
        if already(item_marker("subjects", s)) and s["ref"] in ids:
            continue
        res = mcp.call("tortoise_create_subject",
                       {"name": s["name"], "subjectKind": s["subjectKind"]})
        ids[s["ref"]] = _resolve(s["ref"], s["name"], res, "subject")
        mark(item_marker("subjects", s))
        print(f"  subject  {s['name']:<34} -> {ids[s['ref']]}")

    # 2. Objects — products, competitors, programmes, goals, standards.
    #    The lifecycle `status` is a tenant PROP, not a parameter of this door
    #    (`tortoise_create_object(name, objectKind, props, goalState)`);
    #    `goalState` is the one object field the surface names explicitly.
    for o in doc["objects"]:
        if already(item_marker("objects", o)) and o["ref"] in ids:
            continue
        args = {"name": o["name"], "objectKind": o["objectKind"]}
        if o.get("status"):
            args["props"] = {"status": o["status"]}
        if o.get("goalState"):
            args["goalState"] = o["goalState"]
        res = mcp.call("tortoise_create_object", args)
        ids[o["ref"]] = _resolve(o["ref"], o["name"], res, "object")
        mark(item_marker("objects", o))
        print(f"  object   {o['name'][:44]:<44} -> {ids[o['ref']]}")

    # 3. Sources — the documents claims were read from.
    #    `documentKind` is caller-settable but NOT a parameter of
    #    `tortoise_create_source(url, sourceKind, tier, sourceDate, props)`.
    for s in doc["sources"]:
        if already(item_marker("sources", s)) and s["ref"] in ids:
            continue
        args = {"url": s["url"], "sourceKind": s["sourceKind"]}
        if s.get("documentKind"):
            args["props"] = {"documentKind": s["documentKind"]}
        res = mcp.call("tortoise_create_source", args)
        ids[s["ref"]] = _resolve(s["ref"], s["url"], res, "source")
        mark(item_marker("sources", s))
        print(f"  source   {s['url'][:60]:<60} -> {ids[s['ref']]}")

    # 4. Events — decisions belong on the timeline, not in the belief layer.
    #    `tortoise_create_event(name, eventKind, props)`: the content and the
    #    start time are PROPS, and `name` is required.
    for e in doc["events"]:
        marker = item_marker("events", e)
        if already(marker):
            if e["ref"] in ids:
                pending.discard(marker)   # a resolved id confirms the write
                continue
            # The intent was recorded but no id came back (a run killed between
            # the write and the id assignment, or `_resolve` raising after a
            # landed write). Re-creating would mint a SECOND Event carrying the
            # same identity — and `create_event` has no existence check — so the
            # ref is reported as AMBIGUOUS instead, never silently re-written
            # (#13913 review round 5: the pre-fix guard fell through and
            # re-issued `create_event`).
            ambiguous.append(marker)
            continue
        props = {"content": e["content"]}
        if e.get("startedAt"):
            props["startedAt"] = e["startedAt"]
        res = write_once(item_marker("events", e), "tortoise_create_event",
                         {"name": event_name(e), "eventKind": e["eventKind"],
                          "props": props})
        ids[e["ref"]] = _resolve(e["ref"], e["content"], res, "event")
        print(f"  event    {e['ref']:<34} -> {ids[e['ref']]}")

    # 5. Points — the claims.
    #    Provenance to a Source is `props.extractedFrom` (the contract
    #    `create_point` validates and `_link_source` reads); there is no
    #    `source_url` parameter on this surface.
    for p in doc["points"]:
        if already(item_marker("points", p)) and p["ref"] in ids:
            continue
        args = {"content": p["content"], "kind": p.get("kind", "statement"), "dedup": True}
        src = p.get("source")
        if src:
            args["props"] = {"extractedFrom": doc_by_ref(doc, src)["url"]}
        res = mcp.call("tortoise_create_point", args)
        ids[p["ref"]] = _resolve(p["ref"], p["content"], res, "point")
        mark(item_marker("points", p))
        print(f"  point    {p['ref']:<34} -> {ids[p['ref']]}")

    # 6. Edges — the whole point. Operators carry a mechanism + a meaning;
    #    structural relations are plain typed edges.
    #    Spellings are the surface's own: `source_id`/`target_ids` on the
    #    operator door, `source_id`/`target_id`/`predicate` on the edge door.
    for e in doc["edges"]:
        marker = edge_marker(e)
        if already(marker):
            if "op" in e and marker in pending:
                # Same rule as an unresolved event: the operator may exist, and
                # re-issuing it double-counts the claim's EP weight (#10849).
                failures.append(
                    f"{marker}: attempted, outcome UNCONFIRMED — NOT re-issued. "
                    "Run --verify: if the edge is present it landed — delete this "
                    "marker from `unconfirmed` in state.json, KEEP it in `written`, "
                    "and delete the matching line from `failed`; if it is absent, "
                    "delete it from `written` (and its `failed` line) and re-run.")
                print(f"  !! {failures[-1]}", file=sys.stderr)
            continue
        src, dst = ids.get(e["from"]), ids.get(e["to"])
        if not src or not dst:
            # ⛔ PRINTED, not merely recorded: this is the ONLY trace of a
            # declared edge that was never attempted, and a failure that exists
            # only in `state.json` is one an operator reads about after the run
            # has already reported what it did (#13913 review round 17).
            failures.append(f"{marker}: unbound endpoint {e['from']}/{e['to']}")
            print(f"  !! {failures[-1]}", file=sys.stderr)
            continue
        try:
            if "op" in e:
                args = {"op_type": e["op"], "source_id": src, "target_ids": [dst]}
                if e.get("label"):
                    args["semantics"] = e["label"]
                if e.get("direction"):
                    args["direction"] = e["direction"]
                res = write_once(marker, "tortoise_create_operator", args)
                mismatch = _mechanism_mismatch(res)
                if mismatch:
                    # The write LANDED (warn-not-block, #9963), so the marker
                    # stays — un-recording would re-issue a non-idempotent write
                    # — but the run must NOT report success: the mechanism
                    # stored is not the one the meaning declares, so the
                    # judgement is filed INVERTED and only a re-filing fixes it.
                    failures.append(f"{marker}: {mismatch}")
                    # ⛔ PRINTED, like every other failure class: this arm and the
                    # two below are the ONLY producers of a `failed` line, and the
                    # other two print. Silently appending left the `mismatch` arm's
                    # diagnosis in `state.json` alone on the run that recorded it —
                    # the very condition the unbound arm's print exists to remove
                    # (#13950 review round 2, measured: a run that wrote all 51
                    # operators against a warn-not-block surface reported
                    # `51 edge(s) not written` and named `mechanism_mismatch` ZERO
                    # times on stderr).
                    print(f"  !! {failures[-1]}", file=sys.stderr)
                    # ⛔ PERSISTED AS IT IS OBSERVED, not at the end of the run.
                    # `failures` is a LOCAL list: `mark()`/`write_once()` save
                    # `st` while `st["failed"]` still holds the value loaded from
                    # disk, so a run KILLED between this arm and the final save
                    # left `failed: []` — and the resume, which re-observes
                    # nothing, then printed `all nodes and edges written` with
                    # exit 0 over an operator filed with the INVERTED mechanism
                    # and no other record of it. `--verify` cannot see it (this
                    # module's own stated limitation: the surface stores no
                    # Layer-2 `label`) (#13913 review round 12).
                    st["failed"] = sorted({*(st.get("failed") or []),
                                           *failures})
                    save_state(st)
            else:
                res = mcp.call("tortoise_create_edge",
                               {"source_id": src, "target_id": dst,
                                "predicate": e["relation"]})
                # ⛔ THE DOOR DECLINES WITHOUT RAISING, SO THE RESULT IS THE
                # ONLY WITNESS. `create_edge` answers `{edge, created, nudges}`
                # and `created` is `False` — no exception, no error payload —
                # when the writer cannot resolve an endpoint
                # (`projection/edges.py`: `if not sources or not targets:
                # return False`). Discarding the result marked the edge
                # `written` and printed the success line over a write that
                # never happened, and the marker then made every resume skip
                # it (#13913 review round 14). This is the same false PASS round
                # 3 fixed one layer up (101 edges marked `written` while the
                # structural ones were never created), still open on the door's
                # own declined-write channel. Reachable without a synthetic
                # transport: `ids` is taken on trust from a gitignored,
                # machine-local `state.json`, so a resume against a graph that
                # was reset or re-tenanted writes zero nodes and every
                # structural edge comes back `created: false`.
                # ⛔ THE KEY'S PRESENCE IS WHAT MAKES IT READABLE: the surface
                # always answers `created`, so a payload that carries it and says
                # `false` is a DECLINE and is treated as one. A payload without
                # the key is left to `--verify` — the one residual this guard
                # does not close, stated rather than implied (#13913 review
                # round 14, second pass).
                got = _json_of(res)
                if "created" in got and got.get("created") is not True:
                    failures.append(
                        f"{marker}: create_edge returned created="
                        f"{got.get('created')!r} — the edge was NOT written, so "
                        "it is NOT marked; the endpoints it names may not exist")
                    print(f"  !! {failures[-1]}", file=sys.stderr)
                    continue
            mark(marker)
        except Exception as exc:  # recorded in `failures`, never swallowed
            failures.append(f"{marker}: {e.get('op') or e.get('relation')} "
                            f"{e['from']}->{e['to']}: {exc}")
            print(f"  !! {failures[-1]}", file=sys.stderr)
    # ⛔ A RESUME MUST NOT ERASE A DIAGNOSIS IT DID NOT RE-OBSERVE. `failed` was
    # overwritten unconditionally, so a run that resolved nothing but also failed
    # nothing exited 0 with `failed: []` — measured on a mechanism INVERSION
    # (`mechanism_mismatch`), which `--verify` cannot see by this module's own
    # declared limitation: run 1 failed loudly, run 2 (a resume) printed `all
    # nodes and edges written` and erased the only record that the graph stores an
    # operator meaning the opposite of the judgement (#13913 review round 10).
    # A failure is cleared only when THIS run re-wrote its marker successfully.
    carried = [f for f in failed_before
               if not any(f.startswith(marker) for marker in done)]
    st["failed"] = sorted({*carried, *failures})
    st["unconfirmed"] = sorted(pending)
    save_state(st)
    # ⛔ THE REMEDY BLOCK IS REACHED BY A DECLINED EDGE TOO. A declined
    # `create_edge` wrote NOTHING and has NO marker, so it is not `carried` — but
    # it exits 1, and its only printed line names the failure without a route out
    # (#13913 review round 18).
    declined = [f for f in failures if "create_edge returned created=" in f]
    # ⛔ THE UNBOUND-ENDPOINT CLASS IS STUCK IN THE SAME WAY, and gating on
    # `carried`/`declined` alone skipped it on the one run that matters: the run
    # that FIRST records it, where `carried` is empty (the line is this run's own)
    # and nothing was `declined` — so the exit below was never printed and the
    # `!!` line was the whole trace, i.e. exactly the "names the failure without a
    # route out" state this block exists to remove (#13950 review, measured on the
    # recorded-intent-without-id resume the unbound pin builds).
    unbound = [f for f in failures if "unbound endpoint" in f]
    # ⛔ THE THIRD NO-ROUTE-OUT CLASS. A `mechanism_mismatch` DID land
    # (warn-not-block) and stores the OPPOSITE meaning, so it is the one class
    # whose remedy is bullet 3 rather than "nothing was written" — but the gate
    # could not reach it either: the arm calls `mark(marker)`, so the marker
    # enters `done`, so `carried` drops its line, and it matches neither
    # `declined` nor `unbound`. The remedy was therefore printed only on a
    # SECOND run (#13950 review round 2).
    mismatched = [f for f in failures if "mechanism_mismatch" in f]
    if carried or declined or unbound or mismatched:
        # ⛔ A CARRIED FAILURE HAS NO AUTOMATIC EXIT: its marker stays in
        # `written`, so it is never re-attempted, so `done` never contains it, so
        # the line can never clear itself. Saying so — with the exact edit — is
        # the difference between a fail-closed report and a permanent false red
        # with no route out (#13913 review round 11, measured: the printed remedy
        # for the UNCONFIRMED arm cleared `unconfirmed` and the run still exited 1
        # forever, because nothing named the `failed` line).
        # ⛔ NOT TWICE: a line this run re-observed and re-failed was already
        # printed as `!!` in the loop that produced it. Repeating it here reads
        # as a second, unrelated failure (#13913 review round 18).
        # Keyed on the MARKER, not the whole sentence: an earlier run's wording
        # for the same marker differs from today's, and the duplicate reads as a
        # second, unrelated failure either way.
        reobserved = {f.split(": ", 1)[0] for f in failures}
        repeated = [line for line in carried
                    if line.split(": ", 1)[0] not in reobserved]
        if carried:
            # ⛔ COUNT MARKERS, NOT LINES: the dedupe below is marker-keyed, so a
            # count of LINES can exceed what is listed and the parenthetical then
            # claims a line was printed that was not (#13913 review round 19).
            markers_carried = {line.split(": ", 1)[0] for line in carried}
            print(f"\n{len(markers_carried)} marker(s) from an earlier run are "
                  "STILL unresolved (their markers were not re-written this run) "
                  "— see state.json", file=sys.stderr)
        # ⛔ ONE HEADER PER NON-EMPTY CLASS, not an `else` chain: a run can record
        # a declined edge AND an unbound one, and an `elif` silently drops the
        # second class's count (#13950 review).
        if declined:
            print(f"\n{len(declined)} edge(s) were DECLINED by the door — nothing "
                  "was written for them (see `failed` in state.json)",
                  file=sys.stderr)
        if unbound:
            print(f"\n{len(unbound)} edge(s) hit an UNBOUND endpoint — nothing "
                  "was written for them (see `failed` in state.json)",
                  file=sys.stderr)
        if mismatched:
            print(f"\n{len(mismatched)} operator(s) LANDED with a mechanism the "
                  "judgement does not declare — the write DID happen, so its "
                  "marker stays in `written` (see `failed` in state.json)",
                  file=sys.stderr)
        for line in repeated[:10]:
            print(f"  ! {line}", file=sys.stderr)
        if not repeated:
            # ⛔ THE DECLINED CLASS HAS NO MARKER, so it must not be told one
            # exists — the sentence directly above says "NO marker exists", and a
            # reader who believes otherwise lands on bullet 3's "keep its marker
            # in `written`", a line that can never clear itself (#13913 review
            # round 20).
            print("  (…each of those was re-reported above, in this run's own "
                  "`!!` lines)", file=sys.stderr)
        # ⛔ THE REMEDY IS PER CLASS. The marker-bearing lines (a landed write
        # whose follow-up step failed), the `unbound endpoint` lines and the
        # `create_edge returned created=` lines (both writes that never happened,
        # with different edits) have DIFFERENT exits, and printing one of them for
        # another is a permanent false red with a wrong instruction (#13913 review
        # rounds 12 and 18).
        print("  → to resolve one, `--verify` decides what is in the graph:\n"
              "    * line says `unbound endpoint`: nothing was written for the "
              "EDGE, and its endpoint is missing an id. A plain re-run does NOT "
              "clear this when the endpoint is a recorded Event — file the node "
              "that WAS written, or (the load-bearing edit) record the landed "
              "node's id under the item's ref in `ids`; then delete this line "
              "from `failed`.\n"
              "    * line says `create_edge returned created=`: NOTHING was "
              "written and NO marker exists — this is a line an earlier run "
              "recorded over a write the door DECLINED. Delete this line from "
              "`failed` and do NOT add a marker to `written`: the next run "
              "re-attempts the edge and clears it itself once the endpoints "
              "resolve.\n"
              "    * otherwise the write landed: if it is correct, delete this "
              "line from `failed` and keep its marker in `written`; if it is wrong "
              "(e.g. a mechanism inversion, which `--verify` cannot see), delete "
              "the node in the graph, then delete its marker from `written` and "
              "this line from `failed`, and re-run. If NO marker for this line is "
              "in `written` at all — an exception before the write, say — the "
              "write did not land: delete the line and re-run.\n"
              "    Never delete `state.json` to clear these — a re-run would "
              "re-issue every non-idempotent write.", file=sys.stderr)
        # A failure whose marker the map no longer declares can never be
        # re-observed either: name it, instead of blocking `--write` silently
        # (#13913 review round 11).
        declared_markers = {edge_marker(e) for e in doc["edges"]}
        declared_markers |= {f"event:{e['ref']}" for e in doc["events"]}
        for line in carried:
            marker = line.split(": ", 1)[0]
            if marker.startswith(("edge:", "event:")) and marker not in declared_markers:
                print(f"  ? {marker} is no longer declared by this map — that line "
                      "can never clear itself; drop it from `failed` in "
                      "state.json", file=sys.stderr)
    if ambiguous:
        # ⛔ NOT "drop its marker" — that re-files a non-idempotent write, the
        # duplicate this sentence exists to forbid. The state the door leaves is
        # an ID problem, and the fix is to supply the id (#13913 review round 12).
        print(f"\n{len(ambiguous)} item(s) were recorded but returned no id — an "
              "UNKNOWN outcome, NOT re-written (a second write would duplicate a "
              "non-idempotent node). Run --verify to see whether it landed. If it "
              "did, the node's id is what is missing: record that id under this "
              "item's ref in `ids` in state.json and re-run (the resume then "
              "resolves it without writing). Only if `--verify` shows the node is "
              "ABSENT should you drop the marker — and then delete the `failed` "
              "lines of the EDGES that referenced it (an ambiguous item has no "
              "line of its own; its edges do).", file=sys.stderr)
        for marker in ambiguous:
            print(f"  ? {marker}", file=sys.stderr)
    if failures:
        # ⛔ NOT "not written" FOR A LANDED WRITE. `mechanism_mismatch` DID write
        # (warn-not-block) and stored the OPPOSITE meaning, so one "not written"
        # verb inverts the state the operator has to repair — they would re-file a
        # node that exists instead of removing the inverted operator (#13950
        # review round 2, measured: a run that wrote all 51 operators printed
        # `51 edge(s) not written`). The sentence names each class that is
        # present, because no single verb is true of both.
        # ⛔ AND "not written" IS A THIRD LIE FOR THE UNKNOWN CLASS. `Refusal` vs
        # `UnknownOutcome` is this module's central distinction: an UNKNOWN write
        # MAY HAVE LANDED, and `--verify` is the only authority on whether it did.
        # An arithmetic bucket of "everything that is not a mismatch" told the
        # operator it did NOT land while the `!!` line directly above said it may
        # have — inviting a re-issue of a non-idempotent `create_operator` over
        # endpoints that may already be joined (#10849's EP double-count, the very
        # thing `retry_safe=False` exists to prevent) (#13950 review round 3).
        mismatched_n = sum(1 for f in failures if "mechanism_mismatch" in f)
        unknown_n = sum(1 for f in failures
                        if any(m in f for m in _UNKNOWN_OUTCOME_MARKERS))
        unwritten_n = len(failures) - mismatched_n - unknown_n
        parts = []
        if unwritten_n:
            parts.append(f"{unwritten_n} edge(s) NOT written")
        if mismatched_n:
            parts.append(f"{mismatched_n} operator(s) WRITTEN with a mechanism "
                         "the judgement does not declare (filed INVERTED)")
        if unknown_n:
            parts.append(f"{unknown_n} edge(s) MAY HAVE LANDED (outcome UNKNOWN — "
                         "`--verify` decides; do NOT re-file)")
        print(f"\n{len(failures)} edge(s) failed — {'; '.join(parts)} — "
              "recorded in state.json", file=sys.stderr)
        return 1
    if ambiguous or carried:
        return 1
    print("\nall nodes and edges written")
    return 0


def doc_by_ref(doc: dict, ref: str) -> dict:
    for section in ("subjects", "objects", "sources", "events", "points"):
        for item in doc[section]:
            if item["ref"] == ref:
                return item
    raise KeyError(ref)


def _is_identity_of(doc: dict, ref: str, value: str) -> bool:
    """Is `value` this ref's own identity — i.e. a name masquerading as an id?"""
    try:
        item = doc_by_ref(doc, ref)
    except KeyError:
        return False
    if "url" in item:
        # ⛔ A Source's `id` IS its url (`entities.py`: `s.id = coalesce($id, $url)`),
        # so identity and id legitimately COINCIDE — treating that as a forged id
        # refused every legitimate resume (the second `--write` could never run,
        # and the remedy printed re-issued all 51 operators and 14 events).
        return False
    return declared_identity(item)[1] == value


def event_name(event: dict) -> str:
    """The `name` the event door requires, taken from the map's own handle.

    `tortoise_create_event(name, eventKind, props)` takes a required `name`, and
    the map identifies an event by its `ref` (`ev:unlimited-graphs`) rather than
    inventing a title. Deriving the name from the ref keeps the node's identity
    the judgement's, not this script's: a name the map does not declare would be
    a fact the door made up.

    The `eventKind:` prefix is not decoration — it is what makes the identity
    UNIQUE. `ev:no-capture-caps` and the `standard` Object "no capture caps" are
    two different nodes, and an unprefixed slug gave them the same identity, so
    the read-back could not tell them apart (`validate()` now refuses a
    collision outright rather than letting one item hide the other).
    """
    slug = event["ref"].split(":", 1)[-1].replace("-", " ")
    return f'{event["eventKind"]}: {slug}'


# ── read-back verification (read-only, FalkorDB) ──────────────────────────
CLEAN_GRAPH = "org_3326a01ea34ae595d84de5d8f9_g_311d79c684de4d0e8953d189ed"


#: "This node is not an operator", NULL-SAFE. A Subject/Object/Source/Event
#: carries no `is_operator` property, so `NOT (a.is_operator = true)` evaluates
#: to NULL for it and `WHERE NULL` DROPS the row — which silently excluded every
#: one of this map's 50 structural edges (their sources are never Points). The
#: repo's own form is `(x.is_operator IS NULL OR x.is_operator = false)`.
NON_OPERATOR_PREDICATE = ("(a.is_operator IS NULL OR a.is_operator = false) "
                          "AND a.op_type IS NULL")

#: The properties an item's identity lives in — the map declares exactly one
#: per node (a Subject/Object/Event carries `name`, a Source carries `url`, a
#: Point carries `content`). ⛔ An Event is keyed on `name`, not `content`: the
#: only Event door is `tortoise_create_event(name, eventKind, props)`, so `name`
#: is the one property it is GUARANTEED to write (#13913 review — keying Events
#: on `content` made `--verify` refuse a correctly-filed map).
IDENTITY_PROPS = ("name", "url", "content")

#: ⛔ The LABEL is part of the identity. A property value alone cannot say what
#: KIND of node carries it, so an Event called "decision: x" was certified by an
#: Object of the same name (#13913 review round 5, measured: drop one Event row,
#: add a `name`-only row, read back 78/78 and exit 0). The author already met the
#: shape once — `ev:no-capture-caps` vs the `standard` Object "no capture caps" —
#: and resolved it by renaming; the read-back must instead READ the label.
SECTION_LABELS = {"subjects": "Subject", "objects": "Object", "sources": "Source",
                  "events": "Event", "points": "Point"}

#: A direction the door stores VERBATIM, and the two legacy aliases it still
#: accepts and returns for the same two model values.
DIRECTION_ALIASES = {"unidirectional": "->", "bidirectional": "<->"}

#: `pack_registry.SEMANTICS_DIRECTIONS` — the direction each Layer-2 meaning
#: records. A bare-box transcription, pinned equal to the shipped table by a
#: test (a drifted transcription is how a read-back starts refusing legal maps).
FALLBACK_SEMANTICS_DIRECTIONS = {
    "addresses": "->", "causes": "->", "dependsOn": "->", "managedBy": "->",
    "ownedBy": "->", "produces": "<-", "refutes": "->", "supports": "->",
    "hasPart": "<->", "mutuallyExclusive": "<->", "mutuallySupportive": "<->",
    "related": "-", "transacts": "-",
}


def declared_identity(item: dict) -> tuple[str, str]:
    """The (property, value) that identifies the node ONE map item writes."""
    if "url" in item:
        return ("url", item["url"])
    if "eventKind" in item:
        return ("name", event_name(item))
    if "name" in item:
        return ("name", item["name"])
    return ("content", item["content"])


NODE_MARKER_SECTIONS = {"subjects": "subject", "objects": "object",
                        "sources": "source", "events": "event",
                        "points": "point"}


def item_marker(section: str, item: dict) -> str:
    """The marker a NODE is remembered by: its section, ref AND identity.

    ⛔ A `ref` IS NOT THE NODE'S VALUE. Keyed on `f"subject:{ref}"`, an edit to
    the declaration (a renamed Subject, a corrected Point content) left the marker
    unchanged, so the resume made ZERO calls and printed `all nodes and edges
    written` with exit 0 while the graph kept the OLD value — the same silent skip
    the edge markers were hardened against, one arm over (measured: rename
    `sub:premise-labs` and the second run makes 0 calls, exit 0; #13913 review
    round 9). The declared identity is part of the marker, so an edit ORPHANS it
    and `write_map` refuses by name.
    """
    _prop, value = declared_identity(item)
    return f"{NODE_MARKER_SECTIONS[section]}:{item['ref']}:{value}"


def edge_key(edge: dict) -> str:
    """The edge's ENDPOINTS and mechanism — what it is, before its spelling.

    The duplicate-judgement key in `validate()`: two declarations of the same
    endpoints and mechanism are one fact, however differently they are spelled.
    """
    return f"{edge['from']}->{edge['to']}:{edge.get('op') or edge['relation']}"


def edge_marker(edge: dict) -> str:
    """The STABLE marker this edge is remembered by — never its list index.

    ⛔ An index is not an identity. `f"edge:{i}"` meant that inserting one
    newly-judged edge shifted every later marker, so a resume skipped the new
    edge, re-issued a NON-IDEMPOTENT operator over endpoints it had already
    joined (the double-count this filer's own docstring cites), and still
    printed `all nodes and edges written` and exited 0. Measured: insert one
    edge at `edges[0]` and run 2 made exactly ONE call — the old last edge,
    rewritten as `edge:101` — and never wrote the inserted one.

    Keyed on what the edge IS, an edit can only add or remove a marker, and
    `write_map` refuses a marker the map no longer declares instead of resuming
    onto it.

    ⛔ AND THE MECHANISM-DEFINING FIELDS ARE PART OF WHAT IT IS. Keyed on the
    endpoints and mechanism ALONE, a correction that changes what the edge SAYS
    — its `direction` (`->` → `<->`) or its `label` (`addresses` →
    `mutuallySupportive`) — left the marker unchanged, so a resume skipped the
    edge, made no call, and printed `all nodes and edges written` and exit 0
    over a graph still holding the OLD direction and the OLD semantics
    (measured). `validate()` cannot catch that: both spellings are legal. With
    both fields in the marker the edit ORPHANS the old marker, and the resume
    refuses it by name instead of reporting a filing it never made.
    """
    if "op" in edge:
        return (f"edge:{edge_key(edge)}:{edge.get('label') or '-'}:"
                f"{direction_of(edge)}")
    return f"edge:{edge_key(edge)}"


def identity_by_ref(doc: dict) -> dict[str, tuple[str, str, str]]:
    """ref -> the (label, property, value) triple that identifies its node."""
    out: dict[str, tuple[str, str, str]] = {}
    for section, label in SECTION_LABELS.items():
        for item in doc[section]:
            prop, value = declared_identity(item)
            out[item["ref"]] = (label, prop, value)
    return out


def map_identities(doc: dict) -> list[tuple[str, str, str]]:
    """Every node the map declares, as the triple that identifies it.

    ⛔ Identity, not a COUNT. The pre-fix read-back counted nodes anywhere in
    the graph and passed at 90% of the expected total, so a graph holding 71
    unrelated nodes and none of the map was a PASS — and a run that wrote every
    node and zero edges was a PASS too. A count cannot tell the map from
    anything else; the identity can.

    The LABEL is the first leg: a value alone cannot say what kind of node
    carries it, so `name` alone certified an Event with an Object's row.
    """
    return list(identity_by_ref(doc).values())


def _row_identities(row: list) -> set[tuple[str, str, str]]:
    """(label, property, value) triples from a `(label, name, url, content)` row."""
    label = row[0]
    return {(label, prop, value)
            for prop, value in zip(IDENTITY_PROPS, row[1:4], strict=False)
            if value}


def direction_of(edge: dict) -> str:
    """The direction the write DECLARES, resolved the way the DOOR resolves it.

    ⛔ This is NOT "read the stored value back", and it is not a hand restatement
    of the door's steps either. `create_operator` resolves a direction along a
    FOUR-step chain (`TortoiseSDK._effective_direction`): a declared semantics
    outranks a NON-DECLARATION, an absent direction canonicalizes per mechanism,
    and `coherence` rewrites the legacy `bidirectional` default to `->`. A
    read-back that modelled only the default step FALSELY REFUSES a legal write —
    `IMPL` + `supports` with no declared direction stores `->`, and the pre-fix
    expectation (`<->`) refused it (#13913 review round 6).

    So the expectation is the writer's own resolver whenever the repo is
    importable; `_fallback_direction` reproduces its outcome otherwise, and is
    pinned equal to it by a test.
    """
    try:
        from tortoise.sdk import TortoiseSDK
    except Exception:  # no checkout: the fallback, not a silent pass
        return _fallback_direction(edge)
    return TortoiseSDK._effective_direction(
        edge.get("op") or "", edge.get("direction"), edge.get("label"))


def _fallback_direction(edge: dict) -> str:
    """`TortoiseSDK._effective_direction`'s outcome, without the import."""
    op_type = edge.get("op") or ""
    direction = edge.get("direction")
    semantics = edge.get("label")
    # `_declares_direction`: the parameter default (and its model spelling) is
    # NOT a declaration — except for `coherence`, whose own default is one-way,
    # so `<->` IS a statement there.
    if op_type != "coherence":
        declared = direction not in (None, "bidirectional", "<->")
    else:   # `coherence`'s own default is ONE-WAY, so `<->` IS a statement there
        declared = direction not in (None, "bidirectional")
    if semantics is not None and not declared:
        derived = FALLBACK_SEMANTICS_DIRECTIONS.get(semantics)
        if derived is not None:
            direction = derived
    if direction is None:                       # `_canonical_direction`
        direction = {"NAND": "unidirectional", "coherence": "->"}.get(
            op_type, "bidirectional")
    if op_type == "coherence" and direction == "bidirectional":
        direction = "->"                     # step 4's last half
    return direction


def presence(doc: dict, node_rows: list, edge_rows: list,
             op_rows: list) -> tuple[list, list]:
    """(declared identities absent, declared EDGES absent).

    ⛔ A declared edge is present only if the graph holds THAT edge — matched by
    relation type and both endpoint identities (structural) or by an operator
    node of that mechanism, that DIRECTION and exactly two endpoints
    (operators). The pre-fix arm counted any edge joining two present
    identities, and the DOOR ITSELF mints undeclared edges (`props.extractedFrom`
    on 8 Points materialises the Source and links it), so a read-back missing 8
    declared edges still printed 101/101 (#13913 review round 3). It also
    matched a superset of endpoints, so flipping all 51 declared directions read
    back 101/101 — and a writer that drops `direction` turns 46 one-way IMPL
    edges bidirectional and changes what the graph means (#13913 round 5).

    Pure, so the verdict can be exercised without a database.
    """
    present: set[tuple[str, str, str]] = set()
    for row in node_rows:
        present |= _row_identities(row)
    by_ref = identity_by_ref(doc)
    missing_nodes = [ident for ident in by_ref.values() if ident not in present]

    structural: set[tuple[str, tuple[str, str, str], tuple[str, str, str]]] = set()
    for row in edge_rows:
        kind = row[0]
        for a in _row_identities(row[1:5]):
            for b in _row_identities(row[5:9]):
                structural.add((kind, a, b))

    # An operator is a NODE whose typed edges reach its endpoints, and TWO legs
    # are carried by that node:
    #
    # * the SLOT — `create_operator` writes `inputs = [source_id] + target_ids`
    #   and the fold MERGEs one edge per slot with `idx`, so idx 0 IS the source
    #   and idx 1 IS the target. Matching on an unordered endpoint SET read a
    #   REVERSED operator as the declared edge (#13913 review round 4);
    # * the DIRECTION — `direction` is stored VERBATIM on the operator, and the
    #   door's default is the MUTUAL reading for IMPL/NAND, so a writer that
    #   dropped the keyword turned 46 one-way IMPL edges bidirectional while the
    #   slot order still looked right (#13913 review round 5). The slot says
    #   which END is which; the direction says whether confidence flows back.
    # ⛔ A SLOT HOLDS ROWS, NOT A SET OF IDENTITIES. `_row_identities` returns
    # one identity PER NON-EMPTY IDENTITY PROPERTY, and an Event carries BOTH
    # `name` and `content` (measured on the engine: `create_event` writes a props
    # set including both). Flattening a slot into a set therefore made
    # `len(slot) == 1` FALSE for every operator edge whose endpoint is an Event —
    # 8 declared edges read back absent on a CORRECTLY filed map, and
    # `duplicate_operators()` blind on the same edges (#13913 review round 9).
    # Rows are the endpoints; a row's identity set is only matched INTO.
    operators: dict[object,
                    tuple[str, str, dict[int, list[set[tuple[str, str, str]]]]]] = {}
    for row in op_rows:
        nid, kind, idx, direction = row[0], row[1], row[2], row[3]
        _kind, _dir, slots = operators.setdefault(
            nid, (kind, DIRECTION_ALIASES.get(direction, direction), {}))
        try:
            slot = int(idx)
        except (TypeError, ValueError):
            slot = -1
        slots.setdefault(slot, []).append(_row_identities(row[4:8]))

    missing_edges: list[tuple[str, str, str]] = []
    for e in doc["edges"]:
        a, b = by_ref[e["from"]], by_ref[e["to"]]
        if "op" in e:
            # Both sides through the SAME alias table: the door's resolver can
            # return a legacy spelling (`bidirectional`) that the node stores
            # verbatim, while the read-back normalizes it to the model token.
            want = direction_of(e)
            want = DIRECTION_ALIASES.get(want, want)
            held = any(
                kind == e["op"]
                and direction == want
                # the writer's OWN shape: slot 0 = source, slot 1 = the single
                # target. An operator over three endpoints holds both of a
                # declared pair's identities and would otherwise certify it.
                and len(slots.get(0, ())) == 1
                and len(slots.get(1, ())) == 1
                and a in slots[0][0]
                and b in slots[1][0]
                # ⛔ ONE ENDPOINT PER SLOT, not merely one SLOT KEY: a slot holds
                # a SET, so an operator with TWO targets in slot 1 held both of a
                # declared pair's identities and certified both edges while
                # pricing them as one three-input factor (#13913 review round 8).
                and not (set(slots) - {0, 1})
                for kind, direction, slots in operators.values()
            )
        else:
            held = (e["relation"], a, b) in structural
        if not held:
            missing_edges.append((e.get("op") or e.get("relation"),
                                  e["from"], e["to"]))
    return missing_nodes, missing_edges


def duplicates(doc: dict, node_rows: list) -> list[tuple[str, str, str]]:
    """Declared identities the graph holds MORE THAN ONCE.

    A set-based read-back cannot see a duplicate: two copies of a decision Event
    carry the same identity and `presence()` reports the identity present. The
    door's non-idempotent writes are exactly the ones that can mint one (no
    existence check; the SDK mints a fresh `eventId` per call), so the read-back
    owes the count as well as the membership.
    """
    counts: dict[tuple[str, str, str], int] = {}
    for row in node_rows:
        for ident in _row_identities(row):
            counts[ident] = counts.get(ident, 0) + 1
    declared = set(identity_by_ref(doc).values())
    return sorted(ident for ident in declared if counts.get(ident, 0) > 1)


def duplicate_operators(doc: dict, op_rows: list) -> list[tuple[str, str, str]]:
    """Declared operator edges the graph holds MORE THAN ONCE.

    ⛔ `duplicates()` cannot see these: an operator is a `:Point` whose
    `is_operator`/`op_type`/`direction` live on the node and which carries no
    `name`/`url`/`content`, so it contributes NO identity — and `presence()`'s
    operator arm is EXISTENTIAL (`any(...)`). A re-issued `create_operator` mints
    a fresh point id over the same endpoints (no existence check, #10849), so a
    graph holding TWO copies of every declared operator read `nodes: 78/78 /
    edges: 101/101` and exited 0 — the one corruption the intent-marker state
    machine exists to prevent, and `--verify` is appointed its authority
    (#13913 review round 7, measured).

    Grouped exactly as `presence()` groups them, then required to be UNIQUE.
    """
    by_ref = identity_by_ref(doc)
    aliases = DIRECTION_ALIASES
    grouped: dict[object,
                  tuple[str, str, dict[int, list[set[tuple[str, str, str]]]]]] = {}
    for row in op_rows:
        nid, kind, idx, direction = row[0], row[1], row[2], row[3]
        _kind, _dir, slots = grouped.setdefault(
            nid, (kind, aliases.get(direction, direction), {}))
        try:
            slot = int(idx)
        except (TypeError, ValueError):
            slot = -1
        slots.setdefault(slot, []).append(_row_identities(row[4:8]))

    # ⛔ COUNTED PER ENDPOINT PAIR — UNORDERED, and by what the node actually
    # PRICES. Three blind shapes this closes (#13913 review round 9, each
    # measured as `101/101`, `dup_ops=[]`, exit 0):
    #  * a duplicate that stored a different direction or mechanism;
    #  * a WIDE operator `A -> [B, C]` over a declared `A -> B` (`EP._priced_pairs`
    #    prices `(A,B)` and `(A,C)`, so the declared pair is priced twice);
    #  * a REVERSED operator `B -> A` over the declared `A -> B`.
    # The map's own count of declared operators over the pair is the baseline, so
    # a legitimate IMPL+NAND pair (two declared edges, two nodes) is not flagged.
    declared_by_pair: dict[frozenset, int] = {}
    where: dict[frozenset, list[tuple[str, str, str]]] = {}
    for e in doc["edges"]:
        if "op" not in e:
            continue
        key = frozenset((by_ref[e["from"]], by_ref[e["to"]]))
        declared_by_pair[key] = declared_by_pair.get(key, 0) + 1
        where.setdefault(key, []).append((e["op"], e["from"], e["to"]))

    holders: dict[frozenset, set[object]] = {}
    for nid, node in grouped.items():
        slots = node[2]
        # ⛔ ROWS, not identity SETS: an Event endpoint carries two identities
        # (`name` AND `content`), so a set-flattened slot made the arity check
        # below false for 8 declared operator edges and this census blind on the
        # same edges (#13913 review round 9). The pair itself stays UNORDERED
        # (`frozenset`), so a duplicate priced with a flipped direction or a
        # swapped mechanism is still a duplicate.
        # ⛔ PRICING, NOT SHAPE. The question here is "does this operator charge
        # this claim pair a second time?", so EVERY endpoint row counts: a WIDE
        # operator (`A -> [B, C]`) prices `(A, B)`, and a REVERSED one prices the
        # same unordered pair. (Presence is the strict arm — it certifies nothing
        # unless the shape is exactly one source slot and one target slot —
        # because certifying a declared edge is a different claim from noticing a
        # second charge on it.)
        source_ids: set = set()
        for row_ids in slots.get(0, []):
            source_ids |= row_ids
        target_ids: set = set()
        for slot, rows in slots.items():
            if slot == 0:
                continue
            for row_ids in rows:
                target_ids |= row_ids
        for key in declared_by_pair:
            ends = sorted(key, key=repr)
            if len(ends) != 2:
                continue
            if ((ends[0] in source_ids and ends[1] in target_ids)
                    or (ends[0] in target_ids and ends[1] in source_ids)):
                holders.setdefault(key, set()).add(nid)
    out: list[tuple[str, str, str]] = []
    for key, ids in sorted(holders.items(), key=lambda kv: sorted(map(repr, kv[0]))):
        if len(ids) > declared_by_pair[key]:
            out.extend(where[key])
    return out


def verdict(doc: dict, node_rows: list, edge_rows: list, op_rows: list) -> int:
    """0 only when every declared node AND every declared edge reads back, once."""
    missing_nodes, missing_edges = presence(doc, node_rows, edge_rows, op_rows)
    dupes = duplicates(doc, node_rows)
    dup_ops = duplicate_operators(doc, op_rows)
    total = len(map_identities(doc))
    expected_edges = len(doc["edges"])
    print(f"\nnodes: {total - len(missing_nodes)}/{total} declared identities present")
    print(f"edges: {expected_edges - len(missing_edges)}/{expected_edges} "
          "declared edges present")
    if missing_nodes:
        print(f"MISSING — {len(missing_nodes)} declared node(s) are not in the graph:",
              file=sys.stderr)
        for label, prop, value in missing_nodes[:20]:
            print(f"  - {label}.{prop}: {value[:70]!r}", file=sys.stderr)
        if len(missing_nodes) > 20:
            print(f"  … and {len(missing_nodes) - 20} more", file=sys.stderr)
    if dupes:
        print(f"DUPLICATED — {len(dupes)} declared identity(ies) are in the graph "
              "more than once (a non-idempotent write landed twice):", file=sys.stderr)
        for label, prop, value in dupes[:20]:
            print(f"  - {label}.{prop}: {value[:70]!r}", file=sys.stderr)
    if dup_ops:
        print(f"DUPLICATED OPERATORS — {len(dup_ops)} declared edge(s) are held by "
              "more than one operator node (a non-idempotent write landed twice, "
              "#10849 — the claim's EP weight double-counts):", file=sys.stderr)
        for kind, frm, to in dup_ops[:20]:
            print(f"  - {kind}: {frm} -> {to}", file=sys.stderr)
    if missing_edges:
        print(f"EDGES MISSING — {len(missing_edges)} declared edge(s) are absent "
              "(an unjoined map is filed but not findable):", file=sys.stderr)
        for kind, frm, to in missing_edges[:20]:
            print(f"  - {kind}: {frm} -> {to}", file=sys.stderr)
        if len(missing_edges) > 20:
            print(f"  … and {len(missing_edges) - 20} more", file=sys.stderr)
    return 0 if not (missing_nodes or missing_edges or dupes or dup_ops) else 1


def verify(doc: dict) -> int:
    # optional dep, only needed for --verify
    import redis

    uri = os.environ.get("FALKORDB_CLOUD_URI")
    if not uri:
        print("FALKORDB_CLOUD_URI is not set — cannot read back", file=sys.stderr)
        return 2
    r = redis.from_url(uri)

    def q(cypher: str):
        res = r.execute_command("GRAPH.QUERY", CLEAN_GRAPH, cypher)
        return [[c.decode() if isinstance(c, bytes) else c for c in row]
                for row in (res[1] or [])]

    print("== node census")
    for row in q("MATCH (n) RETURN labels(n)[0] AS l, count(*) AS c ORDER BY c DESC"):
        print(f"  {row[0]:<12} {row[1]}")
    print("== object kinds")
    for row in q("MATCH (o:Object) RETURN o.objectKind, count(*) AS c ORDER BY c DESC"):
        print(f"  {row[0]:<28} {row[1]}")
    print("== events by kind")
    for row in q("MATCH (e:Event) RETURN e.eventKind, count(*) AS c ORDER BY c DESC"):
        print(f"  {row[0]:<20} {row[1]}")
    print("== edges by type")
    for row in q("MATCH ()-[e]->() RETURN type(e), count(*) AS c ORDER BY c DESC"):
        print(f"  {row[0]:<16} {row[1]}")

    print("== the goal cascade, with its edges (does the map answer a question?)")
    for row in q("MATCH (a:Object)-[r]->(b:Object) WHERE a.objectKind = 'goal' "
                 "RETURN coalesce(a.name,''), type(r), coalesce(r.label,''), coalesce(b.name,'')"):
        print(f"  {row[0]} -[{row[1]}{':' + row[2] if row[2] else ''}]-> {row[3]}")

    print("== decisions and what they resolved")
    for row in q("MATCH (e:Event {eventKind:'decision'})-[r]->(o) "
                 "RETURN coalesce(e.content,'')  , type(r), coalesce(o.name, o.url, o.content, '')"):
        print(f"  {row[0][:70]}... -[{row[1]}]-> {row[2][:44]}")

    # The LABEL rides every row: a property value alone cannot say what KIND of
    # node carries it, so an Object named like a declared Event certified the
    # missing Event (#13913 review round 5).
    node_cols = ", ".join(f"coalesce(n.{p},'')" for p in IDENTITY_PROPS)
    node_rows = q(f"MATCH (n) RETURN labels(n)[0], {node_cols}")
    # ⛔ NULL-SAFE, not `NOT (a.is_operator = true OR …)`: a Subject/Object/
    # Source/Event carries NO `is_operator` property, so that predicate evaluates
    # to `null` for every one of this map's 50 structural edges (their sources are
    # never Points), and `WHERE null` drops the row — `--verify` then reported all
    # 50 absent on a correctly filed map. This is the repo's own form
    # (`sdk.py`: `(x.is_operator IS NULL OR x.is_operator = false)`).
    a_cols = ", ".join(f"coalesce(a.{p},'')" for p in IDENTITY_PROPS)
    b_cols = ", ".join(f"coalesce(b.{p},'')" for p in IDENTITY_PROPS)
    edge_rows = q("MATCH (a)-[e]->(b) "
                  f"WHERE {NON_OPERATOR_PREDICATE} "
                  "RETURN type(e), labels(a)[0], "
                  f"{a_cols}, labels(b)[0], {b_cols}")
    # An operator is a NODE: its typed edges reach its endpoints, so the declared
    # operator is matched by (mechanism, both endpoints) grouped per operator id
    # — never by a direct `(from)-[:IMPL]->(to)` edge, which the writer does not
    # create (`projection/edges.py`: `MERGE (o)-[:IMPL]->(s)`).
    x_cols = ", ".join(f"coalesce(x.{p},'')" for p in IDENTITY_PROPS)
    op_rows = q("MATCH (o)-[e]->(x) "
                "WHERE (o.is_operator = true OR o.op_type IS NOT NULL) "
                "RETURN coalesce(o.id, o.nodeKey, toString(id(o))), "
                "coalesce(o.op_type, e.op_type, ''), coalesce(e.idx, -1), "
                f"coalesce(o.direction,''), labels(x)[0], {x_cols}")
    return verdict(doc, node_rows, edge_rows, op_rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="validate the map against the ontology, write nothing")
    ap.add_argument("--write", action="store_true", help="write through the MCP doors")
    ap.add_argument("--verify", action="store_true",
                    help="read the clean graph back and print what is there")
    args = ap.parse_args()

    doc = load_map()
    errs = validate(doc)
    if errs:
        print("MAP REFUSED — the ontology would not accept it:", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        return 2
    print(f"map validates: {len(doc['subjects'])} subjects, {len(doc['objects'])} objects, "
          f"{len(doc['sources'])} sources, {len(doc['events'])} events, "
          f"{len(doc['points'])} points, {len(doc['edges'])} edges")

    if args.dry_run:
        return 0
    if args.write:
        return write_map(doc)
    if args.verify:
        return verify(doc)
    # ⛔ NOT 0: a runbook that passes the mode through an empty variable would
    # read success from a run that neither wrote nor verified anything.
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
