"""#10146 — the agent-finding form must produce a body the provenance gate accepts.

The form (``.github/ISSUE_TEMPLATE/agent_finding.yml``) and the gate
(``tools/finding_provenance.py``) are two halves of ONE contract, and neither
read the other:

* the form asked for a bare short sha in a field that was ``required: false``;
* the gate needs a line matching ``PROVENANCE_RE``
  (``Measured at: <ref>@<40-hex-sha> on <YYYY-MM-DD>``).

So a finding filed **through the intended form** could not satisfy the gate.
These tests pin the two halves to ONE source of truth: the gate's own
module-level regexes, imported — never copied. A test written against a copy of
the regex would drift exactly as the form did (the owner's implementation
constraint on the issue).

⛔ WHICH PARSER DECIDES WHICH SURFACE — the part the first reading of #10146
got wrong, corrected here because a docstring that asserts a consequence the
code does not produce is worse than no docstring. ``claims_finding()`` /
``PROVENANCE_CLAIM_RE`` is reached ONLY from ``--is-finding``, which the
workflow called ONLY for the COMMENT arm. The ISSUE-BODY arm classified with
``grep -qE '^Measured at:'`` — a column-0 shell copy of the predicate — and
``--validate`` uses ``PROVENANCE_RE`` alone. So the claim parser never decided
an issue body, and a rendered heading claim did **not** become
``FAIL UNKNOWN_PROVENANCE`` on the issue path: the grep did not see it, the
``bug``/``beta-feedback`` label fallbacks did not match (the form declared an
``internal`` label GitHub dropped, so the issue carried NO label at all), and
the body was **silently skipped** — never gated at all.
That fail-open is the real residual of #10146 and is closed in the workflow's
``decide`` step, which now classifies BOTH arms with ``--is-finding`` and
REFUSES a form-filed body carrying no parseable claim instead of skipping it
(pinned in ``tests/test_finding_provenance.py``).

⛔ THE KEY THAT REFUSAL USES — corrected (#10146 F1). The first repair keyed
the refusal on the label this form declared (``labels: ["internal"]``). That
label does **not exist** in this repo (measured 2026-11-05: 78 labels, none
named ``internal``), and GitHub **silently drops** a template label that does
not exist — so a form-filed issue arrived with NO label, the refusal never
fired, and the fail-open stayed open in production while the tests passed on an
injected label the form cannot deliver. The refusal is now keyed on what THIS
FILE delivers by itself and no repo state can remove:

* the hidden ``<!-- finding -->`` marker in the first ``markdown`` block, which
  the gate's own ``claims_finding()`` / ``FINDING_MARKER_RE`` already selects,
  and
* the ``title: "[finding]: "`` prefix, which GitHub applies at filing.

``test_the_form_ships_a_marker_the_gate_selects`` below is the anti-rot pin on
this half of the contract.

The rendering below mirrors what GitHub puts in the created issue body: a
``markdown`` block's ``value`` verbatim, and for each question field a
``### <label>`` heading followed by the answer (``_No response_`` when blank).
Field ``description`` text is UI-side hint, but it is checked too — the owner's
constraint on this issue is that no part of the form may ship a line the
*claim* parser selects, because the claim parser tolerates markdown prefixes
(``#``, ``>``, ``*``, ``-``) that a rendered heading supplies for free.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tools.finding_provenance import (
    PROVENANCE_CLAIM_RE,
    PROVENANCE_RE,
    claims_finding,
)

FORM = Path(__file__).resolve().parent.parent / ".github" / "ISSUE_TEMPLATE" / "agent_finding.yml"


def _load():
    return yaml.safe_load(FORM.read_text(encoding="utf-8"))


def _render_body(form) -> str:
    """What GitHub puts in the issue body when the form is used, unanswered."""
    lines: list[str] = []
    for item in form["body"]:
        kind = item.get("type")
        attrs = item.get("attributes") or {}
        if kind == "markdown":
            lines.append(str(attrs.get("value", "")))
            continue
        lines.append(f"### {attrs.get('label', '')}")
        lines.append("")
        if kind == "checkboxes":
            for opt in attrs.get("options") or []:
                lines.append(f"- [ ] {opt.get('label', '')}")
        else:
            lines.append(str(attrs.get("placeholder") or "_No response_"))
        lines.append("")
    return "\n".join(lines)


def test_rendered_form_never_ships_a_malformed_provenance_claim():
    """No line of the rendered body may be a ``Measured at`` CLAIM.

    The claim parser tolerates leading ``#``/``>``/``*``/``-``, so a field
    LABEL of ``Measured at …`` renders as ``### Measured at …`` — a claim the
    strict parser rejects, i.e. ``FAIL UNKNOWN_PROVENANCE`` wherever it IS
    classified.

    ⛔ Do NOT read this as "the form's label made the gate fail" (#10146's
    miscited root cause). It made the gate **skip** the body: the issue-body
    arm classified with a column-0 ``grep``, which cannot see a ``###`` prefix,
    and the form's declared ``internal`` label was dropped by GitHub (the label
    does not exist), so the issue matched neither label fallback. The
    strict-parser rejection above is the consequence on the COMMENT arm (and on
    the issue arm only since it started refusing a form-filed body with no
    parseable claim). The assertion below is the pin in both worlds: the form
    must ship no line the claim parser selects.
    """
    body = _render_body(_load())
    hits = [ln for ln in body.splitlines() if PROVENANCE_CLAIM_RE.match(ln)]
    assert not hits, (
        "the form renders lines the provenance claim parser selects (a claim "
        "the strict parser rejects, so the body is UNKNOWN_PROVENANCE wherever "
        f"it is classified): {hits}")


def test_the_old_label_claim_is_selected_by_one_parser_and_refused_by_the_other():
    """The mechanism this module is about, in one assertion pair.

    ``### Measured at (commit / sha you read)`` — the label the form used to
    ship, and the line the pre-fix red run reported — is a CLAIM (the claim
    parser tolerates the ``###`` prefix) and is never a valid provenance line.
    That split is exactly what the two halves must never disagree about; and it
    is why the ISSUE-BODY arm's column-0 ``grep`` was the deeper defect, since
    it reads neither parser and so saw no claim here at all.
    """
    line = "### Measured at (commit / sha you read)"
    assert PROVENANCE_CLAIM_RE.match(line), (
        "the label the form used to render is a claim the claim parser selects")
    assert not PROVENANCE_RE.search(line), (
        "…and it is never a valid provenance line — the strict parser refuses it")


def test_no_field_label_or_description_reads_as_a_provenance_claim():
    """Same pin over the UI-side text, which the form must also keep clean."""
    form = _load()
    for item in form["body"]:
        attrs = item.get("attributes") or {}
        for key in ("label", "description", "placeholder"):
            text = str(attrs.get(key) or "")
            for line in text.splitlines():
                assert not PROVENANCE_CLAIM_RE.match(line), (
                    f"field {item.get('id')!r} {key} carries a malformed "
                    f"provenance claim: {line!r}")


def test_form_tells_the_filer_the_command_that_emits_the_line():
    """The filer must be told to paste the gate's own output (`--emit`).

    A form that demands a shape without naming the command that produces it
    trades one failed gate for a hand-typed sha — which is what broke in the
    first place (3/3 programmatic filings flagged, 2026-10-26).
    """
    text = FORM.read_text(encoding="utf-8")
    assert "finding_provenance.py --emit" in text, (
        "the form never names `tools/finding_provenance.py --emit`, so the "
        "filer has no mechanical way to produce the line it demands")
    # …and it must state the shape the STRICT parser accepts, in prose, so a
    # careful filer can self-check without running anything.
    assert "<ref>@<40-hex-sha> on <YYYY-MM-DD>" in text, (
        "the form does not state the exact shape PROVENANCE_RE accepts")


def test_the_form_ships_a_marker_the_gate_selects():
    """#10146 F1 — the refusal key must be delivered BY THE FORM, not by a label.

    The gate's ``decide`` step refuses a form-filed body with no parseable
    claim, and it keys that refusal on two things THIS FILE delivers: the
    hidden ``<!-- finding -->`` marker in the rendered body (selected by the
    gate's own ``claims_finding()``, imported — never copied) and the
    ``title:`` prefix. The previous key was ``labels: ["internal"]``, a label
    this repo does not have, which GitHub silently drops at filing — the
    refusal was dead code in production.
    """
    body = _render_body(_load())
    assert claims_finding(body), (
        "the body this form produces is not selected by the gate's own "
        "claims_finding() — a form-filed finding with a malformed provenance "
        "field would be SKIPPED instead of refused (#10146 F1)")
    assert "<!-- finding -->" in body
    assert _load().get("title") == "[finding]: ", (
        "the form's title prefix is the second delivered key the gate reads — "
        "it is gone")
    assert _load().get("labels") is None, (
        "this form declares labels again. A label is repo STATE: GitHub drops "
        "one that does not exist, silently, which is exactly how the #10146 "
        "refusal became dead code. If a label is ever needed here, pin that it "
        "EXISTS in the repo.")


def test_measured_at_is_required():
    """``required: false`` is the second half of the mismatch."""
    fields = {item.get("id"): item for item in _load()["body"] if item.get("id")}
    field = fields["measured_at"]
    assert (field.get("validations") or {}).get("required") is True, (
        "the measured_at field is optional, so the form ships findings with no "
        "provenance line at all — the exact body the gate refuses")


def test_the_shape_the_form_asks_for_is_the_shape_the_gate_accepts():
    """The two halves are pinned by ONE regex each, imported — never copied.

    A value the filer types per the form's instruction must satisfy the strict
    parser, and must NOT be selected by the claim parser while being rejected
    by it (i.e. when the strict parser accepts the line, the claim parser also
    selects it — they agree on a VALID line).
    """
    line = "Measured at: origin/main@" + "a" * 40 + " on 2026-10-27"
    assert PROVENANCE_RE.search(line), "the pinned sample line is not accepted"
    assert PROVENANCE_CLAIM_RE.match(line), (
        "a VALID provenance line must also be selected by the claim parser — "
        "the two regexes must agree on the good case, or the gate's selector "
        "and its validator disagree about what a finding looks like")


@pytest.mark.parametrize("bad", [
    "Measured at: 98ab138c9",                       # the form's old placeholder
    "Measured at: origin/main@98ab138c9",           # short sha
    "Measured at: origin/main@" + "a" * 40,         # no date
])
def test_the_old_form_shape_is_refused(bad):
    """Pin the defect: the shape the form used to ask for fails the gate.

    Selected-then-refused is the pair that makes it ``UNKNOWN_PROVENANCE``
    rather than silence — on the arm that uses the claim parser at all.
    """
    assert not PROVENANCE_RE.search(bad)
    assert PROVENANCE_CLAIM_RE.match(bad), (
        "a malformed provenance claim must still be SELECTED by the claim "
        f"parser (that is what makes it UNKNOWN_PROVENANCE, not discussion): {bad!r}")
