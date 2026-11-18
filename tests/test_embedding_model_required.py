"""#4861 — ``TORTOISE_EMBEDDING_MODEL_REQUIRED``, the fail-loud embedder contract.

The invariant: **a process that ARMED this flag is never handed a keyword-only
result it cannot tell apart from a healthy one.** This is its third surface — a
lane that cannot run hybrid must not run (#2985), a read that could not run its
vector leg must not be labelled hybrid (#2952), and a *process* that required
the embedder must not be handed ``None``.

⛔ THE CLAIM IS SCOPED TO ARMED PROCESSES AND TO THE READ PATH. The blanket form
("no green signal may be producible while the dense leg is absent") was FALSE:
#5275 found the flag defeated at ``tortoise_fts_query``, and the product
deliberately degrades elsewhere. The declared intentional degrades — NOT covered
by the raise and NOT defects — are:

* **Write paths** (``encode_for_store`` callers: ``tortoise/api.py``,
  ``tortoise/sdk.py``, ``tortoise/projection/entities.py``, …). Point creation
  must never depend on embeddings, so a write stores no vector and keeps going.
* **The cached-snapshot fallback tier** (``tortoise/fallback_snapshot.py``),
  which exists to serve a keyword-only result when the primary legs fail. It
  keeps its documented degrade: a text query classifies to the vector strategy,
  so an ARMED process raises at the read path's step 2 and never reaches it.
* **``fallback_tfidf``'s in-memory scorer** — same tier, but #5275 makes the
  function itself PROPAGATE the named refusal when it is armed, because its rows
  ARE a read result and ``[]`` is the quietest possible outcome. The scorer's
  own sklearn degrade (flag off) is untouched.
* **``warm_up()``**, whose NEVER-raises contract is separate (see below).

What the flag DOES guarantee, and what this module plus
``tests/test_5275_required_read_path.py`` pin: every ``None`` origin inside
``EmbeddingModel.get`` raises ``EmbedderUnavailableError`` (this module), and
``TortoiseSDK.tortoise_fts_query`` catches that class BY NAME and re-raises it
instead of laundering it into the designed-absence ``no_embedder`` degrade
(#5275).

The contract is OFF by default and reversible: unset, ``get()`` returns ``None``
exactly as before. Every test below arms the embedder's own failure state, so the
whole module is deterministic and never loads (or downloads) a model.

The load-bearing test is :func:`test_required_raises_on_negative_cache` — it is a
*removal* test: rewire that ``None`` return and it fails. That is what separates
a real gate from a decorative branch; asserting the branch merely exists does
not.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from tortoise.embeddings import (
    EMBEDDING_MODEL,
    EMBEDDING_MODEL_REVISION,
    EmbeddingModel,
)
from tortoise.exceptions import FAILURE_KINDS, EmbedderUnavailableError

REQUIRED_ENV = "TORTOISE_EMBEDDING_MODEL_REQUIRED"


def _arm_negative_cache(monkeypatch, kind: str = "not_installed",
                        err: str = "sentence-transformers is not installed") -> None:
    """Put the singleton into the post-failure negative-cache state.

    This is the state a runner-down or missing-extra process is actually in:
    a load already failed and ``get()`` is inside the cooldown window. No model
    is loaded, so the path is reachable deterministically.
    """
    monkeypatch.setattr(EmbeddingModel, "_instance", None)
    monkeypatch.setattr(EmbeddingModel, "_last_failed_at", time.monotonic())
    monkeypatch.setattr(EmbeddingModel, "_last_failure_kind", kind)
    monkeypatch.setattr(EmbeddingModel, "_last_error", err)


# --------------------------------------------------------------------------
# The default is unchanged — the contract cannot drift.
# --------------------------------------------------------------------------

def test_unset_returns_none(monkeypatch):
    """UNSET → ``None``, exactly as before. Pins the off-by-default contract."""
    monkeypatch.delenv(REQUIRED_ENV, raising=False)
    _arm_negative_cache(monkeypatch)
    assert EmbeddingModel.get() is None


def test_required_is_off_by_default(monkeypatch):
    monkeypatch.delenv(REQUIRED_ENV, raising=False)
    assert EmbeddingModel._embedder_required() is False


@pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
def test_truthy_contract_is_honoured(monkeypatch, value):
    """#4097: the declared truthy contract, not hand-rolled truthiness."""
    monkeypatch.setenv(REQUIRED_ENV, value)
    _arm_negative_cache(monkeypatch)
    assert EmbeddingModel._embedder_required() is False
    assert EmbeddingModel.get() is None


