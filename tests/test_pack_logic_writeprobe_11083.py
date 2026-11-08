"""#11083 — the census's `written` column, over a real graph (the graph-DB gate).

The hermetic half compares the artifact to DECLARATIONS. This half is the only
assertion that can catch a LIE ABOUT THE GRAPH: it mints endpoints on a throwaway
store, invokes the row's own write path, and reads the graph back.

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
def test_g1_write_probe_reproduces_the_committed_written_column(sdk, monkeypatch):
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

    # `_sdk` is the probe's ONE injection point. `monkeypatch.setattr` (not a
    # plain assignment) because the module global is process-wide and the `sdk`
    # fixture CLOSES its SDK at teardown: a leaked `_sdk` would hand the next
    # reader in this process a closed store. `RUF100` reds an unused `noqa`, so
    # no directive is carried here — `SLF001` is not an enabled rule.
    monkeypatch.setattr(census, "_sdk", lambda uri=None: sdk)
    probe = census._run_write_probe(rows, REPO_ROOT / "packs", None)

    # ⛔ The artifact's `probe` block is a MEASURED cell, so it is compared to the
    # probe that just ran — nothing else re-derives it: `_diff_hermetic` cannot
    # (a hermetic build has `probe: None`), and without this assertion an edited
    # `minted`/`deleted` pair left the whole suite AND `--check` green (measured:
    # `minted: 71 / deleted: 71` passed 27 + 8 tests and `--check`).
    assert probe["minted"] == committed["probe"]["minted"], (
        f"the artifact claims {committed['probe']['minted']} mints; this probe "
        f"minted {probe['minted']} — the `probe` cell is a transcription, not a "
        "measurement")
    assert probe["deleted"] == probe["minted"], (
        f"{probe['minted'] - probe['deleted']} minted node(s) survived the "
        f"cleanup: {probe['leftovers']}")
    assert probe["leftovers"] == committed["probe"]["leftovers"] == [], (
        f"a residue was reported ({probe['leftovers']}) — the artifact must carry "
        "the same reading, and an empty list is the only clean one")

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
        # `written_direction` for BOTH shapes: `mediated` carries the operator's
        # direction, `direct` carries the explicit "no bridge to carry one" token.
        # Comparing only the mediated half left the direct half unread, and an
        # empty cell there is indistinguishable from "never measured" (H13b).
        if row["written"] in ("mediated", "direct") and \
                row["written_direction"] != expected["written_direction"]:
            mismatched.append((key, "written_direction",
                               expected["written_direction"],
                               row["written_direction"]))
        # the direction the probe WROTE must be the sense the row DECLARES
        if row["written"] == "mediated" and \
                row["effective_direction"] is not None and \
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
    that the direct Object→Object class is 8 of 42 — 2 relations and 6
    hierarchies (the `pm` rows that keep their Domain words ride the operator
    path, so they read `mediated`, not `direct`).
    """
    committed = json.loads(ARTIFACT.read_text())
    counts = committed["written_counts"]
    assert counts["direct"] == 8, counts
    assert counts["mediated"] == 33, counts
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


# ── G3 — a same-kind row is measured between TWO nodes ───────────────────────
_PROBE_RESIDUE_Q = (
    "MATCH (n) WHERE (n.name IS NOT NULL AND n.name STARTS WITH 'census-probe-')"
    " OR (n:Point AND n.is_operator = true) RETURN count(n)"
)


def _probe_residue(sdk) -> int:
    """Nodes the PROBE could have minted — not the SDK's own bootstrap metadata.

    ⛔ A whole-graph node count cannot answer this question across a close/reopen:
    the SDK's `Meta`/`GraphEventMeta`/`GraphEvent`/`EpMeta` bootstrap nodes appear
    as the store is opened (MEASURED: 1 before, 4 after, with zero probe nodes
    left). The question is narrower than "are there nodes", and a count that wide
    would red on the runtime's own bookkeeping while missing nothing.
    """
    return sdk._get_proj().g.query(_PROBE_RESIDUE_Q).result_set[0][0]


