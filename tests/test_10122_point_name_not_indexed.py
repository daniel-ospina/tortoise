"""#10122 — a Point's ``name`` is STORED but not lexically indexed (advisory pin).

THE DEFECT, measured on both lanes. The Point FULLTEXT index is created over
``content`` + ``search_keys`` only (``tortoise/projection/__init__.py``::
``_FULLTEXT_INDEX_LABEL_FIELDS``), yet the write path ACCEPTS and STORES
``name`` on a Point: ``create_point("statement", "body", name="…")`` and
``update_point(pid, name="…")`` both succeed and the node holds ``n.name``.
That point is then invisible to the lexical leg — a ``run_fts_query`` for its
own exact name returns nothing. ``Event``/``Subject``/``Object`` DO index
``name``, so a Point is the one label where an ACCEPTED write mints a silently
un-findable record: the #5482 class (a write that reported success while
leaving the record unfindable by ``queryNodes``).

THE RULING recorded on the issue: do NOT add ``name`` to the index. That is a
RANKING change — it moves collection statistics and every Point's field length
— and it is gated on the #8486 measurement, which is postponed. Option A
instead, which is what landed: a WARN-NOT-BLOCK advisory on the Point write
path (the #1934 precedent for a declaration gap — it warns, never refuses).
So this file pins four things, and the reasons differ:

* the ADVISORY fires where the identifier is stored but not findable, and the
  write STILL LANDS (warn, never block);
* NO FALSE POSITIVE on the common shape — a name also carried by ``content``
  or by ``search_keys`` IS findable, so it must not warn;
* the advisory is POINT-gated and post-write: ``update_entity`` reaches every
  label (and ``name`` is indexed on Subject/Object/Event), and a missed write
  must not cry wolf;
* the GAP itself stays VISIBLE — the name-only point is absent from the
  FTS leg, pinned with a POSITIVE CONTROL, because an absence assertion alone
  also passes on a lane with no Point FTS index at all (the #5534
  blank-instrument trap: a pin that cannot fail certifies nothing). When the
  #8486-gated index change lands, this test reds on purpose and must be
  flipped DELIBERATELY — that is the point of keeping it.

Zero ranking effect is part of the contract: the advisory writes nothing and
moves no score, so the index's fields and collection statistics are unchanged.
The helper does no graph read of its own — the one non-payload input is the
stored text the `update_point` door reads and hands over.

LANE NOTE. Module-level ``pytestmark = pytest.mark.embedded_only`` — the lane
this defect was measured on. The file is therefore routed to the URI-unset
carve-out job and registered in all three of its homes
(``config/ci-surfaces.yml`` `carve_out:`, ``tests/_embedded.py::
TEST_NO_REDIRECT_STEMS``, and the declaration pin in ``tests/test_markers.py``);
a ``core``-only registration would leave the manifest green and run the file in
no leg under a docker URI (the #8488 trap).
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.sdk import TortoiseSDK
from tortoise.search_engine import run_fts_query

pytestmark = pytest.mark.embedded_only

#: The SDK module that owns the write doors — the advisory's logger.
_SDK_LOGGER = "tortoise.sdk"
#: The issue id every advisory record carries, used to isolate advisory records
#: from the OTHER ``tortoise.sdk`` warnings a write can emit (the #3377 rename
#: deferral, for one) so a count assertion cannot be satisfied by the wrong one.
_MARK = "#10122"
#: A title shaped like the measured case: an identifier that belongs in the
#: indexed text, not only in ``name``.
_TITLE = "capstone-9664 write probe 2026-10-26"


@pytest.fixture
def sdk(tmp_path):
    s = TortoiseSDK(str(tmp_path / "10122.db"))
    try:
        yield s
    finally:
        s.close()


def _advisories(caplog) -> list[str]:
    """The #10122 advisory records emitted so far, as messages."""
    return [r.getMessage() for r in caplog.records
            if r.name == _SDK_LOGGER and _MARK in r.getMessage()]


# ── the advisory: fires, and never blocks ──────────────────────────────────

def test_create_point_with_unindexed_name_warns_and_still_lands(sdk, caplog):
    """The measured write. It must WARN (the identifier is stored and not
    findable) and it must still LAND — warn-not-block is #1934's recorded
    decision, and refusing this write would break every existing caller."""
    with caplog.at_level(logging.WARNING, logger=_SDK_LOGGER):
        created = sdk.create_point("statement", "the body says nothing else",
                                   name=_TITLE)

    advisories = _advisories(caplog)
    assert len(advisories) == 1, advisories
    message = advisories[0]
    assert "create_point" in message, message
    # It names the two places the identifier CAN go, and says why.
    assert "content" in message and "search_keys" in message, message
    assert "name" in message, message

    # WARN, NEVER BLOCK: the write landed and the node holds the name.
    point = sdk.get_point(created["id"])
    assert point.get("name") == _TITLE


