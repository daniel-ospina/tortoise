"""#9287: the WRITE DOOR refuses a ``pointKind`` carrying invisible characters.

The calibration gate (#9063/#9261) demands a prior for every node EP consumes
whose ``pointKind`` normalises to an evidence kind, and EP reads NO kind at all.
So a kind the gate cannot classify is a node that is consumed and stamped while
the gate stays silent — measured at ``HEAD`` before this change::

    create_point('\\u2003statement\\u2003', 'x')       # stored verbatim, warning only
    _uncalibrated_ep_participants(None)              # -> []          (FAIL-OPEN)
    compute_confidence(require_calibration=True)     # RAN
      -> node stamped: ep_alpha 1.287, ep_beta 1.242  (the #7478 class)

The fix is the one #9287 recommends (option 1): the read-path family
(``_KIND_WHITESPACE_CHARS``) stays small and cheap because it only has to cover
IMPORTED rows, and the write door refuses the spellings it cannot classify —
naming the argument and the code point, rather than silently rewriting what the
caller asked for (a silent canonical form would also move the dedup identity
``(pointKind, content_hash)`` behind the caller's back).

Every door that can carry a caller- or model-supplied ``pointKind`` is gated:
``create_point`` (positional kind AND the props passthrough, which WINS over the
positional), ``update_point``, and ``_update_entity`` (the OTHER documented
update door onto a ``:Point`` — it stored a padded value at the first head,
#9591 review cycle 1; its CREATE sibling cannot reach a ``:Point`` at all, so it
is deliberately not gated — cycle 3),
and the journal-first seam ``EventAPI.add_point`` → ``_emit`` (cycle 1, P1:
``extractor.py`` feeds it UNTRUSTED model output). The v1 extractor's parser
canonicalises a model-produced kind before the seam sees it, because the door's
refusal would otherwise abort a whole section's extraction.

⛔ WHAT THE CLOSURE IS, exactly (do not overstate it — the first cut did): no
SDK write can mint a kind the gate cannot classify, for the whitespace/format
categories AND the measured non-rendering set (``_KIND_NON_RENDERING_CHARS`` —
the four Hangul fillers, braille blank, musical null notehead, combining grapheme
joiner, Khmer inherent vowels, Mongolian FVS and both variation-selector ranges;
Unicode files those under printable categories, cycles 2-3 P2). A code point with no such signal at all
is still a different kind, and an IMPORTED row with an out-of-family spelling is
still not demanded by the read path (the declared boundary, pinned below).

The measure of a good test here is the CLOSURE: after this change, a gated run
cannot stamp a node whose kind the gate did not demand. Rows minted by a raw
``SET`` (imported graphs) are still demanded by the read path — that arm is
covered in ``tests/test_calibration.py`` and pinned again below.
"""
from __future__ import annotations  # noqa: I001

import os
import shutil
import tempfile

import pytest
from tortoise.exceptions import CalibrationError
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    """Fresh embedded TortoiseSDK on an isolated file DB (no Docker needed)."""
    db_path = os.path.join(tempfile.mkdtemp(prefix="tt_9287_"), "test.db")
    s = TortoiseSDK(db_path)
    yield s
    s.close()
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


