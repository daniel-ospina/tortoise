"""#7357 — a memory-ceiling refusal says so, instead of reading as a broken write.

The issue is about a memory failure that MISDESCRIBES itself. Its first shape was
an uncapped container SIGKILLed under host pressure, whose death reached the
caller as a bare transport error; #8954 and #9352 made that legible. #9204 then
capped every container this repo starts and #9506 gave the lane's own instance a
Redis-side ``--maxmemory`` BELOW the cap with ``noeviction``, so an overshoot is
an ANSWERED refusal instead of a kill — which created the third shape of the same
defect:

    redis.exceptions.OutOfMemoryError:
        command not allowed when used memory > 'maxmemory'.

MEASURED on this checkout 2026-10-21 against a lane container
(``CONFIG SET maxmemory 1mb`` + ``noeviction``): both a raw ``graph.query`` and
the same call through ``guarded_client`` raised exactly that sentence, unchanged —
no cause, no next action, no owner of the limit, and nothing in the tree that
said "memory" (the issue's own grep for a ceiling diagnosis was empty).

What these tests pin, in order:

* the family predicate, and that it is EXCLUSIVE of the instance-outage family
  (an answered refusal is not a dead box, and a dead box is not an answer);
* the note's content — capacity refusal, nothing applied, data intact, and the
  real remedy rather than an invitation to retry (a deterministic refusal
  is not a transient), and that it conforms to the repo's recorded #2981 ruling
  for the same refusal instead of advising a destructive one;
* the contract: driver sentence at position 0, exception TYPE unchanged, one
  suffix at most;
* the WIRING, driven through a real ``guarded_client`` and through the duck-typed
  proxy, so deleting the seam reddens these rather than merely the helper.
"""
from __future__ import annotations

import pytest

redis_exc = pytest.importorskip("redis.exceptions")

from tortoise import cypher_guard  # noqa: E402
from tortoise.retry import (  # noqa: E402
    _LEGIBLE_OPENER,
    MEMORY_CEILING_OWNERSHIP_CONSTRAINT,
    explain_instance_unavailable,
    explain_memory_ceiling,
    graph_abort_family,
    is_instance_unavailable,
    is_memory_ceiling_refusal,
    retryable_aborted_write,
    retryable_transient,
)
from tortoise.sdk import _classify_db_failure  # noqa: E402

#: The engine's own sentence, verbatim, as the vendor parser hands it over.
CEILING_SENTENCE = "command not allowed when used memory > 'maxmemory'."
WRITE_STATEMENT = "CREATE (n:Point {id:$id}) RETURN n.id"
#: A READ statement, still sent on the denyoom ``GRAPH.QUERY`` verb (measured
#: refused at the ceiling — see ``DENYOOM_VERBS``).
READ_STATEMENT = "MATCH (n) RETURN count(n)"


def _ceiling_error() -> BaseException:
    """The exception the pinned driver mints for the engine's ``-OOM`` reply."""
    return redis_exc.OutOfMemoryError(CEILING_SENTENCE)


# ── the family predicate ────────────────────────────────────────────────────
def test_the_measured_sentence_is_the_memory_ceiling_family():
    assert is_memory_ceiling_refusal(_ceiling_error()) is True


def test_the_family_is_decided_by_class_even_without_the_measured_text():
    """A rebuilt exception (no wire text) is still the family, by type."""
    assert is_memory_ceiling_refusal(redis_exc.OutOfMemoryError("")) is True


def test_a_plain_response_error_carrying_the_engine_sentence_is_the_family():
    """The text arm: a `ResponseError` that did not pass the parser's table."""
    exc = redis_exc.ResponseError(CEILING_SENTENCE)
    assert is_memory_ceiling_refusal(exc) is True


def test_an_unrelated_refusal_is_not_the_family():
    """`maxmemory` alone is not the discriminator — a caller's own message can
    carry it (`CONFIG GET maxmemory`, a docstring, a config dump)."""
    assert is_memory_ceiling_refusal(
        redis_exc.ResponseError("ERR unknown command 'GRAPH.FROB'")) is False
    assert is_memory_ceiling_refusal(
        redis_exc.ResponseError("maxmemory is set to 1610612736")) is False


def test_a_chained_ceiling_refusal_is_still_the_family():
    """P2 (#9532 cycle 2): the driver and our own wrappers re-raise WITH a cause.

    MEASURED before the chain walk existed: a ``ResponseError("write failed")``
    whose ``__cause__`` is the engine's ceiling refusal answered False here and
    False to the outage predicate too — so no note was applied and the
    illegible sentence reached the caller unchanged, which is precisely the
    defect #7357 closes. The sibling predicate at this seam already walked the
    chain (#6977), so this was an inconsistency, not a design choice.

    The sibling's chain rules apply: an explicit ``raise ... from`` wins, a
    ``raise ... from None`` stops the walk, and an id-deduplicated loop cannot
    spin on a cyclic chain.
    """
    wrapped = redis_exc.ResponseError("write failed")
    wrapped.__cause__ = _ceiling_error()
    assert is_memory_ceiling_refusal(wrapped) is True
    assert "memory-ceiling" in (explain_memory_ceiling(wrapped) or "").lower()
    # …and it is STILL not an outage, so one message cannot carry both suffixes.
    assert is_instance_unavailable(wrapped) is False
    assert explain_instance_unavailable(wrapped, mutating=True) is None

    # A suppressed context is still an explicit cause: `raise X from Y` with
    # `__suppress_context__` set must be walked, because __cause__ wins.
    explicit = redis_exc.ResponseError("write failed")
    explicit.__cause__ = _ceiling_error()
    explicit.__suppress_context__ = True
    assert is_memory_ceiling_refusal(explicit) is True

    # A chain that is NOT the family stays out, and a cycle terminates.
    assert is_memory_ceiling_refusal(
        redis_exc.ResponseError("write failed")) is False
    cyclic = redis_exc.ResponseError("loop")
    cyclic.__cause__ = cyclic
    assert is_memory_ceiling_refusal(cyclic) is False


def test_a_transport_failure_is_not_the_family_and_vice_versa():
    """EXCLUSIVITY: the two notes must never be applied to one another's case.

    A ceiling refusal is an ANSWERED reply, and the outage arm also owns one
    ANSWERED member (LOADING), so the exclusion cannot rest on "an outage is the
    absence of a reply" (#9532 cycle 2 P2). It is by CLASS: `_answered_refusal`
    rejects every ``ResponseError`` from the outage family,
    ``OutOfMemoryError`` is one, and LOADING is not. Confusing them sends a
    reader after a container restart that never happened, or retries a capacity
    refusal forever.
    """
    transport = redis_exc.TimeoutError("Timeout reading from socket")
    assert is_memory_ceiling_refusal(transport) is False
    assert is_instance_unavailable(transport) is True
    assert is_instance_unavailable(_ceiling_error()) is False
    assert explain_memory_ceiling(transport) is None
    assert explain_instance_unavailable(
        _ceiling_error(), mutating=True) is None


