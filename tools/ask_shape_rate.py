#!/usr/bin/env python3
"""B6 / objective 4 — the D3 ANSWER-SHAPE instrument (``shape_rate``).

ONE job: measure whether the product's REAL read path returns what a capture
stored — the claim, its provenance, and a verbatim grounding for the answer —
on a FIXED eval set, with a REAL, PINNED reader. This is a measurement tool
(``tools/`` script), NOT a surface: it exposes no tool, no endpoint, and
nothing users can call.

Why a NEW tool (#3910/#3911/#3914, PR #3929): ``tools/ask_spotcheck.py`` is a
different, correctness instrument (its aggregate is a semantic-judge score).
This one is the *answer-shape* instrument and it must NOT share its seeder
drift. It seeds through ``ask_spotcheck.seed_capture_turn_store`` — the ONE
capture-exact turn store every ask fixture in the tree writes through — so it
cannot teach a graph shape the capture path cannot produce (a ``sessionId``
property makes L2 pass where the ``CONTAINS`` edge is broken: the false-GREEN
#3911 removed).

THE RATE
  shape_rate = |{q in 21 : L1 and L2 and L3}| / 21   (denominator 21, no
  exclusions; a per-question exception is a FAIL, never dropped).

  L1 — abstention correctness, BOTH directions (the 3 ``_abs`` questions must
       abstain, the other 18 must not), read from the product's own
       ``abstained`` field, which is the model's WRITTEN decision. Never the
       retired blank->``NO_EVIDENCE_TEXT`` substitution (#2280).
  L2 — provenance / session identity. (a) the ask lane's
       ``retrieved_session_ids`` is non-empty AND a superset of the gold
       session set; (b) the SHIPPING handlers ``tortoise_search`` and
       ``tortoise_recall`` each return >=1 hit carrying a non-empty
       ``sessionId`` that IS a seeded session id (no fabrication) — the #3888
       lesson: an instrument that pins only the ask lane leaves the shipping
       surfaces unpinned.
  L3 — verbatim grounding (an honest LEXICAL FLOOR, not semantic entailment):
       the gold session's turn head is present in ``evidence`` AND the
       evidence and the COMMITTED answer share >=1 contiguous verbatim span
       of >=4 words.

  No LLM judge: all three legs are deterministic reads of product-returned
  fields. Reported alongside, never folded in: abstain p/n, provenance p/n,
  grounding p/n, ``ctx_recall``, ``retrieval_degraded``, ``_abs`` marker
  agreement.

READER — REAL, PINNED, ASSERTED
  ``--mock`` is FORBIDDEN and does not exist as a flag here: the eval lane's
  ``MockReader`` answers straight from the evidence, which makes L1/L3 GREEN
  by construction — an instrument that cannot move. The reader is the
  production ``build_reader_model()`` with ``TORTOISE_ASK_PROVIDER`` pinned to
  ``deepseek-direct`` and ``TORTOISE_ASK_MODEL`` to
  ``deepseek/deepseek-v4-flash``. Because the ladder puts all three keyed
  providers into a rotating pool, the pin is ENFORCED by narrowing the other
  provider keys out of the build environment (recorded in the receipt) and
  then ASSERTED per question on every question that RETURNS A RESULT: a
  result whose ``provider != "deepseek-direct"`` makes the run VOID, not a
  low rate. A per-question EXCEPTION is a FAIL (see THE RATE) — the two are
  kept distinct so a low rate is never laundered as a void, nor a void as a
  low rate.

MOVEMENT CONTROL (§32) — before the rate means anything
  Known-GREEN: the four committed recorded transports
  (``tests/fixtures/ask_llm_transcripts/``) driven through the real pipeline.
  Three deterministic mutations, each applied to the seeded graph (M1/M3) or
  to the retired code path (M2), compared per question against the
  deterministic-probe baseline:
    M1 delete the ``(:Session)-[:CONTAINS]->(:Point)`` edge  -> L2 must RED;
    M2 reinstate blank->``NO_EVIDENCE_TEXT``                  -> L1 must RED;
    M3 remove the gold session's turns                        -> L3 must RED
                                                                 and L1 must
                                                                 flip to
                                                                 abstain.
  FLAT => the instrument is decoration => the run is VOID, reported as VOID,
  never as "no effect".

PRE-REGISTERED DECISION RULE (frozen before the run)
  ADOPT iff shape_rate >= 0.80 on the frozen fixture with the pinned reader
  AND the movement control fires. Per-leg minimums so a headline cannot hide
  a dead leg: provenance 21/21, abstain 21/21, grounding >= 18/21.
  Comparability: the historical 0.90 (2026-09-04) used a qwen3.8-max reader +
  gpt-4o judge — a different reader AND a different instrument. It is NOT the
  baseline and this rate may not be compared to it.

Usage:
  python3 tools/ask_shape_rate.py --pin-sha <sha> [--mode full|live|movement|seed-timing]
"""
from __future__ import annotations

import argparse
import contextlib
import difflib
import hashlib
import itertools
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# #4105 port: the ask lane moved out of ``sdk`` (#3929 removed ``sdk.ask``);
# the lane AND its reader-model seams live in ``tortoise.ask_lane`` now.
import tortoise.ask_lane as ask_mod  # noqa: E402

# The ONE capture-shaped seeder (#3914) plus the shared leg primitives. Imported,
# never mirrored: a local copy is exactly how the shape drifts.
from tools.ask_spotcheck import (  # noqa: E402
    _gold_sessions_covered,
    _seed_memory,
    _to_iso_date,
    _turn_present,
)
from tortoise.ask_lane import run_ask_lane  # noqa: E402

FIXTURE_REL = os.path.join("tests", "fixtures", "ask_spotcheck_composition.json")
#: Frozen fixture identity. A changed fixture is a NEW instrument, not a moved
#: rate — the sha is asserted and there is deliberately NO env override.
FIXTURE_SHA256 = "7f4062643323af4e5d0fec0b98bb3e3ca8499362c3f7e07a83c55f07633d15fa"
FIXTURE_N = 21
FIXTURE_ABS = 3

PINNED_PROVIDER = "deepseek-direct"
PINNED_MODEL = "deepseek/deepseek-v4-flash"
#: Provider keys narrowed out of the build env so the pool resolves to exactly
#: the pinned provider (the 3-key ladder otherwise builds the venice-first
#: rotating pool and the wire is NOT deepseek-direct — measured, see the run).
NARROWED_PROVIDER_KEYS = ("OPENROUTER_API_KEY", "VENICE_API_KEY")

SHAPE_RATE_ADOPT = 0.80
GROUNDING_MIN = 18
SPAN_MIN_WORDS = 4
#: The verbatim-span search is exact (difflib longest common block over word
#: lists); this caps the reported span length only for readability.
SPAN_REPORT_WORDS = 15

TRANSCRIPTS_DIR = os.path.join(_REPO_ROOT, "tests", "fixtures",
                               "ask_llm_transcripts")

EXIT_ADOPT = 0
EXIT_NOT_ADOPT = 1
EXIT_PIN = 3
EXIT_FIXTURE = 4
EXIT_EMBEDDER = 5
EXIT_PROVIDER = 6
EXIT_USAGE = 2


# ── normalization + legs ────────────────────────────────────────────────────

_WORD_RE = re.compile(r"[a-z0-9]+")


def _normalize(text) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower()).strip()


def _words(text) -> list[str]:
    """Word tokens for the verbatim-span floor. Punctuation is stripped so a
    sentence-final period cannot break a span that IS verbatim ('Five trips.'
    vs 'five trips') — a lexical floor must not turn on punctuation."""
    return _WORD_RE.findall(_normalize(text))


def longest_common_span(evidence: str, answer: str, *,
                        min_words: int = SPAN_MIN_WORDS) -> str | None:
    """The longest CONTIGUOUS verbatim word span shared by ``evidence`` and
    ``answer`` — the L3 lexical floor. Returns the span (or None when the
    longest shared run is shorter than ``min_words``). Exact (difflib longest
    matching block over word-token lists), not a heuristic n-gram scan.
    """
    a = _words(evidence)
    b = _words(answer)
    if not a or not b:
        return None
    m = difflib.SequenceMatcher(a=a, b=b, autojunk=False).find_longest_match(
        0, len(a), 0, len(b))
    if m.size < min_words:
        return None
    return " ".join(a[m.a:m.a + m.size])


def _abs_question(question: dict) -> bool:
    return "_abs" in (question.get("question_id") or "")


