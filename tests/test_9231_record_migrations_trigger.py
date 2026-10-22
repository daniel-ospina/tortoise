"""The record schema ships in the images, and the two build workflows agree (#9325).

`tortoise.record_migrations` is a MAPPED package: `pyproject.toml`
`[tool.setuptools.package-dir]` points it at `supabase/migrations`, so an editable
install inside a build context that does not contain that tree fails outright —
`error: package directory 'supabase/migrations' does not exist` — and the image
never builds. Both Dockerfiles therefore COPY the tree, pinned for each of them by
`test_each_image_copies_the_record_migrations_tree`, which also pins WHERE: the
COPY has to precede the editable install it feeds, or the install cannot see it.

The tree is COPY'd, but it is NOT a deploy or publish trigger, and the two
workflows must give that answer together
(`test_the_two_workflows_answer_the_migrations_tree_the_same_way`): they build the
two images from one identical COPY line, so an answer that differs between them
means one of them redeploys on a change it cannot observe — and the answer has to
follow the measured gate, not merely agree.

Why "not a trigger" is the answer, and not merely a preference — the reader is
NAME-GATED (`test_the_record_reader_is_name_gated` below measures it):

  * adding a `.sql` file does not change anything the image reads, because
    `_read_migrations()` iterates `RECORD_MIGRATIONS`, never the directory;
  * editing a file it DOES read is rejected by `check-migration-append-only
    diff` (migrations are append-only). That check runs on every PR touching a
    migration (`.github/workflows/ci.yml`, job `migration-append-only`) and REDS
    it, but it is not one of `main`'s required contexts, so this half is
    enforced by review rather than by branch protection. A legitimate schema
    change arrives as a NEW name in `RECORD_MIGRATIONS` — i.e. in
    `tortoise/record_projection.py`, which `tortoise/**` already triggers.

Listing the tree as a trigger would therefore add no coverage, while costing a RED
push run per migrations-only merge: `supabase-deploy` runs its deploys on
`workflow_dispatch` only, and `deploy-hosted`'s fail-closed `Check migration
drift` exits 1 for every repo-ahead BLOCKING migration until an operator applies
it.

If a future change makes the tree genuinely observable at runtime (a directory
glob, a migration applier in the image), the polarity below flips: list it in
BOTH workflows and delete the name-gate proof with it. The two are the argument
and the answer — changing one alone is the defect these tests exist for.
"""
from __future__ import annotations

import re
import shutil
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import record_projection
from tortoise.record_projection import (
    RECORD_MIGRATIONS,
    _read_migrations,
    table_columns,
)

_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOWS = _ROOT / ".github" / "workflows"
_HOSTED = _WORKFLOWS / "deploy-hosted.yml"
_SELFHOST = _WORKFLOWS / "publish-selfhost.yml"
_DOCKERFILES = (_ROOT / "Dockerfile.hosted", _ROOT / "Dockerfile.selfhost")
_SHIPPED = _ROOT / "supabase" / "migrations"

#: A migration that is NOT in `RECORD_MIGRATIONS` but would visibly change the
#: record if the directory (rather than the tuple) were what the reader reads.
_EXTRA_NAME = "20300101000000_record_added_column_after_the_fact.sql"
_EXTRA_SQL = "ALTER TABLE public.point ADD COLUMN IF NOT EXISTS smuggled_column text;\n"
_SMUGGLED = "smuggled_column"


def _push_paths(workflow: Path) -> list[str]:
    """The `on.push.paths` trigger list.

    PyYAML reads a bare `on:` key as the boolean ``True`` (YAML 1.1), so both
    spellings are consulted — reading only ``"on"`` would silently return an
    empty list and make these assertions vacuous.
    """
    doc = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    on = doc.get("on")
    if on is None:
        on = doc.get(True)
    assert isinstance(on, dict), f"{workflow.name}: no `on:` mapping"
    paths = on["push"]["paths"]
    assert paths, f"{workflow.name}: empty `on.push.paths` — the trigger list is load-bearing"
    return list(paths)


def _is_migrations_tree_entry(entry: str) -> bool:
    """Does one `paths:` entry cover `supabase/migrations`?

    Spelling-tolerant on purpose: `supabase/migrations`, `supabase/migrations/`,
    `supabase/migrations/**` and `supabase/migrations/**/*.sql` all select the
    same tree, so an assertion that only recognised one spelling would go green
    on a reworded entry that still triggers — or red on one that does not.
    """
    text = str(entry).strip().strip("'\"")
    return text.split("*")[0].rstrip("/") == "supabase/migrations"


def _synthetic_tree(tree: Path, *, extra: bool = True) -> Path:
    """A copy of the real migrations, plus (optionally) one unlisted migration."""
    tree.mkdir()
    for name in RECORD_MIGRATIONS:
        shutil.copy(_SHIPPED / name, tree / name)
    if extra:
        (tree / _EXTRA_NAME).write_text(_EXTRA_SQL, encoding="utf-8")
    return tree