def test_the_nearest_neighbour_is_not_the_family_either():
    """`LOADING` is the other ANSWERED refusal, and it sits one step away.

    Both are replies rather than outages, so a reloading instance must not be
    told it "reached a memory limit" — the remediation (free memory, raise the
    ceiling) would turn a knob that changes nothing. MEASURED on the pinned
    redis (7.4.1): `BusyLoadingError` is NOT a `ResponseError`. The exclusion
    that would otherwise fire on it is `_answered_by_server` — the vendor
    parser's own `EXCEPTION_CLASSES` maps `LOADING` to this class, so the
    answered-refusal arm WOULD swallow it — and that is why
    `is_instance_unavailable` needs its order-critical early return for it
    (the same mechanism `tortoise/retry.py`'s own docstring names). P2 (#9532
    cycle 2): this rationale used to attribute the early return to the class's
    CONNECTION family membership, which is not what would reject it. So the two
    predicates partition it deliberately: an outage (per that family) and not a
    ceiling refusal. Both halves are pinned here, because a future redis that
    reparented it would break the partition silently.
    """
    loading = redis_exc.BusyLoadingError(
        "LOADING Redis is loading the dataset in memory")
    assert not isinstance(loading, redis_exc.ResponseError), (
        "the partition below is pinned on the measured inheritance")
    assert is_memory_ceiling_refusal(loading) is False
    assert explain_memory_ceiling(loading) is None
    assert is_instance_unavailable(loading) is True, (
        "LOADING is the outage family's answered member (#9352)")


# ── the note's content ──────────────────────────────────────────────────────
def test_the_note_names_the_mechanism_and_refuses_the_data_loss_reading():
    text = explain_memory_ceiling(_ceiling_error())
    assert text is not None
    # The driver sentence stays at position 0 (every text match reads there).
    assert text.startswith(CEILING_SENTENCE)
    assert _LEGIBLE_OPENER in text
    lowered = text.lower()
    # What happened …
    assert "memory-ceiling" in lowered
    assert "capacity" in lowered
    # … what it is NOT …
    assert "graph was deleted or replaced" in lowered
    assert "not evidence that the graph or its data was lost" in lowered
    assert "not a dead or restarting instance" in lowered
    # … and what was (not) applied.
    assert "nothing was applied" in lowered


def test_the_note_does_not_invite_a_retry_and_names_the_real_remedy():
    """A deterministic refusal is NOT a transient: the next action is memory.

    P1 (#9532 review): the FIRST version of this note named
    ``FALKORDB_CONTAINER_MEMORY`` as if it were the ceiling's lever on every
    container this repo starts. It is not — only ``tools/test_lane.py`` derives
    ``--maxmemory`` from it (3/4 of the cap), while ``graph-scripts/setup.py``
    pins ``--maxmemory 512mb`` (so raising the env var moves only the kernel
    cap) and the CI provisioner passes no ``--maxmemory`` at all. A remedy that
    cannot work is the exact defect class #7357 is about, so this pins the
    NARROWED wording: the Redis-side knob is the ceiling, the env var is one
    container's derivation of it, and the kernel cap is only the backstop.

    NON-VACUOUS: the P1 wording said "a container this repo starts derives it
    from `FALKORDB_CONTAINER_MEMORY`", so the "ONLY a ... container" assertion
    fails on it.
    """
    text = (explain_memory_ceiling(_ceiling_error()) or "").lower()
    assert "re-issuing it now will be refused the same way" in text
    assert "raise the instance's `maxmemory` ceiling" in text
    # The Redis-side knob is the lever, and it is named as such — AND the note
    # says where it lives on each instance class, because the SDK emits this on
    # managed instances too (docs/infra-runbook.md: FalkorDB Cloud is managed,
    # with no container and no `REDIS_ARGS`). P1 (#9532 cycle 2): a
    # container-only lever is the same "remedy that cannot work" defect one
    # scope out.
    assert "`--maxmemory` in its `redis_args`" in text
    assert "managed instance" in text and "provider" in text
    assert "no container there" in text
    # The kernel cap is the BACKSTOP; raising it does not move the refusal, so
    # the note must not send the reader to the wrong knob alone.
    assert "only a `tools/test_lane.py` container derives that ceiling" in text
    assert "falkordb_container_memory" in text
    assert "backstop" in text
    assert "never the ceiling" in text
    # It must never be told "retrying is safe" the way the READ arm of the
    # transport note is (#9352): that arm's licence does not extend here.
    assert "re-issuing this statement is safe" not in text


def _provisioner_redis_args(provision_src):
    """The ``REDIS_ARGS`` strings ``provision.sh`` actually passes to docker.

    P2 (#9532 cycle 3): the first version of this guard sliced the script at the
    FIRST occurrence of the literal ``REDIS_ARGS`` — which is the last word of a
    COMMENT (``# ... `--maxmemory` in REDIS_ARGS``) — so the slice was the empty
    string and ``"--maxmemory" not in ""`` was vacuously true. The fact the
    note's wording rests on ("the CI provisioner passes none") was therefore
    unpinned: appending ``--maxmemory 1g`` to the real default left every
    assertion in that test green.

    So parse the ARGV, not the prose: ``start <name> "<redis_args>" <auth>`` is
    the one call site shape that reaches ``docker run -e REDIS_ARGS=...``, and a
    comment can never be mistaken for it. Self-tested by
    ``test_the_provisioner_argv_scanner_reads_the_argv_not_a_comment`` so a
    rewrite of this function cannot silently re-vacuum the guard.
    """
    import re

    return re.findall(r'^\s*start\s+"[^"]*"\s+"([^"]*)"', provision_src, re.M)


def test_the_provisioner_argv_scanner_reads_the_argv_not_a_comment():
    """The scanner's own self-test (#9532 cycle 3 P2).

    The defect was a guard that read a COMMENT and therefore asserted on the
    empty string. This plants the two shapes that matter — a decoy comment that
    says ``REDIS_ARGS`` before any call site, and a real ``start`` invocation
    that carries a ``--maxmemory`` — and requires the scanner to return the
    argv and to see the flag. A comment-slicing implementation returns ``""``
    here and fails the first assertion.
    """
    planted = (
        "# a comment that mentions REDIS_ARGS and `--maxmemory` in prose\n"
        'start "$NAME" "--requirepass falkordb --save \'\'" "-a falkordb"\n'
        '  start "$LEGACY" "--save \'\' --maxmemory 1g" ""\n'
    )
    argv = _provisioner_redis_args(planted)
    assert argv == ["--requirepass falkordb --save ''",
                    "--save '' --maxmemory 1g"], (
        "the scanner must read the `start` argv, not a comment that names it: "
        f"{argv!r}")
    assert any("--maxmemory" in args for args in argv), (
        "a real argv carrying --maxmemory must be visible to the guard")


