"""S2.2b mechanical value gate — the identifier-only DISCARD predicate (#4899).

What this file pins, and why each is load-bearing:

- **``#2453`` BOTH directions.** The issue mandates a regression test in each
  direction, because the predicate's whole risk is that it deletes memory: a
  row where a *decision turned on* an identifier must survive, and a row that
  is *nothing but* an identifier must go. Both directions are pinned below on
  **real production rows**, not invented strings.
- **The predicate is IDENTIFIER-ONLY**, so ``the <X>`` definite descriptions,
  CI vocabulary, test counts and inline hex colours are NOT discards. The
  ``the owner`` case is the proof that a definite-article rule cannot be
  mechanical, and is left to the arbiter.
- **Off by default.** ``TORTOISE_VALUE_GATE`` unset ⇒ the predicate never runs
  and the only delta is a zeroed ``mechanical`` stats key, so the rate effect is
  measurable before it is trusted (``#4899`` safeguard 1).
- **No silent discard** (safeguard 2) and a **recoverable counterfactual**
  (safeguard 3) — asserted on the record, not on the send.
- **Fail-open is structural** — a predicate that raises keeps everything.
- **The arbiter overrides it** — the predicate is *necessary, not sufficient*.
- **The pack stays the source of policy** — a drift pin tying each rule id to
  the declaration it implements.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import value_gate as vgm
from tortoise import vet_gate as vg

ROOT = Path(__file__).resolve().parent.parent


# ── helpers ────────────────────────────────────────────────────────────────


def _entity_list(*names: str) -> dict:
    """An embed_list carrying only (unreferenced) entities — the shape the
    Layer-1 guard leaves alone, so a removal is observable."""
    return {
        "entities": [{"name": n, "kind": "core:other"} for n in names],
        "points": [],
        "events": [],
        "operators": [],
    }


def _outcomes(out: dict) -> set[str]:
    return {d["outcome"] for d in out["decisions"].values()}


def _decisions_for(out: dict, text: str) -> list[dict]:
    return [d for d in out["decisions"].values() if d["text"] == text]


# ── the predicate, both directions of #2453 ────────────────────────────────

#: Real production rows (drawn from the live graph, `#4899`'s Stage-1 sample)
#: where an identifier is *incident to a claim*. Every one MUST survive: this
#: is the direction that costs memory.
DECISION_RELEVANT = [
    "The decision turned on #4118",
    "the #4771 decision record",
    "#3014 decision",
    "The #4010 issue banner was updated to the 2026-09-19 position, "
    "withdrawing the 'REOPEN' framing, and the banner now states the date.",
    "The _support_cdata override, the fix for #4118 divergence 2, is exercised "
    "by no test: monkeypatching the property back to True leaves all 18 -k id_ "
    "tests green.",
    "tests/_html_links.py:174-183 marks the same CDATA comparison as the pre-fix result.",
    "The embedded-evidence token #656970 (#7a1c3f) is the review's colour.",
    "docs/auth-architecture.md:93 records the cookie policy for the subdomain.",
    "Committed as 9bce8d31f, and that commit is what fixed the fold.",
    "The worktree was left unmodified during the review.",
    "All 16 website/*.html pages reach the guard's test file via select().",
]

#: Rows that are *nothing but* an identifier. `#2453`'s droppable class.
IDENTIFIER_ONLY = [
    "#4118",
    "See #4118",
    "9bce8d31f",
    "Committed as 9bce8d31f",
    "docs/auth-architecture.md:93",
    "tests/_html_links.py:174-183",
]


@pytest.mark.parametrize("text", DECISION_RELEVANT)
def test_2453_decision_relevant_identifier_survives(text: str) -> None:
    """``#2453``, direction 1 — a value the decision turned on is NOT a discard.

    This is the direction that loses memory, so it is asserted on real rows.
    """
    assert vgm.identifier_only(text) is None, (
        f"identifier-only predicate fired on a decision-relevant row: {text!r} "
        "— this is the #2453 regression the predicate exists to avoid"
    )


@pytest.mark.parametrize("text", IDENTIFIER_ONLY)
def test_2453_bare_identifier_is_discardable(text: str) -> None:
    """``#2453``, direction 2 — incidental logistics remain droppable."""
    assert vgm.identifier_only(text) is not None, f"a bare identifier row did not fire: {text!r}"


