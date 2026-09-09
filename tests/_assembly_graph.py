"""#2165 — synthetic multi-session Object-structured graph builders (Task 1 Step 3).

Standing substrate for the connected-assembly tasks (Tasks 2-7). Mirrors the
v2 eval lane faithfully (docs/plans/2026-09-08-2165-connected-assembly.md):

* Objects via ``create_entity`` (deterministic name-id) with a supersession
  chain folded through the live path (``commit_ops.apply_supersessions``);
* aboutObject-linked Points via the ingest_v2 UNWIND-MERGE shape with
  SPARSE ``when`` (extractor D3 semantics — only state-change/decision/
  date-bearing facts carry it) and ``createdAt`` = session date;
* dated Events with ``startedAt`` and ZERO aboutObject edges (the ingest_v2
  gap R8 fixes — the hosted variant builder adds them);
* EP stamped through explicit ep_alpha/ep_beta writes (real-lane note: write
  side EP for extraction points is not landed — fixture authors what the
  renderer must handle; neutral-0.5 is the honest real-lane default).

This module is a TEST HELPER (underscore prefix) — excluded from
ci-surfaces.yml selection and from any production import path.
"""
from __future__ import annotations

# docker-lane helpers: build against a live TortoiseSDK on a dedicated graph.
from tortoise.commit_ops import apply_supersessions
from tortoise.sdk import TortoiseSDK

try:
    from tools.longmem_eval.ingest import UNDATED_SENTINEL  # type: ignore
except Exception:  # pragma: no cover - path guard
    UNDATED_SENTINEL = "1970-01-01T00:00:00Z"

# session dates (session date is the always-present v2-lane date)
SESSION_A_DATE = "2026-08-10"
SESSION_B_DATE = "2026-09-01"
SESSION_A = "sess-2026-08-10"
SESSION_B = "sess-2026-09-01"

OBJECTS = {"couch": "core:furniture", "dog bed": "core:furniture",
           "sofa": "core:furniture"}


def _link_point_about(proj, pid: str, names: list[str]) -> None:
    """ingest_v2 E7-shape aboutObject MERGE for a Point (one batched query)."""
    if names:
        proj.g.query(
            "UNWIND $names AS name "
            "MATCH (p:Point {id:$pid}), (o:Object {name:name}) "
            "MERGE (p)-[:aboutObject]->(o)",
            params={"pid": pid, "names": names})


def _link_event_about(proj, qid: str, si: int, eid: str, names: list[str],
                      ) -> None:
    """R8-shape aboutObject MERGE for an Event (keyed on the dedup triple
    lme_event_id + lme_question_id + lme_session_index — the ingest_v2 event
    idempotency key; mirrors the point-loop batched query)."""
    if names:
        proj.g.query(
            "UNWIND $names AS name "
            "MATCH (e:Event {lme_event_id:$eid, lme_question_id:$qid, "
            "lme_session_index:$si}), (o:Object {name:name}) "
            "MERGE (e)-[:aboutObject]->(o)",
            params={"eid": eid, "qid": qid, "si": si, "names": names})


def stamp_ep(proj, pid: str, alpha: float = 9.0, beta: float = 1.0) -> None:
    """Persist real (non-neutral) EP posteriors on a Point — the R12
    EP-realism fixture requirement (the evidence renderer must show real
    confidence, not neutral 0.5)."""
    proj.g.query(
        "MATCH (p:Point {id:$id}) "
        "SET p.ep_alpha = $a, p.ep_beta = $b, "
        "p.posterior_alpha = $a, p.posterior_beta = $b",
        params={"id": pid, "a": alpha, "b": beta})


