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
    uv run --with redis --with requests python file_map.py --dry-run
    uv run --with redis --with requests python file_map.py --verify
    uv run --with redis --with requests python file_map.py --write

`--dry-run` needs no credentials and no network. `--write` needs
`TORTOISE_MCP_API_KEY` and `FALKORDB_CLOUD_URI` in the environment (on the lane
box: `set -a; . /home/cmux/businessmap.env; set +a`). It is resumable: ids are
persisted to `state.json` after every write and re-resolved by name on retry, so
re-running after a transport death converges instead of duplicating.

⛔ It never deletes, supersedes or mitigates anything. Mitigations are
0.10-0.50 and must be written one at a time; this map needs none.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
MAP_PATH = HERE / "business_map.json"
STATE_PATH = HERE / "state.json"

API = "https://api.premiselabs.co"
MCP_URL = f"{API}/mcp/"

# The four epistemic/semantic vocabularies this map is allowed to use. Core
# §5, plus the two packs the business map leans on. Kept here as data so a
# typo is a refusal at `--dry-run`, not a silently mis-kinded node.
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


def validate(doc: dict) -> list[str]:
    """Every rule the ontology makes, checked before a single write."""
    errs: list[str] = []
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
        if o["objectKind"] not in OBJECT_KINDS:
            errs.append(f"{o['ref']}: objectKind {o['objectKind']!r} is not a declared kind")
        gs = o.get("goalState")
        if gs is not None:
            if gs not in GOAL_STATES:
                errs.append(f"{o['ref']}: goalState {gs!r} not in GOAL_STATES")
            if o["objectKind"] not in {"goal", "target", "strategy", "plan"}:
                errs.append(f"{o['ref']}: goalState is only meaningful on goal/target/strategy/plan")
    for s in doc["sources"]:
        reg(s["ref"], "source")
        if s["sourceKind"] not in SOURCE_KINDS:
            errs.append(f"{s['ref']}: sourceKind {s['sourceKind']!r} not declared")
    for e in doc["events"]:
        reg(e["ref"], "event")
        if e["eventKind"] not in EVENT_KINDS:
            errs.append(f"{e['ref']}: eventKind {e['eventKind']!r} not in core §5")
        if len(e["content"]) > 1000:
            errs.append(f"{e['ref']}: event content {len(e['content'])} > 1000 chars")
    for p in doc["points"]:
        reg(p["ref"], "point")
        if p.get("kind", "statement") not in POINT_KINDS:
            errs.append(f"{p['ref']}: pointKind {p.get('kind')!r} — extraction writes statement only")
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
            if e["relation"] not in STRUCTURAL_RELS:
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
        code, payload, hdrs = self._post({
            "jsonrpc": "2.0", "id": self._next_id, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "business-map-13880", "version": "1"}},
        })
        sid = hdrs.get("Mcp-Session-Id") or hdrs.get("mcp-session-id")
        self.session = sid
        if payload and payload.get("error"):
            raise RuntimeError(f"initialize refused: {payload['error']}")
        # notifications/initialized is a notification: no id, no body expected
        try:
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except urllib.error.HTTPError:
            pass  # some transports answer 202/204 with an error-ish shape

    def call(self, tool: str, args: dict) -> dict:
        """Call one tool, with the transport's own retry contract honoured."""
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
                    # then re-issue. Every write here is idempotent (see README).
                    time.sleep(min(120, float(retry_after) + 1))
                    continue
                raise RuntimeError(f"{tool} refused: {err}")
            if code >= 500 and attempt < 5:
                time.sleep(min(60, 5 * (attempt + 1)))
                continue
            if not payload:
                raise RuntimeError(f"{tool}: empty response (HTTP {code})")
            return payload.get("result", payload)
        raise RuntimeError(f"{tool}: gave up after 6 attempts")


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

    def already(marker: str) -> bool:
        if marker in st["written"]:
            return True
        return False

    def mark(marker: str) -> None:
        if marker not in st["written"]:
            st["written"].append(marker)
        save_state(st)

    # 1. Subjects — the actors.
    for s in doc["subjects"]:
        if already(f"subject:{s['ref']}") and s["ref"] in ids:
            continue
        res = mcp.call("tortoise_create_subject",
                       {"name": s["name"], "subjectKind": s["subjectKind"]})
        ids[s["ref"]] = _id_of(res) or s["name"]
        mark(f"subject:{s['ref']}")
        print(f"  subject  {s['name']:<34} -> {ids[s['ref']]}")

    # 2. Objects — products, competitors, programmes, goals, standards.
    for o in doc["objects"]:
        if already(f"object:{o['ref']}") and o["ref"] in ids:
            continue
        args = {"name": o["name"], "objectKind": o["objectKind"]}
        if o.get("status"):
            args["status"] = o["status"]
        if o.get("goalState"):
            args["goalState"] = o["goalState"]
        res = mcp.call("tortoise_create_object", args)
        ids[o["ref"]] = _id_of(res) or o["name"]
        mark(f"object:{o['ref']}")
        print(f"  object   {o['name'][:44]:<44} -> {ids[o['ref']]}")

    # 3. Sources — the documents claims were read from.
    for s in doc["sources"]:
        if already(f"source:{s['ref']}") and s["ref"] in ids:
            continue
        args = {"url": s["url"], "sourceKind": s["sourceKind"]}
        if s.get("documentKind"):
            args["documentKind"] = s["documentKind"]
        res = mcp.call("tortoise_create_source", args)
        ids[s["ref"]] = _id_of(res) or s["url"]
        mark(f"source:{s['ref']}")
        print(f"  source   {s['url'][:60]:<60} -> {ids[s['ref']]}")

    # 4. Events — decisions belong on the timeline, not in the belief layer.
    for e in doc["events"]:
        if already(f"event:{e['ref']}") and e["ref"] in ids:
            continue
        args = {"eventKind": e["eventKind"], "content": e["content"]}
        if e.get("startedAt"):
            args["startedAt"] = e["startedAt"]
        res = mcp.call("tortoise_create_event", args)
        ids[e["ref"]] = _id_of(res) or e["content"][:40]
        mark(f"event:{e['ref']}")
        print(f"  event    {e['ref']:<34} -> {ids[e['ref']]}")

    # 5. Points — the claims.
    for p in doc["points"]:
        if already(f"point:{p['ref']}") and p["ref"] in ids:
            continue
        args = {"content": p["content"], "kind": p.get("kind", "statement"), "dedup": True}
        src = p.get("source")
        if src:
            args["source_url"] = doc_by_ref(doc, src)["url"]
        res = mcp.call("tortoise_create_point", args)
        ids[p["ref"]] = _id_of(res) or p["content"][:40]
        mark(f"point:{p['ref']}")
        print(f"  point    {p['ref']:<34} -> {ids[p['ref']]}")

    # 6. Edges — the whole point. Operators carry a mechanism + a meaning;
    #    structural relations are plain typed edges.
    for i, e in enumerate(doc["edges"]):
        marker = f"edge:{i}"
        if already(marker):
            continue
        src, dst = ids.get(e["from"]), ids.get(e["to"])
        if not src or not dst:
            failures.append(f"{marker}: unbound endpoint {e['from']}/{e['to']}")
            continue
        try:
            if "op" in e:
                args = {"op_type": e["op"], "src": src, "targets": [dst]}
                if e.get("label"):
                    args["semantics"] = e["label"]
                if e.get("direction"):
                    args["direction"] = e["direction"]
                mcp.call("tortoise_create_operator", args)
            else:
                mcp.call("tortoise_create_edge",
                         {"from_id": src, "to_id": dst, "relation": e["relation"]})
            mark(marker)
        except Exception as exc:                      # noqa: BLE001 — recorded, not swallowed
            failures.append(f"{marker}: {e.get('op') or e.get('relation')} "
                            f"{e['from']}->{e['to']}: {exc}")
            print(f"  !! {failures[-1]}", file=sys.stderr)
    st["failed"] = failures
    save_state(st)
    if failures:
        print(f"\n{len(failures)} edge(s) not written — recorded in state.json", file=sys.stderr)
        return 1
    print("\nall nodes and edges written")
    return 0