def gold_sessions(question: dict) -> set[str]:
    return {s for s in (question.get("answer_session_ids") or []) if s}


def _gold_turns(question: dict) -> list[tuple[str, str]]:
    """Every turn of every gold session as ``(needle, verbatim_slice)``.

    ``needle`` is the normalized 200-char head — the same window
    ``_turn_present`` probes — so the probe transport answers when ANY
    gold-session turn reached the reader's context (retrieval may surface
    some turns of a gold session and not others). ``verbatim_slice`` is a raw
    verbatim slice of that turn, so an answered probe always shares an L3
    span with the evidence.
    """
    ids = question.get("haystack_session_ids") or []
    sessions = question.get("haystack_sessions") or []
    pos = {sid: i for i, sid in enumerate(ids)}
    out: list[tuple[str, str]] = []
    for g in gold_sessions(question):
        i = pos.get(g)
        if i is None or i >= len(sessions):
            continue
        for turn in sessions[i] or []:
            c = (turn.get("content") or "").strip()
            if not c:
                continue
            out.append((_normalize(c)[:200], " ".join(c.split()[:12])))
    return out


def l3_grounding(evidence: str, answer: str, question: dict) -> dict:
    """L3: gold turn head present in evidence AND >=1 verbatim span shared
    with the committed answer. LEXICAL FLOOR — not semantic entailment."""
    evidence_norm = _normalize(evidence)
    sessions = question.get("haystack_sessions") or []
    ids = question.get("haystack_session_ids") or []
    pos = {sid: i for i, sid in enumerate(ids)}
    head_present = False
    for g in gold_sessions(question):
        i = pos.get(g)
        if i is None or i >= len(sessions):
            continue
        if any(_turn_present(evidence_norm, t.get("content"))
               for t in sessions[i] or []):
            head_present = True
            break
    span = longest_common_span(evidence, answer)
    # UNCENSORED longest shared run (floor 1) — the L3 span floor is a
    # pre-registered choice, so the readout must not censor below it.
    raw_span = longest_common_span(evidence, answer, min_words=1)
    return {"gold_turn_head_present": head_present,
            "shared_span": span,
            "shared_span_words": len(span.split()) if span else 0,
            "longest_common_span": raw_span,
            "longest_common_span_words": (len(raw_span.split())
                                          if raw_span else 0),
            "ok": bool(head_present and span)}


def gold_session_turn_count(sdk, question: dict) -> int:
    """Turn Points reachable from the gold sessions — the M3 mutation's
    own post-condition (a mutation that silently no-ops would make the
    movement control meaningless)."""
    gold = sorted(gold_sessions(question))
    rows = sdk._get_proj().g.query(
        "MATCH (s:Session)-[:CONTAINS]->(p:Point) WHERE s.id IN $golds "
        "RETURN count(p)", params={"golds": gold}).result_set
    return int(rows[0][0]) if rows and rows[0] else 0


# ── pre-flight assertions (TREE PIN, fixture, embedder, reader) ─────────────

def assert_tree_pin(repo_root: str, pin: str) -> str:
    """Assert the tree HEAD equals ``pin`` and ABORT if it differs. A pinned
    value that no longer matches the tree is worse than no pin — it would
    launder a different tree as the verified one."""
    out = subprocess.run(["git", "-C", repo_root, "rev-parse", "HEAD"],
                         capture_output=True, text=True, check=False)
    if out.returncode != 0:
        print(f"ask_shape_rate: cannot read HEAD in {repo_root!r}: "
              f"{out.stderr.strip()}", file=sys.stderr)
        raise SystemExit(EXIT_PIN)
    actual = out.stdout.strip()
    if actual != pin:
        print(f"ask_shape_rate: TREE PIN MISMATCH — expected {pin}, tree HEAD "
              f"is {actual}. ABORT (never measure an unverified tree).",
              file=sys.stderr)
        raise SystemExit(EXIT_PIN)
    return actual


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_fixture_asserted() -> tuple[list[dict], dict]:
    """Load the COMMITTED fixture and assert its sha + shape. There is no
    fixture override: ``TORTOISE_SPOTCHECK_FIXTURE`` is deliberately not
    consulted — a substituted fixture is a different instrument."""
    path = os.path.join(_REPO_ROOT, FIXTURE_REL)
    actual = _sha256_file(path)
    if actual != FIXTURE_SHA256:
        print(f"ask_shape_rate: FIXTURE SHA MISMATCH — {path}\n  expected "
              f"{FIXTURE_SHA256}\n  actual   {actual}\n"
              f"A changed fixture is a NEW instrument, not a moved rate. "
              f"ABORT.", file=sys.stderr)
        raise SystemExit(EXIT_FIXTURE)
    with open(path) as f:
        data = json.load(f)
    questions = data["questions"] if isinstance(data, dict) else data
    n = len(questions)
    n_abs = sum(1 for q in questions if _abs_question(q))
    n_gold = sum(1 for q in questions if gold_sessions(q))
    n_sessions = sum(len(q.get("haystack_sessions") or []) for q in questions)
    n_turns = sum(len(s) for q in questions
                  for s in (q.get("haystack_sessions") or []))
    shape = {"path": FIXTURE_REL, "sha256": actual,
             "expected_sha256": FIXTURE_SHA256,
             "n_questions": n, "n_abs": n_abs, "n_with_gold": n_gold,
             "n_sessions": n_sessions, "n_turns": n_turns}
    if (n, n_abs, n_gold) != (FIXTURE_N, FIXTURE_ABS, FIXTURE_N):
        print(f"ask_shape_rate: FIXTURE SHAPE MISMATCH — expected "
              f"n={FIXTURE_N}, abs={FIXTURE_ABS}, with_gold={FIXTURE_N}; got "
              f"n={n}, abs={n_abs}, with_gold={n_gold}. ABORT.",
              file=sys.stderr)
        raise SystemExit(EXIT_FIXTURE)
    # A session WITHOUT its own haystack id seeds the synthetic ``sess-{i}``
    # placeholder, and an identity comparison against gold ids is then
    # meaningless — so the id list must be present and aligned for every
    # question this instrument measures.
    for q in questions:
        ids = q.get("haystack_session_ids") or []
        sessions = q.get("haystack_sessions") or []
        if len(ids) != len(sessions) or not all(
                isinstance(s, str) and s.strip() for s in ids):
            print(f"ask_shape_rate: FIXTURE ID MISMATCH on "
                  f"{q.get('question_id')!r} — haystack_session_ids must be "
                  f"present, non-blank and aligned with haystack_sessions "
                  f"({len(ids)} ids vs {len(sessions)} sessions). ABORT.",
                  file=sys.stderr)
            raise SystemExit(EXIT_FIXTURE)
    return questions, shape


def assert_embedder() -> dict:
    """#2985: without the embeddings extra the product degrades to FTS-only
    and a retrieval-quality claim is unmeasurable. Fail CLOSED — never record
    a keyword-only run as a hybrid one."""
    from tortoise.embeddings import EmbeddingModel
    model = EmbeddingModel.get()
    if model is None:
        print("ask_shape_rate: NO EMBEDDER — the dense leg is absent, so this "
              "would be a KEYWORD-ONLY run (#2985). Install the extras "
              "(`uv sync --extra embeddings --extra parity`) and re-run. "
              "ABORT (VOID, not a low rate).", file=sys.stderr)
        raise SystemExit(EXIT_EMBEDDER)
    return {"present": True, "class": type(model).__name__}