def test_boundary_is_the_whole_residue_not_a_keyword() -> None:
    """The boundary is the *whole* residue, not a keyword search.

    One content word anywhere in what is left flips the verdict — the same
    reference, kept or dropped depending on whether a claim hangs off it.
    """
    assert vgm.identifier_only("See #4118") is not None
    assert vgm.identifier_only("See the fix for #4118") is None
    assert vgm.identifier_only("#4118") is not None
    assert vgm.identifier_only("the fix for #4118") is None


# ── what the predicate deliberately does NOT do ────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "the owner",  # a real entity, no antecedent (B7)
        "the cycle-4 ruling",  # a definite description, not an entity
        "the rail",
        "the #4771 decision record",
        "Person",
        "note",
    ],
)
def test_definite_description_is_never_mechanical(text: str) -> None:
    """The ``the <X>`` question is the ARBITER's, not the regex's.

    ``the owner`` is a real entity with no antecedent while ``the cycle-4
    ruling`` is a definite description — no mechanical test separates them, so
    a mechanical rule here would delete real entities. This is the objection
    that killed the ``the ``-prefix rule (70–77% fire rate on real statements).
    """
    assert vgm.identifier_only(text) is None


def test_inline_hex_is_not_a_hash_but_a_bare_hex_is_a_reference() -> None:
    """The measured false positive, stated exactly.

    A hex colour **inside a claim** is kept (it is not identifier-only). A bare
    ``#656970`` fires — and correctly so: as a *candidate* it is literally an
    identifier with no claim attached, which is the adopted class.
    """
    assert vgm.identifier_only("the token #656970 is the review's colour") is None
    assert vgm.identifier_only("#656970") == vgm.RULE_REFERENCE


#: Measurements, ratios, dates and times. ``#2453`` requires these be carried
#: **verbatim**, so every one must fail open. The first four are the review's
#: P1: every decimal digit is also a hex digit, so a digit-only run of 7+
#: stripped to nothing and fired as a commit hash, making the residue-digit
#: guard unreachable.
MEASUREMENTS = [
    "1234567",  # 7-digit number — the P1 exactly
    "123456",  # 6-digit (never fired — the flip was the tell)
    "20260923",  # a date
    "123/456",  # a ratio, slash-shaped
    "4.9333",  # the sample's composite ratio
    "12.5ms",  # a duration
    "13:45",  # a time
    "4.2",
    "800",
    # ── review cycle 2, P1: the same shapes, but with the digits behind a
    # closed-class word or a reference, and slash-dates. The first four were
    # discarded because `_PATH`'s slash form accepted digit-only segments.
    "by 2026/09/19",
    "as of 2026/09/19",
    "#4771 2026/09/19",
    "#4118 123/456",
]


@pytest.mark.parametrize("text", MEASUREMENTS)
def test_measurements_are_never_identifiers(text: str) -> None:
    """A bare number is NOT an identifier (#2453 — carried verbatim).

    ⚠️ One content word or one reference elsewhere in the row must not buy a
    digits-only token a pass — that is the ``RULE_REFERENCE in found``
    short-circuit, and it is exactly how the cycle-2 P1 slipped through.
    """
    assert vgm.identifier_only(text) is None, (
        f"{text!r} was read as an identifier; #2453 carries a recorded "
        "measurement verbatim, so this is a precision failure, not a trade"
    )


@pytest.mark.parametrize("text", ["CI/CD", "I/O", "i/o", "src/main", "a/b"])
def test_slash_words_are_not_paths(text: str) -> None:
    r"""A ``word/word`` pair is not an identifier (review cycle 2, P1).

    ``CI/CD`` is CI vocabulary (deferred to #4894) and ``I/O`` is the
    slash-abbreviation counterpart of the ``e.g`` case — both were discarded
    because the slash form accepted any ``\w+/\w+``.
    """
    assert vgm.identifier_only(text) is None, (
        f"{text!r} was read as a path; it is not an identifier at all"
    )