def test_the_remedy_claims_match_the_files_they_assert_about():
    """P2 (#9532 cycle 2): pin the FACTS, not only the note's own wording.

    The note's narrowing rests on three claims about other files: `tools/
    test_lane.py` derives its ceiling from the env var (already pinned in
    ``test_test_lane_tool.py``), ``graph-scripts/setup.py`` pins its own
    ``--maxmemory 512mb``, and the CI provisioner passes no ``--maxmemory`` at
    all. Nothing pinned the last two, so if either file ever started deriving
    its ceiling, the note would silently become the wrong remedy again while
    every test stayed green — the same failure this PR exists to fix.
    """
    from pathlib import Path

    import tortoise

    root = Path(tortoise.__file__).resolve().parent.parent

    setup_src = (root / "graph-scripts" / "setup.py").read_text(encoding="utf-8")
    redis_args = [line for line in setup_src.splitlines() if "REDIS_ARGS=" in line]
    assert redis_args, "the dev container's REDIS_ARGS argv must still exist"
    dev_args = " ".join(redis_args)
    assert "--maxmemory 512mb" in dev_args, (
        "the note tells a dev-container operator to raise `--maxmemory`, and "
        "that this env var does NOT move it — if setup.py starts deriving the "
        "ceiling, the note must change with it: " + dev_args)

    provision_src = (root / ".github" / "actions" / "falkordb-provision"
                     / "provision.sh").read_text(encoding="utf-8")
    argv = _provisioner_redis_args(provision_src)
    assert argv, (
        "the provisioner's `start` call sites must still be parseable — the "
        "guard below is only meaningful if it reads argv")
    assert [args for args in argv if "--maxmemory" in args] == [], (
        "the provisioner now passes a --maxmemory; the note's claim that it "
        f"passes none is stale: {argv!r}")
    assert any(args == "--requirepass falkordb --save ''" for args in argv), (
        "the provisioner's REDIS_ARGS default moved — re-derive the note's "
        f"\"the CI provisioner passes none\" claim: {argv!r}")


def test_the_note_never_names_deleting_graphs_as_the_remedy():
    """P2 (#9532 review): the remedy must agree with the repo's #2981 ruling.

    This seam runs on EVERY instance the SDK builds a client for — shared and
    production included — not just a lane's own container, so "free memory by
    deleting graphs this instance no longer needs" is advice the caller cannot
    act on safely: `GRAPH.DELETE` is irreversible and a name you cannot
    attribute is not yours to delete. `_write_refusal_message` (#2981) already
    records that ruling for this exact refusal, and two messages for one
    refusal must not advise opposite actions.

    NON-VACUOUS: the pre-fix note said "free memory (delete graphs this
    instance no longer needs)", so the first assertion fails on it.
    """
    text = (explain_memory_ceiling(_ceiling_error()) or "").lower()
    assert "delete graphs this instance no longer needs" not in text, (
        "the note must not offer graph deletion as the remedy: it is emitted "
        "on shared and production instances too (see #2981)")
    # Deletion may only appear as a PROHIBITION, and it must be composed from
    # #2981's ONE authority rather than loosened into a paraphrase.
    assert "do not hand-pick names from graph.list to free memory" in text
    assert "graph.delete cannot be undone" in text
    assert "not yours to delete" in text
    assert "never delete the production graph tortoise" in text
    assert "tortoise_restored*" in text
    assert "do not flushall" in text
    assert "#2981" in text, (
        "the note must point at the policy it is conforming to, or the next "
        "lane re-decides it")


def _ownership_constraint_holders(root):
    """Modules under *root* that carry the #2981 constraint as PROSE.

    P2 (#9532 cycle 2): the first version of this detector was line-local —
    ``opening in file_text`` — and a second copy written in the repo's dominant
    implicit-concatenation style (a literal split across lines, which is how
    every message in this codebase is written) evaded it, so the "one authority"
    property it claimed was not the property it enforced. The scan therefore
    NORMALISES before comparing: quotes, backslashes, parentheses and all
    whitespace are removed from both sides, so a copy split at ANY point — or
    re-quoted, or re-indented — still matches. Order is preserved, so squashing
    cannot join two unrelated fragments into an accidental hit.
    """
    import re

    sq = lambda text: re.sub(r"[\s\"'()\\`]+", "", text)  # noqa: E731

    from tortoise.retry import MEMORY_CEILING_OWNERSHIP_CONSTRAINT

    needle = sq(MEMORY_CEILING_OWNERSHIP_CONSTRAINT)
    holders = []
    for path in sorted(root.rglob("*.py")):
        if needle in sq(path.read_text(encoding="utf-8")):
            holders.append(str(path.relative_to(root)))
    return holders


def test_the_one_authority_scanner_catches_a_split_copy(tmp_path):
    """The detector's own self-test (#9532 cycle 2 P2).

    A drift guard that cannot catch the way this repo writes messages is worse
    than none: it reports a property it does not hold. This plants a copy split
    across lines exactly as the codebase does it, and requires the scanner to
    find it.
    """
    from tortoise.retry import MEMORY_CEILING_OWNERSHIP_CONSTRAINT as C

    half = len(C) // 2
    cut = C.rindex(" ", 0, half) + 1
    planted = tmp_path / "package"
    planted.mkdir()
    (planted / "copy.py").write_text(
        "MESSAGE = (\n"
        f"    {C[:cut]!r}\n"
        f"    {C[cut:]!r}\n"
        ")\n",
        encoding="utf-8",
    )
    assert _ownership_constraint_holders(planted) == ["copy.py"], (
        "a sentence split across two adjacent literals is the repo's normal "
        "style; the scanner must still see it as a copy")


def test_the_ownership_constraint_has_exactly_one_authority():
    """P2 (#9532 review): the #2981 constraint must not exist as TWO copies.

    The note is emitted on EVERY instance the SDK builds a client for, and
    ``_write_refusal_message`` emits its operator form for the same refusal, so
    two independent prose copies are free to drift into opposite advice — the
    hazard #2981 records. The constraint therefore lives once, in
    :data:`MEMORY_CEILING_OWNERSHIP_CONSTRAINT`, and BOTH messages compose it.

    This pins the property structurally rather than by agreeing literals:
    (1) the SDK note contains the constant verbatim; (2) no SECOND Python
    module in ``tortoise/`` contains the sentence as prose, under the
    normalising scan ``_ownership_constraint_holders`` — itself self-tested
    against a split copy, so this cannot go quietly vacuous; (3) the
    projection's message composes the constant instead of spelling it out.
    """
    from pathlib import Path

    import tortoise
    from tortoise.retry import MEMORY_CEILING_OWNERSHIP_CONSTRAINT

    assert MEMORY_CEILING_OWNERSHIP_CONSTRAINT in (explain_memory_ceiling(_ceiling_error()) or "")

    root = Path(tortoise.__file__).resolve().parent
    holders = _ownership_constraint_holders(root)
    assert holders == ["retry.py"], (
        "the #2981 ownership constraint must have ONE authority — retry.py's "
        f"MEMORY_CEILING_OWNERSHIP_CONSTRAINT — not a second prose copy: {holders}")

    # …and the projection BUILDS its operator message from it.
    projection_src = (root / "projection" / "__init__.py").read_text(encoding="utf-8")
    assert "MEMORY_CEILING_OWNERSHIP_CONSTRAINT" in projection_src, (
        "_write_refusal_message must compose the shared constant, or the two "
        "messages for one refusal can drift (#2981)")