def test_update_point_with_unindexed_name_warns(sdk, caplog):
    """The second door the issue measured (``update_point(pid, name=…)``)."""
    pid = sdk.create_point("statement", "the body says nothing else")["id"]
    caplog.clear()

    with caplog.at_level(logging.WARNING, logger=_SDK_LOGGER):
        sdk.update_point(pid, name=_TITLE)

    advisories = _advisories(caplog)
    assert len(advisories) == 1, advisories
    assert "update_point" in advisories[0], advisories[0]
    assert sdk.get_point(pid).get("name") == _TITLE


def test_update_entity_point_branch_warns(sdk, caplog):
    """The THIRD Point write seam. It writes with a live ``SET n += $p`` and
    does NOT pass through the projection's shared Point writer, so it needs the
    advisory at its own door — this pins that it has one."""
    pid = sdk.create_point("statement", "the body says nothing else")["id"]
    caplog.clear()

    with caplog.at_level(logging.WARNING, logger=_SDK_LOGGER):
        sdk.update_entity(pid, name=_TITLE)

    advisories = _advisories(caplog)
    assert len(advisories) == 1, advisories
    assert "update_entity" in advisories[0], advisories[0]
    assert sdk.get_point(pid).get("name") == _TITLE


# ── no false positives on the shapes that ARE findable ─────────────────────

def test_a_name_already_in_content_does_not_warn(sdk, caplog):
    """THE COMMON SHAPE. The title reaches the index through ``content``, so it
    is findable and there is nothing to advise — a warning here would be noise
    that hides a genuine one."""
    with caplog.at_level(logging.WARNING, logger=_SDK_LOGGER):
        created = sdk.create_point(
            "statement", f"the body does say {_TITLE} verbatim", name=_TITLE)

    assert _advisories(caplog) == []
    assert sdk.get_point(created["id"]).get("name") == _TITLE


def test_a_name_already_in_search_keys_does_not_warn(sdk, caplog):
    """The OTHER indexed field. ``search_keys`` is flattened to its stored
    string form BEFORE the coverage check, so a list-valued caller sees the
    same verdict as the graph does."""
    with caplog.at_level(logging.WARNING, logger=_SDK_LOGGER):
        created = sdk.create_point("statement", "the body says nothing else",
                                   name=_TITLE,
                                   search_keys=[_TITLE, "extra-alias"])

    assert _advisories(caplog) == []
    assert sdk.get_point(created["id"]).get("name") == _TITLE


def test_update_entity_on_a_non_point_does_not_warn(sdk, caplog):
    """``update_entity`` reaches EVERY label, and ``name`` IS indexed on
    Subject/Object/Event (``_FULLTEXT_INDEX_LABEL_FIELDS``). A label-blind
    advisory would warn on exactly the shape the index supports — so the check
    is POINT-gated, and this is the pin for that gate."""
    subject = sdk.create_subject("10122 subject")
    caplog.clear()

    with caplog.at_level(logging.WARNING, logger=_SDK_LOGGER):
        sdk.update_entity(subject["id"], name="10122 subject renamed")

    assert _advisories(caplog) == []


def test_update_entity_miss_does_not_warn(sdk, caplog):
    """A write that matched nothing stored no name — ``update_entity``'s own
    rule for its rename warning ("must not cry wolf"), applied to this one."""
    with caplog.at_level(logging.WARNING, logger=_SDK_LOGGER):
        sdk.update_entity("no-such-id-10122", name=_TITLE)

    assert _advisories(caplog) == []


# ── the defect itself, kept visible (with a positive control) ──────────────

def test_the_name_only_point_is_invisible_to_the_fts_leg(sdk):
    """THE GAP, unchanged by the advisory: ``name`` is stored, and a lexical
    query for it does NOT find the point.

    The POSITIVE CONTROL is load-bearing, not decoration. Asserting only the
    absence would also pass on a lane where no Point FTS index exists at all
    (#5534's blank instrument), certifying nothing — so the same title is
    written into ``content`` on a sibling point and MUST be found.
    """
    name_only = sdk.create_point("statement", "the body says nothing else",
                                 name=_TITLE)
    in_content = sdk.create_point("statement",
                                  f"the body carries {_TITLE} verbatim")

    hits = [pid for pid, _score in run_fts_query(
        sdk._get_proj().g, _TITLE, entity_type="point", limit=20)]

    # POSITIVE CONTROL: the index works, so the absence below means something.
    assert in_content["id"] in hits, (
        "the Point FTS leg found nothing even for a title carried by "
        "`content` — the index is absent/broken on this lane, so the "
        "negative half of this test would be vacuous")
    # THE DEFECT: the same title supplied only as `name` is not findable.
    assert name_only["id"] not in hits, (
        f"a Point named {_TITLE!r} is now findable by its OWN name — `name` "
        f"has been "
        "added to the Point fulltext index. That reverses the #10122 ruling "
        "(option A: a warn-not-block advisory, zero ranking effect) and needs "
        f"the #8486 measurement released first: flip this assertion "
        f"DELIBERATELY, together with the index change, not to make this "
        f"green. title={_TITLE!r}")