@contextlib.contextmanager
def pinned_reader_env():
    """Pin the ask-lane reader to the frozen provider+model.

    The other provider keys are narrowed OUT for the whole run: with all
    three keyed, ``resolve_reader_provider`` returns the full pool and
    ``_build_routing_model`` builds the venice-first ``RotatingModel`` — so an
    explicit ``TORTOISE_ASK_PROVIDER=deepseek-direct`` alone does NOT put the
    wire on deepseek-direct (measured). The narrowing is recorded in the
    receipt and the per-question provider is asserted — this is enforcement of
    the pin, never a silent substitution away from it.

    ``TORTOISE_API_URL`` is narrowed out too: the LOCAL lane is the surface
    under test, and the fleet shell exports a hosted URL — with it set, the
    ask lane refuses (it is eval-only and requires a local graph; the hosted
    ``/v1/ask`` surface was removed by #3849). Narrowing it is what lets the
    pinned tree run at all.
    """
    narrowed_keys = (*NARROWED_PROVIDER_KEYS, "TORTOISE_API_URL")
    saved: dict[str, str | None] = {
        k: os.environ.get(k) for k in narrowed_keys}
    saved["TORTOISE_ASK_PROVIDER"] = os.environ.get("TORTOISE_ASK_PROVIDER")
    saved["TORTOISE_ASK_MODEL"] = os.environ.get("TORTOISE_ASK_MODEL")
    for k in narrowed_keys:
        os.environ.pop(k, None)
    os.environ["TORTOISE_ASK_PROVIDER"] = PINNED_PROVIDER
    os.environ["TORTOISE_ASK_MODEL"] = PINNED_MODEL
    try:
        yield {"narrowed": [k for k in narrowed_keys if saved.get(k)],
               "provider": PINNED_PROVIDER, "model": PINNED_MODEL}
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def assert_reader_pin() -> dict:
    """Build the reader under the pinned env and assert it IS the production
    routing model on the pinned provider — never a mock."""
    from tortoise.model_adapters import build_reader_model, resolve_reader_provider
    primary, pool = resolve_reader_provider(PINNED_MODEL)
    if primary != PINNED_PROVIDER or pool != [PINNED_PROVIDER]:
        print(f"ask_shape_rate: READER POOL NOT PINNED — primary={primary!r} "
              f"pool={pool!r}; expected ['{PINNED_PROVIDER}']. ABORT.",
              file=sys.stderr)
        raise SystemExit(EXIT_PROVIDER)
    model = build_reader_model(PINNED_MODEL)
    cls = type(model).__name__
    if cls not in ("RoutingModel", "RotatingModel") or \
            getattr(model, "provider", None) != PINNED_PROVIDER:
        print(f"ask_shape_rate: READER NOT THE PINNED PRODUCTION MODEL — "
              f"class={cls!r} provider={getattr(model, 'provider', None)!r}. "
              f"ABORT (a mock here makes L1/L3 GREEN by construction).",
              file=sys.stderr)
        raise SystemExit(EXIT_PROVIDER)
    info = {"no_reader": False, "reader_class": cls,
            "provider": model.provider, "model": getattr(model, "model", None),
            "temperature": 0.0, "max_tokens": 500}
    with contextlib.suppress(Exception):
        model.close()
    return info


# ── seeding + per-question run ──────────────────────────────────────────────

_DB_SEQ = itertools.count()
_LAST_DOCKER_GRAPH: str | None = None


def _substrate_label() -> str:
    """A receipt-safe label for the store the run measured against.

    Never the raw ``TORTOISE_ASK_SHAPE_DB_URI`` — its documented form
    embeds a password, and a receipt is a tracked file that gets committed.
    """
    base = os.environ.get("TORTOISE_ASK_SHAPE_DB_URI", "").strip()
    if not base:
        return "embedded"
    from urllib.parse import urlparse
    u = urlparse(base)
    user = u.username or ""
    auth = f"{user}:***@" if (user or u.password) else ""
    port = f":{u.port}" if u.port else ""
    return f"{u.scheme}://{auth}{u.hostname or ''}{port}/{u.path.lstrip('/')}"


def _drop_docker_graph(base: str, name: str) -> None:
    """Best-effort delete of a scratch docker graph (keep the server's
    memory bounded — graphs accumulate across a run otherwise)."""
    from urllib.parse import urlparse
    try:
        import redis as _redis
        u = urlparse(base)
        client = _redis.Redis(host=u.hostname, port=u.port or 6379,
                              password=u.password or None)
        client.execute_command("GRAPH.DELETE", name)
    except Exception:  # noqa: BLE001, RUF100 — cleanup is best-effort
        pass


def _fresh_db(tag: str) -> str | None:
    """A per-call ISOLATED store.

    Default: a fresh embedded redislite FILE (the historical lane). When
    ``TORTOISE_ASK_SHAPE_DB_URI`` names a ``docker://`` base URI, use the
    docker server with a UNIQUE per-call graph instead: the embedded lane
    spawns one redislite server per question and cannot start one reliably
    under fleet load (measured ~50% startup failure at load > 60,
    ``No such file or directory`` on the unix socket), which injects
    substrate faults into the live rate. The docker lane has no
    per-question process to lose. The env var is a SUBSTRATE selector, not
    a measurement knob — it changes where the graph lives, never what is
    seeded or read.

    Docker graphs are named ``<base>_<tag>_<pid>_<seq>`` (unique per process,
    so two runs never append to each other's seed) and the PREVIOUS graph is
    deleted on the next call — the sdk for it is always closed first, and a
    server that accumulates every seeded graph hits its memory ceiling
    mid-run (observed: 12 questions faulting with ``DB refused writes ...
    memory ceiling``).
    """
    global _LAST_DOCKER_GRAPH
    base = os.environ.get("TORTOISE_ASK_SHAPE_DB_URI", "").strip()
    if base:
        from urllib.parse import urlparse
        parsed = urlparse(base)
        leaf = parsed.path.lstrip("/")
        if not leaf:
            raise SystemExit(
                "ask_shape_rate: TORTOISE_ASK_SHAPE_DB_URI must name a base "
                "GRAPH segment (e.g. docker://:pw@host:6379/askshape) — a "
                f"bare server URI is rejected: {base!r}")
        if _LAST_DOCKER_GRAPH:
            _drop_docker_graph(base, _LAST_DOCKER_GRAPH)
            _LAST_DOCKER_GRAPH = None
        name = f"{leaf}_{tag}_{os.getpid()}_{next(_DB_SEQ)}"
        os.environ["TORTOISE_DB_URI"] = (
            f"{parsed.scheme}://{parsed.netloc}/{name}")
        _LAST_DOCKER_GRAPH = name
        return None
    return os.path.join(tempfile.mkdtemp(prefix=f"askshape_{tag}_"), "t.db")


def _seeded_ids(question: dict) -> set[str]:
    return {s for s in (question.get("haystack_session_ids") or []) if s}


@contextlib.contextmanager
def _shipping_handlers(sdk):
    """Make the REAL MCP handlers (``tortoise_search`` / ``tortoise_recall``)
    resolve THIS sdk. Stdio transport so the handler gate is satisfied; the
    dev API key is cleared so the result does not depend on the ambient shell.
    Handlers are resolved by name — never through the MCP ``tortoise_ask``
    tool that PR #3929 removes."""
    import tortoise.mcp_server as mcp_mod
    from tortoise.mcp_auth import _transport_mode
    saved_key = os.environ.pop("TORTOISE_API_KEY", None)
    saved_sdk = mcp_mod.sdk
    token = _transport_mode.set("stdio")
    mcp_mod.sdk = sdk
    try:
        yield mcp_mod
    finally:
        _transport_mode.reset(token)
        mcp_mod.sdk = saved_sdk
        if saved_key is not None:
            os.environ["TORTOISE_API_KEY"] = saved_key


def _envelope(payload) -> dict:
    """A bounded, JSON-safe description of a handler payload that is NOT the
    expected list/results shape — so the receipt can distinguish a handler
    that returned an ERROR envelope from one with an unexpected shape. Without
    this the two are both recorded as ``null`` and the failure is
    untriageable (found by the run itself on gpt4_7a0daae1)."""
    if isinstance(payload, dict):
        if "error" in payload:
            return {"kind": "error",
                    "error": str(payload.get("error"))[:300]}
        return {"kind": "dict", "keys": sorted(str(k) for k in payload)[:12]}
    return {"kind": type(payload).__name__, "repr": str(payload)[:200]}


def _handler_session_ids(mcp_mod, sdk, question: dict) -> dict:
    """The ``sessionId`` set each SHIPPING handler carries for this question.
    Resolved by name so the instrument survives #3929's removal of the MCP
    ``tortoise_ask`` tool."""
    out: dict[str, object] = {"search": None, "recall": None}
    try:
        hits = mcp_mod.tortoise_search(question["question"], limit=40)
    except Exception as e:  # noqa: BLE001, RUF100 — handler failure = leg FAIL
        out["search"] = {"error": f"{type(e).__name__}: {e}"}
        hits = None
    if isinstance(hits, list):
        out["search"] = {"ids": [h.get("sessionId") or ""
                                 for h in hits if isinstance(h, dict)],
                         "n_hits": len(hits)}
    elif hits is not None:
        out["search"] = _envelope(hits)
    try:
        rec = mcp_mod.tortoise_recall(question["question"], mode="state",
                                      limit=10)
    except Exception as e:  # noqa: BLE001, RUF100
        out["recall"] = {"error": f"{type(e).__name__}: {e}"}
        rec = None
    if isinstance(rec, dict) and isinstance(rec.get("results"), list):
        out["recall"] = {
            "ids": [r.get("sessionId") or ""
                    for r in rec["results"] if isinstance(r, dict)],
            "n_hits": len(rec["results"])}
    elif rec is not None:
        out["recall"] = _envelope(rec)
    return out