def test_the_denyoom_claim_is_scoped_to_the_verb_that_was_measured():
    """P3 (#9532 review): the note must not out-claim its own measurement.

    The PR measured a refused `GRAPH.QUERY` WRITE (a `CREATE` left the graph at
    0 nodes). That every `GRAPH.*` verb carries `denyoom` — reads such as
    `GRAPH.RO_QUERY`/`GRAPH.LIST` included — is NOT established by it, so the
    note names the measured verb and the load-bearing half ("rejected BEFORE it
    ran") rather than the absolute.

    NON-VACUOUS: the pre-fix note said "a `denyoom` command (every `GRAPH.*`
    verb)", so the second assertion fails on it.
    """
    text = explain_memory_ceiling(_ceiling_error()) or ""
    lowered = text.lower()
    assert "every `graph.*` verb" not in lowered
    assert "graph.query" in lowered, (
        "the scoped claim names the verb that was actually measured")
    assert "rejected before it" in lowered


def test_the_note_is_idempotent_and_keeps_the_exception_type():
    first = explain_memory_ceiling(_ceiling_error())
    assert first is not None
    exc = redis_exc.OutOfMemoryError(first)
    assert explain_memory_ceiling(exc) is None, (
        "a second suffix on an already-explained message is the #7357 "
        "double-note defect")
    # Type is load-bearing: `_classify_db_failure` buckets BY TYPE, so a wrapper
    # (or a rewritten args[0] on a different class) would silently re-bucket it.
    assert isinstance(exc, redis_exc.OutOfMemoryError)
    assert _classify_db_failure(exc) == "db"


def test_a_ceiling_refusal_is_never_a_retryable_write():
    """Retrying is the one action the note forbids — the predicates must agree.

    Both the transient predicate and the write-arm classifier are checked,
    because either answering True would re-issue a statement the engine has just
    refused, and the note above would then be contradicting the code.

    P2 (#9532 review): the ANNOTATED exception is what a caller actually holds
    (the guard appends the note in place), and the note's own text names the
    graph-abort family's literal — "graph was deleted or replaced" — which is
    exactly what ``_GRAPH_ABORT_RE`` matches. So the guarantee that a refusal
    stays non-retryable rests on the ``\\A`` anchor holding against the note we
    ourselves wrote, not on the raw driver sentence. This pins BOTH: raw and
    annotated, including ``graph_abort_family``, so a future note rewrite that
    moves the literal off position 0 reddens here instead of re-arming the
    write-retry loop on a statement that provably did not run.
    """
    exc = _ceiling_error()
    assert retryable_transient(exc) is False
    assert retryable_aborted_write(exc, graph_exists=lambda: True) is False

    annotated = _ceiling_error()
    annotated.args = (explain_memory_ceiling(annotated),)
    assert _LEGIBLE_OPENER in str(annotated), "the fixture must be annotated"
    assert retryable_transient(annotated) is False, (
        "the note itself must not talk the refusal into the transient family")
    assert retryable_aborted_write(annotated, graph_exists=lambda: True) is False, (
        "the note quotes the graph-abort literal — the anchor must keep it out "
        "of the aborted-write arm")
    assert graph_abort_family(annotated) is False


# ── the ceiling CLAIM needs the ceiling EVIDENCE ────────────────────────────
# The classifier admits the exception CLASS, and the driver mints that class for
# every reply beginning with `OOM` — so family membership establishes that the
# instance ANSWERED with a memory refusal, never that a `maxmemory` exists or was
# reached. These pin the split, in both directions.
def test_a_class_only_refusal_is_not_told_it_reached_a_ceiling_it_never_read():
    """#7357: `OOM failed to allocate 1GB` names no ceiling, so none is asserted.

    Measured on `origin/main` 02e18e752 (issuecomment-6341756094): the note
    reported *"it has reached its configured dataset limit (`maxmemory`)"* for
    this message. Nothing in the exception carries that fact — and an instance
    reporting `maxmemory 0` is a measured shape on this repo's shared container
    and in the CI provisioner, where the prescribed remedy (raise the ceiling)
    cannot be taken at all.

    NON-VACUOUS: every "must not" assertion below fails on the pre-fix note.
    """
    exc = redis_exc.OutOfMemoryError("OOM failed to allocate 1GB")
    text = explain_memory_ceiling(exc) or ""
    assert text.startswith("OOM failed to allocate 1GB"), (
        "the driver sentence stays at position 0")
    assert text.count(_LEGIBLE_OPENER) == 1
    assert "it has reached its configured dataset limit" not in text, (
        "the ceiling was never read — asserting it is the #7357 defect")
    assert "nothing was applied" not in text.lower(), (
        "`nothing was applied` was measured for the denyoom/maxmemory refusal, "
        "not for an allocation failure — do not carry it across")
    assert "rejected before it ran" not in text.lower(), (
        "the pre-execution claim is the measured shape's, not this one's")
    # What the message DOES establish is still stated: it is the legibility fix.
    lowered = text.lower()
    assert "answered" in lowered and "memory refusal" in lowered
    assert "not evidence that the graph or its data was lost" in lowered
    assert "not a dead or restarting instance" in lowered
    # …and the ceiling appears only as a CONDITIONAL, read from the instance.
    assert "info memory" in lowered, (
        "the note must say how to establish whether a ceiling exists")
    assert "`maxmemory 0`" in text
    assert "if it reports a non-zero" in lowered
    assert "within a small drift below" in lowered, (
        "a real ceiling reads 3.5-56 KiB under the cap; an exact 'has "
        "reached' withdraws the remedy there")
    assert "above it" in lowered, (
        "`used + drift >= cap` admits a reading ABOVE the cap — the ordinary "
        "shape after the ceiling is lowered on a busy instance — so the note "
        "must not leave that case with no next action")
    assert "re-read while the refusal is repeating" in lowered
    # The one-authority pin below holds for BOTH arms, not just the measured one.
    assert MEMORY_CEILING_OWNERSHIP_CONSTRAINT in text
    assert "#2981" in text
    # Same contract as the established arm: type kept, idempotent, non-retryable.
    annotated = redis_exc.OutOfMemoryError(text)
    assert explain_memory_ceiling(annotated) is None, (
        "a second suffix on an explained message is the double-note defect")
    assert isinstance(annotated, redis_exc.OutOfMemoryError)
    assert _classify_db_failure(annotated) == "db"
    assert retryable_transient(annotated) is False
    assert retryable_aborted_write(annotated, graph_exists=lambda: True) is False