# ── review round 1: the token-level condition, and the two crying-wolf shapes ──

def test_scattered_name_tokens_in_content_do_not_warn(sdk, caplog):
    """A name whose tokens are SCATTERED through content is findable — SILENCE.

    #10122 review P2: the lexical leg answers with an OR-UNION of the query's
    tokens, so this shape is retrievable (measured: returned at score 1.33).
    The first implementation compared SUBSTRINGS, warned here, and its message
    asserted an absence that did not hold. The condition is DISJOINT token
    sets, nothing weaker.

    FAIL VALUE: revert the coverage test to a substring/whole-name comparison
    and this reds with the advisory present.
    """
    with caplog.at_level(logging.WARNING):
        created = sdk.create_point(
            "statement",
            "the probe was a write for capstone and 9664 rows, all four tokens",
            name="capstone-9664 write probe",
        )
    assert created.get("id"), "FIXTURE NOT REACHED: the point was not created"
    assert not _advisories(caplog), (
        "the name's tokens all appear in content, so a query for the name CAN "
        f"reach this point — no advisory belongs here: {[r.message for r in caplog.records]}")


def test_a_partial_token_overlap_does_not_warn(sdk, caplog):
    """Even ONE shared token is enough to reach the record — SILENCE.

    The advisory promises a MISS; anything short of disjoint sets only costs
    rank, which is not what it may claim. Same mutation as above.
    """
    with caplog.at_level(logging.WARNING):
        created = sdk.create_point(
            "statement", "the probe was a write for capstone",
            name="capstone-9664 write probe")
    assert created.get("id"), "FIXTURE NOT REACHED: the point was not created"
    assert not _advisories(caplog), (
        "one of the name's tokens IS in content, so the record is reachable: "
        f"{[r.message for r in caplog.records]}")


def test_update_point_miss_does_not_warn(sdk, caplog):
    """An update that matches nothing writes nothing, so it must not advise.

    #10122 review P3: `update_point("no-such-id", name=…)` returned `{}` and
    still warned, while `update_entity` gates its own door on the match.

    FAIL VALUE: drop the existence probe before the advisory and this reds.
    """
    with caplog.at_level(logging.WARNING):
        out = sdk.update_point("no-such-point-10122", name="capstone-9664 x")
    assert out == {}, f"FIXTURE NOT REACHED: expected a miss, got {out}"
    assert not _advisories(caplog), (
        "a miss writes nothing, so there is nothing to advise: "
        f"{[r.message for r in caplog.records]}")


def test_the_point_index_field_set_is_pinned():
    """#10122 Indicator 2: pin WHICH fields the Point index holds.

    Without this, adding any other field (a `title`) would red nothing, and
    adding `name` would only red the behavioural pin — this states the set the
    ruling depends on, so a future change has to face it explicitly.
    """
    from tortoise.projection import _FULLTEXT_INDEX_LABEL_FIELDS

    assert dict(_FULLTEXT_INDEX_LABEL_FIELDS)["Point"] == ["content", "search_keys"], (
        "the Point fulltext field set changed — the #10122 ruling (option A: "
        "warn, do NOT index `name`) and the #8486 measurement gate both rest "
        "on this exact set")


def test_a_name_only_update_silenced_by_stored_content(sdk, caplog):
    """A name-only UPDATE must read the STORED text, not just its own payload.

    #10122 round-2 review P2: an update that carries only `name` supplies no
    indexed text at all, so a payload-only comparison fired on EVERY such
    update — while the sentence claimed something about the index it had not
    looked at (measured: stored content "the body mentions capstone heavily",
    `update_point(pid, name="capstone-9664")` warned, and the sentence was
    false). The door now reads the stored content + search_keys.

    FAIL VALUE: drop `stored_text=` from the update door and this reds.
    """
    created = sdk.create_point(
        "statement", "the body mentions capstone heavily, and 9664 too")
    pid = created["id"]
    with caplog.at_level(logging.WARNING):
        sdk.update_point(pid, name="capstone-9664")
    assert not _advisories(caplog), (
        "the STORED content already carries the name's tokens, so the record is "
        f"reachable by its name — no advisory belongs here: "
        f"{[r.message for r in caplog.records]}")