def evaluate_question(sdk, question: dict, *, reader_mode: str,
                      probe=None, mcp_mod=None) -> dict:
    """Run one question end-to-end and evaluate L1/L2/L3.

    ``reader_mode`` is the caller's LABEL for the transport this question
    runs under: ``"live"`` (the pinned real reader), ``"probe"`` (a
    deterministic evidence-aware probe — requires ``probe``) or ``"blank"``
    (the blank transport that reproduces the pre-#2280 collapse). The
    transport itself is installed by the caller's monkeypatch, so this
    function ASSERTS the label and the supplied ``probe`` agree — a
    mislabelled run fails loud instead of silently measuring the wrong
    reader.
    """
    if reader_mode == "probe" and probe is None:
        raise ValueError("reader_mode='probe' requires a probe transport")
    if reader_mode != "probe" and probe is not None:
        raise ValueError(f"reader_mode={reader_mode!r} takes no probe")
    from tortoise.reader import _looks_abstained
    qid = question.get("question_id") or ""
    gold = gold_sessions(question)
    seeded = _seeded_ids(question)
    expect_abstain = _abs_question(question)
    t0 = time.monotonic()
    try:
        if probe is not None:
            probe.arm(question)
        result = run_ask_lane(
            sdk, question["question"],
            question_date=_to_iso_date(
                question.get("question_date") or ""))
    except Exception as e:  # noqa: BLE001, RUF100 — per-question FAIL
        # Canonical leg keys: every consumer (_pn, _leg_map, movement_report)
        # reads `l1_abstain`/`l2_provenance`/`l3_grounding`. An exception is a
        # FAIL on all three, never a dropped question.
        return {"question_id": qid, "expected_abstain": expect_abstain,
                "error": f"{type(e).__name__}: {e}",
                "abstained": None, "provider": None, "route": None,
                "model": None,
                "l1_abstain": False, "l2_provenance": False,
                "l3_grounding": False, "pass": False,
                "duration_ms": int((time.monotonic() - t0) * 1000)}
    if mcp_mod is not None:
        handlers = _handler_session_ids(mcp_mod, sdk, question)
    else:
        handlers = {"search": None, "recall": None}

    abstained = result.get("abstained")
    l1 = bool(abstained) is expect_abstain
    answer = result.get("answer") or ""
    marker_ok = (bool(abstained) and _looks_abstained(answer)) \
        if expect_abstain else None

    ask_ids = [i for i in (result.get("retrieved_session_ids") or []) if i]
    l2a = bool(ask_ids) and gold.issubset(set(ask_ids))

    def _handler_ok(side: dict | None) -> bool | None:
        if side is None or "ids" not in side:
            return False
        ids = [i for i in side["ids"] if i]
        if not ids:
            return False
        # Identity correctness: names a seeded session, never fabricates.
        return set(ids).issubset(seeded)

    l2b_search = _handler_ok(handlers.get("search"))
    l2b_recall = _handler_ok(handlers.get("recall"))
    l2 = bool(l2a and l2b_search and l2b_recall)

    g = l3_grounding(result.get("evidence") or "", answer, question)
    l3 = bool(g["ok"])
    # REPORTED-ALONGSIDE triage readout (not a leg): is the fixture's own GOLD
    # ANSWER in the reader's context at all? Separates a READER miss (the
    # facts were in front of it and it abstained) from a RETRIEVAL/ASSEMBLY
    # miss (the facts never reached the reader).
    gold_span = longest_common_span(
        result.get("evidence") or "", question.get("answer") or "",
        min_words=1)
    return {
        "question_id": qid,
        "expected_abstain": expect_abstain,
        "abstained": bool(abstained),
        "answer_head": answer[:160],
        "provider": result.get("provider"),
        "route": result.get("route"),
        "model": result.get("model"),
        "duration_ms": int((time.monotonic() - t0) * 1000),
        "l1_abstain": l1,
        "_abs_marker_agreement": marker_ok,
        "ask_session_ids": ask_ids,
        "gold_sessions": sorted(gold),
        "gold_covered_ask": bool(l2a),
        "handler_search": handlers.get("search"),
        "handler_recall": handlers.get("recall"),
        "l2_provenance": l2,
        "l2a_ask_gold": bool(l2a),
        "l2b_search": l2b_search,
        "l2b_recall": l2b_recall,
        "l3_grounding": l3,
        "l3": g,
        "gold_answer_span": gold_span,
        "gold_answer_span_words": (len(gold_span.split()) if gold_span else 0),
        "ctx_recall": _gold_sessions_covered(result.get("evidence") or "",
                                             question),
        "retrieval_degraded": result.get("retrieval_degraded"),
        "pass": bool(l1 and l2 and l3),
    }


# ── deterministic movement-control transport ────────────────────────────────

class ProbeReader:
    """Evidence-aware deterministic transport for the movement control.

    Abstains on the ``_abs`` questions (they must abstain) and otherwise
    answers with a VERBATIM gold-turn slice when any gold-session turn is in
    the reader's context; abstains when none is. This makes the baseline
    GREEN on the legs the mutations are named to move, so exactly one graph
    mutation (M1/M3) or code mutation (M2) shows up as a RED — the
    instrument's sensitivity is what the control tests, and a real reader
    adds variance that hides it.
    """

    model = "probe-reader"
    provider = "probe"
    last_completion_tokens = 24
    last_prompt_tokens = 1
    last_finish_reason = "stop"

    def __init__(self) -> None:
        self._turns: list[tuple[str, str]] = []
        self._expect_abstain = False

    def arm(self, question: dict) -> None:
        self._turns = _gold_turns(question)
        self._expect_abstain = _abs_question(question)

    def complete(self, *, system: str, user: str,
                 max_tokens: int | None = None) -> str:
        del system, max_tokens
        if self._expect_abstain:
            return "I don't have that information."
        norm = _normalize(user)
        for needle, verbatim in self._turns:
            if needle and needle in norm:
                return verbatim or "no evidence"
        return "I don't have that information."

    def close(self) -> None:
        pass


class BlankReader:
    """The pre-#2280 reasoner collapse: the reader emits NOTHING (the whole
    output budget consumed thinking), ``finish_reason="length"``."""

    model = "blank-reader"
    provider = "blank"
    last_completion_tokens = 0
    last_prompt_tokens = 1
    last_finish_reason = "length"

    def complete(self, *, system: str, user: str,
                 max_tokens: int | None = None) -> str:
        del system, user, max_tokens
        return ""

    def close(self) -> None:
        pass


def _retired_reader_complete(model, *, system: str, user: str):
    """The RETIRED pre-#2280 path: one raw call, blank output passed through
    (never escalated, never fail-loud), so the lane's surviving defensive
    invariant substitutes ``NO_EVIDENCE_TEXT`` and labels it abstained."""
    raw = model.complete(system=system, user=user)
    return raw, int(getattr(model, "last_completion_tokens", 0) or 0)


# ── movement mutations ─────────────────────────────────────────────────────

def mutate_m1_drop_contains(sdk) -> int:
    """M1: delete every ``(:Session)-[:CONTAINS]->(:Point)`` edge. Returns
    the remaining edge count (must be 0 — a silent no-op would make the
    control meaningless)."""
    sdk._get_proj().g.query(
        "MATCH (:Session)-[r:CONTAINS]->(:Point) DELETE r")
    left = sdk._get_proj().g.query(
        "MATCH (:Session)-[r:CONTAINS]->(:Point) RETURN count(r)"
    ).result_set
    remaining = int(left[0][0]) if left and left[0] else 0
    if remaining:
        raise RuntimeError(f"M1 mutation did not take: {remaining} CONTAINS "
                           f"edges remain")
    return remaining


def mutate_m3_drop_gold_turns(sdk, question: dict) -> int:
    """M3: remove the gold session's turn Points (and the Session nodes).
    Returns the remaining gold turn count (must be 0)."""
    gold = sorted(gold_sessions(question))
    sdk._get_proj().g.query(
        "MATCH (s:Session)-[r:CONTAINS]->(p:Point) WHERE s.id IN $golds "
        "DELETE r, p", params={"golds": gold})
    sdk._get_proj().g.query(
        "MATCH (s:Session) WHERE s.id IN $golds DELETE s",
        params={"golds": gold})
    remaining = gold_session_turn_count(sdk, question)
    if remaining:
        raise RuntimeError(f"M3 mutation did not take: {remaining} gold turn "
                           f"Points remain")
    return remaining


