"""#8491 (in-repo read path): calendar attendees promoted into CRM identity
ungrounded by the meeting under process.

The issue's writer is OUT-OF-REPO — the ``minutes`` CLI's ``attribution`` step
emits ``attendees`` / ``speaker_map`` / ``calendar_people`` into
``~/meetings/*.md``, and no module in this repo writes those fields (measured:
``git grep -n -e speaker_map -e calendar_people -e diarization_collapsed``
returns nothing). What IS in-repo is the same defect **on the read path**:
``bridge.py::get_calendar_attendees()`` read
``~/.minutes/cal-trigger-state.json`` — a SINGLE-VALUED store that cal-trigger.py
overwrites on every recording start and that carries no ``meeting_id`` — and
``process_meeting`` auto-created one ``Person`` and one "First call"
``Opportunity`` per entry. Any re-run, backfill or ``--watch`` pass over an
older meeting therefore promoted *another* meeting's calendar participants into
this meeting's CRM, at machine confidence, with no grounding against evidence
already in hand: #8491's fix direction 3 ("scope the speaker-resolution store by
``meeting_id`` on the read path"), and its own stated harm — wrong people
attributed with machine confidence on a structured identity surface.

These tests pin the grounding contract (artifact evidence only; refuse rather
than guess) and the end-to-end consequence (no contact, no opportunity, no
week-old attendees attached to an unrelated call).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
# Repo root on sys.path so `import integrations` works under any pytest
# invocation (console script does not add CWD; `python -m pytest` does) — the
# same construction as tests/test_bridge_mcp.py. Deliberately NOT inserting
# `integrations/crm/twenty/` itself: that directory holds `review.py`, a name
# generic enough to shadow another module for the rest of the session.
sys.path.insert(0, str(REPO_ROOT))

import integrations.crm.twenty.bridge as bridge  # noqa: E402

# The contaminated artifact shape from the issue: one remote participant, a
# state file left behind by a *different* call recorded the same week.
MEETING_DATE = "2026-10-09T11:03:58-05:00"
MEETING_TITLE = "Preston Younger Intro for GTM Packaging"

ATTENDEES = [
    {"name": "Contact One", "email": "one@example.com"},
    {"name": "Contact Two", "email": "two@example.com"},
]


def _state_file(tmp_path: Path, **overrides) -> Path:
    state = {
        "recording_pid": 4242,
        "current_event_uid": "uid-previous-call",
        "started_at": "2026-10-07T09:00:00",
        "event_title": "Web3 GTM Shifts and Infrastructure Focus",
        "attendees": list(ATTENDEES),
    }
    state.update(overrides)
    path = tmp_path / "cal-trigger-state.json"
    path.write_text(json.dumps(state))
    return path


def _meeting_md(tmp_path: Path) -> Path:
    path = tmp_path / "2026-10-09-preston-younger-intro.md"
    path.write_text(
        "---\n"
        'id: "2026-10-09-preston-younger-intro"\n'
        f'date: "{MEETING_DATE}"\n'
        f'title: "{MEETING_TITLE}"\n'
        "type: meeting\n"
        "speakers:\n"
        '  - name: "SPEAKER_1"\n'
        "    segments: 12\n"
        "---\n"
        "## Transcript\n"
        "[SPEAKER_1 0:04] Let's talk packaging.\n"
    )
    return path


# ── grounding contract ─────────────────────────────────────────────────────

def test_no_grounding_evidence_refuses(tmp_path, monkeypatch):
    """No evidence supplied → refuse. An unscoped read is the defect."""
    monkeypatch.setattr(bridge, "CAL_STATE_FILE", str(_state_file(tmp_path)))
    assert bridge.get_calendar_attendees() == []


def test_stale_neighbouring_state_is_not_applied(tmp_path, monkeypatch):
    """A state file from a different call (day AND title differ) → []."""
    monkeypatch.setattr(bridge, "CAL_STATE_FILE", str(_state_file(tmp_path)))
    assert bridge.get_calendar_attendees(MEETING_DATE, MEETING_TITLE) == []


def test_state_from_the_same_day_is_applied(tmp_path, monkeypatch):
    """Same local day grounds it even when the titles disagree."""
    monkeypatch.setattr(
        bridge, "CAL_STATE_FILE",
        str(_state_file(tmp_path, started_at="2026-10-09T10:55:00",
                        event_title="A Different Calendar Title")),
    )
    assert bridge.get_calendar_attendees(MEETING_DATE, MEETING_TITLE) == ATTENDEES


def test_state_from_the_same_event_title_is_applied(tmp_path, monkeypatch):
    """Same event title grounds it even across a midnight boundary."""
    monkeypatch.setattr(
        bridge, "CAL_STATE_FILE",
        str(_state_file(tmp_path, started_at="2026-10-08T23:52:00",
                        event_title=MEETING_TITLE)),
    )
    assert bridge.get_calendar_attendees(MEETING_DATE, MEETING_TITLE) == ATTENDEES


def test_unparseable_dates_do_not_ground(tmp_path, monkeypatch):
    """A timestamp nobody can read is evidence of nothing → refuse."""
    monkeypatch.setattr(
        bridge, "CAL_STATE_FILE",
        str(_state_file(tmp_path, started_at="not-a-timestamp")),
    )
    assert bridge.get_calendar_attendees(
        "also-not-a-date", "Some Other Title") == []


def test_no_state_file(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "CAL_STATE_FILE", str(tmp_path / "absent.json"))
    assert bridge.get_calendar_attendees(MEETING_DATE, MEETING_TITLE) == []


def test_corrupt_state_file(tmp_path, monkeypatch):
    path = tmp_path / "cal-trigger-state.json"
    path.write_text("{not json")
    monkeypatch.setattr(bridge, "CAL_STATE_FILE", str(path))
    assert bridge.get_calendar_attendees(MEETING_DATE, MEETING_TITLE) == []


# ── end-to-end consequence on process_meeting ──────────────────────────────

@pytest.fixture
def crm_calls(monkeypatch):
    """Stub the Twenty REST surface and record what process_meeting asks for."""
    calls = {"created_people": [], "created_opportunities": []}
    created: set[str] = set()
    monkeypatch.setattr(bridge, "load_resolved_speakers", lambda: [])
    monkeypatch.setattr(bridge, "find_note_by_external_id", lambda *a, **k: None)

    def _find_person_by_email(email):
        # No CRM contact exists for any attendee yet: `process_meeting` then
        # creates one (and, on the re-read, an opportunity). This is what makes
        # the stale-state test NON-VACUOUS — if the bridge grounds an attendee,
        # a Person appears.
        return {"id": f"person-{email}", "name": email} if email in created else None

    monkeypatch.setattr(bridge, "find_person_by_email", _find_person_by_email)

    def _create_person(name, email):
        created.add(email)
        calls["created_people"].append((name, email))
        return {"id": f"person-{email}"}

    def _create_opportunity(person_id, name):
        calls["created_opportunities"].append((person_id, name))
        return {"id": "opp-1"}

    monkeypatch.setattr(bridge, "create_person", _create_person)
    monkeypatch.setattr(bridge, "create_opportunity", _create_opportunity)
    monkeypatch.setattr(bridge, "queue_unmatched_speaker", lambda *a, **k: None)
    monkeypatch.setattr(
        bridge, "create_note", lambda *a, **k: {"id": "note-1"})
    monkeypatch.setattr(
        bridge, "push_to_tortoise", lambda fm: {"status": "skipped"})
    return calls


def test_process_meeting_does_not_seed_crm_from_a_stale_state(
        tmp_path, monkeypatch, crm_calls):
    """The #8491 harm: an unrelated call's attendees must not become contacts.

    ``find_person_by_email`` returns a contact for every email, so any attendee
    the bridge decides to ground becomes a Person — this asserts the count is
    ZERO, not merely that the call "did not fail".
    """
    monkeypatch.setattr(bridge, "CAL_STATE_FILE", str(_state_file(tmp_path)))
    result = bridge.process_meeting(str(_meeting_md(tmp_path)))
    assert crm_calls["created_people"] == []
    assert crm_calls["created_opportunities"] == []
    assert result["status"] in {"ok", "partial"}


def test_process_meeting_still_seeds_crm_when_grounded(
        tmp_path, monkeypatch, crm_calls):
    """Positive control: the same meeting with its OWN cal-trigger state."""
    monkeypatch.setattr(
        bridge, "CAL_STATE_FILE",
        str(_state_file(
            tmp_path,
            started_at="2026-10-09T10:55:00",
            event_title=MEETING_TITLE,
        )),
    )
    bridge.process_meeting(str(_meeting_md(tmp_path)))
    assert crm_calls["created_people"] == [
        (a["name"], a["email"]) for a in ATTENDEES]
    assert crm_calls["created_opportunities"] == [
        (f"person-{a['email']}", f"{a['name']} — First call 2026-10-09")
        for a in ATTENDEES]


# ── the wiring that keeps this file running on its own subject ──────────────

def test_bridge_change_selects_core_so_this_guard_runs():
    """A bridge.py-only diff must select `core`, or this file never runs.

    Measured before the CORE_ALSO entry: `select(['integrations/crm/twenty/
    bridge.py'])` → ``surfaces=[]`` (45 tier-1 files) — `integrations/` is a flat
    NON_PYTHON_PREFIXES entry, so the path was dropped before the "unmatched →
    core" fallback. Mutation check: removing the CORE_ALSO entry fails this.
    """
    import tools.ci_selection as cs

    selected = cs.select(["integrations/crm/twenty/bridge.py"],
                         "pull_request", cs.load_manifest())
    assert "core" in selected["surfaces"]
    assert "test_8491_cal_attendee_grounding.py" in selected["test_files"]
    # And the row is registered exactly once (an unregistered file reds
    # `ci_selection.py --integrity`).
    manifest = cs.load_manifest()
    assert cs.classify_test_file(
        "test_8491_cal_attendee_grounding.py", manifest) == "core"