@pytest.mark.parametrize(
    "text",
    [
        "docs/auth-architecture.md",
        "tests/_html_links.py:174-183",
        "a/b/c.py",
        "src/main.py",
        ".github/workflows/python-ci.yml",
        ".github/actions/x/y.yml",
        # ── real tracked paths silently KEPT before cycle 4's fix (it allowed a
        # single ≤6-letter extension only) and cycle 5's (it required a letter
        # in the final stem). A revert to either shape fails here.
        "docs/4899/notes.md",
        "src/app.test.tsx",
        "tests/foo.spec.ts",
        "docs/plan.markdown",
        "a/b.py.txt",
        "node_modules/@scope/pkg/index.js",
        "a/./b.py",
        "_/b.py",
        "archive.tar.gz",
        "index.d.ts",
        "x/y.py:12",
        "website/404.html",
        "404.html",
        "docs/2026/09/19.md",
        "website/apps/dashboard/public/404.html",
        "docs/retrospectives/2026-08-13-05-49-04.md",
        "a/b/2026.py",
    ],
)
def test_real_paths_still_fire(text: str) -> None:
    """The extra path guards must not cost the real class."""
    assert vgm.identifier_only(text) == vgm.RULE_PATH


#: Dotted tokens that are NOT paths — versions, decimals, abbreviations, an IP.
#: The alphabetic **extension** (≥2 letters) is what rejects them, not the stem.
NOT_PATHS = [
    "v1.2.3",
    "v2.0.1",
    "S2.2",
    "S2.2b",
    "S.1.2",
    "4.2",
    "20.09",
    "3.14",
    "12.5ms",
    "e.g.",
    "i.e.",
    "etc.",
    "U.S.",
    "a.m.",
    "Ph.D",
    "Ph.D.",
    "1.2.3.4",
    "192.168.1.1",
    "a.b",
    "x.com",
    # ── cycle 6, P1: dropping the stem-letter rule made VERSIONS match as
    # paths. #2453 carries versions verbatim, so this is surface-(a).
    "10.0.0.beta",
    "10.0.0.rc",
    "10.0.0.alpha",
    "10.0.0.dev",
    "11.0.0.beta",
    "12.5.beta",
    "12.5.rc",
    "2026.09.beta",
    "v1.2.3.beta",
    "v2.0.0.rc",
    "we shipped 10.0.0.beta",
]


@pytest.mark.parametrize("text", NOT_PATHS)
def test_dotted_tokens_are_not_paths(text: str) -> None:
    """Widening the path pattern must not turn versions into discards."""
    assert vgm.identifier_only(text) is None, (
        f"{text!r} was read as a path; it is a version, a decimal or an "
        "abbreviation, not an identifier"
    )


def test_hash_still_fires_when_it_has_a_hex_letter() -> None:
    """The measurement guard must not cost the real class."""
    assert vgm.identifier_only("9bce8d31f") == vgm.RULE_HASH
    assert vgm.identifier_only("Committed as 9bce8d31f") == vgm.RULE_HASH
    assert vgm.identifier_only("a1b2c3d4e5f6") == vgm.RULE_HASH
    # An uppercase SHA is still a SHA (review cycle 2, P2).
    assert vgm.identifier_only("9BCE8D31F") == vgm.RULE_HASH


def test_uuid_survives_the_strip_order() -> None:
    """The strip order is load-bearing: HASH-first splits a dashed UUID and the
    tail survives, inverting the verdict (review cycle 2, P2)."""
    uid = "550e8400-e29b-41d4-a716-446655440000"
    assert vgm.identifier_only(uid) == vgm.RULE_UUID
    assert vgm.identifier_only(f"see {uid}") == vgm.RULE_UUID


def test_version_and_abbreviation_are_not_paths() -> None:
    """``S2.2`` and ``e.g`` are not file paths — both would be false positives."""
    assert vgm.identifier_only("S2.2") is None
    assert vgm.identifier_only("e.g.") is None


# ── the flag: off by default, inert ────────────────────────────────────────


