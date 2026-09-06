"""seed_mode test-support — the SINGLE home Tasks 4/10 import (#2284).

Every helper drives the REAL hermetic A4/batch path (battery.arms.a4_tortoise
+ battery.runner.setup.batch_setup) — never a reimplementation of seeding
logic. Contracts:

- ``setup_seed_mode(namespace, scenario_id)`` — seed ONE scenario into a
  hermetic A4 store under ``namespace`` (a directory; the DB file lives at
  ``namespace/battery.db``) through the real arm, seed_mode default (¬A
  never pre-seeded). Re-setup over a stale PRE-FIX full graph in the SAME
  namespace raises ``ConfigError`` (warm guard, seeder-owned marker); re-setup
  over a clean seed_mode graph accumulates (Task 10 locks the stream side).
- ``seed_full_legacy(namespace, scenario_id)`` — the PRE-FIX full seeding
  (claim_b + injection-turn statement + NAND present, NO seed-manifest
  marker) over the same hermetic batch path with ``seed_mode=False``. Closes
  its projection before returning so ``setup_seed_mode`` can reopen the SAME
  store (the warm-guard shape).
- ``real_prek_surface(arm_id, scenario_id, *, namespace=None)`` — ONE arm's
  real pre-k reader-visible surface (a string): hermetic arms (a4 — or any
  arm whose arms.yaml config declares the hermetic capability key below) =
  seed_mode store retrieve + pre-k policy render; every other arm (mock/a0/a1
  + vendor arms a2/a2b/a3) = pre-k policy render only. The arm classification
  is driven by the capability key, never a hardcoded tuple.
- ``prek_policy_render(scenario)`` — the rendered policy (single render rule
  via ``render_reader_prompt``) truncated BEFORE the ¬A injection turn: for
  contradiction scenarios with planted pairs the non-system turns at/after
  the injection turn index are dropped (the ¬A turn = authored index
  ``CONTRADICTION_K - 1``); pair-less/benign scenarios render in full.

Namespace semantics: one directory = one hermetic store (one DB file). The
store's per-scenario graphs are the A4 namespaces (``battery_<id>``) — the
warm guard is per scenario namespace inside the same DB file.
"""
from __future__ import annotations

import tempfile
from functools import lru_cache
from pathlib import Path

from battery.arms.a4_tortoise import A4TortoiseArm
from battery.arms.base import AgentContext
from battery.config.arms import load_arms
from battery.config.corpus import Scenario, load_corpus
from battery.config.corpus_loader import render_reader_prompt
from battery.config.schema import CONTRADICTION_K  # noqa: F401  (re-exported)

_CORPUS_YAML = Path(__file__).resolve().parent.parent / "config" / "corpus.yaml"
_ARMS_YAML = Path(__file__).resolve().parent.parent / "config" / "arms.yaml"

#: arms.yaml capability key that marks an arm as holding a hermetic
#: scenario store (its pre-k surface = seeded store + pre-k policy render).
#: NOT declared by any arm today — a4 is hermetic by adapter (the tortoise
#: graph arm), mock/a0/a1 + vendor arms (a2/a2b/a3) are policy-surface-only.
#: real_prek_surface consults this key per arm config (documented, never a
#: hardcoded tuple) so a future hermetic vendor carve-out composes correctly.
HERMETIC_CAPABILITY_KEY = "hermetic_carve_out_store"

#: Hermetic arms are identified by adapter family when the capability key is
#: absent: a4 is the ONLY in-repo arm whose setup_scenarios writes a
#: scenario graph (its own embedded store). Mirrors the arm wiring, not a
#: corpus tuple.
_HERMETIC_BY_ADAPTER = ("battery.arms.a4_tortoise",)


@lru_cache(maxsize=1)
def _scenarios_by_id() -> dict[str, Scenario]:
    """Run-path corpus (yaml source) indexed by id — mirrors run.py."""
    return {sc.id: sc for sc in load_corpus(_CORPUS_YAML)}


