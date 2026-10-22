"""#2981 — a full-but-healthy DB is not a corrupt DB.

`_auto_health_recover` collapses every probe failure into ONE message that
advises `python -m tortoise rebuild`. When the server has simply reached
`maxmemory` (policy `noeviction`), the graph is INTACT and that advice points
at destroying healthy data.

These tests **execute the real handler** and assert on what it RAISES — the
value the operator actually receives — not on the presence of a string in the
source. Both directions are pinned, because a fix that only removes the
rebuild advice is as wrong as the bug:

* full-but-healthy  -> the error NAMES used_memory/maxmemory and does NOT
                       advise rebuild (the write-refusal branch);
* genuinely corrupt -> the rebuild advice STILL appears (the branch NARROWS
                       the remedy, it does not remove it).

Revert-verified: delete the `_write_refusal_message` branch in
`_auto_health_recover` and `test_full_but_healthy_is_not_reported_as_corrupt`
goes RED (the rebuild advice reappears); make `_write_refusal_message` return
a message for every exception and
`test_genuine_corruption_still_advises_rebuild` goes RED.

#7392 pins a THIRD direction, on the STRENGTH of the lead sentence: a refusal
whose ceiling was never READ must not claim one. The
`test_no_ceiling_established_*` and `test_unset_maxmemory_*` cases go RED if
the ceiling sentence (or the ceiling-only `--maxmemory` lever) is emitted on
the `_memory_pressure() is None` and `maxmemory == 0` branches.
"""

from __future__ import annotations

import pytest

from tortoise.projection import FalkorProjection

# The server's own wording when `maxmemory` is reached with `noeviction`.
_MAXMEMORY_ERROR = (
    "OOM command not allowed when used memory > 'maxmemory'."
)
# An unrelated failure — the class that must STILL reach the rebuild advice.
_CORRUPTION_ERROR = "Invalid graph operation on empty key"


def _projection(*, probe_error: BaseException | None, embedded: bool = False):
    """A projection whose probe fails for a chosen reason.

    Built with ``object.__new__`` so the REAL ``_auto_health_recover`` runs
    without a live server: the handler is the unit under test, not the DB.
    """
    proj = object.__new__(FalkorProjection)
    proj._probe_error = probe_error
    proj._is_embedded = embedded
    proj._path = "/tmp/does-not-exist-2981"
    proj._probe_ok = lambda: False  # type: ignore[method-assign]
    return proj


def test_full_but_healthy_is_not_reported_as_corrupt(monkeypatch):
    """A maxmemory write-refusal names the numbers and must NOT say rebuild."""
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    proj = _projection(probe_error=RuntimeError(_MAXMEMORY_ERROR))
    # used 512 MiB of a 512 MiB ceiling — the wedge the issue measured.
    proj._memory_pressure = lambda: (512 * 1024 * 1024, 512 * 1024 * 1024)  # type: ignore[method-assign]

    with pytest.raises(RuntimeError) as err:
        proj._auto_health_recover()

    msg = str(err.value)
    # 1. It names the memory numbers — the operator sees WHY.
    assert "used_memory" in msg, msg
    assert "maxmemory" in msg, msg
    assert "512.0 MiB" in msg, msg
    # 2. It does NOT ADVISE the rebuild — the assertion is on the PRESCRIBED
    #    COMMAND, not on the word "rebuild": the correct message contains
    #    "do NOT rebuild", so a substring check on the bare word would fail
    #    on a correct message and pass on a wrong one that merely drops the
    #    word while still pointing at the rebuild path.
    assert "python -m tortoise rebuild" not in msg, (
        "a full-but-healthy graph must never be sent to the rebuild path: " + msg
    )
    # 3. It says the data is intact, forbids the rebuild, and names the remedy.
    assert "INTACT" in msg, msg
    assert "do NOT rebuild" in msg, msg
    assert "GRAPH.DELETE" in msg, msg


