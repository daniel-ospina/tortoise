"""#10524 review ISSUE 3 (P3) — ONE declaration for "what an ABSENT status reports".

The #9974 R1 change made an absent ``status`` the ordinary birth state of an
``:Object``/``:Subject``, so the reference fold's status leg has to compare the
**effective** value (absent READS live) or every status-less birth is reported
as ``divergence="content"`` on a faithful journal.

The first draft spelled that rule INLINE in ``consistency._compare_entities``:

    if (gv if gv is not None else "live") != jv:

while the same PR introduced the one declaration for exactly that question —
``live.status_as_reported`` (with ``STATUS_CARRYING_EPISTEMIC_LABELS``, the
``:Event`` carve-out it exists to carry). Two spellings of one rule that are
**not equivalent in general**: the declaration returns ``""`` for a label whose
layer has no ``status`` family, the inline copy returns ``"live"``
unconditionally. No live bug TODAY only because the leg is bounded to
``_STATUS_COMPARED_LABELS`` = ``{Object, Subject}``, where the two agree — so
the next label widened into that set would silently take the wrong arm of
whichever copy it hit, and a durability verifier taking the wrong arm is a
false GREEN.

⛔ The defect is the MISSING ROUTING, so the pin has to be about routing, not
about a coincidence the two spellings happen to share. ``test_the_status_leg_
routes_through_the_shared_declaration`` is the load-bearing one: it swaps the
declaration for a recording function and fails on ANY code path that does not
call it. The behavioural leg below is the reason routing matters — it is the
case where the two spellings disagree.

Non-vacuity, both legs, against the pre-fix code:
  * ``…routes_through_the_shared_declaration`` — pre-fix the leg never calls the
    helper, so the spy records nothing and the assertion reds.
  * ``…a_label_whose_layer_has_no_status_is_not_read_as_live`` — pre-fix the
    inline copy yields ``"live" == jv``, so ``count == 0`` and the assertion
    reds. It is deliberately a monkeypatched widening of the compared set: the
    hazard the finding names is the NEXT label, and a pin that only re-stated
    today's ``{Object, Subject}`` set could not see it.
  * ``…an_absent_Object_status_still_reads_live`` is the anti-vacuity control —
    without it a fix that compared the RAW value would pass both legs above and
    re-break #9974 R1.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tortoise import consistency


def _rows(*, label: str, journal_status, graph_status):
    """One journal entity and its graph twin, as the comparison sees them."""
    journal = {(label, "eid-1"): {"id": "eid-1", "name": "n1"}}
    if journal_status is not None:
        journal[(label, "eid-1")]["status"] = journal_status
    graph = {(label, "eid-1"): {}}
    if graph_status is not None:
        graph[(label, "eid-1")]["status"] = graph_status
    return journal, graph


def test_the_status_leg_routes_through_the_shared_declaration(monkeypatch):
    """The leg must ASK ``live.status_as_reported``, not re-type its rule.

    Pre-fix ``_compare_entities`` inlined ``(gv if gv is not None else "live")``
    and never called the declaration, so nothing here is recorded and this reds.
    The spy returns the real answer, so the behaviour of the comparison is
    unchanged — what is pinned is that the ONE declaration is the one consulted.
    """
    calls = []
    real = consistency.status_as_reported

    def spy(value, *, label=None):
        calls.append((value, label))
        return real(value, label=label)

    monkeypatch.setattr(consistency, "status_as_reported", spy)

    journal, graph = _rows(label="Object", journal_status="live", graph_status=None)
    mismatches, count, _ = consistency._compare_entities(journal, graph, set(), set())

    assert calls == [(None, "Object")], (
        "the entity-parity status leg did not consult `live.status_as_reported` "
        f"(spy saw {calls!r}) — it is re-typing the absent-reads-live rule inline "
        "again (#10524 review ISSUE 3)")
    assert count == 0, (
        "an absent graph status against a journal `live` must not be a "
        f"divergence (#9974 R1); got {mismatches!r}")


def test_a_label_whose_layer_has_no_status_is_not_read_as_live(monkeypatch):
    """The two spellings DISAGREE here — which is why there must be only one.

    ``:Event`` carries ``eventStatus``, so ``status_as_reported(None,
    label="Event")`` reports ``""`` ("this layer exposes no ``status``"), never
    ``live``. A widened compared set therefore has to see the divergence; the
    pre-fix inline copy reported ``live`` and silently passed.
    """
    monkeypatch.setattr(
        consistency, "_STATUS_COMPARED_LABELS", frozenset({"Object", "Subject", "Event"}))

    journal, graph = _rows(label="Event", journal_status="live", graph_status=None)
    mismatches, count, _ = consistency._compare_entities(journal, graph, set(), set())

    assert count == 1, (
        "a journal `live` against an ABSENT :Event status must be reported — "
        "an :Event has no `status` family, so `\"\"` is not `\"live\"`; the "
        f"inline copy said live and passed vacuously. got {mismatches!r}")
    assert mismatches[0]["field"] == "status", mismatches


@pytest.mark.parametrize("label", ["Object", "Subject"])
def test_an_absent_Object_status_still_reads_live(label):
    """Anti-vacuity: the #9974 R1 direction must survive the re-routing.

    ``status_as_reported`` is only correct as a replacement if it still treats
    an absent status as LIVE on the two families that actually own the field.
    A "fix" that compared the RAW ``gv`` would pass the routing test and red
    this one.
    """
    journal, graph = _rows(label=label, journal_status="live", graph_status=None)
    _, count, _ = consistency._compare_entities(journal, graph, set(), set())
    assert count == 0, (
        f"an absent :{label} status must read live against a journal `live` "
        "(#9974 R1); the effective value is not being compared")

    # …and a genuinely buried (out-of-band terminal) status is still caught.
    journal, graph = _rows(label=label, journal_status="live", graph_status="archived")
    _, count, _ = consistency._compare_entities(journal, graph, set(), set())
    assert count == 1, (
        f"a :{label} buried behind an `archived` status is the failure this leg "
        "exists for; it must still be reported")


# --- the changelog's consumer pointer (review at `22a448f3d`, ISSUE 1 P3) -----
#
# The v3.31 build record publishes a consumer TRAP (a literal `status:'live'`
# filter now drops every status-less birth) and closes it with a scope carve-out
# pointing at the Point idiom: "`MATCH (p:Point {status:'live'})` idiom printed
# in §4.3 remains correct as written." The claim was true and the SECTION was
# wrong — the idiom is printed in §5's `Point Status Vocabulary`. A lane
# following that pointer lands in §4.3 (`### §4.3 Object`), reads the
# pre-change `ObjectRegistered→live` cell, and does not find the Filter it was
# told was there — the exact failure the preceding paragraph exists to prevent.
#
# Non-vacuity, against the pre-fix text (both legs red on it):
#   * the section leg — pre-fix named `§4.3`, whose span is §4's (:385–:956),
#     while the idiom is at :1149 inside §5 and that assertion reds;
#   * the line leg — pre-fix carried no `ONTOLOGY.md:NNNN` citation at all.

_DOCS = Path(__file__).resolve().parent.parent / "docs"
_CHANGELOG = _DOCS / "ONTOLOGY-CHANGELOG.md"
_ONTOLOGY = _DOCS / "ONTOLOGY.md"
_POINT_IDIOM = "MATCH (p:Point {status:'live'})"


def _pointer_text() -> str:
    """The changelog passage that tells a consumer where the Point idiom lives.

    Taken as a window from the idiom forward, because the sentence wraps: the
    section name and the `ONTOLOGY.md:NNNN` citation can sit on either side of
    the line break.
    """
    text = _CHANGELOG.read_text(encoding="utf-8")
    start = text.index(_POINT_IDIOM)
    window = text[start:start + 400]
    assert "idiom printed in" in window, (
        "the v3.31 block no longer points a consumer at the Point idiom — this "
        "pin has lost its subject, re-anchor it rather than deleting it")
    return window


def _section_span(number: str) -> range:
    """Line numbers (1-based) covered by `## §<number>` up to the next `## §`."""
    lines = _ONTOLOGY.read_text(encoding="utf-8").splitlines()
    head_pat = re.compile(r"^## §(\d+)\.")
    headings = [(i + 1, m.group(1)) for i, line in enumerate(lines)
                if (m := head_pat.match(line))]
    for idx, (line_no, sec) in enumerate(headings):
        if sec != number:
            continue
        end = headings[idx + 1][0] if idx + 1 < len(headings) else len(lines) + 1
        return range(line_no, end)
    raise AssertionError(f"`docs/ONTOLOGY.md` has no `## §{number}` section")


def test_the_point_idiom_pointer_names_the_section_that_prints_it():
    """The named § must actually CONTAIN the idiom it points at.

    Section numbers drift; the claim that matters is "go here and you will find
    it". Pre-fix this named §4.3 and red; §5 is where `:1149` lives.
    """
    window = _pointer_text()
    named = re.search(r"§(\d+(?:\.\d+)?)", window)
    assert named, f"the Point-idiom pointer names no section: {window[:160]!r}"
    section = named.group(1)

    idiom_lines = [
        i + 1 for i, line in enumerate(_ONTOLOGY.read_text(encoding="utf-8").splitlines())
        if _POINT_IDIOM in line
    ]
    assert idiom_lines, (
        "`docs/ONTOLOGY.md` no longer prints the Point idiom — the changelog's "
        "carve-out claims it still does")

    span = _section_span(section.split(".")[0])
    in_section = [n for n in idiom_lines if n in span]
    assert in_section, (
        f"the changelog points a consumer at §{section} for the Point idiom, but "
        f"`ONTOLOGY.md` prints it at {idiom_lines} — outside §{section.split('.')[0]} "
        f"({span.start}–{span.stop - 1}). A lane following the pointer lands in the "
        "wrong section and does not find it (#10524 review ISSUE 1)")


def test_the_point_idiom_pointer_line_number_lands_on_the_idiom():
    """A `ONTOLOGY.md:NNNN` citation must resolve to a line carrying the idiom.

    The line number is what makes the pointer actionable; pre-fix the sentence
    cited no line at all, and a later edit can silently move it.
    """
    window = _pointer_text()
    cited = re.search(r"ONTOLOGY\.md:(\d+)", window)
    assert cited, (
        "the Point-idiom pointer carries no `ONTOLOGY.md:NNNN` citation, so the "
        f"consumer has a section number and no landmark: {window[:200]!r}")

    line_no = int(cited.group(1))
    lines = _ONTOLOGY.read_text(encoding="utf-8").splitlines()
    assert 1 <= line_no <= len(lines), (
        f"the changelog cites `ONTOLOGY.md:{line_no}`, but the file has "
        f"{len(lines)} lines")
    assert _POINT_IDIOM in lines[line_no - 1], (
        f"the changelog cites `ONTOLOGY.md:{line_no}` for the Point idiom, but "
        f"that line reads {lines[line_no - 1].strip()!r} — the landmark moved "
        "and the pointer was not updated with it")


# --- the changelog's READ-SURFACE SWEEP cites (review at `85c7b0198`, ISSUE 1 P3) ---
#
# The same v3.31 block closes the consumer trap with the sweep claim: "it is why
# every in-repo read surface was swept before this landed", evidenced by THREE
# cites — the Object leg's NULL-tolerant predicate, `projection/entities.py`'s
# `WITH o, (o.status IS NULL OR NOT …) AS live`, and `audit.py`'s spelled-out arm.
# That paragraph is the only place telling a consumer where to verify the sweep
# after `ONTOLOGY.md`'s cells were reverted, so a cite that does not resolve sends
# a lane to unrelated code — and the plausible next step for that lane is to
# "fix" a read surface that is already correct.
#
# Measured drift, inside THIS PR: the Object-leg cite read `sdk.py:20346` when it
# was written (`22a448f3d`, where `grep -n "_terminal_excluded('o.status')"` gave
# 20346) and the later merge of `origin/main` (`a7485da75`) inserted 55 lines
# above it, leaving `:20346` in a docstring and the leg at `:20401`. That is the
# failure `docs/00_index.md`'s edited cell already names — *"a bare `sdk.py:<line>`
# here drifted by +152 lines … a symbol cannot re-stale"* — so the fix cites the
# SYMBOL and this pin accepts either form while holding each to its own currency.
#
# Non-vacuity, against the pre-fix text (`85c7b0198` + the merge):
#   * the line leg — pre-fix carried `sdk.py:20346`, whose line is docstring prose
#     ("natural key: under #3590 D2 two live Objects may hold it, so the read"), so
#     `_terminal_excluded('o.status') in lines[20346-1]` reds;
#   * the SYMBOL leg — measured by MUTATION, not by the pre-fix text, which carried
#     a line cite and so satisfied `symbol_cites or line_cites` while the symbol
#     loop ran ZERO times. Rewriting the arm out of `belief_timeline`'s body (the
#     `_terminal_excluded('o.status')` call renamed or removed) reds this pin on
#     "that method's body carries no `…`", which is what makes the symbol cite
#     load-bearing rather than decorative. That is also why the symbol leg is now
#     REQUIRED below (`assert symbol_cites`): the doctrine this pin serves is
#     "a symbol cannot re-stale", and a paragraph that reverted to a bare line
#     would be re-vacuumed by the next merge.

_SDK = Path(__file__).resolve().parent.parent / "tortoise" / "sdk.py"
_PROJECTION_ENTITIES = Path(__file__).resolve().parent.parent / "tortoise" / "projection" / "entities.py"
_AUDIT = Path(__file__).resolve().parent.parent / "tortoise" / "audit.py"

#: The leg's own code, in the one spelling the sweep paragraph quotes it by.
_OBJECT_LEG = "_terminal_excluded('o.status')"

_SWEEP_START = "every in-repo read surface was swept"
_SWEEP_END = "relies on the literal"


def _sweep_text() -> str:
    """The changelog passage that evidences the read-surface sweep."""
    text = _CHANGELOG.read_text(encoding="utf-8")
    start = text.index(_SWEEP_START)
    return text[start:text.index(_SWEEP_END, start)]


def _def_body(source: str, name: str) -> str:
    """The source of `def <name>`, up to the next def/class at the same or lower depth.

    Deliberately not `ast`: this is a citation check on TEXT (the shape the
    changelog's other cite pins already use), and the two files it reads are
    already read as text by them.
    """
    lines = source.splitlines()
    for i, line in enumerate(lines):
        m = re.match(rf"^(\s*)(?:async )?def {re.escape(name)}\(", line)
        if not m:
            continue
        depth = len(m.group(1))
        end = len(lines)
        for j in range(i + 1, len(lines)):
            nxt = re.match(r"^(\s*)(?:async )?def |^(\s*)class ", lines[j])
            if nxt and len(nxt.group(1)) <= depth:
                end = j
                break
        return "\n".join(lines[i:end])
    raise AssertionError(
        f"the changelog cites `{name}`, but neither file defines a "
        f"`def {name}(` — the symbol moved or was renamed")


def test_the_read_surface_sweep_cites_resolve_at_this_head():
    """Every cite evidencing the sweep must LAND on what it claims.

    A line cite is checked because it CAN re-stale (that is the defect this pin
    was filed for); a symbol cite is checked because it is the fix, and a symbol
    that resolves to a method which no longer carries the leg is just as wrong.
    """
    window = _sweep_text()
    source = _SDK.read_text(encoding="utf-8")
    lines = source.splitlines()

    symbol_cites = re.findall(r"TortoiseSDK\.(\w+)", window)
    line_cites = [int(m.group(1)) for m in re.finditer(r"(?<![\w/.])sdk\.py:(\d+)", window)]
    assert symbol_cites or line_cites, (
        "the sweep paragraph cites no `sdk.py` Object leg at all — the claim "
        f"'every in-repo read surface was swept' is then unverifiable: {window[:240]!r}")
    assert symbol_cites, (
        "the sweep paragraph cites the Object leg by LINE only — a bare line "
        "cannot survive `sdk.py` growing above it (it drifted +55 inside this "
        "very PR), so the resumed line is stale before the next merge. Cite "
        "`TortoiseSDK.<method>` (#10524 review ISSUE 1)")

    for name in symbol_cites:
        body = _def_body(source, name)
        assert _OBJECT_LEG in body, (
            f"the sweep paragraph cites `TortoiseSDK.{name}` for the Object leg, "
            f"but that method's body carries no `{_OBJECT_LEG}` — the symbol "
            "resolves and the claim does not (#10524 review ISSUE 1)")

    for line_no in line_cites:
        assert 1 <= line_no <= len(lines), (
            f"the changelog cites `sdk.py:{line_no}`, but the file has {len(lines)} lines")
        assert _OBJECT_LEG in lines[line_no - 1], (
            f"the changelog cites `sdk.py:{line_no}` for the Object NULL-tolerant "
            f"leg, but that line reads {lines[line_no - 1].strip()!r}. A bare line "
            "cite cannot survive this file growing above it — cite the symbol "
            "(`TortoiseSDK.<method>`) instead (#10524 review ISSUE 1)")

    # The two sibling cites in the same sentence carry the rest of the evidence.
    # Same rule, same two currencies: whichever form the paragraph names must
    # resolve — a line range is checked against its span, a `_fold_object_superseded`
    # symbol against that method's body. (This leg is not hypothetical either: at
    # the head this review read, the range was `:4746-4748`; the merged tree moved
    # it to `:4800-4802`, and the pre-merge range lands in a docstring.)
    entities_source = _PROJECTION_ENTITIES.read_text(encoding="utf-8")
    entity_spans = [m for m in re.finditer(r"projection/entities\.py:(\d+)-(\d+)", window)]
    entity_symbols = re.findall(r"`(_fold_object_superseded)`", window)
    assert entity_spans or entity_symbols, (
        "the sweep paragraph cites no `projection/entities.py` fold at all — the "
        f"NULL-tolerant-folding half of the evidence is unverifiable: {window[:240]!r}")
    for m in entity_spans:
        first, last = int(m.group(1)), int(m.group(2))
        block = "\n".join(entities_source.splitlines()[first - 1:last])
        assert "o.status IS NULL" in block, (
            f"the changelog cites `projection/entities.py:{first}-{last}` for the "
            f"NULL-tolerant fold, but that span reads {block.strip()!r}")
    for name in entity_symbols:
        assert "o.status IS NULL" in _def_body(entities_source, name), (
            f"the changelog cites `projection/entities.py`'s `{name}` for the "
            "NULL-tolerant fold, but that method's body does not carry "
            "`o.status IS NULL`")

    for m in re.finditer(r"audit\.py:(\d+),(\d+)", window):
        audit_lines = _AUDIT.read_text(encoding="utf-8").splitlines()
        for line_no in (int(m.group(1)), int(m.group(2))):
            assert "active.status IS NULL" in audit_lines[line_no - 1], (
                f"the changelog cites `audit.py:{line_no}` for the spelled-out "
                f"NULL arm, but that line reads {audit_lines[line_no - 1].strip()!r}")


# --- `tortoise/log.py`'s disclosed-exception cites (review at `3a37fb34e`, ISSUE 1 P3) ---
#
# `log.py`'s "ONE DISCLOSED EXCEPTION, AND ITS BOUNDARY" block is the load-bearing
# text that tells a durability reader WHY a torn ``PointAdded``/``OperatorAdded`` is
# tolerated at all. It evidenced that exception with three cites into
# `projection/entities.py` — `:2525`, `:2515`, `:2627` — and NONE of them landed on
# the clause the sentence names: at this head those lines are comment prose ("# #5008:
# through the EMBED gate — an identifier-only text earns no vector.", "# express a
# clear; only an explicit arm can (see $embedding_clear", "# An un-sourced Point, or
# one whose Source has no recorded hash, carries"). They were not right at the merge
# base either (2555 / 2559 / 2661), so this is the same rot `docs/00_index.md`'s own
# cell names — *"a bare `sdk.py:<line>` here drifted by +152 lines … a symbol cannot
# re-stale"* — in the one place in this PR where that rule was applied and not
# verified. The next reader is a durability auditor deciding whether a resurrection
# risk is REAL, so a cite that lands on unrelated prose is not a nit.
#
# Fix: cite the SYMBOL (`_upsert_point_props`), and hold both forms here the way the
# sweep pin above does — a symbol must resolve to a body that CARRIES the clause, a
# line cite must LAND on it.
#
# Non-vacuity, measured against the pre-fix block (each leg reds on its own):
#   * the LINE leg — with `:2525`/`:2515`/`:2627` restored the pin reds on the cite
#     that lands on comment prose;
#   * the CLAUSE leg — with the operator clause renamed out of the function body
#     (`n.direction=$dir` → `n.dirn=$dir`) the pin reds on "that function's body
#     does not carry it". This leg is what stops a symbol cite from being
#     decorative. The pre-fix block already CONTAINED a bare ``_upsert_point_props``
#     mention (in the `PointPromoted` sentence), which is why the missing symbol was
#     never the defect: the defect is that no cite — symbol or line — CARRIED the
#     clauses the sentence names, and only the symbol is durable enough to hold them.

_LOG = Path(__file__).resolve().parent.parent / "tortoise" / "log.py"

_LOG_BLOCK_START = "ONE DISCLOSED EXCEPTION, AND ITS BOUNDARY"
_LOG_BLOCK_END = "WHY THE FOUR REMAIN"

#: The three clauses the block names, in the spelling `entities.py` writes them. The
#: block itself spaces the SET assignment (`n.status = coalesce(...)`), so the
#: comparison is whitespace-insensitive rather than text-identical — a pin that reds
#: on spacing teaches the next editor to weaken it.
_LOG_CLAUSES = (
    "n.status=coalesce($st, n.status, 'live')",
    "n.content=$content",
    "n.direction=$dir",
)


def _normalised(text: str) -> str:
    """Whitespace-free form, for the one spacing difference between prose and code."""
    return re.sub(r"\s+", "", text)


def _log_exception_block() -> str:
    """The disclosed-exception passage of `tortoise/log.py`."""
    text = _LOG.read_text(encoding="utf-8")
    start = text.index(_LOG_BLOCK_START)
    return text[start:text.index(_LOG_BLOCK_END, start)]


def test_the_disclosed_exception_cites_resolve_at_this_head():
    """Every cite behind the torn-tail exception must LAND on what it claims."""
    window = _log_exception_block()
    source = _PROJECTION_ENTITIES.read_text(encoding="utf-8")
    lines = source.splitlines()

    symbol_cites = re.findall(r"`(_upsert_point_props)`", window)
    line_cites = [int(m.group(1)) for m in re.finditer(r"entities\.py:(\d+)", window)]
    line_cites += [int(m.group(1)) for m in re.finditer(r",\s*:(\d+)", window)]
    assert symbol_cites or line_cites, (
        "the torn-tail exception cites no `projection/entities.py` fold at all, so "
        "the claim that its folds write an authoritative value STRAIGHT from the "
        f"payload is unverifiable: {window[:240]!r}")

    for name in symbol_cites:
        body = _normalised(_def_body(source, name))
        for clause in _LOG_CLAUSES:
            assert _normalised(clause) in body, (
                f"the exception block cites `projection/entities.{name}` for "
                f"`{clause}`, but that function's body does not carry it — the "
                "symbol resolves and the claim does not (#10524 review ISSUE 1)")

    for line_no in line_cites:
        assert 1 <= line_no <= len(lines), (
            f"`log.py` cites `entities.py:{line_no}`, but the file has "
            f"{len(lines)} lines")
        cited = _normalised(lines[line_no - 1])
        assert any(_normalised(c) in cited for c in _LOG_CLAUSES), (
            f"`log.py`'s torn-tail exception cites `entities.py:{line_no}`, but that "
            f"line reads {lines[line_no - 1].strip()!r} — none of the three clauses "
            f"it names ({', '.join(_LOG_CLAUSES)}) is there. A bare line cite cannot "
            "survive this file growing above it; cite the symbol "
            "(`_upsert_point_props`) instead (#10524 review ISSUE 1)")


# --- the REST of `tortoise/log.py`'s module comment (delta review, ISSUE 3) ---
#
# The disclosed-exception pin above holds only the passage it names
# (`ONE DISCLOSED EXCEPTION` … `WHY THE FOUR REMAIN`). The SAME module comment
# carried SIX more `projection/entities.py:<line>` cites — plus four into
# `projection/__init__.py` and one into `hosted_api.py` — and every one of them
# was dead at the head the delta review read: `:719-730 → :772` landed on a
# docstring and a `github_issue` list, `entities.py:853` on a blank line,
# `:1650`/`:1713-1719` on unrelated prose, `:1989` on a comment about
# `_DOCUMENT_HANDLED`, `:1261-1266` on `return False`, `:2390`/`:2399` on
# docstring text, `projection/__init__.py:6734-6736` on a journal-ownership
# paragraph, `:6585`/`:6717` on blank lines, and `hosted_api.py:10041-10065` on
# the `/v1/points` route. A lane told "cite the symbol, a line cannot re-stale"
# reads a survivor as verified — so the sweep has to cover the WHOLE comment,
# not the one passage a review happened to quote.
#
# The invariant below is deliberately the rot-proof one: after repointing, this
# comment cites NO `file.py:<line>` at all, so there is nothing left to drift.
# (Bare historical mentions such as "the three line cites that stood here
# (`:2525`, …)" are not cites — they point at nothing and are labelled as such.)
_LOG_COMMENT_START = "# ── torn-tail revival classification (#3316)"
_LOG_COMMENT_END = '# A ``"type"`` KEY'

#: Every symbol the comment leans on, and the file that must still define it.
#: Renaming one reds this pin instead of silently leaving a decorative cite.
_LOG_SYMBOLS = {
    "tortoise/projection/entities.py": (
        "_upsert_point_props", "_belief_prop_value_ok", "_fold_session_recorded",
        "_upsert_document", "_DOC_RETIRED_KEYS", "_materialize_connector_source",
    ),
    "tortoise/projection/__init__.py": (
        "_apply_one", "_apply_annotator", "_revise_point", "_annotator_value_ok",
    ),
    "tortoise/vector_write.py": (
        "clear_embedding", "clear_then_set_prefix", "set_embedding_clause",
    ),
    "tortoise/hosted_api.py": ("_capture_session_impl",),
}


def _log_module_comment() -> str:
    """The whole `#3316` torn-tail comment block, not only its exception part."""
    text = _LOG.read_text(encoding="utf-8")
    start = text.index(_LOG_COMMENT_START)
    return text[start:text.index(_LOG_COMMENT_END, start)]


def test_the_log_module_comment_cites_no_file_line_number():
    """No `file.py:<line>` cite may survive in the comment — they all rot.

    Pre-fix this reds on eleven dead cites (listed above); post-fix it is the
    invariant that keeps the next merge from re-introducing one.
    """
    comment = _log_module_comment()
    line_cites = re.findall(r"[\w/]+\.py:(\d+)", comment)
    assert not line_cites, (
        "`tortoise/log.py`'s torn-tail comment still cites a LINE "
        f"({line_cites}) — a line cannot survive the file growing above it (the "
        "cites in this block were 100+ lines off), so cite the SYMBOL "
        "(``_upsert_point_props``, ``clear_embedding``, …) instead "
        "(#10524 delta review ISSUE 3)")


def test_every_symbol_the_log_module_comment_cites_still_exists():
    """A symbol cite is only the fix if the symbol resolves.

    Checked against the defining FILE, so a rename or a move reds here rather
    than leaving the durability reader a plausible-looking dead end.
    """
    root = Path(__file__).resolve().parent.parent
    missing = []
    for rel_path, names in _LOG_SYMBOLS.items():
        source = (root / rel_path).read_text(encoding="utf-8")
        for name in names:
            defined = re.search(
                rf"^\s*(?:async )?def {re.escape(name)}\(|^\s*{re.escape(name)}\s*[:=]"
                rf"|^\s*(?:async )?def {re.escape(name)}\b",
                source, re.MULTILINE)
            if defined is None:
                missing.append(f"{rel_path}::{name}")
    assert not missing, (
        "`tortoise/log.py`'s torn-tail comment cites symbols that no longer "
        f"exist: {missing} — repoint the cite (#10524 delta review ISSUE 3)")