# ── analysis ───────────────────────────────────────────────────────────────

def _pn(records: list[dict], key: str) -> dict:
    hits = sum(1 for r in records if r.get(key))
    return {"passed": hits, "n": len(records),
            "rate": (hits / len(records)) if records else 0.0}


def _leg_map(records: list[dict]) -> dict:
    return {r["question_id"]: {k: bool(r.get(k)) for k in
                               ("l1_abstain", "l2_provenance", "l3_grounding")}
            for r in records}


def _compact(records: list[dict]) -> list[dict]:
    """Receipt-sized per-question view of a movement arm."""
    return [{"question_id": r.get("question_id"),
             "l1": bool(r.get("l1_abstain")),
             "l2": bool(r.get("l2_provenance")),
             "l3": bool(r.get("l3_grounding")),
             "abstained": r.get("abstained"),
             "error": r.get("error")}
            for r in records]


def _err_qids(records: list[dict]) -> set:
    """Question ids whose record is a per-question EXCEPTION (top-level
    ``error``) — a run/arm fault, never usable as movement evidence: an
    all-red fault record would otherwise count as a 'flip' and let a
    substrate failure satisfy the movement gate."""
    return {r.get("question_id") for r in records if r.get("error")}


def _substrate_error(rec: dict) -> dict | None:
    """A per-question substrate fault, from EITHER source, with a
    discriminator — a top-level exception (``run``) or a shipping handler
    that returned an ERROR envelope (``handler_search``/``handler_recall``).
    Both lower a leg while leaving the aggregate looking like a quality
    result, so both must reach the receipt's summary."""
    qid = rec.get("question_id")
    if rec.get("error"):
        return {"question_id": qid, "source": "run",
                "error": str(rec["error"])[:300]}
    for side in ("handler_search", "handler_recall"):
        payload = rec.get(side)
        if isinstance(payload, dict) and (
                payload.get("kind") == "error" or "error" in payload):
            return {"question_id": qid, "source": side,
                    "error": str(payload.get("error"))[:300]}
    return None


def movement_report(baseline: list[dict], mutated: list[dict],
                    leg: str) -> dict:
    """Compare one mutation arm to the baseline per question. ``leg`` is the
    leg the mutation is NAMED to move; the report records the named-leg
    movement plus any collateral movement (reported, never hidden).

    Questions whose record is a per-question EXCEPTION are EXCLUDED from
    flip detection and named in ``excluded_errors``: an all-red fault record
    is not evidence that the mutation moved the leg, and crediting it would
    let a substrate failure satisfy the movement gate (the 'instrument is
    decoration' hazard the gate exists to prevent).
    """
    base = _leg_map(baseline)
    mut = _leg_map(mutated)
    keys = {"l1_abstain": "L1", "l2_provenance": "L2", "l3_grounding": "L3"}
    named = keys[leg]
    faulted = _err_qids(baseline) | _err_qids(mutated)
    named_flips, collateral, excluded = [], {}, []
    for qid, b in base.items():
        m = mut.get(qid)
        if m is None or qid in faulted:
            if qid in faulted:
                excluded.append(qid)
            continue
        for k, label in keys.items():
            if b[k] and not m[k]:
                if k == leg:
                    named_flips.append(qid)
                else:
                    collateral.setdefault(label, []).append(qid)
    # direction counts for L1 (both directions are part of the leg)
    l1_dir = {"must_abstain_green": 0, "must_abstain_red": 0,
              "must_not_abstain_green": 0, "must_not_abstain_red": 0}
    for r in mutated:
        if "_abs" in (r.get("question_id") or ""):
            k = ("must_abstain_green" if r.get("l1_abstain")
                 else "must_abstain_red")
        else:
            k = ("must_not_abstain_green" if r.get("l1_abstain")
                 else "must_not_abstain_red")
        l1_dir[k] += 1
    return {"named_leg": named, "named_leg_flips": named_flips,
            "named_leg_moved": bool(named_flips),
            "collateral": collateral, "l1_directions": l1_dir,
            "baseline_errors": len(_err_qids(baseline)),
            "mutated_errors": len(_err_qids(mutated)),
            "excluded_errors": sorted(excluded)}


# ── known-GREEN (committed recorded transports) ─────────────────────────────

class _ReplayReader:
    def __init__(self, completion: str):
        self.completion = completion
        self.last_completion_tokens = 12
        self.last_prompt_tokens = 1
        self.last_finish_reason = "stop"
        self.user_message = None
        self.calls = 0

    def complete(self, *, system: str, user: str,
                 max_tokens: int | None = None) -> str:
        del system, max_tokens
        self.calls += 1
        self.user_message = user
        return self.completion

    def close(self) -> None:
        pass


def known_green(*, n_questions: int | None = None) -> dict:
    """Drive the committed recorded transports through the REAL pipeline and
    assert their recorded verdicts. Deterministic, no provider calls."""
    import tortoise.sdk as sdk_mod
    from tools.gen_ask_transcripts import _seed
    from tortoise.reader import NO_EVIDENCE_TEXT

    results = []
    if not os.path.isdir(TRANSCRIPTS_DIR):
        return {"ok": False, "reason": "transcripts dir missing", "cases": []}
    for name in sorted(os.listdir(TRANSCRIPTS_DIR)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(TRANSCRIPTS_DIR, name)) as f:
            tx = json.load(f)
        for attempt in _attempts():
            ask_mod._reset_ask_reader_cache_for_tests()
            sdk = sdk_mod.TortoiseSDK(_fresh_db(f"kg{attempt}"))
            replay = _ReplayReader(tx["completion"])
            saved = ask_mod._default_ask_reader_factory
            ask_mod._default_ask_reader_factory = lambda replay=replay: replay
            try:
                _seed(sdk, tx["seeds"])
                res = run_ask_lane(
                    sdk, tx["question"],
                    question_date=tx.get("question_date"))
            except Exception:  # noqa: BLE001, RUF100 — infrastructure fault
                sdk.close()
                ask_mod._default_ask_reader_factory = saved
                ask_mod._reset_ask_reader_cache_for_tests()
                if attempt != _attempts()[-1]:
                    time.sleep(1.0)
                continue
            break
        else:
            results.append({"fixture": tx["fixture"], "ok": False,
                            "error": "known-green could not run "
                                     "(substrate faults on every attempt)"})
            continue
        try:
            abstained = bool(res.get("abstained"))
            ok = abstained is tx["expected_abstained"]
            if ok and tx.get("expect_superseded_markers"):
                ok = "[SUPERSEDED BY:" in (res.get("evidence") or "")
            if ok and not tx["expected_abstained"]:
                ok = (res.get("answer") or "") == tx["completion"].strip()
            if ok and tx["expected_abstained"]:
                ok = (res.get("answer") in (tx["completion"], NO_EVIDENCE_TEXT))
            results.append({"fixture": tx["fixture"], "ok": ok,
                            "abstained": abstained,
                            "expected_abstained": tx["expected_abstained"]})
        finally:
            ask_mod._default_ask_reader_factory = saved
            sdk.close()
            ask_mod._reset_ask_reader_cache_for_tests()
    return {"ok": bool(results) and all(r["ok"] for r in results),
            "cases": results}


# ── seed phase ──────────────────────────────────────────────────────────────

def seed_timing(questions: list[dict], n: int = 1) -> dict:
    """Time ``_seed_memory`` — the unmeasured cost that dominates wall-clock.
    Step 1 of the run: report this BEFORE launching the full 21."""
    from tortoise.sdk import TortoiseSDK
    measured = []
    for q in questions[:n]:
        turns = sum(len(s) for s in (q.get("haystack_sessions") or []))
        db = _fresh_db("phase")
        t0 = time.monotonic()
        sdk = TortoiseSDK(db)
        _seed_memory(sdk, q)
        dt = time.monotonic() - t0
        sdk.close()
        measured.append({"question_id": q.get("question_id"), "turns": turns,
                         "seed_s": round(dt, 2)})
    total_turns = sum(len(s) for q in questions
                      for s in (q.get("haystack_sessions") or []))
    per_turn = None
    if measured and measured[0]["turns"]:
        per_turn = measured[0]["seed_s"] / measured[0]["turns"]
    return {"measured": measured,
            "total_turns_all_21": total_turns,
            "extrapolated_full_21_s": (round(per_turn * total_turns, 1)
                                       if per_turn else None)}