#: The measured spellings from #9287's body, plus the ASCII cases #9261 pinned:
#: every Unicode space separator, the control whitespace, the zero-width/format
#: marks, and a lone surrogate. Each one escaped the gate's read-path family,
#: which is exactly why the door has to be the closure.
INVISIBLE_KINDS = [
    # Unicode space separators (Zs/Zl/Zp) — the issue's measured list
    "\u2003statement\u2003",   # EM SPACE
    "\u2000statement",         # EN QUAD
    "\u2009statement",         # THIN SPACE
    "\u200astatement",         # HAIR SPACE
    "\u2028statement",         # LINE SEPARATOR
    "\u2029statement",         # PARAGRAPH SEPARATOR
    "\u205fstatement",         # MEDIUM MATHEMATICAL SPACE
    "\u3000statement",         # IDEOGRAPHIC SPACE
    "\u00a0statement",         # NBSP (in the read family, still not a spelling)
    # Cc controls
    "\u0085statement",         # NEL
    "\tstatement\t",
    "\nstatement",
    # Cf invisible format marks
    "\u200bstatement",         # ZWSP
    "\ufeffstatement",         # BOM
    "state\u200bment",         # interior ZWSP — the same escape, not padding
    "\u00adstatement",         # SOFT HYPHEN
    # plain ASCII padding: the write door's contract, measured by #9261 as
    # accepted-and-demanded; #9287 moved the repair to the door
    " statement",
    "statement ",
    # BLANK-RENDERING code points Unicode files under ordinary categories
    # (#9591 review cycle 2, P2): no category rule reaches these, and each was
    # measured accepted-and-gate-escaping before the explicit set existed.
    "\u3164statement",   # HANGUL FILLER (Lo)
    "\u115fstatement",   # HANGUL CHOSEONG FILLER (Lo)
    "\u1160statement",   # HANGUL JUNGSEONG FILLER (Lo)
    "\uffa0statement",   # HALFWIDTH HANGUL FILLER (Lo)
    "\u2800statement",   # BRAILLE PATTERN BLANK (So)
    "\ufe0fstatement",   # VARIATION SELECTOR-16 (Mn)
]


def _kinds_in_graph(sdk, pid) -> list:
    return [r[0] for r in sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.pointKind",
        params={"id": pid}).result_set]


def _point_ids(sdk) -> list:
    return [r[0] for r in sdk._get_proj().g.query(
        "MATCH (n:Point) RETURN n.id").result_set]


class TestCreatePointRefuses:
    """The create door is closed for every measured spelling."""

    @pytest.mark.parametrize("kind", INVISIBLE_KINDS)
    def test_create_point_refuses_and_writes_nothing(self, sdk, kind):
        """Refusal BEFORE any write: the caller's graph is untouched."""
        with pytest.raises(ValueError, match="cannot be part of a kind"):
            sdk.create_point(kind, f"kind={kind!r}", status="live")
        assert _point_ids(sdk) == []

    def test_the_refusal_names_the_argument_and_the_code_point(self, sdk):
        """A refusal a caller can act on: which argument, which character."""
        with pytest.raises(ValueError) as exc:
            sdk.create_point("\u2003statement\u2003", "x", status="live")
        message = str(exc.value)
        assert "create_point" in message
        assert "pointKind" in message
        assert "U+2003" in message
        assert "#9287" in message
        # …and the fix is in the message, not left to the caller to derive.
        assert "'statement'" in message

    def test_the_refusal_survives_the_props_passthrough(self, sdk):
        """The props spelling WINS over the positional kind — so it is gated.

        Measured at HEAD: ``create_point("statement", "x",
        pointKind="\\u2003statement\\u2003")`` stored the padded value, i.e. a
        caller could reach the escape without ever passing a padded positional
        kind. Both mined spellings (camel + snake) are screened.
        """
        for spelling in ("pointKind", "point_kind"):
            with pytest.raises(ValueError, match="cannot be part of a kind"):
                sdk.create_point("statement", "x", status="live",
                                 **{spelling: "\u2003statement\u2003"})
        assert _point_ids(sdk) == []

    def test_dedup_does_not_smuggle_a_padded_kind(self, sdk):
        """The dedup path forwards props to ``update_point``; both refuse.

        Nothing is created on the refusal, so a re-run cannot leave a padded
        spelling behind under a "the point already exists" branch.
        """
        with pytest.raises(ValueError, match="cannot be part of a kind"):
            sdk.create_point("\u2003statement\u2003", "x", status="live",
                             dedup=True)
        assert _point_ids(sdk) == []