def build_base_graph(sdk: TortoiseSDK) -> dict:
    """Primary v2-lane-faithful fixture:

    * Objects ``couch`` / ``dog bed`` (live) and the supersession chain
      ``couch`` --superseded by--> ``sofa`` (live path fold; the fold's
      supersededAt is then PINNED to the story date 2026-09-01 — the raw
      fold stamps build wall-clock, which would make Task 5/6 byte-golden
      state headers non-reproducible across fresh fixture builds);
    * session A (2026-08-10): couch purchased (dated point, ``when``
      stamped); dog-bed chewed + vet visit (undated — no ``when``);
    * session B (2026-09-01): couch sold / replaced by sofa (dated point,
      ``when`` stamped) — the supersession evidence; sofa delivered
      (undated);
    * Events per session with ``startedAt`` (dated) and ZERO aboutObject
      edges (v2-lane faithful — the R8 ingest gap); the hosted-variant
      shape (Event-aboutObject edges) is ``build_hosted_variant``;
    * ≥12 distractor points (unrelated topics, other sessions);
    * EP stamped on the evidence points (non-neutral).

    Returns a handle dict of ids + the canary question texts.
    """
    proj = sdk._get_proj()
    qid = "q2165base"
    for name, kind in OBJECTS.items():
        sdk.create_entity("object", name, objectKind=kind,
                          lme_question_id=qid, is_episodic=True)

    # supersession chain: couch superseded by sofa (live fold path)
    apply_supersessions(
        proj, sdk,
        [{"superseded": "couch", "supersedes_by": "sofa",
          "evidence": "replaced the couch with a sofa"}],
        session_id=SESSION_B)
    # pin the fold timestamp to the story date (see docstring) — mirrors the
    # orphan/torn-row builders' raw SET below
    proj.g.query(
        "MATCH (o:Object {name:'couch'}) "
        "SET o.supersededAt = $ts", params={"ts": "2026-09-01T00:00:00Z"})

    # session A points
    sdk.create_point(
        "statement", "bought the grey couch from ikea for 800 dollars",
        id="pA-couch-bought", session_id=SESSION_A,
        lme_question_id=qid, is_episodic=True, status="draft",
        search_keys="couch ikea 800 dollars",
        quote="bought the grey couch", createdAt=SESSION_A_DATE,
        when="2026-08-10", validFrom="2026-08-10")
    sdk.create_point(
        "statement", "the dog chewed the corner of the dog bed cushion",
        id="pA-dogbed-chewed", session_id=SESSION_A,
        lme_question_id=qid, is_episodic=True, status="draft",
        search_keys="dog bed chewed cushion", quote="chewed the corner",
        createdAt=SESSION_A_DATE)
    sdk.create_point(
        "statement", "took the dog to the vet for the chewed cushion",
        id="pA-vet", session_id=SESSION_A,
        lme_question_id=qid, is_episodic=True, status="draft",
        search_keys="vet dog visit", quote="took the dog to the vet",
        createdAt=SESSION_A_DATE)
    _link_point_about(proj, "pA-couch-bought", ["couch"])
    _link_point_about(proj, "pA-dogbed-chewed", ["dog bed"])
    _link_point_about(proj, "pA-vet", ["dog bed"])
    stamp_ep(proj, "pA-couch-bought", 8.0, 1.5)
    stamp_ep(proj, "pA-dogbed-chewed", 6.0, 2.0)

    # session B points (the supersession evidence + sofa delivery)
    sdk.create_point(
        "statement", "sold the old couch and ordered a new sofa instead",
        id="pB-couch-sold", session_id=SESSION_B,
        lme_question_id=qid, is_episodic=True, status="draft",
        search_keys="couch sold sofa replaced", quote="sold the old couch",
        createdAt=SESSION_B_DATE, when="2026-09-01", validFrom="2026-09-01")
    sdk.create_point(
        "statement", "the new sofa was delivered on the first of september",
        id="pB-sofa-delivered", session_id=SESSION_B,
        lme_question_id=qid, is_episodic=True, status="draft",
        search_keys="sofa delivered september", quote="sofa was delivered",
        createdAt=SESSION_B_DATE)
    _link_point_about(proj, "pB-couch-sold", ["couch", "sofa"])
    _link_point_about(proj, "pB-sofa-delivered", ["sofa"])
    stamp_ep(proj, "pB-couch-sold", 9.0, 1.0)

    # dated Events (zero aboutObject — v2-lane faithful; the hosted-variant
    # Event-aboutObject shape lives in build_hosted_variant)
    sdk.create_event("bought the grey couch from ikea", "purchase",
                     sessionId=SESSION_A, lme_question_id=qid,
                     lme_session_index=0, is_episodic=True,
                     startedAt="2026-08-10", lme_event_id="evA-couch")
    sdk.create_event("sold the old couch and ordered a sofa", "purchase",
                     sessionId=SESSION_B, lme_question_id=qid,
                     lme_session_index=0, is_episodic=True,
                     startedAt="2026-09-01", lme_event_id="evB-sofa")

    # ≥12 distractors (unrelated; other sessions; no aboutObject)
    topics = ["pasta recipe tomato basil", "road bike tire inflation psi",
              "yoga class tuesday evening", "rainy walk umbrella park",
              "new running shoes half marathon", "coffee machine descale",
              "book club mystery novel chapter", "garden tomatoes watering",
              "phone battery replacement shop", "birthday cake chocolate",
              "airport parking shuttle", "gym membership renewal june"]
    for i, topic in enumerate(topics):
        sdk.create_point(
            "statement", f"talked about {topic} with a friend",
            id=f"pD{i}", session_id=f"sess-distract-{i}",
            lme_question_id=qid, is_episodic=True, status="draft",
            search_keys=topic, createdAt="2026-06-01")

    return {"qid": qid, "session_a": SESSION_A, "session_b": SESSION_B,
            "objects": dict(OBJECTS),
            "points": {"couch_bought": "pA-couch-bought",
                       "dogbed_chewed": "pA-dogbed-chewed",
                       "couch_sold": "pB-couch-sold",
                       "sofa_delivered": "pB-sofa-delivered"},
            "events": {"couch_bought": "evA-couch", "sofa_ordered": "evB-sofa"},
            "questions": {
                # current-state (fires; single subject)
                "current": "what is the current status of the couch?",
                # ordering/compare (fires; both subjects)
                "compare": ("which came first - the couch or the dog bed?"),
                # interval (fires; both subjects, days between)
                "interval": ("how many days between buying the couch and "
                             "selling the couch?"),
                # misfire (must NOT fire — preference with two options)
                "misfire": ("compare the couch and the dog bed, which should "
                            "i keep?"),
                # ago-relative (must NOT fire — R13)
                "ago": "what was the couch status two weeks ago?",
            }}