# ── main ────────────────────────────────────────────────────────────────────

def _attempts() -> tuple[int, ...]:
    """Attempts per question. A transient substrate failure (an embedded
    FalkorDB socket vanishing mid-run — OBSERVED repeatedly on a
    load-average-100+ host) is an INFRASTRUCTURE fault, not a product
    result; retrying on a fresh store keeps a valid measurement obtainable
    without laundering the fault. Only a question that fails EVERY attempt is
    recorded as a per-question FAIL, with its reason and the attempt count.

    Callers pass ``expected_error_prefix`` for an arm whose errors are its
    DESIGNED outcome (the M2-control arm's ``AskReaderUnavailable``): such an
    attempt counts as complete, so a genuine fault alongside it still retries.
    """
    return (1, 2, 3)


def _fault_record(question: dict, error: str, attempts: int) -> dict:
    """Canonical all-legs-FAIL record for a question that could not be run.
    Every consumer reads the canonical keys, so a fault counts as a FAIL
    (never a dropped question) and stays visible."""
    return {"question_id": question.get("question_id"),
            "expected_abstain": _abs_question(question),
            "error": error, "attempts": attempts,
            "abstained": None, "provider": None, "route": None,
            "model": None, "duration_ms": None,
            "l1_abstain": False, "l2_provenance": False,
            "l3_grounding": False, "pass": False}


def _run_arm(questions: list[dict], *, arm: str, probe: ProbeReader | None,
             blank: bool, retired_substitution: bool,
             mutation=None, limit: int | None = None,
             expected_error_prefix: str | None = None) -> list[dict]:
    """Seed + run one movement-control arm over the fixture (bounded by
    ``limit``). Deterministic transports; no provider calls."""
    import tortoise.sdk as sdk_mod
    records = []
    subset = questions[:limit] if limit else questions
    attempts = _attempts()
    for q in subset:
        rec: dict | None = None
        last_err = ""
        for attempt in attempts:
            ask_mod._reset_ask_reader_cache_for_tests()
            sdk = sdk_mod.TortoiseSDK(_fresh_db(f"m_{arm}_{attempt}"))
            reader = BlankReader() if blank else probe
            saved = ask_mod._default_ask_reader_factory
            ask_mod._default_ask_reader_factory = lambda r=reader: r
            saved_arc = ask_mod._ask_reader_complete
            if retired_substitution:
                ask_mod._ask_reader_complete = _retired_reader_complete
            try:
                _seed_memory(sdk, q)
                if mutation is not None:
                    mutation(sdk, q)
                import tortoise.mcp_server as mcp_mod
                with _shipping_handlers(sdk):
                    rec = evaluate_question(
                        sdk, q, reader_mode="blank" if blank else "probe",
                        probe=reader if not blank else None, mcp_mod=mcp_mod)
            except Exception as e:  # noqa: BLE001, RUF100 — infrastructure fault
                rec = None
                last_err = f"{type(e).__name__}: {e}"
            finally:
                ask_mod._ask_reader_complete = saved_arc
                ask_mod._default_ask_reader_factory = saved
                sdk.close()
                ask_mod._reset_ask_reader_cache_for_tests()
            fault = _substrate_error(rec) if rec is not None else None
            designed = bool(
                fault and expected_error_prefix
                and str(fault.get("error", "")).startswith(
                    expected_error_prefix))
            if rec is not None and (fault is None or designed):
                rec["attempts"] = attempt
                break
            if fault is not None:
                last_err = str(fault.get("error") or "")
            rec = None
            if attempt != attempts[-1]:
                time.sleep(1.0)   # let a thrashing host settle
        if rec is None:
            rec = _fault_record(q, last_err, attempts[-1])
        records.append(rec)
    return records