def test_an_empty_out_of_memory_is_hedged_not_asserted():
    """The sharpest case: an `OutOfMemoryError` carrying NO text at all.

    `is_memory_ceiling_refusal` is deliberately decided by CLASS even without
    the measured sentence (:func:`test_the_family_is_decided_by_class_even_without_the_measured_text`),
    so this is a family member whose ceiling evidence is the EMPTY string. A
    note may therefore not name a ceiling here either — that is exactly the
    over-claim this issue's last leg is about, in its purest form.
    """
    text = explain_memory_ceiling(redis_exc.OutOfMemoryError("")) or ""
    assert text.startswith(_LEGIBLE_OPENER), (
        "with no driver text, the note opens the message")
    assert "it has reached its configured dataset limit" not in text
    assert "`maxmemory 0`" in text
    assert text.count(_LEGIBLE_OPENER) == 1


def test_the_measured_ceiling_sentence_still_asserts_the_ceiling():
    """The other direction: the engine's own sentence IS the evidence.

    A hedge applied to the measured shape would be the same defect pointing the
    other way — a caller at a real ceiling told "maybe there is no ceiling" and
    sent to read `INFO memory` instead of raising it. This pins that the split
    is EVIDENCE-driven, not a blanket softening.

    NON-VACUOUS: making `explain_memory_ceiling` always take the hedge arm
    reddens the first assertion.
    """
    text = explain_memory_ceiling(_ceiling_error()) or ""
    assert "it has reached its configured dataset limit" in text
    assert "`maxmemory 0`" not in text, (
        "the measured sentence needs no no-ceiling hedge")
    assert "nothing was applied" in text.lower(), (
        "the denyoom rejection-before-execution claim was measured on THIS shape")


def test_a_chain_that_names_the_ceiling_still_earns_the_ceiling_note():
    """The evidence walk follows the chain, as the family predicate does.

    A wrapper whose `__cause__` is the engine's ceiling refusal is a family
    member by the cause (#9532 added the walk), so demoting it to the hedge
    would under-report a ceiling that WAS named. The converse — a chain of
    allocation failures with no ceiling sentence anywhere — stays hedged.
    """
    named = redis_exc.ResponseError("write failed")
    named.__cause__ = _ceiling_error()
    assert is_memory_ceiling_refusal(named) is True
    assert "it has reached its configured dataset limit" in (
        explain_memory_ceiling(named) or "")

    unnamed = redis_exc.ResponseError("write failed")
    unnamed.__cause__ = redis_exc.OutOfMemoryError("OOM failed to allocate 1GB")
    hedged = explain_memory_ceiling(unnamed) or ""
    assert hedged.startswith("write failed")
    assert "it has reached its configured dataset limit" not in hedged


def test_both_arms_share_one_remedy_sentence():
    """The remedy's two homes live in ONE copy (#2981's one-authority rule).

    The hedge arm was written as its own prose, and both arms must send an
    operator to the same two homes — the container's `REDIS_ARGS` and the
    managed provider's console (#9532 cycle 2/3 P1). Two wordings of one remedy
    is the drift #2981 rules out, one function apart, so both compose
    `_CEILING_REMEDY_HOMES` and neither spells it out again.

    NON-VACUOUS: inlining the sentence into either arm reddens the assertion
    for that arm.
    """
    from pathlib import Path

    import tortoise
    from tortoise.retry import _CEILING_REMEDY_HOMES

    src = (Path(tortoise.__file__).resolve().parent / "retry.py").read_text(
        encoding="utf-8")
    # Normalise before comparing: the repo splits message prose across adjacent
    # literals, so the sentence is not contiguous in the SOURCE even when it is
    # written once (the same trap `_ownership_constraint_holders` documents).
    import re
    squash = lambda text: re.sub(r"[\s\"'()\\`]+", "", text)  # noqa: E731
    assert squash(src).count(squash(_CEILING_REMEDY_HOMES)) == 1, (
        "the remedy sentence must exist once, as the constant")
    assert _CEILING_REMEDY_HOMES in (explain_memory_ceiling(_ceiling_error()) or "")
    assert _CEILING_REMEDY_HOMES in (
        explain_memory_ceiling(redis_exc.OutOfMemoryError("OOM failed")) or "")


def test_the_reached_decision_has_one_authority():
    """Both messages decide "the ceiling was reached" with ONE tolerance.

    #7357 review P2, and it was a live defect rather than a tidy-up: the SDK's
    hedge told an operator to raise `maxmemory` only if `used_memory` "has
    reached" it, while `_write_refusal_message` already knew — measured on
    falkordb 4.20.6 — that a reading taken after the refusal sits **3.5-56 KiB
    below** the cap and still counts. So the note withdrew the remedy at a
    genuine ceiling, the same "advice that cannot be acted on" class the issue
    is about, pointing the other way.

    NON-VACUOUS: re-inlining the tolerance in either place reddens the
    structural assertions; dropping the drift sentence from the note reddens
    `test_a_class_only_refusal_is_not_told_it_reached_a_ceiling_it_never_read`
    (this test does not read the note, so it cannot pin the note's prose).
    """
    from pathlib import Path

    import tortoise
    import tortoise.projection as projection
    from tortoise.retry import ceiling_reading_drift

    # The measured absolute floor, the 1 % heuristic, and its 1 MiB cap.
    assert ceiling_reading_drift(16 << 20) == (16 << 20) // 100
    assert ceiling_reading_drift(0) == 64 << 10
    assert ceiling_reading_drift(1 << 40) == 1 << 20

    root = Path(tortoise.__file__).resolve().parent
    projection_src = (root / "projection" / "__init__.py").read_text(encoding="utf-8")
    retry_src = (root / "retry.py").read_text(encoding="utf-8")

    # ⚠️ IDENTITY, not a text scan (#7357 cycle-2 review P3). The first version
    # counted one exact literal in each file, which a reflow, a renamed local or
    # a rewritten equivalent (`max(65536, min(cap // 100, 1048576))`) all evade
    # while the drift is genuinely duplicated — and which FALSE-REDS a faithful
    # refactor of the function it was pinning. The property that matters is that
    # the projection's code path resolves to the SAME callable object, and it is
    # checked directly.
    assert projection.ceiling_reading_drift is ceiling_reading_drift, (
        "the projection must resolve the shared rule, not a local copy of it")
    assert "ceiling_reading_drift(cap)" in projection_src, (
        "the shared rule must still be what the branch calls")
    assert retry_src.count("def ceiling_reading_drift") == 1, (
        "one definition, whose values the behavioural assertions above pin")

    # ⚠️ IDENTITY ALONE DOES NOT CATCH A *SECOND* DECISION SITE (#7357 cycle 3
    # P2): a new branch in the projection doing the arithmetic inline leaves the
    # import in place, the name resolving, and the `def` count at 1 — every
    # assertion above green while two tolerances drive one decision, the exact
    # drift this PR closes. So the DISTINCTIVE HALF of the expression is scanned,
    # normalised, across the package: quotes, brackets and whitespace are
    # squashed on both sides first, because this repo splits expressions across
    # lines and a reflow must not evade the guard.
    #
    # It keys on `min(cap // 100, …)` rather than on the whole formula, because
    # the whole-formula spelling is what a copy-paste DUPLICATE would keep while
    # a re-typed variant (`max(65536, min(cap // 100, 1048576))`) would not —
    # and the literal `1 << 20` alone is no use (four unrelated modules use it).
    # It keys on `min(...)` and not on a bare `cap // 100`, which the tiny-cap
    # guard below legitimately carries. Counted, not just file-matched, so a
    # second copy inside `retry.py` itself is caught too.
    import re
    squash = lambda text: re.sub(r"[\s\"'()\\`]+", "", text)  # noqa: E731
    needle = squash("min(cap // 100")
    holders = [
        str(p.relative_to(root)) for p in sorted(root.rglob("*.py"))
        if needle in squash(p.read_text(encoding="utf-8"))
    ]
    assert holders == ["retry.py"], (
        "the tolerance's 1% arm must have ONE implementation, inside "
        f"ceiling_reading_drift — not a second decision site: {holders}")
    assert squash(retry_src).count(needle) == 1, (
        "a second copy of the 1% arm inside retry.py is still two tolerances")


