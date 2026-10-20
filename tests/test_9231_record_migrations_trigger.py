"""The record schema ships in the images, and the two build workflows agree (#9325, #9326).

`tortoise.record_migrations` is a MAPPED package: `pyproject.toml`
`[tool.setuptools.package-dir]` points it at `supabase/migrations`, so an editable
install inside a build context that does not contain that tree fails outright —
`error: package directory 'supabase/migrations' does not exist` — which is the
`packaging-smoke` red #9325 fixed. Both Dockerfiles therefore COPY the tree.

The second half is the one that went wrong: whether that COPY makes the tree a
DEPLOY / PUBLISH TRIGGER. It does not, and the two workflows have to answer that
the same way — `deploy-hosted` listed `supabase/migrations/**` while
`publish-selfhost` (whose declared invariant is "the exact build-input set of
its Dockerfile") did not, for the identical COPY line (#9326 review, P2 x2).

Why "not a trigger" is the answer, and not merely a preference — the reader is
NAME-GATED (`test_the_record_reader_is_name_gated` below proves it):

  * adding a `.sql` file does not change anything the image reads, because
    `_read_migrations()` iterates `RECORD_MIGRATIONS`, never the directory;
  * editing a file it DOES read is refused by `check-migration-append-only
    diff` (migrations are append-only), so such a change must arrive as a new
    name in `RECORD_MIGRATIONS` — i.e. in `tortoise/record_projection.py`, which
    `tortoise/**` already triggers.

So the entry added no coverage, while costing a RED push run per migrations-only
merge: `supabase-deploy` runs its deploys on `workflow_dispatch` only, and
`deploy-hosted`'s fail-closed `Check migration drift` exits 1 for every
repo-ahead BLOCKING migration until an operator applies it.

If a future change makes the tree genuinely observable at runtime (a directory
glob, a migration applier in the image), the polarity below flips: list it in
BOTH workflows and delete the name-gate proof with it. The two are the argument
and the answer — changing one alone is the defect these tests exist for.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.record_projection import (  # noqa: E402
    RECORD_MIGRATIONS,
    _read_migrations,
    table_columns,
)

_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOWS = _ROOT / ".github" / "workflows"
_MIGRATIONS_GLOB = "supabase/migrations/**"
_HOSTED = _WORKFLOWS / "deploy-hosted.yml"
_SELFHOST = _WORKFLOWS / "publish-selfhost.yml"
_DOCKERFILES = (_ROOT / "Dockerfile.hosted", _ROOT / "Dockerfile.selfhost")


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


@pytest.mark.parametrize("dockerfile", _DOCKERFILES, ids=lambda p: p.name)
def test_each_image_copies_the_record_migrations_tree(dockerfile: Path):
    """The mapped package directory must exist in the build context (#9325).

    Without this COPY the editable install cannot resolve the package and the
    image never builds — in BOTH images, which is why the COPY is asserted for
    each rather than for `Dockerfile.hosted` alone.
    """
    copy_lines = [
        line.strip()
        for line in dockerfile.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("COPY ") and "supabase/migrations" in line
    ]
    assert copy_lines, f"{dockerfile.name} does not COPY supabase/migrations/ into the build context"


def test_the_two_workflows_answer_the_migrations_tree_the_same_way():
    """Both-or-neither (#9326 review, ISSUE 2).

    One COPY line, added to both Dockerfiles by one change — so a trigger
    answer that differs between the two workflows is one of them being wrong.
    This is the polarity the review found broken: `deploy-hosted` listed the
    tree, `publish-selfhost` did not.
    """
    hosted = _push_paths(_HOSTED)
    selfhost = _push_paths(_SELFHOST)
    in_hosted = _MIGRATIONS_GLOB in hosted
    in_selfhost = _MIGRATIONS_GLOB in selfhost
    assert in_hosted == in_selfhost, (
        f"{_MIGRATIONS_GLOB} is a deploy/publish trigger in "
        f"{'deploy-hosted' if in_hosted else 'publish-selfhost'} but not in the other. "
        "Both images COPY that tree, so both workflows must give the same answer — "
        "either list it in both (and accept the red drift run per migrations-only merge) "
        "or in neither, with the reason recorded on the exclusion."
    )


def test_the_record_reader_is_name_gated(tmp_path: Path):
    """A migration outside `RECORD_MIGRATIONS` cannot change what the image reads.

    This is the evidence under the "not a trigger" answer: the reader iterates
    its hardcoded tuple, so a newly added `.sql` file is not merely unread — it
    cannot contribute a column. Built against a synthetic tree (the real
    migrations copied, plus one ADDED migration that would visibly change
    `public.point`), so the assertion measures the gate and not the fixture.
    """
    tree = tmp_path / "migrations"
    tree.mkdir()
    source = _ROOT / "supabase" / "migrations"
    for name in RECORD_MIGRATIONS:
        shutil.copy(source / name, tree / name)

    extra_name = "20300101000000_record_added_column_after_the_fact.sql"
    (tree / extra_name).write_text(
        "ALTER TABLE public.point ADD COLUMN IF NOT EXISTS smuggled_column text;\n",
        encoding="utf-8",
    )

    read = _read_migrations(tree)
    assert extra_name not in read, (
        "the reader picked up a migration that is not in RECORD_MIGRATIONS — the "
        "directory IS observed, so `supabase/migrations/**` must become a trigger "
        "in BOTH workflows (see this module's docstring)"
    )
    assert "smuggled_column" not in read
    assert "smuggled_column" not in table_columns(tree)["point"], (
        "a migration absent from RECORD_MIGRATIONS changed the parsed schema"
    )


def test_the_names_it_reads_are_all_present_in_the_shipped_tree():
    """`RECORD_MIGRATIONS` must name real files — the gate reads by name.

    A rename in `supabase/migrations/` that misses this tuple would make
    `_complete()` false for the repo tree, silently preferring (or falling back
    to) the packaged stub, so the names and the tree are pinned together here.
    """
    tree = _ROOT / "supabase" / "migrations"
    missing = sorted(name for name in RECORD_MIGRATIONS if not (tree / name).exists())
    assert not missing, f"RECORD_MIGRATIONS names files absent from {tree}: {missing}"