def run_full(args, questions: list[dict], fixture_shape: dict) -> int:
    receipt: dict = {
        "instrument": "tools/ask_shape_rate.py",
        "instrument_sha256": _sha256_file(os.path.abspath(__file__)),
        "generated_at": datetime.now(UTC).isoformat(),
        "fixture": fixture_shape,
        # Which store the rate was measured against. The docker selector
        # (TORTOISE_ASK_SHAPE_DB_URI) is a substrate change the SDK branches
        # on, so it is recorded rather than implied by the command line —
        # REDACTED (the URI carries a password; the receipt is committed).
        "substrate": _substrate_label(),
        "decision_rule": {
            "shape_rate_min": SHAPE_RATE_ADOPT,
            "provenance_min": f"{FIXTURE_N}/{FIXTURE_N}",
            "abstain_min": f"{FIXTURE_N}/{FIXTURE_N}",
            "grounding_min": f"{GROUNDING_MIN}/{FIXTURE_N}",
            "movement_control_required": True,
            "comparability": ("the historical 0.90 (2026-09-04) used a "
                              "qwen3.8-max reader + gpt-4o judge — a different "
                              "reader AND a different instrument; it is NOT "
                              "the baseline and this rate may not be compared "
                              "to it"),
        },
        "legs": {
            "L1": "abstention correctness both directions, product's own "
                  "`abstained` over the WRITTEN answer",
            "L2": "ask lane retrieved_session_ids non-empty and superset of "
                  "gold; shipping tortoise_search/tortoise_recall carry "
                  "non-empty sessionId that IS a seeded session",
            "L3": "gold turn head in evidence AND >=1 contiguous verbatim "
                  "span (>=4 words) shared with the committed answer — a "
                  "LEXICAL FLOOR, not semantic entailment",
        },
    }

    embedder = assert_embedder()
    receipt["embedder"] = embedder

    with pinned_reader_env() as env_info:
        receipt["reader_env"] = env_info
        receipt["reader"] = assert_reader_pin()
        import tortoise.sdk as sdk_mod
        ask_mod._reset_ask_reader_cache_for_tests()
        # The movement arms monkeypatch the reader seam; capture the REAL
        # seam and restore it before the pinned measurement so a leaked
        # patch can never taint the live rate.
        _real_factory = ask_mod._default_ask_reader_factory
        _real_arc = ask_mod._ask_reader_complete

        if args.phase_seed:
            st = seed_timing(questions, n=args.phase_seed)
            receipt["seed_phase"] = st
            print(f"[seed-phase] {st['measured'][0]['question_id']}: "
                  f"{st['measured'][0]['seed_s']}s for "
                  f"{st['measured'][0]['turns']} turns; extrapolated full-21 "
                  f"seed ~{st['extrapolated_full_21_s']}s")

        if args.mode in ("full", "movement"):
            kg = known_green()
            receipt["known_green"] = kg
            print(f"[known-green] recorded transports: "
                  f"{sum(1 for c in kg['cases'] if c['ok'])}/"
                  f"{len(kg['cases'])} pass")

            mov: dict = {}
            print("[movement] baseline (deterministic probe)...")
            base = _run_arm(questions, arm="base", probe=ProbeReader(),
                            blank=False, retired_substitution=False,
                            limit=args.movement_limit)
            mov["baseline"] = {"l1_pn": _pn(base, "l1_abstain"),
                               "l2_pn": _pn(base, "l2_provenance"),
                               "l3_pn": _pn(base, "l3_grounding"),
                               "per_question": _compact(base)}
            print("[movement] M1 (drop CONTAINS edges)...")
            m1 = _run_arm(questions, arm="m1", probe=ProbeReader(),
                          blank=False, retired_substitution=False,
                          mutation=lambda sdk, q: mutate_m1_drop_contains(sdk),
                          limit=args.movement_limit)
            print("[movement] M2 (blank output, retired substitution)...")
            m2 = _run_arm(questions, arm="m2", probe=None, blank=True,
                          retired_substitution=True,
                          limit=args.movement_limit)
            print("[movement] M2-control (blank output, fixed fail-loud)...")
            m2c = _run_arm(questions, arm="m2c", probe=None, blank=True,
                           retired_substitution=False,
                           expected_error_prefix="AskReaderUnavailable",
                           limit=args.movement_limit)
            print("[movement] M3 (drop gold sessions' turns)...")
            m3 = _run_arm(questions, arm="m3", probe=ProbeReader(),
                          blank=False, retired_substitution=False,
                          mutation=mutate_m3_drop_gold_turns,
                          limit=args.movement_limit)
            mov["M1"] = movement_report(base, m1, "l2_provenance")
            mov["M1"]["per_question"] = _compact(m1)
            mov["M2"] = movement_report(base, m2, "l1_abstain")
            mov["M2"]["per_question"] = _compact(m2)
            mov["M2_control"] = movement_report(base, m2c, "l1_abstain")
            mov["M2_control"]["per_question"] = _compact(m2c)
            mov["M3_L3"] = movement_report(base, m3, "l3_grounding")
            mov["M3_L1"] = movement_report(base, m3, "l1_abstain")
            mov["M3"] = {"per_question": _compact(m3)}
            fired = {
                "M1_l2_red": mov["M1"]["named_leg_moved"],
                "M2_l1_red": mov["M2"]["named_leg_moved"],
                "M3_l3_red": mov["M3_L3"]["named_leg_moved"],
                "M3_l1_red": mov["M3_L1"]["named_leg_moved"],
            }
            # An arm carrying per-question EXCEPTIONS is not valid movement
            # evidence: its faults are excluded from flip detection above and
            # its flips are not credited here. A faulted arm makes the whole
            # control NOT fired (=> VOID), never quietly fired.
            # ⚠ The M2-CONTROL arm's AskReaderUnavailable records are its
            # DESIGNED outcome — it demonstrates that the fixed code fails
            # loud on a blank output instead of fabricating an abstention
            # (that is the whole comparison against M2). The exemption is
            # CAUSE-scoped, not arm-scoped: any OTHER exception in that arm is
            # a genuine fault and VOIDs the control like any other.
            m2c_expected, m2c_unexpected = set(), set()
            for r in m2c:
                if not r.get("error"):
                    continue
                if str(r["error"]).startswith("AskReaderUnavailable"):
                    m2c_expected.add(r.get("question_id"))
                else:
                    m2c_unexpected.add(r.get("question_id"))
            faulted_arms = [
                name for name, recs in
                (("baseline", base), ("M1", m1), ("M2", m2), ("M3", m3))
                if _err_qids(recs)]
            if m2c_unexpected:
                faulted_arms.append("M2_control")
            fired["arms_error_free"] = not faulted_arms
            fired["faulted_arms"] = faulted_arms
            fired["M2_control_expected_failures"] = len(m2c_expected)
            fired["M2_control_unexpected_failures"] = sorted(m2c_unexpected)
            fired["all"] = (all(fired[k] for k in
                                ("M1_l2_red", "M2_l1_red", "M3_l3_red",
                                 "M3_l1_red"))
                            and fired["arms_error_free"])
            mov["fired"] = fired
            # The substitution's SIGNATURE: with a blank output the retired
            # substitution launders a reader failure into a fabricated
            # abstention, so the must-abstain direction goes GREEN under M2
            # while the fixed fail-loud control keeps it RED.
            mov["substitution_signature"] = {
                "must_abstain_green_M2":
                    mov["M2"]["l1_directions"]["must_abstain_green"],
                "must_abstain_green_M2_control":
                    mov["M2_control"]["l1_directions"]["must_abstain_green"],
                "must_not_abstain_red_M2":
                    mov["M2"]["l1_directions"]["must_not_abstain_red"],
                "must_not_abstain_red_M2_control":
                    mov["M2_control"]["l1_directions"]["must_not_abstain_red"],
            }
            receipt["movement"] = mov
            for k in ("M1_l2_red", "M2_l1_red", "M3_l3_red", "M3_l1_red"):
                print(f"[movement] {k} = {fired[k]}")
            if not fired["all"]:
                # Name the ACTUAL cause. A faulted arm is not a flat control:
                # reporting it as "decoration" would tell an operator to
                # discard a working instrument over a transient substrate
                # fault.
                void_reasons: list[str] = []
                if not fired["arms_error_free"]:
                    void_reasons.append(
                        "movement control INVALID — per-question fault(s) in "
                        "arm(s): " + ", ".join(fired["faulted_arms"]) +
                        " (a fault is not movement evidence); reported VOID")
                flat = [k for k in ("M1_l2_red", "M2_l1_red", "M3_l3_red",
                                    "M3_l1_red") if not fired[k]]
                if flat:
                    void_reasons.append(
                        "movement control FLAT — the named leg did not move "
                        f"({', '.join(flat)}); the instrument is decoration; "
                        "reported VOID, never 'no effect'")
                receipt["verdict"] = {"decision": "VOID",
                                      "reasons": void_reasons}
                _write_receipt(args, receipt)
                return EXIT_NOT_ADOPT
            if args.mode == "movement":
                receipt["verdict"] = {
                    "decision": "MOVEMENT-ONLY",
                    "reasons": ["--mode movement: the measurement was not run; "
                                "the movement control result stands alone"],
                }
                _write_receipt(args, receipt)
                return EXIT_ADOPT

        # ── the live run (REAL pinned reader over all 21) ──────────────────
        ask_mod._default_ask_reader_factory = _real_factory
        ask_mod._ask_reader_complete = _real_arc
        ask_mod._reset_ask_reader_cache_for_tests()
        live: list[dict] = []
        for i, q in enumerate(questions):
            rec: dict | None = None
            last_err = ""
            live_attempts = _attempts()
            for attempt in live_attempts:
                ask_mod._reset_ask_reader_cache_for_tests()
                sdk = sdk_mod.TortoiseSDK(_fresh_db(f"live_{attempt}"))
                try:
                    _seed_memory(sdk, q)
                    import tortoise.mcp_server as mcp_mod
                    with _shipping_handlers(sdk):
                        rec = evaluate_question(sdk, q, reader_mode="live",
                                                mcp_mod=mcp_mod)
                except Exception as e:  # noqa: BLE001, RUF100
                    rec = None
                    last_err = f"{type(e).__name__}: {e}"
                finally:
                    sdk.close()
                    ask_mod._reset_ask_reader_cache_for_tests()
                if rec is not None and _substrate_error(rec) is None:
                    rec["attempts"] = attempt
                    break
                if rec is not None:
                    fault = _substrate_error(rec)
                    last_err = ((fault or {}).get("error")
                                or str(rec.get("error") or ""))
                rec = None
                if attempt != live_attempts[-1]:
                    time.sleep(1.0)
            if rec is None:
                rec = _fault_record(q, last_err, live_attempts[-1])
            # A per-question exception is a FAIL, never a VOID and never a
            # dropped question — only a RESULT whose provider is not the pin
            # makes the run VOID (a low rate must not be laundered as a void,
            # and a void must not be laundered as a low rate).
            if not rec.get("error") and rec.get("provider") != PINNED_PROVIDER:
                print(f"ask_shape_rate: PROVIDER VOID on "
                      f"{rec['question_id']} — got {rec.get('provider')!r}, "
                      f"expected {PINNED_PROVIDER!r}. The run is VOID, not a "
                      f"low rate.", file=sys.stderr)
                receipt["live"] = {"per_question": [*live, rec]}
                receipt["verdict"] = {
                    "decision": "VOID",
                    "reasons": [f"provider != {PINNED_PROVIDER} on "
                                f"{rec['question_id']} "
                                f"(got {rec.get('provider')!r})"],
                }
                _write_receipt(args, receipt)
                return EXIT_PROVIDER
            live.append(rec)
            print(f"[{i+1}/{len(questions)}] {rec['question_id'][:34]:34s} "
                  f"L1={int(bool(rec.get('l1_abstain')))} "
                  f"L2={int(bool(rec.get('l2_provenance')))} "
                  f"L3={int(bool(rec.get('l3_grounding')))} "
                  f"pass={int(bool(rec.get('pass')))} "
                  f"abs={int(bool(rec.get('abstained')))} "
                  f"ctx={rec.get('ctx_recall')} "
                  f"prov={rec.get('provider')} "
                  f"{rec.get('duration_ms')}ms "
                  f"{rec.get('error') or ''}")

        shape_rate = (sum(1 for r in live if r.get("pass")) / len(live)
                      if live else 0.0)
        receipt["live"] = {
            "per_question": live,
            "shape_rate": shape_rate,
            "abstain_pn": _pn(live, "l1_abstain"),
            "provenance_pn": _pn(live, "l2_provenance"),
            "grounding_pn": _pn(live, "l3_grounding"),
            "ctx_recall_pn": _pn(live, "ctx_recall"),
            "retrieval_degraded_pn": _pn(live, "retrieval_degraded"),
            "_abs_marker_agreement": [
                {"question_id": r["question_id"],
                 "agreement": r.get("_abs_marker_agreement")}
                for r in live if "_abs" in (r.get("question_id") or "")],
            # REPORTED-ALONGSIDE triage: was the fixture's own GOLD ANSWER
            # ever in front of the reader? (>=3-word span = present.)
            # Denominator = the 18 ANSWERABLE questions; an _abs question's
            # gold answer is absent from the corpus by design.
            "gold_answer_in_evidence_pn": {
                "passed": sum(1 for r in live
                              if not r.get("expected_abstain")
                              and r.get("gold_answer_span_words", 0) >= 3),
                "n": sum(1 for r in live if not r.get("expected_abstain")),
                "note": ("answerable questions only; >=3 contiguous shared "
                         "words counts as present")},
            "providers_observed": sorted({str(r.get("provider"))
                                          for r in live}),
            # The L3 span floor is a pre-registered CHOICE (4 words); the
            # counts at other floors are reported so the choice is auditable.
            "grounding_span_sensitivity": {
                f"span_ge_{floor}_words": {
                    "passed": sum(
                        1 for r in live
                        if (r.get("l3") or {}).get("gold_turn_head_present")
                        and (r.get("l3") or {}).get(
                            "longest_common_span_words", 0) >= floor),
                    "n": len(live)}
                for floor in (1, 2, 3, 4, 5, 6)},
        }
        degraded = [r for r in live if r.get("retrieval_degraded")]
        substrate = [e for r in live if (e := _substrate_error(r))]
        receipt["live"]["substrate_errors"] = substrate
        receipt["caveats"] = [
            ("L3 is a LEXICAL FLOOR, not semantic entailment: the gold turn "
             "head must be in evidence AND the evidence and the committed "
             "answer must share >= " + str(SPAN_MIN_WORDS) + " contiguous "
             "words."),
            ("L1 reads the product's own `abstained` FIELD, which is the "
             "clause-scoped `_looks_abstained` predicate over the written "
             "answer — a hedged answer whose phrasing is outside that "
             "vocabulary is not flagged. The receipt carries every "
             "answer_head so the field can be audited."),
            ("The capture-shaped seed writes turn Points with NO stored "
             "embedding (capture's turn store writes none either), so the "
             "dense leg is inert (`no_embeddings`) and this rate is the "
             "SPARSE (FTS+RRF) lane's answer-shape rate.") if degraded
             else "no retrieval_degraded question observed",
            ("The reader is deepseek/deepseek-v4-flash at temperature 0, "
             "max_tokens 500. The historical 0.90 (2026-09-04) used a "
             "qwen3.8-max reader + gpt-4o judge — a different reader AND a "
             "different instrument; not comparable."),
        ]

        reasons: list[str] = []
        abort = False
        if substrate:
            # A per-question exception or a shipping-handler error envelope
            # lowers a leg without looking like a fault in the aggregate. A
            # contaminated run is reported as such and can never ADOPT — a
            # substrate fault must not be laundered into a quality result.
            where = sorted({f"{e['source']}:{e['question_id']}"
                            for e in substrate})
            reasons.append(f"CONTAMINATED — {len(substrate)} substrate "
                           f"fault(s) ({', '.join(where)})")
            abort = True
        if shape_rate < SHAPE_RATE_ADOPT:
            reasons.append(f"shape_rate {shape_rate:.3f} < "
                           f"{SHAPE_RATE_ADOPT:.2f}")
        abort = abort or shape_rate < SHAPE_RATE_ADOPT
        if receipt["live"]["provenance_pn"]["passed"] != FIXTURE_N:
            reasons.append("provenance "
                           f"{receipt['live']['provenance_pn']['passed']}/"
                           f"{FIXTURE_N} < {FIXTURE_N}/{FIXTURE_N}")
            abort = True
        if receipt["live"]["abstain_pn"]["passed"] != FIXTURE_N:
            reasons.append("abstain "
                           f"{receipt['live']['abstain_pn']['passed']}/"
                           f"{FIXTURE_N} < {FIXTURE_N}/{FIXTURE_N}")
            abort = True
        if receipt["live"]["grounding_pn"]["passed"] < GROUNDING_MIN:
            reasons.append("grounding "
                           f"{receipt['live']['grounding_pn']['passed']}/"
                           f"{FIXTURE_N} < {GROUNDING_MIN}/{FIXTURE_N}")
            abort = True
        mov_fired = receipt.get("movement", {}).get("fired", {}).get("all")
        if not mov_fired:
            # The frozen rule is unconditional: ADOPT requires the movement
            # control to have run AND fired. `--mode live` skips it, so it can
            # never ADOPT.
            reasons.append("movement control did not run or did not fire — "
                           "the frozen rule requires it for ADOPT")
            abort = True
        kg_ok = receipt.get("known_green", {}).get("ok")
        if not kg_ok:
            reasons.append("known-GREEN (committed recorded transports) did "
                           "not pass — the pipeline itself is not reproduced")
            abort = True
        decision = "DO-NOT-CLAIM" if abort else "ADOPT"
        receipt["verdict"] = {
            "decision": decision,
            "reasons": reasons or ["shape_rate >= 0.80, per-leg minimums met, "
                                   "movement control fired"],
            "claim_allowed": ("retrieval returns what was captured"
                              if decision == "ADOPT" else None),
        }
        print(f"\nshape_rate = {sum(1 for r in live if r.get('pass'))}"
              f"/{len(live)} = {shape_rate:.3f}")
        print(f"provenance {receipt['live']['provenance_pn']['passed']}/"
              f"{len(live)} · abstain "
              f"{receipt['live']['abstain_pn']['passed']}/{len(live)} · "
              f"grounding {receipt['live']['grounding_pn']['passed']}/"
              f"{len(live)} · ctx_recall "
              f"{receipt['live']['ctx_recall_pn']['passed']}/{len(live)}")
        sens = receipt["live"]["grounding_span_sensitivity"]
        print("grounding span-floor sensitivity: " + " · ".join(
            f">={f}w:{sens[f'span_ge_{f}_words']['passed']}"
            for f in (1, 2, 3, 4, 5, 6)))
        print(f"retrieval_degraded {len(degraded)}/{len(live)}")
        _ga = receipt['live']['gold_answer_in_evidence_pn']
        print("gold-answer-in-evidence "
              f"{_ga['passed']}/{_ga['n']} (answerable only, reported "
              f"aside — not a leg)")
        print(f"VERDICT: {decision}"
              + (f" — {'; '.join(reasons)}" if reasons else ""))
        _write_receipt(args, receipt)
        return EXIT_ADOPT if decision == "ADOPT" else EXIT_NOT_ADOPT