def test_write_refusal_remedy_names_the_safe_path_and_claims_no_flags(monkeypatch):
    """#2979 — the remedy must be executable, and must make no false claim.

    The message must make no claim about which graph commands the server
    refuses. Upstream registers `graph.DELETE` as "write deny-script" (no
    `deny-oom`) while `graph.QUERY` carries `deny-oom`, so a refusal reported
    during a `GRAPH.QUERY` says nothing about `GRAPH.DELETE` — and #2979's
    "OutOfMemoryError" is exactly that, the DETACH `safe_graph_delete` sends
    before `graph.delete()`.

    The executable lever is pinned exactly. The second pin — that no
    command-refusal claim is made — is BEST-EFFORT only: the stem denylist below
    cannot catch every wording, and a claim phrased with "refused" would pass
    it. What protects the property is that the claim is absent and that the
    comment on `_write_refusal_message` says why it must stay absent.
    """
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    proj = _projection(probe_error=RuntimeError(_MAXMEMORY_ERROR))
    proj._memory_pressure = lambda: (512 * 1024 * 1024, 512 * 1024 * 1024)  # type: ignore[method-assign]

    msg = proj._write_refusal_message(RuntimeError(_MAXMEMORY_ERROR))
    assert msg is not None
    # 1. The one executable lever — named for BOTH instance classes: the
    #    projection message is emitted on managed instances too, where a
    #    container flag cannot be set (#9532 cycle 3 P1).
    assert "raise / relieve the instance's maxmemory ceiling" in msg, msg
    assert "REDIS_ARGS" in msg, msg
    assert "managed instance" in msg, msg
    # 2. The residue-pass lever is NOT named: that pass runs only when an
    #    operator invokes it, so naming its env lever would be advice that frees
    #    nothing.
    assert "TORTOISE_TEST_SWEEP_LEGACY" not in msg, msg
    # 3. The unsafe shortcut is refused, and the mechanism is still named.
    assert "GRAPH.DELETE" in msg, msg          # the pre-existing pin, kept
    assert "Do NOT hand-pick names from GRAPH.LIST" in msg, msg
    assert "cannot be undone" in msg, msg
    assert "FLUSHALL" in msg, msg
    # 4. NO claim about which commands the server refuses. BEST-EFFORT: this
    #    denylist does not catch every wording a refusal claim could use, and
    #    "refus"/"cannot" are legitimate elsewhere in the message, so they
    #    cannot be stems. What protects the property is that the claim is absent
    #    and the code comment says why it must stay absent.
    lowered = msg.lower()
    for stem in ("deny", "reject", "blocked"):
        assert stem not in lowered, (stem, msg)


def test_write_refusal_remedy_does_not_hand_roll_the_ownership_policy(monkeypatch):
    """#2979 — enumerate no delete-authorizing family: defer to the predicate.

    The ownership policy lives in the code: `_SWEEP_OWNED_PREFIXES`, with
    `owns_by_ownership_record` as the JOURNAL path's predicate, and
    `is_legacy_residue` on the journal-blind path. A flat list of graph-name
    families cannot be exact here, because the two predicates differ and the
    victim is a name someone cannot attribute — so the message names none.

    The ABSENCE properties below are pinned best-effort, and are labelled as
    such rather than as stronger properties than they are:

      (a) a WILDCARD BUDGET — exactly the one guard `*`. Bounds glob-spelled
          enumerations only.
      (b) a DENYLIST of specific graph-name tokens. It does NOT catch a concrete
          name outside that list: the real residue entry
          ``askshape_b6_live_1_33760_21`` passes both (a) and (b).

    The PRESENCE property in (c) is an exact substring pin; (d) is an absence
    pin like (a) and (b). What protects those properties is review, not these
    assertions.
    """
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    proj = _projection(probe_error=RuntimeError(_MAXMEMORY_ERROR))
    proj._memory_pressure = lambda: (512 * 1024 * 1024, 512 * 1024 * 1024)  # type: ignore[method-assign]

    msg = proj._write_refusal_message(RuntimeError(_MAXMEMORY_ERROR))
    assert msg is not None
    # (a) WILDCARD BUDGET: exactly the one guard wildcard. Bounds glob-spelled
    #     enumerations only — a prose list of concrete names carries no `*`.
    assert msg.count("*") == 1, msg
    assert "tortoise_restored*" in msg, msg
    # (b) DENYLIST of specific graph-name tokens the message must never carry,
    #     plus the residue-pass lever (that pass runs only when an operator
    #     invokes it, so naming it is advice that frees nothing). Not a guard against a
    #     concrete name outside this list — see the docstring.
    for token in ("NEVER delete", "org_*", "team_*", "registry", "test_",
                  "v10fix", "tt_gate", "typeprobe", "review_rw_probe",
                  "TORTOISE_TEST_SWEEP_LEGACY"):
        assert token not in msg, (token, msg)
    # (c) the two names `TortoiseSDK.test_guard` blocks are still named.
    assert "the production graph tortoise" in msg, msg
    assert "tortoise_restored* snapshot" in msg, msg
    # (d) the message promises no reclaim: the default pass drops the journal
    #     INTERSECT `_SWEEP_OWNED_PREFIXES`, so "reclaims what the ownership
    #     record lists" would be over-inclusive.
    assert "reclaim" not in msg.lower(), msg