def build_hosted_variant(sdk: TortoiseSDK) -> None:
    """Add Event-aboutObject edges to the base graph (hosted-lane shape)."""
    proj = sdk._get_proj()
    _link_event_about(proj, "q2165base", 0, "evA-couch", ["couch"])
    _link_event_about(proj, "q2165base", 0, "evB-sofa", ["couch", "sofa"])


def build_hub_graph(sdk: TortoiseSDK, n_points: int = 60) -> str:
    """A hub Object with >per-slice-cap aboutObject points (the Task 4
    bounded-pre-fetch fixture). Returns the hub Object name."""
    proj = sdk._get_proj()
    name = "hub-subject"
    sdk.create_entity("object", name, objectKind="core:other",
                      lme_question_id="qhub", is_episodic=True)
    for i in range(n_points):
        pid = f"pH{i}"
        sdk.create_point(
            "statement", f"hub-subject update number {i}: another detail "
                         f"about the ongoing topic",
            id=pid, session_id=f"sess-hub-{i % 5}",
            lme_question_id="qhub", is_episodic=True, status="draft",
            createdAt=f"2026-{(i % 12) + 1:02d}-05")
        _link_point_about(proj, pid, [name])
    return name


def build_deep_rank_substrate(sdk: TortoiseSDK) -> dict:
    """R9 geometric-fidelity substrate: 87 same-subject rows (55 question-
    token crowds + 2 dated golds + 30 unrelated fillers) over 87 sessions.

    MEASURED platform reality (pinned empirically, not asserted by design):
    this FalkorDB fulltext returns score ties (0.0) for every match — the
    result order is a deterministic-but-opaque internal order, NOT BM25
    relevance — and the DEFAULT ask evidence keeps the first ~40 rows (the
    pool-40 rerank depth). On this exact content set the earlier-created
    gold (pDeepG1) deterministically ranks at FTS index 1 (an in-pool
    same-subject evidence row — faithful: real graphs always hold SOME
    in-pool evidence) and the second gold (pDeepG2) at index 56 of 57
    retrievable — OUTSIDE the default pool-40, INSIDE a widened 120 fetch.
    That geometry is the honest, non-vacuous R9 mechanism: the DEFAULT arm
    admits G1 only; Task 7's A-widened arm (pool 40→120) must admit BOTH
    (G2 is the discriminating row) — pinned by the calibration NOW because
    Task 7 is forbidden from editing the substrate. Positional stability:
    gold_a is created before gold_b in this builder (do not reorder — the
    ranks are creation-order-pinned)."""
    proj = sdk._get_proj()
    name = "deep-subject"
    sdk.create_entity("object", name, objectKind="core:other",
                      lme_question_id="qdeep", is_episodic=True)
    gold_a, gold_b = "pDeepG1", "pDeepG2"
    rows = 0
    # rows 0-54: T0 crowds (55 rows) — every query token present
    for i in range(55):
        pid = f"pDeepX{i}"
        day = 1 + i
        sdk.create_point(
            "statement",
            f"day {day} - how many days passed since the deep-subject "
            f"milestone was discussed and what deep-subject milestone came "
            f"next in the plan",
            id=pid, session_id=f"sess-deep-{i}",
            lme_question_id="qdeep", is_episodic=True, status="draft",
            quote="milestone plan",
            # VALID calendar dates (months 6-7; never 2026-08-32+)
            createdAt=f"2026-{6 + (i // 28):02d}-{((i % 28) + 1):02d}")
        _link_point_about(proj, pid, [name])
        rows += 1
    # rows 55-56: the TWO dated gold rows (G2 is the deep discriminator)
    gold_markers = {gold_a: "fieldtrip", gold_b: "almanac"}
    for j, pid in enumerate((gold_a, gold_b)):
        day = 56 + j
        gold_date = f"2026-09-{1 + j:02d}"   # VALID calendar dates (Sep 01/02)
        sdk.create_point(
            "statement",
            f"the deep-subject milestone happened on day {day} and was "
            f"noted as a dated {gold_markers[pid]} fact in the log",
            id=pid, session_id=f"sess-deep-{55 + j}",
            lme_question_id="qdeep", is_episodic=True, status="draft",
            has_answer=True, quote="milestone happened",
            createdAt=gold_date, when=gold_date, validFrom=gold_date)
        _link_point_about(proj, pid, [name])
        stamp_ep(proj, pid, 9.0, 1.0)
        rows += 1
    # rows 57-86: unrelated filler (30 rows) — sink below the golds
    for i in range(30):
        sdk.create_point(
            "statement",
            f"grocery shopping and weather and errands chat number {i} - "
            f"milk bread eggs and rain plans and bus times",
            id=f"pDeepZ{i}", session_id=f"sess-deep-fill-{i}",
            lme_question_id="qdeep", is_episodic=True, status="draft",
            createdAt=f"2026-10-{((i % 28) + 1):02d}")  # valid (Oct 01-28)
        _link_point_about(proj, f"pDeepZ{i}", [name])
        rows += 1
    assert rows == 87, rows
    return {"gold_a": gold_a, "gold_b": gold_b,
            "question": ("how many days passed between the two deep-subject "
                         "milestones?"),
            "subject": name,
            "pool_rows": rows}


