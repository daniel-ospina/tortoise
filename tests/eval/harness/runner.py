"""W3 harness — the graded reflex replay lane (issue #8125).

``tests/eval/harness/`` originally held the W3 replay-and-grade benchmark: a
committed corpus replayed through the REAL write seam (the SDK's
``capture_session``), then graded on the recall and reflex surfaces, and
published as a receipt the ``_main`` CLI could bless into a baseline.

**#4282 deleted ``capture_session`` from the SDK surface, and no surviving SDK
entry point reaches an in-memory conversation capture** (``index_sessions`` /
``reconcile_sessions`` index session *files*; the hosted ``POST /v1/sessions``
door is a server route, not an SDK method).  The replay leg and everything that
fed it — ``replay_and_grade``, ``run_benchmark``, ``_bless_main`` and the CLI —
were therefore removed with it, together with the corpus-replay probes that
drove them (``tests/eval/harness/test_harness_benchmark.py``,
``tests/eval/write_path/test_write_path_benchmark.py`` and the
``write_path`` benchmark lane).  What remains is the ONE helper that never
touched the write path: ``replay_reflex``, the graded
``tortoise.volunteer.decide`` replay over an ALREADY-populated cell.  It is kept
because ``tests/test_volunteer_reflex_harness_seam.py`` grades the product's
reflex seam against it, and that seam survives untouched.
"""

from __future__ import annotations

# The reflex's EP gate (tortoise.volunteer defaults) — the product bar the
# graded replay decides on.  Pinned here so a corpus replay states the exact
# gate it graded against instead of inheriting whatever the product default
# happens to be on the day.
REFLEX_MIN_CONFIDENCE = 0.7
REFLEX_MAX_POINTERS = 3


def replay_reflex(sdk, turns: list[dict], *, log: list[str] | None = None
                  ) -> dict[int, list[str]]:
    """Replay the turn window through the REAL reflex → injected pointers.

    One ``volunteer.decide`` call per USER turn with the window so far (the
    product's own per-turn shape), carrying the canonical prior-context block
    forward so cross-turn re-mention suppression is exercised exactly as in
    the product.  Returns the harness's ``injected`` vocabulary:
    ``{fixture-turn-index: [point ids]}``.
    """
    log = log if log is not None else []
    from tortoise.volunteer import build_block, decide

    proj = sdk._get_proj()
    window: list[dict] = []
    prior_context: str | None = None
    injected: dict[int, list[str]] = {}
    for index, turn in enumerate(turns, start=1):
        window.append(turn)
        if turn.get("role") != "user":
            continue
        decision = decide(
            proj, window, prior_context=prior_context,
            min_confidence=REFLEX_MIN_CONFIDENCE,
            max_pointers=REFLEX_MAX_POINTERS,
            _search_fn=sdk.tortoise_fts_query,
        )
        if not decision["fire"]:
            continue
        injected[index] = list(decision["pointer_ids"])
        prior_context = build_block(
            decision["pointers"],
            [{"label": p["label"], "band": "medium"}
             for p in decision["pointers"]],
        )
    log.append(
        f"graded reflex: fired on {len(injected)} turn(s) "
        f"(min_confidence={REFLEX_MIN_CONFIDENCE}, "
        f"max_pointers={REFLEX_MAX_POINTERS})"
    )
    return injected