def test_memory_pressure_unreadable_still_avoids_rebuild(monkeypatch):
    """An unreadable INFO must not demote the failure back to 'corrupt'."""
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    proj = _projection(probe_error=RuntimeError(_MAXMEMORY_ERROR))
    proj._memory_pressure = lambda: None  # type: ignore[method-assign]

    with pytest.raises(RuntimeError) as err:
        proj._auto_health_recover()

    msg = str(err.value)
    assert "maxmemory" in msg, msg
    assert "python -m tortoise rebuild" not in msg, msg


# ── #7392 — the message must not claim a ceiling it did not establish ──────

#: The lead sentence that is only TRUE once a ceiling was read from the server.
_CEILING_CLAIM = "has reached its memory ceiling"
#: The remedy that only makes sense when that ceiling exists.
_CEILING_LEVER = "raise / relieve the instance's maxmemory ceiling"


def _assert_no_ceiling_claim(msg: str) -> None:
    """Assert the message does not assert a ceiling, or prescribe raising one.

    The non-corruption guarantees (#2981: intact, no rebuild, no graph
    deletion) are asserted alongside so a fix cannot buy the absence of the
    false claim by deleting the whole branch — the same two-directional
    posture the module docstring describes.
    """
    assert _CEILING_CLAIM not in msg, (
        "the ceiling sentence is only true once INFO memory returned one: " + msg)
    assert _CEILING_LEVER not in msg, (
        "the --maxmemory lever is the remedy for a ceiling that does not "
        "exist on this branch: " + msg)
    assert "INTACT" in msg, msg
    assert "do NOT rebuild" in msg, msg
    assert "python -m tortoise rebuild" not in msg, msg
    assert "Do NOT hand-pick names from GRAPH.LIST" in msg, msg


def test_no_ceiling_established_refusal_is_stated_as_a_memory_refusal(monkeypatch):
    """#7392 — an UNREADABLE datum is not evidence of a ceiling.

    NON-VACUOUS: pre-fix this branch emitted "the server has reached its
    memory ceiling (server reports a maxmemory write-refusal (used_memory/
    maxmemory unreadable))" plus the `--maxmemory` lever — so both absence
    assertions above fail on it.

    What it must say instead is what was OBSERVED: the server refused a command
    for memory reasons. That keeps the operator away from `rebuild` (the
    #2981 job) without inventing a ceiling to tune.

    The wording must also be TRUE OF EVERY shape that reaches this branch, not
    just a raised `INFO` call: a reply that answers WITHOUT a `maxmemory` field
    is not an unreadable INFO. So the message says the datum could not be read,
    and it does NOT offer the flag as an action — on this branch whether a
    ceiling exists is exactly what is unknown (both found in review of this
    change).
    """
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    proj = _projection(probe_error=RuntimeError(_MAXMEMORY_ERROR))
    proj._memory_pressure = lambda: None  # type: ignore[method-assign]

    msg = proj._write_refusal_message(RuntimeError(_MAXMEMORY_ERROR))
    assert msg is not None
    _assert_no_ceiling_claim(msg)
    lowered = msg.lower()
    assert "refused a command for memory reasons" in lowered, msg
    # …it says the DATUM was not usable, naming no failed read it cannot know…
    assert "no usable used_memory/maxmemory reading" in lowered, msg
    assert "info memory is unreadable" not in lowered, msg
    # …the next action is to ESTABLISH it, so the flag is not offered as a lever.
    assert "info memory" in lowered, msg
    assert "--maxmemory" not in msg, msg