def build_supersession_chain_variants(sdk: TortoiseSDK) -> dict:
    """Task 5 no-fabrication variants: (a) fold's supersededBy NAME resolves
    to ZERO visible successor nodes; (b) status='superseded' with EMPTY
    supersededBy (hand-written/torn row); (c) successor exists but is
    recall-excluded (archived)."""
    proj = sdk._get_proj()
    # (a) orphan successor: supersededBy name has no Object node
    sdk.create_entity("object", "orphan-src", objectKind="core:other",
                      is_episodic=True)
    proj.g.query(
        "MATCH (o:Object {name:'orphan-src'}) "
        "SET o.status='superseded', o.supersededBy='successor-never-created', "
        "o.supersededAt='2026-09-01T00:00:00Z'")
    # (b) torn row: superseded with EMPTY supersededBy
    proj.g.query(
        "CREATE (o:Object {name:'torn-row', status:'superseded', "
        "supersededAt:'2026-09-01T00:00:00Z', id:'torn-row-id'})")
    # (c) recall-excluded successor (archived) — the chain exists but the
    # successor is excluded from the default current-state view
    sdk.create_entity("object", "excl-src", objectKind="core:other",
                      is_episodic=True)
    sdk.create_entity("object", "excl-dst", objectKind="core:other",
                      is_episodic=True)
    apply_supersessions(
        proj, sdk,
        [{"superseded": "excl-src", "supersedes_by": "excl-dst",
          "evidence": "superseded then archived"}],
        session_id=SESSION_B)
    proj.g.query(
        "MATCH (o:Object {name:'excl-dst'}) SET o.status='archived'")
    return {"orphan": "orphan-src", "torn": "torn-row",
            "excluded_src": "excl-src", "excluded_dst": "excl-dst"}