def _reader_observes_unlisted_migration(tmp_path: Path) -> bool:
    """MEASURED: can a file absent from `RECORD_MIGRATIONS` change the schema?

    This is the question the whole exclusion rests on, so it is executed rather
    than asserted in prose: build the tree, add a migration that would add a
    column, and see whether the parsed `public.point` gains it.
    """
    tree = _synthetic_tree(tmp_path / "migrations")
    return _SMUGGLED in table_columns(tree)["point"]


@pytest.mark.parametrize("dockerfile", _DOCKERFILES, ids=lambda p: p.name)
def test_each_image_copies_the_record_migrations_tree(dockerfile: Path):
    """The mapped package directory must exist in the build context (#9325).

    Without this COPY the editable install cannot resolve the package and the
    image never builds — in BOTH images, which is why the COPY is asserted for
    each rather than for `Dockerfile.hosted` alone.

    Presence alone is not the invariant: setuptools validates `package-dir` when
    the editable install RUNS, so a COPY placed after it is the #9325 red
    unchanged. The COPY is therefore pinned to precede the install it feeds.
    """
    lines = [line.strip() for line in dockerfile.read_text(encoding="utf-8").splitlines()]
    copies = [i for i, line in enumerate(lines) if line.startswith("COPY ") and "supabase/migrations" in line]
    assert copies, f"{dockerfile.name} does not COPY supabase/migrations/ into the build context"
    installs = [i for i, line in enumerate(lines) if "pip install" in line and "-e ." in line]
    assert installs, (
        f"{dockerfile.name}: no `pip install -e .` line found — this pin cannot say whether "
        "the COPY precedes the install it feeds, so it must not pass silently"
    )
    assert copies[0] < installs[0], (
        f"{dockerfile.name}: the `supabase/migrations` COPY (line {copies[0] + 1}) comes AFTER "
        f"the editable install (line {installs[0] + 1}), so setuptools still validates "
        "`package-dir` against a build context without the tree — the #9325 failure"
    )


def test_the_two_workflows_answer_the_migrations_tree_the_same_way(tmp_path: Path):
    """The two workflows agree on the trigger answer, and agree with the gate.

    Both images COPY the same tree, so a trigger answer that differs between the
    two workflows means one of them redeploys (or fails to) on a change it cannot
    observe. Agreement is not enough on its own — listing the tree in BOTH would
    also be symmetric — so the answer is checked against the gate
    `_reader_observes_unlisted_migration` measures: the tree is listed as a
    trigger exactly when the reader can observe a file it was not told about.

    Neither lists it today: the reader is name-gated, and the reason is recorded
    on the exclusion in each workflow's `paths:` block.
    """
    in_hosted = any(_is_migrations_tree_entry(p) for p in _push_paths(_HOSTED))
    in_selfhost = any(_is_migrations_tree_entry(p) for p in _push_paths(_SELFHOST))
    assert in_hosted == in_selfhost, (
        f"supabase/migrations is a deploy/publish trigger in "
        f"{'deploy-hosted' if in_hosted else 'publish-selfhost'} but not in the other. "
        "Both images COPY that tree, so both workflows must give the same answer — "
        "either list it in both (and accept the red drift run per migrations-only merge) "
        "or in neither, with the reason recorded on the exclusion."
    )
    gated = not _reader_observes_unlisted_migration(tmp_path)
    assert in_hosted != gated, (
        "the workflows and the reader disagree: the record reader "
        f"{'IS name-gated (an unlisted migration contributes nothing)' if gated else 'observes the directory'}, "
        f"yet `supabase/migrations` is {'listed' if in_hosted else 'not listed'} as a trigger in "
        "both. List it in BOTH when the reader can observe the tree, in NEITHER when it "
        "cannot — and update this module's docstring with the change."
    )