def test_g3_a_same_kind_row_gets_a_second_node_not_a_self_loop(sdk):
    """`pm:blocks` is `pm:card → pm:card`, and the endpoint cache is per KIND.

    With one cached node the probe handed the SAME id to both sides, so the cell
    measured a `(card)-->(card)` SELF-LOOP — a real edge, but not the declared
    connection — and it made the `written` cell a false-positive pathway: the
    read-back matches `(a {id:$s})-[r]->(b {id:$d})` with `s == d`, so a writer
    that refused a genuine two-card edge would still read `direct`.
    """
    import pack_relation_census as census

    cache: dict[str, str] = {}
    minted: list[str] = []  # insertion-ordered: `_delete_minted` relies on MINT order
    src = census._mint_endpoint(sdk, "pm:card", cache, minted)
    dst = census._mint_endpoint(sdk, "pm:card", cache, minted, slot="dst")
    assert src != dst, (
        "the destination side resolved to the SAME node — a same-kind row is "
        "being measured as a self-loop")
    # …while the shared path still REUSES: that is the cost bound the per-kind
    # cache exists for (create_entity ≈1 s/call, a write ≈0.015 s).
    assert census._mint_endpoint(sdk, "pm:card", cache, minted) == src
    assert len(minted) == 2


def test_g3b_the_endpoint_cache_is_keyed_on_the_NAMESPACED_kind(sdk):
    """`dev:issue` and `pm:issue` are different declared kinds that share a label.

    The cache was keyed on the BARE label, so both resolved to one node and a row
    declaring `dev:issue → pm:issue` read back with `s == d` — the exact self-loop
    false-positive `slot=` closes for `pm:card → pm:card`, one namespace over: a
    writer that refused a genuine two-issue edge would still read `direct`.
    """
    import pack_relation_census as census

    cache: dict[str, str] = {}
    minted: list[str] = []
    dev = census._mint_endpoint(sdk, "dev:widget", cache, minted)
    pm = census._mint_endpoint(sdk, "pm:widget", cache, minted)
    assert dev != pm, (
        "two namespaces sharing a kind LABEL collapsed onto ONE node — a row "
        "declaring a cross-namespace edge would be measured as a self-loop")
    assert len(minted) == 2
    # …while the same namespaced kind still REUSES its node: the per-kind cache is
    # the cost bound this probe exists behind (create_entity ≈1 s/call).
    assert census._mint_endpoint(sdk, "dev:widget", cache, minted) == dev
    assert len(minted) == 2


def test_g4_the_probe_deletes_what_it_mints(sdk, monkeypatch):
    """Two docstrings claimed `--write-probe` deletes what it mints. It did not.

    There was no `delete` anywhere in the census, `_sdk` honoured
    `TORTOISE_DB_URI`, and the README's regeneration command is the documented
    path — so the probe wrote entities and operator Points into whatever graph the
    environment named (in the fleet: the SHARED docker container, #5084's residue
    class) while the file's two contracts said it cleaned up and never touched a
    real graph. This asserts the cleanup on the store, closing and re-opening it
    so a residue that only lives in the cache cannot hide.
    """
    import pack_relation_census as census

    before = _probe_residue(sdk)
    assert before == 0
    row = census._relation_row("pm", {
        "predicate": "blocks",
        "mechanism": "IMPL",
        "semantics": "dependsOn",
        "direction": "<-",
        "fromKind": "pm:card",
        "toKind": "pm:card",
    })
    # Restored by `monkeypatch`, not left for the process: the `sdk` fixture
    # closes its SDK at teardown, so a leaked module global would make every
    # later test in this process read a closed store (H10's `_sdk` spy included).
    monkeypatch.setattr(census, "_sdk", lambda uri=None: sdk)
    # ⛔ Observe the ids the PROBE actually passed: `_mint_endpoint` alone cannot
    # answer this, because the defect would be the CALLER not asking for the
    # second node (measured: a `_mint_endpoint`-only test stayed green with the
    # caller's `slot=` removed).
    seen: dict[str, tuple[str, str]] = {}
    real_read_back = census._read_back

    def _spy(s, src, dst, predicate):
        seen["ids"] = (src, dst)
        return real_read_back(s, src, dst, predicate)

    census._read_back = _spy
    try:
        census._run_write_probe([row], REPO_ROOT / "packs", None)
    finally:
        census._read_back = real_read_back
    # The row is `blocks`, which is NOT in the structural writer's set, so its own
    # write path is the operator — and the ruled `<-` must be what is stored.
    assert row["written"] == "mediated"
    assert row["written_direction"] == "<-", (
        "the probe wrote a different sense than the declaration it measured")
    assert seen["ids"][0] != seen["ids"][1], (
        "the probe measured `pm:card → pm:card` as a SELF-LOOP — one node on both "
        "sides, which is not the declared connection and reads `direct` even if "
        "the writer refused a real two-card edge")

    # The probe mints 2 entities + 1 operator for this row; none may survive.
    assert _probe_residue(sdk) == 0, (
        "the probe left the nodes it minted in the store — the cleanup its "
        "docstrings promise did not happen")