def test_the_evidence_walk_cannot_replace_the_exception_it_explains():
    """A raising `__str__` in the chain must not lose the note (#7357 cycle 2).

    `_chain_names_the_ceiling` stringifies EVERY link, so it can read a
    caller-supplied `__str__` that raises — where the family predicate only
    stringifies `ResponseError` links. Without a belt the raise escapes to
    `cypher_guard._note_instance_outage`, whose own belt swallows it and hands
    the caller the raw illegible message with NO note at all: #7357's defect, for
    that one exception. The module's rule for `is_instance_unavailable` is that a
    legibility predicate never REPLACES the error it explains, and the fail-safe
    answer here is the hedge (which asserts no ceiling).

    NON-VACUOUS: remove the `try` in `_chain_names_the_ceiling` and this raises.
    """

    class _Exploding(Exception):
        def __str__(self):
            raise RuntimeError("boom")

    exc = redis_exc.OutOfMemoryError("OOM failed to allocate 1GB")
    exc.__cause__ = _Exploding()
    text = explain_memory_ceiling(exc) or ""
    assert text.startswith("OOM failed to allocate 1GB")
    assert "it has reached its configured dataset limit" not in text, (
        "an unreadable chain establishes nothing — the hedge is the safe arm")


def test_an_unreadable_message_degrades_to_no_note_rather_than_a_raise():
    """The MESSAGE-level belt, not just the chain one (#7357 cycle 3 P3).

    `is_memory_ceiling_refusal` stringifies `ResponseError` links and
    `str(exc)` reads the exception's own `__str__`, so a caller-defined
    subclass can raise from either — before the chain belt is ever reached.
    A legibility helper that replaced the error it explains is what the
    module's `is_instance_unavailable` P1 forbids, so the public entry point is
    belted: an unreadable MESSAGE returns ``None`` (the raw sentence, no note),
    because there is nothing to append a note to.

    NON-VACUOUS: remove the belt around `_explain_memory_ceiling` and this
    raises `RuntimeError`.
    """

    class _ExplodingText(redis_exc.OutOfMemoryError):
        def __str__(self):
            raise RuntimeError("boom")

    assert explain_memory_ceiling(_ExplodingText("anything")) is None

    class _ExplodingResponse(redis_exc.ResponseError):
        def __str__(self):
            raise RuntimeError("boom")

    assert explain_memory_ceiling(_ExplodingResponse("anything")) is None


def test_an_implicit_context_is_not_ceiling_evidence():
    """A handled prior failure's sentence is not this failure's evidence.

    #7357 cycle 4, measured: `OutOfMemoryError("OOM failed to allocate 1GB")`
    raised inside an `except ResponseError("command not allowed when used memory
    > 'maxmemory'")` block inherits that sentence through Python's IMPLICIT
    `__context__` chaining. The ceiling there belongs to a different, already
    handled failure — so asserting it is the same over-claim one level of
    indirection out, in the arm whose whole job is to stop that.

    `raise ... from` IS the author stating causality, so `__cause__` still
    licenses the assertion; the FAMILY predicate keeps walking `__context__`
    (there a wider walk is the safe direction — it only widens what is told to
    the caller, not what is claimed about the instance).

    NON-VACUOUS: walk `__context__` in `_chain_names_the_ceiling` and the first
    assertion reddens (the ceiling sentence comes back).
    """
    prior = redis_exc.ResponseError(
        "command not allowed when used memory > 'maxmemory'.")
    failure = redis_exc.OutOfMemoryError("OOM failed to allocate 1GB")
    failure.__context__ = prior
    assert failure.__suppress_context__ is False, (
        "the fixture must be the IMPLICIT chain, not `from None`")
    assert is_memory_ceiling_refusal(failure) is True, (
        "still the family — but BY CLASS on the top link here; the CONTEXT half "
        "of the family walk is pinned by "
        "test_the_family_walk_follows_an_implicit_context_to_a_ceiling, below")
    hedged = explain_memory_ceiling(failure) or ""
    assert "it has reached its configured dataset limit" not in hedged

    # …while an EXPLICIT cause still licenses it.
    explicit = redis_exc.OutOfMemoryError("OOM failed to allocate 1GB")
    explicit.__cause__ = _ceiling_error()
    assert "it has reached its configured dataset limit" in (
        explain_memory_ceiling(explicit) or "")