# --------------------------------------------------------------------------
# The removal test — the one that proves the gate is real.
# --------------------------------------------------------------------------

def test_required_raises_on_negative_cache(monkeypatch):
    """REQUIRED + a failed load → raise, not ``None``. **Removal test.**

    Delete the ``return cls._unavailable(...)`` wiring on this path and this
    test fails — which is the point: it asserts the failure is *visible*, not
    that a branch exists.
    """
    monkeypatch.setenv(REQUIRED_ENV, "1")
    _arm_negative_cache(monkeypatch, kind="not_installed")
    with pytest.raises(EmbedderUnavailableError) as ei:
        EmbeddingModel.get()
    assert ei.value.failure_kind == "not_installed"
    assert ei.value.context == "negative cache, load failed within cooldown"


def test_required_raises_on_transient_failure_exit(monkeypatch):
    """The other reachable origin: an instance exists but has no model.

    This is the "provisioning succeeded, the load still broke" case — the one
    #4123's provisioning gate cannot see, because the step passed.
    """
    monkeypatch.setenv(REQUIRED_ENV, "1")
    monkeypatch.setattr(EmbeddingModel, "_instance", SimpleNamespace(_model=None))
    monkeypatch.setattr(EmbeddingModel, "_last_failed_at", None)
    monkeypatch.setattr(EmbeddingModel, "_last_failure_kind", "load_timeout")
    monkeypatch.setattr(EmbeddingModel, "_last_error", "timed out after 90s")
    with pytest.raises(EmbedderUnavailableError) as ei:
        EmbeddingModel.get()
    assert ei.value.failure_kind == "load_timeout"
    assert ei.value.context == "load failed (timeout/OOM)"


# --------------------------------------------------------------------------
# Attribution — the two runs must stay distinguishable BY THE FAILURE.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["not_installed", "load_failed", "load_timeout"])
def test_failure_kind_is_carried_through(monkeypatch, kind):
    """`not_installed` (the env never had it) ≠ `load_failed`/`load_timeout`.

    A REQUIRED process does not accept "designed absence" as an excuse —
    designed for a process that did not ask for the embedder.
    """
    monkeypatch.setenv(REQUIRED_ENV, "1")
    _arm_negative_cache(monkeypatch, kind=kind)
    with pytest.raises(EmbedderUnavailableError) as ei:
        EmbeddingModel.get()
    assert ei.value.failure_kind == kind


def test_message_names_model_revision_kind_and_fix(monkeypatch):
    """The raise must be attributable and actionable without reading code."""
    monkeypatch.setenv(REQUIRED_ENV, "1")
    _arm_negative_cache(monkeypatch, kind="not_installed", err="ModuleNotFoundError: no st")
    with pytest.raises(EmbedderUnavailableError) as ei:
        EmbeddingModel.get()
    msg = str(ei.value)
    assert EMBEDDING_MODEL in msg
    assert EMBEDDING_MODEL_REVISION in msg
    assert "not_installed" in msg
    assert REQUIRED_ENV in msg
    assert "keyword-only" in msg
    # #5277: the remediation must NOT repeat the #2985 footgun — an explicit
    # --extra list is EXACT and silently REMOVES the ``parity`` extra.
    assert "--extra embeddings --extra parity" in msg
    assert "--all-extras" in msg
    assert "#2985" in msg
    assert "ModuleNotFoundError" in msg