def _scenario(scenario_id: str) -> Scenario:
    sc = _scenarios_by_id().get(scenario_id)
    if sc is None:
        raise KeyError(f"no scenario {scenario_id!r} in the committed corpus")
    return sc


def _db_file(namespace: str | Path) -> Path:
    return Path(namespace) / "battery.db"


def _open_proj(namespace: str | Path):
    """Hermetic projection over the namespace store (mirrors A4's init)."""
    from tortoise.projection import FalkorProjection
    return FalkorProjection(str(_db_file(namespace)), graph_name="test")


def _uri_set_supported() -> bool:
    """A supported TORTOISE_DB_URI is set (the docker-lane redirect
    predicate family — mirrors tests/_embedded). Absent URI ⇒ embedded lane."""
    uri = __import__("os").environ.get("TORTOISE_DB_URI")
    if not uri:
        return False
    from tortoise.config import is_db_uri
    return is_db_uri(uri)


def purge_owned_namespace(namespace: str | Path, scenario_id: str) -> None:
    """Server-lane hermetic purge of the owned scenario namespace.

    Under a supported TORTOISE_DB_URI + TEST_MODE (the docker lane, epic
    #1647) every raw embedded ``FalkorProjection(db_path)`` construction
    redirects to the shared server, where the per-scenario graphs
    (``battery_<id>`` via ``scenario_namespace``) are GLOBAL: leftover
    markers/legacy residue from an earlier test item or session would
    corrupt the warm-guard and no-leak assertions. This helper
    DETACH-DELETEs + drops EXACTLY the scenario namespace(s) the seed
    helpers are about to write (scoped to the owned graph — never a blanket
    wipe). No URI ⇒ embedded lane (fresh per-test tmp_path store) ⇒ no-op
    without even opening a projection.
    """
    if not _uri_set_supported():
        return  # embedded lane: fresh per-test store — nothing to purge
    proj = _open_proj(namespace)
    try:
        if getattr(proj, "_is_embedded", True):
            return  # construction stayed embedded (defensive) — no purge
        from battery.runner.setup import scenario_namespace
        proj.db.select_graph(scenario_namespace(scenario_id)).query(
            "MATCH (n) DETACH DELETE n")
        proj.db.select_graph(scenario_namespace(scenario_id)).delete()
    finally:
        proj.close()


def _authored_turns(scenario: Scenario) -> list[dict]:
    return [t for t in scenario.prompt_pack if t.get("role") != "system"]


def prek_policy_render(scenario: Scenario) -> str:
    """Rendered policy truncated BEFORE the ¬A injection turn (pre-k
    projection) — the pre-k reader surface for arms without a graph store.
    Contradiction scenarios with planted pairs drop the non-system turns at
    authored index ``injection_turn - 1`` and beyond (the ¬A arrives
    in-context at k); benign/pair-less scenarios render in full."""
    cut: int | None = None
    if scenario.task_type == "contradiction" and scenario.contradiction_pairs:
        cut = min(p.injection_turn - 1 for p in scenario.contradiction_pairs)
    rd = scenario.to_render_dict()
    if cut is not None:
        rd["prompt"]["turns"] = list(rd["prompt"]["turns"][:cut])
    return render_reader_prompt(rd)


class SeededStore:
    """Facade over a REAL hermetic A4 seed_mode store.

    ``retrieve`` goes through the actual arm adapter (``A4TortoiseArm.
    retrieve`` — the product read surface, never reimplemented);
    ``find_content`` reads the scenario's graph directly (test support only)
    with one CONTAINS query per fragment. ``surface_text`` joins the
    retrieved memories (the arm's pre-k memory surface).
    """

    def __init__(self, arm: A4TortoiseArm, scenario: Scenario):
        self._arm = arm
        self._scenario = scenario

    def retrieve(self, context_text: str = "") -> list:
        """Real arm retrieve over the seeded graph (pre-k memory)."""
        ctx = AgentContext(
            scenario=self._scenario, episode_seed=0,
            prior_memories=(), user_message=context_text or "")
        return self._arm.retrieve(ctx)

    def surface_text(self) -> str:
        return " ".join(str(m) for m in self.retrieve(""))

    def find_content(self, fragment: str) -> list[str]:
        """Node ids whose content contains the fragment (each fragment is
        searched separately — never a concatenated search string)."""
        g = self._arm._scenario_graph(self._scenario)
        rows = g.query(
            "MATCH (n:Point) WHERE n.content CONTAINS $frag RETURN n.id",
            params={"frag": fragment}).result_set
        return [str(r[0]) for r in rows]

    def close(self) -> None:
        self._arm.close()


