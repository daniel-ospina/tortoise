"""The published client-boundary STATUS VOCABULARY contract (#3805).

Roadmap §7 item 9 (ADOPTED 2026-09-17) adopts **one status vocabulary** at the
client boundary — ``available | empty | degraded | unconfigured`` — so that
*memory unavailable* is distinguishable from *memory empty*. This module is the
guard for that term set: it EXECUTES the real handler (``vocabulary.classify`` /
``vocabulary.resolve`` / the shipped ``tortoise-client status`` probe) for each
condition and asserts the term. A source-text scan would be defeated by an edit
that changes nothing observable; executing the path cannot.

The load-bearing property, asserted in both directions below: ``empty`` (the
store answered and had nothing) is never reported as ``degraded`` or
``unconfigured`` (the store did not answer), and neither failure is ever
reported as a successful empty result. ``degraded`` (an outage) and
``unconfigured`` (a set-up gap) are never reported as each other either.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CLIENT_DIR = REPO_ROOT / "client"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Bind the CANONICAL driver FIRST, then load the client shim from `client/`.
# Inserting `client/` shadows the engine `tortoise` package while it is on the
# path (the client dir carries its own `tortoise/` namespace), so the driver has
# to be cached in sys.modules first — mirrors tests/test_client_cli_probe.py.
import tortoise.mcp_client  # noqa: E402, F401
import tortoise.status_vocabulary as vocabulary  # noqa: E402

sys.path.insert(0, str(CLIENT_DIR))
try:
    from tortoise_client import cli
finally:
    sys.path.remove(str(CLIENT_DIR))

AVAILABLE = vocabulary.STATUS_AVAILABLE
EMPTY = vocabulary.STATUS_EMPTY
DEGRADED = vocabulary.STATUS_DEGRADED
UNCONFIGURED = vocabulary.STATUS_UNCONFIGURED


def _fake_status(word: str, **extra):
    return lambda: {"status": word, "url": "http://localhost:8000/mcp", **extra}


# ── The term set itself (machine-readable, consumed not re-derived) ──────────


class TestPublishedTermSet:
    def test_term_set_is_exactly_the_recorded_four(self):
        """Positive: the published set IS the adopted four."""
        assert set(vocabulary.CLIENT_STATUS_TERMS) == {
            "available",
            "empty",
            "degraded",
            "unconfigured",
        }
        assert len(vocabulary.CLIENT_STATUS_TERMS) == 4

    def test_no_fifth_term_was_coined(self):
        """Negative: a parallel term is how one contract becomes two."""
        published = set(vocabulary.CLIENT_STATUS_TERMS)
        assert published == set(vocabulary.CONDITIONS)
        for invented in (
            "ok",
            "unavailable",
            "tortoise_unavailable",
            "not_configured",
            "error",
            "skipped",
            "offline",
        ):
            assert invented not in published

    def test_every_term_names_its_condition(self):
        """Positive: the mapping is data, so each term answers 'what does it name?'."""
        for term in vocabulary.CLIENT_STATUS_TERMS:
            condition = vocabulary.CONDITIONS[term]
            assert isinstance(condition, str) and condition.strip()

    def test_no_legacy_word_survives_as_a_term(self):
        """Negative: the superseded words are aliases, never first-class terms."""
        assert set(vocabulary.LEGACY_WORDS) == {"ok", "not_configured"}
        assert not set(vocabulary.LEGACY_WORDS) & set(vocabulary.CLIENT_STATUS_TERMS)

    def test_flat_table_carries_only_words_it_can_answer(self):
        """Negative — the defect this pin exists for. `tortoise_unavailable`
        names two conditions (configured/down vs never-configured), so it must
        not be in a table that can only return one fixed value: a migrating
        caller reading it there would map an unset endpoint onto `degraded`
        (exit 3) instead of `unconfigured` (exit 4). It is published in the
        config-dependent set instead, and the two sets are disjoint."""
        assert "tortoise_unavailable" not in vocabulary.LEGACY_WORDS
        assert "tortoise_unavailable" in vocabulary.LEGACY_WORDS_NEEDING_CONFIGURATION
        assert not set(vocabulary.LEGACY_WORDS) & set(vocabulary.LEGACY_WORDS_NEEDING_CONFIGURATION)
        assert not set(vocabulary.LEGACY_WORDS_NEEDING_CONFIGURATION) & set(
            vocabulary.CLIENT_STATUS_TERMS
        )


# ── One term per condition — the real handler, positive AND negative ─────────


class TestConditionToTerm:
    def test_available_when_reached_with_content(self):
        """Positive: reached + content -> `available`."""
        assert vocabulary.classify(configured=True, reached=True, hits=3) == AVAILABLE

    def test_available_is_not_any_other_term(self):
        """Negative: `available` is distinct from each of the other three."""
        term = vocabulary.classify(configured=True, reached=True, hits=3)
        assert term != EMPTY
        assert term != DEGRADED
        assert term != UNCONFIGURED

    def test_empty_when_reached_without_content(self):
        """Positive: reached + nothing -> `empty` (its own term, not a success word)."""
        assert vocabulary.classify(configured=True, reached=True, hits=0) == EMPTY

    def test_empty_is_never_reported_as_unavailable(self):
        """Negative — THE LOAD-BEARING PROPERTY, direction 1. An empty store is
        not an outage and not a set-up gap, so it must never be reported as one."""
        assert vocabulary.classify(configured=True, reached=True, hits=0) != DEGRADED
        assert vocabulary.classify(configured=True, reached=True, hits=0) != UNCONFIGURED

    def test_unavailable_is_never_reported_as_empty(self):
        """Negative — THE LOAD-BEARING PROPERTY, direction 2. A failure to reach
        the store must never be laundered into a successful empty result."""
        assert vocabulary.classify(configured=True, reached=False) != EMPTY
        assert vocabulary.classify(configured=False, reached=False) != EMPTY

    def test_degraded_when_configured_but_unreachable(self):
        """Positive: `degraded` names EXACTLY one condition — the store is
        configured but could not be reached (off by outage)."""
        assert vocabulary.classify(configured=True, reached=False) == DEGRADED

    def test_degraded_is_neither_a_setup_gap_nor_empty(self):
        """Negative: an outage must not read as a set-up gap or as an empty store."""
        assert vocabulary.classify(configured=True, reached=False) != UNCONFIGURED
        assert vocabulary.classify(configured=True, reached=False) != EMPTY
        assert vocabulary.classify(configured=True, reached=False) != AVAILABLE

    def test_unconfigured_when_no_provider_key_is_configured(self):
        """Positive: **no provider key configured** is `unconfigured` — the term
        B6's read-path unit sits nearest, and the condition a fifth term would
        otherwise be coined for. A set-up gap, not an outage."""
        assert vocabulary.classify(configured=False, reached=False) == UNCONFIGURED

    def test_unconfigured_is_never_an_outage_or_an_empty_store(self):
        """Negative: a missing key must not blame the service, and must not look
        like a store that answered with nothing."""
        assert vocabulary.classify(configured=False, reached=False) != DEGRADED
        assert vocabulary.classify(configured=False, reached=False) != EMPTY
        assert vocabulary.classify(configured=False, reached=False) != AVAILABLE

    def test_a_probe_read_that_was_not_made_is_available(self):
        """A connectivity probe asks nothing about content (hits is None): the
        store answered, so the honest term is `available`, never `empty`."""
        assert vocabulary.classify(configured=True, reached=True) == AVAILABLE
        assert vocabulary.classify(configured=True, reached=True) != EMPTY


# ── The driver-word resolver the probe actually calls ───────────────────────


class TestDriverWordResolution:
    def test_reachable_driver_reports_available(self):
        assert vocabulary.resolve("ok", configured=True) == AVAILABLE

    def test_down_daemon_with_declared_endpoint_is_degraded(self):
        """The endpoint was declared, so a failure to answer is an OUTAGE."""
        assert vocabulary.resolve("tortoise_unavailable", configured=True) == DEGRADED

    def test_down_daemon_without_declared_endpoint_is_unconfigured(self):
        """Same driver word, different term — the endpoint was never declared,
        so the failure is a SET-UP gap. This split is the whole point."""
        assert vocabulary.resolve("tortoise_unavailable", configured=False) == UNCONFIGURED

    def test_config_dependent_word_cannot_be_answered_by_the_table_alone(self):
        """The migration path, asserted as BEHAVIOUR: the flat table has NO
        answer for `tortoise_unavailable`, and the same word resolves to two
        different terms depending on the configuration fact. Reading it as a
        table lookup is therefore impossible, not merely discouraged."""
        assert vocabulary.LEGACY_WORDS.get("tortoise_unavailable") is None
        unset = vocabulary.resolve("tortoise_unavailable", configured=False)
        declared = vocabulary.resolve("tortoise_unavailable", configured=True)
        assert unset == UNCONFIGURED and declared == DEGRADED
        assert unset != declared

    def test_legacy_not_configured_word_resolves_unconfigured(self):
        """Forward-compat: a driver that learns the split is believed."""
        assert vocabulary.resolve("not_configured", configured=False) == UNCONFIGURED
        assert vocabulary.resolve("not_configured", configured=True) == UNCONFIGURED

    def test_canonical_words_pass_through_untouched(self):
        for term in vocabulary.CLIENT_STATUS_TERMS:
            assert vocabulary.resolve(term, configured=True) == term

    def test_unknown_word_fails_loud_never_silently_available(self):
        """Negative: an unrecognised state degrades to the loud term; it must
        never be optimistically read as a success."""
        assert vocabulary.resolve("wat", configured=True) == DEGRADED
        assert vocabulary.resolve(None, configured=True) == DEGRADED
        assert vocabulary.resolve("wat", configured=True) != AVAILABLE
        assert vocabulary.resolve(None, configured=True) != EMPTY

    def test_resolver_never_invents_a_term(self):
        """Every branch lands inside the published set — a guard on the guard."""
        for word in (
            *vocabulary.CLIENT_STATUS_TERMS,
            *vocabulary.LEGACY_WORDS,
            *vocabulary.LEGACY_WORDS_NEEDING_CONFIGURATION,
            "wat",
            None,
            "",
        ):
            for configured in (True, False):
                assert (
                    vocabulary.resolve(word, configured=configured)
                    in vocabulary.CLIENT_STATUS_TERMS
                )


# ── The shipped probe, executed end to end ──────────────────────────────────


class TestProbeExecutesTheContract:
    def test_reachable_probe_reports_available_exit_zero(self, monkeypatch, capsys):
        monkeypatch.setenv("TORTOISE_MCP_URL", "http://localhost:8000/mcp")
        monkeypatch.setattr(cli, "status", _fake_status("ok", tools=7))
        assert cli.main(["status"]) == 0
        assert json.loads(capsys.readouterr().out)["status"] == AVAILABLE

    def test_unreachable_probe_reports_degraded_exit_three(self, monkeypatch, capsys):
        monkeypatch.setenv("TORTOISE_MCP_URL", "http://127.0.0.1:1/mcp")
        monkeypatch.setattr(cli, "status", _fake_status("tortoise_unavailable", error="boom"))
        assert cli.main(["status"]) == 3
        assert json.loads(capsys.readouterr().out)["status"] == DEGRADED

    def test_never_configured_probe_reports_unconfigured_exit_four(self, monkeypatch, capsys):
        monkeypatch.delenv("TORTOISE_MCP_URL", raising=False)
        monkeypatch.setattr(cli, "status", _fake_status("tortoise_unavailable", error="boom"))
        assert cli.main(["status"]) == 4
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == UNCONFIGURED
        assert out["configured"] is False

    def test_a_reachable_probe_is_never_a_failure_at_the_boundary(self, monkeypatch, capsys):
        """Negative: the reachable path can never emit the failure terms (the
        collapse #3832/D5 removed, pinned here so it cannot come back)."""
        monkeypatch.setenv("TORTOISE_MCP_URL", "http://localhost:8000/mcp")
        monkeypatch.setattr(cli, "status", _fake_status("ok", tools=0))
        assert cli.main(["status"]) == 0
        assert json.loads(capsys.readouterr().out)["status"] not in {DEGRADED, UNCONFIGURED}

    def test_non_zero_exit_is_reserved_for_the_two_failure_terms(self):
        """The exit-code contract maps onto the vocabulary: the two success
        terms share 0, and only the two failure terms leave it."""
        assert cli._exit_code(AVAILABLE) == 0
        assert cli._exit_code(EMPTY) == 0
        assert cli._exit_code(DEGRADED) == 3
        assert cli._exit_code(UNCONFIGURED) == 4
        assert cli._exit_code("wat") == 3  # fail loud, never silently success