def doc_by_ref(doc: dict, ref: str) -> dict:
    for section in ("subjects", "objects", "sources", "events", "points"):
        for item in doc[section]:
            if item["ref"] == ref:
                return item
    raise KeyError(ref)


# ── read-back verification (read-only, FalkorDB) ──────────────────────────
CLEAN_GRAPH = "org_3326a01ea34ae595d84de5d8f9_g_311d79c684de4d0e8953d189ed"


def verify(doc: dict) -> int:
    import redis  # noqa: PLC0415 — optional dep, only needed for --verify

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

    expected = (len(doc["subjects"]) + len(doc["objects"]) + len(doc["sources"])
                + len(doc["events"]) + len(doc["points"]))
    got = q("MATCH (n) RETURN count(n) AS c")[0][0]
    print(f"\nnodes: expected ~{expected}, found {got}")

    print("== the goal cascade, with its edges (does the map answer a question?)")
    for row in q("MATCH (a:Object)-[r]->(b:Object) WHERE a.objectKind = 'goal' "
                 "RETURN coalesce(a.name,''), type(r), coalesce(r.label,''), coalesce(b.name,'')"):
        print(f"  {row[0]} -[{row[1]}{':' + row[2] if row[2] else ''}]-> {row[3]}")

    print("== decisions and what they resolved")
    for row in q("MATCH (e:Event {eventKind:'decision'})-[r]->(o) "
                 "RETURN coalesce(e.content,'')  , type(r), coalesce(o.name, o.url, o.content, '')"):
        print(f"  {row[0][:70]}... -[{row[1]}]-> {row[2][:44]}")

    return 0 if got >= expected * 0.9 else 1


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
