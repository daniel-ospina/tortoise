"""Acceptance tests for the PR-side SIGNATURE producer and the ``decide`` CLI
(tortoise #3756, Step A + Step B).

The wire shape under test is not invented: it is the real ``gh run view
--log-failed`` capture measured on tortoise run 35223536174 (python-ci.yml,
2026-09-17). Each test names the MUTATION that must RED it — a test whose
mutation cannot fail it is not a test.

Threat surface (same as ``test_ci_exemption.py`` — the exemption face): the
producer's failures are all in the fail-OPEN direction, because a signature the
parser DROPS makes the PR's signature set smaller and therefore *easier* to
contain in main's:

* a per-run interpolated id left in the signature → the PR never matches main →
  every run BLOCKS forever (fail-closed, a cost — but the thing #3749 needs
  fixed);
* a signature stored BLANK or low-entropy → the subset rule becomes vacuous →
  a genuinely-new failure is exempted (fail-OPEN);
* an id that enters the producer's output without a signature, or leaves the
  wire parser by a rejection path, silently vanishes from the decision.
"""

from __future__ import annotations

import pytest

from tools.ci_exemption import (
    Decision,
    Failure,
    Rate,
    decide,
    main,
    normalize_signature,
    parse_failure_rows,
    parse_pr_failure_text,
    parse_rotation_runs,
    parse_signature_rows,
    render_signature_table,
)

ID = "tests/test_oauth_token_fault.py::test_capture_exception_raising_does_not_break_the_typed_error"
DR = "tests/dr_endpoints.py::TestDrDrill::test_dr_restores_to_scratch"

_GH_JOB = "test (a)"
_GH_STEP = "Run fast test suite (slow files run in the test-slow job)"


def _log(body: str, *, ansi: bool = False) -> str:
    """Wrap plain pytest output in the real gh ``--log-failed`` envelope.

    Byte-for-byte the measured shape: ``<job>\\t<step>\\t\\ufeff<ISO>Z <line>``
    (BOM on the first line only), with optional SGR escapes inside the content.
    """
    out: list[str] = []
    for i, line in enumerate(body.splitlines()):
        ts = f"2026-09-17T13:10:44.17{i:05d}Z"
        bom = "\ufeff" if i == 0 else ""
        payload = f"\x1b[36;1m{line}\x1b[0m" if (ansi and line.strip()) else line
        out.append(f"{_GH_JOB}\t{_GH_STEP}\t{bom}{ts} {payload}")
    return "\n".join(out) + "\n"


_OAUTH_BODY = f"""\
=================================== FAILURES ===================================
________ test_capture_exception_raising_does_not_break_the_typed_error _________

    def test_capture_exception_raising_does_not_break_the_typed_error(monkeypatch, fault_client):
>       assert r.status_code == 503 and r.json()["error"] == "te
E       assert (200 == 503)
E        +  where 200 = <Response [200 OK]>.status_code

{ID.partition("::")[0]}:779: AssertionError
=========================== short test summary info ============================
FAILED {ID} - assert (200 == 503)
"""


def _drill_body(hex_suffix: str, *, exc: str = "RuntimeError") -> str:
    return f"""\
=================================== FAILURES ===================================
________ test_dr_restores_to_scratch ________

E       {exc}: Drill failed: Restore swap failed - verified temp graph _drill_..._{hex_suffix} intact: I/O operation on closed file.

tests/dr_endpoints.py:412: {exc}
=========================== short test summary info ============================
FAILED {DR}
"""


def _assert_body(left: str, right: str) -> str:
    return f"""\
=================================== FAILURES ===================================
________ test_dr_restores_to_scratch ________

>       assert restored == expected
E       assert {left} == {right}

tests/dr_endpoints.py:88: AssertionError
=========================== short test summary info ============================
FAILED {DR} - assert {left} == {right}
"""


# ── Step A · the signature producer ───────────────────────────────────────