def test_the_family_walk_follows_an_implicit_context_to_a_ceiling():
    """COVERAGE for the ``__context__`` half of the family walk.

    Not a regression guard for this PR's hedge split — ``main`` already walked
    ``__context__`` here. It is the shape nothing else in this file reaches:
    every other use of the ceiling predicate is admitted by the TOP link (by
    class, or through ``__cause__``), so narrowing the walk to ``__cause__``
    alone would redden nothing, while the predicate's own docstring records the
    wider walk as what closed #7357's measured shape.

    NON-VACUOUS relative to that narrowing: delete the ``__context__`` fallback
    in ``is_memory_ceiling_refusal`` and the first two assertions redden (both
    cases below were run against exactly that mutation).
    """
    # (a) the context names the ceiling in its own TEXT; the top link is not
    # the family at all, so nothing but the walk can admit it.
    text_half = redis_exc.ResponseError("write failed")
    text_half.__context__ = redis_exc.ResponseError(CEILING_SENTENCE)
    assert text_half.__cause__ is None
    assert text_half.__suppress_context__ is False, (
        "the fixture must be the IMPLICIT chain, not `from None`")
    assert is_memory_ceiling_refusal(text_half) is True

    # (b) …and by its CLASS, which is the shape the driver itself mints.
    class_half = redis_exc.ResponseError("write failed")
    class_half.__context__ = redis_exc.OutOfMemoryError(
        "OOM failed to allocate 1GB")
    assert is_memory_ceiling_refusal(class_half) is True

    # …while `raise ... from None` DETACHES the context: the author said this
    # failure is not that one, so the walk stops and no note is owed.
    detached = redis_exc.ResponseError("write failed")
    detached.__context__ = redis_exc.ResponseError(CEILING_SENTENCE)
    detached.__suppress_context__ = True
    assert is_memory_ceiling_refusal(detached) is False


# ── the wiring, through a real guarded client ───────────────────────────────
#: The verbs the pinned engine actually refuses at the ceiling, MEASURED
#: 2026-10-21 on `falkordb/falkordb-server:v4.20.6` (`maxmemory 5mb` <
#: `used_memory` 10.9 MB, `noeviction`): `GRAPH.QUERY` (a WRITE **or a READ**
#: statement), `GRAPH.EXPLAIN` and `SET` answer
#: `OutOfMemoryError: command not allowed when used memory > 'maxmemory'.`
#: `GRAPH.RO_QUERY`, `GRAPH.LIST`, `PING` and `DEL` were NOT refused — they are
#: not `denyoom` verbs, so a read served through them still succeeds at the
#: ceiling. The fixture refuses exactly the measured set (#9532 review P2: it
#: used to raise for `GRAPH.RO_QUERY` too, pinning a shape the engine never
#: produces and hiding the read path that actually matters).
DENYOOM_VERBS = {"GRAPH.QUERY", "GRAPH.EXPLAIN", "SET"}


class _CeilingConnection:
    """Vendor-shaped connection (redis-py): the public verb IS the dispatcher.

    ``Redis.execute_command`` is only a façade — ``return
    self._execute_command(*args, **options)`` — so this is the shape
    ``_guard_connection`` rebinds BOTH names of, and the shape a guarded command
    therefore crosses on its way to the socket.
    """

    def __init__(self, error):
        self.error = error
        self.execute_command = self._execute_command
        self.commands: list = []

    def _execute_command(self, *args, **kwargs):
        self.commands.append(args)
        verb = str(args[0]).upper() if args else ""
        if verb not in DENYOOM_VERBS:
            # Faithful to the measurement: a non-denyoom verb is SERVED, so the
            # refusal cannot be raised for it. Silently raising here is what
            # made the earlier read test vacuous.
            return [[0]]
        # A FRESH instance per command, as a real driver does: the note mutates
        # the exception in place, so a shared instance would carry it forward.
        raise type(self.error)(*self.error.args)


class _CeilingGraph:
    """Vendor-shaped graph handle whose every command reaches *client*."""

    def __init__(self, client, name):
        self.client = client
        self.name = name
        self.execute_command = client.execute_command

    def query(self, q, params=None, timeout=None):
        return self._query(q, params=params, timeout=timeout)

    def ro_query(self, q, params=None, timeout=None):
        return self._query(q, params=params, timeout=timeout, read_only=True)

    def _query(self, q, params=None, timeout=None, read_only=False):
        return self.execute_command(
            "GRAPH.RO_QUERY" if read_only else "GRAPH.QUERY", self.name, q)


class _CeilingClient:
    """Vendor-shaped client answering every command with the engine's refusal."""

    def __init__(self, error=None):
        self.error = error if error is not None else _ceiling_error()
        self.connection = _CeilingConnection(self.error)
        self.execute_command = self._execute_command
        self.commands: list = []

    def _execute_command(self, *args, **kwargs):
        # As redis-py does: the façade delegates to the connection's channel, so
        # a handle command really crosses TWO appliers — `_forward_query` on the
        # guarded handle proxy, then the connection's `_GuardedExecuteCommand`
        # (rebound in place by `_guard_connection`). A façade that raised
        # directly would cross only the first, which is the vacuous fixture
        # the #9532 review caught.
        self.commands.append(args)
        return self.connection.execute_command(*args, **kwargs)

    def select_graph(self, name):
        return _CeilingGraph(self, name)


def _guarded(error=None):
    client = cypher_guard.guarded_client(lambda **kw: _CeilingClient(error))
    graph = client.select_graph("tortoise")
    return client, graph


def test_a_guarded_handle_query_carries_the_ceiling_note():
    _client, graph = _guarded()
    with pytest.raises(redis_exc.OutOfMemoryError) as caught:
        graph.query(WRITE_STATEMENT)
    message = str(caught.value)
    assert message.startswith(CEILING_SENTENCE), (
        "the driver sentence must stay at position 0: a retry predicate and "
        "every caller text match reads from there")
    assert _LEGIBLE_OPENER in message
    assert "memory-ceiling" in message.lower()


def test_a_guarded_handle_passes_the_hedge_for_a_class_only_refusal():
    """The seam a caller hits: an `OOM` reply with no ceiling in its text.

    The helper tests above prove `explain_memory_ceiling` branches; this proves
    the BRANCH REACHES THE CALLER through the same guarded channel as the
    measured shape, so the fix cannot be a helper nobody wires up. The assertion
    on the absent ceiling sentence is the #7357 defect itself.
    """
    error = redis_exc.OutOfMemoryError("OOM failed to allocate 1GB")
    _client, graph = _guarded(error)
    with pytest.raises(redis_exc.OutOfMemoryError) as caught:
        graph.query(WRITE_STATEMENT)
    message = str(caught.value)
    assert message.startswith("OOM failed to allocate 1GB")
    assert "it has reached its configured dataset limit" not in message
    assert "`maxmemory 0`" in message
    assert message.count(_LEGIBLE_OPENER) == 1