def build_malformed_date_row(sdk: TortoiseSDK) -> None:
    """Task 6 malformed-date fixture: garbage ``when`` on a point whose
    createdAt is the SENTINEL (no usable date → undated tier)."""
    sdk.create_point(
        "statement", "the event with an unparseable date on the record",
        id="pMalformed", session_id="sess-malformed",
        is_episodic=True, status="draft", quote="unparseable date",
        createdAt=UNDATED_SENTINEL, when="not-a-real-date-2026",
        validFrom="not-a-real-date-2026")


def build_out_of_subgraph_gold(sdk: TortoiseSDK) -> dict:
    """R16(b) canary substrate: a dated gold point anchored ONLY to a THIRD
    Object (bookshelf — outside the compare subjects' subgraphs) whose TEXT
    carries the canary's OWN tokens (couch / dog bed / bought) so legacy
    admits it by DIRECT question overlap — never through a fragile indirect
    search_keys/PRF chain. Task 1 Step 4 calibration asserts legacy admits
    ≥1 (A≥1); the assembled arm resolves couch + dog bed and walks THEIR
    aboutObject subgraphs only, so the bookshelf-anchored gold is outside
    both (B=0 structurally reachable)."""
    proj = sdk._get_proj()
    sdk.create_entity("object", "bookshelf", objectKind="core:furniture",
                      is_episodic=True)
    sdk.create_point(
        "statement",
        "on the fifth of september we moved the bookshelf into the study "
        "and bought a reading lamp - the same week the old couch was "
        "discussed and the dog bed got chewed, but the bookshelf was the "
        "newest thing we bought that month",
        id="pOutsideGold", session_id="sess-bookshelf",
        is_episodic=True, status="draft", has_answer=True,
        search_keys="bookshelf study reading lamp couch dog bed september",
        quote="bookshelf reading lamp", createdAt="2026-09-05",
        when="2026-09-05", validFrom="2026-09-05")
    _link_point_about(proj, "pOutsideGold", ["bookshelf"])
    stamp_ep(proj, "pOutsideGold", 8.0, 1.0)
    return {"gold": "pOutsideGold", "object": "bookshelf",
            # the canary compare question: both compare subjects (couch/dog
            # bed) resolve; the gold is anchored ONLY to bookshelf — a third
            # object inside neither resolved subgraph. Legacy admits it by
            # direct token overlap (calibration asserts the reading-lamp
            # marker); the assembled pool-replacement must not starve it
            # (B=0 means the assembled arm admits ZERO of it).
            "question": ("which came first - buying the couch or the dog "
                         "bed getting chewed?"),
            "date": "2026-09-05"}