def test_signature_extraction_matches_the_real_gh_log_shape():
    """THE grounding test: the measured artifact parses to the measured signature.

    MUTATION: make ``_strip_log_prefix`` return the line unchanged → the ``E``
    and trailer lines keep the ``<job>\\t<step>\\t<ts> `` prefix, match nothing,
    and the id falls back to the bare summary detail (or goes unsigned) → the
    exact-string assertion REDs.
    MUTATION: drop the ``_ANSI_RE.sub`` in ``parse_pr_failure_text`` → the
    trailer ``path.py:779: AssertionError`` is wrapped in SGR escapes, so the
    exception TYPE is lost and the signature degrades to ``assert (200 == 503)``
    → the exact-string assertion REDs.
    """
    parsed = parse_pr_failure_text(_log(_OAUTH_BODY, ansi=True))

    assert parsed.ids == [ID]
    assert parsed.signatures[ID] == frozenset({"AssertionError: assert (200 == 503)"})
    assert parsed.rejected == []
    assert parsed.unsigned == []


def test_per_run_interpolated_identifier_does_not_change_the_signature():
    """The declared class: ``_drill_..._416bf7c5`` varies per run, the failure does not.

    MUTATION: remove the ``<HEX>`` normalizer from ``_VOLATILE_NORMALIZERS`` →
    the two captures yield two different signatures → the equality REDs (and
    with it every exemption for the #3749 substrate family).
    """
    a = parse_pr_failure_text(_log(_drill_body("416bf7c5")))
    b = parse_pr_failure_text(_log(_drill_body("aa11bb22")))

    assert a.signatures[DR] == b.signatures[DR]
    assert a.signatures[DR] == frozenset(
        {
            "RuntimeError: Drill failed: Restore swap failed - verified temp graph "
            "_drill_..._<HEX> intact: I/O operation on closed file."
        }
    )


def test_distinct_assertions_do_not_collapse_into_one_signature():
    """Over-normalising is the fail-OPEN direction — guard it explicitly.

    MUTATION: add a generic digit/identifier mask (e.g. ``re.sub(r"\\d+", "<N>")``)
    to ``_VOLATILE_NORMALIZERS`` → both assertions collapse to one signature,
    which is exactly how the subset rule would start exempting a failure the PR
    introduced → the inequality REDs.
    """
    three = parse_pr_failure_text(_log(_assert_body("3", "2")))
    four = parse_pr_failure_text(_log(_assert_body("4", "2")))

    assert three.signatures[DR] != four.signatures[DR]
    assert three.signatures[DR] == frozenset({"AssertionError: assert 3 == 2"})


def test_unsigned_id_is_reported_and_never_stored_blank():
    """An id with no signature is EVIDENCE, never an empty signature.

    MUTATION: store ``frozenset({""})`` (or ``""``) for the id → the
    ``"" not in`` assertion and the ``unsigned`` assertion RED — and in the real
    gate a blank signature is what makes the subset rule vacuous.
    """
    parsed = parse_pr_failure_text(_log(f"FAILED {DR}\n"))

    assert parsed.ids == [DR]
    assert DR not in parsed.signatures
    assert parsed.unsigned == [DR]
    for sigs in parsed.signatures.values():
        assert "" not in sigs


def test_a_failed_line_with_a_bad_payload_is_rejected_and_counted():
    """``FAILED may`` is not a failure record — and must not enter the id set.

    MUTATION: remove the ``_NODEID_LOOSE_RE`` guard → ``may`` enters ``ids`` and
    ``rejected`` is empty → RED.
    """
    parsed = parse_pr_failure_text(_log(f"FAILED may\nFAILED {DR}\n"))

    assert parsed.ids == [DR]
    assert parsed.rejected == ["FAILED may"]


def test_a_param_nodeid_with_a_space_is_accepted_not_dropped():
    """Real pytest params contain spaces; dropping the id would be fail-OPEN.

    MUTATION: use the narrow ``_NODEID_RE`` here instead of ``_NODEID_LOOSE_RE``
    → the nodeid is rejected, the id disappears from the decision entirely, and
    an undecided id is an unblocked id → RED.
    """
    nid = "tests/e2e/test_x.py::test_a[chromium-Claude Desktop]"
    parsed = parse_pr_failure_text(_log(f"FAILED {nid}\n"))

    assert parsed.ids == [nid]
    assert parsed.rejected == []