def test_flag_off_is_inert(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset ⇒ no candidate is judged and nothing is discarded."""
    monkeypatch.delenv(vgm.ENV_FLAG, raising=False)
    out = vg.vet_candidates(_entity_list("#4118", "9bce8d31f"))
    assert out["stats"]["mechanical"]["enabled"] is False
    assert out["stats"]["mechanical"]["fired"] == 0
    assert _outcomes(out) == {vg.KEEP}
    assert out["stats"]["discarded"] == 0


@pytest.mark.parametrize("raw", ["0", "false", "no", ""])
def test_flag_falsey_values_are_off(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv(vgm.ENV_FLAG, raw)
    assert vgm.value_gate_enabled() is False


# ── the flag on: discard, rule id, counterfactual, stats ──────────────────


def test_flag_on_discards_and_records_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    """Safeguards 2 and 3 — the rule id is recorded and the counterfactual is
    recoverable, so a discard is never silent."""
    monkeypatch.setenv(vgm.ENV_FLAG, "1")
    out = vg.vet_candidates(_entity_list("#4118", "the owner"))

    (discarded,) = _decisions_for(out, "#4118")
    assert discarded["outcome"] == vg.DISCARD
    assert discarded["rule_id"] == vgm.RULE_REFERENCE
    assert discarded["reason"]
    assert discarded["counterfactual"], "no counterfactual ⇒ no recovery path"

    (kept,) = _decisions_for(out, "the owner")
    assert kept["outcome"] == vg.KEEP

    assert out["stats"]["mechanical"]["enabled"] is True
    assert out["stats"]["mechanical"]["fired"] == 1
    assert out["stats"]["mechanical"]["by_rule"][vgm.RULE_REFERENCE] == 1
    assert out["stats"]["discarded"] == 1


def test_removal_reaches_the_list_and_is_not_reminted(monkeypatch: pytest.MonkeyPatch) -> None:
    """The DISCARD must survive ``apply_vet`` — the payload is the artifact."""
    monkeypatch.setenv(vgm.ENV_FLAG, "1")
    el = _entity_list("#4118", "the owner")
    out = vg.vet_candidates(el, narrative="")
    new_list, _warnings = vg.apply_vet(el, out["decisions"])
    names = {e.get("name") for e in new_list["entities"]}
    assert "#4118" not in names
    assert "the owner" in names


def test_referenced_entity_survives_a_mechanical_discard(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Layer-1 guard must fire for a MECHANICAL discard, not just an
    arbiter's. A referenced entity removed would break
    ``about_entities ⊆ entities`` and 422 the whole session."""
    monkeypatch.setenv(vgm.ENV_FLAG, "1")
    el = {
        "entities": [{"name": "#4118", "kind": "core:other"}],
        "points": [
            {
                "content": "The decision turned on it, so it is kept.",
                "pointKind": "statement",
                "about_entities": ["#4118"],
            }
        ],
        "events": [],
        "operators": [],
    }
    out = vg.vet_candidates(el, narrative="")
    # the predicate DID fire on the bare-identifier entity...
    assert out["decisions"]["entities:0:#4118"]["outcome"] == vg.DISCARD
    new_list, warnings = vg.apply_vet(el, out["decisions"])
    # ...and the referential guard kept it anyway, loudly.
    names = {e.get("name") for e in new_list["entities"]}
    assert "#4118" in names
    assert any("referenced by a surviving candidate" in w for w in warnings)
    for point in new_list["points"]:
        assert set(point.get("about_entities") or []) <= names


# ── the composition: arbiter overrides, predicate failure fails open ──────


def test_arbiter_overrides_the_mechanical_discard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Necessary, not sufficient — the adversarial half can rescue a discard."""
    monkeypatch.setenv(vgm.ENV_FLAG, "1")

    def _keep(cands, _story):
        return {"verdicts": [{"id": c["id"], "outcome": vg.KEEP} for c in cands]}

    out = vg.vet_candidates(_entity_list("#4118"), narrative="", arbiter=_keep)
    assert _outcomes(out) == {vg.KEEP}
    assert out["decisions"]["entities:0:#4118"]["rule_id"] != vgm.RULE_REFERENCE
    # An overridden hit must NOT be reported as a fired discard — otherwise the
    # evidence surface attributes a discard to a rule that produced none.
    assert out["stats"]["mechanical"]["fired"] == 0
    assert out["stats"]["mechanical"]["overridden"] == 1
    assert out["stats"]["mechanical"]["by_rule"][vgm.RULE_REFERENCE] == 0
    assert out["stats"]["discarded"] == 0


def test_predicate_failure_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """A raising predicate keeps every candidate AND records the failure."""
    monkeypatch.setenv(vgm.ENV_FLAG, "1")

    def _boom(_text):
        raise RuntimeError("boom")

    monkeypatch.setattr(vgm, "mechanical_verdict", _boom)
    out = vg.vet_candidates(_entity_list("#4118", "9bce8d31f"))
    assert any("value gate failed" in w for w in out["warnings"])
    assert _outcomes(out) == {vg.KEEP}
    assert out["stats"]["mechanical"]["fired"] == 0


def test_predicate_is_a_leaf_module() -> None:
    """The predicate must not import the pipeline — both ends import it.

    Parsed with ``ast``, not string-matched: ``from . import vet_gate`` and
    ``from tortoise import vet_gate`` both satisfy a literal-substring check
    while creating exactly the cycle this pin exists to forbid.
    """
    import ast

    tree = ast.parse((ROOT / "tortoise" / "value_gate.py").read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                imported.add("." * node.level + (node.module or ""))
            else:
                imported.add(node.module or "")
    assert imported <= {"__future__", "os", "re", ".env_truthy"}, (
        f"value_gate.py gained an import beyond its leaf set: {imported}"
    )


# ── drift: the policy still lives in the pack, not the engine ─────────────


def test_rules_are_declared_by_their_source() -> None:
    """Every rule id names the declaration it implements, and that declaration
    still exists. A rule that stops being declared stops being justified.

    ⚠️ **A bare substring check is too weak** (the review's finding): a generic
    phrase like the original ``process logistics`` named no path class at all
    yet passed, because it happened to occur somewhere in a large file. So the
    pack is read **inside its ``memory_granularity`` scalar** and a non-pack
    source must carry ``declares`` and its ``anchor`` on the **same line**.
    """
    import re as _re

    for rule_id, decl in vgm.DECLARED_CLASS.items():
        source = ROOT / decl["source"]
        text = source.read_text()
        if decl["source"].startswith("packs/"):
            (value,) = _re.findall(r"memory_granularity:\s*'([^']*)'", text, _re.DOTALL) or [""]
            assert decl["declares"].lower() in value.lower(), (
                f"{rule_id} implements {decl['declares']!r}, which is not in "
                f"{decl['source']}'s memory_granularity — the predicate has "
                "outlived its policy"
            )
            continue
        anchor = decl.get("anchor")
        assert anchor, f"{rule_id} names a non-pack source without an anchor"
        lines = [
            ln
            for ln in text.splitlines()
            if decl["declares"].lower() in ln.lower() and anchor.lower() in ln.lower()
        ]
        assert lines, (
            f"{rule_id} declares {decl['declares']!r}, but no line of "
            f"{decl['source']} carries it together with {anchor!r} — the "
            "phrase alone is not a declaration"
        )


def test_rules_tuple_covers_every_emittable_id() -> None:
    assert set(vgm.RULES) == set(vgm.DECLARED_CLASS)
    assert len(vgm.RULES) == len(set(vgm.RULES))


@pytest.mark.parametrize("text", ["CI/CD//ab.py", "I/O//i.id", "src/main//ab.py"])
def test_a_dotless_slash_pair_is_not_laundered_by_a_neighbour(text: str) -> None:
    """The dotted segment must be *inside* the match (review cycle 3, P2).

    A start-anchored lookahead scans the whole whitespace-free run, so
    ``CI/CD//ab.py`` satisfied it from ``ab.py`` and still discarded the
    dotless ``CI/CD`` — a residue of the cycle-2 P1 class.
    """
    assert vgm.identifier_only(text) is None, (
        f"{text!r} was discarded by laundering a dotless slash-pair past the "
        "path pattern via a neighbouring dotted token"
    )


def test_reference_before_hash_would_invert_the_verdict() -> None:
    """The strip order is load-bearing for HASH vs REFERENCE too (cycle 3, P2).

    ``REFERENCE`` first takes only ``#1234`` and leaves ``abcd`` as a content
    word, so the same candidate flips from DISCARD to KEEP.
    """
    assert vgm.identifier_only("#1234abcd") == vgm.RULE_HASH


def test_value_gate_flag_switches_the_vet_pass_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Safeguard 1 requires the flag to be usable ALONE (review cycle 4).

    The predicate lives inside ``vet_gate.vet_candidates``, which only runs
    inside ``extractor_v2._run_vet_pass``, which only runs when ``vet_enabled``.
    So ``TORTOISE_VALUE_GATE`` must be folded into that expression, or an
    operator using the flag to *measure the rate effect* sees zero firings and
    wrongly concludes the predicate never fires.

    Pinned at the source, because ``_run_vet_pass`` needs a live extraction; the
    behavioural half (the flag alone ⇒ ``vet_candidates`` discards) is covered
    by ``test_flag_on_discards_and_records_provenance``.
    """
    import re as _re

    src = (ROOT / "tortoise" / "extractor_v2.py").read_text()
    assert "def _value_gate_enabled" in src, (
        "the flag helper is gone — the subordination documented in the "
        "docstring would be unenforced"
    )
    match = _re.search(r"vet_enabled\s*=\s*\(", src)
    assert match, "the vet_enabled assignment changed shape — re-pin it"
    # A window, not a balanced-paren group: the expression contains calls, so
    # ``[^)]*`` stops inside ``_value_gate_enabled(`` and the parenthesis the
    # assertion needs is cut off.
    window = src[match.start() : match.start() + 300]
    assert "_value_gate_enabled()" in window, (
        "TORTOISE_VALUE_GATE is no longer folded into vet_enabled, so it is "
        f"inert on its own: {window.strip()!r}"
    )

    # The text pin above catches a DELETED term; these catch a NEUTERED helper,
    # which is the actual inert-flag bug this test exists to prevent.
    from tortoise import extractor_v2 as v2

    monkeypatch.delenv(vgm.ENV_FLAG, raising=False)
    assert v2._value_gate_enabled() is False
    monkeypatch.setenv(vgm.ENV_FLAG, "1")
    assert v2._value_gate_enabled() is True


#: Documented **fail-open** misses — recorded here so they are a decision rather
#: than a silent gap. Each is a real path the predicate keeps:
#:   * ``a//b.py`` — relaxing the segment class to ``/+`` re-opens the cycle-3 P2
#:     (``CI/CD//ab.py``), which is a false POSITIVE; a fail-open miss costs
#:     noise, a false positive costs memory. Keep the miss.
#:   * ``src\main.py`` — backslash paths are outside the adopted class.
#:   * ``a/b.py/c`` — a dot in a *middle* segment only.
#:   * single-letter extensions (``a.c``) — the ≥2-letter rule is what rejects
#:     the abbreviation ``Ph.D``.
#:   * single-character stems (``x.py``, ``a.md``, ``1.py``) — the ``≥2 chars
#:     before the dot`` guard rejects ``e.g`` / ``i.e.`` / ``U.S.`` / ``a.m.``,
#:     which is worth more than the rare one-character filename.
KNOWN_FAIL_OPEN = [
    "a//b.py",
    "src\\main.py",
    "a/b.py/c",
    "a.c",
    "x.h",
    "x.py",
    "a.md",
    "1.py",
    # ── the inner-numeric-segment lookahead (the cycle-6 P1 fix) also keeps a
    # genuine-looking dated/release filename. Kept, not chased: the same
    # lookahead is what stops `10.0.0.beta` being discarded as a path, and a
    # fail-open miss costs noise while that false positive costs memory.
    "setup.3.py",
    "test.2.md",
    "release.1.txt",
    "report.2026.md",
]


@pytest.mark.parametrize("text", KNOWN_FAIL_OPEN)
def test_documented_fail_open_misses(text: str) -> None:
    """Fail-open is the safe direction, so these are pinned as *expected*."""
    assert vgm.identifier_only(text) is None


#: Accepted **false positives** on a BARE token. Each is dotted-token-shaped and
#: so is read as a path, and each is pinned here as a known consequence rather
#: than left for a reviewer to rediscover:
#:   * ``ab.cd.ef`` — indistinguishable from ``index.d.ts`` without a declared
#:     extension vocabulary; a bare token, so it can never carry a claim.
#:   * ``Mr.Smith`` / ``no.de`` / ``premiselabs.co`` — an abbreviation or a
#:     domain. In a claim-bearing row the residue blocks the rule
#:     (``the fix is in src/main.py`` → ``None``), so the exposure is a bare
#:     fragment, and a bare fragment is what this predicate exists to drop.
#:   * ``10.beta`` / ``10.rc`` / ``10.final`` — a version with only ONE numeric
#:     segment escapes the lookahead, whose discriminant is an *inner*
#:     ``.digits.``. Every claim-bearing variant returns ``None`` (``the chosen
#:     target is 10.beta``, ``the freeze is 10.beta``), because a content word
#:     puts ``10.beta`` in the residue — only rows made of closed-class words
#:     (the module's decided definition of "carries no claim") reach it.
ACCEPTED_BARE_FP = [
    "ab.cd.ef",
    "Mr.Smith",
    "no.de",
    "premiselabs.co",
    "10.beta",
    "v1.beta",
    "10.rc",
    "10.final",
]


@pytest.mark.parametrize("text", ACCEPTED_BARE_FP)
def test_accepted_bare_token_false_positives(text: str) -> None:
    """Recorded, not chased: a bare fragment with no claim attached."""
    assert vgm.identifier_only(text) == vgm.RULE_PATH
