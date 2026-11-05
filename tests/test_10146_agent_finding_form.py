"""#10146 — the agent-finding form must produce a body the provenance gate accepts.

The form (``.github/ISSUE_TEMPLATE/agent_finding.yml``) and the gate
(``tools/finding_provenance.py``) are two halves of ONE contract, and neither
read the other:

* the form asked for a bare short sha in a field that was ``required: false``;
* the gate needs a line matching ``PROVENANCE_RE``
  (``Measured at: <ref>@<40-hex-sha> on <YYYY-MM-DD>``) and, seeing any looser
  ``Measured at`` claim, reports ``FAIL UNKNOWN_PROVENANCE``.

So a finding filed **through the intended form** still failed the gate. These
tests pin the two halves to ONE source of truth: the gate's own module-level
regexes, imported — never copied. A test written against a copy of the regex
would drift exactly as the form did (the owner's implementation constraint on
the issue).

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

from tools.finding_provenance import PROVENANCE_CLAIM_RE, PROVENANCE_RE

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
    strict parser then rejects. The filer who follows the form fails the gate.
    """
    body = _render_body(_load())
    hits = [ln for ln in body.splitlines() if PROVENANCE_CLAIM_RE.match(ln)]
    assert not hits, (
        "the form renders lines the provenance claim parser selects (each one "
        f"becomes FAIL UNKNOWN_PROVENANCE): {hits}")


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
    """Pin the defect: the shape the form used to ask for fails the gate."""
    assert not PROVENANCE_RE.search(bad)
    assert PROVENANCE_CLAIM_RE.match(bad), (
        "a malformed provenance claim must still be SELECTED by the claim "
        f"parser (that is what makes it UNKNOWN_PROVENANCE, not discussion): {bad!r}")
