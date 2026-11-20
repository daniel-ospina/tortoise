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
  contradiction are OPERATORS (`IMPL`/`NAND`, with a Layer-2 `semantics` label);
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
OP_TYPES = {"IMPL", "NAND", "coherence"}
SEMANTICS = {"addresses", "supports", "refutes", "mutuallySupportive",
             "mutuallyExclusive", "hasPart", "causes", "produces", "dependsOn",
             "transacts", "related", "ownedBy", "managedBy"}
DIRECTIONS = {"->", "<-", "<->", "-"}


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
    return {
        "point": set(vocab.point_kinds),
        "event": set(vocab.event_kinds),
        "source": set(vocab.source_kinds),
        "object": object_kinds,
        "goal_state": set(GOAL_STATE_VALUES),
        "relation": relations,
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
    # sharing one (property, value) make one of them unverifiable: the read-back
    # cannot tell them apart and reports both present when one is missing.
    seen: dict[tuple[str, str], int] = {}
    for ident in map_identities(doc):
        seen[ident] = seen.get(ident, 0) + 1
    for (prop, value), count in sorted(seen.items()):
        if count > 1:
            errs.append(f"{count} items share the identity {prop}={value[:60]!r} — "
                        "the read-back cannot tell them apart")
    # the same rule for EDGES. `--verify` matches each declared edge against the
    # graph independently, so the same edge judged twice is certified by ONE
    # written edge — two judgements, one fact, and the duplicate is invisible.
    edge_seen: dict[str, int] = {}
    for e in doc["edges"]:
        edge_seen[edge_marker(e)] = edge_seen.get(edge_marker(e), 0) + 1
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
        claim would be the false PASS again. `state.json` is left untouched, so
        a re-run re-resolves by name and only writes what `--verify` shows is
        missing.
        """
        for attempt in range(6):
            self._next_id += 1
            try:
                code, payload, _ = self._post({
                    "jsonrpc": "2.0", "id": self._next_id, "method": "tools/call",
                    "params": {"name": tool, "arguments": args},
                })
            except urllib.error.HTTPError as exc:
                code, payload = exc.code, None
                body = exc.read().decode("utf-8", "replace")
                try:
                    payload = json.loads(body)
                except json.JSONDecodeError:
                    payload = {"raw": body}
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if not retry_safe:
                    raise UnknownOutcome(_unknown(tool, exc)) from exc
                if attempt == 5:
                    raise RuntimeError(f"{tool}: transport died — {exc}") from exc
                time.sleep(min(60, 5 * (attempt + 1)))
                continue

            err = (payload or {}).get("error")
            if err:
                retry_after = ((err.get("data") or {}).get("retry_after")
                               if isinstance(err.get("data"), dict) else None)
                if retry_after:
                    # "the work may still complete" — wait the advertised delay,
                    # then re-issue. Only for a write whose repeat converges.
                    if not retry_safe:
                        raise UnknownOutcome(_unknown(tool, err))
                    time.sleep(min(120, float(retry_after) + 1))
                    continue
                raise RuntimeError(f"{tool} refused: {err}")
            if code >= 500:
                if not retry_safe:
                    raise UnknownOutcome(_unknown(tool, f"HTTP {code}"))
                if attempt < 5:
                    time.sleep(min(60, 5 * (attempt + 1)))
                    continue
                # ⛔ The SIXTH attempt used to FALL THROUGH: a gateway's error
                # body is not a JSON-RPC `result`, so `_refusal` found nothing
                # and the failure was returned as success — 101 edges marked
                # `written` while 50 were never created (#13913 review).
                raise RuntimeError(f"{tool}: HTTP {code} after 6 attempts — the "
                                   "write is UNCONFIRMED")
            if not payload:
                raise RuntimeError(f"{tool}: empty response (HTTP {code})")
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
                raise RuntimeError(
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
                raise RuntimeError(f"{tool} FAILED: {msg[:400]}")
            return result
        raise RuntimeError(f"{tool}: gave up after 6 attempts")


class UnknownOutcome(RuntimeError):
    """The transport died before the door answered — the write MAY have landed.

    A distinct type on purpose: the caller must NOT retire the intent marker for
    this class (a re-run would re-issue a non-idempotent write), while a definite
    refusal must (the write certainly did not land).
    """


def _unknown(tool: str, reason: object) -> str:
    return (f"{tool}: outcome UNKNOWN ({reason}) — this write is NOT idempotent, so "
            "it is not retried; re-run and let --verify decide what landed")


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
    STATE_PATH.write_text(json.dumps(st, indent=1, sort_keys=True) + "\n")


def write_map(doc: dict) -> int:
    key = os.environ.get("TORTOISE_MCP_API_KEY")
    if not key:
        print("TORTOISE_MCP_API_KEY is not set — load /home/cmux/businessmap.env first",
              file=sys.stderr)
        return 2
    mcp = Mcp(key)
    mcp.initialize()
    st = load_state()
    ids: dict[str, str] = st["ids"]
    failures: list[str] = []
    ambiguous: list[str] = []

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
    orphan_markers = sorted(m for m in st["written"]
                            if m.startswith("edge:") and m not in declared_markers)
    if orphan_markers:
        print(f"state.json names {len(orphan_markers)} edge marker(s) this map does "
              f"not declare (e.g. {orphan_markers[0]!r}) — an edge was added, "
              f"removed or re-typed since it was written, or the markers are the "
              f"INDEX-keyed spelling an older version wrote. Resuming would skip "
              f"the edited edges and re-issue the rest. Remove those 'edge:*' "
              f"entries from {STATE_PATH} (KEEP the ids) and re-run.",
              file=sys.stderr)
        return 2

    def already(marker: str) -> bool:
        return marker in st["written"]

    def mark(marker: str) -> None:
        if marker not in st["written"]:
            st["written"].append(marker)
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
        CLOSED on the duplicate side: a definite refusal un-records it, and an
        UNKNOWN outcome stays recorded while being reported, with `--verify` as
        the authority on whether it landed.
        """
        mark(marker)
        try:
            return mcp.call(tool, args, retry_safe=False)
        except UnknownOutcome:
            # It MAY have landed: the marker STAYS, so the next run does not
            # re-issue a write this door cannot dedup (`create_operator` has no
            # existence check). `--verify` is the authority on whether it did.
            raise
        except Exception:
            unmark(marker)      # a definite refusal: the write certainly did not land
            raise

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
        if already(f"subject:{s['ref']}") and s["ref"] in ids:
            continue
        res = mcp.call("tortoise_create_subject",
                       {"name": s["name"], "subjectKind": s["subjectKind"]})
        ids[s["ref"]] = _resolve(s["ref"], s["name"], res, "subject")
        mark(f"subject:{s['ref']}")
        print(f"  subject  {s['name']:<34} -> {ids[s['ref']]}")

    # 2. Objects — products, competitors, programmes, goals, standards.
    #    The lifecycle `status` is a tenant PROP, not a parameter of this door
    #    (`tortoise_create_object(name, objectKind, props, goalState)`);
    #    `goalState` is the one object field the surface names explicitly.
    for o in doc["objects"]:
        if already(f"object:{o['ref']}") and o["ref"] in ids:
            continue
        args = {"name": o["name"], "objectKind": o["objectKind"]}
        if o.get("status"):
            args["props"] = {"status": o["status"]}
        if o.get("goalState"):
            args["goalState"] = o["goalState"]
        res = mcp.call("tortoise_create_object", args)
        ids[o["ref"]] = _resolve(o["ref"], o["name"], res, "object")
        mark(f"object:{o['ref']}")
        print(f"  object   {o['name'][:44]:<44} -> {ids[o['ref']]}")

    # 3. Sources — the documents claims were read from.
    #    `documentKind` is caller-settable but NOT a parameter of
    #    `tortoise_create_source(url, sourceKind, tier, sourceDate, props)`.
    for s in doc["sources"]:
        if already(f"source:{s['ref']}") and s["ref"] in ids:
            continue
        args = {"url": s["url"], "sourceKind": s["sourceKind"]}
        if s.get("documentKind"):
            args["props"] = {"documentKind": s["documentKind"]}
        res = mcp.call("tortoise_create_source", args)
        ids[s["ref"]] = _resolve(s["ref"], s["url"], res, "source")
        mark(f"source:{s['ref']}")
        print(f"  source   {s['url'][:60]:<60} -> {ids[s['ref']]}")

    # 4. Events — decisions belong on the timeline, not in the belief layer.
    #    `tortoise_create_event(name, eventKind, props)`: the content and the
    #    start time are PROPS, and `name` is required.
    for e in doc["events"]:
        if already(f"event:{e['ref']}"):
            if e["ref"] in ids:
                continue
            # The intent was recorded but no id came back (a run killed between
            # the write and the id assignment, or `_resolve` raising after a
            # landed write). Re-creating would mint a SECOND Event carrying the
            # same identity — and `create_event` has no existence check — so the
            # ref is reported as AMBIGUOUS instead, never silently re-written.
            ambiguous.append(f"event:{e['ref']}")
            continue
        props = {"content": e["content"]}
        if e.get("startedAt"):
            props["startedAt"] = e["startedAt"]
        res = write_once(f"event:{e['ref']}", "tortoise_create_event",
                         {"name": event_name(e), "eventKind": e["eventKind"],
                          "props": props})
        ids[e["ref"]] = _resolve(e["ref"], e["content"], res, "event")
        print(f"  event    {e['ref']:<34} -> {ids[e['ref']]}")

    # 5. Points — the claims.
    #    Provenance to a Source is `props.extractedFrom` (the contract
    #    `create_point` validates and `_link_source` reads); there is no
    #    `source_url` parameter on this surface.
    for p in doc["points"]:
        if already(f"point:{p['ref']}") and p["ref"] in ids:
            continue
        args = {"content": p["content"], "kind": p.get("kind", "statement"), "dedup": True}
        src = p.get("source")
        if src:
            args["props"] = {"extractedFrom": doc_by_ref(doc, src)["url"]}
        res = mcp.call("tortoise_create_point", args)
        ids[p["ref"]] = _resolve(p["ref"], p["content"], res, "point")
        mark(f"point:{p['ref']}")
        print(f"  point    {p['ref']:<34} -> {ids[p['ref']]}")

    # 6. Edges — the whole point. Operators carry a mechanism + a meaning;
    #    structural relations are plain typed edges.
    #    Spellings are the surface's own: `source_id`/`target_ids` on the
    #    operator door, `source_id`/`target_id`/`predicate` on the edge door.
    for e in doc["edges"]:
        marker = edge_marker(e)
        if already(marker):
            continue
        src, dst = ids.get(e["from"]), ids.get(e["to"])
        if not src or not dst:
            failures.append(f"{marker}: unbound endpoint {e['from']}/{e['to']}")
            continue
        try:
            if "op" in e:
                args = {"op_type": e["op"], "source_id": src, "target_ids": [dst]}
                if e.get("label"):
                    args["semantics"] = e["label"]
                if e.get("direction"):
                    args["direction"] = e["direction"]
                write_once(marker, "tortoise_create_operator", args)
            else:
                mcp.call("tortoise_create_edge",
                         {"source_id": src, "target_id": dst,
                          "predicate": e["relation"]})
            mark(marker)
        except Exception as exc:  # recorded in `failures`, never swallowed
            failures.append(f"{marker}: {e.get('op') or e.get('relation')} "
                            f"{e['from']}->{e['to']}: {exc}")
            print(f"  !! {failures[-1]}", file=sys.stderr)
    st["failed"] = failures
    save_state(st)
    if ambiguous:
        print(f"\n{len(ambiguous)} item(s) were recorded but returned no id — an "
              "UNKNOWN outcome, NOT re-written (a second write would duplicate a "
              "non-idempotent node). Run --verify: if the node is present it "
              "landed; to re-file it, drop its marker from state.json.", file=sys.stderr)
        for marker in ambiguous:
            print(f"  ? {marker}", file=sys.stderr)
    if failures:
        print(f"\n{len(failures)} edge(s) not written — recorded in state.json", file=sys.stderr)
        return 1
    if ambiguous:
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


def declared_identity(item: dict) -> tuple[str, str]:
    """The (property, value) that identifies the node ONE map item writes."""
    if "url" in item:
        return ("url", item["url"])
    if "eventKind" in item:
        return ("name", event_name(item))
    if "name" in item:
        return ("name", item["name"])
    return ("content", item["content"])


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
    """
    return f"edge:{edge['from']}->{edge['to']}:{edge.get('op') or edge['relation']}"


def identity_by_ref(doc: dict) -> dict[str, tuple[str, str]]:
    """ref -> the (property, value) that identifies the node it writes."""
    out: dict[str, tuple[str, str]] = {}
    for section in ("subjects", "objects", "sources", "events", "points"):
        for item in doc[section]:
            out[item["ref"]] = declared_identity(item)
    return out


def map_identities(doc: dict) -> list[tuple[str, str]]:
    """Every node the map declares, as the (property, value) that identifies it.

    ⛔ Identity, not a COUNT. The pre-fix read-back counted nodes anywhere in
    the graph and passed at 90% of the expected total, so a graph holding 71
    unrelated nodes and none of the map was a PASS — and a run that wrote every
    node and zero edges was a PASS too. A count cannot tell the map from
    anything else; the identity can.
    """
    return list(identity_by_ref(doc).values())


def _row_identities(row: list) -> set[tuple[str, str]]:
    return {(prop, value) for prop, value in zip(IDENTITY_PROPS, row, strict=False)
            if value}


def presence(doc: dict, node_rows: list, edge_rows: list,
             op_rows: list) -> tuple[list, list]:
    """(declared identities absent, declared EDGES absent).

    ⛔ A declared edge is present only if the graph holds THAT edge — matched by
    relation type and both endpoint identities (structural) or by an operator
    node of that mechanism over exactly those two endpoints (operators). The
    pre-fix arm counted any edge joining two present identities, and the DOOR
    ITSELF mints undeclared edges (`props.extractedFrom` on 8 Points
    materialises the Source and links it), so a read-back missing 8 declared
    edges still printed 101/101 (#13913 review round 3).

    Pure, so the verdict can be exercised without a database.
    """
    present: set[tuple[str, str]] = set()
    for row in node_rows:
        present |= _row_identities(row)
    by_ref = identity_by_ref(doc)
    missing_nodes = [(prop, value) for prop, value in by_ref.values()
                     if (prop, value) not in present]

    structural: set[tuple[str, tuple[str, str], tuple[str, str]]] = set()
    for row in edge_rows:
        kind = row[0]
        for a in _row_identities(row[1:4]):
            for b in _row_identities(row[4:7]):
                structural.add((kind, a, b))

    # An operator is a NODE whose typed edges reach its endpoints, and the SLOT
    # is what carries the direction: `create_operator` writes
    # `inputs = [source_id] + target_ids` and the fold MERGEs an edge per slot
    # with `idx`, so idx 0 IS the source and idx >= 1 ARE the targets. Matching
    # on an unordered endpoint SET would read a REVERSED operator as the declared
    # edge — 46 of this map's 51 operators declare `->`, i.e. the direction is
    # what EP propagates (#13913 review round 4).
    operators: dict[object, tuple[str, dict[int, set[tuple[str, str]]]]] = {}
    for row in op_rows:
        nid, kind, idx = row[0], row[1], row[2]
        _kind, slots = operators.setdefault(nid, (kind, {}))
        try:
            slot = int(idx)
        except (TypeError, ValueError):
            slot = -1
        slots.setdefault(slot, set()).update(_row_identities(row[3:6]))

    missing_edges: list[tuple[str, str, str]] = []
    for e in doc["edges"]:
        a, b = by_ref[e["from"]], by_ref[e["to"]]
        if "op" in e:
            held = any(
                kind == e["op"]
                and a in slots.get(0, set())
                and b in set().union(*[ends for slot, ends in slots.items() if slot >= 1])
                for kind, slots in operators.values()
            )
        else:
            held = (e["relation"], a, b) in structural
        if not held:
            missing_edges.append((e.get("op") or e.get("relation"),
                                  e["from"], e["to"]))
    return missing_nodes, missing_edges


def duplicates(doc: dict, node_rows: list) -> list[tuple[str, str]]:
    """Declared identities the graph holds MORE THAN ONCE.

    A set-based read-back cannot see a duplicate: two copies of a decision Event
    carry the same identity and `presence()` reports the identity present. The
    door's non-idempotent writes are exactly the ones that can mint one (no
    existence check; the SDK mints a fresh `eventId` per call), so the read-back
    owes the count as well as the membership.
    """
    counts: dict[tuple[str, str], int] = {}
    for row in node_rows:
        for ident in _row_identities(row):
            counts[ident] = counts.get(ident, 0) + 1
    declared = set(identity_by_ref(doc).values())
    return sorted(ident for ident in declared if counts.get(ident, 0) > 1)


def verdict(doc: dict, node_rows: list, edge_rows: list, op_rows: list) -> int:
    """0 only when every declared node AND every declared edge reads back, once."""
    missing_nodes, missing_edges = presence(doc, node_rows, edge_rows, op_rows)
    dupes = duplicates(doc, node_rows)
    total = len(map_identities(doc))
    expected_edges = len(doc["edges"])
    print(f"\nnodes: {total - len(missing_nodes)}/{total} declared identities present")
    print(f"edges: {expected_edges - len(missing_edges)}/{expected_edges} "
          "declared edges present")
    if missing_nodes:
        print(f"MISSING — {len(missing_nodes)} declared node(s) are not in the graph:",
              file=sys.stderr)
        for prop, value in missing_nodes[:20]:
            print(f"  - {prop}: {value[:70]!r}", file=sys.stderr)
        if len(missing_nodes) > 20:
            print(f"  … and {len(missing_nodes) - 20} more", file=sys.stderr)
    if dupes:
        print(f"DUPLICATED — {len(dupes)} declared identity(ies) are in the graph "
              "more than once (a non-idempotent write landed twice):", file=sys.stderr)
        for prop, value in dupes[:20]:
            print(f"  - {prop}: {value[:70]!r}", file=sys.stderr)
    if missing_edges:
        print(f"EDGES MISSING — {len(missing_edges)} declared edge(s) are absent "
              "(an unjoined map is filed but not findable):", file=sys.stderr)
        for kind, frm, to in missing_edges[:20]:
            print(f"  - {kind}: {frm} -> {to}", file=sys.stderr)
        if len(missing_edges) > 20:
            print(f"  … and {len(missing_edges) - 20} more", file=sys.stderr)
    return 0 if not missing_nodes and not missing_edges and not dupes else 1


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

    node_rows = q("MATCH (n) RETURN coalesce(n.name,''), coalesce(n.url,''), "
                  "coalesce(n.content,'')")
    # ⛔ NULL-SAFE, not `NOT (a.is_operator = true OR …)`: a Subject/Object/
    # Source/Event carries NO `is_operator` property, so that predicate evaluates
    # to `null` for every one of this map's 50 structural edges (their sources are
    # never Points), and `WHERE null` drops the row — `--verify` then reported all
    # 50 absent on a correctly filed map. This is the repo's own form
    # (`sdk.py`: `(x.is_operator IS NULL OR x.is_operator = false)`).
    edge_rows = q("MATCH (a)-[e]->(b) "
                  f"WHERE {NON_OPERATOR_PREDICATE} "
                  "RETURN type(e), coalesce(a.name,''), coalesce(a.url,''), "
                  "coalesce(a.content,''), coalesce(b.name,''), coalesce(b.url,''), "
                  "coalesce(b.content,'')")
    # An operator is a NODE: its typed edges reach its endpoints, so the declared
    # operator is matched by (mechanism, both endpoints) grouped per operator id
    # — never by a direct `(from)-[:IMPL]->(to)` edge, which the writer does not
    # create (`projection/edges.py`: `MERGE (o)-[:IMPL]->(s)`).
    op_rows = q("MATCH (o)-[e]->(x) "
                "WHERE (o.is_operator = true OR o.op_type IS NOT NULL) "
                "RETURN coalesce(o.id, o.nodeKey, toString(id(o))), "
                "coalesce(o.op_type, e.op_type, ''), coalesce(e.idx, -1), "
                "coalesce(x.name,''), coalesce(x.url,''), coalesce(x.content,'')")
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
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