def setup_seed_mode(namespace: str | Path, scenario_id: str, *,
                    purge: bool = True) -> SeededStore:
    """Seed ONE scenario into a hermetic A4 store (real arm, seed_mode
    default) under ``namespace``. Raises ``ConfigError`` when a stale
    PRE-FIX full graph occupies the same store (warm guard); a clean
    seed_mode graph accumulates (no refuse, no duplicate content).

    ``purge=True`` (default): the owned scenario namespace is purged first
    on the server lane (embedded: no-op — fresh per-test DB), so the store
    is always fresh. ``purge=False``: observe/accumulate over existing
    namespace content (the warm-store tests) — the warm guard fires when a
    stale PRE-FIX graph is present."""
    sc = _scenario(scenario_id)
    Path(namespace).mkdir(parents=True, exist_ok=True)
    if purge:
        purge_owned_namespace(namespace, scenario_id)
    arm = A4TortoiseArm(db_path=str(_db_file(namespace)))
    arm.setup_scenarios([sc])  # seed_mode is the batch default (#2284 T4)
    return SeededStore(arm, sc)


def seed_full_legacy(namespace: str | Path, scenario_id: str) -> None:
    """Seed the PRE-FIX full graph (claim_b + injection-turn statement +
    NAND; NO seed-manifest marker) over the SAME hermetic batch path with
    ``seed_mode=False``. The owned scenario namespace is always purged
    first (server lane), so the legacy store is fresh; the projection is
    closed before returning so ``setup_seed_mode`` can reopen the same
    store (warm-store shape)."""
    sc = _scenario(scenario_id)
    Path(namespace).mkdir(parents=True, exist_ok=True)
    purge_owned_namespace(namespace, scenario_id)
    proj = _open_proj(namespace)
    try:
        from battery.runner.setup import batch_setup
        batch_setup(proj, [sc], namespaced=True, seed_mode=False)
    finally:
        proj.close()


def _hermetic(arm_cfg) -> bool:
    """Hermetic-store arms: a4 by adapter family, or any arm whose arms.yaml
    config declares HERMETIC_CAPABILITY_KEY (documented capability seam —
    never a hardcoded tuple)."""
    if arm_cfg.config.get(HERMETIC_CAPABILITY_KEY):
        return True
    return arm_cfg.adapter in _HERMETIC_BY_ADAPTER


def real_prek_surface(arm_id: str, scenario_id: str, *,
                      namespace: str | Path | None = None) -> str:
    """Compose ONE arm's real pre-k reader-visible surface (string).

    Hermetic arms (a4 today): a REAL seed_mode hermetic store retrieve
    (open → compose → close; content is pure and deterministic) + the pre-k
    policy render. All other arms: pre-k policy render only (their only
    pre-k content is the transcript so far).
    """
    sc = _scenario(scenario_id)
    arms = load_arms(_ARMS_YAML)
    cfg = arms[arm_id]
    parts = [prek_policy_render(sc)]
    if _hermetic(cfg):
        ns = Path(namespace) if namespace is not None \
            else Path(tempfile.mkdtemp(prefix="battery_r1seed_"))
        store_ns = ns / f"{arm_id}-hermetic"
        store = setup_seed_mode(store_ns, scenario_id)
        try:
            parts.append(store.surface_text())
        finally:
            store.close()
    return "\n\n".join(parts)
