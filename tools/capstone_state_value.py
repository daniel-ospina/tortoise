#!/usr/bin/env python3
"""The state-model VALUE assessment (#13038) — a slow, on-demand, scored run.

WHAT THIS IS, AND WHAT IT IS DELIBERATELY NOT
---------------------------------------------
This is not a unit suite, not a drift check and not a CI job. It is the instrument that
asks the question the model exists for: **does the product tell the truth about a
business?** — what is current, what ended and how, what was planned versus what
happened, and whether a goal is actually met.

It does that by running ONE fixed business narrative (`docs/evidence/13038-capstone/
narrative.json`, the scenario's KEY, frozen before the run) through the product's own
write doors, then asking SIX customer questions through the product's own read surface,
then scoring the answers against the narrative.

* A **drift test** guarantees the code agrees with the document. It can be green while
  the product still serves a retired claim as current. It is not this.
* A **unit test** guarantees a function returns what its author expected. It says
  nothing about whether a customer's question gets the right answer. It is not this.
* This is scored on **customer-visible behaviour**, and it is allowed to fail — the
  point of the first run is that the scoreboard shows today's gap rather than hiding it.

HOW IT IS SCORED
----------------
Answers are judged **right / wrong / blocked / unjudged** against the narrative, by a
FRESH READER (an agent given the narrative as its key and the product's answers, and
nothing else), never by substring matching and never against internal fields. Mechanical
*o signals* (does the retired id appear in what was served? did the read come back
`degraded`?) are recorded alongside each answer so a failure is attributable at a glance
— they are evidence, never the verdict.

`blocked` is a real outcome and is never folded into `wrong`: "we could not ask" is not
"we answered badly". The row says which.

HONESTY RULES (each one is load-bearing, none is decorative)
------------------------------------------------------------
1. **A target it cannot reach is not a score.** If the deployed API has no credentials,
   or the key cannot address a dedicated disposable graph, the run says so and REFUSES
   to ingest — it never writes fixture claims into a graph a human relies on, and it
   never prints a fabricated score.
2. **`blocked` beats invention.** A read that times out, a shape the target cannot
   express, a judge that returns nothing — all record `blocked`/`unjudged` with the
   reason, never a guess.
3. **The read's own verdict is carried.** Every search records the product's
   `read_status` (`available` / `empty` / `degraded` / `unconfigured`) and its
   truncation marker, because on a degraded target an unrelated row set can come back
   looking like an answer, and a scoreboard that hid that would be measuring the
   instrument's optimism.
4. **The scenario is fixed before the run.** `narrative.json` is not edited after a
   scored run (see its own `why`).

THE SIX SHAPES (from #13038, all of them present in the narrative)
------------------------------------------------------------------
a plan (not an event) · a superseded commitment with a named successor · a retirement
with nothing replacing it · a plan that reached a terminal stage with no occurrence ·
a goal with a declared target and two readings, the latest below target · a withdrawn
claim.

USAGE
-----
    # ONE COMMAND (recommended): a private throwaway FalkorDB, the ingest, the questions,
    # the scoreboard. `test_lane.py run` runs PYTEST and cannot take this argv, so the
    # wrapper uses the `uri` route with the SAFE assign-then-eval form (a failed `uri`
    # prints nothing and `eval ""` returns 0, which would silently reuse an inherited URI).
    bash docs/evidence/13038-capstone/rerun.sh \
        --judge file --verdicts docs/evidence/13038-capstone/verdicts-baseline.json

    # the same, spelled out, when the exports are needed for another command.
    # ⚠️ `--out` IS REQUIRED for an UNJUDGED run: the bundle is the default and publishing a
    # board nobody scored there would replace the committed baseline with a non-result (exit 1,
    # nothing written). Score it (`--judge fresh|file`) to publish into the bundle.
    uri="$(uv run python tools/test_lane.py uri)" || exit 1
    eval "$uri"
    # --out is mandatory here: the default destination is the committed evidence bundle,
    # and a run that is not fully judged (this one has no --judge, so every row would be
    # `unjudged`) is REFUSED there. Point it at a scratch directory.
    uv run python tools/capstone_state_value.py run --target local --out /tmp/capstone-run

    # judge the answers with a fresh reader (spawns one `pi -p`), then render.
    # `--verdicts-out` writes the reader's verdicts in the shape `--verdicts` reads, so the
    # round-trip below is produced by the instrument rather than assembled by hand.
    uv run python tools/capstone_state_value.py run --target local --judge fresh \
        --verdicts-out /tmp/verdicts.json

    # or hand the packet to a human/agent and feed the verdicts back
    uv run python tools/capstone_state_value.py judge --packet docs/evidence/13038-capstone/judge-packet.json --verdicts verdicts.json

    # re-render a committed run without touching the graph
    uv run python tools/capstone_state_value.py render --run docs/evidence/13038-capstone/run-local.json

WHAT IT COSTS
-------------
Local target: one throwaway FalkorDB container and 17 write ops on a fresh graph; measured
62.8 s of wall clock for the committed run (judge-packet.json → header.duration_s, which is
the ingest + ask portion); no LLM tokens, no provider keys, no money. `--judge fresh` adds one
agent invocation (measured 150.2 s / ~2.5 minutes on the committed run: run-local.json →
judge.elapsed_s). Deployed target: refused unless a dedicated disposable graph is named
(see rule 1 above) — the measurement below says why.

EXIT CODES
----------
0  the run completed and every question was JUDGED (whatever the verdicts were — this
   tool measures the product, it does not pass or fail on the product's behalf)
1  the run did NOT produce a usable score: some question is `blocked` or `unjudged`, OR
   the board scored NOTHING (no rows — an empty population is not a completed run). The
   scoreboard must be read, not trusted
2  usage / unreadable narrative / a document that is not a judge packet or a verdicts map
3  the target refused (no credentials, no disposable graph, an unreachable or unreadable
   store) — always a refusal with the reason and NO score, never a traceback
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.parse
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_NARRATIVE = ROOT / "docs" / "evidence" / "13038-capstone" / "narrative.json"
DEFAULT_OUT = ROOT / "docs" / "evidence" / "13038-capstone"

VERDICTS = ("right", "wrong", "blocked", "unjudged")
PACKET_SCHEMA = "capstone-state-value-judge-packet/v1"

#: Target capabilities. An op or a question recipe that needs one of these is `blocked`
#: (with its requirement named) rather than attempted and reported as a failure — the
#: target's limits are never scored as the product's defects.
CAPABILITIES = (
    "objects",
    "points",
    "events",
    "pipeline_stages",
    "lifecycle_word",
    "supersede",
    "retract",
    "goal_state",
    "target",
    "judgement",
    "search_points",
    "search_objects",
    # #13302: the ENDED-SCOPE read ("what ended, and how"). A target that cannot
    # enumerate the ended population cannot be asked Q5 or Q6 at all, so the two
    # questions BLOCK rather than scoring an unaskable question as a wrong answer.
    "ended_scope",
    "read_entity",
    "read_events",
    "read_status",
)

#: Read-status terms the product itself publishes (`tortoise/read_status.py`).
DEGRADED_TERMS = ("degraded", "unconfigured", "partial")


# ── small utilities ──────────────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dt%H%M%Sz")


def _cut(value, limit: int = 320, *, redact: bool = True) -> str:
    """One-line, REDACTED, truncated text for a recorded field.

    Redaction runs BEFORE truncation, and that order is load-bearing (#13303 cycle 7, P1):
    `_cut(secret, 160)` leaves a 141-character PREFIX, which neither the
    `scheme://user:pass@` regex nor a literal `replace` of the FULL secret can match —
    measured on a 210-character password, 141 characters of it were written into
    `run-deployed-refused.json`. Redacting here closes the whole class at ONE point rather
    than at the nine call sites that each had to remember it.

    `redact=False` is for PRODUCT-SERVED text and the reader's own prose, which travels into
    the judge packet: running the credential patterns over it rewrites the very answer being
    scored (#13303 cycle 8). Measured: `_cut("password rotation policy was superseded")`
    became `"password <redacted> policy was superseded"`, and the fresh reader would then
    judge a payload the instrument had already altered. Everything that can carry a
    credential (exception text, a URI, a traceback) keeps the default.
    """
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = " ".join(text.split())
    if redact:
        text = _redact(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


_WORD = re.compile(r"[a-z0-9]{3,}")
_STOP = frozenset(
    [
        "the",
        "and",
        "for",
        "are",
        "was",
        "were",
        "what",
        "which",
        "who",
        "how",
        "why",
        "did",
        "does",
        "this",
        "that",
        "our",
        "we",
        "us",
        "with",
        "from",
        "into",
        "about",
        "been",
        "being",
        "has",
        "have",
        "had",
        "its",
        "it's",
        "they",
        "them",
        "their",
        "there",
        "then",
        "than",
        "any",
        "all",
        "not",
        "but",
        "one",
        "two",
        "been",
    ]
)


def _overlap(query: str, rows: list[dict]) -> dict:
    """Mechanical relatedness: how many served rows share a non-stop word with the query.

    This is NOT a correctness score — it is the fact that makes a degraded read legible.
    A full-corpus fallback serves rows that share nothing with the question, and a
    scoreboard that reported those rows without this number would look like an answer.
    """
    q = {w for w in _WORD.findall((query or "").lower()) if w not in _STOP}
    zero = 0
    for row in rows:
        text = {w for w in _WORD.findall(str(row.get("what") or "").lower()) if w not in _STOP}
        if not (q & text):
            zero += 1
    return {"query_words": sorted(q)[:12], "rows": len(rows), "zero_overlap_rows": zero}


def _redact(text: str) -> str:
    """Strip anything that could be a credential out of a recorded line.

    The URI form is first because it is the one that reaches a COMMITTED artifact: the
    store URI is recorded in the header of every run and rendered into the scoreboard, and
    `docker://:hunter2@host:6379/db` / `bolt://user:s3cr3t@host` / `redis://:Wq8-xT2pLm@…`
    all passed the token patterns below untouched. Today's committed baseline is clean only
    because the private test lane's password is the empty string — which is a property of
    the lane, not of this function.
    """
    text = re.sub(r"(?i)\b([a-z][a-z0-9+.\-]*://)([^\s/@]*:[^\s/@]*)@", r"\1<redacted>@", text)
    text = re.sub(r"(tt_[A-Za-z0-9]{6,})", "tt_<redacted>", text)
    text = re.sub(
        r"(?i)(api[_-]?key|token|password)([\"'=:\s]+)([^\s\"',}]{4,})", r"\1\2<redacted>", text
    )
    return text


def _strip_userinfo(text: str, url: str) -> str:
    """`_redact`, plus the URL's OWN credentials in whatever fragment leaks them.

    A scheme-less fragment defeats the `scheme://user:pass@` regex: `urlopen` on
    `https://alice:s3cr3t-pw@api.example.com` raises `InvalidURL` whose message is
    `nonnumeric port: 's3cr3t-pw@api.example.com'` — the password with NO scheme and NO
    colon in front of it, so `_redact` passed it through (measured: the pin caught the
    password in `provenance()`, #13303 cycle 6). Scrub the URL's own username and password
    literally, then apply the general rules.
    """
    out = _redact(text)
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return out
    for secret in (parts.password, parts.username):
        if secret and len(secret) >= 3:
            out = out.replace(secret, "<redacted>")
    return out


def _git(*args: str) -> str:
    try:
        out = subprocess.run(("git", *args), cwd=ROOT, capture_output=True, text=True, timeout=20)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


# ── records ──────────────────────────────────────────────────────────────────────


@dataclass
class Call:
    """One product call, recorded the way the scoreboard reads it."""

    surface: str
    request: dict
    ok: bool
    detail: str = ""
    read_status: dict = field(default_factory=dict)
    rows: list = field(default_factory=list)
    #: The product's own per-leg trace (#3892). Kept because "degraded" alone does
    #: not say WHICH leg was impaired, and on a keyword-only store that is the
    #: difference between "the state model is wrong" and "the dense leg is absent".
    legs: list = field(default_factory=list)
    #: How many served rows share any word with the query — the mechanical answer to
    #: "are these rows even about the question?". A degraded read can serve the whole
    #: corpus ranked, and this is what makes that visible instead of plausible.
    overlap: dict = field(default_factory=dict)
    #: WHAT THIS CALL IS FOR, in the recipe's own words ("the_plan_node", …). A recipe
    #: looks its payload up by role, never by position: when a ref fails to resolve the
    #: optional call is simply absent, and positional indexing then hands the
    #: NEXT question's payload to this one — measured shape: with `q3_pricing`
    #: unresolved, `calls[2]` was the *successor* node, so the signal printed
    #: "replaced node status='live', names-its-successor=False", inverting the very
    #: evidence the Q5/Q6 findings rest on. Roles make a missing call missing.
    role: str = ""

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "surface": self.surface,
            "request": self.request,
            "ok": self.ok,
            "detail": self.detail,
            "read_status": self.read_status,
            "legs": self.legs,
            "overlap": self.overlap,
            "rows": self.rows,
        }


@dataclass
class Answer:
    """What the product served for one question, plus the mechanical signals."""

    question_id: str
    status: str = "answered"  # answered | blocked
    reason: str = ""
    calls: list[Call] = field(default_factory=list)
    payload: dict = field(default_factory=dict)
    signals: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def degraded(self) -> bool:
        for call in self.calls:
            term = str(call.read_status.get("status") or "")
            if term in DEGRADED_TERMS:
                return True
        return False

    def call(self, role: str) -> Call | None:
        """The call a recipe made for *role*, or None when it was never made.

        Every recipe reads its payload through this and marks the question `blocked`
        when the role is absent, so an unresolved ref can never be answered with a
        different call's rows.
        """
        for c in self.calls:
            if c.role == role:
                return c
        return None

    def rows(self, role: str) -> list:
        c = self.call(role)
        return list(c.rows) if c else []

    def first(self, role: str) -> dict:
        rows = self.rows(role)
        return rows[0] if rows else {}

    def to_dict(self) -> dict:
        return {
            "question": self.question_id,
            "status": self.status,
            "reason": self.reason,
            "degraded": self.degraded,
            "signals": self.signals,
            "notes": self.notes,
            "calls": [c.to_dict() for c in self.calls],
            "payload": self.payload,
        }


# ── targets ──────────────────────────────────────────────────────────────────────


class TargetRefused(RuntimeError):
    """The target cannot be used. Never scored as a product defect."""


def _served_row(r: dict) -> dict:
    """One served row, projected for the record — in ONE place so the rule can be pinned.

    `redact=False` is load-bearing (#13303 cycle 8/9): this text is the PRODUCT's own answer
    and it travels into the judge packet, where the credential patterns match ordinary
    business prose (`password rotation policy was superseded` → `password <redacted> …`).
    A reader would then score a payload the instrument had already altered. It is a function
    because the alternatives — a pin that inspects `search()`'s source, or one that builds a
    `Call` by hand — prove nothing.
    """
    return {
        "id": r.get("id"),
        # `or r.get("name")` (#13302): an ENDED Object/Subject row carries its label
        # under `name` (a Point's is `content`), and the served text of the two
        # questions this read answers is an Object's name. Without it the reader
        # would score a row whose text was silently EMPTY.
        "what": _cut(
            r.get("content") or r.get("name") or r.get("snippet") or "", 120, redact=False
        ),
        "kind": (r.get("point_kind") or r.get("objectKind") or r.get("subjectKind")
                 or r.get("pointKind")),
        "status": r.get("status"),
        "score": (r.get("scores") or {}).get("rrf"),
        "match": r.get("match_source"),
        "supersedes": [s.get("id") for s in (r.get("supersedes") or [])],
        **_ended_fields(r),
    }


def _link_ids(row: dict) -> list[str]:
    """The ids a served row's ``supersedes`` link names, in whichever shape it has.

    The projection (`_served_row`) reduces the link to ids, while a raw SDK row
    carries ``[{id, content_snippet, created_at}]`` (`search_engine`'s decorated
    state). Accepting both keeps this reading the ROW rather than a shape.
    """
    out: list[str] = []
    for link in row.get("supersedes") or []:
        if isinstance(link, str):
            out.append(link)
        elif isinstance(link, dict) and link.get("id"):
            out.append(link["id"])
    return out


def _successor_carrier(node: dict, served: list) -> tuple[bool, str]:
    """Does this record name its successor, and BY WHICH of the family's carriers?

    #13302 review F2. The successor is TWO facts and each family carries its own
    (#13302, `tortoise/ended.py:227`): a **Point**'s is the `CORRECTS` edge —
    `supersede_point` writes the edge and NO `supersededBy` — while an
    **Object**/**Subject**'s is the `supersededBy` NAME. A by-id `read_entity`
    serves the node's PROPERTIES only (`projection._resolve_entity` returns
    `properties(n)`), so a Point comes back with neither carrier and the old
    signal's `bool(node.get("supersededBy"))` printed `names-its-successor=False`
    for every superseded Point in the graph, forever — on the row a judge scores
    Q3 from, contradicting that same run's own judge reason.

    So the fact is looked for where the FAMILY puts it: the id carrier when the
    read serves one, the name carrier when the record holds one, and otherwise
    the successor's own row, which names the Point's `CORRECTS` edge in its
    `supersedes` link. The carrier is returned WITH the verdict, because "no"
    from a carrier that was never searched is the false signal this replaces.
    """
    oid = node.get("id")
    ids = [i for i in (node.get("successorId"), *(node.get("allSuccessorIds") or []))
           if i]
    if ids:
        return True, f"by id {ids[0]!r} — the CORRECTS edge"
    named = node.get("supersededBy") or node.get("superseded_by")
    if named:
        return True, f"by name {named!r} — the `supersededBy` carrier"
    for row in served:
        if oid and oid in _link_ids(row):
            return True, (
                f"by edge — the successor's own row {row.get('id')!r} names it "
                f"(a Point's successor is the CORRECTS edge)"
            )
    return False, (
        "neither carrier on the replaced record nor a successor row naming the "
        "edge (a Point's successor is the CORRECTS edge; a by-id read does not "
        "serve edges)"
    )


def _ended_fields(r: dict) -> dict:
    """The #13302 half of a served row: HOW the record ended, when it says so.

    Conditional on the row CARRYING the field, so a current-scope row is
    byte-identical to the pre-#13302 projection (this function returns ``{}`` for
    one) — the packet a reader sees for Q1–Q4 must not change shape because a new
    read exists beside it.

    ``linkedEvents`` is keyed on PRESENCE, not truthiness: an empty list is the
    product's own statement that nothing is linked to the record, which is half of
    the "planned and never happened" answer (`stage.slot == 'terminal'` is the
    other half). Dropping it as falsy would delete exactly that evidence.
    """
    out: dict = {}
    for key in ("successorId", "successorName", "endedAt"):
        if r.get(key) is not None:
            out[key] = r[key]
    if r.get("allSuccessorIds"):
        out["allSuccessorIds"] = r["allSuccessorIds"]
    if r.get("stage"):
        out["stage"] = r["stage"]
    if r.get("isClosed"):
        out["isClosed"] = True
    if "linkedEvents" in r:
        out["linkedEvents"] = r["linkedEvents"]
    return out


class LocalTarget:
    """A local instance of the product: the SDK against a throwaway FalkorDB.

    This is the reference target. It reaches every shape in the narrative because the
    SDK is the product's own write door for every knowledge kind — which is exactly why
    a run here measures the model rather than the transport.
    """

    kind = "local"

    def __init__(
        self, namespace: str | None = None, *, db_uri: str | None = None, search_timeout_ms: int = 0
    ):
        uri = db_uri or os.environ.get("TORTOISE_DB_URI")
        if not uri:
            raise TargetRefused(
                "no local instance: TORTOISE_DB_URI is unset. Get one from the private test "
                'lane with `uri="$(uv run python tools/test_lane.py uri)" || exit 1; '
                'eval "$uri"` (or run the whole assessment with '
                "`bash docs/evidence/13038-capstone/rerun.sh`). `test_lane.py run` is NOT "
                "this route — it hands its argv to pytest."
            )
        if not re.match(r"^(docker|bolt|redis)://", uri):
            raise TargetRefused(
                f"TORTOISE_DB_URI is not a store URI: {_cut(_strip_userinfo(uri, uri), 40)}"
            )
        os.environ["TORTOISE_DB_URI"] = uri
        # `_strip_userinfo`, not `_redact`: a bare username (`docker://alice@host`) is not
        # matched by the `user:pass@` regex, and this reaches the committed header + scoreboard
        # (#13303 cycle 8, P5).
        self.uri = _strip_userinfo(uri, uri)
        self.namespace = namespace or f"capstone13038_{_stamp()}"
        self.capabilities = set(CAPABILITIES)
        #: 0 = the PRODUCT's own read budget (500 ms, `search_engine`'s collective
        #: cap). Raised only when the box cannot answer inside it, and always
        #: reported in the scoreboard header — a latency budget is not a state-model
        #: property, and hiding the difference would let a timeout read as a verdict.
        self.search_timeout_ms = search_timeout_ms
        self._sdk = None

    # -- plumbing ------------------------------------------------------------

    @property
    def sdk(self):
        if self._sdk is None:
            from tortoise.sdk import TortoiseSDK

            self._sdk = TortoiseSDK(namespace=self.namespace)
        return self._sdk

    def provenance(self) -> dict:
        return {
            "target": self.kind,
            "store": self.uri,
            "graph": self.namespace,
            "product": {"schema": "tortoise-sdk", "commit": _git("rev-parse", "HEAD")},
            "ingest_surface": [
                "Objects (create_object / update_knowledge)",
                "Points (create_point / supersede_point / retract_point)",
                "Events (create_event) for goal readings",
                "the pipeline stage writer (pipeline.seed_pipeline / advance_stage)",
                "the goal judgement writer (goal_judgement.judge_and_record)",
            ],
            "ingest_not_exercised": [
                "session capture — the SDK recorder was retired (#4282); only the "
                "hosted POST /v1/sessions captures sessions, and it needs an "
                "extractor model. This run therefore writes the state layer through "
                "the entity doors, which is the surface the state model governs.",
            ],
        }

    #: Labels the SDK itself writes on open (a `:Meta` bootstrap marker, e.g.
    #: `point_fts_v2`). They are not scenario content: counting them would make every
    #: fresh graph look dirty and the contamination guard would refuse every run.
    BOOTSTRAP_LABELS = ("Meta",)

    def node_count(self) -> int:
        """Scenario-relevant nodes in the graph, excluding the SDK's own bootstrap."""
        try:
            rows = (
                self.sdk._get_proj()
                .g.query(
                    "MATCH (n) WHERE NOT any(l IN labels(n) WHERE l IN $skip) RETURN count(n)",
                    params={"skip": list(self.BOOTSTRAP_LABELS)},
                )
                .result_set
            )
            return int(rows[0][0]) if rows else 0
        except Exception as exc:
            raise TargetRefused(
                f"cannot read the store at {self.uri}: {type(exc).__name__}: {_cut(str(exc), 160)}"
            ) from exc

    def drop(self) -> str:
        """Remove the throwaway graph. Best-effort and reported either way."""
        try:
            graph = self.sdk._get_proj().g
            graph.query("MATCH (n) DETACH DELETE n")
            return f"graph {self.namespace}: nodes deleted"
        except Exception as exc:
            return f"graph {self.namespace}: NOT dropped ({type(exc).__name__})"

    # -- write doors ---------------------------------------------------------

    def create_object(self, name: str, kind: str, props: dict | None = None) -> dict:
        return self.sdk.create_object(name, objectKind=kind, **(props or {}))

    def set_lifecycle_word(self, ref_id: str, word: str) -> dict:
        return self.sdk.update_knowledge(ref_id, status=word)

    def create_point(self, content: str, kind: str) -> dict:
        return self.sdk.create_point(kind=kind, content=content)

    def supersede_point(self, old_id: str, new_id: str) -> dict:
        return self.sdk.supersede_point(old_id, new_id)

    def retract_point(self, point_id: str) -> dict:
        return self.sdk.retract_point(point_id)

    def create_reading(
        self, indicator_id: str, name: str, value: float, unit: str, as_of: str
    ) -> dict:
        return self.sdk.create_event(
            name, "measurement", aboutObject=indicator_id, value=value, unit=unit, startedAt=as_of
        )

    def declare_pipeline(self, key: str, stages: list[dict]) -> dict:
        from tortoise import pipeline

        graph = self.sdk._get_proj().g
        pipe = pipeline.seed_pipeline(graph, key, {"stages": stages})
        return {
            "pipeline": pipe.pipeline_key,
            "default": pipe.default_stage_id,
            "source": pipe.source,
            "stages": [s.stage_id for s in pipe.stages],
        }

    def advance_stage(self, object_id: str, key: str, stage: str) -> dict:
        from tortoise import pipeline

        move = pipeline.advance_stage(self.sdk, object_id, key, stage)
        return {
            "applied": bool(move.applied),
            "reason": getattr(move, "reason", ""),
            "stage": getattr(getattr(move, "stage", None), "stage_id", None),
            "slot": getattr(getattr(move, "stage", None), "slot", None),
            "outcome": getattr(getattr(move, "stage", None), "outcome", None),
        }

    def judge_goal(self, goal_id: str, indicator_id: str, target_id: str) -> dict:
        from tortoise import goal_judgement

        node = self.sdk.get_entity(target_id)
        target = goal_judgement.Target.from_node(node)
        return goal_judgement.judge_and_record(
            self.sdk, goal_id=goal_id, indicator_id=indicator_id, target=target
        )

    # -- read doors ----------------------------------------------------------

    def search(
        self,
        query: str | None,
        *,
        entity_type: str = "point",
        limit: int = 10,
        include_terminal: bool = False,
        ended_scope: bool = False,
        role: str = "",
    ) -> Call:
        """The product's search surface, with its OWN read verdict carried out.

        `read_status_out` / `_truncation_out` / `leg_trace` are the product's own sinks
        (#3892 / #8447): a degraded read is recorded as degraded rather than being
        re-labelled by this tool. They are requested rather than inferred because the
        SDK's search returns a bare list, so a caller that does not ask cannot tell a
        full-corpus fallback from a match set.

        `ended_scope` (#13302) is the read whose QUESTION is "what ended, and how" —
        a state enumeration rather than a relevance read, so it takes no query.
        """
        request = {
            "query": query,
            "entity_type": entity_type,
            "limit": limit,
            "include_terminal": include_terminal,
        }
        if ended_scope:
            request["ended_scope"] = True
        status: dict = {}
        trunc: dict = {}
        trace: list = []
        extra = {"_elevated_timeout_ms": self.search_timeout_ms} if self.search_timeout_ms else {}
        if extra:
            request["elevated_timeout_ms"] = self.search_timeout_ms
        try:
            rows = (
                self.sdk.tortoise_fts_query(
                    query=query,
                    entity_type=entity_type,
                    limit=limit,
                    include_terminal=include_terminal,
                    ended_scope=ended_scope,
                    read_status_out=status,
                    _truncation_out=trunc,
                    leg_trace=trace,
                    **extra,
                )
                or []
            )
        except Exception as exc:
            return Call(
                "search",
                request,
                False,
                f"{type(exc).__name__}: {_cut(str(exc), 200)}",
                status,
                [],
                role=role,
            )
        served = [_served_row(r) for r in rows]
        legs = [
            {
                "leg": t.get("leg"),
                "ran": t.get("ran"),
                "degraded": t.get("degraded"),
                "reason": t.get("reason"),
                "count": t.get("count"),
            }
            for t in trace
            if isinstance(t, dict)
        ]
        overlap = _overlap(query, served) if query else {}
        detail = (
            f"{len(served)} rows · status={status.get('status')!r} · "
            f"truncated={trunc.get('truncated')} · "
            + (
                f"zero-overlap rows={overlap['zero_overlap_rows']}/{overlap['rows']} · "
                if overlap
                else ""
            )
            + f"legs={[(t.get('leg'), t.get('reason'), t.get('degraded')) for t in legs][:6]}"
        )
        return Call("search", request, True, detail, status, served, legs, overlap, role=role)

    def read_entity(self, entity_id: str, *, role: str = "") -> Call:
        try:
            node = self.sdk.get_entity(entity_id)
        except Exception as exc:
            return Call(
                "read_entity",
                {"id": entity_id},
                False,
                f"{type(exc).__name__}: {_cut(str(exc), 200)}",
                role=role,
            )
        return Call("read_entity", {"id": entity_id}, True, "one node", {}, [node], role=role)

    def read_events(self, limit: int = 50, *, role: str = "") -> Call:
        try:
            rows = self.sdk.get_events(limit=limit) or []
        except Exception as exc:
            return Call(
                "read_events",
                {"limit": limit},
                False,
                f"{type(exc).__name__}: {_cut(str(exc), 200)}",
                role=role,
            )
        served = [
            {
                "id": r.get("id"),
                "name": r.get("name"),
                "kind": r.get("eventKind"),
                "value": r.get("value"),
                "unit": r.get("unit"),
                "asOf": r.get("startedAt"),
            }
            for r in rows
        ]
        return Call(
            "read_events", {"limit": limit}, True, f"{len(served)} events", {}, served, role=role
        )

    def judge_read(
        self, goal_id: str, indicator_id: str, target_id: str, *, role: str = ""
    ) -> Call:
        """The product's own achievement surface: state + the reading it used."""
        request = {"goal": goal_id, "indicator": indicator_id, "target": target_id}
        try:
            from tortoise import goal_judgement

            node = self.sdk.get_entity(target_id)
            target = goal_judgement.Target.from_node(node)
            judged = goal_judgement.judge(
                self.sdk, indicator_id=indicator_id, target=target, goal_id=goal_id
            )
        except Exception as exc:
            return Call(
                "judge", request, False, f"{type(exc).__name__}: {_cut(str(exc), 200)}", role=role
            )
        payload = {
            "state": judged.state,
            "reason": judged.reason,
            "comparator": target.comparator,
            "threshold": target.threshold,
            "readingValue": judged.reading.value if judged.reading else None,
            "readingAsOf": judged.reading.started_at if judged.reading else None,
            "readingId": judged.reading.reading_id if judged.reading else None,
            "comparison": judged.comparison,
        }
        return Call(
            "judge",
            request,
            True,
            f"state={payload['state']} value={payload['readingValue']} "
            f"{target.comparator} {target.threshold}",
            {},
            [payload],
            role=role,
        )


class DeployedTarget:
    """The hosted API — reachability-verified, and REFUSED for ingestion.

    This is deliberate, and it is the honest answer on a box whose only credential is a
    graph-scoped key bound to a graph a human relies on:

    * **No disposable graph.** Creating a graph is session-authed
      (`POST /v1/graphs` → `PROVISION_VIA_ORG_ENDPOINT` for a key); a graph-scoped key
      can only write into the graph it was minted for. Writing 20 fixture claims into a
      live agent-memory graph is not a measurement, it is contamination — and the
      contamination would be indistinguishable from the data the questions ask about.
    * **The REST surface cannot express the shapes.** `/v1/objects` and `/v1/points`
      exist; the pipeline stage writer (`advance_stage`) and the goal judgement writer
      (`judge_and_record`) do not. A run that "passed" there would have skipped Q4 and
      Q5 by construction.

    So it verifies what it can (health, version) and refuses with the reason recorded in
    the scoreboard header. When a dedicated disposable graph exists, the remaining work
    is an ingestion adapter for the two writers above — not a redesign of this tool.
    """

    kind = "deployed"

    def __init__(self, api_url: str | None = None, api_key: str | None = None):
        self.api_url = (api_url or os.environ.get("TORTOISE_API_URL") or "").rstrip("/")
        self.api_key = (
            api_key
            or os.environ.get("TORTOISE_API_KEY")
            or os.environ.get("TORTOISE_MCP_API_KEY")
            or ""
        )
        self.capabilities: set[str] = set()
        if not self.api_url:
            raise TargetRefused("no deployed target: TORTOISE_API_URL is unset.")
        if not self.api_key:
            raise TargetRefused(
                "no deployed credentials: TORTOISE_API_KEY / TORTOISE_MCP_API_KEY unset. "
                "Nothing can be measured against the deployed API without a key — use "
                "--target local instead (see the module docstring, rule 1)."
            )
        self.reach = self._probe()

    def _probe(self) -> dict:
        """Reachability + version, recorded as data — NEVER raised, NEVER echoing a secret.

        Any exception is recorded, not propagated: a probe is the one thing that must not
        take the run down, and `http.client.InvalidURL` (an `HTTPException`, NOT a
        `ValueError`) proved that a narrowed tuple is a hole — a deployed URL carrying
        userinfo with no explicit port made `_get_hostport` read the password as a port,
        and BOTH the escaped traceback and the recorded `error` string then carried the
        credential while the run exited 2 where the table promises a refusal with exit 3
        (#13303 cycle 6, P1).
        """
        import urllib.error
        import urllib.request

        out: dict = {"health": None, "version": None, "error": None}
        for path in ("/health", "/v1/version"):
            try:
                req = urllib.request.Request(
                    f"{self.api_url}{path}", headers={"User-Agent": "capstone-13038"}
                )
                with urllib.request.urlopen(req, timeout=20) as resp:
                    out[path.strip("/").replace("/", "_")] = json.loads(
                        resp.read().decode("utf-8", "replace")
                    )
            except Exception as exc:  # a probe records, it never raises
                out["error"] = _cut(
                    _strip_userinfo(f"{path}: {type(exc).__name__}: {exc}", self.api_url),
                    160,
                )
        return out

    def provenance(self) -> dict:
        return {
            "target": self.kind,
            # `_redact` here for the same reason `LocalTarget` redacts its store URI: this
            # dict is written into `run-deployed-refused.json` inside the COMMITTED
            # evidence directory, and a deployed API URL can carry userinfo
            # (`https://user:pass@host`) just as a store URI can (#13303 cycle 4, P2).
            # `_strip_userinfo`, not `_redact`: a bare USERNAME (`https://alice@host`) is
            # not matched by the `user:pass@` regex, and this dict is written into the
            # committed evidence dir (#13303 cycle 7, P4).
            "api": _strip_userinfo(self.api_url, self.api_url),
            "reachability": self.reach,
            "capabilities": [],
            "ingest_surface": [],
            "ingest_not_exercised": [
                "everything: this target verified reachability and refused to "
                "ingest — a graph-scoped key can only write into the graph a human "
                "relies on, and the REST surface has no pipeline-stage or "
                "goal-judgement writer, so Q4 and Q5 could not be asked at all.",
            ],
        }


def build_target(
    kind: str, *, namespace: str | None, db_uri: str | None, search_timeout_ms: int = 0
) -> object:
    if kind == "local":
        return LocalTarget(namespace=namespace, db_uri=db_uri, search_timeout_ms=search_timeout_ms)
    if kind == "deployed":
        return DeployedTarget()
    raise TargetRefused(f"unknown target {kind!r}")


# ── ingestion ────────────────────────────────────────────────────────────────────


class Ingestor:
    """Execute the narrative's ops through the target's write doors, in order."""

    def __init__(self, target: LocalTarget, ops: list[dict]):
        self.target = target
        self.ops = ops
        self.refs: dict[str, str] = {}
        self.log: list[dict] = []
        #: Refs whose OWN identity write succeeded but a LATER state-changing op on them
        #: did not (an `advance_stage` refused by `terminal-guard`, a `retract_point`
        #: rejected, a `supersede_point` whose successor is missing). `refs` cannot carry
        #: this: the identity op is what puts the id in `refs`, so a refused
        #: `advance_stage` leaves the ref present and the plan looking like it was never
        #: cancelled — the question is then asked, the product serves a `live` row, and a
        #: reader attributes the instrument's own refused write to a product issue (#13034).
        #: This is the exact case `--allow-existing-graph` documents (a second terminal
        #: advance is refused) and the exact inversion README:66 promises cannot happen.
        #: A READ the question's key depends on belongs here too: Q4's key is the LATEST
        #: reading (99.72, November), so a refused reading write leaves October's 99.95 as
        #: the newest and the product's own judge reports `met` — a `wrong` row manufactured
        #: by the instrument's failed write (#13303 cycle 6, P1).
        self.failed_refs: set[str] = set()

    def _need(self, requires: list[str]) -> str | None:
        missing = [r for r in requires if r not in self.target.capabilities]
        return ", ".join(missing) if missing else None

    def _record(
        self, entry_id: str, op: str, ok: bool, detail: str, product_id: str | None = None
    ) -> dict:
        row = {"op": op, "ref": entry_id, "ok": ok, "detail": _redact(detail)}
        if not ok and entry_id:
            self.failed_refs.add(entry_id)
        if product_id:
            row["product_id"] = product_id
        self.log.append(row)
        return row

    def run(self) -> list[dict]:
        for op in self.ops:
            name = op.get("op")
            ref = ingest_ref_key(op)
            missing = self._need(op.get("requires") or [])
            if missing:
                self._record(ref, name, False, f"BLOCKED — target lacks capability: {missing}")
                continue
            handler = getattr(self, f"_op_{name}", None)
            if handler is None:
                self._record(ref, name, False, f"unknown op {name!r}")
                continue
            try:
                handler(op)
            except Exception as exc:
                self._record(ref, name, False, f"{type(exc).__name__}: {_cut(str(exc), 240)}")
        return self.log

    # -- individual ops ------------------------------------------------------

    def _op_declare_pipeline(self, op: dict) -> None:
        out = self.target.declare_pipeline(op["pipeline"], op["stages"])
        self._record(op["pipeline"], "declare_pipeline", True, json.dumps(out))

    def _op_object(self, op: dict) -> None:
        node = self.target.create_object(op["name"], op["kind"], op.get("props"))
        self.refs[op["ref"]] = node["id"]
        self._record(op["ref"], "object", True, f"{op['kind']} → {node['id']}", node["id"])

    def _op_point(self, op: dict) -> None:
        node = self.target.create_point(op["content"], op["kind"])
        self.refs[op["ref"]] = node["id"]
        self._record(op["ref"], "point", True, f"{op['kind']} → {node['id']}", node["id"])

    def _op_supersede_point(self, op: dict) -> None:
        old, new = self.refs.get(op["old"]), self.refs.get(op["new"])
        if not old or not new:
            raise RuntimeError(f"unresolved refs: {op['old']} / {op['new']}")
        self.target.supersede_point(old, new)
        self._record(op["old"], "supersede_point", True, f"superseded by {new}", old)

    def _op_retract_point(self, op: dict) -> None:
        pid = self.refs.get(op["ref"])
        if not pid:
            raise RuntimeError(f"unresolved ref: {op['ref']}")
        node = self.target.retract_point(pid)
        self._record(op["ref"], "retract_point", True, f"status={node.get('status')}", pid)

    def _op_deprecate_object(self, op: dict) -> None:
        oid = self.refs.get(op["ref"])
        if not oid:
            raise RuntimeError(f"unresolved ref: {op['ref']}")
        node = self.target.set_lifecycle_word(oid, "deprecated")
        self._record(op["ref"], "deprecate_object", True, f"status={node.get('status')}", oid)

    def _op_advance_stage(self, op: dict) -> None:
        oid = self.refs.get(op["ref"])
        if not oid:
            raise RuntimeError(f"unresolved ref: {op['ref']}")
        out = self.target.advance_stage(oid, op["pipeline"], op["stage"])
        self._record(op["ref"], "advance_stage", bool(out.get("applied")), json.dumps(out), oid)

    def _op_reading(self, op: dict) -> None:
        oid = self.refs.get(op["indicator"])
        if not oid:
            raise RuntimeError(f"unresolved ref: {op['indicator']}")
        node = self.target.create_reading(oid, op["name"], op["value"], op["unit"], op["asOf"])
        self.refs[op["ref"]] = node["id"]
        self._record(
            op["ref"], "reading", True, f"{op['value']}{op['unit']} asOf {op['asOf']}", node["id"]
        )

    def _op_judge_goal(self, op: dict) -> None:
        goal = self.refs.get(op["goal"])
        indicator = self.refs.get(op["indicator"])
        target = self.refs.get(op["target"])
        if not (goal and indicator and target):
            raise RuntimeError("unresolved goal/indicator/target ref")
        out = self.target.judge_goal(goal, indicator, target)
        judged = out.get("judged") or {}
        self._record(
            op["goal"],
            "judge_goal",
            True,
            f"state={judged.get('state')} value={judged.get('value')} "
            f"reading={judged.get('readingId')}",
            goal,
        )


# ── the six recipes ──────────────────────────────────────────────────────────────
#
# Each recipe asks ONE customer question by making the product calls a read surface
# makes for it, and returns what the product served. The recipes are declared here (not
# inferred) so a wrong answer can be attributed to a call rather than to "the product".


def _served_ids(answer: Answer) -> set[str]:
    """Ids the product SERVED from a question-shaped SEARCH.

    Search calls only, deliberately: a by-id `read_entity` is the legitimate way to see
    a retired node (Q3 and Q6 both depend on it), so counting those rows here would fire
    `RETIRED-AS-CURRENT` on a caller who already held the id — a false signal in the one
    field a reader uses to attribute a failure to the read path.
    """
    out: set[str] = set()
    for call in answer.calls:
        if call.surface != "search":
            continue
        for row in call.rows:
            if isinstance(row, dict) and row.get("id"):
                out.add(row["id"])
    return out


def _require_call(answer: Answer, *roles: str) -> None:
    """`blocked` when a call the answer NEEDS did not succeed.

    "We could not ask" is not "we answered badly" (honesty rule 2). A call that is
    absent (its ref never resolved) or that failed is exactly that case — without this,
    a refused read is scored as a product defect by a fresh reader who has no way to
    tell the difference. A call that SUCCEEDED with zero rows is deliberately NOT
    blocked: "the product answered with nothing" is a real answer and the reader scores
    it.
    """
    missing = [r for r in roles if answer.call(r) is None or not answer.call(r).ok]
    if not missing:
        return
    answer.status = "blocked"
    detail = "; ".join(
        f"{r}: {'never made' if answer.call(r) is None else answer.call(r).detail}" for r in missing
    )
    answer.reason = answer.reason or f"the read did not complete — {detail}"


def _signal_refs(answer: Answer, refs: dict[str, str], spec: dict) -> None:
    """Record whether the narrative's key identities appeared in what was served."""
    served = _served_ids(answer)
    for ref in spec.get("must_not_appear") or []:
        pid = refs.get(ref)
        if pid and pid in served:
            answer.signals.append(f"RETIRED-AS-CURRENT: {ref} ({pid}) was served")
    for ref in spec.get("must_appear") or []:
        pid = refs.get(ref)
        if pid:
            answer.signals.append(f"{'found' if pid in served else 'NOT-SERVED'}: {ref} ({pid})")
    for call in answer.calls:
        if call.surface != "search" or not call.ok:
            continue
        # A call with no QUERY is not a failed text match (#13302): the ended-scope
        # read selects by STATE, so "every row shares no word with the question"
        # would be a true sentence about a question nobody asked — an evidence line
        # pointing a reader at a corpus ranking that never happened.
        if not call.request.get("query"):
            continue
        ov = call.overlap or {}
        if ov.get("rows") and ov.get("zero_overlap_rows") == ov.get("rows"):
            answer.signals.append(
                f"NO-RELATION-TO-QUESTION: all {ov['rows']} rows served for "
                f"{call.request.get('query')!r} share no word with it — a degraded "
                f"read ranked the corpus rather than answering"
            )
    if answer.degraded:
        impaired = sorted(
            {
                str(leg.get("leg"))
                for call in answer.calls
                for leg in (call.legs or [])
                if isinstance(leg, dict) and leg.get("degraded")
            }
        )
        answer.signals.append(
            "DEGRADED-READ: the product reported the read degraded"
            + (f" (legs: {', '.join(impaired)})" if impaired else "")
            + " — rows may be the whole corpus ranked, not a match set"
        )


def recipe_current_commitments(target: LocalTarget, refs: dict[str, str], spec: dict) -> Answer:
    ans = Answer("Q1")
    ans.calls.append(
        target.search(
            "what are we currently committed to — pricing, onboarding, launch and uptime",
            entity_type="point",
            limit=10,
            role="committed_points",
        )
    )
    ans.calls.append(
        target.search(
            "current commitments", entity_type="object", limit=10, role="committed_objects"
        )
    )
    ans.calls.append(
        target.search("onboarding flow", entity_type="object", limit=10, role="retired_flow")
    )
    ans.payload = {
        "served_points": ans.rows("committed_points"),
        "served_objects_for_current_commitments": ans.rows("committed_objects"),
        "served_objects_for_retired_flow": ans.rows("retired_flow"),
    }
    _require_call(ans, "committed_points", "committed_objects", "retired_flow")
    _signal_refs(ans, refs, spec)
    return ans


def recipe_plan_vs_occurrence(target: LocalTarget, refs: dict[str, str], spec: dict) -> Answer:
    ans = Answer("Q2")
    ans.calls.append(target.search("launch event", entity_type="object", limit=10, role="search"))
    plan_id = refs.get("launch_plan")
    if plan_id:
        ans.calls.append(target.read_entity(plan_id, role="plan_node"))
    ans.calls.append(target.read_events(limit=50, role="events"))
    node = ans.first("plan_node")
    ans.payload = {
        "served_objects": ans.rows("search"),
        "the_plan_node": {
            k: node.get(k)
            for k in (
                "id",
                "name",
                "objectKind",
                "status",
                "pipelineStageId",
                "pipelineSlot",
                "pipelineOutcome",
                "isClosed",
            )
        }
        if node
        else None,
        "events_in_the_graph": ans.rows("events"),
    }
    if node:
        ans.signals.append(
            f"stage={node.get('pipelineStageId')!r} slot={node.get('pipelineSlot')!r} "
            f"isClosed={node.get('isClosed')!r} status={node.get('status')!r}"
        )
    _require_call(ans, "search", "plan_node", "events")
    _signal_refs(ans, refs, spec)
    return ans


def recipe_why_changed(target: LocalTarget, refs: dict[str, str], spec: dict) -> Answer:
    ans = Answer("Q3")
    ans.calls.append(
        target.search("pricing decision Q3 Q4", entity_type="point", limit=10, role="default")
    )
    ans.calls.append(
        target.search(
            "pricing decision Q3 Q4",
            entity_type="point",
            limit=10,
            include_terminal=True,
            role="include_terminal",
        )
    )
    old = refs.get("q3_pricing")
    if old:
        ans.calls.append(target.read_entity(old, role="replaced_node"))
    new = refs.get("q4_pricing")
    if new:
        ans.calls.append(target.read_entity(new, role="successor_node"))
    node_old = ans.first("replaced_node")
    node_new = ans.first("successor_node")
    ans.payload = {
        "served_default_scope": ans.rows("default"),
        "served_include_terminal": ans.rows("include_terminal"),
        "the_replaced_node": {
            k: node_old.get(k)
            for k in ("id", "content", "status", "validTo", "expiredAt", "supersededBy")
        }
        if node_old
        else None,
        "the_successor_node": {k: node_new.get(k) for k in ("id", "content", "status")}
        if node_new
        else None,
    }
    if node_old:
        # #13302 review F2: the successor fact is read off the CARRIER THE FAMILY
        # USES (a Point's `CORRECTS` edge is only visible on the successor's own
        # row, never on a by-id property read), and the signal says which one
        # answered — the alternative is a `False` that means "not looked for".
        _named, _carrier = _successor_carrier(
            node_old, ans.rows("default") + ans.rows("include_terminal"))
        ans.signals.append(
            f"replaced node status={node_old.get('status')!r}, "
            f"names-its-successor={_named} ({_carrier})"
        )
    _require_call(ans, "default", "include_terminal", "replaced_node", "successor_node")
    _signal_refs(ans, refs, spec)
    return ans


def recipe_goal_met(target: LocalTarget, refs: dict[str, str], spec: dict) -> Answer:
    ans = Answer("Q4")
    goal, target_id = refs.get("uptime_goal"), refs.get("uptime_target")
    if goal:
        ans.calls.append(target.read_entity(goal, role="goal_node"))
    if goal and target_id:
        ans.calls.append(target.judge_read(goal, goal, target_id, role="judgement"))
    ans.calls.append(target.read_events(limit=50, role="readings"))
    node = ans.first("goal_node")
    judged = ans.first("judgement")
    ans.payload = {
        "the_goal_node": {k: node.get(k) for k in ("id", "name", "goalState", "status")}
        if node
        else None,
        "the_products_judgement": judged or None,
        "readings_in_the_graph": ans.rows("readings"),
    }
    if node:
        ans.signals.append(f"goalState={node.get('goalState')!r} status={node.get('status')!r}")
    if judged:
        ans.signals.append(
            f"judgement cites reading={judged.get('readingValue')} "
            f"vs {judged.get('comparator')} {judged.get('threshold')} → "
            f"{judged.get('state')}"
        )
    else:
        ans.status = "blocked"
        ans.reason = "the product's judgement surface returned no state"
    _require_call(ans, "goal_node", "judgement", "readings")
    _signal_refs(ans, refs, spec)
    return ans


def recipe_planned_never_happened(target: LocalTarget, refs: dict[str, str], spec: dict) -> Answer:
    ans = Answer("Q5")
    ans.calls.append(
        target.search(
            "what was planned and never happened", entity_type="object", limit=10, role="question"
        )
    )
    # #13302: the read whose question this IS. "What was planned and never happened"
    # is a STATE question, so a text match cannot answer it (measured pre-fix: 0 rows)
    # — the ended-scope enumeration of the Object family returns the plans that
    # ENDED, each with the stage it ended on and the events linked to it. A plan is
    # `stage.slot == 'terminal'` with no linked event; a live plan is not in this
    # population at all, which is why "is not returned as never happened" is a
    # property of the read rather than of the reader's judgement.
    ans.calls.append(
        target.search(None, entity_type="object", limit=50, ended_scope=True, role="ended")
    )
    ans.calls.append(
        target.search("thursday partner call", entity_type="object", limit=10, role="by_name")
    )
    call_id = refs.get("partner_call")
    if call_id:
        ans.calls.append(target.read_entity(call_id, role="cancelled_plan"))
    node = ans.first("cancelled_plan")
    served = [r for r in ans.rows("by_name") if r.get("id") == call_id]
    ans.payload = {
        "served_for_the_question": ans.rows("question"),
        "ended_objects": ans.rows("ended"),
        "served_for_the_cancelled_call": ans.rows("by_name"),
        "the_cancelled_plan": {
            k: node.get(k)
            for k in (
                "id",
                "name",
                "status",
                "pipelineStageId",
                "pipelineSlot",
                "pipelineOutcome",
                "isClosed",
            )
        }
        if node
        else None,
    }
    if node:
        ans.signals.append(
            f"by-id read_entity: stage={node.get('pipelineStageId')!r} "
            f"slot={node.get('pipelineSlot')!r} isClosed={node.get('isClosed')!r}"
        )
    if served:
        ans.signals.append(
            "SEARCH STILL SERVES THE CANCELLED PLAN: it came back from the plain "
            "default-scope search as a current row "
            f"(status={served[0].get('status')!r}) — the S1 gap (#13034's predicate "
            "reads neither the terminal slot nor isClosed)"
        )
    _require_call(ans, "question", "ended", "by_name", "cancelled_plan")
    _signal_refs(ans, refs, spec)
    return ans


def recipe_withdrawn(target: LocalTarget, refs: dict[str, str], spec: dict) -> Answer:
    ans = Answer("Q6")
    ans.calls.append(
        target.search(
            "has anything we relied on been withdrawn",
            entity_type="point",
            limit=10,
            role="question",
        )
    )
    # #13302: "has anything been withdrawn?" is the ended-scope question, and it is
    # asked WITHOUT knowing an id, a query string that matches the record's words, or
    # which surface accepts a terminal flag. The pre-fix path (below) needed all
    # three: the default-scope reads are a DEGRADED corpus ranking here (0 of 2 rows
    # share a word with the question, score 0.0) and the claim surfaces only when
    # terminal scope is forced by name.
    ans.calls.append(
        target.search(None, entity_type="point", limit=50, ended_scope=True, role="ended")
    )
    ans.calls.append(
        target.search("beach club confirmed booking", entity_type="point", limit=10, role="by_name")
    )
    ans.calls.append(
        target.search(
            "beach club confirmed booking",
            entity_type="point",
            limit=10,
            include_terminal=True,
            role="include_terminal",
        )
    )
    claim = refs.get("beach_club_claim")
    if claim:
        ans.calls.append(target.read_entity(claim, role="claim_node"))
    node = ans.first("claim_node")
    ans.payload = {
        "served_default_scope": ans.rows("question"),
        "ended_points": ans.rows("ended"),
        "served_for_the_claim_default_scope": ans.rows("by_name"),
        "served_for_the_claim_include_terminal": ans.rows("include_terminal"),
        "the_claim_node": {k: node.get(k) for k in ("id", "content", "status")} if node else None,
    }
    if node:
        ans.signals.append(f"claim status={node.get('status')!r} (a by-id read_entity)")
    if ans.rows("ended"):
        # ⛔ "withdrawn record(s)" would be a claim the product never made: the ended
        # population is EVERY ending (a `superseded` decision is in it too), and this
        # line is the one field a reader uses to attribute a failure. The row's own
        # `status`/`stage.outcome` is where "how it ended" is stated.
        ended_rows = ans.rows("ended")

        def _how(row: dict) -> str:
            """The row's own "how it ended", never a claim invented here."""
            word = row.get("status")
            if word:
                return str(word)
            stage = row.get("stage") or {}
            if stage:
                return f"{stage.get('slot')}/{stage.get('outcome')}"
            return "(no word served)"

        ans.signals.append(
            "the ended-scope read served "
            f"{len(ended_rows)} ended record(s) without the id being supplied — "
            "each row states how it ended: "
            + "; ".join(f"{r.get('id')}: {_how(r)}" for r in ended_rows)
        )
    if not ans.rows("by_name"):
        ans.signals.append(
            "the withdrawn claim is absent from the default-scope search — correct for "
            "currency, but a customer asking 'has anything been withdrawn' is then told "
            "nothing, which is the wrong direction of absence"
        )
    _require_call(ans, "question", "ended", "by_name", "include_terminal", "claim_node")
    _signal_refs(ans, refs, spec)
    return ans


RECIPES = {
    "current_commitments": recipe_current_commitments,
    "plan_vs_occurrence": recipe_plan_vs_occurrence,
    "why_changed": recipe_why_changed,
    "goal_met": recipe_goal_met,
    "planned_never_happened": recipe_planned_never_happened,
    "withdrawn": recipe_withdrawn,
}

#: Which capabilities a recipe needs before it can be asked at all.
RECIPE_NEEDS = {
    "current_commitments": ["search_points", "search_objects"],
    "plan_vs_occurrence": ["search_objects", "read_entity", "read_events"],
    "why_changed": ["search_points", "read_entity"],
    "goal_met": ["read_entity", "read_events", "judgement"],
    "planned_never_happened": ["search_objects", "read_entity", "ended_scope"],
    "withdrawn": ["search_points", "read_entity", "ended_scope"],
}

#: Identities the SCENARIO creates that no question is scored against, and why each is
#: allowed to go unwatched. An entry says the refusal cannot make a question WRONG, which is
#: not the same as "blocks nothing" — a cascade counts too — so each entry carries the reason, and the pins assert every key still
#: exists in the narrative (a stale allowlist would silently license a future gap).
SCENARIO_ONLY_REFS = {
    "core:plan": (
        "the pipeline DECLARATION, which no question reads. Its refusal cannot hide: every "
        "`advance_stage` on it then fails, and those ops are attributed to the plans "
        "(`launch_plan`, `partner_call`), so the questions that need those stages block. "
        "Allowed here because the cascade — not a direct declaration — is what carries it."
    ),
}


def ingest_ref_key(op: dict) -> str:
    """The key an ingest op's log row is attributed to (the one definition).

    Used by `Ingestor.run()` AND by the manifest pin, so "every ref is load-bearing for
    some question" cannot be checked against a narrower set than the one the log records
    (#13303 review cycle 6: `produced` read only `op["ref"]`, so `old`/`goal`-shaped ops —
    and `declare_pipeline` — were invisible to it).
    """
    return op.get("ref") or op.get("old") or op.get("goal") or op.get("pipeline") or ""


#: Which INGESTED identities a recipe needs before it can be asked at all.
#:
#: The narrative's write doors can refuse (a bad stage name, a superseded id, a terminal
#: guard). When one does, the ref it would have produced is simply absent — and a recipe
#: that ran anyway would ask the product about a graph the scenario never created and
#: hand the empty result to a fresh reader as a product defect. That is the inversion
#: `README.md` promises never happens: "if a door refuses … the dependent question becomes
#: `blocked`". This map is what makes the promise true, and the pins assert it covers
#: every ref the recipes name (`test_every_recipe_declares_the_refs_it_needs`).
RECIPE_REFS = {
    "current_commitments": [
        # Q1's answer key names FOUR current commitments (Q4 pricing, the 12 December launch
        # plan, the Q4 uptime target) and two identities that must NOT appear as current
        # (the replaced Q3 price, the retired onboarding flow). Every one of them is part of
        # the question, so a missing or refused write makes the question unanswerable rather
        # than merely harder — declaring `[]` here (as this map first did) asked Q1 against a
        # graph the scenario had not created and let a reader score it.
        #
        # `uptime_TARGET`, not `uptime_GOAL` (#13303 cycle 6, P2): Q1's key names the 99.9 %
        # TARGET as the commitment. Declaring the goal object made a refused `judge_goal`
        # write — a Q4 concern — block Q1 and drop its measured `wrong` for a write Q1 does
        # not depend on. `uptime_goal` stays on `goal_met`, which is where the judgement is.
        "q3_pricing",
        "q4_pricing",
        "old_onboarding",
        "launch_plan",
        "uptime_target",
    ],
    "plan_vs_occurrence": ["launch_plan"],
    "why_changed": ["q3_pricing", "q4_pricing"],
    "goal_met": [
        "uptime_goal",
        "uptime_target",
        # Q4's key turns on the LATEST reading being 99.72 (November, below target). A
        # refused `create_event` for the November reading leaves the product's own judgement
        # `met` from October's 99.95 — so without these two the question is asked, the reader
        # scores it `wrong`, and it names a PRODUCT defect for the instrument's refused write.
        # They were the only two ingest refs in the whole narrative that no question waited
        # for; a pin now asserts no ingest ref can be unwatched again.
        #
        # October's reading IS declared even though Q4's key does not turn on it: the class
        # invariant (every produced ref is watched by some recipe) is what keeps a future
        # narrative from adding an unwatched ref, and blocking Q4 on a refused October write
        # is the fail-closed direction — the scenario is not the scenario.
        "uptime_oct",
        "uptime_nov",
    ],
    "planned_never_happened": ["partner_call"],
    "withdrawn": ["beach_club_claim"],
}


def refs_unresolved(
    recipe: str, refs: dict[str, str], failed_refs: set[str] | None = None
) -> list[str]:
    """The identities this recipe needs that the narrative's ingestion did not produce.

    Two failures make a ref unusable, and both are required: the ref may never have been
    created (`refs`), or its identity was created and a later state-changing op on it was
    refused (`failed_refs` — a cancelled plan that is still `live` is not the scenario, and
    a question asked against it scores the instrument's own failed write as a product bug).
    """
    failed = failed_refs or set()
    return [r for r in RECIPE_REFS.get(recipe or "", []) if r not in refs or r in failed]


# ── the fresh-reader judge ───────────────────────────────────────────────────────


JUDGE_PROMPT = """\
You are a fresh reader scoring a product against a business narrative. You have no stake
in the product and no prior knowledge of it.

You are given:
  * THE NARRATIVE: a short business story. This is the KEY — the truth.
  * SIX QUESTIONS a customer would ask about that business.
  * WHAT THE PRODUCT ANSWERED for each question: the raw payloads its read surfaces
    returned, plus mechanical signals (ids that appeared, read status, stage/slot values).

For each question decide ONE verdict, against the narrative only:

  right    — a customer asking this question would be satisfied: the answer is correct
             about the narrative AND, where the question asks for a basis ("why",
             "on what basis", "what replaced it"), it gives that basis.
  wrong    — the answer is incorrect about the narrative; or it is only accidentally
             correct while the evidence the product itself served contradicts it (e.g.
             a retired/replaced/cancelled thing served as current).
  blocked  — the question could not be ASKED: the read errored or timed out with no
             rows, or the product serves no surface that can answer this question at
             all. Do NOT use blocked for "the answer is bad" — that is wrong, and do
             NOT use wrong for "we could not ask".

Rules:
  * Judge the ANSWER, not the tool. A DEGRADED read is not an excuse: if the product
    still served rows, the customer receives them. Rows of unrelated content returned
    to a question are a wrong answer; an empty result where the narrative says
    something exists is a wrong answer. Degradation is evidence, not a verdict.
  * Being right requires the key fact, not a keyword: "Q4 pricing replaced Q3" is right;
    "there is a pricing decision" is not.
  * Never invent evidence. If the payload does not establish it, say so in `because`.
  * Do not reward verbosity and do not penalise it.
  * Do NOT use any tools. Answer from the packet alone, and do not read or write files.

Reply with ONE JSON object and nothing else, in exactly this shape:

{"verdicts": [{"id": "Q1", "verdict": "right|wrong|blocked", "because": "<one sentence>", "cause": "<the failing mechanism if wrong/blocked, else null>"}, ... six entries, one per question ... ]}
"""


def _judge_packet(
    narrative: dict,
    answers: list[Answer],
    refs: dict[str, str],
    header: dict,
    ingest: list[dict] | None = None,
) -> dict:
    return {
        "schema": PACKET_SCHEMA,
        "issue": narrative.get("issue"),
        "instructions": JUDGE_PROMPT,
        "rubric": narrative.get("rubric"),
        "narrative": {"story": narrative.get("story"), "shapes": narrative.get("shapes")},
        "questions": [
            {
                "id": q["id"],
                "question": q["question"],
                "key": q["key"],
                "what_a_wrong_answer_means": q["wrong_means"],
            }
            for q in narrative["questions"]
        ],
        "product_answers": [a.to_dict() for a in answers],
        "scenario_refs": refs,
        "header": header,
        # The packet carries the INGESTION log too, so a re-render from the packet
        # (`judge --packet …`) reproduces the same scoreboard. Dropping it produced a
        # scoreboard whose ingestion table read as "nothing was written" — a silent
        # lie about the run, which is the failure this whole instrument exists against.
        "ingest": ingest or [],
    }


def _extract_json(text: str) -> dict | None:
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidates = [fence.group(1)] if fence else []
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i, ch in enumerate(text[start:], start=start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start : i + 1])
                    break
    for blob in candidates:
        try:
            parsed = json.loads(blob)
        except ValueError:
            continue
        if isinstance(parsed, dict) and "verdicts" in parsed:
            return parsed
    return None


def judge_fresh(packet: dict, *, cmd: str | None = None, timeout: int = 900) -> dict:
    """Spawn ONE fresh reader (`pi -p`) with the packet and return its verdicts.

    A judge that fails is `unjudged` — never a guess, never a default verdict.
    """
    # `--no-tools` is not cosmetic (#13303 review P2): DEFAULT_OUT is the committed
    # evidence dir, so a reader holding Read/Bash can reproduce the PREVIOUS run's
    # verdicts from `run-{target}.json` / `scoreboard-{target}.md`, which are still
    # on disk when it is spawned. Independence must be enforced, not requested.
    argv = shlex_split(cmd) if cmd else ["pi", "-p", "--no-session", "--no-tools"]
    if shutil.which(argv[0]) is None:
        return {"ok": False, "reason": f"judge command not found: {argv[0]}", "verdicts": {}}
    prompt = (
        JUDGE_PROMPT
        + "\n\n--- JUDGE PACKET (JSON) ---\n"
        + json.dumps(
            {k: packet[k] for k in ("narrative", "questions", "product_answers", "rubric")},
            indent=2,
            default=str,
        )
    )
    started = time.time()
    try:
        # An EMPTY sandbox, never ROOT: a reader that could still reach the repo would
        # find this run's and every earlier run's artifacts beside the packet.
        with tempfile.TemporaryDirectory(prefix="capstone-judge-") as sandbox:
            proc = subprocess.run(
                [*argv, prompt],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=sandbox,
            )
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": f"judge timed out after {timeout}s", "verdicts": {}}
    elapsed = round(time.time() - started, 1)
    parsed = _extract_json(proc.stdout or "") or _extract_json(proc.stderr or "")
    if parsed is None:
        return {
            "ok": False,
            "elapsed_s": elapsed,
            "reason": "the fresh reader returned no parseable verdict JSON "
            f"(exit {proc.returncode}): {_cut((proc.stdout or proc.stderr or '')[-400:], 300)}",
            "verdicts": {},
        }
    verdicts = {}
    for row in parsed.get("verdicts") or []:
        if not isinstance(row, dict):
            continue
        verdict = str(row.get("verdict") or "").strip().lower()
        if verdict in ("right", "wrong", "blocked"):
            verdicts[str(row.get("id") or "").strip()] = {
                "verdict": verdict,
                "because": _cut(row.get("because") or "", 400, redact=False),
                "cause": _cut(row.get("cause") or "", 240, redact=False) or None,
            }
    return {
        "ok": bool(verdicts),
        "elapsed_s": elapsed,
        "verdicts": verdicts,
        "judge_cmd": " ".join(argv),
        "reason": "" if verdicts else "the fresh reader produced no usable verdicts",
    }


def shlex_split(cmd: str) -> list[str]:
    import shlex

    return shlex.split(cmd)


# ── scoreboard ───────────────────────────────────────────────────────────────────


def _search_label(request: dict, *, tick: bool = True) -> str:
    """`search(<query>, <type>[, <flags>])` — ONE spelling for BOTH renderers.

    A DICT, not a `Call`, because the top table renders from the recorded request
    (`_calls_summary`) while the detail sections hold a live `Call` — and two
    builders for one call meant the same read rendered two ways in one document
    (the top table dropped `entity_type` and spelled the flag `+terminal`).

    `ended_scope` MUST be in it (#13302): without the marker an ended-scope
    enumeration renders exactly like a query-less default-scope search, i.e. the
    committed evidence would show a retired object served as `status=deprecated`
    from a plain read — which is the trust failure the scoreboard EXISTS to catch.
    A reader must never have to guess which call produced a row.
    """
    flags = ""
    if request.get("include_terminal"):
        flags += ", include_terminal"
    if request.get("ended_scope"):
        flags += ", ended_scope"
    label = (
        f"search({request.get('query')!r}, "
        f"{request.get('entity_type')}{flags})"
    )
    return f"`{label}`" if tick else label


def _ended_suffix(row: dict) -> str:
    """How a served row ended, in one bracket — empty for a current-scope row.

    The scoreboard's job is to let a human see WHY a row is the answer. A row that
    merely prints `status=None` for a plan says nothing; `[stage=terminal/withdrawn,
    no linked event]` is the answer to "planned and never happened" on its face.
    """
    bits: list[str] = []
    stage = row.get("stage") or {}
    if stage:
        bits.append(f"stage={stage.get('slot')}/{stage.get('outcome')}")
    if row.get("successorId"):
        bits.append(f"successor={row['successorId']}")
    elif row.get("successorName"):
        bits.append(f"successor={row['successorName']}")
    if "linkedEvents" in row and not row["linkedEvents"]:
        bits.append("no linked event")
    if row.get("endedAt"):
        bits.append(f"endedAt={row['endedAt']}")
    return f" [{', '.join(bits)}]" if bits else ""


def _answer_text(answer: Answer) -> str:
    """One human paragraph of what the product actually served."""
    bits: list[str] = []
    for call in answer.calls:
        if not call.ok:
            bits.append(f"`{call.surface}` FAILED — {call.detail}")
            continue
        if call.surface == "search":
            if not call.rows:
                bits.append(f"{_search_label(call.request)} → 0 rows")
                continue
            shown = "; ".join(
                f"*{_cut(r.get('what'), 70, redact=False)}* (kind={r.get('kind')}, "
                f"status={r.get('status')}, rrf={r.get('score')})"
                f"{_ended_suffix(r)}"
                for r in call.rows[:4]
            )
            more = "" if len(call.rows) <= 4 else f"; +{len(call.rows) - 4} more"
            ov = call.overlap or {}
            rel = ""
            if ov.get("rows"):
                rel = (
                    f" [related-to-question: "
                    f"{ov['rows'] - ov.get('zero_overlap_rows', 0)}/{ov['rows']}]"
                )
            bits.append(
                f"{_search_label(call.request)}"
                f" → {len(call.rows)} rows{rel}: {shown}{more}"
            )
        elif call.surface == "read_entity":
            node = call.rows[0] if call.rows else {}
            keep = {
                k: v
                for k, v in node.items()
                if k
                in (
                    "name",
                    "content",
                    "objectKind",
                    "status",
                    "goalState",
                    "pipelineStageId",
                    "pipelineSlot",
                    "pipelineOutcome",
                    "isClosed",
                    "validTo",
                )
            }
            bits.append(f"`read_entity` → {json.dumps(keep, default=str)}")
        elif call.surface == "judge":
            row = call.rows[0] if call.rows else {}
            bits.append(f"`judge` → {json.dumps(row, default=str)}")
        elif call.surface == "read_events":
            bits.append(f"`read_events` → {len(call.rows)} events")
    return " · ".join(bits) if bits else "(no product call was made)"


def _calls_summary(calls: list[dict]) -> str:
    """The one-line form for the scoreboard's top table (the detail sections keep the long form)."""
    bits: list[str] = []
    for c in calls or []:
        if not c.get("ok"):
            bits.append(f"`{c.get('surface')}` FAILED")
            continue
        surf = c.get("surface")
        req = c.get("request") or {}
        if surf == "search":
            ov = c.get("overlap") or {}
            rel = (
                f" {ov.get('rows', 0) - ov.get('zero_overlap_rows', 0)}/{ov.get('rows', 0)}"
                " on-topic"
                if ov.get("rows")
                else ""
            )
            bits.append(
                f"{_search_label(req, tick=False)}"
                f" → {len(c.get('rows') or [])} rows{rel}"
            )
        elif surf == "read_entity":
            node = (c.get("rows") or [{}])[0]
            keep = {
                k: node[k]
                for k in ("status", "goalState", "pipelineStageId", "pipelineSlot", "isClosed")
                if node.get(k) not in (None, "")
            }
            bits.append(f"entity → {json.dumps(keep, default=str)}")
        elif surf == "judge":
            row = (c.get("rows") or [{}])[0]
            bits.append(
                f"judge → {row.get('state')} "
                f"({row.get('readingValue')} vs {row.get('comparator')} "
                f"{row.get('threshold')})"
            )
        elif surf == "read_events":
            bits.append(f"events → {len(c.get('rows') or [])}")
    return " · ".join(bits) if bits else "(no product call)"


def _evidence_text(answer: Answer) -> str:
    bits: list[str] = []
    for call in answer.calls:
        if call.read_status:
            bits.append(f"{call.surface}: read_status={call.read_status.get('status')!r}")
    for sig in answer.signals:
        bits.append(sig)
    return "; ".join(bits) if bits else "—"


def retrieval_posture(answers: list[Answer]) -> dict:
    """What the target's read path could actually do, summarised from the leg traces.

    Recorded in the header because it decides how much of a `wrong` row is the state
    model and how much is a store with no dense leg. It is a TARGET property, never
    scored as a product defect — but it is never hidden either.
    """
    legs: dict[str, dict] = {}
    for ans in answers:
        for call in ans.calls:
            for leg in call.legs or []:
                if not isinstance(leg, dict):
                    continue
                name = str(leg.get("leg"))
                slot = legs.setdefault(name, {"leg": name, "runs": 0, "degraded": 0, "reasons": []})
                slot["runs"] += 1
                if leg.get("degraded"):
                    slot["degraded"] += 1
                reason = leg.get("reason")
                if reason and reason not in slot["reasons"]:
                    slot["reasons"].append(reason)
    return {
        "searches": sum(1 for a in answers for c in a.calls if c.surface == "search"),
        "legs": list(legs.values()),
    }


def exit_code(tally: dict) -> int:
    """The run's exit: 0 only when EVERY question was actually answered by the product
    and read by the judge. A `blocked` or `unjudged` row is not a completed run, so it
    is non-zero — the one rule that stops a partial run reporting as a full score.

    Extracted (from `cmd_run` and `cmd_judge`, which each carried a copy) so the rule
    has ONE definition and is pinnable without a graph, a model or a subprocess.
    """
    # An EMPTY population is not a completed run (#13303 review cycle 2, P1). With no
    # rows every count is 0, so the blocked/unjudged test below passes vacuously and a
    # scoreboard that scored NOTHING reports success — measured as `judge --packet
    # run-local.json` writing `0 right · 0 wrong · 0 blocked · 0 unjudged` over the
    # committed baseline and exiting 0.
    if sum(tally.get(v, 0) for v in VERDICTS) == 0:
        return 1
    return 0 if tally.get("blocked", 0) == 0 and tally.get("unjudged", 0) == 0 else 1


def may_publish(board: dict, out_dir: str | Path) -> bool:
    """May this board be written into the COMMITTED evidence bundle?

    Only a COMPLETE run may (#13303 cycle 8, P1). Keyed on the PRODUCED tally, never on a
    flag: `--judge none`, a `--judge fresh` run whose reader failed, and `--judge file` with
    an empty map ALL produce an all-`unjudged` board, and a flag-keyed guard let the
    documented reproduction command replace the baseline with a non-result — measured:
    `--judge fresh --judge-cmd true` changed all three committed files and exited 1 over a
    board reading `0 right · 0 wrong · 0 blocked · 6 unjudged`. Any other destination is the
    caller's business.
    """
    if Path(out_dir).resolve() != DEFAULT_OUT.resolve():
        return True
    return exit_code(board.get("tally") or {}) == 0


def build_scoreboard(
    *, narrative: dict, header: dict, answers: list[Answer], judged: dict, ingest: list[dict]
) -> dict:
    rows = []
    by_id = {a.question_id: a for a in answers}
    for q in narrative["questions"]:
        ans = by_id.get(q["id"])
        j = judged.get(q["id"]) or {}
        verdict = j.get("verdict") or "unjudged"
        # The judge's vocabulary is CLOSED. A verdicts file is a caller-supplied artifact,
        # so a typo, a fourth word, or a value for a question that was never asked must not
        # leave a row outside the tally — `tally` is summed over `VERDICTS`, so an
        # unrecognised value would vanish from it while the row still rendered (measured:
        # one doctored verdict produced "3 right · 2 wrong · 0 blocked · 0 unjudged" over
        # SIX rows, Q1 rendered `Excellent`, and the run exited 0).
        if verdict not in VERDICTS:
            verdict = "unjudged"
            j = {
                "verdict": "unjudged",
                "because": f"ignored a verdict outside the closed vocabulary: {j.get('verdict')!r}",
                "cause": "unknown-verdict",
            }
        # `blocked` wins over EVERY judge verdict, `right` included: a question the
        # instrument could not ask was not answered, so a reader that says `right`
        # is reading the packet's own framing rather than a product response. (HONESTY
        # RULE 2; #13303 review P1. Without the `right` arm the run exits 0 — "every
        # question was judged" — over a row that was never asked.)
        #
        # `ans is None` is the SAME case (`#13303` review cycle 2): a question with no
        # recorded Answer already renders `answer_status: "blocked"` and
        # `product_answer: "(not asked)"`, so letting the judge's `right` stand made one
        # row contradict itself and scored the product for a question never put to it.
        if ans is None or ans.status == "blocked":
            if ans is not None and verdict != "blocked":
                ans.notes.append(
                    f"the reader judged this {verdict!r}, but the question could not be "
                    f"asked ({ans.reason or 'blocked'}) — `blocked` is not an answer"
                )
            verdict = "blocked"
            j = {
                "verdict": "blocked",
                "because": (ans.reason if ans else "") or "the question was not asked",
                "cause": (ans.reason if ans else None),
            }
        rows.append(
            {
                "id": q["id"],
                "question": q["question"],
                "verdict": verdict,
                "because": j.get("because") or "",
                "cause": j.get("cause") or "",
                "product_answer": _answer_text(ans) if ans else "(not asked)",
                "evidence": _evidence_text(ans) if ans else "",
                "wrong_means": q["wrong_means"],
                "degraded": bool(ans.degraded) if ans else False,
                "answer_status": ans.status if ans else "blocked",
                "signals": ans.signals if ans else [],
                "payload": ans.payload if ans else {},
                "notes": list(ans.notes) if ans else [],
                "calls": [c.to_dict() for c in ans.calls] if ans else [],
            }
        )
    tally = {v: sum(1 for r in rows if r["verdict"] == v) for v in VERDICTS}
    return {
        "schema": "capstone-state-value-scoreboard/v1",
        "header": header,
        "ingest": ingest,
        "rows": rows,
        "tally": tally,
    }


def _posture_notes(posture: dict) -> list[str]:
    if not posture.get("searches"):
        return []
    out = []
    for leg in posture.get("legs") or []:
        if leg.get("degraded"):
            out.append(
                f"retrieval leg `{leg['leg']}` degraded in {leg['degraded']} of "
                f"{leg['runs']} reads (reasons: {', '.join(leg['reasons'][:4])})"
            )
    if out:
        out.append(
            "the state-model verdicts in this scoreboard are read against that "
            "posture: a row "
            "that says a thing was wrong because unrelated rows came back is the "
            "read path, not the lifecycle model"
        )
    return out


def render_markdown(board: dict) -> str:
    h, rows, tally = board["header"], board["rows"], board["tally"]
    # Tolerant on purpose: a re-render may be driven by a judge packet whose header
    # is thinner than a run's, and a KeyError here would lose a whole scoreboard to a
    # cosmetic field.
    lines = [
        "# Capstone #13038 — the state model measured by customer value",
        "",
        f"**Run:** {h.get('started', '?')} → {h.get('finished', '?')} "
        f"(target `{h.get('target', '?')}`, {h.get('duration_s', '?')}s)",
        f"**Product:** {h.get('product', '?')}",
        f"**Store:** `{h.get('store', '?')}` · graph `{h.get('graph', '?')}`",
        f"**Scenario:** `{h.get('narrative', '?')}` (frozen {h.get('narrative_frozen', '?')})",
        "",
        f"## Scoreboard: {tally['right']} right · {tally['wrong']} wrong · "
        f"{tally['blocked']} blocked · {tally['unjudged']} unjudged",
        "",
        "| # | question | verdict | what the product answered | why |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| **{r['id']}** | {r['question']} | **{r['verdict']}** | "
            f"{_calls_summary(r.get('calls')).replace('|', '/')} | "
            f"{(r['because'] or '—').replace('|', '/')} |"
        )
    lines += ["", "## Per-question detail", ""]
    for r in rows:
        lines += [
            f"### {r['id']} — {r['question']} → **{r['verdict']}**",
            "",
            f"- **what the product answered:** {r['product_answer']}",
            f"- **evidence it cited / signals:** {r['evidence'] or '—'}",
            f"- **verdict reason:** {r['because'] or '(no fresh-reader verdict)'}",
        ]
        if r["cause"]:
            lines.append(f"- **cause:** {r['cause']}")
        lines.append(f"- **what a wrong answer means:** {r['wrong_means']}")
        lines.append("")
    lines += [
        "## Ingestion (the narrative through the product's write doors)",
        "",
        "| op | ref | ok | detail |",
        "|---|---|---|---|",
    ]
    if not board["ingest"]:
        lines.append("| — | — | — | the packet carried no ingestion log |")
    for row in board["ingest"]:
        lines.append(
            f"| `{row['op']}` | {row.get('ref', '')} | "
            f"{'yes' if row['ok'] else '**NO**'} | {_cut(row['detail'], 200)} |"
        )
    lines += ["", "## Target and honesty notes", ""]
    for note in h.get("notes") or []:
        lines.append(f"- {note}")
    if h.get("retrieval_posture"):
        lines += [
            "",
            "### Retrieval posture of the target",
            "",
            "| leg | reads | degraded | reasons |",
            "|---|---|---|---|",
        ]
        for leg in h["retrieval_posture"].get("legs") or []:
            lines.append(
                f"| `{leg['leg']}` | {leg['runs']} | {leg['degraded']} | "
                f"{', '.join(leg['reasons'][:4]) or '—'} |"
            )
    if h.get("judge"):
        j = h["judge"]
        lines += [
            "",
            "### Judge",
            "",
            f"- mode: `{j.get('mode')}`"
            + (f" · cmd: `{j.get('judge_cmd')}`" if j.get("judge_cmd") else "")
            + (f" · {j.get('elapsed_s')}s" if j.get("elapsed_s") else "")
            + (f" · **{j.get('reason')}**" if j.get("reason") else ""),
        ]
    # One trailing newline, never two: the docs lint refuses multiple blank lines.
    while lines and lines[-1] == "":
        lines.pop()
    lines.append("")
    return "\n".join(lines)


# ── the run ──────────────────────────────────────────────────────────────────────


def _load_verdicts_map(path: str | Path) -> dict:
    """Load a `--verdicts` document, or raise `ValueError` if it is not a verdicts map.

    ONE definition, used by BOTH entry points (`run --judge file` and `judge`), because
    both write into `--out` whose default is the COMMITTED evidence directory
    (`DEFAULT_OUT`). A run file passed as `--verdicts` (a plausible typo —
    `verdicts-baseline.json` is documented as "copied from `run-local.json`'s rows")
    used to score nothing and replace the board with `0 right · 0 wrong · 0 blocked ·
    6 unjudged`. Cycle 3 closed this on `judge` only; cycle 4 found the other door open.

    The vocabulary itself is NOT enforced here — a recognised map carrying a word
    outside `VERDICTS` is `build_scoreboard`'s job (recorded `unjudged`, never refused).
    """
    # `--verdicts` is optional in argparse, so `run --judge file` with it omitted reaches
    # here with None; `Path(None)` is a TypeError, which the callers do not catch, so the
    # promise above ("or raise ValueError") was broken by a traceback (#13303 cycle 5).
    if not path:
        raise ValueError("--verdicts was not given, but --judge file needs one")
    doc = json.loads(Path(path).read_text())
    if isinstance(doc, dict):
        doc.pop("_provenance", None)
    if not isinstance(doc, dict) or not all(isinstance(v, dict) for v in doc.values()):
        raise ValueError(
            "not a verdicts map (expected {question_id: {'verdict': one of "
            f"{', '.join(VERDICTS)}}})"
        )
    return doc


def cmd_run(args: argparse.Namespace) -> int:
    started, t0 = _now(), time.time()
    narrative_path = Path(args.narrative).resolve()
    try:
        narrative = json.loads(narrative_path.read_text())
    except (OSError, ValueError) as exc:
        print(f"capstone: cannot read the narrative {narrative_path}: {exc}", file=sys.stderr)
        return 2
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []

    # Validate `--judge file` BEFORE the target is built or anything is written: the run
    # measures a whole scenario, so a mistyped verdicts file must not cost 213 s and a
    # board overwrite to discover (#13303 cycle 4, P1).
    judged_from_file: dict | None = None
    if args.judge == "file":
        try:
            judged_from_file = _load_verdicts_map(args.verdicts)
        except (OSError, ValueError) as exc:
            print(
                f"capstone: refusing --verdicts {args.verdicts} — {exc}; nothing was "
                "measured and nothing was written",
                file=sys.stderr,
            )
            return 2

    try:
        target = build_target(
            args.target,
            namespace=args.graph,
            db_uri=args.db_uri,
            search_timeout_ms=args.search_timeout_ms,
        )
    except TargetRefused as exc:
        print(f"capstone: target refused — {exc}", file=sys.stderr)
        header = {
            "started": started,
            "finished": _now(),
            "duration_s": round(time.time() - t0, 1),
            "target": args.target,
            "product": "unknown",
            "store": "—",
            "graph": "—",
            "narrative": str(narrative_path.relative_to(ROOT))
            if str(narrative_path).startswith(str(ROOT))
            else str(narrative_path),
            "narrative_frozen": narrative.get("frozen"),
            "notes": [f"REFUSED, no score written: {exc}"],
        }
        board = {
            "schema": "capstone-state-value-scoreboard/v1",
            "header": header,
            "ingest": [],
            "rows": [],
            "tally": {v: 0 for v in VERDICTS},
            "refused": str(exc),
        }
        if args.json:
            print(json.dumps(board, indent=2, default=str))
        return 3

    prov = target.provenance()
    if isinstance(target, DeployedTarget):
        reason = (
            "deployed target verified and REFUSED for ingestion: a graph-scoped "
            "key cannot address a dedicated disposable graph (graph creation is "
            "session-authed), so writing the fixture into it would contaminate a "
            "live memory graph; and the REST surface has no pipeline-stage or "
            "goal-judgement writer, so Q4/Q5 are unaskable there. Use --target "
            "local."
        )
        notes.append(reason)
        header = {
            "started": started,
            "finished": _now(),
            "duration_s": round(time.time() - t0, 1),
            "target": "deployed",
            "product": f"hosted api {prov.get('api')} "
            f"commit={(prov.get('reachability') or {}).get('v1_version', {}).get('commit_sha')}",
            # `prov["api"]` is already userinfo-scrubbed by `DeployedTarget.provenance`.
            "store": prov.get("api") or "",
            "graph": "—",
            "narrative": str(narrative_path),
            "narrative_frozen": narrative.get("frozen"),
            "notes": notes,
            "provenance": prov,
        }
        print(f"capstone: {reason}", file=sys.stderr)
        board = {
            "schema": "capstone-state-value-scoreboard/v1",
            "header": header,
            "ingest": [],
            "rows": [],
            "tally": {v: 0 for v in VERDICTS},
            "refused": reason,
            "provenance": prov,
        }
        (out_dir / "run-deployed-refused.json").write_text(json.dumps(board, indent=2, default=str))
        return 3

    # 0. the graph must be EMPTY. Measured the hard way: re-running the scenario into a
    # graph that already holds the narrative merges Objects by canonical name, ACCUMULATES
    # events (a second reading pair), and refuses the second `advance_stage` with
    # `terminal-guard` — so the run measures a graph the scenario did not create, and the
    # scoreboard looks identical while the evidence behind it is not. Refuse, name it, and
    # let the operator pass `--allow-existing-graph` only if that is what they meant.
    # The store may be unreachable, and `node_count` deliberately raises rather than
    # returning a plausible number. An unreachable target is a REFUSAL (exit 3, no score),
    # not a crash — a caller keying on the documented exit table must not read it as one.
    try:
        existing = target.node_count()
    except TargetRefused as exc:
        print(f"capstone: target refused — {exc}", file=sys.stderr)
        return 3
    if existing and not args.allow_existing_graph:
        reason = (
            f"graph {target.namespace!r} already holds {existing} nodes — the "
            "scenario would merge into them (Objects by canonical name, events "
            "accumulate, a second terminal advance is refused) and the scoreboard "
            "would describe a graph this run did not create. Use a fresh --graph "
            "name (the default is fresh), or pass --allow-existing-graph."
        )
        print(f"capstone: target refused — {reason}", file=sys.stderr)
        return 3
    if existing:
        notes.append(
            f"run into a NON-EMPTY graph ({existing} pre-existing nodes): the "
            "ingestion merged into existing data, so the answers below describe "
            "a graph this run did not create."
        )

    # 1. ingest the narrative through the product's own write doors
    print(f"capstone: ingesting the narrative into {target.namespace} …", file=sys.stderr)
    ingestor = Ingestor(target, narrative["ingest"]["ops"])
    ingest_log = ingestor.run()
    for row in ingest_log:
        if not row["ok"]:
            notes.append(f"ingest `{row['op']}` {row.get('ref')}: {row['detail']}")

    # 2. ask the six questions through the read surface
    answers: list[Answer] = []
    for q in narrative["questions"]:
        recipe = RECIPES.get(q.get("recipe") or "")
        needs = RECIPE_NEEDS.get(q.get("recipe") or "", [])
        missing = [n for n in needs if n not in target.capabilities]
        unresolved = refs_unresolved(q.get("recipe") or "", ingestor.refs, ingestor.failed_refs)
        if recipe is None:
            answers.append(
                Answer(q["id"], status="blocked", reason=f"unknown recipe {q.get('recipe')!r}")
            )
            continue
        if missing:
            answers.append(
                Answer(
                    q["id"],
                    status="blocked",
                    reason="target lacks capability: " + ", ".join(missing),
                )
            )
            continue
        if unresolved:
            # The narrative's own write doors refused, so the identities this question
            # asks about do not exist. Asking anyway scores an empty graph as a product
            # defect, which is the one way this instrument could lie in the product's
            # disfavour — so it is `blocked`, never `wrong`.
            answers.append(
                Answer(
                    q["id"],
                    status="blocked",
                    reason=(
                        "the narrative's ingestion did not produce: "
                        + ", ".join(unresolved)
                        + " — the question was never asked (see the ingest log)"
                    ),
                )
            )
            continue
        print(f"capstone: {q['id']} — {q['question']}", file=sys.stderr)
        try:
            answers.append(recipe(target, ingestor.refs, q))
        except Exception as exc:
            answers.append(
                Answer(
                    q["id"], status="blocked", reason=f"{type(exc).__name__}: {_cut(str(exc), 240)}"
                )
            )

    # 3. score. The header (including the retrieval posture and the honesty notes) is
    # finished BEFORE the packet is built: a packet that omits them produces a
    # re-rendered scoreboard that has quietly lost its own caveats.
    posture = retrieval_posture(answers)
    notes.extend(_posture_notes(posture))
    notes.extend(n for n in target.provenance().get("ingest_not_exercised") or [] if n not in notes)
    header = {
        "started": started,
        "finished": _now(),
        "duration_s": round(time.time() - t0, 1),
        "target": target.kind,
        "product": f"tortoise-sdk @ {(prov.get('product') or {}).get('commit', '?')}",
        "store": target.uri,
        "graph": target.namespace,
        "narrative": str(narrative_path.relative_to(ROOT))
        if str(narrative_path).startswith(str(ROOT))
        else str(narrative_path),
        "narrative_frozen": narrative.get("frozen"),
        "provenance": prov,
        "notes": notes,
        "retrieval_posture": posture,
    }
    packet = _judge_packet(narrative, answers, ingestor.refs, header, ingest_log)
    packet_path = out_dir / "judge-packet.json"
    # NOT written yet (#13303 cycle 9, P1): the publish guard below runs first, and a
    # REFUSED run used to replace the committed `judge-packet.json` while its own message
    # said "Nothing was written" — leaving a packet from a run that never produced a board.

    judged: dict = {}
    judge_meta: dict = {"mode": args.judge}
    if args.judge == "fresh":
        print("capstone: scoring with a fresh reader (this takes a minute) …", file=sys.stderr)
        out = judge_fresh(packet, cmd=args.judge_cmd)
        judged = out.get("verdicts") or {}
        judge_meta.update({k: v for k, v in out.items() if k != "verdicts"})
        if not out.get("ok"):
            notes.append(f"no fresh-reader verdicts: {out.get('reason')}")
    elif args.judge == "file":
        verdicts_path = Path(args.verdicts).resolve()
        judged = judged_from_file or {}
        judge_meta["file"] = str(verdicts_path)
    else:
        notes.append(
            "judging was disabled (--judge none): every row is `unjudged`. "
            "Feed judge-packet.json to a fresh reader and re-render with "
            "`judge --verdicts`."
        )

    header["notes"] = notes
    header["judge"] = judge_meta

    board = build_scoreboard(
        narrative=narrative, header=header, answers=answers, judged=judged, ingest=ingest_log
    )
    header["finished"] = _now()
    header["duration_s"] = round(time.time() - t0, 1)
    board["header"] = header
    board["judge"] = judge_meta

    # THE BUNDLE IS WRITTEN ONLY BY A COMPLETE RUN (#13303 cycle 8, P1). Keyed on the
    # PRODUCED tally, not on `--judge`: a `--judge fresh` run whose reader fails, and a
    # `--judge file` run handed an empty map, BOTH produce an all-`unjudged` board — a
    # flag-keyed guard let the documented reproduction command replace the committed
    # baseline with a non-result while the README said that could not happen. Measured:
    # `--judge fresh` with `--judge-cmd true` changed all three committed files.
    # `blocked` is refused ALONGSIDE `unjudged`, deliberately (#13303 cycle 9): `blocked`
    # means the run could not ask a question, so the board is not a baseline a later run can
    # be compared against. An earlier comment here said the opposite ("a board that carries
    # real `blocked` rows is still publishable") and contradicted the code three lines below
    # it — `may_publish` delegates to `exit_code`, which returns 1 when `blocked > 0`. The
    # code, this comment and the README now state one rule.
    publishing = out_dir.resolve() == DEFAULT_OUT.resolve()

    def refuse(message: str, code: int = 1) -> int:
        """Every post-ingestion refusal goes through here, so `--drop-graph` cannot be skipped.

        The publish gate used to drop the graph and its three siblings did not: a run refused
        for a bad `--verdicts-out` left the throwaway graph it had just ingested behind, while
        the README said a refused run cleans up (#13303 cycle 11). `drop()` is best-effort and
        never raises, so routing all four here cannot change a documented exit code.
        """
        if args.drop_graph:
            print(f"capstone: cleanup — {target.drop()}", file=sys.stderr)
        print(message, file=sys.stderr)
        return code

    # WHERE the verdicts go is decided HERE, before any bundle write (#13303 cycle 10):
    # the cycle-9 rule keyed on the DIRECTORY, so `--verdicts-out <bundle>/run-local.json`
    # overwrote the machine-readable board with a verdicts map and still exited 0; a
    # publishing run that omitted the flag left `verdicts-baseline.json` describing a
    # DIFFERENT run; and a directory target raised `IsADirectoryError` only AFTER three
    # committed files had been replaced. One decision, keyed on the FILE, taken first.
    verdicts_target: Path | None = None
    bundle_baseline = DEFAULT_OUT.resolve() / "verdicts-baseline.json"
    if publishing and judged:
        if args.verdicts_out:
            verdicts_target = Path(args.verdicts_out).resolve()
            if verdicts_target != bundle_baseline:
                return refuse(
                    f"capstone: refusing --verdicts-out {verdicts_target} while publishing the "
                    f"board into {DEFAULT_OUT} — a publishing run writes its verdicts to "
                    f"{bundle_baseline} so that all three carriers describe ONE run; drop the "
                    "flag (the baseline is the default here) or send the board elsewhere with "
                    "--out"
                )
        else:
            # The bundle comes from ONE run, so a complete run that publishes its board
            # here also publishes the verdicts that produced it — otherwise the three
            # carriers would disagree with exit 0 and nothing would say so.
            verdicts_target = DEFAULT_OUT / "verdicts-baseline.json"
    elif args.verdicts_out and judged:
        verdicts_target = Path(args.verdicts_out).resolve()

    if verdicts_target is not None:
        if verdicts_target.is_dir():
            return refuse(
                f"capstone: refusing --verdicts-out {verdicts_target} — that is a directory, "
                "not a verdicts file"
            )
        # CONTAINMENT, not one level of it (#13303 cycle 11, #2): `parent == DEFAULT_OUT`
        # let `<bundle>/nested/v.json` through, and `_write_verdicts` mkdir'd it.
        if verdicts_target.is_relative_to(DEFAULT_OUT.resolve()):
            # Only the baseline may live in the bundle, and only from the run whose board
            # is published beside it. Any other name would overwrite `run-local.json`,
            # `scoreboard-local.md` or `judge-packet.json`.
            if verdicts_target.name != "verdicts-baseline.json":
                return refuse(
                    f"capstone: refusing to write {verdicts_target.name} into the evidence "
                    f"bundle {DEFAULT_OUT} — the bundle holds exactly four files and this is "
                    "not one of them; write elsewhere"
                )
            if not publishing:
                return refuse(
                    f"capstone: refusing to write verdicts into the bundle {DEFAULT_OUT} — "
                    "these verdicts are not the ones published beside it"
                )
    if not may_publish(board, out_dir):
        if not args.json:
            print(render_markdown(board))
        return refuse(
            f"\ncapstone: REFUSING to publish this board into the evidence bundle "
            f"{DEFAULT_OUT} — the tally is {board['tally']}, so it is not a complete "
            "baseline. NOTHING was written into the bundle; point --out at a scratch "
            "directory to inspect it."
        )

    packet_path.write_text(json.dumps(packet, indent=2, default=str))
    run_json = out_dir / f"run-{target.kind}.json"
    run_json.write_text(json.dumps(board, indent=2, default=str))
    md = render_markdown(board)
    (out_dir / f"scoreboard-{target.kind}.md").write_text(md)

    # AFTER the scoreboard, deliberately: this write is secondary, and a bad path here
    # used to abort the run once the 150 s reader had already been paid for, losing the
    # verdicts AND the scoreboard. The primary artifact lands first; a verdicts-out failure
    # is then loud but costs nothing that cannot be re-derived from the packet.
    if verdicts_target:
        _write_verdicts(
            verdicts_target,
            judged,
            packet_path=packet_path,
            mode=args.judge,
            meta=judge_meta,
        )

    if args.drop_graph:
        print(f"capstone: cleanup — {target.drop()}", file=sys.stderr)

    if args.json:
        print(json.dumps(board, indent=2, default=str))
    else:
        print(md)
    print(
        f"\ncapstone: wrote {run_json} and {out_dir / f'scoreboard-{target.kind}.md'}",
        file=sys.stderr,
    )

    tally = board["tally"]
    return exit_code(tally)


def _write_verdicts(path: Path, judged: dict, *, packet_path: Path, mode: str, meta: dict) -> None:
    """Write the reader's verdicts in the shape `--verdicts` CONSUMES.

    Without this, the documented round-trip (`run --judge fresh` → re-score / re-render
    from the stored verdicts) had no producer: `run` wrote the packet and the scoreboard
    and nothing that `--verdicts` would accept, so the one file in the chain had to be
    assembled by hand. Same dict, same ids, same vocabulary — the artifact this writes is
    accepted by `judge --packet … --verdicts …` and by `run --judge file --verdicts …`.
    """
    doc = {
        "_provenance": {
            "packet": str(packet_path),
            "reader": mode,
            **{k: v for k, v in meta.items() if k in ("elapsed_s", "cmd", "file", "reason")},
            "note": (
                "Written by this instrument. `--verdicts <this file>` re-scores the same "
                "packet without spawning a reader."
            ),
        },
        **{k: v for k, v in judged.items() if not k.startswith("_")},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, default=str))


def cmd_judge(args: argparse.Namespace) -> int:
    """Judge an existing packet (fresh reader or a verdicts file) and re-render."""
    packet_path = Path(args.packet).resolve()
    try:
        packet = json.loads(packet_path.read_text())
    except (OSError, ValueError) as exc:
        print(f"capstone: cannot read packet {packet_path}: {exc}", file=sys.stderr)
        return 2
    # REFUSE a document that is not a judge packet, BEFORE anything is written
    # (#13303 review cycle 2, P1). This command renders a scoreboard from the
    # packet's OWN `questions` and then OVERWRITES `run-{target}.json` /
    # `scoreboard-{target}.md` in the packet's directory by default — so pointing
    # `--packet` at a run file or a verdicts file produced a 0-row board that kept
    # the correct identity header and ingestion table while scoring nothing, and
    # exited 0 on top of the committed baseline. The schema was written by
    # `_judge_packet` and read by no one; now it is the gate.
    schema = str((packet or {}).get("schema") or "")
    if schema != PACKET_SCHEMA:
        print(
            f"capstone: refusing {packet_path} — not a judge packet "
            f"(schema={schema or 'absent'!r}, expected {PACKET_SCHEMA!r}); `judge` would "
            "render an empty scoreboard and overwrite the run beside it",
            file=sys.stderr,
        )
        return 2
    if not packet.get("questions"):
        print(
            f"capstone: refusing {packet_path} — it carries no questions, so there is "
            "nothing to score (an empty scoreboard is not a result)",
            file=sys.stderr,
        )
        return 2
    # The packet's questions ARE the frozen scenario's questions (`_judge_packet` copies
    # them from the narrative, which is frozen before the run). A packet holding a SUBSET
    # rendered a complete-looking board over a different denominator and exited 0 —
    # "2 right · 1 wrong · 0 blocked · 0 unjudged" over three of six questions (#13303
    # cycle 6, P2). Verified only when the packet records a narrative we can read; a
    # packet from another checkout is judged with a note rather than refused.
    same_scenario = _same_questions_as_narrative(packet)
    scenario_note: str | None = None
    if same_scenario is None:
        # The comment above says so, so WRITE it (#13303 cycle 7, P5): a promise with no
        # artifact behind it is the shape this instrument exists against.
        scenario_note = (
            "scenario not verified: the packet records no readable narrative path here, so "
            "the question set could not be checked against the frozen scenario"
        )
    if same_scenario is False:
        print(
            f"capstone: refusing {packet_path} — its questions are not the frozen "
            f"scenario's ({len(packet.get('questions') or [])} in the packet); re-run the "
            "assessment instead of judging a truncated packet",
            file=sys.stderr,
        )
        return 2

    # The bundle's own board comes from a REAL run (`run --judge fresh`), whose header says
    # `mode: fresh` and carries the run's own durations. `judge` rebuilds the header from
    # the packet, which was frozen BEFORE the reader ran, so writing here replaces the
    # baseline with a lower-fidelity copy (`mode: file`, 62.8 s) and exits 0 while doing
    # it — measured, with the bundle's two files changing md5 (#13303 cycle 6, P2).
    out_dir = Path(args.out).resolve() if args.out else packet_path.parent
    if out_dir == DEFAULT_OUT.resolve():
        print(
            f"capstone: refusing to write into the evidence bundle {DEFAULT_OUT} — "
            "`judge` rebuilds the header from the packet, which predates the reader, so "
            "it would replace the real run's board with a lower-fidelity copy. Point "
            "--out at a scratch directory (e.g. /tmp/capstone-rerender).",
            file=sys.stderr,
        )
        return 2
    # The same identity rule as `run`: a re-score must not drop its verdicts into the
    # bundle, where they would describe a run the committed board does not (#13303 c9, P2).
    if getattr(args, "verdicts_out", None):
        vo = Path(args.verdicts_out).resolve()
        if vo.is_dir():
            print(
                f"capstone: refusing --verdicts-out {vo} — that is a directory, not a "
                "verdicts file",
                file=sys.stderr,
            )
            return 2
        if vo.is_relative_to(DEFAULT_OUT.resolve()):
            print(
                f"capstone: refusing --verdicts-out into the evidence bundle {DEFAULT_OUT} "
                "— `judge` does not produce the baseline",
                file=sys.stderr,
            )
            return 2

    if args.verdicts:
        try:
            judged = _load_verdicts_map(args.verdicts)
        except (OSError, ValueError) as exc:
            print(
                f"capstone: refusing {args.verdicts} — {exc}",
                file=sys.stderr,
            )
            return 2
        judge_meta = {"mode": "file", "file": str(args.verdicts)}
    else:
        out = judge_fresh(packet, cmd=args.judge_cmd)
        judged = out.get("verdicts") or {}
        judge_meta = {k: v for k, v in out.items() if k != "verdicts"}
        judge_meta["mode"] = "fresh"
        if not out.get("ok"):
            print(f"capstone: {out.get('reason')}", file=sys.stderr)
    if getattr(args, "verdicts_out", None) and judged:
        _write_verdicts(
            Path(args.verdicts_out).resolve(),
            judged,
            packet_path=packet_path,
            mode=judge_meta.get("mode", "judge"),
            meta=judge_meta,
        )
    narrative = {
        "issue": packet.get("issue"),
        "questions": packet.get("questions"),
        "rubric": packet.get("rubric"),
    }
    # The packet's questions already carry the narrative fields the scoreboard needs.
    narrative["questions"] = [
        {
            "id": q["id"],
            "question": q["question"],
            "key": q.get("key", ""),
            "wrong_means": q.get("what_a_wrong_answer_means", ""),
        }
        for q in packet.get("questions") or []
    ]
    answers = []
    for row in packet.get("product_answers") or []:
        calls = [
            Call(
                c["surface"],
                c.get("request") or {},
                bool(c.get("ok")),
                c.get("detail") or "",
                c.get("read_status") or {},
                c.get("rows") or [],
                c.get("legs") or [],
                c.get("overlap") or {},
                role=c.get("role") or "",
            )
            for c in row.get("calls") or []
        ]
        answers.append(
            Answer(
                row["question"],
                status=row.get("status") or "answered",
                reason=row.get("reason") or "",
                calls=calls,
                payload=row.get("payload") or {},
                signals=row.get("signals") or [],
                notes=row.get("notes") or [],
            )
        )
    header = dict(packet.get("header") or {})
    board = build_scoreboard(
        narrative=narrative,
        header=header,
        answers=answers,
        judged=judged,
        ingest=packet.get("ingest") or [],
    )
    board["header"]["judge"] = judge_meta
    board["judge"] = judge_meta
    if scenario_note:
        board["header"].setdefault("notes", []).append(scenario_note)
        judge_meta["scenario_check"] = "unverified"
    else:
        judge_meta["scenario_check"] = "matches-frozen-scenario"

    md = render_markdown(board)
    print(md)
    # Persist beside the packet so the committed run JSON and the committed
    # scoreboard are the SAME run — a re-render that only printed would let the
    # two drift, and the drift would be invisible.
    target = str(header.get("target") or "local")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"run-{target}.json").write_text(json.dumps(board, indent=2, default=str))
    (out_dir / f"scoreboard-{target}.md").write_text(md)
    print(
        f"capstone: wrote {out_dir / f'run-{target}.json'} and "
        f"{out_dir / f'scoreboard-{target}.md'}",
        file=sys.stderr,
    )
    tally = board["tally"]
    return exit_code(tally)


def _same_questions_as_narrative(packet: dict) -> bool | None:
    """Does this packet carry the FROZEN scenario's question ids?

    `True`/`False` when the packet records a narrative this checkout can read; `None`
    when it records none, or the path does not resolve here (a packet from another
    checkout) — the caller then judges with no claim about the denominator.

    Why it exists: the packet's `questions` ARE `narrative['questions']` (`_judge_packet`
    copies them), so a packet holding a SUBSET is not the scenario the narrative froze —
    and it rendered a complete-looking board over a smaller denominator and exited 0
    (#13303 cycle 6, P2).
    """
    recorded = (packet.get("header") or {}).get("narrative")
    if not recorded:
        return None
    path = Path(recorded)
    if not path.is_absolute():
        path = ROOT / path
    try:
        narrative = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    want = [q.get("id") for q in narrative.get("questions") or []]
    got = [q.get("id") for q in packet.get("questions") or []]
    return bool(want) and want == got


def cmd_render(args: argparse.Namespace) -> int:
    try:
        board = json.loads(Path(args.run).resolve().read_text())
    except (OSError, ValueError) as exc:
        print(f"capstone: cannot read run {args.run}: {exc}", file=sys.stderr)
        return 2
    # A non-board (a verdicts map, a run file, a packet) used to reach `render_markdown` as a
    # `KeyError: 'header'` traceback. Refused with the documented shape instead (#13303 c8).
    missing = {"header", "rows", "tally"} - set(board) if isinstance(board, dict) else None
    if missing is None or missing:
        print(
            f"capstone: refusing {args.run} — not a scoreboard "
            f"({'missing ' + ', '.join(sorted(missing)) if missing else 'not an object'})",
            file=sys.stderr,
        )
        return 2
    # …and `render` is not a producer of the baseline: a board reaches the bundle from a
    # COMPLETE run, so a mistyped `--write` cannot replace it with a re-render of something
    # else (#13303 cycle 8, P2).
    # `render` is not a PRODUCER, so "complete board" is not enough here: a complete board
    # from ANOTHER run would leave `scoreboard-local.md` describing a different run than
    # `run-local.json` beside it, and nothing would say so (#13303 cycle 8, P2). Refused
    # outright; write elsewhere and compare.
    if args.write and Path(args.write).resolve().parent == DEFAULT_OUT.resolve():
        print(
            f"capstone: refusing to write a rendered board into the evidence bundle "
            f"{DEFAULT_OUT} — that board comes from `run`; write elsewhere and copy if it "
            "is genuinely the same run",
            file=sys.stderr,
        )
        return 2
    md = render_markdown(board)
    if args.write:
        Path(args.write).resolve().write_text(md)
        print(f"capstone: wrote {args.write}", file=sys.stderr)
    print(md)
    # The third producer of a scoreboard, and the only one that used to ignore the rule:
    # `render` returned 0 over a board with six `unjudged` rows while EXIT CODES says that
    # is a 1 (#13303 cycle 6, P2). One definition, `exit_code`, for all three.
    return exit_code(board.get("tally") or {})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="capstone_state_value",
        description="The state-model value assessment (#13038): narrative → products "
        "answers → a scored scoreboard. Slow, on demand, never in CI.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="ingest the narrative, ask the six questions, score")
    run.add_argument(
        "--target",
        choices=("local", "deployed"),
        default="local",
        help="local (default: the reachable, disposable target) or deployed",
    )
    run.add_argument("--narrative", default=str(DEFAULT_NARRATIVE))
    run.add_argument("--out", default=str(DEFAULT_OUT))
    run.add_argument("--graph", default=None, help="local graph namespace (default: fresh)")
    run.add_argument("--db-uri", default=None, help="override TORTOISE_DB_URI")
    run.add_argument(
        "--search-timeout-ms",
        type=int,
        default=0,
        help="raise the product's read budget (0 = keep the product's own "
        "500 ms collective cap). Recorded in the header either way.",
    )
    run.add_argument(
        "--judge",
        choices=("fresh", "file", "none"),
        default="none",
        help="score with a fresh reader, from a verdicts file, or not at all",
    )
    run.add_argument("--verdicts", default=None, help="verdicts JSON for --judge file")
    run.add_argument(
        "--verdicts-out",
        default=None,
        help=(
            "write the reader's verdicts here, in the shape --verdicts consumes "
            "(the re-score / re-render round-trip had no producer before this flag)"
        ),
    )
    run.add_argument(
        "--judge-cmd", default=None, help="judge command, default `pi -p --no-session --no-tools`"
    )
    run.add_argument(
        "--allow-existing-graph",
        action="store_true",
        help="write into a graph that is NOT empty (the scenario then "
        "merges into existing data — recorded in the header)",
    )
    run.add_argument(
        "--drop-graph",
        action="store_true",
        help="delete the throwaway graph's nodes when the run finishes",
    )
    run.add_argument("--json", action="store_true")
    run.set_defaults(func=cmd_run)

    judge = sub.add_parser("judge", help="score an existing judge packet and re-render")
    judge.add_argument("--packet", required=True)
    judge.add_argument("--verdicts", default=None)
    judge.add_argument(
        "--verdicts-out",
        default=None,
        help="write the reader's verdicts here, in the shape --verdicts consumes",
    )
    judge.add_argument("--judge-cmd", default=None)
    judge.add_argument(
        "--out",
        default=None,
        help="where to persist the re-rendered run (default: beside the packet)",
    )
    judge.set_defaults(func=cmd_judge)

    render = sub.add_parser("render", help="re-render a committed run JSON")
    render.add_argument("--run", required=True)
    render.add_argument("--write", default=None, help="also write the markdown here")
    render.set_defaults(func=cmd_render)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("capstone: interrupted", file=sys.stderr)
        return 130
    except Exception:
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