def test_an_unmatched_failures_block_is_counted_not_guessed():
    """A block that joins to no id is a rejection, not an attribution.

    MUTATION: attribute every unjoined block to the first/only known id → the
    block's signature is invented onto an id that never had it, and
    ``unattributed`` is empty → RED.
    """
    body = (
        "=================================== FAILURES ===================================\n"
        "________ test_ghost ________\n"
        "\n"
        "E       AssertionError: nowhere\n"
        "\n"
        "tests/test_other.py:9: AssertionError\n"
        "=========================== short test summary info ============================\n"
        f"FAILED {DR}\n"
    )
    parsed = parse_pr_failure_text(_log(body))

    assert parsed.unattributed == ["test_ghost"]
    assert DR not in parsed.signatures, "no signature may be invented by position"
    assert parsed.unsigned == [DR]


def test_summary_detail_is_the_fallback_when_no_block_exists():
    """The summary line's own payload is used only when no block joined.

    MUTATION: drop the ``details`` fallback in ``parse_pr_failure_text`` → the id
    goes ``unsigned`` and the non-empty-``signatures`` assertion REDs.
    """
    parsed = parse_pr_failure_text(_log(f"FAILED {DR} - assert 3 == 2\n"))

    assert parsed.signatures[DR] == frozenset({"assert 3 == 2"})
    assert parsed.unsigned == []


def test_block_signature_wins_and_only_one_form_is_stored_per_id():
    """One id contributes ONE form — the block's, which carries the exception TYPE.

    MUTATION: union the block signature with the summary detail →
    ``len(signatures[ID]) == 2`` and the set-equality REDs; the competing forms
    would also make ``pr <= main`` fail against a main side that saw only one.
    """
    parsed = parse_pr_failure_text(_log(_OAUTH_BODY))

    assert parsed.signatures[ID] == frozenset({"AssertionError: assert (200 == 503)"})
    assert len(parsed.signatures[ID]) == 1


def test_normalize_signature_collapses_whitespace():
    """The same assertion is rendered at different prefix widths between runs.

    MUTATION: delete the ``" ".join(str(text).split())`` collapse → the two
    strings differ → RED.
    """
    assert normalize_signature("assert   3   ==   2") == "assert 3 == 2"


# ── Step B · the wire parser ──────────────────────────────────────────────


def test_failure_rows_parse_and_union_signatures_for_duplicate_ids():
    """Two rows for one id are how a multi-signature id is expressed.

    MUTATION: let the last row win instead of unioning signatures → only one
    signature remains → RED.
    """
    r = parse_failure_rows(
        f"{DR}\t4\t8\tAssertionError: assert 3 == 2\n"
        f"{DR}\t4\t8\tConnectionError: socket gone\n"
    )

    assert r.failures[DR].rate == Rate(4, 8)
    assert r.failures[DR].signatures == frozenset(
        {"AssertionError: assert 3 == 2", "ConnectionError: socket gone"}
    )
    assert r.rejected == []


def test_a_blank_signature_column_is_dropped_and_counted():
    """The rate survives; the blank signature does NOT become an entry.

    MUTATION: store the blank signature (``frozenset({""})``) → the ``unsigned``
    assertion and the ``"" not in`` assertion RED.
    """
    r = parse_failure_rows(f"{DR}\t1\t1\t\n")

    assert r.failures[DR].rate == Rate(1, 1)
    assert r.failures[DR].signatures == frozenset()
    assert r.unsigned == [DR]


def test_conflicting_duplicate_rows_leave_the_id_blockable():
    """A self-contradicting table must not make the id VANISH.

    MUTATION: ``failures.pop(nodeid)`` on conflict (mirroring ``parse_rates``) →
    the id is absent from ``pr_failures``, so :func:`decide` never blocks it and
    a contradicted failure ships → both the presence assertion and the
    ``decide`` block assertion RED.
    """
    r = parse_failure_rows(
        f"{DR}\t8\t8\tAssertionError: assert 3 == 2\n"
        f"{DR}\t0\t8\tAssertionError: assert 3 == 2\n"
    )

    assert DR in r.failures, "a contradicted id must not vanish from the decision"
    assert r.failures[DR].rate.runs == 0, "unmeasurable rate → the existing BLOCK path"
    assert len(r.rejected) == 1

    d = decide(
        r.failures,
        {DR: Rate(4, 8)},
        main_signatures={DR: frozenset({"AssertionError: assert 3 == 2"})},
    )
    assert d.any_blocked, "an unmeasurable PR rate is never exempt"


