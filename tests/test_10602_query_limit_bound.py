"""#10602 — `tortoise_query`'s `limit` was silently dropped on its default path.

``tortoise_query(kind=..., limit=N)`` documents (and its own body computes,
``eff_limit = limit if limit is not None else (20 if paginated else 100)``) that
a plain structural query is bounded. It was not: ``paginated`` is
``offset is not None or page is not None``, so ``limit`` ALONE fell through to
the plain path, which called ``sdk.query(kind, …)`` — and ``sdk.query`` has no
``limit`` parameter at all (``tortoise/sdk.py``: ``MATCH (n:Point) WHERE …
RETURN properties(n)``, no ``LIMIT``, no ``ORDER BY``). **The whole label came
back**, and ``eff_limit`` was used only on the tag/text/paginated branches.

Measured on the hosted graph 2026-11-05 (``graph_size`` 31,248,
``pointkinds``: statement 15,733)::

    tortoise_query({"kind":"statement","limit":3})            -> 15,417 ms, no data
    tortoise_query({"kind":"statement","limit":3})   x7        -> 10,1xx ms  -32009
    tortoise_query({"kind":"statement","offset":0,"limit":3})  ->  1,362 ms, 3 rows

The unbounded read + serialisation of 15,708 rows outlives
``mcp_auth._TRANSPORT_WAIT_BOUND_S`` (10.0), so a SINGLE agent gets
``-32009`` ``retry_after: 10`` — the issue's own failure signature — and the
advertised retry cannot recover it because the work is deterministic.

Test doctrine (Class B): every test states (1) the value whose presence makes it
fail and (2) that the fixture reaches that value.

Run with (stub-only, no database):
  TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \\
    tests/test_10602_query_limit_bound.py -q -p no:cacheprovider
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


class _StubSDK:
    """A graph with `n_points` rows and the real accessors' bounds semantics.

    `query()` deliberately behaves like the SDK's own: no limit, whole
    population, no ordering. `paginated_query()` slices. The distinction is the
    value under test — a fix that merely slices in the handler would still pay
    the unbounded read, so the assertion is on what the ACCESSOR was asked for.
    """

    def __init__(self, n_points=5, *, raises=None, n_tag_points=5):
        self.calls = []
        self.points = [{"id": f"p{i}"} for i in range(n_points)]
        self.n_tag_points = n_tag_points
        self.raises = raises

    def query(self, kind=None, **kwargs):
        self.calls.append(("query", kind, kwargs))
        return self.points

    def paginated_query(self, kind=None, skip=0, limit=20, **kwargs):
        self.calls.append(("paginated_query", kind, skip, limit, kwargs))
        if self.raises is not None:
            raise self.raises
        total = len(self.points)
        return {"results": self.points[skip:skip + limit], "total": total,
                "hasMore": skip + limit < total}

    def query_points_by_tag(self, tag, *, _record_reads=True):
        self.calls.append(("query_points_by_tag", tag, _record_reads))
        return [{"id": f"tag{i}"} for i in range(self.n_tag_points)]


@pytest.fixture(autouse=True)
def _http_transport():
    """These tests drive the tool bodies directly (no HTTP middleware).

    `_safe` fail-closes on `_transport_mode is None`, and the stdio arm further
    refuses when `TORTOISE_API_KEY` is present (a dev box that also holds a
    hosted key). The `http` arm is the one this code runs under in production
    and it delegates auth to the transport, which is exactly what a tool-body
    test wants — the stub is still what `_get_org_sdk()` resolves to.
    """
    from tortoise.mcp_auth import (  # noqa: I001
        _current_org_id, _current_org_limits, _transport_mode,
    )
    _transport_mode.set("http")
    _current_org_id.set(None)
    _current_org_limits.set(None)
    yield
    _transport_mode.set(None)
    _current_org_id.set(None)
    _current_org_limits.set(None)


@pytest.fixture
def stub_factory(monkeypatch):
    from tortoise import mcp_server

    def make(n_points=5, *, raises=None, n_tag_points=5):
        stub = _StubSDK(n_points, raises=raises, n_tag_points=n_tag_points)
        monkeypatch.setattr(mcp_server, "_get_org_sdk", lambda: stub)
        return stub

    return make


def test_limit_is_honoured_on_the_plain_structural_path(stub_factory):
    """RED before the fix: limit=2 returned all 5 rows (the whole label).

    Fails on the value `len(result) == 2`; the fixture reaches it because the
    stub holds 5 rows and a bound of 2 must select 2 of them.
    """
    from tortoise.mcp_server import tortoise_query
    stub_factory(n_points=5)
    result = tortoise_query(kind="statement", limit=2)
    assert isinstance(result, list), "the plain path must stay a plain list"
    assert len(result) == 2, f"limit ignored: asked for 2, got {len(result)} of 5"


def test_the_bound_reaches_the_accessor_not_just_the_response(stub_factory):
    """A handler-side slice would return 2 rows while STILL reading the label.

    The bound must be pushed to the accessor, so `sdk.query` (no LIMIT in its
    Cypher at all) must not be the accessor used for a bounded plain read.
    """
    from tortoise.mcp_server import tortoise_query
    stub = stub_factory(n_points=500)
    tortoise_query(kind="statement", limit=2)
    name = stub.calls[-1][0]
    assert name == "paginated_query", (
        "a bounded plain query must reach an accessor that applies LIMIT; "
        f"got {name!r} (sdk.query has no LIMIT in its Cypher)")


def test_default_plain_query_is_capped_at_the_computed_eff_limit(stub_factory):
    """`eff_limit` defaults to 100 on the plain path — and was never applied.

    RED before the fix: no `limit` returned all 500 rows. The default cap is
    the code's OWN number (`eff_limit`, 100 when not paginated), so applying it
    implements the intent rather than inventing a new one.
    """
    from tortoise.mcp_server import tortoise_query
    stub_factory(n_points=500)
    result = tortoise_query(kind="statement")
    assert isinstance(result, list)
    assert len(result) == 100, f"default cap not applied: got {len(result)} of 500"


def test_pagination_shape_is_unchanged(stub_factory):
    """offset=/page= keep returning {results,total,hasMore} — no regression."""
    from tortoise.mcp_server import tortoise_query
    stub_factory(n_points=5)
    out = tortoise_query(kind="statement", offset=0, limit=2)
    assert out == {"results": [{"id": "p0"}, {"id": "p1"}],
                   "total": 5, "hasMore": True}


def test_a_legitimately_empty_result_is_not_an_error(stub_factory):
    """The issue's implied case: finding nothing must NOT raise or read as failure.

    A bounded read over a kind with no rows is a successful answer of `[]` —
    it must not become an error, and must not be an `_SafeError`.
    """
    from tortoise.mcp_server import _SafeError, tortoise_query
    stub_factory(n_points=0)
    result = tortoise_query(kind="statement", limit=5)
    assert not isinstance(result, _SafeError), "an empty read is not a failure"
    assert result in ([], ) or (isinstance(result, dict)
                                and result.get("results") == [])


def test_a_real_failure_still_surfaces_through_the_bound(stub_factory):
    """The bound must not swallow an error that used to surface.

    `_safe` returns an `_SafeError` (a dict SUBCLASS) rather than raising, so a
    fix that does `.get("results", [])` on its return turns every database
    failure into an empty successful read — a false PASS. Gate on the type.
    """
    from tortoise.mcp_server import _SafeError, tortoise_query
    stub_factory(n_points=5, raises=RuntimeError("db down"))
    result = tortoise_query(kind="statement", limit=2)
    assert isinstance(result, _SafeError), (
        "a failed read must surface as _SafeError, not as an empty list")


def test_a_reserved_filter_key_is_a_structured_error_not_a_typeerror(stub_factory):
    """#10602 review F3 + round-6 finding 2 — a reserved name must get the documented refusal.

    `skip`/`limit` are reserved filter keys (`security.validate_filter_key`) AND
    the names the bounded path passes to `_safe(...)` itself, so
    `filters={"limit": 5}` is a duplicate keyword while BINDING that call —
    outside `_safe`'s try — and escaped the tool body as a bare TypeError
    (measured by neutralising the guard). `kind` collides the other way: it is
    `paginated_query`'s first POSITIONAL argument, so the duplicate forms inside
    `_safe` and came back as a structured error carrying the internal signature
    instead of this message. Both are guarded above the arms, so all three names
    must answer with "Reserved filter key".

    Fails on the value `isinstance(result, _SafeError)` and on the message text:
    before the guard the plain path reached `validate_filter_key` inside `_safe`
    and returned the structured error, while the bounded path crashed (`limit` /
    `skip`) or answered with the raw SDK signature (`kind`). The fixture reaches
    it because 5 > 0 points and the filter dict carries a colliding key.
    """
    from tortoise.mcp_server import _SafeError, tortoise_query
    stub_factory(n_points=5)
    for bad in ({"limit": 5}, {"skip": 3}, {"kind": "statement"}):
        result = tortoise_query(kind="statement", filters=bad)
        assert isinstance(result, _SafeError), (
            f"filters={bad} escaped as {type(result).__name__} instead of a "
            "structured refusal")
        assert "Reserved filter key" in str(result.get("error", "")), result


def test_reserved_filter_key_is_refused_on_EVERY_bounded_arm(stub_factory):
    """F3's class, not just its instance — review round 2's finding 3.

    The collision was closed on the plain arm while the `offset=`/`page=` arm
    eight lines away still raised a bare TypeError (pre-existing, but the F3
    wording claimed the class was closed). One guard above BOTH arms is what
    closes it, so this asserts every arm that forwards `filters` — for every
    reserved name the guard advertises (`skip`, `limit`, `kind`; round-6 finding 2
    showed `kind` was the one it did not check).
    """
    from tortoise.mcp_server import _SafeError, tortoise_query
    stub_factory(n_points=5)
    for bad in ({"limit": 5}, {"skip": 3}, {"kind": "statement"}):
        for kwargs in ({"kind": "statement"},
                       {"kind": "statement", "offset": 0},
                       {"kind": "statement", "page": 1}):
            result = tortoise_query(filters=bad, **kwargs)
            assert isinstance(result, _SafeError), (
                f"filters={bad} on {kwargs} escaped as "
                f"{type(result).__name__} instead of a structured refusal")
            assert "Reserved filter key" in str(result.get("error", "")), result


def test_the_served_description_discloses_the_bound():
    """#10602 review F2 — the truncation must not be SILENT to the caller.

    `docs/architecture/BOUNDED-OPERATION-CONTRACT.md` §1 forbids returning a
    silently-truncated result indistinguishable from a complete one. This arm
    still returns a bare list (no `hasMore`/`total`), so the disclosure has to
    live in the text the model actually reads: the tool's SERVED description
    (`ToolDefinition.description`, what `FunctionTool.from_function` publishes),
    not the handler docstring.

    The assertions are the POSITIVE claims, deliberately not bare substrings:
    review round 2 showed that `"bound" in desc` is satisfied by "UNBOUNDED" and
    `"offset" in desc` by a sentence saying offset does not page it, so a
    description asserting the exact opposite still passed. Each assertion here
    fails if the corresponding claim is negated or removed.
    """
    from tortoise.tool_registry import TOOL_REGISTRY

    entry = next(t for t in TOOL_REGISTRY if t.name == "tortoise_query")
    desc = entry.description
    low = desc.lower()

    # the bound is stated, and stated as a bound — not its antonym
    assert "not a full-population scan" not in low, (
        f"the description over-claims a full-scan-free read: {desc}")
    assert "unbounded" not in low, f"the description contradicts itself: {desc}"
    # the number the code actually applies — BOTH defaults, because `eff_limit` has two
    # (`20 if paginated else 100`) and the walk the same sentence recommends is the
    # paged one, so naming only 100 mis-states what a paging caller gets.
    assert "default 100" in low, f"the default cap is not named: {desc}"
    assert "20 when you page" in low, f"the paged default is not named: {desc}"
    # the way to see the rest — `hasMore` is the signal a walk terminates on
    assert "hasmore" in low, f"no terminating signal named: {desc}"
    assert "offset" in low, f"no way to page the population: {desc}"
    # The `tag=` arm: its RESPONSE is bounded, its READ is not — `query_points_by_tag`
    # has no LIMIT, so the whole tagged population is materialised and then sliced
    # (tortoise/sdk.py). That distinction is the one review round 4 found mis-stated in
    # the opposite direction (round 2 under-claimed "not capped", round 3's fix then
    # over-claimed "not a full-population scan"). These assertions pin the SENTENCE, not
    # the SDK fact it rests on: if `query_points_by_tag` ever gained a LIMIT the text
    # would keep asserting an unbounded read and nothing would red — the safe direction
    # (an under-claim), and the SDK's own Cypher is what the sentence quotes. Kept
    # explicit so a future reader does not assume more coverage than exists.
    assert "tag=" in low, f"the tag= arm is not addressed at all: {desc}"
    assert "not capped" not in low, (
        f"the description still claims one arm is uncapped: {desc}")
    assert "still scans the whole tagged population" in low, (
        f"the tag= arm's unbounded READ is not disclosed: {desc}")
    # …and the walk it recommends is QUALIFIED on the arm where it is not lossless
    # (review round 7, P2). `query_points_by_tag` orders by `createdAt` with no
    # `id(n)` tiebreak, so consecutive `offset=` windows are not adjacent ranges of
    # ONE order (#5346) and a walked tag drops and repeats rows while `hasMore`
    # still says complete. The pre-fix text recommended "walk … until hasMore is
    # false when you need every row" on that arm too, which is the #8447 §1 harm
    # recommended by the disclosure rather than merely left unmentioned. Each
    # assertion below fails if the caveat is negated or removed.
    assert "not lossless" in low, (
        f"the tag= arm's walk is advertised as lossless: {desc}")
    assert "approximate" in low, (
        f"the description does not say what a walked tag IS: {desc}")
    assert "#11062" in desc, (
        f"the caveat names no owner for the missing total order: {desc}")


def test_the_tag_alias_is_not_silently_truncated_by_the_new_bound(stub_factory):
    """Review round 4, finding 2 — a retirement alias must not start lying.

    `tortoise_query_points_by_tag` forwards to `tortoise_query(tag=...)` and its
    signature takes only `tag`, so it cannot page. Once `tortoise_query` bounded the
    un-paginated tag arm at 100, the alias would have returned an incomplete set with
    no way to see the rest — and no way to tell. It now passes an explicit limit.
    """
    from tortoise.mcp_server import _TAG_ALIAS_LIMIT, tortoise_query_points_by_tag
    assert _TAG_ALIAS_LIMIT == 10_000, (
        "the alias's cap is population-sized; no SDK limit guard runs on this path")
    stub_factory(n_points=5, n_tag_points=250)
    result = tortoise_query_points_by_tag("pricing")
    assert len(result) == 250, (
        f"the alias truncated a 250-row tag to {len(result)} with no way to page")


def test_the_retirement_docstring_agrees_with_the_constant_and_names_the_walk():
    """Review round 6, finding 1 — the two sentences that ship with the deprecation.

    On the retirement path the docstring IS the migration instruction, and it
    said two things this same PR makes false: that `_TAG_ALIAS_LIMIT` is "the
    same ceiling the SDK's own `limit` validation accepts" (it is not —
    `paginated_query` checks only `limit >= 1`, so no ceiling exists and 10,000
    is a number this file invented; the constant's own comment says the exact
    opposite), and that it "restores the pre-bound behaviour exactly"
    (it truncates above 10,000 — the SERVED description says so). Worse, its
    `Migrate to:` line pointed at the bare `tortoise_query(tag=tag)`, whose
    RESPONSE now caps at 100 with a plain list and no `hasMore`/`total`, so a
    caller who followed the printed migration silently lost the population the
    alias exists to return.

    Fails on the pre-fix docstring: the false ceiling claim is present, the
    "exactly" claim is present, and the `Migrate to:` line names neither the
    walk (`offset=`) nor its terminating signal (`hasMore`).
    """
    from tortoise.mcp_server import tortoise_query_points_by_tag

    doc = " ".join((tortoise_query_points_by_tag.__doc__ or "").split())
    assert doc, "the retiring alias lost its docstring"
    assert "the same ceiling the SDK's own" not in doc, (
        "the docstring still claims an SDK-validated ceiling that does not "
        "exist (`paginated_query` checks only limit >= 1): " + doc)
    assert "restores the pre-bound behaviour exactly" not in doc, (
        "the docstring still claims the cap is exact, while this PR's own "
        "SERVED text says it truncates above 10000 tagged rows: " + doc)
    assert "10,000" in doc or "10_000" in doc, (
        "the docstring does not state where the population-sized cap bites: " + doc)
    assert "Migrate to:" in doc, "the migration instruction is gone: " + doc
    migrate = doc.split("Migrate to:", 1)[1]
    assert "offset=" in migrate, (
        "the migration path it prints does not name the walk, so following it "
        "lands on a response capped at 100 with no way to see the rest: " + migrate)
    assert "hasMore" in migrate, (
        "the migration path names no terminating signal, so the truncation it "
        "lands on cannot be noticed: " + migrate)


def test_the_tag_arm_honours_a_lone_limit_like_the_structural_arm(stub_factory):
    """Review round 3, finding 1 — the disclosure must MATCH the branch.

    The round-2 wording said the `tag=` arm was uncapped "unless you pass
    offset=/limit=", but `limit` alone did not cap it (`paginated` keys on
    offset/page), so a caller doing exactly what the disclosure said got the
    whole tagged population — #10602's own failure class. This pins the branch
    the text now describes: 5 tagged rows, `limit=2` -> 2.
    """
    from tortoise.mcp_server import tortoise_query
    stub_factory(n_points=5, n_tag_points=5)
    result = tortoise_query(tag="pricing", limit=2)
    assert isinstance(result, list), "a lone limit= keeps the plain-list shape"
    assert len(result) == 2, f"tag arm ignored limit=: got {len(result)} of 5"
    # …and the paged form still reports the population
    paged = tortoise_query(tag="pricing", offset=0, limit=2)
    assert paged["total"] == 5 and paged["hasMore"] is True


def test_the_tag_arms_unbounded_read_and_non_total_order_are_censused():
    """Review round 4 (P2-2, second half) — the residue this PR cannot fix is RECORDED.

    The bound reached the structural arm's accessor. It could not reach the
    `tag=` arm's: `query_points_by_tag` accepts no `skip`/`limit`, so the handler
    still reads the whole tagged population and slices it — the bound is on the
    RESPONSE, not the READ. Bounding that accessor is a public SDK signature
    change and needs the owner's approval (`AGENTS.md` → *MCP/SDK Surface
    Approval*), so the contract's census is where a lane auditing #8447 must be
    able to find it. Two facts have to survive: the READ is unbounded, and the
    order the page walk depends on is not total (#5346's defect, on an accessor
    that suite does not pin).

    Non-vacuous: the census carried no `tag=` row at all before this pin, so it
    reds on the pre-fix doc rather than passing over a row it never checked.
    """
    from pathlib import Path
    doc = (Path(__file__).resolve().parent.parent / "docs" / "architecture"
           / "BOUNDED-OPERATION-CONTRACT.md").read_text(encoding="utf-8")
    rows = [ln for ln in doc.splitlines()
            if ln.startswith("| `tortoise_query(tag=…)`")]
    assert len(rows) == 1, (
        f"expected exactly ONE `tortoise_query(tag=…)` census row, found "
        f"{len(rows)} — the arm whose READ the bound never reached is not "
        "recorded, so a lane reads the tool as fixed")
    row = rows[0]
    # The READ/response distinction is the whole point of the row.
    assert "query_points_by_tag" in row, f"the row names no accessor: {row}"
    assert "**no** bound on the READ" in row, (
        f"the row does not say the read is unbounded — a lane would re-derive "
        f"the handler-side slice as a fix: {row}")
    # …and the walk it advertises is only lossless on a TOTAL order.
    assert "id(n)" in row and "#5346" in row, (
        f"the row does not record the non-total order the page walk depends "
        f"on: {row}")
    # The residue has a home, not just a complaint.
    assert "#11062" in row, f"the row cites no follow-up: {row}"
def test_every_editable_served_skill_that_reads_a_population_discloses_the_bound():
    """Review round 5 (findings P2-1 / F2) — the SHIPPED consumers, not only the tool text.

    #10602's cost does not land on the tool call, it lands on the skill an agent
    loaded: a skill that presents a bounded structural read as a complete
    enumeration makes its reader report a subset as the whole set.
    `test_the_served_description_discloses_the_bound` above guards the served
    `tools/list` text; this guards the other served artifact — the skills under
    `website/apps/dashboard/public/skills/` — because round 4 fixed the tool text
    while `analyse-for-contradictions` still said `tortoise_query(kind="decision")`
    was "the committed decisions".

    SCOPE IS "WHAT THIS TREE MAY EDIT". `config/served-skills-upstream.yml` pins
    three skills as DERIVED mirrors of the MIT skills repo; editing one in place is
    exactly the defect review round 5 reverted (P1-1), because the fix belongs
    upstream and must come back through `--sync`. So a pinned skill is out of scope
    HERE by construction — `how-to-use-tortoise`'s "Returns ALL Points" row is the
    known gap that fix has to close, tracked as a follow-up rather than edited in
    place in this repo. Every unpinned skill is in scope: its bytes live here.

    Value whose presence makes this fail: an in-scope skill that issues a
    `tortoise_query(kind…` read without the two things that make the bound usable —
    the walk (`offset=`) and the signal it terminates on (`hasMore`).
    """
    from pathlib import Path

    from tools import served_skills_parity as sp

    repo = Path(__file__).resolve().parents[1]
    served_root = repo / "website/apps/dashboard/public/skills"
    manifest = sp.load_manifest(repo / sp.MANIFEST_REL)
    pinned = {
        Path(entry["served_path"]).parent.name
        for entry in manifest["skills"].values()
    }

    checked, offenders = [], []
    for skill in sorted(served_root.iterdir()):
        doc = skill / "SKILL.md"
        if not doc.is_file() or skill.name in pinned:
            continue
        text = doc.read_text(encoding="utf-8")
        if "tortoise_query(kind" not in text:
            continue  # does not issue a structural read — nothing to disclose
        checked.append(skill.name)
        if "hasMore" not in text or "offset=" not in text:
            offenders.append(skill.name)

    assert checked, (
        "no served skill was examined — the walk found nothing, so this arm proves "
        "nothing (a rename of the served-skills directory would do it)")
    assert offenders == [], (
        "a served skill reads a bounded population but does not tell its reader so "
        f"(#10602 review P2-1): {offenders}. State the cap AND the walk — `offset=` "
        "plus `hasMore` — or the skill reports a subset as a complete set.")


def test_the_retirement_pointer_names_the_walk_it_recommends():
    """#11057 review F3 — the string a retired-tool caller is actually told to follow.

    `RETIRED_USE_INSTEAD` is SERVED, not cosmetic: `tortoise/mcp_server.py` renders it
    into the retired shim's message and description, and
    `docs/product/mcp-rename-table.md` is GENERATED from it. The pointer read
    `tortoise_query(tag=...)`, which after this PR caps its RESPONSE at the un-paginated
    default (100 rows) and answers a plain list with no `hasMore`/`total` — so following
    the printed migration landed on a truncated answer indistinguishable from a complete
    one, the exact harm the handler docstring's `Migrate to:` repudiates one layer up.

    Non-vacuous on the pre-fix map: the value named neither `offset=` nor `limit=`, and
    the generated table's two rows carried the bare form.
    """
    from pathlib import Path

    from tortoise.tool_registry import RETIRED_USE_INSTEAD

    expr = RETIRED_USE_INSTEAD["tortoise_query_points_by_tag"]
    assert "offset=" in expr and "limit=" in expr, (
        "the retirement pointer still names the bare read, so a caller who follows it "
        f"lands on a response capped at 100 that cannot be walked or told apart: {expr!r}")

    doc = (Path(__file__).resolve().parent.parent / "docs" / "product"
           / "mcp-rename-table.md").read_text(encoding="utf-8")
    # Matched by NAME, never by the migration table's row NUMBER: that index is GENERATED
    # (it is the retired list's position), so hard-coding it — this line said `| 74 |` — reds
    # on any change to the retired set instead of on a change to the pointer. #10914 moved it
    # to 75 and this pin failed for a reason unrelated to what it guards.
    rows = [ln for ln in doc.splitlines()
            if ln.startswith("|") and "`tortoise_query_points_by_tag`" in ln]
    assert len(rows) == 2, (
        f"expected the generated table's two `tortoise_query_points_by_tag` rows "
        f"(the migration table and the redirect-agreement table), found {rows!r}")
    for row in rows:
        assert f"`{expr}`" in row, (
            "a generated artifact still carries a retirement pointer the registry no "
            f"longer emits — regenerate `tools/mcp_rename_table.py`. Row: {row}")
