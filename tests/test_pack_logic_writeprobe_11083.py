"""#11083 — the census's `written` column, over a real graph (the graph-DB gate).

The hermetic half compares the artifact to DECLARATIONS. This half is the only
assertion that can catch a LIE ABOUT THE GRAPH: it mints one endpoint pair per
declared row on a throwaway store, invokes the row's own write path, and reads
the graph back.

  G1  the probe reproduces the committed artifact's `written` column exactly
      (`direct | mediated | none`), and the direction read back from the graph
      equals `effective_direction(row)` for every written row.
      ⛔ A probe that omitted `direction=` would write `->` for
      `venture:holds` while the census reported `<-` — a cell measuring nothing.
      That is what the direction assertion catches, and it is why `direction=`
      is not optional in the probe.
  G2  `create_operator(..., semantics="produces")` stores `direction='<-'` with
      `direction_basis='semantics'` — the FIRST table value to produce `<-`, and
      the one row a reader assuming `{->, <->, -}` would drop (R1).

**Carve-out, and why it is load-bearing.** The fixtures build
`TortoiseSDK(<tmpfile>)` and read state back off that graph, so a URI-set lane
would redirect the construction to a server and the assertions would be gone.
Mirror of the `carve_out:` entry in `config/ci-surfaces.yml` — the two
registries are one set in two homes
(`tests/test_ci_selection.py::test_carve_out_mirrors_test_no_redirect_stems`).

Run:
    TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest tests/test_pack_logic_writeprobe_11083.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tools"))

from tortoise.sdk import TortoiseSDK  # noqa: E402

ARTIFACT = REPO_ROOT / "docs" / "evidence" / "11083" / "pack-relation-census.json"


@pytest.fixture()
def events_dir(tmp_path):
    events = tmp_path / "events"
    events.mkdir()
    return events


@pytest.fixture()
def sdk(tmp_path, events_dir):
    s = TortoiseSDK(str(tmp_path / "census.db"),
                    event_log_path=str(events_dir / "events.jsonl"))
    yield s
    s.close()


def _stored(sdk, op_id: str) -> tuple[str, str]:
    row = sdk._get_proj().g.query(
        "MATCH (o:Point {id:$id}) RETURN o.direction, o.direction_basis",
        params={"id": op_id},
    ).result_set[0]
    return row[0], row[1]


# ── G1 — the `written` column, against a real graph ──────────────────────────
#
# ⛔ COST, measured rather than assumed (the scoping recorded this fallback
# before the file existed, so it is not discovered later): the probe mints one
# endpoint per declared kind — `create_entity` measures ≈1–3 s/call on an
# embedded store while a write is 0.015 s — so the FIRST graph write in this file
# pays a one-time ~124 s of store/embedding warmup and the probe itself adds
# ~2 s. Because the warmup is unavoidable for any graph assertion here, the full
# 42-row population is asserted rather than a sample: the marginal cost of the
# full probe over one row is ~2 s, and a sample would leave 37 `written` cells
# unverified. The same pass is the artifact's evidence producer:
#   uv run python tools/pack_relation_census.py --write-probe
def test_g1_write_probe_reproduces_the_committed_written_column(sdk):
    """The artifact's `written` cells are the graph's answer, not a transcription.

    ⛔ A probe that omitted `direction=` would write `->` for `venture:holds`
    while the census reported `<-` — a cell measuring nothing. That is what the
    read-back assertion catches, and it is the only assertion in this issue that
    can catch a lie ABOUT THE GRAPH (the hermetic half compares declarations).
    """
    import pack_relation_census as census

    assert ARTIFACT.exists(), f"{ARTIFACT} is not committed"
    committed = json.loads(ARTIFACT.read_text())
    fresh = census.build_census(REPO_ROOT / "packs", write_probe=False)
    rows = [r for section in fresh["packs"].values()
            for r in section["relations"] + section["hierarchies"]]

    census._sdk = lambda: sdk  # noqa: SLF001 — the probe's one injection point
    census._run_write_probe(rows, REPO_ROOT / "packs")

    committed_rows = {
        (r["pack"], r["surface"], r["predicate"], r["from_kind"], r["to_kind"]): r
        for section in committed["packs"].values()
        for r in section["relations"] + section["hierarchies"]
    }
    mismatched = []
    for row in rows:
        key = (row["pack"], row["surface"], row["predicate"],
               row["from_kind"], row["to_kind"])
        assert key in committed_rows, (
            f"{key} is declared but absent from the artifact — run "
            "`uv run python tools/pack_relation_census.py --write-probe --out "
            "docs/evidence/11083/pack-relation-census.json`")
        expected = committed_rows[key]
        if row["written"] != expected["written"]:
            mismatched.append((key, "written", expected["written"], row["written"]))
        if row["written"] == "mediated":
            if row["written_direction"] != expected["written_direction"]:
                mismatched.append((key, "written_direction",
                                   expected["written_direction"],
                                   row["written_direction"]))
            # the direction the probe WROTE must be the sense the row DECLARES
            if row["effective_direction"] is not None and \
                    row["written_direction"] != row["effective_direction"]:
                mismatched.append((key, "effective_vs_readback",
                                   row["effective_direction"],
                                   row["written_direction"]))
    assert mismatched == [], (
        "the committed `written` column is not what the graph answers:\n"
        + "\n".join(map(str, mismatched)))


def test_g1b_the_one_direction_that_proves_the_probe_reads_the_declaration(artifact=None):
    """`venture:holds` is `<-` BY DECLARATION while its meaning (`ownedBy`, via
    `holds`) is `->`. A probe that let the meaning win would record `->`.

    Asserted on the committed artifact so the cell cannot be "fixed" by changing
    the probe's default instead of the declaration.
    """
    committed = json.loads(ARTIFACT.read_text())
    depends = {(r["pack"], r["predicate"]): r
               for section in committed["packs"].values()
               for r in section["relations"]}
    holds = depends[("venture", "holds")]
    assert holds["declared_direction"] == "<-"
    assert holds["effective_direction"] == "<-", (
        "`ownedBy`'s table default is `->` — the declared per-edge sense must "
        "outrank it (#11083 ruling (c))")
    assert holds["written"] == "mediated"
    assert holds["written_direction"] == "<-", (
        "the probe wrote a different direction than the declaration it "
        "measured — that cell measures nothing")


def test_g1c_the_written_column_distinguishes_a_bridge_from_an_edge():
    """The whole reason the column is three-valued.

    `create_operator` writes `(o1)-[:INPUT]->(op)-[:IMPL]->(o2)`, never
    `(o1)-[:p]->(o2)`. A census asking only "written?" reports 41/42 and hides
    that the direct Object→Object class is 10 of 42.
    """
    committed = json.loads(ARTIFACT.read_text())
    counts = committed["written_counts"]
    assert counts["direct"] == 10, counts
    assert counts["mediated"] == 31, counts
    assert counts["none"] == 1, counts
    assert sum(v for k, v in counts.items() if not k.startswith("UNMEASURED")) == \
        committed["population"]["declared"], (
        "every declared row must have exactly one `written` cell — an "
        "UNMEASURED cell in a --write-probe artifact is a probe that skipped a row")


# ── G2 — the first `<-` table value, on the write leg ───────────────────────
def test_g2_produces_stores_the_target_to_source_direction(sdk):
    a = sdk.create_entity("object", "census-g2-asset", objectKind="widget")["node"]["id"]
    b = sdk.create_entity("object", "census-g2-program", objectKind="widget")["node"]["id"]
    op = sdk.create_operator("IMPL", a, [b], label="producedBy",
                             semantics="produces")
    direction, basis = _stored(sdk, op["id"])
    assert direction == "<-", (
        "`produces` is the FIRST table value that produces `<-`; a reader "
        "assuming {->, <->, -} drops it and the edge reads as the product "
        "causing the producer")
    assert basis == "semantics", basis


def test_g2b_the_direction_survives_a_rebuild(sdk, events_dir):
    """#7852's invariant extended to the new row: the direction is re-derived from
    the journal, not from the live node."""
    a = sdk.create_entity("object", "census-g2b-asset", objectKind="widget")["node"]["id"]
    b = sdk.create_entity("object", "census-g2b-program", objectKind="widget")["node"]["id"]
    op = sdk.create_operator("IMPL", a, [b], label="producedBy",
                             semantics="produces")
    sdk._get_proj().rebuild_all(str(events_dir), confirm_destructive=True)
    direction, _basis = _stored(sdk, op["id"])
    assert direction == "<-"