def test_signature_rows_and_rotation_runs_parse_their_formats():
    """Main signatures are ``<nodeid>\\t<sig>``; a rotation run is one line of ids.

    MUTATION: split rotation input on "::" or treat each id as its own run → the
    three-run shape collapses and the assertion REDs.
    """
    sigs = parse_signature_rows(f"{DR}\tAssertionError: assert 3 == 2\n\n")
    assert sigs == {DR: frozenset({"AssertionError: assert 3 == 2"})}

    runs = parse_rotation_runs(f"{DR} tests/other.py::t\ntests/other.py::t\n")
    assert runs == [frozenset({DR, "tests/other.py::t"}), frozenset({"tests/other.py::t"})]


def test_render_signature_table_is_the_documented_wire_form():
    """MUTATION: emit ``nodeid sig`` (space) instead of a tab → the split
    assertion REDs, and the shell's join would silently produce a bad row."""
    out = render_signature_table({DR: frozenset({"AssertionError: x"})})
    assert out == f"{DR}\tAssertionError: x\n"


# ── Step B · the CLI ──────────────────────────────────────────────────────


def _write(tmp_path, name: str, text: str):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_cli_decide_blocks_and_writes_the_residual(tmp_path, capsys):
    """The shell's residual is the decision's BLOCKED set, not a subtraction.

    MUTATION: return 0 unconditionally → the exit assertion REDs.
    MUTATION: skip ``--blocked-out`` → the residual-file assertion REDs.
    """
    pr = _write(tmp_path, "pr.txt", f"{DR}\t8\t8\tAssertionError: assert 3 == 2\n")
    mainf = _write(tmp_path, "main.txt", f"{DR}\t1\t8\n")
    msig = _write(tmp_path, "msig.txt", f"{DR}\tAssertionError: assert 3 == 2\n")
    blocked = tmp_path / "blocked.txt"
    verdict = tmp_path / "verdict.txt"

    rc = main(
        [
            "decide", "--pr-failures", pr, "--main-rates", mainf,
            "--main-signatures", msig,
            "--blocked-out", str(blocked), "--verdict-out", str(verdict),
        ]
    )
    out = capsys.readouterr().out

    assert rc == 1
    assert blocked.read_text(encoding="utf-8") == f"{DR}\n"
    assert verdict.read_text(encoding="utf-8").startswith("VERDICT\tBLOCK")
    assert "blocked=1" in out


def test_cli_decide_exempts_with_both_rates_visible(tmp_path, capsys):
    """An exemption must be RECORDED with both rates — never an absence.

    MUTATION: write the EXEMPT line without the measured rates → the ``4/8``
    assertion REDs; a green with no recorded exemption is the fail-open defect.
    """
    pr = _write(tmp_path, "pr.txt", f"{DR}\t4\t8\tAssertionError: assert 3 == 2\n")
    mainf = _write(tmp_path, "main.txt", f"{DR}\t4\t8\n")
    msig = _write(tmp_path, "msig.txt", f"{DR}\tAssertionError: assert 3 == 2\n")
    exempt = tmp_path / "exempt.txt"

    rc = main(
        [
            "decide", "--pr-failures", pr, "--main-rates", mainf,
            "--main-signatures", msig, "--exempt-out", str(exempt),
        ]
    )
    out = capsys.readouterr().out

    assert rc == 0
    line = exempt.read_text(encoding="utf-8")
    assert line.startswith("EXEMPT:")
    assert "main 4/8" in line and "PR 4/8" in line
    assert "VERDICT\tCLEAN" in out


