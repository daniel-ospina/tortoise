"""W3 harness corpus generator + authoring spec (issue #2099 W3-a).

Deterministically renders the Cat-34-style scripted-conversation corpus::

    tests/eval/harness/
      fixtures/<sid>.json          # {suite, seed, holdout, turns, ...} ONLY
      gold/<sid>.gold.json         # SEALED — per-turn labels + suite expectations
      _manifest.json               # sha256 of every fixture + gold file
      baselines/{main,m2}.json     # first-run-pending baselines (published by the runner)

The generator is byte-idempotent (sorted keys, fixed indent, no timestamps)
for the frozen corpus = fixtures/ + gold/ + _manifest.json.  Baselines are
OUTSIDE that drift scope (they change when the runner blesses a run).

Suites (DM-6/7):
* know_to_ask — per-turn should_retrieve labels; courtesy / re-mention /
  below-notability turns MUST NOT fire (false-fire anti-gaming).
* push — pointer-budget precision/recall (gold-acceptable pointer ids per
  should_retrieve turn).
* write_back — planted anchors that must survive session→graph write-back
  with provenance intact (gradeable TODAY — no reflex needed).
* continuity — writer fixture → write-back → reader fixture; the reader
  cell's recall must surface the planted decision.
* isolation — multi-team fixtures (two team namespaces, overlapping entity
  names, disjoint facts); content-level isolation gate across ALL suites
  (E2E-4, #2099's own gate).

Authoring note: the know_to_ask / push suites grade the REFLEX decision
layer, which the W4 delivery issue builds.  The corpus + grader ship here;
an initial NULL-reflex baseline (nothing injects) is honest + publishable —
the harness can fail per the fix-wave protocol.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from eval.harness import corpus, schema

CORPUS_SEED = 42
MIN_SESSIONS = 6          # ≥ 2 per graded-today suite (write_back/continuity/isolation)
MIN_PLANTED_ANCHORS = 24  # floor on planted anchors across write_back/continuity gold


def _t(role: str, content: str) -> dict:
    return {"role": role, "content": content}


# ═══ know_to_ask fixtures (reflex-graded; null-reflex baseline honest) ═════

KTA1_FIXTURE = {
    "suite": "know_to_ask", "seed": 1, "holdout": False,
    "turns": [
        _t("user", "Morning! Hope your weekend was good."),          # below-notability
        _t("assistant", "Thanks — it was restful. What can I help with?"),
        _t("user", "What did Alice say about the Widget Co deal?"),   # should_retrieve
        _t("assistant", "Alice flagged the Widget Co pricing as too aggressive."),
        _t("user", "Thanks, that helps a lot."),                      # courtesy — no fire
        _t("user", "And remind me — what was Alice's view on the Widget Co terms?"),  # re-mention
    ],
}
KTA1_GOLD = {
    "suite": "know_to_ask", "schema_version": 1, "session_id": "kta01_reminder_turns",
    "per_turn": [
        {"turn": 1, "should_retrieve": False},    # below-notability opener
        {"turn": 2, "should_retrieve": False},
        {"turn": 3, "should_retrieve": True, "pointers": ["pt_alice_widgetco_pricing"]},
        {"turn": 4, "should_retrieve": False},
        {"turn": 5, "should_retrieve": False},    # courtesy turn
        {"turn": 6, "should_retrieve": True, "pointers": ["pt_alice_widgetco_pricing"]},
    ],
}

KTA2_FIXTURE = {
    "suite": "know_to_ask", "seed": 2, "holdout": True,   # PINNED holdout member
    "turns": [
        _t("user", "What's the status on the Aurora perf work?"),    # should_retrieve
        _t("assistant", "The Aurora profile run showed a 12% regression on the write path."),
        _t("user", "Who owns the follow-up?"),                        # should_retrieve
        _t("assistant", "Diego is tracking it in the perf board."),
        _t("user", "Okay — I'll ping Diego later."),                  # courtesy
    ],
}
KTA2_GOLD = {
    "suite": "know_to_ask", "schema_version": 1, "session_id": "kta02_aurora_status",
    "per_turn": [
        {"turn": 1, "should_retrieve": True, "pointers": ["pt_aurora_perf_regression"]},
        {"turn": 2, "should_retrieve": False},
        {"turn": 3, "should_retrieve": True, "pointers": ["pt_aurora_owner_diego"]},
        {"turn": 4, "should_retrieve": False},
        {"turn": 5, "should_retrieve": False},
    ],
}


# ═══ push fixtures (pointer-budget precision/recall) ═══════════════════════

PUSH1_FIXTURE = {
    "suite": "push", "seed": 3, "holdout": False,
    "turns": [
        _t("user", "Before the retro, can you pull up the decisions from the Lumen refactor?"),
        _t("assistant", "Sure — which ones matter most?"),
        _t("user", "The ones about the module boundary and the test strategy."),
        _t("assistant", "Got it — summarizing now."),
    ],
}
PUSH1_GOLD = {
    "suite": "push", "schema_version": 1, "session_id": "push01_lumen_decisions",
    "per_turn": [
        {"turn": 1, "should_retrieve": True,
         "pointers": ["pt_lumen_boundary", "pt_lumen_test_strategy"]},
        {"turn": 2, "should_retrieve": False},
        {"turn": 3, "should_retrieve": True,
         "pointers": ["pt_lumen_boundary", "pt_lumen_test_strategy"]},
        {"turn": 4, "should_retrieve": False},
    ],
}

PUSH2_FIXTURE = {
    "suite": "push", "seed": 4, "holdout": False,
    "turns": [
        _t("user", "I'm about to talk to the Ember design team. What did we decide about the onboarding flow?"),
        _t("assistant", "Let me check the design notes."),
        _t("user", "Specifically the three-step flow and the consent copy."),
        _t("assistant", "On it."),
    ],
}
PUSH2_GOLD = {
    "suite": "push", "schema_version": 1, "session_id": "push02_ember_onboarding",
    "per_turn": [
        {"turn": 1, "should_retrieve": True,
         "pointers": ["pt_ember_flow", "pt_ember_consent"]},
        {"turn": 2, "should_retrieve": False},
        {"turn": 3, "should_retrieve": True,
         "pointers": ["pt_ember_flow", "pt_ember_consent"]},
        {"turn": 4, "should_retrieve": False},
    ],
}


# ═══ write_back fixtures (gradeable TODAY — no reflex needed) ═════════════

WB1_FIXTURE = {
    "suite": "write_back", "seed": 5, "holdout": False,
    "turns": [
        _t("user", "Alice said the Widget Co pricing was too aggressive and we should push for a 10% cut."),
        _t("assistant", "Noted — I'll record that as a decision candidate."),
        _t("user", "Diego confirmed the Aurora perf regression is a write-path queue issue, not the indexer."),
        _t("assistant", "Got it — recording the root-cause narrowing."),
    ],
}
WB1_GOLD = {
    "suite": "write_back", "schema_version": 1, "session_id": "wb01_meeting_notes",
    "write_back": {
        "planted_points": [
            "Widget Co pricing was too aggressive",
            "push for a 10% cut",
            "Aurora perf regression is a write-path queue issue",
        ],
        "provenance_required": True,
    },
}

WB2_FIXTURE = {
    "suite": "write_back", "seed": 6, "holdout": False,
    "turns": [
        _t("user", "The Lumen refactor keeps the module boundary we agreed — no shared mutable state across services."),
        _t("assistant", "Recording that as the confirmed boundary decision."),
        _t("user", "Test strategy is contract tests for the public seams, not end-to-end for everything."),
        _t("assistant", "Noted — contract-first test strategy recorded."),
    ],
}
WB2_GOLD = {
    "suite": "write_back", "schema_version": 1, "session_id": "wb02_lumen_notes",
    "write_back": {
        "planted_points": [
            "Lumen refactor keeps the module boundary",
            "no shared mutable state across services",
            "contract tests for the public seams",
        ],
        "provenance_required": True,
    },
}


# ═══ continuity fixtures (writer → reader pairs) ══════════════════════════

CW1_WRITER_FIXTURE = {
    "suite": "continuity", "seed": 7, "holdout": False, "writer": True,
    "turns": [
        _t("user", "Decision: we're moving the ingest retry to exponential backoff with a 5x cap. Maya proposed it, Leo seconded."),
        _t("assistant", "Recorded as a decision with the proposer attribution."),
    ],
}
CW1_WRITER_GOLD = {
    "suite": "continuity", "schema_version": 1, "session_id": "cw01_writer_ingest",
    "continuity": {
        "writer_session": "cw01_writer_ingest",
        "reader_planted": ["ingest retry to exponential backoff with a 5x cap"],
        "reader_queries": ["What did we decide about ingest retries?"],
    },
}
CW1_READER_FIXTURE = {
    "suite": "continuity", "seed": 8, "holdout": False,
    "turns": [
        _t("user", "What did we decide about ingest retries last week?"),
    ],
}
CW1_READER_GOLD = {
    "suite": "continuity", "schema_version": 1, "session_id": "cw01_reader_ingest",
    "continuity": {
        "writer_session": "cw01_writer_ingest",
        "reader_planted": ["ingest retry to exponential backoff with a 5x cap"],
        "reader_queries": ["What did we decide about ingest retries?"],
    },
}

CW2_WRITER_FIXTURE = {
    "suite": "continuity", "seed": 9, "holdout": True, "writer": True,  # holdout pair
    "turns": [
        _t("user", "Decision: the Ember onboarding ships as a three-step flow with an explicit consent step. Approved by Ana."),
        _t("assistant", "Recorded."),
    ],
}
CW2_WRITER_GOLD = {
    "suite": "continuity", "schema_version": 1, "session_id": "cw02_writer_ember",
    "continuity": {
        "writer_session": "cw02_writer_ember",
        "reader_planted": ["three-step flow with an explicit consent step"],
        "reader_queries": ["What onboarding flow did we approve for Ember?"],
    },
}
CW2_READER_FIXTURE = {
    "suite": "continuity", "seed": 10, "holdout": True,
    "turns": [
        _t("user", "Remind me — what onboarding flow did we approve for Ember?"),
    ],
}
CW2_READER_GOLD = {
    "suite": "continuity", "schema_version": 1, "session_id": "cw02_reader_ember",
    "continuity": {
        "writer_session": "cw02_writer_ember",
        "reader_planted": ["three-step flow with an explicit consent step"],
        "reader_queries": ["What onboarding flow did we approve for Ember?"],
    },
}


# ═══ isolation fixtures (multi-team; overlapping names, disjoint facts) ═══

ISO_A_FIXTURE = {
    "suite": "isolation", "seed": 11, "holdout": False, "team": "team_a",
    "turns": [
        _t("user", "For Team A: the 'Mercury' project ships its alpha next Tuesday. The Mercury budget is approved at $40k."),
        _t("assistant", "Recorded under Team A."),
        _t("user", "Also: Diego owns the Mercury launch checklist."),
        _t("assistant", "Recorded."),
    ],
}
ISO_A_GOLD = {
    "suite": "isolation", "schema_version": 1, "session_id": "iso_a_mercury",
    "teams": {
        "team_a": {
            "anchors": ["ships its alpha next Tuesday", "budget is approved at $40k",
                        "owns the Mercury launch checklist"],
        },
        "team_b": {
            "anchors": ["incident postmortem is scheduled Friday"],  # overlapping NAME, disjoint fact
        },
    },
}
ISO_B_FIXTURE = {
    "suite": "isolation", "seed": 12, "holdout": False, "team": "team_b",
    "turns": [
        _t("user", "For Team B: the 'Mercury' incident postmortem is scheduled Friday. The Mercury on-call rotation is fixed."),
        _t("assistant", "Recorded under Team B."),
        _t("user", "Priority: ship the Mercury retry fix before the postmortem."),
        _t("assistant", "Recorded."),
    ],
}
ISO_B_GOLD = {
    "suite": "isolation", "schema_version": 1, "session_id": "iso_b_mercury",
    "teams": {
        "team_a": {
            "anchors": ["ships its alpha next Tuesday"],
        },
        "team_b": {
            "anchors": ["incident postmortem is scheduled Friday",
                        "on-call rotation is fixed",
                        "retry fix before the postmortem"],
        },
    },
}

# Multi-team write_back + continuity pairs (isolation applies across suites).
ISO_WB_A_FIXTURE = {
    "suite": "write_back", "seed": 13, "holdout": False, "team": "team_a",
    "turns": [
        _t("user", "Team A note: the 'Atlas' migration is green in staging."),
        _t("assistant", "Recorded."),
    ],
}
ISO_WB_A_GOLD = {
    "suite": "write_back", "schema_version": 1, "session_id": "iso_wb_a_atlas",
    "write_back": {"planted_points": ["migration is green in staging"],
                   "provenance_required": True},
}
ISO_WB_B_FIXTURE = {
    "suite": "write_back", "seed": 14, "holdout": False, "team": "team_b",
    "turns": [
        _t("user", "Team B note: the 'Atlas' migration is BLOCKED on a schema review."),
        _t("assistant", "Recorded."),
    ],
}
ISO_WB_B_GOLD = {
    "suite": "write_back", "schema_version": 1, "session_id": "iso_wb_b_atlas",
    "write_back": {"planted_points": ["migration is blocked on a schema review"],
                   "provenance_required": True},
}

ISO_CW_A_WRITER = {
    "suite": "continuity", "seed": 15, "holdout": False, "writer": True, "team": "team_a",
    "turns": [
        _t("user", "Team A decision: 'Orion' uses the internal queue, not Kafka."),
        _t("assistant", "Recorded."),
    ],
}
ISO_CW_A_WRITER_GOLD = {
    "suite": "continuity", "schema_version": 1, "session_id": "iso_cw_a_writer_orion",
    "continuity": {"writer_session": "iso_cw_a_writer_orion",
                   "reader_planted": ["uses the internal queue, not Kafka"],
                   "reader_queries": ["What queue does Orion use?"]},
}
ISO_CW_A_READER = {
    "suite": "continuity", "seed": 16, "holdout": False, "team": "team_a",
    "turns": [
        _t("user", "What queue does Orion use?"),
    ],
}
ISO_CW_A_READER_GOLD = {
    "suite": "continuity", "schema_version": 1, "session_id": "iso_cw_a_reader_orion",
    "continuity": {"writer_session": "iso_cw_a_writer_orion",
                   "reader_planted": ["uses the internal queue, not Kafka"],
                   "reader_queries": ["What queue does Orion use?"]},
}
ISO_CW_B_WRITER = {
    "suite": "continuity", "seed": 17, "holdout": False, "writer": True, "team": "team_b",
    "turns": [
        _t("user", "Team B decision: 'Orion' integrates with Kafka for the event feed."),
        _t("assistant", "Recorded."),
    ],
}
ISO_CW_B_WRITER_GOLD = {
    "suite": "continuity", "schema_version": 1, "session_id": "iso_cw_b_writer_orion",
    "continuity": {"writer_session": "iso_cw_b_writer_orion",
                   "reader_planted": ["integrates with Kafka for the event feed"],
                   "reader_queries": ["How does Orion consume events?"]},
}
ISO_CW_B_READER = {
    "suite": "continuity", "seed": 18, "holdout": False, "team": "team_b",
    "turns": [
        _t("user", "How does Orion consume events?"),
    ],
}
ISO_CW_B_READER_GOLD = {
    "suite": "continuity", "schema_version": 1, "session_id": "iso_cw_b_reader_orion",
    "continuity": {"writer_session": "iso_cw_b_writer_orion",
                   "reader_planted": ["integrates with Kafka for the event feed"],
                   "reader_queries": ["How does Orion consume events?"]},
}

# ── Authored set: fixture spec + gold spec (session_id = filename stem) ────
AUTHORED: list[tuple[str, dict, dict]] = [
    ("kta01_reminder_turns", KTA1_FIXTURE, KTA1_GOLD),
    ("kta02_aurora_status", KTA2_FIXTURE, KTA2_GOLD),
    ("push01_lumen_decisions", PUSH1_FIXTURE, PUSH1_GOLD),
    ("push02_ember_onboarding", PUSH2_FIXTURE, PUSH2_GOLD),
    ("wb01_meeting_notes", WB1_FIXTURE, WB1_GOLD),
    ("wb02_lumen_notes", WB2_FIXTURE, WB2_GOLD),
    ("cw01_writer_ingest", CW1_WRITER_FIXTURE, CW1_WRITER_GOLD),
    ("cw01_reader_ingest", CW1_READER_FIXTURE, CW1_READER_GOLD),
    ("cw02_writer_ember", CW2_WRITER_FIXTURE, CW2_WRITER_GOLD),
    ("cw02_reader_ember", CW2_READER_FIXTURE, CW2_READER_GOLD),
    ("iso_a_mercury", ISO_A_FIXTURE, ISO_A_GOLD),
    ("iso_b_mercury", ISO_B_FIXTURE, ISO_B_GOLD),
    ("iso_wb_a_atlas", ISO_WB_A_FIXTURE, ISO_WB_A_GOLD),
    ("iso_wb_b_atlas", ISO_WB_B_FIXTURE, ISO_WB_B_GOLD),
    ("iso_cw_a_writer_orion", ISO_CW_A_WRITER, ISO_CW_A_WRITER_GOLD),
    ("iso_cw_a_reader_orion", ISO_CW_A_READER, ISO_CW_A_READER_GOLD),
    ("iso_cw_b_writer_orion", ISO_CW_B_WRITER, ISO_CW_B_WRITER_GOLD),
    ("iso_cw_b_reader_orion", ISO_CW_B_READER, ISO_CW_B_READER_GOLD),
]


def _build_session_docs(sid: str, fixture: dict, gold: dict) -> tuple[dict, dict]:
    """Commit the fixture doc (harness-visible ONLY — gold NEVER embedded)
    + the sealed gold doc (session_id keyed to the stem)."""
    fx = dict(fixture)
    fx.pop("gold", None)  # paranoid: answer-key contamination guard
    gy = dict(gold)
    gy["session_id"] = sid
    return fx, gy


def render_corpus() -> dict[str, bytes]:
    """Render the full corpus to ``{relative_path: bytes}`` (no disk writes)."""
    outputs: dict[str, bytes] = {}
    file_digests: dict[str, str] = {}
    anchor_count = 0
    for sid, fixture, gold in AUTHORED:
        fx, gy = _build_session_docs(sid, fixture, gold)
        fx_rel = f"fixtures/{sid}.json"
        gy_rel = f"gold/{sid}.gold.json"
        outputs[fx_rel] = _dump_json_bytes(fx)
        outputs[gy_rel] = _dump_json_bytes(gy)
        file_digests[fx_rel] = schema.sha256_bytes(outputs[fx_rel])
        file_digests[gy_rel] = schema.sha256_bytes(outputs[gy_rel])
        anchor_count += len(gold.get("write_back", {}).get("planted_points", [])
                          if gold.get("suite") == "write_back" else [])
        cont = gold.get("continuity") or {}
        anchor_count += len(cont.get("reader_planted", []))
        if gold.get("suite") == "isolation":
            for team_spec in (gold.get("teams") or {}).values():
                anchor_count += len(team_spec.get("anchors", []))

    if len(AUTHORED) < MIN_SESSIONS:
        raise AssertionError(f"corpus has {len(AUTHORED)} sessions < {MIN_SESSIONS} floor")
    if anchor_count < MIN_PLANTED_ANCHORS:
        raise AssertionError(
            f"corpus has {anchor_count} planted anchors < {MIN_PLANTED_ANCHORS} floor"
        )

    digest_payload = "\n".join(
        f"{rel}:{file_digests[rel]}" for rel in sorted(file_digests)
    ).encode("utf-8")
    fixtures_hash = schema.sha256_bytes(digest_payload)

    manifest = {
        "schema_version": schema.SCHEMA_VERSION,
        "corpus": "harness",
        "seed": CORPUS_SEED,
        "generator": "tests/eval/harness/generate_corpus.py",
        "fixtures_hash": fixtures_hash,
        "files": {rel: digest for rel, digest in sorted(file_digests.items())},
    }
    outputs["_manifest.json"] = _dump_json_bytes(manifest)

    for posture in ("llm", "m2"):
        baseline = corpus.first_run_pending_baseline(posture=posture)
        baseline["fixtures_hash"] = fixtures_hash
        rel = "baselines/main.json" if posture == "llm" else "baselines/m2.json"
        outputs[rel] = _dump_json_bytes(baseline)
    return outputs


def write_corpus(root: Path | None = None) -> list[str]:
    """Write the rendered corpus under a root dir; returns rel paths.

    Baselines are NOT part of the frozen drift scope; each pending baseline
    is written only when missing/pending — a PUBLISHED baseline (non-empty
    metrics) is never clobbered by a generator re-run."""
    root = root or corpus.HARNESS_DIR
    outputs = render_corpus()
    written: list[str] = []
    for rel, data in outputs.items():
        path = root / rel
        if rel.startswith("baselines/") and path.exists():
            existing = schema.read_json(path)
            if existing.get("metrics") or existing.get("history"):
                continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        written.append(rel)
    return written


def _iter_committed(root: Path):
    """Yield the frozen-corpus JSON files (fixtures + gold + _manifest.json),
    EXCLUDING baselines/ (they change when the runner blesses)."""
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix == ".json":
            rel = path.relative_to(root).as_posix()
            if rel == "_manifest.json" or rel.startswith(("fixtures/", "gold/")):
                yield rel, path


def check_drift(root: Path | None = None) -> list[str]:
    """Compare a fresh in-memory render against committed files."""
    root = root or corpus.HARNESS_DIR
    committed = {rel: path for rel, path in _iter_committed(root)}
    fresh = render_corpus()
    drifted = []
    for rel, data in fresh.items():
        if rel.startswith("baselines/"):
            continue
        if rel not in committed:
            drifted.append(f"{rel} (missing on disk)")
        elif committed[rel].read_bytes() != data:
            drifted.append(f"{rel} (content differs)")
    for rel in committed:
        if rel not in fresh:
            drifted.append(f"{rel} (orphan on disk)")
    return drifted


def validate_committed(root: Path | None = None) -> list[str]:
    """Full schema validation of the committed corpus (fixture ↔ gold, hash,
    holdout ratio, both posture baselines)."""
    root = root or corpus.HARNESS_DIR
    issues: list[str] = []
    for sid in corpus.session_ids(root):
        fixture = corpus.load_fixture(sid, root)
        gold = corpus.load_gold(sid, root)
        issues += [f"{sid} fixture: {i}" for i in schema.validate_fixture(fixture)]
        issues += [f"{sid} gold: {i}" for i in schema.validate_gold(gold, fixture=fixture)]
        issues += [f"{sid}: {i}" for i in schema.fixture_gold_consistent(fixture, gold, sid)]
    n = len(corpus.session_ids(root))
    n_holdout = len(corpus.holdout_ids(root))
    if n and (n_holdout / n) < 0.05:
        issues.append(
            f"holdout set too small: {n_holdout}/{n} < 5% (plan ~15% pinned per fixture)"
        )
    committed_hash = corpus.compute_fixtures_hash(root)
    for posture in ("llm", "m2"):
        rel = "baselines/main.json" if posture == "llm" else "baselines/m2.json"
        baseline = schema.read_json(root / rel)
        issues += [f"{rel}: {issue}" for issue in schema.validate_baseline(baseline)]
        if baseline.get("fixtures_hash") != committed_hash:
            issues.append(f"{rel} fixtures_hash != on-disk corpus hash")
        cfg_posture = (baseline.get("config") or {}).get("extractor_posture")
        if cfg_posture != posture:
            issues.append(f"{rel}: config.extractor_posture {cfg_posture!r} != {posture!r}")
    verification = corpus.verify_manifest(root)
    if not verification["ok"]:
        issues.append(
            f"_manifest.json verification failed (missing={verification['missing']}, "
            f"extra={verification['extra']}, mismatched={verification['mismatched']}, "
            f"malformed={verification['malformed']})"
        )
    return issues


def _dump_json_bytes(doc: dict) -> bytes:
    return (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--check" in argv:
        drifted = check_drift()
        if drifted:
            print("CORPUS DRIFT:\n  " + "\n  ".join(drifted))
            return 1
        print("harness corpus is byte-identical to a fresh deterministic render")
        return 0
    if "--validate" in argv:
        issues = validate_committed()
        if issues:
            print("VALIDATION ISSUES:\n  " + "\n  ".join(issues))
            return 1
        print(f"committed harness corpus valid ({len(corpus.session_ids())} sessions)")
        return 0
    written = write_corpus()
    issues = validate_committed()
    if issues:
        print("GENERATOR OUTPUT FAILED VALIDATION:\n  " + "\n  ".join(issues))
        return 1
    print(f"wrote {len(written)} files ({len(corpus.session_ids())} sessions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