def test_a_guarded_read_gets_the_same_note_and_no_write_ambiguity():
    """A refusal is a refusal: the note does not split read/write.

    The transport arm says "OUTCOME IS UNKNOWN, read back first" for a mutating
    statement because the REPLY was lost. Here the reply arrived, so the outcome
    is known for both shapes — and claiming ambiguity would send the caller to
    read back a statement that provably did not run.

    P2 (#9532 review): this drives a READ STATEMENT through ``graph.query`` —
    the ``GRAPH.QUERY`` verb, which IS ``denyoom`` and is measured refused — and
    not ``ro_query``, which the engine serves at the ceiling. The earlier
    version asserted the note on a shape production never produces.
    """
    _client, graph = _guarded()
    with pytest.raises(redis_exc.OutOfMemoryError) as caught:
        graph.query(READ_STATEMENT)
    message = str(caught.value)
    assert "memory-ceiling" in message.lower()
    assert "OUTCOME IS UNKNOWN" not in message
    assert message.startswith(CEILING_SENTENCE), (
        "a read statement on the denyoom verb is refused the same way, so the "
        "driver sentence must still be at position 0")


def test_a_verb_the_engine_still_serves_is_never_annotated():
    """The boundary the note must not cross (#9532 review P2).

    ``GRAPH.RO_QUERY`` is not ``denyoom``: the pinned engine serves it at the
    ceiling. Annotating it would invent a refusal that did not happen — the
    same defect class as the illegible message this PR fixes, pointing the
    other way. This pins that the note is the answer to a REAL refusal, and
    keeps the fixture honest about which verbs produce one.
    """
    _client, graph = _guarded()
    assert graph.ro_query(READ_STATEMENT) == [[0]], (
        "the fixture must serve a non-denyoom verb, not raise for it")
    served = graph.client.connection.commands[-1]
    assert served[0] == "GRAPH.RO_QUERY"


def test_the_client_command_channel_carries_the_note_too():
    """The second channel (a raw `execute_command`) is the same seam."""
    client, _graph = _guarded()
    with pytest.raises(redis_exc.OutOfMemoryError) as caught:
        client.execute_command("GRAPH.QUERY", "tortoise", WRITE_STATEMENT)
    assert "memory-ceiling" in str(caught.value).lower()


def test_a_duck_typed_handle_proxy_carries_the_note():
    """The proxy dispatches straight to its wrapped handle, so it needs its own
    application (the class it claims to cover must not overstate)."""

    class _Stub:
        name = "tortoise"

        def query(self, q, params=None, timeout=None):
            raise _ceiling_error()

    proxy = cypher_guard._guard_handle(_Stub())
    with pytest.raises(redis_exc.OutOfMemoryError) as caught:
        proxy.query(WRITE_STATEMENT)
    assert "memory-ceiling" in str(caught.value).lower()


def test_the_note_is_applied_once_across_two_nested_channels(monkeypatch):
    """A handle verb and the connection channel both wrap the same failure.

    The fixture must make the delegations REAL, or this test proves nothing
    (#9532 review): `_GuardedClientProxy` guards the connection IN PLACE and
    `_guard_connection` rebinds both of its command entries, but a client whose
    `_execute_command` raised directly would cross ONE applier — and then
    `count == 1` would hold with no opener guard at all. So `_CeilingClient`
    delegates to `self.connection` exactly as `redis.Redis` does, and the
    applier count is ASSERTED (>= 2) rather than assumed: the chain is
    `_forward_query` (the handle proxy) then `_GuardedExecuteCommand` (the
    connection), two chances to append one suffix.

    The opener is the shared idempotency marker; without it the note grows a
    second suffix per layer — the #7357 double-note defect. NON-VACUOUS:
    delete `if _LEGIBLE_OPENER in text` from `explain_memory_ceiling` and the
    `count == 1` assertion below reddens (the applier count stays at 2).
    """
    appliers: list = []
    real_applier = cypher_guard._note_instance_outage

    def counting_applier(exc, *, mutating):
        appliers.append(mutating)
        real_applier(exc, mutating=mutating)

    # Both call sites (`_forward_query`, `_explain_instance_unavailable`) look
    # the applier up on the MODULE, so this counts every layer in the chain.
    monkeypatch.setattr(cypher_guard, "_note_instance_outage", counting_applier)

    _client, graph = _guarded()
    with pytest.raises(redis_exc.OutOfMemoryError) as caught:
        graph.query(WRITE_STATEMENT)
    message = str(caught.value)
    assert len(appliers) >= 2, (
        "the fixture stopped crossing two appliers, so the single-suffix "
        f"assertion below is vacuous (appliers={len(appliers)}) — the client "
        "must delegate to its guarded connection as redis.Redis does")
    assert message.startswith(CEILING_SENTENCE)
    assert message.count(_LEGIBLE_OPENER) == 1, message
    assert "memory-ceiling" in message.lower()


def test_the_ordering_not_the_disjointness_keeps_one_suffix():
    """P3 (#9532 cycle 3): the two predicates are NOT universally disjoint.

    The docstrings of :func:`is_memory_ceiling_refusal` and
    ``cypher_guard._note_instance_outage`` claimed the two families "cannot both
    answer True for one exception" / "are provably disjoint". The class
    partition is real, but it is per LINK: both predicates walk the
    ``__cause__``/``__context__`` chain (this PR added that walk), so a
    ``TimeoutError`` whose cause is the engine's ceiling refusal is admitted by
    BOTH. What actually keeps a caller-visible message to ONE suffix is the
    ORDER in the applier — the outage arm first, the ceiling arm only as its
    fallback — plus the shared ``_LEGIBLE_OPENER`` marker.

    This pins the mechanism instead of the claim, because with both predicates
    answering True an arm swap would apply the ceiling note ("Nothing was
    applied … re-issuing it now will be refused the same way") to a transport
    failure whose REPLY WAS LOST — the overclaim
    :func:`explain_instance_unavailable` exists to forbid.

    NON-VACUOUS: swap the two arms in ``_note_instance_outage`` (ceiling first)
    and the "no ceiling note" assertion below reddens — the ceiling arm returns
    a note, so the outage fallback never runs.
    """
    chained = redis_exc.TimeoutError("Timeout reading from socket")
    chained.__cause__ = _ceiling_error()
    # Both predicates admit it: the top link is a transport failure, the cause
    # is the ceiling family. That is the fact the docstrings now state.
    assert is_instance_unavailable(chained) is True
    assert is_memory_ceiling_refusal(chained) is True

    cypher_guard._note_instance_outage(chained, mutating=True)
    message = str(chained)
    assert message.startswith("Timeout reading from socket"), (
        "the driver sentence must stay at position 0")
    assert message.count(_LEGIBLE_OPENER) == 1, message
    assert "OUTCOME IS UNKNOWN" in message, (
        "a lost reply is the outage note's case, not the ceiling's")
    assert "memory-ceiling" not in message.lower(), (
        "the ceiling note must never be applied to a transport failure: it "
        "claims nothing was applied and no retry is needed, but a lost reply "
        "proves nothing either way — the outage arm running FIRST is what "
        "guarantees this, not the sets being disjoint")