def test_a_reply_missing_a_field_does_not_claim_the_info_read_failed():
    """#7392 review — `None` has FIVE causes; the message must fit all five.

    `_memory_pressure()` refuses a datum when the reply raised, was not a dict,
    omitted `used_memory` or `maxmemory`, carried unparseable values, or
    reported a negative ceiling. Asserting "INFO memory is unreadable" is false
    for a reply that ANSWERED without a field, and "no maxmemory was read" is
    false for one whose value was unusable — so this drives the REAL probe with
    a reply missing `maxmemory` into the message and pins wording true of every
    cause.

    NON-VACUOUS for this change, not for the pre-existing code: before it, a
    reply missing the field collapsed into the asserting branch instead (a
    ceiling claim for a datum nobody read).
    """

    class _Conn:
        def __init__(self, reply):
            self._reply = reply

        def info(self, section):
            return self._reply

    def _proj_with(reply):
        proj = object.__new__(FalkorProjection)
        proj.db = type("DB", (), {"connection": _Conn(reply)})()
        proj._probe_error = RuntimeError("Out of memory")
        return proj

    # `maxmemory` omitted, and `used_memory` omitted: both are unanswered, and
    # each must reach the SAME wording rather than a cause-specific guess.
    for reply in ({"used_memory": 1024}, {"maxmemory": 2048}):
        msg = _proj_with(reply)._write_refusal_message(RuntimeError("Out of memory"))
        assert msg is not None, reply
        _assert_no_ceiling_claim(msg)
        lowered = msg.lower()
        assert "no usable used_memory/maxmemory reading" in lowered, (reply, msg)
        assert "info memory is unreadable" not in lowered, (reply, msg)


def test_a_real_unset_ceiling_reaches_the_message_without_a_ceiling_claim():
    """#7392 review — the `cap == 0` MESSAGE must see a REAL reading.

    The other `cap == 0` cases stub `_memory_pressure`, so reverting its
    contract to `(used, cap) if cap > 0 else None` was caught only by the
    contract test — the message branch never saw a reading. This drives the
    real probe (`INFO memory` answering `maxmemory 0`) all the way into the
    operator message. That shape is the CI provisioner's today (its REDIS_ARGS
    passes no --maxmemory, pinned by test_7357_memory_ceiling_legibility); it
    was ALSO the shared container's when #7392 was filed (2026-10-04, before the
    #9204/#9506 ceilings landed), so the branch is not hypothetical.
    """

    class _Conn:
        def info(self, section):
            return {"used_memory": 512 * 1024 * 1024, "maxmemory": 0}

    proj = object.__new__(FalkorProjection)
    proj.db = type("DB", (), {"connection": _Conn()})()

    msg = proj._write_refusal_message(RuntimeError(_MAXMEMORY_ERROR))
    assert msg is not None
    _assert_no_ceiling_claim(msg)
    lowered = msg.lower()
    assert "no maxmemory ceiling is set" in lowered, msg
    assert "512.0 mib" in lowered, msg


@pytest.mark.parametrize("refusal", [
    _MAXMEMORY_ERROR,
    # The generic engine wording `_WRITE_REFUSAL_MARKERS` accepts — the reason
    # this branch is reachable on a server with NO ceiling at all. Pinned so
    # the issue's own reachability argument is exercised, not just asserted.
    "Out of memory",
])
def test_unset_maxmemory_names_the_absence_without_asserting_a_locus(monkeypatch, refusal):
    """#7392 — `maxmemory == 0` means NO ceiling; the LOCUS is not asserted.

    The reading establishes one thing: `maxmemory` is not the cause. It does not
    establish WHERE the pressure is — and the example the message gives, "a
    per-query cap", is a limit OF the instance — so "outside the instance" would
    contradict its own example. Pin the absence AND that non-claim.

    Reachability: `maxmemory = 0` was measured on the shared container 2026-10-04
    (before the #9204/#9506 ceilings landed); today the ceiling-less instance is
    the CI provisioner's, whose REDIS_ARGS passes no --maxmemory.

    NON-VACUOUS: `_memory_pressure()` returned `None` for this case pre-fix, so
    the message made the same false ceiling claim as the unusable-reading one.
    """
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    proj = _projection(probe_error=RuntimeError(refusal))
    proj._memory_pressure = lambda: (512 * 1024 * 1024, 0)  # type: ignore[method-assign]

    msg = proj._write_refusal_message(RuntimeError(refusal))
    assert msg is not None
    _assert_no_ceiling_claim(msg)
    lowered = msg.lower()
    assert "no maxmemory ceiling is set" in lowered, msg
    # The reading is still reported — it WAS readable.
    assert "512.0 MiB" in msg, msg
    # A candidate cause is offered AS a candidate, and the locus is NOT asserted:
    # "outside the instance" would contradict the in-instance per-query cap.
    assert "host" in lowered, msg
    assert "outside the instance" not in lowered, msg


