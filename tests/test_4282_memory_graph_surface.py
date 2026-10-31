"""#4282 phase 2.3 — the memory-graph surface: `update_memory_graph` and its siblings.

The owner approved the 40-method SDK target (`docs/product/beta-sdk-surface.md`); phase
2.3 is its memory-graph block. This slice retires **four** live names and lands **three**
targets:

    graph_list            -> list_memory_graphs      (row 31, 1:1)
    graph_restore         -> restore_memory_graph    (row 33, 1:1)
    graph_set_name        -> update_memory_graph     (row 30, the partial update)
    graph_set_recording   -> update_memory_graph     (row 30, the partial update)

The old names do not resolve — owner ruling #3836 (c): a retired SDK name FAILS and
names its replacement, with no alias layer and no warning shim.

WHAT THIS FILE IS THE GATE ON (one test per property, so a regression names itself):

  1. **The new names work.** A rename that only renames the `def` and never proves the
     behaviour is a rename with no evidence behind it.
  2. **The merge did not change behaviour.** `graph_set_name` + `graph_set_recording`
     become ONE call with a sentinel default. The dangerous half is the sentinel: with
     `recording: bool | None = None`, a caller who only wanted to RENAME would silently
     CLEAR the recording override on every call (`None` is a meaningful value here, not
     "unspecified"). `test_..._leaves_the_override_untouched` is the regression pin.
  3. **The old names are gone, and say where to go.**
  4. **No caller was left behind.** A rename that misses one call site leaves a live
     `AttributeError` in a route or tool; this scans the package for the retired tokens.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from tortoise.sdk import TortoiseSDK

ROOT = Path(__file__).resolve().parent.parent

#: The four names this slice retires, and the approved target each now resolves to.
RETIRED = {
    "graph_list": "list_memory_graphs",
    "graph_restore": "restore_memory_graph",
    "graph_set_name": "update_memory_graph",
    "graph_set_recording": "update_memory_graph",
}


@pytest.fixture
def sdk(tmp_path):
    """Registry-mode SDK on an embedded graph (no server needed)."""
    return TortoiseSDK(db_path=str(tmp_path / "test_4282_memory_graph.db"))


@pytest.fixture
def team(sdk):
    """A team to own the graphs; torn down so the registry does not accumulate."""
    result = sdk.org_create("memory-graph-surface")
    yield result


def _recording(sdk: TortoiseSDK, org_id: str, graph_id: str):
    """The raw `recording` prop of a Graph node — `None` means "no override"."""
    rows = sdk._get_registry().query(
        "MATCH (g:Graph {id:$gid, org_id:$tid}) RETURN g.recording",
        params={"gid": graph_id, "tid": org_id},
    ).result_set
    assert rows, f"no Graph node {graph_id} for {org_id}"
    return rows[0][0]


# ── 1. the new names work ───────────────────────────────────────────────────
class TestTheNewNamesWork:
    def test_list_memory_graphs_returns_the_accounts_rows(self, sdk, team):
        sdk._graph_create(team["id"], "prod", kind="custom")
        rows = sdk.list_memory_graphs(team["id"])
        assert {r["name"] for r in rows} >= {"prod"}
        # The default graph sorts first (the seam's documented ordering).
        assert rows[0]["kind"] == "default"
        for row in rows:
            assert {"graph_id", "org_id", "name", "kind", "namespace",
                    "status", "recording"} <= set(row)

    def test_update_memory_graph_renames_the_display_name_only(self, sdk, team):
        g = sdk._graph_create(team["id"], "before", kind="custom")
        assert sdk.update_memory_graph(team["id"], g["graph_id"], name="after") is True
        row = next(r for r in sdk.list_memory_graphs(team["id"])
                   if r["graph_id"] == g["graph_id"])
        assert row["name"] == "after"
        # A rename must never re-key the data plane (#2701).
        assert row["namespace"] == g["namespace"]
        assert row["kind"] == "custom"

    def test_update_memory_graph_sets_and_clears_the_recording_override(self, sdk, team):
        g = sdk._graph_create(team["id"], "prod", kind="custom")
        assert sdk.update_memory_graph(
            team["id"], g["graph_id"], recording=True) is True
        assert _recording(sdk, team["id"], g["graph_id"]) is True
        assert sdk.update_memory_graph(
            team["id"], g["graph_id"], recording=False) is True
        assert _recording(sdk, team["id"], g["graph_id"]) is False
        # `None` is the CLEAR value (inherit the org default), not "unspecified".
        assert sdk.update_memory_graph(
            team["id"], g["graph_id"], recording=None) is True
        assert _recording(sdk, team["id"], g["graph_id"]) is None

    def test_update_memory_graph_does_both_fields_in_one_call(self, sdk, team):
        g = sdk._graph_create(team["id"], "prod", kind="custom")
        assert sdk.update_memory_graph(
            team["id"], g["graph_id"], name="renamed", recording=True) is True
        row = next(r for r in sdk.list_memory_graphs(team["id"])
                   if r["graph_id"] == g["graph_id"])
        assert row["name"] == "renamed"
        assert _recording(sdk, team["id"], g["graph_id"]) is True

    def test_update_memory_graph_resolves_the_literal_default_id(self, sdk, team):
        """Both old setters accepted `graph_id="default"`; the merged one must too."""
        assert sdk.update_memory_graph(
            team["id"], "default", recording=True) is True
        default = next(r for r in sdk.list_memory_graphs(team["id"])
                       if r["kind"] == "default")
        assert _recording(sdk, team["id"], default["graph_id"]) is True

    def test_restore_memory_graph_revives_a_tombstone(self, sdk, team):
        g = sdk._graph_create(team["id"], "prod", kind="custom")
        assert sdk.graph_delete(team["id"], g["graph_id"]) is True
        assert sdk.restore_memory_graph(team["id"], g["graph_id"]) is True
        row = next(r for r in sdk.list_memory_graphs(team["id"])
                   if r["graph_id"] == g["graph_id"])
        assert row["status"] == "active"

    def test_restore_memory_graph_refuses_a_purged_graph(self, sdk, team):
        """Row 33: "Undo a recoverable delete. **Refuses on a purged graph**"."""
        g = sdk._graph_create(team["id"], "prod", kind="custom")
        assert sdk.graph_delete(team["id"], g["graph_id"]) is True
        sdk._get_registry().query(
            "MATCH (g:Graph {id:$gid, org_id:$tid}) "
            "SET g.purged_at = '2026-01-01T00:00:00+00:00'",
            params={"gid": g["graph_id"], "tid": team["id"]},
        )
        assert sdk.restore_memory_graph(team["id"], g["graph_id"]) is False


# ── 2. the sentinel: a rename must not touch the recording override ────────
class TestTheMergeDidNotChangeBehaviour:
    def test_renaming_leaves_the_override_untouched(self, sdk, team):
        """THE regression pin for the 2-setter merge.

        With `recording: bool | None = None` this test fails: the override is
        cleared by a call that never mentioned it.
        """
        g = sdk._graph_create(team["id"], "prod", kind="custom")
        assert sdk.update_memory_graph(team["id"], g["graph_id"], recording=True) is True
        assert _recording(sdk, team["id"], g["graph_id"]) is True

        assert sdk.update_memory_graph(team["id"], g["graph_id"], name="renamed") is True
        assert _recording(sdk, team["id"], g["graph_id"]) is True, (
            "a rename CLEARED the session-recording override — the merged update's "
            "`recording` default is conflating 'unspecified' with None"
        )

    def test_setting_recording_leaves_the_name_untouched(self, sdk, team):
        g = sdk._graph_create(team["id"], "prod", kind="custom")
        assert sdk.update_memory_graph(
            team["id"], g["graph_id"], recording=True) is True
        row = next(r for r in sdk.list_memory_graphs(team["id"])
                   if r["graph_id"] == g["graph_id"])
        assert row["name"] == "prod"

    def test_a_non_bool_recording_is_refused_not_stored(self, sdk, team):
        """The `_Unset` annotation is NOT the enforcement — this guard is.

        `sdk.py` is `from __future__ import annotations`, so at runtime every
        annotation is a string; the only static consumer (`mypy tortoise/`) disables
        `arg-type` repo-wide. So a `recording="false"` reaching the write would be
        stored VERBATIM, and `hosted_api._apply_graph_recording_override` resolves the
        override with `bool(override)` — `bool("false")` is `True`, i.e. session
        recording left ON for a caller who asked to disable it. Same strict-bool rule
        the REST twin declares (`GraphRecordingPatch._strict_bool`: "reject anything
        that is not an actual bool"), enforced here before any read or write.

        Pre-fix this fails: the string is stored and the call returns True.
        """
        g = sdk._graph_create(team["id"], "prod", kind="custom")
        for bad in ("false", "true", "", 1, 0, [False], {"on": False}):
            with pytest.raises(TypeError, match="STRICT bool"):
                sdk.update_memory_graph(team["id"], g["graph_id"], recording=bad)
        assert _recording(sdk, team["id"], g["graph_id"]) is None, (
            "a REFUSED `recording` still wrote the override")
        # The refusals must leave the method usable and the node untouched: a real
        # bool still writes, and a rename still does not disturb the override.
        assert sdk.update_memory_graph(
            team["id"], g["graph_id"], recording=False) is True
        assert _recording(sdk, team["id"], g["graph_id"]) is False

    def test_an_update_that_asks_for_nothing_is_refused(self, sdk, team):
        """A silent no-op that reports True is the failure mode this refuses."""
        g = sdk._graph_create(team["id"], "prod", kind="custom")
        with pytest.raises(ValueError, match="at least one field"):
            sdk.update_memory_graph(team["id"], g["graph_id"])

    def test_an_unknown_graph_reports_false_not_an_error(self, sdk, team):
        """Both old setters answered False (callers map it to 404). Preserved."""
        assert sdk.update_memory_graph(
            team["id"], "g_does_not_exist", name="x") is False
        assert sdk.update_memory_graph(
            team["id"], "g_does_not_exist", recording=True) is False

    def test_a_soft_deleted_graph_is_not_patchable(self, sdk, team):
        """Both old setters treated a tombstone as unknown — the merge must too."""
        g = sdk._graph_create(team["id"], "prod", kind="custom")
        assert sdk.graph_delete(team["id"], g["graph_id"]) is True
        assert sdk.update_memory_graph(
            team["id"], g["graph_id"], name="x") is False
        assert sdk.update_memory_graph(
            team["id"], g["graph_id"], recording=True) is False


# ── 3. the retired names are gone and say where to go ──────────────────────
class TestTheOldNamesAreGone:
    def test_a_retired_name_fails_naming_its_replacement(self):
        sdk = object.__new__(TortoiseSDK)  # no graph, no DB, no fixture
        for old, new in RETIRED.items():
            with pytest.raises(AttributeError) as exc:
                getattr(sdk, old)
            assert new in str(exc.value), (
                f"`TortoiseSDK.{old}` failed without naming `{new}`: {exc.value}"
            )

    def test_the_replacement_is_on_the_class(self):
        for new in set(RETIRED.values()):
            assert callable(getattr(TortoiseSDK, new, None)), (
                f"{new} is named as a replacement but is not on `TortoiseSDK`"
            )

    def test_no_in_repo_caller_still_uses_a_retired_name(self):
        """A missed call site is a live `AttributeError` in a route or a tool.

        The scan is deliberately WIDER than a `.name(` call-shape match, because the
        narrow version is how a real call site survived this very slice:

          * a **bound method reference** carries no `(` — `asyncio.to_thread(
            sdk.graph_restore, …)` was the actual shape in `hosted_api.py`;
          * `graph-scripts/` and `tools/` are not under `tortoise/`, and
            `graph-scripts/` did hold a live `graph_list` caller.

        So: every TRACKED Python file (no test file — this one and a sibling pin the old
        names on purpose), matching `.<name>` at a word boundary, which covers calls,
        bound references and `getattr`-free attribute reads. Two allowances, both
        explicit: `sdk_retired.py` owns the names, and a registered MCP TOOL id
        (`id="surface.graph_set_recording"`) is a tool identifier, never an SDK
        attribute — that tool is an owner-ruled survivor (2026-09-21).
        """
        out = subprocess.run(
            ["git", "ls-files", "*.py"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.split()
        pattern = re.compile(
            r"\.(" + "|".join(map(re.escape, RETIRED)) + r")\b",
        )
        offenders: list[str] = []
        for rel in out:
            if rel.startswith("tests/") or rel == "tortoise/sdk_retired.py":
                continue
            for lineno, line in enumerate(
                ROOT.joinpath(rel).read_text(encoding="utf-8").splitlines(), 1
            ):
                for m in pattern.finditer(line):
                    if f'id="surface.{m.group(1)}"' in line:
                        continue  # an MCP tool id, not an SDK attribute
                    offenders.append(f"{rel}:{lineno}: .{m.group(1)}")
        assert not offenders, (
            "a caller still references a retired SDK name — it would raise at runtime: "
            f"{offenders}"
        )


# ── 4. phase 2 does not rename the MCP surface ─────────────────────────────
class TestTheMcpSurfaceIsUntouched:
    def test_the_recording_tool_keeps_its_served_name(self):
        """Phase 3 renames the MCP surface; phase 2 must not move a served tool name.

        The SDK method `graph_set_recording` is discarded, but the MCP TOOL of that name
        is an owner-ruled survivor (2026-09-21) whose SDK method is
        `update_memory_graph` — so the tool name outlives the method it was named after.
        """
        from tortoise.tool_registry import TOOL_REGISTRY

        assert "tortoise_graph_set_recording" in {t.name for t in TOOL_REGISTRY}