def test_error_defaults_kind_when_none_recorded(monkeypatch):
    monkeypatch.setenv(REQUIRED_ENV, "1")
    _arm_negative_cache(monkeypatch, kind=None)  # type: ignore[arg-type]
    with pytest.raises(EmbedderUnavailableError) as ei:
        EmbeddingModel.get()
    assert ei.value.failure_kind == "model_unavailable"


def test_every_recorded_failure_kind_is_in_the_declared_vocabulary():
    """The producers' literals and :data:`FAILURE_KINDS` cannot drift apart.

    ``FAILURE_KINDS`` (``exceptions.py``) is the ONE declaration of the
    ``failure_kind`` vocabulary. ``EmbeddingModel._as_failure_kind`` answers the
    documented ``model_unavailable`` fallback for anything outside it — the same
    value it answers when nothing was recorded — so a producer that starts
    recording a NEW literal would silently downgrade an ARMED refusal to
    "nothing was recorded" without any error. That is the #5275 mis-attribution
    class one level down (#13534 review round 2, B).

    ⛔ DERIVED, not re-typed. The producers are found by PARSING
    ``tortoise/embeddings.py`` for ``<x>._last_failure_kind = <value>``
    assignments, so adding a fifth kind at a producer reds here rather than
    needing this test to have been remembered — the drift class ``MEMORY.md``
    records under ``[vocab]`` (two hand copies never catch their SHARED drift).

    ⛔ AND A NON-LITERAL PRODUCER IS REFUSED, not skipped: a check that compares
    only ``ast.Constant`` values would leave a producer assigned from a module
    constant — the likely refactor once ``FAILURE_KINDS`` exists — unmeasured,
    reporting the vocabulary as pinned while an armed refusal silently degraded
    it (#13534 review round 3, A and B).
    """
    import ast
    from pathlib import Path

    source = Path(__file__).resolve().parent.parent / "tortoise" / "embeddings.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))

    def _records_kind(target: ast.expr) -> bool:
        return (
            isinstance(target, ast.Attribute)
            and target.attr == "_last_failure_kind"
        )

    assignments = [
        node for node in ast.walk(tree)
        if (isinstance(node, ast.Assign) and any(_records_kind(t) for t in node.targets))
        or (isinstance(node, ast.AnnAssign) and _records_kind(node.target))
    ]
    assert assignments, (
        "the AST scan found no ``_last_failure_kind`` assignments — the producer "
        "pattern moved, so this pin is no longer measuring anything")
    non_literal = [
        node for node in assignments
        if getattr(node, "value", None) is None
        or not isinstance(node.value, ast.Constant)
    ]
    assert not non_literal, (
        "a producer assigns ``_last_failure_kind`` a value this pin cannot compare "
        f"against FAILURE_KINDS (lines {[n.lineno for n in non_literal]}) — inline "
        "the literal, or widen this pin; an unmeasurable producer reports the "
        "vocabulary as pinned while an armed refusal degrades it silently")
    recorded = {
        node.value.value for node in assignments
        if isinstance(node.value.value, str)
    }
    assert recorded, (
        "the AST scan found no recorded kind literals — the producer pattern "
        "moved, so this pin is no longer measuring anything")
    assert recorded <= FAILURE_KINDS, (
        f"a producer records a kind outside the declared vocabulary: "
        f"{sorted(recorded - FAILURE_KINDS)!r}; an armed refusal would report "
        f"``model_unavailable`` for it, indistinguishable from nothing recorded")


@pytest.mark.parametrize("kind", sorted(FAILURE_KINDS))
def test_every_declared_kind_passes_through_the_translation(monkeypatch, kind):
    """A reason already IN the vocabulary names its own cause — unchanged.

    ``_as_failure_kind`` has two arms; the recorded-cause arm is pinned in
    ``tests/test_13528_read_never_blocks_on_embedder.py`` next to the decline it
    translates. This row pins the OTHER arm over the whole declared vocabulary:
    every member passes through untouched, so a translator that "helpfully"
    re-mapped one of them would red here.
    """
    monkeypatch.setattr(EmbeddingModel, "_last_failure_kind", None)
    assert EmbeddingModel._as_failure_kind(kind) == kind