def _write_receipt(args, receipt: dict) -> None:
    path = args.receipt or os.path.join(
        _REPO_ROOT, "docs", "runbook",
        f"ask-shape-rate-{datetime.now(UTC):%Y-%m-%d}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    receipt["receipt_path"] = path
    with open(path, "w") as f:
        json.dump(receipt, f, indent=2, sort_keys=False, default=str)
    print(f"receipt: {path}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="B6/objective-4 D3 answer-shape instrument (shape_rate). "
                    "REAL pinned reader — there is deliberately NO --mock.")
    ap.add_argument("--pin-sha", required=True,
                    help="the explicit origin/main SHA this run is pinned to; "
                         "asserted against `git rev-parse HEAD`")
    ap.add_argument("--mode", default="full",
                    choices=("full", "live", "movement", "seed-timing"))
    ap.add_argument("--receipt", default=None,
                    help="receipt JSON path (default docs/runbook/"
                         "ask-shape-rate-<date>.json)")
    ap.add_argument("--phase-seed", type=int, default=1,
                    help="seed questions to time before the full run")
    ap.add_argument("--movement-limit", type=int, default=None,
                    help="bound the movement arms to the first N questions")
    ap.add_argument("--seed-timing-questions", type=int, default=1)
    args = ap.parse_args(argv)

    pin = assert_tree_pin(_REPO_ROOT, args.pin_sha)
    questions, fixture_shape = load_fixture_asserted()
    fixture_shape["tree_pin"] = {"asserted": True, "sha": pin}
    print(f"tree pin OK: {pin}")
    print(f"fixture OK: sha256={fixture_shape['sha256'][:16]}... "
          f"n={fixture_shape['n_questions']} sessions="
          f"{fixture_shape['n_sessions']} turns={fixture_shape['n_turns']} "
          f"abs={fixture_shape['n_abs']}")

    if args.mode == "seed-timing":
        assert_embedder()
        st = seed_timing(questions, n=args.seed_timing_questions)
        print(json.dumps(st, indent=2))
        return EXIT_ADOPT

    return run_full(args, questions, fixture_shape)


if __name__ == "__main__":
    sys.exit(main())
