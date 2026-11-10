"""#11739 — the deterministic composed embedding text, gated OFF by default.

What this file pins, and why each test exists:

* the GATE: ``TORTOISE_COMPOSED_EMBED_TEXT`` is fail-safe OFF, and an unquoted,
  unbound point is the BYTE-IDENTICAL pre-#11739 write (no ``embeddingText``
  property, the vector computed from ``content``) — INCLUDING content whose
  whitespace would not survive ``" ".join(split())``, which is the case that
  makes the arm falsifiable;
* the COMPOSITION: verbatim ``quote`` + RESOLVED neighbour names, with the ONE
  declared order (``(casefold, spelling)``) — permutation-invariant, so the
  composed text is byte-identical across runs (decision 3 on #11739);
* the TRUNCATION BUDGET: the names line is RESERVED out of the 512-word encode
  budget, so names appended to a ≥512-word quote still reach the vector. The
  pre-fix shape (append, then let the head-kept cut discard the tail) reds
  ``test_the_names_survive_a_quote_that_fills_the_word_budget``. The budget
  drops WHOLE names, never a fragment of one;
* the NAMES HALF is caller-supplied (the recompose seam's job, not the create
  door's): the create door composes the quote alone because a Point has no
  resolved neighbours at creation, so no test here claims otherwise;
* NAMES ONLY, bounded: a neighbour contributes its own name — never its own
  composed text and never a nested container — and ``NAME_FANOUT_CEILING``
  bounds a point with dozens of edges;
* REDACTION: a name passed as ``redacted`` never appears in the composed text,
  and it frees its slot for a live neighbour;
* the WIRING (#4194, schema A′): with the flag ON the point HOLDS the composed
  text (``embeddingText``), the ONE vector is computed FROM it, and the
  ``PointAdded`` record carries both — so ``derived = replay(journal)`` restores
  the composed text rather than recomposing it (decision 4), read off the real
  journal, not a hand-built payload;
* the #4194 guards the vector-owning arms need: a CALLER-supplied vector and an
  UNAVAILABLE embedder both leave ``embeddingText`` off the node (nothing
  describes it), and composition is CREATE-only — a revise that edits ``quote``
  leaves the existing composed text AND its vector in place (a DECLARED
  residual, both halves pinned by
  ``test_a_QUOTE_revise_does_not_recompose_create_only``);
* the NARROWING: the create door composes the quote only and never infers a
  neighbour from an ``about*`` prop (the props are plain strings there, not
  relations), pinned in that direction.

Embedded SDK fixture (the same shape as ``tests/test_about_edges.py``): a
tempfile FalkorDBLite graph, so nothing here needs a live server or a model —
the encoder is a recorder, and the assertions are about WHICH TEXT reached it.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.embed_compose import (
    COMPOSE_ENV,
    EMBED_WORD_BUDGET,
    NAME_FANOUT_CEILING,
    compose_embedding_text,
    compose_enabled,
    names_line,
    quote_text,
)
from tortoise.embeddings import stamp_journal_embedding
from tortoise.ids import content_hash
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    """A fresh SDK on an isolated embedded graph."""
    db_path = f"{tempfile.mkdtemp(prefix='tt_11739_')}/test.db"
    s = TortoiseSDK(db_path)
    s.test_guard = lambda: None
    yield s
    s.close()
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


@pytest.fixture
def journal_sdk(tmp_path):
    """An SDK whose JSONL journal is a temp file — the replay evidence."""
    journal = tmp_path / "journal.jsonl"
    s = TortoiseSDK(str(tmp_path / "journal.db"), event_log_path=str(journal))
    s.test_guard = lambda: None
    s._journal_path_for_test = journal
    try:
        yield s
    finally:
        s.close()


# ── the gate ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", [None, "", "   ", "0", "false", "off", "no",
                                 "garbage"])
def test_the_gate_is_fail_safe_off(monkeypatch, raw):
    if raw is None:
        monkeypatch.delenv(COMPOSE_ENV, raising=False)
    else:
        monkeypatch.setenv(COMPOSE_ENV, raw)
    assert compose_enabled() is False


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on"])
def test_the_gate_arms_on_the_shared_truthy_spellings(monkeypatch, raw):
    monkeypatch.setenv(COMPOSE_ENV, raw)
    assert compose_enabled() is True


# ── the composition ─────────────────────────────────────────────────────

def test_an_unbound_unquoted_point_composes_to_its_content_UNCHANGED():
    """The degrade arm is byte-identity — not a normalisation.

    Measured review finding (#11739): returning ``" ".join(content.split())``
    here re-attested every point whose content carries a newline, so the flag
    was NOT a no-op on an unbound graph.
    """
    content = "  an  unbound\nclaim  "
    assert compose_embedding_text("", [], content=content) == content


def test_a_node_with_no_resolved_neighbours_composes_to_exactly_its_quote():
    composed = compose_embedding_text("the exact words", [],
                                      content="a paraphrase of them")
    assert composed == "the exact words"


def test_the_quote_is_taken_verbatim_before_the_content():
    composed = compose_embedding_text("  the   exact\n words  ", [])
    assert composed == "the exact words"


def test_a_list_valued_quote_composes_in_its_own_record_order():
    """``quote`` is a flatten-list prop; the spans' order is the source's."""
    assert quote_text(["second span", "first span"]) == "second span first span"


def test_the_fan_out_ceiling_keeps_the_first_names_in_the_declared_order():
    many = [f"entity-{i:02d}" for i in range(NAME_FANOUT_CEILING + 8)]
    line = names_line(reversed(many))
    assert line == "Related: " + ", ".join(many[:NAME_FANOUT_CEILING])


def test_the_names_ride_under_our_label_and_never_a_caller_supplied_one():
    assert names_line(["a", "b"]) == "Related: a, b"
    assert names_line([]) == ""


def test_the_names_line_is_word_bounded_and_drops_WHOLE_names():
    """A name cut mid-way would name nothing and still cost a slot."""
    long_names = [f"Internal Platform Component Number {i:02d}"
                  for i in range(NAME_FANOUT_CEILING)]
    line = names_line(long_names)
    assert len(line.split()) <= 48
    for name in line.removeprefix("Related: ").split(", "):
        # Every surviving entry is a COMPLETE name from the input.
        assert name in long_names


def test_a_single_name_over_the_whole_budget_yields_no_line():
    monster = " ".join(["word"] * 60)
    assert names_line([monster]) == ""
    assert compose_embedding_text("q", [monster]) == "q"


def test_the_names_survive_a_quote_that_fills_the_word_budget():
    """THE truncation-budget defect this decision exists to prevent.

    ``_truncate_for_embedding`` keeps the HEAD of the text, so a names line
    appended after a ≥512-word quote would contribute ZERO. The composition
    reserves the names out of the budget instead, so they are always inside the
    encoded window — and the total still fits it.
    """
    quote = " ".join(f"w{i}" for i in range(EMBED_WORD_BUDGET + 100))
    composed = compose_embedding_text(quote, ["Acme", "Release-4"])
    words = composed.split()
    assert len(words) <= EMBED_WORD_BUDGET
    # The composed line is comma-joined, so assert on the text, not a token.
    assert "Acme" in composed and "Release-4" in composed
    assert composed.endswith("Related: Acme, Release-4")


def test_a_short_quote_is_not_cut_at_all():
    quote = " ".join(f"w{i}" for i in range(10))
    composed = compose_embedding_text(quote, ["Acme"])
    assert composed == f"{quote}\n\nRelated: Acme"


def test_a_redacted_name_never_appears_in_the_composed_text():
    """#11739's redaction requirement, at the seam that has to satisfy it."""
    composed = compose_embedding_text(
        "a claim", ["Deleted Co", "Live Co"], redacted=["deleted co"])
    assert "Deleted Co" not in composed
    assert "Live Co" in composed


def test_a_redaction_frees_the_slot_for_a_live_neighbour():
    many = [f"entity-{i:02d}" for i in range(NAME_FANOUT_CEILING + 1)]
    line = names_line(many, redacted=[many[0]])
    assert many[0] not in line
    assert many[NAME_FANOUT_CEILING] in line


# ── the wiring: what the vector describes (#4194) ───────────────────────

class _Recorder:
    """Records the exact text handed to the ONE write-side encode seam."""

    def __init__(self, vector=None):
        self.seen: list[str] = []
        self._vector = [0.5, 0.5, 0.5] if vector is None else vector

    def __call__(self, content, expected_dim):
        self.seen.append(content)
        return self._vector


@pytest.fixture
def recorder(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr("tortoise.embeddings.encode_for_row", rec)
    return rec


def test_OFF_embeds_the_content_and_stores_no_embeddingText(
        sdk, recorder, monkeypatch):
    monkeypatch.delenv(COMPOSE_ENV, raising=False)
    point = sdk.create_point("statement", "a raw paraphrase",
                             quote="the exact words", aboutObject="Acme")
    assert recorder.seen == ["a raw paraphrase"]
    assert "embeddingText" not in sdk.get_point(point["id"])


def test_ON_does_not_touch_a_point_with_no_quote_and_no_neighbours(
        sdk, recorder, monkeypatch):
    """The degrade arm, with content whose whitespace would NOT survive a collapse."""
    content = "an  unbound\nclaim"
    monkeypatch.delenv(COMPOSE_ENV, raising=False)
    off = sdk.create_point("statement", content)
    off_point = sdk.get_point(off["id"])
    off_calls = list(recorder.seen)

    monkeypatch.setenv(COMPOSE_ENV, "1")
    on = sdk.create_point("statement", content)
    on_point = sdk.get_point(on["id"])

    assert recorder.seen[len(off_calls):] == [content]
    assert "embeddingText" not in off_point
    assert "embeddingText" not in on_point


def test_the_field_the_hash_attests_is_the_COMPOSED_text(sdk, recorder,
                                                        monkeypatch):
    """Schema A′ / #4194: the hash attests the text the vector describes."""
    monkeypatch.setenv(COMPOSE_ENV, "1")
    point = sdk.create_point("statement", "a raw paraphrase",
                             quote="the exact words", aboutObject="Acme")
    composed = sdk.get_point(point["id"])["embeddingText"]
    assert recorder.seen == [composed]
    stamped = stamp_journal_embedding(
        {"id": "p", "content": "a raw paraphrase", "embeddingText": composed,
         "embedding": [0.5, 0.5, 0.5]}, creating=True)
    assert stamped["embedding_text_hash"] == content_hash(composed)
    # The #4194 lie this must never become: attesting the raw content while the
    # vector describes the composition.
    assert stamped["embedding_text_hash"] != content_hash("a raw paraphrase")


def test_the_JOURNAL_carries_the_composed_text_and_its_hash(
        journal_sdk, recorder, monkeypatch):
    """Decision 4's falsifier, read off the REAL record rather than a stub.

    ``derived = replay(journal)`` must RESTORE the composed text: if the record
    omitted it (or attested the raw content), the replay would recompose — or
    worse, restore a vector whose attested text the node does not hold.
    """
    monkeypatch.setenv(COMPOSE_ENV, "1")
    point = journal_sdk.create_point("statement", "a raw paraphrase",
                                     quote="the exact words")
    composed = "the exact words"
    records = [json.loads(line)
               for line in journal_sdk._journal_path_for_test.read_text()
               .splitlines() if line.strip()]
    added = [r for r in records
             if r.get("type") == "PointAdded"
             and r.get("point", {}).get("id") == point["id"]]
    assert added, "no PointAdded record for the composed point"
    payload = added[-1]["point"]
    assert payload["embeddingText"] == composed
    assert payload["embedding_text_hash"] == content_hash(composed)


def test_the_create_door_does_NOT_invent_a_neighbour_from_an_about_prop(
        sdk, recorder, monkeypatch):
    """#11739 review round 4 (P1), pinned in the direction that matters.

    `create_point` mints NO `about*` edge — it stores the prop as a plain string
    and the EDGE is written by a later pass. Composing a name from that string
    would attest a relation the graph does not hold, so the create door composes
    the quote alone and the names half waits for the recompose seam.
    """
    monkeypatch.setenv(COMPOSE_ENV, "1")
    point = sdk.create_point("statement", "a raw paraphrase",
                             quote="the exact words", aboutObject="Acme Corp")
    assert sdk.get_point(point["id"])["embeddingText"] == "the exact words"
    assert recorder.seen == ["the exact words"]


def test_a_CALLER_supplied_vector_leaves_embeddingText_off_the_node(
        sdk, recorder, monkeypatch):
    """#4194 guard (review finding): nothing describes a caller's own vector."""
    monkeypatch.setenv(COMPOSE_ENV, "1")
    point = sdk.create_point("statement", "a raw paraphrase",
                             quote="the exact words", aboutObject="Acme",
                             embedding=[0.25, 0.25])
    assert "embeddingText" not in sdk.get_point(point["id"])


def test_an_UNAVAILABLE_embedder_leaves_embeddingText_off_the_node(
        sdk, monkeypatch):
    """Nothing describes the composed text when no vector was computed for it."""
    monkeypatch.setenv(COMPOSE_ENV, "1")
    monkeypatch.setattr("tortoise.embeddings.encode_for_row",
                        lambda content, dim: None)
    point = sdk.create_point("statement", "a raw paraphrase",
                             quote="the exact words", aboutObject="Acme")
    assert "embeddingText" not in sdk.get_point(point["id"])


def test_a_QUOTE_revise_does_not_recompose_create_only(sdk, recorder,
                                                     monkeypatch):
    """Composition is CREATE-only — the disclosed residual, pinned.

    A `quote` revise leaves the existing composed text AND the vector computed
    from it in place — BOTH halves are asserted, because a change that cleared
    the vector while keeping the text would leave the #4194 state this test
    exists to forbid (review round 5, P2). Clearing them here was review round
    2's measured defect: the clear diverged live from rebuild. Pinned so the next
    lane meets the residual as a DECLARED state rather than rediscovering it.
    """
    monkeypatch.setenv(COMPOSE_ENV, "1")
    point = sdk.create_point("statement", "a raw paraphrase",
                             quote="the exact words")
    composed = sdk.get_point(point["id"])["embeddingText"]
    assert composed == "the exact words"
    vector_before = sdk.get_point(point["id"])["embedding"]

    sdk.update_point(point["id"], quote="completely different words")
    after = sdk.get_point(point["id"])
    assert after.get("quote") == "completely different words"
    assert after.get("embeddingText") == composed
    assert after.get("embedding") == vector_before
    assert after.get("embedding") is not None