#: The post-refusal shortfall that must NOT be read as "below the ceiling":
#: MEASURED on falkordb 4.20.6 at a 16 MiB `noeviction` ceiling, three refused
#: writes left the reading 3 568 / 56 552 / 16 880 bytes under the cap while the
#: instance was still refusing at it. The implementation's tolerance is
#: `max(64 KiB, cap // 100)`; these cases pin both sides of it.
_CAP = 16 * 1024 * 1024


@pytest.mark.parametrize("used", [
    _CAP,                 # exactly at the ceiling
    _CAP - 1,             # a byte under: the MEASURED drift, not a cause
    _CAP - 4096,          # a page under: same
    _CAP - (_CAP // 100), # sampled drift: same (at the tolerance boundary)
])
def test_a_post_refusal_shortfall_is_not_read_as_below_the_ceiling(monkeypatch, used):
    """#7392 cycle 5 — the reading is taken AFTER the refusal, so a small
    shortfall is the MEASUREMENT, not a different cause.

    `_auto_health_recover` probes first and reads `INFO memory` afterwards, and
    `used_memory` includes buffers that are released when the refused reply is
    written. Measured on a real server: the refusal fires at `used > cap`, and
    the very next reading is up to ~56 KiB under it. Treating that as "the
    ceiling was not reached" would tell an operator not to raise a ceiling that
    is very much being hit.
    """
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    proj = _projection(probe_error=RuntimeError(_MAXMEMORY_ERROR))
    proj._memory_pressure = lambda: (used, _CAP)  # type: ignore[method-assign]

    msg = proj._write_refusal_message(RuntimeError(_MAXMEMORY_ERROR))
    assert msg is not None
    assert "has reached its memory ceiling" in msg, msg
    assert "raise / relieve the instance's maxmemory ceiling" in msg, msg
    assert "does not show the ceiling being reached" not in msg, msg


def test_a_ratio_far_below_the_ceiling_does_not_claim_the_ceiling_was_reached(monkeypatch):
    """#7392 — a reading well under `maxmemory` is not "reached".

    The issue's proposal kept the ceiling sentence whenever the ratio could be
    read, assuming "read" implies "reached". It does not: the generic
    `out of memory` marker reaches this branch with a reading of
    `used_memory 1.0 KiB of maxmemory 512.0 MiB`, and quoting "has reached its
    memory ceiling" there would state a fact the reading contradicts.

    The branch therefore reports the OBSERVATION with the shortfall (so two
    rounded-equal figures can never be printed as "below"), keeps the cause list
    modal, and asks for a re-read instead of forbidding the ceiling.

    NON-VACUOUS: on the pre-fix code this message asserts the ceiling AND the
    `--maxmemory` lever for exactly these numbers.
    """
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    cap = 512 * 1024 * 1024
    proj = _projection(probe_error=RuntimeError("Out of memory"))
    proj._memory_pressure = lambda: (1024, cap)  # type: ignore[method-assign]

    msg = proj._write_refusal_message(RuntimeError("Out of memory"))
    assert msg is not None
    _assert_no_ceiling_claim(msg)
    lowered = msg.lower()
    # It reports the OBSERVATION, with the shortfall named…
    assert "this instance now reports used_memory" in lowered, msg
    assert "does not show the ceiling being reached" in lowered, msg
    assert "512.0 mib" in lowered and "1.0 kib" in lowered, msg
    # …the remedy is to RE-READ, not a prohibition and not the ceiling lever…
    assert "re-read info memory" in lowered, msg
    assert "do not raise the ceiling" not in lowered, msg
    # …and the cause list stays modal, like the `maxmemory == 0` branch.
    assert "may be elsewhere" in lowered, msg


def test_the_shortfall_is_named_so_two_rounded_figures_never_read_as_below():
    """#7392 cycle 5 — `_fmt_bytes` rounds to 0.1 of a unit, and the branch
    prints BOTH figures: at 16 MiB a sub-52 KiB drift renders both as
    "16.0 MiB", so a message saying "BELOW the ceiling (16.0 MiB)" beside
    `used_memory 16.0 MiB` contradicts itself.

    Naming the SHORTFALL is what makes that impossible: any reading that reaches
    this branch is more than `max(64 KiB, cap // 100)` under the cap, so the
    delta always renders as its own non-zero figure.
    """
    cap = 16 * 1024 * 1024
    proj = object.__new__(FalkorProjection)
    proj._memory_pressure = lambda: (cap - (3 * cap // 10), cap)  # type: ignore[method-assign]

    msg = proj._write_refusal_message(RuntimeError("Out of memory"))
    assert msg is not None
    # The rendered usage and ceiling differ — no self-contradiction.
    rendered = msg.split("NOW reports ", 1)[1]
    used_text = rendered.split(" — ", 1)[0]
    assert used_text != "16.0 MiB", msg
    assert "below its maxmemory ceiling" in msg.lower(), msg


def test_memory_pressure_reports_an_unset_ceiling_instead_of_hiding_it():
    """#7392 — "no ceiling" and "could not read" are DIFFERENT answers.

    `_memory_pressure()` collapsed both to `None`, which is how the message
    came to claim a ceiling in the one case where the datum positively said
    there is none (`maxmemory = 0`). Pin the distinction on the REAL method,
    through both of its readings — an overriding test double would let the
    method keep collapsing them unnoticed.
    """

    class _Conn:
        """An `INFO memory` stand-in that RAISES on a wrong section.

        Deliberately a raise and not a bare `assert`: `_memory_pressure`
        swallows every exception, so an AssertionError here would surface as
        the generic `None == (1024, 0)` failure instead of naming the wrong
        section — a misleading red (found in review of this change).
        """

        def __init__(self, reply):
            self._reply = reply

        def info(self, section):
            if section != "memory":
                raise RuntimeError(f"wrong INFO section requested: {section!r}")
            if isinstance(self._reply, Exception):
                raise self._reply
            return self._reply

    def _proj_with(reply):
        proj = object.__new__(FalkorProjection)
        proj.db = type("DB", (), {"connection": _Conn(reply)})()
        return proj

    # An unset ceiling is a READING, not a failure to read.
    assert _proj_with({"used_memory": 1024, "maxmemory": 0})._memory_pressure() == (1024, 0)
    # A ceiling that exists is reported with its ratio.
    assert _proj_with({"used_memory": 1024, "maxmemory": 2048})._memory_pressure() == (1024, 2048)
    # Only a FAILED read is `None`.
    assert _proj_with(RuntimeError("no INFO on this server"))._memory_pressure() is None
    assert _proj_with("not-a-dict")._memory_pressure() is None
    # An OMITTED maxmemory is an unanswered datum, not "no limit": the
    # `or 0` default must not mint the positive claim the message then makes.
    assert _proj_with({"used_memory": 1024})._memory_pressure() is None
    # A NEGATIVE ceiling is a broken reading, not an answer (no conformant
    # Redis reports one — `CONFIG SET maxmemory` refuses it), so it must not
    # become a "no ceiling" claim either.
    assert _proj_with({"used_memory": 1024, "maxmemory": -1})._memory_pressure() is None


def test_genuine_corruption_still_advises_rebuild(monkeypatch):
    """THE OTHER DIRECTION — an unrelated failure keeps the rebuild advice.

    Without this test the fix could be 'delete the message', which would be a
    worse bug than the one being fixed.
    """
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    proj = _projection(probe_error=RuntimeError(_CORRUPTION_ERROR))
    proj._memory_pressure = lambda: (1, 512 * 1024 * 1024)  # type: ignore[method-assign]

    with pytest.raises(RuntimeError) as err:
        proj._auto_health_recover()

    assert "python -m tortoise rebuild" in str(err.value), (
        "corruption must still point at the rebuild path: " + str(err.value)
    )


def test_corruption_in_server_mode_still_advises_rebuild(monkeypatch):
    """Production/server mode: the rebuild advice is unchanged for corruption."""
    monkeypatch.setenv("FLY_APP_NAME", "tortoise-test")
    proj = _projection(probe_error=RuntimeError(_CORRUPTION_ERROR))

    with pytest.raises(RuntimeError) as err:
        proj._auto_health_recover()

    msg = str(err.value)
    assert "server/production mode" in msg, msg
    assert "python -m tortoise rebuild" in msg, msg


@pytest.mark.parametrize("cause,needle", [
    (_MAXMEMORY_ERROR, "maxmemory"),
    ("LOADING Redis is loading the dataset in memory", "loading"),
    ("GRAPH.COPY failed, could not fork", "fork"),
])
def test_each_backend_cause_names_itself(cause, needle):
    """#3634 — each backend cause reports ITS OWN reason, not a neighbour's.

    A maxmemory refusal, a still-hydrating `LOADING` reply and a module-fork
    refusal are three different problems with three different remedies.
    Falling through to the rebuild advice (or borrowing the maxmemory
    message) would send an operator to destroy healthy data for a transient
    cause.

    No `FLY_APP_NAME` dance: all three causes branch before the
    `is_prod or not self._is_embedded` gate, so which message is raised here
    does not depend on the env var. (The FORK lane text is keyed on
    `self._is_embedded` alone — see `_backend_failure_message`.)
    """
    proj = _projection(probe_error=RuntimeError(cause))
    with pytest.raises(RuntimeError) as err:
        proj._auto_health_recover()
    assert needle in str(err.value).lower()
    assert "python -m tortoise rebuild" not in str(err.value)


@pytest.mark.parametrize("cause", [
    "GRAPH.COPY failed, could not fork",   # FalkorDB's reply (cmd_copy.c)
    "Can't fork for module: File exists",  # redis module.c, errno 17 (EEXIST)
])
def test_documented_fork_wordings_route_to_the_fork_remedy(cause):
    """P2-1 — the canonical classifier recognises the engine's REAL wordings.

    The parallel table shipped in 0b9b78eb8 matched `fork failed`/`can't fork`
    but MISSED `could not fork` — FalkorDB's own reply, and the exact string in
    `tests/test_fork_slot_wedge_3845.py` — so a real fork refusal fell through
    to the rebuild advice. Routing through `fork_slot.is_fork_refusal` closes
    that gap; this test fails if that routing is removed.

    `embedded=True` pins the embedded-lane variant, whose action names the
    slot cure. The server-lane variant is pinned separately (below).
    """
    proj = _projection(probe_error=RuntimeError(cause))
    msg = proj._backend_failure_message(RuntimeError(cause), embedded=True)
    assert msg is not None, cause
    assert "recover_fork_slot" in msg, msg


def test_fork_remedy_names_the_slot_cure_and_not_memory(monkeypatch):
    """P2-4 — the fork remedy must not repeat the misattribution it fixes.

    errno 17 is EEXIST: a hung `redis-module-fork` child holds Redis's single
    module-fork slot (fork_slot.py:23-30) — NOT memory/process pressure. The
    remedy shipped in 0b9b78eb8 told the operator to relieve a memory limit,
    which cannot clear a slot held by a hung child, and omitted the documented
    cure. Pin both halves: the cure is named, the memory misattribution is not.

    Pinned on the EMBEDDED lane (`_is_embedded=True`, no FLY_APP_NAME) — the
    only lane where `recover_fork_slot` applies.
    """
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    proj = _projection(
        probe_error=RuntimeError("GRAPH.COPY failed, could not fork"),
        embedded=True,
    )
    with pytest.raises(RuntimeError) as err:
        proj._auto_health_recover()
    msg = str(err.value)
    assert "EEXIST" in msg, msg
    assert "recover_fork_slot" in msg, msg
    assert "redis-module-fork" in msg, msg
    assert "not corrupt" in msg, msg
    # The forbidden misattribution: no memory/process remedy may be prescribed.
    assert "raise --maxmemory" not in msg, msg
    assert "memory/process limit" not in msg, msg
    assert "add memory" not in msg.lower(), msg


def test_fork_remedy_names_both_causes_and_the_discriminator(monkeypatch):
    """P2 — one refusal, TWO mechanically distinct causes; do not assert one.

    `GRAPH.COPY failed, could not fork` is emitted both when a background
    RDB/AOF child holds the slot (transient, healthy) and when a previous
    module fork never exited (#3845). A remedy that asserts only the hang is
    false in the save case — and its prescribed `recover_fork_slot` is a NO-OP
    there (it is socket-scoped to `redis-module-fork`, so it returns
    `recovered=False`). Pin both causes and the discriminating action.
    """
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    proj = _projection(
        probe_error=RuntimeError("GRAPH.COPY failed, could not fork"),
        embedded=True,
    )
    msg = str(proj._backend_failure_message(
        RuntimeError("GRAPH.COPY failed, could not fork"), embedded=True))
    # (a) BOTH causes are named — the message must not claim a single cause.
    assert "TWO" in msg, msg
    assert "background RDB save" in msg or "background\nRDB save" in msg, msg
    assert "hung" in msg and "redis-module-fork" in msg, msg
    # The old single-cause assertion, verbatim — must be gone.
    assert "a hung, un-reaped `redis-module-fork` child still holds that slot" not in msg, msg
    # (b) The discriminating action: when no hung child is found, the slot is
    #     held by an in-flight save — wait, don't reap.
    assert "if no hung" in msg, msg
    assert "wait for it to finish and retry" in msg, msg
    # The EEXIST / EAGAIN distinction is retained.
    assert "EAGAIN" in msg, msg


def test_fork_remedy_on_server_lane_does_not_prescribe_the_embedded_cure(monkeypatch):
    """P2 — `_auto_health_recover` emits the FORK remedy BEFORE the
    `is_prod or not self._is_embedded` gate, so a remote/docker operator must
    not be handed an embedded-only call. There `socket_path_of(db)` is None and
    `recover_fork_slot` returns 'not an embedded unix-socket server'.
    """
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    proj = _projection(
        probe_error=RuntimeError("GRAPH.COPY failed, could not fork"),
        embedded=False,  # remote/docker FalkorDB
    )
    with pytest.raises(RuntimeError) as err:
        proj._auto_health_recover()
    msg = str(err.value)
    # (c) the embedded-only cure is NOT prescribed on this lane...
    assert "recover_fork_slot" not in msg, msg
    # ...the server-mode action IS...
    assert "server" in msg.lower(), msg
    assert "restart" in msg.lower(), msg
    # ...and the two-cause analysis + discriminator still apply on every lane.
    assert "TWO" in msg, msg
    assert "wait for it to finish and retry" in msg, msg
    assert "EEXIST" in msg, msg
    assert "not corrupt" in msg, msg
    assert "python -m tortoise rebuild" not in msg, msg


@pytest.mark.parametrize("is_prod,embedded,lane_label,has_cure", [
    (False, False, "[server lane", False),
    (False, True, "[embedded lane]", True),
    (True, False, "[server lane", False),
    (True, True, "[embedded lane]", True),
])
def test_fork_remedy_lane_follows_the_embedded_axis_only(
    monkeypatch, is_prod, embedded, lane_label, has_cure
):
    """P2 — the lane remedy is selected on `self._is_embedded` ALONE.

    The flag previously read `_is_embedded and not is_prod`, conflating two
    independent axes. That handed a prod+embedded handle the server text
    ("this is not an embedded unix-socket server") even though its socket
    path WAS present and the slot cure was available. Pin all four
    `is_prod` × `_is_embedded` products: only the embedded axis moves the
    lane, and no variant may assert a socket property it cannot know.
    """
    if is_prod:
        monkeypatch.setenv("FLY_APP_NAME", "tortoise-test")
    else:
        monkeypatch.delenv("FLY_APP_NAME", raising=False)
    proj = _projection(
        probe_error=RuntimeError("GRAPH.COPY failed, could not fork"),
        embedded=embedded,
    )
    with pytest.raises(RuntimeError) as err:
        proj._auto_health_recover()
    msg = str(err.value)
    assert lane_label in msg, msg
    assert ("recover_fork_slot" in msg) is has_cure, msg
    assert "this is not an embedded unix-socket server" not in msg, msg
    assert "python -m tortoise rebuild" not in msg, msg


def test_bare_fork_stem_is_not_a_refusal():
    """P2-2 — a bare `fork` stem is not evidence of a module-fork refusal.

    The removed parallel table matched `fork failed`/`can't fork`/`cannot
    fork`; the canonical classifier requires one of the engine's actual
    module-fork wordings. Unrelated text mentioning a fork can no longer be
    routed to the fork remedy.
    """
    proj = _projection(probe_error=None)
    assert proj._backend_failure_message(
        RuntimeError("disk fork failed during compaction")) is None


def test_write_refusal_only_matches_the_server_wording():
    """The classifier is signature-scoped, not a blanket catch-all."""
    proj = _projection(probe_error=None)
    proj._memory_pressure = lambda: (1, 2)  # type: ignore[method-assign]

    # The real server wordings are recognised...
    for text in (
        _MAXMEMORY_ERROR,
        "OOM command not allowed when used memory > 'maxmemory'",
        "Out of memory",
    ):
        assert proj._write_refusal_message(RuntimeError(text)) is not None, text

    # ...and unrelated failures are NOT (so they fall through to rebuild).
    for text in (_CORRUPTION_ERROR, "connection refused", "WRONGTYPE"):
        assert proj._write_refusal_message(RuntimeError(text)) is None, text

    # A missing exception is not a write refusal.
    assert proj._write_refusal_message(None) is None