class TestUpdatePointRefuses:
    """The second door: an UPDATE is a write and stored the same spelling."""

    def test_update_point_refuses_a_padded_point_kind(self, sdk):
        p = sdk.create_point("statement", "Updatable", status="live")
        with pytest.raises(ValueError, match="cannot be part of a kind"):
            sdk.update_point(p["id"], pointKind="\u2003observation\u2003")
        assert _kinds_in_graph(sdk, p["id"]) == ["statement"]

    def test_update_point_refuses_the_snake_spelling(self, sdk):
        p = sdk.create_point("statement", "Updatable", status="live")
        with pytest.raises(ValueError, match="cannot be part of a kind"):
            sdk.update_point(p["id"], point_kind="\u2003observation\u2003")
        assert _kinds_in_graph(sdk, p["id"]) == ["statement"]


class TestUpdateEntityRefuses:
    """#9591 review P1: ``update_entity`` is the OTHER update door onto a Point.

    Measured at the first head: ``update_entity(p["id"],
    pointKind='\u2003statement\u2003')`` stored the padded kind, the gate answered
    ``[]`` and a gated run stamped the node — the same escape, one verb over. It
    is tenant-reachable (``tortoise_update_entity`` splats props, and
    ``pointKind`` is not server-managed) and ``pipeline.py`` calls it as the
    generic entity write, so it must be gated, not merely documented.
    """

    @pytest.mark.parametrize("kwargs", [
        {"pointKind": "\u2003statement\u2003"},
        {"point_kind": "\u2003statement\u2003"},
        {"props": {"pointKind": "\u2003statement\u2003"}},
        {"pointKind": "statement\u200b"},
    ])
    def test_update_entity_refuses_and_writes_nothing(self, sdk, kwargs):
        p = sdk.create_point("statement", "Victim", status="live")
        with pytest.raises(ValueError, match="cannot be part of a kind"):
            sdk.update_entity(p["id"], **kwargs)
        assert _kinds_in_graph(sdk, p["id"]) == ["statement"]
        assert sdk._uncalibrated_ep_participants(None) == [p["id"]]

    def test_update_entity_still_writes_a_clean_kind(self, sdk):
        p = sdk.create_point("statement", "Victim", status="live")
        sdk.update_entity(p["id"], pointKind="observation")
        assert _kinds_in_graph(sdk, p["id"]) == ["observation"]

    def test_the_entity_CREATE_funnel_is_not_gated_and_that_is_deliberate(
            self, sdk):
        """#9591 review cycle 3: `_create_entity` cannot create a `:Point`.

        ``create_entity("Point", …)`` raises `unknown type` — the funnel only
        makes Subject/Object/Event/Document/Source — so a ``pointKind`` riding
        it is a stray property on a label the calibration gate never reads
        (``_CALIBRATION_GATED_LABELS == ("Point",)``). Gating it there refused an
        unrelated write (measured: ``create_object(..., props={'point_kind':
        padded})``), so the arm was dropped and this test pins that decision —
        if a Point label ever becomes reachable through that funnel, this reds
        and the gate must come back.
        """
        with pytest.raises(ValueError, match="unknown type"):
            sdk.create_entity("Point", "Not a Point door")
        obj = sdk.create_object(
            "Thing", props={"point_kind": "statement\u200b"})
        assert obj["id"]


class TestTheJournalSeamRefuses:
    """#9591 review P1: ``EventAPI`` persists ``pointKind`` verbatim.

    ``EventAPI`` is the write seam every pipeline uses (the v1 extractor,
    ``ingest``, ``docs_miner``, ``mining``, the CLI) and it already hosts the
    #5475 kind gate for exactly this reason. Measured at the first head:
    ``api.add_point(..., pointKind='statement\u2003')`` stored the padded kind and
    a gated run stamped it. The refusal must come BEFORE the ``log.append``
    commit point, or the record is replayed faithfully.
    """

    def _api(self, tmp_path):
        from tortoise.api import EventAPI
        from tortoise.log import EventLog
        return EventAPI(EventLog(tmp_path / "events.jsonl"),
                        initiated_by="extractor", agent_id="test")

    def test_add_point_refuses_a_padded_kind_and_writes_nothing(self, tmp_path):
        api = self._api(tmp_path)
        with pytest.raises(ValueError, match="cannot be part of a kind"):
            api.add_point("claim", {"source": "test"},
                          pointKind="statement\u2003")
        assert api.log.read_all() == []

    def test_add_point_still_writes_a_clean_kind(self, tmp_path):
        api = self._api(tmp_path)
        api.add_point("claim", {"source": "test"}, pointKind="statement")
        assert [e["type"] for e in api.log.read_all()] == ["PointAdded"]

    def test_a_projection_less_lane_is_refused_too(self, tmp_path):
        """The refusal is a pure string predicate — no graph handle needed."""
        from tortoise.api import EventAPI
        from tortoise.log import EventLog
        api = EventAPI(EventLog(tmp_path / "events.jsonl"),
                       initiated_by="extractor", agent_id="test",
                       projection=None)
        with pytest.raises(ValueError, match="cannot be part of a kind"):
            api.add_point("claim", {"source": "test"},
                          pointKind="\ufeffstatement")
        assert api.log.read_all() == []