@pytest.mark.parametrize("reason", ["encode_failed", "breaker_open"])
def test_an_unmapped_trace_reason_is_never_published_as_itself(monkeypatch, reason):
    """An UNMAPPED reason is translated, never put on the wire verbatim (#13543).

    ``main`` did this:

    ```python
    failure_kind = _READ_DECLINE_FAILURE_KIND.get(kind, kind)
    ```

    and ``.get(kind, kind)`` publishes **any** unmapped leg-trace term straight
    into ``failure_kind`` — a CLOSED set. Measured on ``main``:
    ``_unavailable(kind="encode_failed")`` → ``failure_kind == "encode_failed"``,
    and the same for ``"breaker_open"``. It is unreachable today only because
    the sole caller (``tortoise/sdk.py``) passes one of the four known terms — a
    trap for the next caller.

    ``_as_failure_kind`` has no pass-through arm, so the trap is closed in code.
    ⛔ THIS ROW IS WHAT KEEPS IT CLOSED (#13543 comment item 1: *"it does need a
    row, or a future dict-style refactor re-opens it silently"*). Without it a
    translator that special-cased only the two trace words its docstring names
    — ``no_embedder`` and ``load_in_flight`` — and restored ``.get(kind, kind)``
    for everything else kept EVERY other row in this diff green while
    re-publishing ``encode_failed`` as itself (agent-infra#3442 shape: a fix that
    closes a fail-open path with no assertion that reds when it reopens).

    The assertions are LITERALS, never "equals the constant the seam reads":
    comparing the answer against the vocabulary it is derived from proves
    nothing (the #13544 trap, recorded in ``MEMORY.md`` under ``[vocab]``).
    """
    monkeypatch.setattr(EmbeddingModel, "_last_failure_kind", None)
    published = EmbeddingModel._as_failure_kind(reason)
    assert published == "model_unavailable", (
        f"an unmapped reason ({reason!r}) names no cause of its own; with nothing "
        f"recorded the documented fallback is the honest answer — publishing the "
        f"trace word itself puts a value outside FAILURE_KINDS on the wire")
    assert published in FAILURE_KINDS

    monkeypatch.setattr(EmbeddingModel, "_last_failure_kind", "load_failed")
    published = EmbeddingModel._as_failure_kind(reason)
    assert published == "load_failed", (
        "a cause recorded by the time the refusal is raised must be named for an "
        "unmapped reason too, not only for the two trace words the docstring lists")
    assert published in FAILURE_KINDS


# --------------------------------------------------------------------------
# warm_up()'s NEVER-raises contract is preserved (#2952).
# --------------------------------------------------------------------------

def test_warm_up_stays_non_fatal_under_required(monkeypatch):
    """Init stays non-fatal; the *use* site is what fails loudly.

    ``warm_up`` catches ``Exception`` from ``get()``, so raising under REQUIRED
    does not turn a best-effort init probe into a crash — the two contracts do
    not collide.
    """
    monkeypatch.setenv(REQUIRED_ENV, "1")
    _arm_negative_cache(monkeypatch, kind="load_failed")
    assert EmbeddingModel.warm_up() is False


def test_warm_up_still_returns_false_when_not_required(monkeypatch):
    monkeypatch.delenv(REQUIRED_ENV, raising=False)
    _arm_negative_cache(monkeypatch, kind="not_installed")
    assert EmbeddingModel.warm_up() is False


# --------------------------------------------------------------------------
# #5277 item 1 — the NON-RETRIEVAL seams keep the #399/#880 TF-IDF degrade.
# --------------------------------------------------------------------------


def test_encode_degrades_to_tfidf_under_required(monkeypatch):
    """``_encode``'s documented fallback must stay reachable when armed.

    The flag exists so a *retrieval* result cannot be misread as hybrid; it
    must not turn semantic dedup (#880), pairwise similarity or cross-lens
    matching (#399) into hard failures. **Removal test**: rewire the handler
    around ``EmbeddingModel.get()`` in ``_encode`` and this raises.
    """
    monkeypatch.setenv(REQUIRED_ENV, "1")
    _arm_negative_cache(monkeypatch, kind="not_installed")
    from tortoise.embeddings import _encode

    vectors, degraded = _encode([
        "deploy the new feature to production servers tonight",
        "deploy the new feature to production servers today",
    ])
    assert degraded is True
    assert vectors.shape[0] == 2