def test_cli_decide_rotation_is_unattributable_and_gates(tmp_path, capsys):
    """A rotating identity gates but is reported as UNATTRIBUTABLE, not blocked.

    MUTATION: treat ``unattributable`` as non-gating (``return 0``) → the exit
    assertion REDs; MUTATION: fold it into ``blocked`` → the
    ``--unattributable-out`` assertion REDs.
    """
    cls = "tests/dr_endpoints.py::TestDrDrillScheduled"
    a = f"{cls}::test_manual_drill_records_measured_time"
    b = f"{cls}::test_status_surfaces_last_drill"
    pr = _write(tmp_path, "pr.txt", f"{a}\t1\t8\tAssertionError: assert 3 == 2\n")
    mainf = _write(tmp_path, "main.txt", f"{a}\t1\t8\n")
    msig = _write(tmp_path, "msig.txt", f"{a}\tAssertionError: assert 3 == 2\n")
    rot = _write(tmp_path, "rot.txt", f"{a} {b}\n{b}\n{b}\n")
    un = tmp_path / "un.txt"

    rc = main(
        [
            "decide", "--pr-failures", pr, "--main-rates", mainf,
            "--main-signatures", msig, "--rotation", rot,
            "--unattributable-out", str(un),
        ]
    )
    out = capsys.readouterr().out

    assert rc == 1, "a rotating identity must gate"
    assert un.read_text(encoding="utf-8") == f"{a}\n"
    assert "unattributable=1" in out
    assert "UNATTRIBUTABLE" in out


def test_cli_decide_without_main_signatures_fails_closed_and_says_so(tmp_path, capsys):
    """No main signature evidence = every signature check BLOCKS, visibly.

    MUTATION: default ``main_sigs`` to the PR's own signatures → the PR is
    exempted with no main-side evidence → the exit assertion REDs.
    MUTATION: drop the explanatory note → the note assertion REDs, and the
    safe-but-wrong state becomes indistinguishable from a real block.
    """
    pr = _write(tmp_path, "pr.txt", f"{DR}\t4\t8\tAssertionError: assert 3 == 2\n")
    mainf = _write(tmp_path, "main.txt", f"{DR}\t4\t8\n")

    rc = main(["decide", "--pr-failures", pr, "--main-rates", mainf])
    out = capsys.readouterr().out

    assert rc == 1
    assert "signature differs from main's" in out
    assert "no --main-signatures supplied" in out


def test_cli_signatures_emits_the_wire_table(tmp_path, capsys):
    """The producer's stdout is the shell-joinable table.

    MUTATION: print nothing (or an unnormalized line) → the row assertion REDs.
    """
    log = _write(tmp_path, "log.txt", _log(_OAUTH_BODY, ansi=True))

    rc = main(["signatures", "--log", log])
    captured = capsys.readouterr()

    assert rc == 0
    assert captured.out == f"{ID}\tAssertionError: assert (200 == 503)\n"
    assert "signed=1" in captured.err


def test_cli_signatures_fails_closed_when_nothing_parses(tmp_path, capsys):
    """A capture with no usable id exits non-zero — an unreadable set is not empty.

    MUTATION: return 0 when ``parsed.ok`` is False → RED (a vacuous producer would
    let the shell build a PR set with no ids, i.e. nothing to gate).
    """
    log = _write(tmp_path, "log.txt", _log("some prose\nanother line\n"))

    rc = main(["signatures", "--log", log])

    assert rc == 1


def test_cli_requires_a_subcommand():
    """MUTATION: ``add_subparsers(required=False)`` → no SystemExit → RED."""
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_cli_rejects_an_unknown_subcommand():
    """MUTATION: a catch-all that accepts any argv → no SystemExit → RED."""
    with pytest.raises(SystemExit) as exc:
        main(["exempt-everything"])
    assert exc.value.code == 2


def test_decision_type_is_importable_for_the_cli():
    """A tiny guard that the CLI's own types stay exported (and that the
    ``Decision``/``Failure`` names the CLI builds are the same ones the tests
    drive). MUTATION: rename ``Decision`` → AttributeError → RED."""
    assert isinstance(decide({}, {}), Decision)
    assert Failure(rate=Rate(0, 0)).signatures == frozenset()