class TestTheRefusalIsActionable:
    """#9591 review P2: the message's own suggestion must be a value the door
    ACCEPTS — ``str.strip()`` cannot remove a ``Cf`` mark, so the first cut told
    the caller to pass the identical offending value back."""

    @pytest.mark.parametrize("kind", [
        "\u2003statement\u2003",   # Zs — strip() would have worked
        "\tstatement\t",           # Cc
        "\ufeffstatement",         # Cf — strip() does NOT touch it
        "state\u200bment",          # interior Cf
        "statement\u200b",
    ])
    def test_the_suggested_fix_is_itself_accepted(self, sdk, kind):
        from tortoise.sdk import _kind_invisible_char
        with pytest.raises(ValueError) as exc:
            sdk.create_point(kind, "x", status="live")
        message = str(exc.value)
        suggested = message.rsplit("e.g. ", 1)[1].rstrip(".")
        assert suggested.startswith("'") and suggested.endswith("'")
        clean = suggested[1:-1]
        # EXACT, not merely "accepted": the first cut interpolated ``repr()``,
        # whose escaping turns a ``Cf`` mark into the six-character backslash
        # form — a DIFFERENT string the door accepts and stores under a name the
        # caller never had (`'\\ufeffstatement'` measures as accepted).
        assert clean == "statement", (
            f"the refusal suggests {clean!r}, not the caller's kind minus the "
            "offending character")
        assert _kind_invisible_char(clean) is None
        # …and the suggestion really writes.
        assert sdk.create_point(clean, "fixed", status="live")["id"]

    def test_an_all_invisible_kind_says_supply_a_name(self, sdk):
        with pytest.raises(ValueError, match="only invisible characters"):
            sdk.create_point("\u2003\u2003", "x", status="live")


class TestTheModelProducerKeepsItsOwnOutputInContract:
    """#9287: the write door refuses, so the LLM producer must clean its kind.

    A refusal at the seam would abort an entire section's extraction because a
    model emitted a stray em-space; storing it verbatim is the fail-open. The
    producer therefore canonicalises (with a warning) before the door sees it.
    """

    def test_a_padded_model_kind_is_cleaned_with_a_warning(self):
        from tortoise.sdk import _clean_model_kind
        assert _clean_model_kind("\u2003statement\u2003", door="extractor") \
            == "statement"
        assert _clean_model_kind("\ufeffstatement", door="extractor") \
            == "statement"
        assert _clean_model_kind("state\u200bment", door="extractor") \
            == "statement"

    def test_a_clean_model_kind_is_untouched(self):
        from tortoise.sdk import _clean_model_kind
        assert _clean_model_kind("statement") == "statement"
        assert _clean_model_kind("jobToBeDone") == "jobToBeDone"
        assert _clean_model_kind("technical debt") == "technical debt"

    def test_a_model_kind_with_nothing_left_falls_back(self):
        from tortoise.sdk import _clean_model_kind
        assert _clean_model_kind("\u2003", default="statement") == "statement"
        assert _clean_model_kind("", default="statement") == "statement"
        assert _clean_model_kind(None, default="statement") == "statement"

    def test_the_v1_extractor_parser_cleans_the_model_kind(self, caplog):
        """The producer half is BEHAVIOURALLY wired — a stub model, not a grep.

        A source-text check ("``_clean_model_kind(`` appears in the module")
        stays green if the cleaned value is discarded or applied to the wrong
        field, so this drives the real parser with a stub model and asserts what
        it returns (and that it SAYS what it changed).
        """
        import json
        import logging

        from tortoise.extractor import _DocumentPointStage

        class _StubModel:
            def complete(self, *, system, user):
                return json.dumps({"points": [
                    {"content": "padded", "pointKind": "\u2003statement\u2003"},
                    {"content": "zero width", "pointKind": "state\u200bment"},
                    {"content": "only padding", "pointKind": "\u2003"},
                    {"content": "clean", "pointKind": "jobToBeDone"},
                ]})

        with caplog.at_level(logging.WARNING, logger="tortoise.sdk"):
            out = _DocumentPointStage(_StubModel()).run("t", "body", "ctx")
        assert [p["pointKind"] for p in out] == [
            "statement", "statement", "statement", "jobToBeDone"], out
        # …and the repair is SURFACED, never silent: one warning per cleaned
        # value, naming it (the door's own guarantee is a refusal, so a producer
        # that quietly rewrites is the other way to hide the model's noise).
        warned = [r.message for r in caplog.records if "#9287" in r.message]
        assert len(warned) == 3, caplog.records