def test_default_pairwise_similarity_degrades_under_required(monkeypatch):
    """``TortoiseSDK._default_pairwise_similarity`` is a #399 degrade surface."""
    monkeypatch.setenv(REQUIRED_ENV, "1")
    _arm_negative_cache(monkeypatch, kind="load_timeout")
    from tortoise.sdk import TortoiseSDK

    points = [
        {"id": "a", "content": "deploy the new feature to production servers tonight"},
        {"id": "b", "content": "deploy the new feature to production servers today"},
    ]
    scores = TortoiseSDK._default_pairwise_similarity(points)
    assert scores, "TF-IDF must still score the pair; a raise would have propagated"


def test_find_cross_lens_matches_degrades_under_required(monkeypatch):
    """``cross_lens.find_cross_lens_matches`` is a #399 degrade surface."""
    monkeypatch.setenv(REQUIRED_ENV, "1")
    _arm_negative_cache(monkeypatch, kind="load_failed")
    from tortoise.cross_lens import find_cross_lens_matches

    points = {
        "a": {"content": "deploy the new feature to production servers tonight",
              "lens": "src-1"},
        "b": {"content": "deploy the new feature to production servers today",
              "lens": "src-2"},
    }
    candidates = find_cross_lens_matches(points)
    assert isinstance(candidates, list)  # ran degraded, did not raise


def test_semantic_dedup_degrades_under_required(monkeypatch):
    """``_semantic_dedup``'s #880 degrade chain stays reachable when armed."""
    monkeypatch.setenv(REQUIRED_ENV, "1")
    _arm_negative_cache(monkeypatch, kind="not_installed")
    from types import SimpleNamespace

    from tortoise.sdk import TortoiseSDK

    class _FakeProj:
        def __init__(self, rows):
            self.g = SimpleNamespace(
                query=lambda *a, **kw: SimpleNamespace(result_set=rows))

    sdk = object.__new__(TortoiseSDK)
    sdk._get_proj = lambda: _FakeProj(
        [("p1", "deploy the new feature to production servers tonight")])
    pairs = sdk._semantic_dedup(
        [
            ({"id": "c1",
              "content": "deploy the new feature to production servers today"}, ""),
        ],
        threshold=0.7,
        return_pairs=True,
    )
    assert pairs, "TF-IDF must still catch the near-duplicate (#880)"


# --------------------------------------------------------------------------
# #5277 item 5 — ``_last_error`` must not nest the prior message every cycle.
# --------------------------------------------------------------------------


def test_last_error_does_not_nest_under_repeated_warm_up(monkeypatch):
    """The REQUIRED raise interpolates ``_last_error``; storing its message
    re-nests the whole prior chain and grows without bound (#5277).
    """
    monkeypatch.setenv(REQUIRED_ENV, "1")
    _arm_negative_cache(monkeypatch, kind="not_installed")
    lengths = []
    for cycle in range(5):
        assert EmbeddingModel.warm_up() is False
        error = EmbeddingModel._last_error or ""
        assert "last error:" not in error, (
            f"_last_error nested the prior raise at cycle {cycle}: {error!r}")
        lengths.append(len(error))
    assert max(lengths) <= 200, f"_last_error grew across cycles: {lengths}"


# --------------------------------------------------------------------------
# #5277 item 4 — the knob must be discoverable next to its siblings.
# --------------------------------------------------------------------------


def test_env_example_declares_the_required_flag():
    from pathlib import Path

    env = (Path(__file__).resolve().parents[1] / ".env.example").read_text()
    assert REQUIRED_ENV in env, (
        "TORTOISE_EMBEDDING_MODEL_REQUIRED is a fail-loud contract switch "
        "but is not declared in .env.example (#5277)")
    assert f"# {REQUIRED_ENV}=0" in env, "the default must be documented as OFF"