def test_a_name_only_update_on_a_point_without_its_tokens_still_warns(sdk, caplog):
    """The other direction: stored text that does NOT carry the name still warns.

    Without this, "always silent on an update" would pass the test above.

    FAIL VALUE: silence unconditionally on the update door and this reds.
    """
    created = sdk.create_point("statement", "an unrelated body about curtains")
    pid = created["id"]
    with caplog.at_level(logging.WARNING):
        sdk.update_point(pid, name="capstone-9664")
    assert len(_advisories(caplog)) == 1, (
        "the stored text carries none of the name's tokens, so the advisory is "
        f"correct here: {[r.message for r in caplog.records]}")


def test_an_identifier_shaped_name_with_no_tokens_still_warns(sdk, caplog):
    """`R&D`-shaped names ARE findable via the raw-literal fallback — so warn.

    #10122 round-3 review P2: the previous round silenced a name whose token
    list came out empty, on the reasoning that the query would "tokenize to
    nothing". Measured false: `build_or_query` falls back to the ESCAPED RAW
    literal (#1791), so `build_or_query("R&D") == "R&D"`, and a point whose
    content says "the brand R&D handles the audit" IS returned by
    `run_fts_query("R&D")` at 1.0. The advice would have been actionable and
    correct, so the advisory must speak.

    FAIL VALUE: restore the `if not name_tokens: return` guard and this reds.
    """
    with caplog.at_level(logging.WARNING):
        created = sdk.create_point("statement", "an unrelated body about curtains",
                                   name="R&D")
    assert created.get("id"), "FIXTURE NOT REACHED: the point was not created"
    assert len(_advisories(caplog)) == 1, (
        "an R&D-shaped name contributes no token to OUR list but the engine "
        "still matches the escaped raw literal, so the advisory is correct: "
        f"{[r.message for r in caplog.records]}")


def test_a_refused_update_does_not_warn(sdk, caplog):
    """The advisory must sit after EVERY refusal this door can raise from.

    #10122 round-3 review P3: a name-carrying update that then fails
    `validate_span` / the validity window / the draft→live status guard wrote
    nothing, yet advised — the crying-wolf class this whole change exists to
    avoid.

    FAIL VALUE: move the advisory back above the status guard and this reds.
    """
    created = sdk.create_point("statement", "an unrelated body about curtains")
    pid = created["id"]
    with caplog.at_level(logging.WARNING), pytest.raises(ValueError):
        sdk.update_point(pid, name="capstone-9664", status="retracted")
    assert not _advisories(caplog), (
        "the update was REFUSED, so nothing was written and there is nothing "
        f"to advise: {[r.message for r in caplog.records]}")


def test_an_update_that_replaces_the_covering_content_still_warns(sdk, caplog):
    """Per-FIELD: stored text counts only where the payload does not set it.

    #10122 round-4 review P2: with the stored text UNIONED in, this update was
    silent — although the name's tokens lived only in the content being
    overwritten, so after the write the name is exactly as unreachable as the
    issue describes. Measured before the fix: stored content "the capstone
    audit body mentions 9664", then
    `update_point(pid, name="capstone-9664", content="unrelated curtains")` →
    silent, with the tokens gone.

    FAIL VALUE: union the stored text unconditionally (the previous version)
    and this reds.
    """
    created = sdk.create_point(
        "statement", "the capstone audit body mentions 9664")
    pid = created["id"]
    with caplog.at_level(logging.WARNING):
        sdk.update_point(pid, name="capstone-9664",
                         content="unrelated curtains here")
    assert len(_advisories(caplog)) == 1, (
        "the update REPLACED the only text that carried the name's tokens, so "
        "after this write the name is unreachable — the advisory is correct "
        f"here: {[r.message for r in caplog.records]}")


def test_a_search_keys_replacement_also_counts_per_field(sdk, caplog):
    """The same rule for the other indexed field.

    FAIL VALUE: as above, unioned stored text reds this.
    """
    created = sdk.create_point("statement", "an audit body",
                               search_keys="capstone 9664")
    pid = created["id"]
    with caplog.at_level(logging.WARNING):
        sdk.update_point(pid, name="capstone-9664",
                         search_keys="unrelated alias")
    assert len(_advisories(caplog)) == 1, (
        "the replacement dropped the only aliases that carried the name: "
        f"{[r.message for r in caplog.records]}")