class TestLegitimateKindsStillWrite:
    """The refusal must not narrow the open vocabulary it guards."""

    def test_a_plain_interior_space_is_still_a_legitimate_kind(self, sdk):
        """'technical debt' is identifier-shaped prose, not invisible padding.

        The gate strips interior ASCII whitespace wherever it occurs (so
        'state ment' is demanded as the 'statement' it is); an interior plain
        space therefore cannot make a node escape, and refusing it would break
        the open-vocabulary contract (#1934 warn-not-block).
        """
        p = sdk.create_point("technical debt", "Refactor", status="live")
        assert _kinds_in_graph(sdk, p["id"]) == ["technical debt"]
        # not an evidence kind either way
        assert p["id"] not in sdk._uncalibrated_ep_participants(None)

    def test_a_mis_cased_kind_is_still_accepted_verbatim(self, sdk):
        """#9261's measured decision stands: case is a spelling, not padding.

        The read path normalises case, so the node is demanded either way —
        refusing here would be a different change (and would break the
        case-insensitive filter's reason to exist).
        """
        p = sdk.create_point("Statement", "Mis-cased", status="live")
        assert _kinds_in_graph(sdk, p["id"]) == ["Statement"]
        assert sdk._uncalibrated_ep_participants(None) == [p["id"]]

    def test_an_out_of_contract_non_string_kind_still_writes(self, sdk):
        """`create_point(42, …)` is out of contract, writable on both trees.

        The door's refusal is type-guarded (a non-string carries no characters
        to inspect) and the gate's ``toLower(typeof(…))`` guard keeps it out of
        the universe (#9063) — refusing it would be a different change.
        """
        p = sdk.create_point(42, "Numeric kind", status="live")
        assert p["id"] not in sdk._uncalibrated_ep_participants(None)