def test_the_record_reader_is_name_gated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A migration outside `RECORD_MIGRATIONS` cannot change what the image reads.

    This is the evidence under the "not a trigger" answer: the reader iterates
    its hardcoded tuple, so a newly added `.sql` file is not merely unread — it
    cannot contribute a column. Built against a synthetic tree (the real
    migrations copied, plus one ADDED migration that would visibly change
    `public.point`), so the assertion measures the gate and not the fixture.

    The second half is the non-vacuity proof: with that same file's name added to
    the tuple, the column DOES appear. Without it, the assertion above would also
    pass against a tree the fixture never made observable.
    """
    tree = _synthetic_tree(tmp_path / "migrations")

    read = _read_migrations(tree)
    assert _SMUGGLED not in read, (
        "the reader picked up a file that is not in RECORD_MIGRATIONS — the directory "
        "IS observed, so `supabase/migrations/**` must become a trigger in BOTH workflows"
    )
    assert _SMUGGLED not in table_columns(tree)["point"], (
        "a migration absent from RECORD_MIGRATIONS changed the parsed schema"
    )

    monkeypatch.setattr(
        record_projection, "RECORD_MIGRATIONS", (*RECORD_MIGRATIONS, _EXTRA_NAME)
    )
    assert _SMUGGLED in table_columns(tree)["point"], (
        "adding the unlisted migration's NAME did not change the parse either — the "
        "fixture does not discriminate, so the assertions above prove nothing"
    )


def test_the_names_it_reads_are_all_present_in_the_shipped_tree():
    """`RECORD_MIGRATIONS` must name real files — the gate reads by name.

    A rename in `supabase/migrations/` that misses this tuple drops the file from
    the concatenated SQL. How loudly that fails depends on the file: losing a
    base migration takes whole `CREATE TABLE`s with it and `table_columns()`
    raises `RecordProjectionError`, while losing a FORWARD migration (an
    `ALTER TABLE … ADD COLUMN`) raises nothing and silently returns a smaller
    schema — the stale-record failure this pin exists for. Measured in
    `test_a_missing_name_degrades_silently_for_a_forward_migration`.
    """
    missing = sorted(name for name in RECORD_MIGRATIONS if not (_SHIPPED / name).exists())
    assert not missing, f"RECORD_MIGRATIONS names files absent from {_SHIPPED}: {missing}"


def test_every_record_migration_the_wheel_ships_is_named():
    """The CONVERSE pin (R2/#9550): the tuple is the registry, not a subset.

    The pin above (`RECORD_MIGRATIONS` → disk) and
    `test_the_record_schema_ships_with_the_wheel` (tuple → `package-data` glob)
    both only ever read the tuple, so a `.sql` file that nobody appended was
    invisible in BOTH directions — and it was: measured 2026-10-22, the tree had
    SEVEN `*_record_*.sql` files and SIX names, the erasure RPC
    (`20261012000005`) absent since the commit that created the whole record
    (#8179). Nothing was red, and nothing could be.

    Why the FILENAME is the gate and not the SQL's shape:
    `tests/test_record_projection.py` derives the set from the SQL (a
    `CREATE TABLE`/`ALTER TABLE … ADD COLUMN`/re-declared CHECK regex), which is
    a heuristic over spellings — a column added inside a `DO` block, or with
    `ADD` spelled without `COLUMN`, is shipped and read by nobody with that pin
    still green. `supabase/migrations/*_record_*.sql` is the one partition that
    cannot miss a file, and it is the same convention `pyproject.toml` ships the
    wheel by, so it is READ FROM `pyproject.toml` here rather than restated.

    Non-vacuity: the assertion reds on the tree before this change names the
    RPC (7 shipped, 6 named); it is green only while the two sets are equal.
    """
    with open(_ROOT / "pyproject.toml", "rb") as fh:
        conf = tomllib.load(fh)["tool"]["setuptools"]
    patterns = conf["package-data"]["tortoise.record_migrations"]
    shipped = sorted(
        p.name for pattern in patterns for p in _SHIPPED.glob(pattern)
    )
    assert shipped, f"no record migration matched {patterns} in {_SHIPPED}"
    unnamed = sorted(set(shipped) - set(RECORD_MIGRATIONS))
    assert not unnamed, (
        f"record migration(s) ship in the wheel but are NOT in "
        f"RECORD_MIGRATIONS: {unnamed} — the reader iterates the tuple, so a "
        "file missing from it is read by nobody: append its name (the registry "
        "is the tuple, not a subset of it — #9550)"
    )


def test_a_missing_name_degrades_silently_for_a_forward_migration(tmp_path: Path):
    """Why the name pin above is not pedantry: the forward half fails SILENTLY.

    A missing base migration is loud (`RecordProjectionError`). A missing FORWARD
    migration is not — the create-table schema still parses, so the reader
    returns a well-formed schema that has simply lost the later columns. Both
    directions are measured here, so the docstring's claim and the pin's
    necessity rest on the behaviour rather than on a reading of the tuple.
    """
    def _text(name: str) -> str:
        return (_SHIPPED / name).read_text(encoding="utf-8")

    create_table = re.compile(r"^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?public\.", re.M | re.I)
    base_names = [n for n in RECORD_MIGRATIONS if create_table.search(_text(n))]
    money_names = [n for n in RECORD_MIGRATIONS if "amount_minor" in _text(n)]
    assert base_names, "expected at least one base (CREATE TABLE) record migration"
    assert len(money_names) == 1, f"expected exactly one migration adding amount_minor, got {money_names}"
    forward_name = money_names[0]
    assert forward_name not in base_names, "the money column is expected to arrive as a forward ALTER"

    # Forward: silently smaller schema, no exception.
    forward_tree = tmp_path / "forward"
    forward_tree.mkdir()
    for name in RECORD_MIGRATIONS:
        if name != forward_name:
            shutil.copy(_SHIPPED / name, forward_tree / name)
    assert "amount_minor" not in table_columns(forward_tree)["point"], (
        "dropping the forward ADD COLUMN migration still reported its column — the "
        "silent-degradation half of this pin no longer describes the reader"
    )

    # Base: loud, so a whole lost table cannot pass unnoticed.
    base_tree = tmp_path / "base"
    base_tree.mkdir()
    for name in RECORD_MIGRATIONS:
        if name != base_names[0]:
            shutil.copy(_SHIPPED / name, base_tree / name)
    with pytest.raises(record_projection.RecordProjectionError):
        table_columns(base_tree)
