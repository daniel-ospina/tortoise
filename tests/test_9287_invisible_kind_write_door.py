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

Two doors carry a caller-supplied ``pointKind``, and both are gated:
``create_point`` (positional kind AND the props passthrough, which WINS over the
positional) and ``update_point`` (measured: it stored a padded value too).

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
        with pytest.raises(ValueError, match="pointKind must be an identifier"):
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
            with pytest.raises(ValueError, match="pointKind must be an identifier"):
                sdk.create_point("statement", "x", status="live",
                                 **{spelling: "\u2003statement\u2003"})
        assert _point_ids(sdk) == []

    def test_dedup_does_not_smuggle_a_padded_kind(self, sdk):
        """The dedup path forwards props to ``update_point``; both refuse.

        Nothing is created on the refusal, so a re-run cannot leave a padded
        spelling behind under a "the point already exists" branch.
        """
        with pytest.raises(ValueError, match="pointKind must be an identifier"):
            sdk.create_point("\u2003statement\u2003", "x", status="live",
                             dedup=True)
        assert _point_ids(sdk) == []


class TestUpdatePointRefuses:
    """The second door: an UPDATE is a write and stored the same spelling."""

    def test_update_point_refuses_a_padded_point_kind(self, sdk):
        p = sdk.create_point("statement", "Updatable", status="live")
        with pytest.raises(ValueError, match="pointKind must be an identifier"):
            sdk.update_point(p["id"], pointKind="\u2003observation\u2003")
        assert _kinds_in_graph(sdk, p["id"]) == ["statement"]

    def test_update_point_refuses_the_snake_spelling(self, sdk):
        p = sdk.create_point("statement", "Updatable", status="live")
        with pytest.raises(ValueError, match="pointKind must be an identifier"):
            sdk.update_point(p["id"], point_kind="\u2003observation\u2003")
        assert _kinds_in_graph(sdk, p["id"]) == ["statement"]


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
    cannot classify, so the fail-open state (consumed + stamped while the gate
    answered ``[]``) is unreachable through the SDK.

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