class TestTheClassIsClosed:
    """Closure, stated at its measured edge — the door, not the read path.

    What the fix guarantees: no SDK write can mint a ``pointKind`` the gate
    cannot classify — for the whitespace/format categories AND the measured
    blank-rendering set (see the module docstring's ⛔ for the exact statement;
    the first cut claimed this WITHOUT that set, and ``U+3164`` disproved it,
    #9591 review cycle 2 P2) — so the fail-open state (consumed + stamped while
    the gate answered ``[]``) is unreachable through the SDK.

    What it does NOT do, deliberately: extend the read-path family to every
    Unicode space. That was #9287's option 3 and it is refused on MEASURED cost
    (each code point is one ``replace()`` on every scanned row: +5.7 ms per
    seven characters at 2,000 Points, and the full list timed out the gate on an
    eight-node fixture). An imported row with an out-of-family spelling
    therefore still escapes — the declared boundary documented on
    ``_KIND_WHITESPACE_CHARS`` and pinned below, so it cannot drift silently.
    """

    def test_an_imported_in_family_padded_kind_is_demanded_and_never_stamped(
            self, sdk):
        """The read-path arm still covers what the door no longer accepts."""
        anchor = sdk.create_point("statement", "Anchor", credibility="gold",
                                  status="live")
        # An imported/hand-written row is the reachable path left (the SDK door
        # refuses the value, measured above).
        imported = sdk.create_point("statement", "Imported", status="live")
        sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) SET n.pointKind = '\u00a0statement\u00a0'",
            params={"id": imported["id"]})
        sdk.create_operator("IMPL", anchor["id"], [imported["id"]])

        assert sdk._uncalibrated_ep_participants(None) == [imported["id"]]
        with pytest.raises(CalibrationError, match="uncalibrated") as exc:
            sdk.compute_confidence(require_calibration=True)
        assert imported["id"] in str(exc.value)
        # The gate refused the run BEFORE it could stamp the node.
        stamped = sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) RETURN n.ep_alpha, n.ep_beta",
            params={"id": imported["id"]}).result_set
        assert stamped == [[None, None]], stamped

    def test_the_out_of_family_read_path_boundary_is_pinned(self, sdk):
        """The remaining gap, MEASURED — invert this deliberately, never silently.

        An imported row whose padding is outside the read-path family
        (``_KIND_WHITESPACE_CHARS`` = ASCII whitespace + NBSP) is still consumed
        and NOT demanded. That is #9287's declared boundary with its cost
        measurement, kept here so a future change to either half has to say so.
        """
        imported = sdk.create_point("statement", "Imported em space",
                                    status="live")
        sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) SET n.pointKind = '\u2003statement\u2003'",
            params={"id": imported["id"]})
        assert sdk._uncalibrated_ep_participants(None) == []  # KNOWN gap
        # …and the DOOR knows it is not a kind: the two halves cannot drift into
        # "nobody guards this spelling" without this test moving.
        from tortoise.sdk import _kind_invisible_char
        assert _kind_invisible_char("\u2003statement\u2003") == "\u2003"

    def test_ingest_reports_the_refusal_as_a_PHASE_1_violation(self, sdk):
        """#9287 (review cycle 4): ingest must refuse BEFORE it writes anything.

        `_check_kind` is shared by Phase 1 (`_validate_bundle`) and Phase 2 so
        that no kind rule is Phase-2-only. Without the arm added here, a bundle
        whose SECOND point carried a padded kind passed Phase 1 (zero mutations)
        and then raised from `create_point` mid-write with the FIRST item already
        committed — measured: `landed: [['clean first item', 'statement']]`.
        """
        from tortoise.exceptions import BundleValidationError
        # EVERY spelling the write door reads, at the depth it reads it: the
        # positional `kind`, a flat `pointKind`/`point_kind`, and the same names
        # nested in `props` (which WIN over the positional kind — the spelling
        # that actually reached the graph on `origin/main`). Screening only the
        # positional `kind` left this issue's own shape Phase-2-only
        # (#9591 review cycle 5).
        offenders = [
            {"kind": "\u2003statement\u2003", "content": "padded second item"},
            {"content": "padded props", "props": {"pointKind": "\u2003statement\u2003"}},
            {"content": "padded flat", "point_kind": "statement\u200b"},
        ]
        for offender in offenders:
            sdk._get_proj().g.query("MATCH (n:Point) DETACH DELETE n")
            bundle = {"points": [
                {"kind": "statement", "content": "clean first item"},
                offender,
            ]}
            violations = sdk._validate_bundle(bundle)
            assert any("cannot be part of a kind" in v["message"]
                       for v in violations), (offender, violations)
            with pytest.raises(BundleValidationError):
                sdk.ingest(bundle)
            landed = sdk._get_proj().g.query(
                "MATCH (n:Point) RETURN n.content").result_set
            assert landed == [], (offender, landed)  # zero mutations
        # …and a legitimate bundle still ingests.
        sdk._get_proj().g.query("MATCH (n:Point) DETACH DELETE n")
        out = sdk.ingest({"points": [
            {"kind": "statement", "content": "clean"},
            {"content": "props kind", "props": {"pointKind": "observation"}},
        ]})
        assert out["created"]["points"] == 2, out

    def test_the_door_accepts_no_invisible_code_point(self):
        """GENERATIVE closure over the WHOLE code space (#9591 review cycle 3).

        The first two cuts of this fix were enumerations and each missed its own
        class — a category blacklist missed ``U+3164`` (``Lo``, renders blank);
        the enumerated fix for that missed ``U+E0100``–``U+E01EF`` (variation
        selectors-17+) and ``U+1D159`` (``So``). So the property is asserted
        generatively, against an oracle that is INDEPENDENT of the door's own
        rule: a character the door accepts must be PRINTABLE (``str.isprintable``
        is False for the whole ``Cc``/``Cf``/``Cs``/``Co``/``Cn``/``Z*`` set, i.e.
        the invisible half of Unicode, which is where every escape in this issue
        lived). The blank-rendering LETTERS Unicode files under ``Lo`` are outside
        that oracle, which is exactly why they are an explicit measured list —
        and they are asserted here too.
        """
        from tortoise.sdk import _KIND_NON_RENDERING_CHARS, _kind_invisible_char
        accepted_invisible = []
        for cp in range(0x110000):
            ch = chr(cp)
            if ch == " ":
                continue  # the one documented interior exemption
            # BOTH positions: the rule is position-dependent, so an
            # interior-only widening — e.g. exempting Cf marks so ZWJ emoji
            # kinds pass — must red here too (#9591 review cycle 4).
            for probe in (f"{ch}statement", f"state{ch}ment"):
                if _kind_invisible_char(probe) is None and not ch.isprintable():
                    accepted_invisible.append(
                        (hex(cp), "interior" if probe.startswith("state")
                         else "edge"))
        assert accepted_invisible == [], (
            "the door accepts non-printable code points — the #9287 escape is "
            f"reachable through those: {accepted_invisible[:20]}")
        # …and the letter-blanks the printable oracle cannot see:
        for ch in _KIND_NON_RENDERING_CHARS:
            assert _kind_invisible_char(f"{ch}statement") is not None, (
                f"the door accepts U+{ord(ch):04X}, which renders nothing and "
                "escapes the gate's classification")
            assert _kind_invisible_char(f"state{ch}ment") is not None, (
                f"the door accepts U+{ord(ch):04X} at an INTERIOR position")

    def test_the_door_refuses_every_character_the_gate_strips(self):
        """The demand and the write door cannot drift apart (#9287).

        The door's refusal must be a SUPERSET of the read path's normalisation
        family: any spelling the gate folds into an evidence kind is a spelling
        the door must refuse, or a caller could still mint it and be gated by a
        filter that is doing the door's job at scan cost. Widening the read
        family (or narrowing it) without touching the door reds this.
        """
        from tortoise.sdk import _KIND_WHITESPACE_CHARS, _kind_invisible_char
        assert _KIND_WHITESPACE_CHARS, "the family must not be empty"
        for ch in _KIND_WHITESPACE_CHARS:
            assert _kind_invisible_char(f"{ch}statement") is not None, (
                f"U+{ord(ch):04X} is stripped by the gate's read path but the "
                "write door accepts it as padding"
            )
            if ch == " ":
                # The ONE documented exemption: a plain INTERIOR space is a
                # legitimate separator ('technical debt'), and the gate folds it
                # wherever it occurs — so 'state ment' is demanded as the
                # 'statement' it is. The door therefore does not need to refuse
                # it, and refusing it would narrow the open vocabulary.
                assert _kind_invisible_char("state ment") is None
                continue
            assert _kind_invisible_char(f"state{ch}ment") is not None, (
                f"U+{ord(ch):04X} padded INTERIORLY is folded by the gate but "
                "the write door accepts it"
            )
