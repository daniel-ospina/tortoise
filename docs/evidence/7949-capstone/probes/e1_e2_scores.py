"""E1 and E2 scored from the #9889 measurement artifacts — ONE invocation.

Why this exists. `capstone_measure.py` could only score E1/E2 by parsing
`docs/evidence/7949-capstone/e1-e2.log`, a fixed artifact whose Object
population is a 25-name owner-labelled audit set (below E1's frozen floor of 30),
whose corpus is not the pre-registration's frozen one, and which carries **one**
arm — so both rows were reported `BLOCKED`/`REFUSED` no matter what a fresh run
held. `#9889`'s three probes produced the missing halves (`#10342`); this driver
is the piece that makes the table's rows *theirs*: it reads the probes' records
and emits E1 and E2 (both halves) as one scoreable artifact, so
`capstone_measure.py` no longer has to reach for the superseded log.

It measures nothing itself. Every number is read out of a committed record that
names the command and the graph that produced it (`docs/evidence/9889/README.md`
quotes each command). That is the point: the row cites an artifact, and the
artifact cites a probe run.

What each number is, and what it is NOT — stated here so a reader does not have
to find it:

* **E1** is `after_share <= multiplier x before_share` over ALL `:Object` nodes
  written by the run, floored at 30 Objects. BOTH numbers come out of the
  pre-registration by `capstone_measure.parse_bars` — the multiplier (0.50 today)
  from `criteria[E1].threshold`, the two floors from `denominator_floors` — so
  this record cannot score against a bar the record did not freeze. The before
  arm exists in TWO independent realizations (two OFF runs, because a single one
  cannot show whether a difference is the arm or the model) and the after arm in
  one (the ON run).
  Pairing the single after arm against each before arm gives **two different
  verdicts**, so the honest aggregate is `decidable: false` — the metric is
  REFUSED at n=1 per arm, and the record prints both pairings rather than
  picking the convenient one.
* **E2a** is the count of the frozen **14** value-bearing planted units that
  strictly survive. The denominator is derived by rule in the pre-registration
  §3.3 and is read from the probe's record, never typed here.
  `e2a-value-units-falsifier.json` is the FALSIFIER: it is the same measurement
  with an anchor injected for every lost unit, and it must reach 14/14 — a
  counter that reports the same number with and without the injection is inert,
  and this driver refuses rather than reporting its low number as a measurement.
* **E1b** and **E2b** are the log-derived controls they already were. They stay
  log-derived on purpose: no probe emits them, and pretending otherwise would be
  the same "evidence behind the number" defect in the other direction.

Usage::

    uv run python docs/evidence/7949-capstone/probes/e1_e2_scores.py \\
        --out docs/evidence/9889/e1-e2-scores.json

Exit status: 0 when a scoreable record was written; **2** when any input record
is missing, when a threshold cannot be parsed out of the frozen record, when an
input record, block, census or floor is a shape the reader cannot grade — not
JSON, not UTF-8 text, not a JSON object, a `label_counts` census that is absent
or carries a non-numeric `:Object`, a census member that is not a unit id, a
census that is not a partition (a unit both kept and lost, or one listed twice),
a count or floor that is not a finite number, or a `corpus.fixtures_hash` that is
not a string, or an `object_names` member that is not an object name, or an
`object_names` census that lists the same member twice (a population, not a
multiset) — when the
E2a record grades a denominator other than the frozen **14** (a bar printed over
an unfrozen population), when the E2a falsifier did not reach the frozen
denominator (an inert counter), when the ON arm and the E2a counts come from
different graphs (two populations in one record), when the falsifier's SCORED
`with_anchor` block is not the very run the E2a record was taken on — the same
head is not the same run, because a graph is `capstone_on_<head>_<stamp>`, and
naming the head is not enough — when the TWO before artifacts carry the SAME
`object_names` population (a fresh graph stamp over one population is one
realization, not two, so the pairing set they produce can only ever agree; and an
artifact naming no graph at all is refused as absent provenance), when a
`with_anchor` block is PRESENT but EMPTY
(a record that never wrote its counter is not the flat shape), when its
`as_written` block carries no graph (an
absent provenance is not an inherited one), sits on another run, or reads counts
that are not the E2a record's (that block IS the E2a reading), when the falsifier's
SCORED block does not declare the injection it is credited for (`injected_anchor`
`is not True` — an un-injected reading reaching 14/14 is the inert counter under
another name), when a share is not the fraction its OWN `bare_definite` / `objects`
counts give (on either arm — the share is the one number E1's bar is scored on, and
nothing may edit it while the counts printed beside it stand), when a graph name
does not carry the measured head under the prefix the PRODUCERS write (`<head8>`),
when the
value-gate positive controls are dead, when a before arm's `arm` block is
absent, PRESENT but EMPTY, or missing one of its four switches (a key that was
never written is not a key that was written as unset), when the after arm's own
`# arm:` readback does not show the two measured passes ON or does not show the
two switches that must stay OFF, or when a before/after share is absent —
because a score computed over an unproven counter is the exact failure mode this
bundle exists to stop.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[4]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

#: The records this driver reads, all of them committed by `#10342`.
EVIDENCE = Path("docs/evidence/9889")
OFF_ARMS = ("e1-paired-off-arm-1.json", "e1-paired-off-arm-2.json")
ON_ARM_LOG = "on-arm.log"
E2A_RECORD = "e2a-value-units.json"
E2A_FALSIFIER = "e2a-value-units-falsifier.json"
GATE_DROPS = "e1-gate-drops.json"
PREREG = Path("docs/evidence/7949-capstone/preregistration.json")
# The switches `e1_paired_arm.py` forces OFF for a before arm and refuses to run over
# when they are already set (#9889 acceptance item 4). It is FOUR keys, not the three
# the acceptance list names: `TORTOISE_S2_DECISION_SEAM` arms S2.2 *through the
# arbiter*, so `_vet_enabled` reads True even against an explicit `TORTOISE_VET=0`
# (`e1_paired_arm.SEAM_KEY`'s own comment, and #5087). The driver re-reads all four
# from each artifact's own `arm` block, because it is the driver that puts the number
# in a scored row.
_ARM_VARS = ("TORTOISE_VET", "TORTOISE_CLASSIFY_LATER", "TORTOISE_VALUE_GATE",
             "TORTOISE_S2_DECISION_SEAM")
# The four pin fields the shipped `e1_paired_arm.py` writes on every record it
# produces. The two committed before arms predate them (README, "Which probe
# revision produced each artifact"), which is why absence is DISCLOSED rather than
# refused here — see `_before_corpus_provenance`.
_BEFORE_PIN_FIELDS = ("head", "fixtures_hash", "object_floor",
                      "min_before_share")
#: The repo's declared env-truthiness vocabulary, mirrored rather than imported.
#:
#: `tortoise/env_truthy.py` is the ONE contract ("unset/blank/falsy/garbage -> False"),
#: and it is stdlib-only precisely so a module can carry the same rule without an
#: import cycle — `tortoise/embedded_reaper.py` and `tortoise/hermes-plugin/__init__.py`
#: already MIRROR it, and `tests/test_env_truthy.py` pins every mirror. This driver
#: follows that precedent, and `test_the_arm_vocabulary_is_the_repos_truthiness_contract`
#: pins the copy against the real `is_truthy`/`TRUTHY`/`FALSY`.
#:
#: ⛔ THE DENY-LIST WAS THE BUG (#10610 review at `f12471075`, P1). `_ARM_OFF_STATES`
#: was a hand-typed list of OFF spellings, used to decide BOTH directions, while the
#: pipeline decides with `is_truthy` — an ALLOW-LIST. So `TORTOISE_VET=off` in the ON
#: run's readback was accepted as the ON half of E1's pair (`is_truthy("off") is
#: False` — the treatment was OFF, and the pair was two control arms), and the same
#: list REFUSED a genuine before arm that spelled its switches `off`/`no`. The two
#: directions need the repo's own predicate, not a list to compare spellings against.
_TRUTHY = ("1", "true", "yes", "on")
_FALSY = ("0", "false", "no", "off")


def _is_truthy(raw) -> bool:
    """`tortoise.env_truthy.is_truthy`, mirrored: unset/blank/falsy/garbage -> False."""
    return ("" if raw is None else str(raw).strip().lower()) in _TRUTHY
# The measured arm is the mirror image: it must have been ON. `capstone_measure`
# writes that readback onto the log this driver already parses (`# arm: …`), so
# nothing about the after side needs to be assumed either.
_ARM_ON_VARS = ("TORTOISE_VET", "TORTOISE_CLASSIFY_LATER")
# …and the same arm PINS two switches OFF: `capstone_measure.ARM_OFF`, whose own
# comment says the value gate "also switches S2.2 on … an ambient value would
# silently change the arm between two runs of the same probe" and the seam "would
# arm a network call and a spend inside an arm this file declares OFF". Checking
# only the ON half accepts a log measured with `TORTOISE_VALUE_GATE=1` — the
# value-gate arm `E1-E2.md` measures separately — as the ON half of E1's pair.
# Kept as a literal rather than an import because this module is loaded before
# `capstone_measure`; `test_the_arm_key_sets_are_the_producers` holds the two
# definitions equal, so they cannot drift apart silently.
_ARM_OFF_VARS = ("TORTOISE_VALUE_GATE", "TORTOISE_S2_DECISION_SEAM")

#: The subset of :data:`_ARM_OFF_VARS` whose UNSET value is **ON** since `#10996`.
#:
#: ⛔ "PINNED OFF" AND "LEFT UNSET" STOPPED BEING THE SAME STATE (review cycle 2,
#: P1). `tortoise/value_gate.value_gate_enabled` reads
#: `is_truthy(os.environ.get(ENV_FLAG, "1"))`, so an ABSENT `TORTOISE_VALUE_GATE`
#: is now the TREATMENT — while a blank or garbage value is still OFF, because
#: those are PRESENT and `is_truthy` reads them falsy. Reading absence as an OFF
#: spelling therefore certified the treatment under the control's label, and E1's
#: pair (`after_share <= 0.50 x before_share`) can be built from TWO gate-armed
#: arms and still be scored as decided — the exact case this file's own comment
#: says must be refused ("scoring it as one compares a treatment against itself
#: and still reports a decided E1").
#:
#: Both producers this driver reads now PIN the key explicitly
#: (`capstone_measure.ARM_OFF` and `e1_paired_arm.DEFAULT_ON_KEYS` both write
#: `"0"`), so a record that spells it as absent can only have been produced by
#: the pre-`#10996` code. :func:`_absent_default_on_refusal` admits that ONE
#: legacy reading, under the campaign-head licence, and refuses every other.
#:
#: `TORTOISE_S2_DECISION_SEAM` is deliberately NOT here: it still defaults OFF
#: (it arms a paid network call), so absence remains a genuine OFF spelling for
#: it before and after the flip. Only the key whose DEFAULT MOVED belongs here,
#: and `test_the_default_on_key_set_is_the_one_whose_default_moved` pins this
#: literal against the real resolver rather than against this comment.
_DEFAULT_ON_VARS = ("TORTOISE_VALUE_GATE",)

#: The head the whole committed campaign was measured at — the ONLY head at which
#: an absent `TORTOISE_VALUE_GATE` is still a record of an OFF switch.
#:
#: ⛔ THIS IS THE LEGACY LICENCE, AND IT IS A HEAD, NOT A NAME. Every record this
#: bundle scores declares this head (`docs/evidence/9889/on-arm.log`'s `# head:`
#: line, and the two before arms' `capstone_off_54922a800_<stamp>` graph names),
#: and it is a commit in which
#: `tortoise/value_gate.py` still DEFAULTS THE PREDICATE OFF — so absence in a
#: record taken there really was OFF. Pinning the head is what keeps the licence
#: from becoming a spelling rule: a NEW run declares a NEW head, and so is refused
#: the legacy reading. The premise is not trusted, it is TESTED at that commit
#: (`test_the_legacy_head_really_predates_the_flip` reads
#: `git show <this head>:tortoise/value_gate.py` and asserts the default is not
#: ON) — if the campaign head ever contained the flip, that test reds and this
#: licence has to be revisited instead of silently widening. ⛔ THAT CHECK NEEDS
#: HISTORY: on a `fetch-depth: 1` checkout (CI's `test` job) the ancestor is
#: absent by construction, so the pin SKIPS and names the premise UNCHECKED
#: rather than passing; an unreadable head in a FULL clone FAILS, because there
#: the sha is wrong rather than unreachable.
_PRE_FLIP_HEAD = "54922a800f96858935b58ef250da569a4896df44"

#: The shortest head prefix the PRODUCERS actually write into a graph name.
#:
#: `capstone_measure.py` builds its own default graph as
#: `capstone_measure_{head[:8]}_{stamp}`, and all three probes' `--graph` usage
#: documents `capstone_off_<head8>_<epoch>` / `capstone_measure_<head8>_<epoch>`.
#: The committed records happen to carry NINE characters
#: (`capstone_on_54922a800_1793378318`), so a driver that required nine refused a
#: graph produced by a producer's OWN documented command — fail-closed (no wrong
#: number), but "a tool that cannot answer its own question" (#10610 review P3).
#: Matching the SHORTER prefix accepts both spellings and still rejects a graph
#: naming a different head; the two spellings below are pinned.
_HEAD_PREFIX = 8

#: The repo's own bare-definite SHAPE rule, copied here as a literal rather than
#: imported, so this driver stays stdlib-only (it runs before any DB is up).
#:
#: `capstone_measure._is_bare_definite` defers to
#: `tortoise.definite_description_backfill.is_bare_definite_description`, which is
#: `_ARTICLE.match(name.strip()) is not None` for `_ARTICLE =
#: re.compile(r"^the[ \t]+(?P<body>\S(?:.*\S)?)$", re.IGNORECASE)`. The two are
#: held EQUAL by `test_the_bare_definite_shape_is_the_repos_own_predicate`, over
#: the shipped names and a synthetic corpus, so this copy cannot drift into
#: agreeing with a record it should refuse.
_BARE_ARTICLE = re.compile(r"^the[ \t]+\S(?:.*\S)?$", re.IGNORECASE)


def _is_bare_definite(name: str) -> bool:
    """Whether ``name`` has the ``the <body>`` shape (repo predicate, copied)."""
    raw = str(name or "").strip()
    return bool(raw) and _BARE_ARTICLE.match(raw) is not None


def _is_absent_pin(raw) -> bool:
    """Whether a record spells a switch as NOT IN THE ENVIRONMENT.

    `None` is the JSON producer's spelling (`os.environ.get(var)` on a key that
    was never set) and `<unset>` is `capstone_measure.arm_header`'s. Neither is a
    falsy VALUE: since `#10996` the default-ON key reads ON when it is absent, so
    absence is that key's ON spelling and can no longer certify an OFF pin.
    A BLANK value is not absence — `TORTOISE_VALUE_GATE=` is present, and
    `is_truthy` reads it falsy, exactly as the pipeline does.
    """
    return raw is None or str(raw).strip() == "<unset>"


def _names_pre_flip_head(head: str) -> bool:
    """Whether ``head`` is a prefix of :data:`_PRE_FLIP_HEAD` (a 40-hex branch
    records the full sha, the graph names carry eight or nine characters)."""
    wanted = str(head or "").strip()
    return bool(wanted) and _PRE_FLIP_HEAD.startswith(wanted)


def _absent_default_on_refusal(where: str, var: str, licence: str | None) -> None:
    """Refuse an `#10996`-moved switch that is recorded as ABSENT, not falsy.

    The two producers write an explicit `"0"` (`capstone_measure.ARM_OFF`,
    `e1_paired_arm.DEFAULT_ON_KEYS`), so absence is the ONE state they no longer
    produce — it only survives in records taken at :data:`_PRE_FLIP_HEAD`, where
    the default still made it OFF. `licence` is that legacy reading when the
    record has earned it, and `None` otherwise; anything the shipped producers
    can write must therefore be explicit, which is what stops the next corpus
    from producing a silently-misconfigured pair.
    """
    if licence is not None:
        return
    raise _refuse(
        f"{where} records {var} as ABSENT rather than set to a falsy value. "
        "Since `#10996` the S2.2 mechanical half DEFAULTS ON, so an absent "
        f"{var} is the TREATMENT arm: a record that expresses OFF by absence "
        "does not show the passes were OFF, and scoring it as E1's control (or "
        "as the ON half while its pinned-OFF switches are absent) compares a "
        "treatment against itself and still reports a decided E1. Pin it "
        f"explicitly ({var}=0) and re-record — the legacy reading is admitted "
        f"only for the pre-`#10996` campaign head {_PRE_FLIP_HEAD[:9]}")


def _names_head(graph: str, head: str) -> bool:
    """Does ``graph`` name ``head`` under the prefix the producers write?

    ⛔ A SUBSTRING TEST IS NOT A NAME TEST (#10610 review at `75bfd9c6`). The
    previous form was `head[:8] in graph`, which the comment above claimed "still
    rejects a graph naming a different head" — it does not. Measured: setting an
    OFF arm's graph to `capstone_off_9152448d_54922a80` — a graph naming head
    `9152448d` with our own eight characters merely APPENDED — scored, and that
    arm's share was then used as a before realization of head `54922a80`.

    So the producers' documented shape (`<name>_<head>_<epoch>`) is parsed and the
    HEAD TOKEN is compared, not searched for: the token must be a prefix of the
    head the record claims (producers write eight, the committed records carry
    nine, so any prefix length is accepted), and it must be the token in the
    head's OWN position — the segment before the numeric epoch. A name that does
    not parse into that shape names no head this driver can check, so it refuses
    rather than being searched for a lucky substring.
    """
    parts = str(graph or "").split("_")
    if len(parts) < 3 or not parts[-1].isdigit() or not parts[-2]:
        return False
    token = parts[-2]
    wanted = str(head or "").strip()
    return bool(wanted) and len(token) >= 6 and wanted.startswith(token)


def _consistent_share(where: str, share, bare, objects) -> None:
    """Refuse a share the record's OWN counts do not reproduce.

    The share is the only operand E1's bar has, and every producer writes it as
    the float division of the two counts printed on the same line — so all three
    inputs for `share == bare_definite / objects` are already in hand wherever it
    is read. Nothing compared them (#10610 review P2), which made the share a
    TRUSTED TOKEN: editing only `share` in the two OFF records scored both
    pairings PASS on a share the record's own counts contradict, and the row —
    the one `#9889` is waiting on — was written GREEN off the edit.

    Every other guard in this file is a consistency check of exactly this shape
    (the `as_written` counts must equal the E2a reading, the E2a `of` must equal
    the frozen corpus denominator, the scored block's graph must BE the E2a
    record's). This is the one number that was USED and whose components were
    not checked against it. The tolerance is 1e-9: both producers write the
    unreconciled float division (`len(bare) / len(names)`, `bare / len(...)`), so
    the shipped artifacts reconcile exactly. A `None` state is a refusal rather
    than a skip — an absent count is not a default one.
    """
    if bare is None or objects is None:
        raise _refuse(f"{where} carries no bare_definite/objects beside its "
                      "share, so the share cannot be reconciled against the "
                      "counts it is supposed to be a fraction OF")
    # ⛔ CONVERT FIRST, THEN BOUND (#10610 review at `21b2af22`). The `objects <= 0` test used
    # to run BEFORE the conversion, so a non-numeric field escaped as a TypeError traceback
    # (exit 1) instead of the refusal the `except` below advertises — measured: `objects:
    # "78"` produced "ESCAPED TypeError: '<=' not supported between instances of 'str' and
    # 'int'". A run an operator cannot tell from a crash is not a refusal.
    #
    # ⛔ AND A NON-NUMBER IS REFUSED, not coerced. `float("78")` would ACCEPT a string field
    # and then the raw string travels on to the comparisons below, which is exactly how the
    # traceback got out; every artifact here is JSON with real numbers, so a string is an
    # edited record or a different producer's shape, not a value to be helpfully parsed.
    # `bool` is an `int` in Python, so it is excluded explicitly (the same rule
    # `capstone_measure.parse_bars`' floor helpers apply).
    for field, value in (("bare_definite", bare), ("objects", objects),
                         ("share", share)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _refuse(f"{where} carries {field}={value!r}, which is not a "
                          "number — a share and the counts it is a fraction of "
                          "are numeric fields, so this is an edited record or "
                          "another producer's shape")
    try:
        bare_n = float(bare)
        objects_n = float(objects)
        declared = float(share)
    except (TypeError, ValueError, OverflowError) as exc:
        # ⛔ `OverflowError` IS THE THIRD ARM (#10610 review at `fe77fb13f`, P2): an
        # unbounded JSON integer (`10**400`) converts to no float, and only
        # `TypeError`/`ValueError` were caught — so the guard raised a traceback
        # (exit 1) on the one input it exists to refuse.
        raise _refuse(f"{where} carries bare_definite={bare!r} / "
                      f"objects={objects!r} / share={share!r}, which is not a "
                      f"share: {exc}") from exc
    if not math.isfinite(objects_n) or objects_n <= 0:
        raise _refuse(f"{where} carries objects={objects}, so its share is not "
                      "a fraction of any population")
    # ⛔ THE NUMERATOR IS BOUNDED TOO (#10610 review at `21b2af22`). Only the lower bound was
    # checked, so a `bare_definite` ABOVE its own population — which no producer can write
    # (`len(bare) / len(names)`) and which is therefore an edit — reconciled happily against
    # `share = 100/78` and wrote a DECIDED `PASS`: measured, `bound 0.641` against an after
    # share of 0.2131 PASSES both pairings, flipping E1 from REFUSED to a decided PASS off
    # the numerator. A share outside [0, 1] is not a fraction of a population.
    if not (0.0 <= bare_n <= objects_n):
        raise _refuse(f"{where} carries bare_definite={bare} of objects={objects}, "
                      "which is not a subset of the population its share is a "
                      "fraction of — a share above 1 (or below 0) is a numerator "
                      "edited away from its own denominator")
    implied = bare_n / objects_n
    # ⛔ NON-FINITE IS A REFUSAL, not a value (#10610 review at `e079c8dd`). `share=nan`
    # parses fine, and `abs(nan - implied) > 1e-9` is False — every comparison against NaN
    # is False — so the reconciliation below PASSED and a log with `share=nan` wrote a
    # DECIDED E1 record (`outcome: FAIL`, `decidable: true`) instead of refusing. Measured
    # on `on-arm.log` with the share set to `nan`: exit 0, record written, both pairings
    # FAIL, ratio `nan`. `inf` has the same shape (abs(inf - implied) > 1e-9 is True, so it
    # refused by luck, not by rule).
    if not (math.isfinite(declared) and math.isfinite(implied)):
        raise _refuse(f"{where} reports share={share!r} over counts "
                      f"({bare} of {objects}), which is not a finite number — a share "
                      "must be a fraction of a real population, not nan or inf")
    if not math.isclose(declared, implied, rel_tol=0.0, abs_tol=1e-9):
        raise _refuse(f"{where} reports share={share!r} but its own counts "
                      f"({bare} of {objects}) give {implied!r} — the share is "
                      "the operand E1's bar is scored on, so a record whose "
                      "share contradicts the counts printed beside it would "
                      "score a number nothing in it measured")


def _read_json(path: Path) -> dict:
    if not path.is_file():
        raise _refuse(f"{path} does not exist — a row cannot cite an artifact "
                      "the tree does not carry")
    try:
        # `encoding="utf-8"` deliberately, not the locale default (#10610 review at
        # `2ffc5db19`, P3): `read_text()` with no encoding decodes with the AMBIENT
        # locale, so under a latin-1 locale a latin-1 record parsed and was scored
        # while this refusal's message promised "not UTF-8". No fail-open either way
        # (both refuse), but the contract must not depend on the box's locale.
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        # ⛔ A RECORD THAT IS NOT UTF-8 IS CORRUPT INPUT, NOT A CRASH (#10610 review
        # at `858675ade`, P2): the sibling `is not JSON` arm exists precisely to turn
        # corrupt input into an exit-2 refusal, and a truncated or UTF-16 file raised
        # `UnicodeDecodeError` -> exit **1** beside it. `read_text()` was called
        # INSIDE the try but only `JSONDecodeError` was caught.
        raise _refuse(f"{path} is not UTF-8 text, so it cannot be the record it "
                      f"is cited as: {exc}") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:  # pragma: no cover - corrupt input
        raise _refuse(f"{path} is not JSON: {exc}") from exc
    # ⛔ THE TOP LEVEL IS AN OBJECT OR IT IS REFUSED (#10610 review at `73382c7e6`,
    # P2). Every reader below grades NAMED fields, so a file holding a list, a
    # number or a string reached `.get(...)` as an `AttributeError` — exit **1**
    # with a traceback, which an operator cannot tell from a crash, and not the
    # exit-2 refusal this module's docstring promises. One guard here covers every
    # record this driver reads, rather than one per field.
    if not isinstance(payload, dict):
        raise _refuse(f"{path} carries a {type(payload).__name__} at the top "
                      "level, not a JSON object — this driver reads named "
                      "fields, so a record of another type is an edited file")
    return payload


def _object_block(where: str, field: str, value: dict) -> dict:
    """A record's named BLOCK must be a JSON object, or the reader refuses.

    ⛔ `(value or {})` IS NOT A GUARD (#10610 review at `73382c7e6`, P2). A
    non-empty STRING, number or list is truthy, so `value or {}` handed it to the
    caller unchanged and the next `.get(...)` raised `AttributeError` — exit **1**
    with a traceback instead of the exit-**2** refusal, the same class
    `_unit_id_members`, `_count`, `_float_token` and `_consistent_share` already
    convert-and-refuse. Measured at `73382c7e6` on copies of the shipped bundle:
    `"with_anchor": "none"` and `"as_written": "none"` both exited 1 with
    `AttributeError: 'str' object has no attribute 'get'` — at
    `(record.get("with_anchor") or {}).get("graph")`, the graph-identity loop that
    runs BEFORE the scored-block arms (which is why the first attempt at this fix,
    applied only to those arms, left the escape reachable; #10610 review at
    `858675ade` measured it there).

    The empty case is the CALLER's, not this helper's: `{}` is a well-formed block
    that wrote nothing, and each site refuses it with its own message ("present but
    empty is not the flat shape").
    """
    if not isinstance(value, dict):
        raise _refuse(f"{where} carries a {field} that is not a JSON object "
                      f"({type(value).__name__}: {value!r}) — the reader grades "
                      "that block field by field, so a value of another type is "
                      "an edited record")
    return value


def _refuse(message: str) -> SystemExit:
    """A refusal is exit **2**, never 1 — it is not an unhandled error.

    The two sibling probes document exit 2 for a refusal, and a caller that
    cannot tell "refused to measure" from "crashed" will treat the second as
    the first. `SystemExit("text")` exits 1, so the text is printed here and the
    status set explicitly.
    """
    print(f"e1-e2-scores: REFUSED — {message}", file=sys.stderr)
    return SystemExit(2)


def _count(where: str, field: str, value, ceiling: int | None = None) -> int:
    """A count field, bounded by the population it counts — or refused.

    ⛔ Every count in this file was a TRUSTED TOKEN until the review at
    `75bfd9c6` (#10610): `_consistent_share` bounded the SHARE's numerator by its
    denominator, and the E2a counter beside it was still compared straight against
    its bar. Three mutations went GREEN:

    * `value_survived: 20` against the frozen 14 wrote `E2 PASS — E2a 20/14`
      (a numerator above its own denominator is not a population);
    * `value_survived: Infinity` wrote `E2 PASS — E2a inf/14` — JSON carries the
      `Infinity` literal and `int.__ge__` accepts it;
    * `value_survived: "8"` escaped as `TypeError: '>=' not supported between
      instances of 'str' and 'int'` — exit 1 with a traceback, which an operator
      cannot tell from a crash, not the refusal the module docstring promises.

    So: numeric (never `bool`, which is an `int` in Python), finite, integral,
    non-negative, and no larger than the population it counts. `ceiling=None`
    means the caller bounds it itself (the E2a denominator is held to the frozen
    corpus count further down, and an unbounded read here must not pre-empt that
    refusal's more specific message).
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _refuse(f"{where} carries {field}={value!r}, which is not a number "
                      f"— a count is numeric, so this is an edited record or "
                      f"another producer's shape")
    try:
        finite = math.isfinite(value)
    except OverflowError as exc:
        # ⛔ AN ARBITRARY-PRECISION INTEGER IS THE INTEGER SIBLING OF `Infinity`
        # (#10610 review at `fe77fb13f`, P2). `json.loads` returns an unbounded
        # Python int for any integer literal, and `math.isfinite(10**400)` — like
        # `float(10**400)` — raises `OverflowError: int too large to convert to
        # float`, which is exit **1** with a traceback out of the guard whose whole
        # job is to refuse rather than crash.
        raise _refuse(f"{where} carries {field} at a magnitude no float can "
                      f"hold ({len(str(abs(value)))} digits) — a count of a real "
                      f"population, so this is an edited record: {exc}") from exc
    if not finite:
        raise _refuse(f"{where} carries {field}={value!r}, which is not a finite "
                      f"number — a count of a real population cannot be nan or "
                      f"inf")
    if value != int(value):
        raise _refuse(f"{where} carries {field}={value!r}, which is not a whole "
                      f"number — a count that is not integral is a ratio wearing "
                      f"a count's name")
    if ceiling is not None and not 0 <= value <= ceiling:
        raise _refuse(f"{where} carries {field}={value} of a population of "
                      f"{ceiling} — a count outside [0, population] is a number "
                      f"edited away from the census it counts")
    if value < 0:
        raise _refuse(f"{where} carries {field}={value}, which is negative — no "
                      f"population counts back below zero")
    return int(value)


def _unit_id_members(where: str, session_id, field: str, members: list) -> None:
    """Every census member is a UNIT ID — a string, or a refusal.

    ⛔ The census is compared as a SET, so a member of any other type raised
    straight out of `set(ids)`: `TypeError: unhashable type: 'list'` — exit **1**
    with a traceback, which an operator cannot tell from a crash, and NOT the exit
    **2** refusal this module's docstring promises (#10610 review at
    `ef4c448d7`, P2). It is the same class `_count`, `_float_token` and
    `_consistent_share` already convert-and-refuse in this file; this is the one
    guard the whole E2a partition rests on, so it cannot be the one that skips it.

    A unit id is a string by construction: `e2a_value_units.py:150-153` writes
    `value_ids` / `value_survived` / `value_lost`, the first two carried straight
    from the `survived` comprehensions at `:124-125` (as-written) and `:143-144`
    (with-anchor), and `value_lost` built on `:152` by
    `[v for v in value_ids if v not in set(survived)]`; `value_bearing_ids` reads
    `unit["id"]` out of the corpus gold — so a member that is not a string is an
    edited record, not a shape this reader can grade.

    (#10610 review at `858675ade`, P3: an earlier revision attributed `value_lost`
    to those `:124`/`:143` comprehensions. Both of them build `survived`, i.e. the
    `value_survived` list, and the miscitation left the one key this new guard
    exists for — `value_lost` — with no provenance at all.)
    """
    for member in members:
        if not isinstance(member, str):
            raise _refuse(
                f"{where}'s session {session_id!r} carries {field}={member!r}, "
                "which is not a unit id — the census is a set of string ids, so "
                "a member of another type is an edited record")


def _census_reconcile(where: str, block: dict, *, declared=None) -> None:
    """The totals a record STATES must be the sum of its own per-session census.

    ⛔ The E2a record carries the census the totals are summed from
    (`e2a_value_units.py` builds `units_total += len(value_ids)` and
    `survived_total += len(survived)` per session), and the driver read the totals
    and ignored the census (#10610 review at `75bfd9c6`). One field edit to the
    file a lane would reach for to unblock the row — `value_survived: 14` with the
    census untouched, which still sums to 8 — then wrote `E2 PASS — E2a 14/14`
    while the record's own detail said 8 units survived. That is the shiftiest
    possible forged green: a single number, no other file touched, and the
    artifact contradicts itself where nobody was looking.

    A record with no census is refused rather than trusted: every producer writes
    it, so its absence means the totals are the only surviving statement of the
    population, which is the state this check exists to exclude.
    """
    sessions = block.get("per_session")
    if not isinstance(sessions, dict) or not sessions:
        raise _refuse(f"{where} carries no `per_session` census, so its totals "
                      "are the only statement of the population it grades — "
                      "every producer writes the census those totals are summed "
                      "from, so an absent one is an edited record")
    units = survived = 0
    for session_id, row in sessions.items():
        if not isinstance(row, dict):
            raise _refuse(f"{where} carries a `per_session` row for "
                          f"{session_id!r} that is not an object")
        ids = row.get("value_ids")
        kept = row.get("value_survived")
        if not isinstance(ids, list) or not isinstance(kept, list):
            raise _refuse(f"{where} carries no value_ids/value_survived lists for "
                          f"session {session_id!r} — the census is what the "
                          "totals are summed from")
        # ⛔ THE MEMBERS ARE CHECKED BEFORE THE SET OPERATIONS (#10610 review at
        # `ef4c448d7`, P2): `set(ids)` on a list/dict member is an uncaught
        # `TypeError`, so an edited census exited 1 with a traceback instead of
        # the REFUSED line. Reading a member as a unit id is what makes the set
        # relation below a comparison of units rather than of whatever JSON held.
        _unit_id_members(where, session_id, "value_ids", ids)
        _unit_id_members(where, session_id, "value_survived", kept)
        # ⛔ THE CENSUS IS A SET RELATION, not four lengths (#10610 review at
        # `f12471075`, P2). `e2a_value_units.py` writes `value_lost` beside the two
        # lists (`[v for v in value_ids if v not in survived]`), so a row that lists
        # the same unit as both surviving and lost contradicts itself — measured: the
        # census `kept = ids` with `value_lost` untouched wrote `E2 PASS` while six
        # units appeared in BOTH. `len()` also counted duplicates, which inflates the
        # totals the bar is scored on.
        if len(set(ids)) != len(ids):
            raise _refuse(f"{where}'s session {session_id!r} lists a value unit "
                          "twice in value_ids — the census counts units, not rows")
        if len(set(kept)) != len(kept):
            raise _refuse(f"{where}'s session {session_id!r} lists a surviving unit "
                          "twice — a duplicated id inflates the count the bar is "
                          "scored on")
        if not set(kept) <= set(ids):
            raise _refuse(f"{where}'s session {session_id!r} reports surviving units "
                          "that are not among its own value_ids — a unit cannot "
                          "survive a population it was never in")
        # ⛔ ABSENCE IS NOT A DEFAULT ONE (#10610 self-audit of the guards at
        # `380a2b7a2`, P2). ⚠️ No review comment raised these: the review recorded
        # at that head returned CLEAN, so this finding and its three siblings came
        # from this lane's own audit of the pre-fix guards — a reader looking for a
        # review that names them will not find one. The
        # partition rule below was gated on `lost is not None`, so the ONE field a
        # forger deletes was the one that switched the rule off: measured on the
        # shipped bundle, deleting `value_lost` from every `per_session` row,
        # setting `value_survived = value_ids` and mirroring the falsifier's
        # `as_written` block wrote `E2 PASS — E2a 14/14`, exit 0 — the identical
        # forgery this block's own docstring records having measured and closed for
        # the row-present case. `e2a_value_units.py` writes `value_lost` on every
        # row (`[v for v in value_ids if v not in set(survived)]`) and all 21 rows
        # across the three shipped records carry it, so a row without it was edited,
        # not produced — which is the rule the rest of this file already applies
        # ("an absent provenance is not a default one").
        lost = row.get("value_lost")
        if lost is None:
            raise _refuse(f"{where}'s session {session_id!r} carries no "
                          "value_lost — the census is a PARTITION of its "
                          "value_ids, every producer writes the losing half, so "
                          "an absent one is a deleted field and not an empty list")
        if not isinstance(lost, list):
            raise _refuse(f"{where}'s session {session_id!r} carries a "
                          "value_lost that is not a list")
        _unit_id_members(where, session_id, "value_lost", lost)
        both = set(lost) & set(kept)
        if both:
            raise _refuse(f"{where}'s session {session_id!r} lists "
                          f"{sorted(both)!r} as BOTH survived and lost — the row "
                          "contradicts itself, so its totals describe no run")
        if set(lost) | set(kept) != set(ids) or len(set(lost)) != len(lost):
            raise _refuse(f"{where}'s session {session_id!r} does not partition "
                          "its value_ids into survived + lost — the census is a "
                          "partition of the population, not two independent "
                          "lists")
        units += len(ids)
        survived += len(kept)
    stated = declared if declared is not None else (
        block.get("value_units"), block.get("value_survived"))
    if (units, survived) != tuple(stated):
        raise _refuse(f"{where} states {stated[1]} of {stated[0]} value-bearing "
                      f"units survived, but its own `per_session` census sums to "
                      f"{survived} of {units} — the totals are the census, so a "
                      "record that disagrees with its own detail grades a "
                      "population it does not carry")


def _grep(text: str, pattern: str, *, what: str) -> str:
    match = re.search(pattern, text, re.M)
    if not match:
        raise _refuse(f"{ON_ARM_LOG} carries no {what} line")
    return match.group(1)


def _float_token(text: str, pattern: str, *, what: str) -> float:
    """A numeric log token, REFUSED if it is not a number.

    ⛔ `float(_grep(...))` was an uncaught `ValueError` (#10610 review at
    `f12471075`, P2): `share=abc` in the log exited 1 with a traceback and wrote
    nothing, which an operator cannot tell from a crash — the same class the
    previous round closed on the JSON side. A token that does not parse is a
    refusal the driver has to be able to print, not an exception it cannot catch.
    """
    raw = _grep(text, pattern, what=what)
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise _refuse(f"{ON_ARM_LOG}'s {what} token {raw!r} is not a number "
                      f"({exc}) — a log field that does not parse is not a "
                      "measurement") from exc
    if not math.isfinite(value):
        raise _refuse(f"{ON_ARM_LOG}'s {what} token {raw!r} is not finite")
    return value


def _int_token(text: str, pattern: str, *, what: str) -> int:
    """An integer log token, on the same refusal contract as `_float_token`."""
    value = _float_token(text, pattern, what=what)
    if value != int(value):
        raise _refuse(f"{ON_ARM_LOG}'s {what} token is {value!r}, which is not a "
                      "whole number — a count that is not integral is a ratio "
                      "wearing a count's name")
    return int(value)


#: The bundle's ONE bar reader, loaded from the sibling probe by path.
#:
#: E1's multiplier, E1b's 90, E2a's `13 of 14` and E2b's 0.05 are read out of
#: `preregistration.json` by `capstone_measure.parse_bars`, whose docstring states
#: the rule this driver obeys: *"a bar typed into this file is a bar nobody froze,
#: so a lane could move a threshold by editing the probe"*. This driver emits the
#: artifact BOTH capstone rows cite, so a second reading of the same bars would be
#: a second place a threshold can move — and two readers of one frozen number can
#: only disagree; they can never both be right. An amendment ("a NEW linked record,
#: never an edit to the frozen one") then moves this record with the probe that
#: scores it. Loaded by FILE PATH because `probes/` is not a package and this module
#: is executed as a script.
_BARS_READER: dict = {}


def _parse_bars(prereg: dict) -> dict:
    if not _BARS_READER:
        path = Path(__file__).with_name("capstone_measure.py")
        spec = importlib.util.spec_from_file_location(
            "_capstone_measure_bars", path)
        if spec is None or spec.loader is None:  # pragma: no cover - absent file
            raise _refuse(f"cannot load the bar reader at {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _BARS_READER["module"] = module
    # An unparseable threshold is a `ValueError` in the reader (G0's refusal
    # contract); here it is this driver's refusal — exit 2, not a traceback.
    #
    # ⛔ `OverflowError` IS THE THIRD ARM (#10610 review at `ec7b5e7fe`, P2), and
    # it is the one shape the rest of this file already refuses. Every floor is a
    # number read out of JSON, and `json.loads` returns an UNBOUNDED Python int for
    # any integer literal — so `denominator_floors.e1_min_before_share = 10**400`
    # reached `share_floor`'s `float(value)` (`capstone_measure.py:519`) and raised
    # `OverflowError: int too large to convert to float`, i.e. exit **1** with a
    # traceback out of the one loader whose sibling `_count` converts the identical
    # value into an exit-2 refusal ("AN ARBITRARY-PRECISION INTEGER IS THE INTEGER
    # SIBLING OF `Infinity`"). Measured at the pre-fix head: exit 1; an
    # `e1_objects` of the same magnitude beside it refused cleanly, so the asymmetry
    # sat inside one record's floor map.
    #
    # ⚠️ DEFENCE IN DEPTH, NOT THE LOAD-BEARING GUARD (#10610 self-audit at
    # `380a2b7a2`).
    # `frozen_denominators` now validates all three floors (finite, numeric, and
    # each floor > 0 — the positivity arm covers all THREE keys, not the share
    # alone) BEFORE this call, so the `10**400` case is answered there with a
    # message that names the field — and no test exercises this arm through the
    # floors any more. It is kept because `parse_bars` is the bundle's shared reader
    # and a bar path or a newly added floor could reach `float()` without passing
    # that validation; a reader should not mistake it for the guard that fires today.
    try:
        return _BARS_READER["module"].parse_bars(prereg)
    except (ValueError, OverflowError) as exc:
        raise _refuse(f"{PREREG}'s criteria cannot be parsed: {exc}") from exc


def frozen_denominators(repo: Path) -> dict:
    """The frozen floors, the frozen denominator AND the frozen bars.

    Every one is READ from the pre-registration. A bar typed into this file would
    be a bar nobody froze: amend E2a's threshold in the record and the ROW would
    move while the artifact the row cites kept the old one — the row and its
    evidence disagreeing is the exact class this bundle exists to refuse. The bars
    come from `capstone_measure.parse_bars`, the bundle's ONE reader, so this
    driver and the probe that scores the row cannot read two thresholds.
    """
    prereg = _read_json(repo / PREREG)
    # ⛔ THE TWO SUB-OBJECTS ARE SHAPE-CHECKED, not truthiness-defaulted (#10610 review
    # at `858675ade`, P2 — the same `(value or {})` shape cycle 3 fixed one level
    # down). `prereg["denominator_floors"] = 7` reached
    # `"e1_objects" not in floors` as `TypeError: argument of type 'int' is not
    # iterable`, and `corpus = "sha256:dead"` reached `.get` as an `AttributeError`
    # — both exit **1**, before any refusal. `None`/absent stay "missing", which the
    # named-key refusals below report with their own wording.
    floors = prereg.get("denominator_floors")
    floors = {} if floors is None else _object_block(
        str(PREREG), "denominator_floors", floors)
    for key in ("e1_objects", "e1_min_before_share", "e2b_surviving_units"):
        if key not in floors:
            raise _refuse(f"{PREREG} carries no denominator_floors.{key}")
    # ⛔ A FLOOR THAT IS NOT A FINITE NUMBER IS NOT A FLOOR (#10610 self-audit of
    # the guards at `380a2b7a2`, P2). These three are read RAW and then used as COMPARISON
    # thresholds, and JSON carries the `NaN`/`Infinity` literals — so
    # `denominator_floors.e1_min_before_share = NaN` made E1's floor describe
    # nothing: every `share < nan` is False, so the floor silently stopped binding
    # and a control arm too flat to measure E1 was accepted. Composed with a
    # zero-share before arm (legal under `_consistent_share` when `bare_definite:
    # 0`) the inert floor let the pairing through and the ratio at
    # `after["share"] / arm["share"]` raised `ZeroDivisionError: float division by
    # zero` — exit **1** with a traceback, the exact contract violation the two
    # `OverflowError` arms above close, one function over. `inf` was refused only
    # incidentally (`share < inf` happens to be True), which is luck and not a
    # rule — the same reasoning `e079c8dd`'s NaN-share pin states for the log.
    #
    # EVERY floor is additionally required to be POSITIVE, for one reason that
    # holds identically for all three (#10610 review at `120381532`, P2): each
    # consumer refuses a measurement that falls BELOW its floor —
    # `objects < floors["e1_objects"]` in `read_off_arms`, `after["objects"] <
    # floors["e1_objects"]` in `e1_pairings`, `after["atomic_points"] <
    # e2b_floor` in `score`, and `share < floors["e1_min_before_share"]` — and no
    # count and no share falls below zero. So a floor of `0` is cleared by every
    # reading, and the guard it feeds passes everything.
    #
    # Applied to the SHARE floor alone, this left the two COUNT floors as inert
    # `if` statements three lines away: `e1_objects: 0` disables both the
    # under-floor refusal in `read_off_arms` and the BLOCKED-vs-decided split in
    # `e1_pairings`, and `e2b_surviving_units: 0` makes `below_floor` permanently
    # False in `score` so the E2b REFUSED outcome can never fire. That is the same
    # "a floor that binds nothing is not a floor" class the finite check above
    # closes, one key over — closed here for all three keys, not just the share.
    #
    # For the share floor `0` also re-opens the zero division from the other side.
    for key in ("e1_objects", "e1_min_before_share", "e2b_surviving_units"):
        value = floors[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _refuse(f"{PREREG}'s denominator_floors.{key} is a "
                          f"{type(value).__name__} ({value!r}), not a number — a "
                          "floor is compared against a measurement, so a value of "
                          "another type cannot be one")
        try:
            finite = math.isfinite(value)
        except OverflowError as exc:
            raise _refuse(f"{PREREG}'s denominator_floors.{key} is at a magnitude "
                          f"no float can hold — a floor that no reading can be "
                          f"compared against: {exc}") from exc
        if not finite:
            raise _refuse(f"{PREREG}'s denominator_floors.{key} is {value!r} — a "
                          "floor that is not finite binds nothing (every "
                          "comparison against NaN is False), so it is not a "
                          "floor")
        if value <= 0:
            raise _refuse(f"{PREREG}'s denominator_floors.{key} is {value!r} — a "
                          "floor of zero or less binds nothing: its consumer "
                          "refuses a measurement that falls BELOW the floor, "
                          "and no count or share falls below zero, so every "
                          "reading would clear it and the guard it feeds would "
                          "pass everything")
    corpus = prereg.get("corpus")
    corpus = {} if corpus is None else _object_block(
        str(PREREG), "corpus", corpus)
    if not corpus.get("fixtures_hash"):
        raise _refuse(f"{PREREG} carries no corpus.fixtures_hash")
    # ⛔ A PIN THAT IS PRESENT BUT NOT A STRING IS REFUSED HERE (#10610 review at
    # `73382c7e6`, P2). Truthiness accepted `12345`, `{"a": 1}`, `[1, 2]` and
    # `true`, and every one of them then reached `normalise_pin`'s
    # `value.startswith` as an `AttributeError` — exit **1**, not the exit-2
    # refusal. It was the single unguarded member of an otherwise-guarded family
    # (`value_bearing_units`, the three `denominator_floors` entries and E2a's
    # `of` all refuse by type), so the pin the whole E1 corpus comparison rests on
    # was the one value nobody type-checked.
    if not isinstance(corpus["fixtures_hash"], str):
        raise _refuse(f"{PREREG}'s corpus.fixtures_hash is a "
                      f"{type(corpus['fixtures_hash']).__name__} "
                      f"({corpus['fixtures_hash']!r}), not a string — it is "
                      "compared against every record's pin, so a value of "
                      "another type cannot be one")
    value_bearing = corpus.get("value_bearing_units")
    if not isinstance(value_bearing, int) or isinstance(value_bearing, bool):
        raise _refuse(f"{PREREG} carries no integer corpus.value_bearing_units "
                      "— E2a's denominator cannot be checked against the record")
    bars = _parse_bars(prereg)
    # The record freezes E2a's denominator in TWO places — `corpus.value_bearing_units`
    # (the population §3.3 derives) and `criteria[E2a]`'s `of` (the bar's denominator).
    # A run that printed one while scoring the other would be reporting a bar nothing
    # froze, so their disagreement is a refusal here rather than a number later.
    if bars["E2a"]["of"] != value_bearing:
        raise _refuse(f"{PREREG}'s criteria[E2a] bar is 'of "
                      f"{bars['E2a']['of']}' but corpus.value_bearing_units is "
                      f"{value_bearing} — two frozen statements about E2a's "
                      "denominator disagree, so no bar can be printed over it")
    # ⛔ A BAR THAT BINDS NOTHING IS NOT A BAR (#10610 review at `6e28a20e1f`,
    # P2). The `denominator_floors` map three statements above got a positivity arm
    # for exactly this reason ("a floor of zero or less binds nothing: … every
    # reading would clear it and the guard it feeds would pass everything"), and the
    # four `criteria` bars are read from the SAME frozen record one statement later
    # with only E2a's `of` reconciled. Measured at the pre-fix head, each of these
    # exited 0 and moved a verdict:
    #
    #   E1  `after_share <= 0.50 × before_share` -> `<= 100 ×`  E1=PASS, decided=True
    #   E2a `>= 13 of 14`                        -> `>= 0 of 14` E2=PASS (8/14 survived)
    #   E2b `<= 0.05`                            -> `<= 1.0`     accepted
    #
    # The three rules below are PROVABLE from the population each bar is stated
    # over, not a policy about how strict a pre-registration should be:
    #
    #  * a `>= n of m` COUNT bar is inert at `n == 0` (nothing ever fails it) and
    #    unsatisfiable at `n > m` (nothing ever passes it), so it binds iff
    #    `0 < n <= m`;
    #  * E2b is a SHARE over surviving units, which is in `[0, 1]` by construction,
    #    so `t >= 1.0` accepts the worst possible reading (every unit fused) and
    #    `t < 0` accepts nothing — it binds iff `0 <= t < 1`;
    #  * E1 is a REDUCTION (`after <= m × before`). At `m >= 1` the criterion is
    #    satisfied by the null result — an after arm that improved nothing, or got
    #    worse-but-not-more-than-m-times-worse — so it is not the reduction E1 is
    #    defined to measure. It binds iff `0 <= m < 1`: `m == 0` is MAXIMALLY strict
    #    (only a zero after-share passes it), which is why the boundary at zero is
    #    INCLUSIVE here and in E2b above — the two rules must agree at the strict
    #    end (#10610 review at `52d7173d5`).
    #
    # ⛔ SCOPE: the four bars this driver SCORES. `parse_bars` also returns E4's and
    # E5's, and this record grades neither; refusing a pre-registration over a bar
    # nothing here reads would be over-block, so they are deliberately not checked.
    # `parse_bars`' regexes only admit digit literals, so there is no `NaN`/`Infinity`
    # /`bool` avenue to close here — every hole left was MAGNITUDE, which is why the
    # rules above are about magnitude.
    for bar_id in ("E1b", "E2a"):
        required, of = bars[bar_id]["required"], bars[bar_id]["of"]
        if not 0 < required <= of:
            raise _refuse(
                f"{PREREG}'s criteria[{bar_id}] bar is '>= {required} of {of}' — "
                "a count bar binds nothing outside `0 < n <= m`: at n=0 no "
                "reading can fail it, and above m no reading can pass it, so "
                f"either way row {bar_id} would be decided by nothing")
    max_fused = bars["E2b"]["max_fused_share"]
    if not 0 <= max_fused < 1:
        raise _refuse(
            f"{PREREG}'s criteria[E2b] bar is '<= {max_fused}" + "' — a fused "
            "share is a fraction of the surviving units, so a bound of 1.0 or "
            "more accepts the worst possible reading (every unit fused) and a "
            "negative one accepts none; it binds iff `0 <= t < 1`")
    multiplier = bars["E1"]["multiplier"]
    # ⛔ THE BOUNDARY AT ZERO IS INCLUSIVE (#10610 review at `05761cf29`, P3). The
    # first cut of this rule said `0 < m < 1`, justified by the null result — right
    # about `m >= 1`, WRONG about `m == 0`: `after_share <= 0.0 x before_share` is
    # satisfied only by a zero after-share, so it fails every non-zero reading and
    # binds as hard as a bar can. Measured at the pre-fix head: `0.0` REFUSED while
    # the equally strict sibling E2b `<= 0` was ACCEPTED — an asymmetry between two
    # rules that must agree at the strict end. Same shape as E2b's now; the shipped
    # 0.50 is inside it either way, so this is over-block only.
    if not 0 <= multiplier < 1:
        raise _refuse(
            f"{PREREG}'s criteria[E1] bar is 'after_share <= {multiplier} x "
            "before_share' — E1 registers a REDUCTION, and at a multiplier of "
            "1.0 or more the criterion is satisfied by the null result (an after "
            "arm that improved nothing), so it is not the reduction the row "
            "claims to measure; it binds iff `0 <= m < 1` (0.0 is MAXIMALLY "
            "strict — only a zero after-share passes it)")
    return {
        "e1_objects": floors["e1_objects"],
        "e1_min_before_share": floors["e1_min_before_share"],
        "e2b_surviving_units": floors["e2b_surviving_units"],
        "fixtures_hash": corpus["fixtures_hash"],
        "e2a_units": value_bearing,
        "bars": bars,
    }


def read_off_arms(repo: Path, floors: dict) -> list[dict]:
    """The before arm, per realization, with BOTH floors applied to each.

    A share under `e1_min_before_share` is a control arm too flat to measure E1,
    and an Object count under `e1_objects` is a denominator too small to move a
    share — both are REFUSED by the probe that produced them. Asserted again
    here, because this driver is what puts the number in a scored row.

    The THIRD refusal the probe makes is asserted here too, and it is the one
    that decides whether the row means anything at all: the arm must have been
    OFF. `e1_paired_arm.py` refuses to run when any of the FOUR variables is
    already set (#9889 acceptance item 4), and it writes the env it read back
    into `arm` — so the record CARRIES the proof. Collecting that field without
    reading it (which this function did) let two ON realizations be scored as
    "before" against an ON "after", producing a DECIDED `FAIL` from a comparison
    that measured nothing: the inert counter #9889's own O/I/T forbids. A
    missing `arm` is refused rather than inherited, on the same rule the
    falsifier's `as_written` block is refused on — an absent provenance is not a
    default one. An EMPTY block (or one missing a switch) is refused on the
    sibling half of that rule: `arm.get(var)` on a key that was never written
    returns `None`, which stringifies to `"none"` — a token of the OFF
    vocabulary — so a block that wrote nothing would certify the arm it is
    supposed to prove.

    ⚠️ WHAT THIS FUNCTION STILL CANNOT REFUSE, stated so the row is not read as
    more than it is (#10610 self-audit at `380a2b7a2`, P1 second arm). Every guard here
    reads ONE record. A before arm has no SECOND artifact about its own run —
    `e1-gate-drops.json` exists precisely to witness the ON arm's census, and
    nothing plays that part for the OFF arms — so a coherent MULTI-FIELD edit to a
    single before record is undetectable in principle: measured, prefixing six
    non-bare names with `the ` and reconciling `bare_definite` (28 -> 34) and
    `share` (0.3590 -> 0.4359) exits 0 and moves E1's bound from 0.1795 to 0.2179,
    above the after arm's 0.2131. Every field still agrees with every other field,
    which is all a single artifact can be held to. What IS refused is every edit
    that leaves the record internally incoherent (a count that disagrees with its
    census, a duplicated name, a share its own counts refute) — which is why the
    rows cite the RECORD's corpus pin and the reasoning travels to the README
    rather than being implied here.

    ⛔ And the FOURTH refusal is the one every other guard in this file cannot
    reach, because it is the only place the mirror rule has to be INVERTED. The
    rest of the file refuses records that come from *different* runs ("the same
    head is not the same run"); the before pair is the one pair that must NOT be
    the same run, and nothing enforced it (#10610 review P2). `decidable` is
    computed from the SET of pairings, so two copies of a single OFF reading can
    only ever agree — they make E1 look DECIDED off one realization, which is
    the pseudoreplication the two-realization design exists to rule out. The
    identity is the POPULATION, not the graph name: the producers mint a fresh
    `capstone_off_<head8>_<stamp>` per run, but a re-stamped copy of ONE population
    is still one realization, so a shared `graph` alone is neither necessary nor
    sufficient (#10610 review at `2ffc5db19` — `graph` was a one-character-wide
    identity and a one-second epoch bump forged a second "realization"). What is
    compared is the `object_names` list, order-insensitively; and an artifact that
    names no graph at all cannot be told apart from a duplicated one, so it is
    refused as an absent provenance on the same rule as `as_written`'s.

    ⚠️ The no-duplicate rule below is justified as "a census no producer can
    write", and that premise comes from the WRITE DOOR, not from the query:
    `capstone_measure.census()` reads `MATCH (n:Object) RETURN n.name` with no
    `DISTINCT` and drops falsy names, so uniqueness is supplied by
    `add_object` → `resolve_or_mint`, which refuses a second live carrier of one
    name. The query alone would not guarantee it (#10610 review at `1c2130dd9`).
    """
    arms: list[dict] = []
    for name in OFF_ARMS:
        record = _read_json(repo / EVIDENCE / name)
        # ⛔ THE LEGACY LICENCE, and both halves of it are needed. (`#10996`
        # review cycle 2, P1.) An absent `TORTOISE_VALUE_GATE` is only a record
        # of an OFF switch in a record TAKEN BEFORE THE FLIP, so a record earns
        # the legacy reading only when (a) it carries NONE of the four pin fields
        # — the shape the shipped probe stopped producing at `76c2e1f09`, i.e.
        # long before the flip — and (b) its graph names the pre-`#10996`
        # campaign head. Either half alone is too wide: (a) without (b) admits a
        # hand-stripped record, (b) without (a) admits a record from a head
        # someone merely named.
        legacy_licence = (
            f"legacy record @ {_PRE_FLIP_HEAD[:9]} (produced before the pin "
            "fields existed, so before `#10996`; the shipped probe writes those "
            f"four fields and pins {_DEFAULT_ON_VARS[0]} explicitly)"
            if (not any(field in record for field in _BEFORE_PIN_FIELDS)
                and _names_head(str(record.get("graph") or ""), _PRE_FLIP_HEAD))
            else None)
        share = record.get("share")
        objects = record.get("objects")
        if share is None or not objects:
            raise _refuse(f"{name} carries no share/objects")
        # The share must be the fraction its OWN counts give — see
        # `_consistent_share`. Read here, on the before side, because these two
        # counts are the whole of the control arm's population.
        _consistent_share(f"{name}", share, record.get("bare_definite"), objects)
        if objects < floors["e1_objects"]:
            raise _refuse(f"{name} has {objects} Objects, under the frozen "
                          f"floor of {floors['e1_objects']}")
        if share < floors["e1_min_before_share"]:
            raise _refuse(f"{name} share {share:.4f} is under the frozen "
                          f"e1_min_before_share {floors['e1_min_before_share']}")
        arm = record.get("arm")
        if not isinstance(arm, dict):
            raise _refuse(f"{name} carries no `arm` block, so nothing in it shows "
                          "the passes were OFF — a before arm's provenance cannot "
                          "be assumed")
        for var in _ARM_VARS:
            # PRESENCE first, then value. `arm.get(var)` on a block that never
            # wrote the key returns `None`, which stringifies to "none" — a token
            # of the OFF vocabulary — so an EMPTY (or partially-keyed) block would
            # certify an arm nothing in it establishes. That is the same
            # fail-open the falsifier's `with_anchor` block is refused on
            # ("an empty one is not the flat shape"), applied to the field that
            # says which population a share came from: a key that was never
            # written is not a key that was written as unset.
            if var not in arm:
                raise _refuse(
                    f"{name}'s `arm` block carries no {var}, so it does not show "
                    "the passes were OFF — a missing key is not an unset one, "
                    "and a before arm's provenance cannot be assumed")
            state = arm.get(var)
            # ⛔ A SWITCH IS A STRING OR `null` (#10610 review at `2ffc5db19`, P3).
            # `_is_truthy` stringifies, so a JSON list or object reads FALSY — `[]`
            # and `{"a": 1}` both certified a before arm. Measured at
            # `bde94f89d3`: setting arm 1's `TORTOISE_VET` to `[]` and to `{"a": 1}`
            # each exited 0 with a byte-identical score, i.e. an edited provenance
            # certified the arm the docstring below says the guard refuses. No
            # producer writes either shape (`e1_paired_arm.py` writes `os.environ`
            # strings, or `null` for an unset key), so a non-string here is an edited
            # record either way.
            if state is not None and not isinstance(state, str):
                raise _refuse(
                    f"{name}'s `arm` block carries {var}={state!r}, which is not "
                    "a switch value — a producer writes the environment string "
                    "it read (or `null` when the key was unset), so a list or an "
                    "object is an edited record and cannot certify the pass was "
                    "OFF")
            # ⛔ NOT A SPELLING LIST (#10610 review at `f12471075`, P1): a before arm's
            # switch is OFF exactly when the pipeline would read it as falsy, which is
            # `is_truthy`'s rule. `"off"` and `"no"` are OFF (`FALSY`), and so is
            # garbage — but the old deny-list refused BOTH realizations of the shipped
            # pair the moment a producer spelled a switch `off`.
            if _is_truthy(state):
                raise _refuse(
                    f"{name} was measured with {var}={state!r}, so it is not a "
                    "before arm — scoring it as one compares a treatment against "
                    "itself and still reports a decided E1")
            # …and the other half of the same rule: OFF is a falsy VALUE, never
            # an ABSENCE, for the key whose default moved (`#10996`).
            if var in _DEFAULT_ON_VARS and _is_absent_pin(state):
                _absent_default_on_refusal(f"{name}'s `arm` block", var,
                                           legacy_licence)
        graph = record.get("graph")
        if not isinstance(graph, str) or not graph.strip():
            raise _refuse(
                f"{name} carries no `graph`, so nothing in it shows it is a "
                "SECOND realization of the before arm — an absent run cannot be "
                "told apart from a duplicated one")
        # INVERTED, deliberately: everywhere else in this file a shared graph is
        # the proof two records are the same run. Here that is the defect. Two
        # copies of one OFF realization pair identically, so `decidable` comes
        # back True ("every before realization agrees") over ONE realization — a
        # DECIDED row asserted from the replication the design requires and never
        # got, and the `before_spread_pp: 0.0` that follows reads as perfect
        # agreement rather than as the absent-replication it is.
        duplicate = next((prior for prior in arms if prior["graph"] == graph), None)
        if duplicate is not None:
            raise _refuse(
                f"{name} and {duplicate['artifact']} were both taken on graph "
                f"{graph!r} — the same run is not a second realization, and "
                "`decidable` is the SET of pairings, so two copies of one "
                "reading can only ever agree; the before arm still has n=1")
        # ⛔ THE GRAPH NAME IS NOT THE RECORD'S IDENTITY (#10610 review at
        # `75bfd9c6`). Comparing the string alone left the guard one character
        # wide: copying arm 2 over arm 1 with its epoch bumped by one second
        # (`…_1793379347` -> `…_1793379348`, record otherwise byte-identical) made
        # two names for ONE realization, and E1 went from REFUSED to a DECIDED
        # `PASS` off a single reading — the pseudoreplication this guard exists to
        # stop, and the cheapest way to satisfy the "n >= 2 per arm" the design
        # asks for. `e1_paired_arm.py` writes `object_names` from the run, so two
        # realizations of the same arm differ there; identical names are the same
        # measurement wearing a new stamp.
        names = record.get("object_names")
        if not isinstance(names, list) or not names:
            raise _refuse(
                f"{name} carries no `object_names`, so nothing in it establishes "
                "a population of its own — a realization that names no population "
                "cannot be told apart from a re-stamped copy of another")
        # ⛔ THE IDENTITY IS THE POPULATION, NOT THE ORDER IT IS WRITTEN IN
        # (#10610 review at `ec7b5e7fe`, P1). `prior["object_names"] == names` is a
        # LIST comparison, so it was still one cheap edit wide: copying arm 2 over
        # arm 1, giving it arm 1's graph stamp and REVERSING the name list left
        # every later reconciliation green — `objects == len(names)` and the
        # bare-definite count are both order-independent — and the bundle scored a
        # DECIDED `E1 PASS` (`before spread 0.0 pp`, both pairings on one run,
        # exit 0) off a single realization. That is the exact state this guard is
        # documented to refuse, reached by `list(reversed(...))` instead of by a
        # one-second epoch bump.
        #
        # `sorted` rather than `set`: a set would also collapse a DUPLICATED name,
        # so `[a, a, b]` would match `[a, b]` — two populations of different size
        # reported as one. Sorting is order-insensitive and keeps the multiplicity
        # the census is counted over.
        #
        # The members are checked as object names FIRST: `sorted` over a list
        # holding a dict or a list raises `TypeError` — exit **1** with a traceback,
        # beside a guard whose failure mode must be the exit-**2** refusal (the class
        # `_unit_id_members` already converts for the E2a census, which is the same
        # shape: a list of string ids compared as a population). Written out rather
        # than borrowed from `_unit_id_members` because that helper's message says
        # "session" and a BEFORE ARM HAS NO SESSIONS — it would report the graph
        # name as a session id and the offending member as the whole list, which is
        # the one string an operator uses to find the bad field (#10610 self-audit at
        # `380a2b7a2`, P3).
        for member in names:
            if not isinstance(member, str):
                raise _refuse(
                    f"{name} carries an `object_names` member {member!r}, which is "
                    "not an object name — the population is the run's own list of "
                    "`:Object` names, so a member of another type is an edited "
                    "record")
        # ⛔ A CENSUS HAS NO DUPLICATES (#10610 self-audit at `380a2b7a2`, P1). The
        # identity guard below reasons about multiplicity ACROSS arms; nothing read
        # it WITHIN one. `e1_paired_arm.py` writes one row per `:Object` node, so a
        # name can appear once — and a duplicated population is self-consistent for
        # every guard on the block: `objects == len(names)` (a duplicate is still a
        # member), `bare_in_names`, `_consistent_share`, `label_counts`. Measured:
        # replacing arm 1's 78 names with 78 copies of one name and reconciling
        # `bare_definite: 78, share: 1.0, label_counts: 78` scored
        # `E1 PASS decidable=True` at a 43.5 pp spread, exit 0 — a DECIDED row from a
        # census no producer can write. The E2a census beside this one has refused
        # the same shape since `f12471075` (`len(set(ids)) != len(ids)`); this is
        # that rule, on the population E1's share is computed over.
        if len(set(names)) != len(names):
            raise _refuse(
                f"{name} lists the same `object_names` member twice ({len(names)} "
                f"members, {len(set(names))} distinct) — the population is a CENSUS "
                "of `:Object` names, one row per node, so a duplicate cannot come "
                "from a producer and would inflate the count the share is "
                "computed over")
        same = next((prior for prior in arms
                     if sorted(prior["object_names"]) == sorted(names)), None)
        if same is not None:
            raise _refuse(
                f"{name} and {same['artifact']} carry the SAME `object_names` — "
                f"a fresh graph stamp ({graph!r} vs {same['graph']!r}) is not a "
                "fresh realization, so the before arm still has n=1 and "
                "`decidable` would be asserted from one reading; the identity is "
                "the POPULATION, so re-ordering the list does not make a second "
                "run of it")
        # ⛔ …and the counts must BE that population (#10610 review at
        # `75bfd9c6`). `_consistent_share` reconciled the share against
        # `bare_definite`/`objects`, and nothing reconciled THOSE with the
        # `object_names` they are counted from: editing arm 1 to `bare_definite:
        # 70, share: 70/78` (names untouched: 78, of which 28 bare) scored a
        # DECIDED `PASS` at a 33.2 pp spread. A count that disagrees with the list
        # it counts is an edit, and the census is the list.
        if objects != len(names):
            raise _refuse(
                f"{name} states objects={objects} but carries {len(names)} "
                "`object_names` — the count is the census, so a record that "
                "disagrees with its own population grades a run it does not "
                "carry")
        bare_in_names = sum(1 for item in names if _is_bare_definite(item))
        if record.get("bare_definite") != bare_in_names:
            raise _refuse(
                f"{name} states bare_definite={record.get('bare_definite')} but "
                f"{bare_in_names} of its {len(names)} `object_names` have the "
                "bare-definite shape — a share computed over a count the record's "
                "own names refute")
        # ⛔ `isinstance(counts, dict)` WAS the whole guard, so an ABSENT or
        # wrong-typed `label_counts` skipped it and the record still scored
        # (#10610 review at `2ffc5db19`, P2). Measured on a copy of
        # `e1-paired-off-arm-1.json`: replacing `label_counts` with `"x"`, or
        # deleting the field, both exited **0** and wrote a record. That is the
        # `isinstance(...)`-as-a-no-op shape the sibling `gate_names` guard closed
        # one field over, left in place here — while `probes/e1_paired_arm.py:276`
        # writes the field UNCONDITIONALLY, so its absence is an edit, never a
        # producer's older shape. The map is a label->count census, so a label it
        # does not list is a label the run had NONE of: absent `Object` is 0, not
        # "unchecked", and 0 against a positive `objects` is the refusal below.
        counts = record.get("label_counts")
        if not isinstance(counts, dict):
            raise _refuse(
                f"{name} carries no `label_counts` census "
                f"({type(counts).__name__}) — this record's second census of the "
                "same run cannot be skipped by omitting it")
        labelled = counts.get("Object", 0)
        if isinstance(labelled, bool) or not isinstance(labelled, int):
            raise _refuse(
                f"{name} carries label_counts['Object']={labelled!r}, which is "
                "not a node count — a non-numeric census member is an edited "
                "record")
        if labelled != objects:
            raise _refuse(
                f"{name} states objects={objects} but its own label_counts "
                f"reports {labelled} `:Object` — two censuses of one run "
                "cannot disagree")
        arms.append({
            "artifact": f"{EVIDENCE / name}",
            "graph": graph,
            "object_names": names,
            "objects": objects,
            "bare_definite": record.get("bare_definite"),
            "share": share,
            "arm": arm,
            "value_gate_pin": (
                "legacy-absent"
                if any(_is_absent_pin(arm.get(var))
                       for var in _DEFAULT_ON_VARS) else "explicit"),
            "corpus": _before_corpus_provenance(name, record, floors),
        })
    return arms


def _before_corpus_provenance(name: str, record: dict, floors: dict) -> dict:
    """WHICH CORPUS a before artifact was measured on — the operand no other
    guard in this file reached (#10610 review P2, category A under *Product Over
    Process*).

    E1's verdict is a fraction of these two shares (`after <= 0.5 x before`), and
    every number above is checked against the record that carries it — except
    the one that decides what population the number is ABOUT. The ON arm's corpus
    is re-verified against the frozen pin (`score()`, "a row citing a different
    corpus than the pre-registration froze is the defect this record exists to
    fix"); theirs was not, so the treatment side was pin-checked while the
    control side, which the bar is computed over, was not. `#9076`'s own rule —
    *a bar is a threshold AND its corpus* — forbids exactly that asymmetry.

    ⛔ WHY THIS DISCLOSES AN ABSENT FIELD INSTEAD OF REFUSING IT (the sibling
    guards DO refuse an absent `graph`/`arm`/`object_names`): the two committed
    artifacts predate the four pin fields and CANNOT be re-recorded to acquire
    them. Their graphs lived in a disposable `tools/test_lane.py` container that
    has since been removed, and the README's own rule is that a re-run is a *new
    LLM realization*, not these numbers — so a `required` field here would delete
    the only before arm this bundle has, which is a worse trade than an honest
    disclosure. What IS refused is the case that can lie: a before artifact that
    NAMES a pin must name the FROZEN one. Absence is then carried into the record
    (`before_arm_provenance`), so the limitation travels with the number instead
    of living only in the README.
    """
    present = [field for field in _BEFORE_PIN_FIELDS if field in record]
    absent = [field for field in _BEFORE_PIN_FIELDS if field not in record]
    if "fixtures_hash" in present:
        stated = str(record.get("fixtures_hash") or "")
        if not stated.strip():
            raise _refuse(
                f"{name} carries an EMPTY `fixtures_hash`, which is not the same "
                "as carrying none — a pin written blank is a claim that was "
                "never made, and the corpus it measured is unrefutable either way")
        if normalise_pin(stated) != normalise_pin(floors["fixtures_hash"]):
            raise _refuse(
                f"{name} was measured on corpus {normalise_pin(stated)}, not the "
                f"frozen {normalise_pin(floors['fixtures_hash'])} — E1's bar is a "
                "fraction of THIS share, so a before arm citing a corpus the "
                "pre-registration did not freeze is the same defect as an after "
                "arm citing one")
    if "object_floor" in present and record["object_floor"] != floors["e1_objects"]:
        raise _refuse(
            f"{name} was recorded under an Object floor of "
            f"{record['object_floor']!r}, not the frozen {floors['e1_objects']} — "
            "a before arm measured against a different bar cannot be scored "
            "against this one")
    if ("min_before_share" in present
            and record["min_before_share"] != floors["e1_min_before_share"]):
        raise _refuse(
            f"{name} was recorded under a min-before-share of "
            f"{record['min_before_share']!r}, not the frozen "
            f"{floors['e1_min_before_share']} — a before arm measured against a "
            "different bar cannot be scored against this one")
    return {
        "artifact": f"{EVIDENCE / name}",
        "graph": record.get("graph"),
        "head": record.get("head"),
        "fixtures_hash": record.get("fixtures_hash"),
        "pin_fields_present": present,
        "pin_fields_absent": absent,
        "unpinned": "fixtures_hash" in absent,
    }


def read_on_arm(repo: Path) -> dict:
    """The after arm, off the ON run's own census line (`e1-aux …`).

    The line is written by `capstone_measure.py` at the head it measured, so it
    is the after arm's own reading rather than a second capture by this driver.
    """
    path = repo / EVIDENCE / ON_ARM_LOG
    if not path.is_file():
        raise _refuse(f"{EVIDENCE / ON_ARM_LOG} does not exist")
    try:
        # UTF-8 explicitly, for the same reason as `_read_json`'s (#10610 review at
        # `2ffc5db19`, P3): the locale default is not this file's contract.
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        # Same class as `_read_json`'s (#10610 review at `858675ade`, P2): a log that
        # is not UTF-8 was an uncaught traceback, not the exit-2 refusal the module
        # promises for a corrupt input.
        raise _refuse(f"{EVIDENCE / ON_ARM_LOG} is not UTF-8 text, so it cannot be "
                      f"the log it is cited as: {exc}") from exc
    objects = _int_token(text, r"^e1-aux objects_in_graph=(\d+)",
                        what="e1-aux objects_in_graph")
    bare = _int_token(text, r"^e1-aux objects_in_graph=\d+ bare_definite=(\d+)",
                      what="e1-aux bare_definite")
    share = _float_token(text, r"^e1-aux objects_in_graph=\d+ "
                               r"bare_definite=\d+ share=(\S+)",
                         what="e1-aux share")
    # …and the same reconciliation on the after side. The log prints all three
    # numbers on ONE line (`objects_in_graph=61 bare_definite=13
    # share=0.21311475409836064`, and `13/61` is that float exactly), so a share
    # that disagrees with them is a record edited between the count and the
    # share — the arm a false GREEN is cheapest to forge on.
    _consistent_share(f"{EVIDENCE / ON_ARM_LOG}", share, bare, objects)
    # …and the log's OWN third copy of the same count. `e3 label=Object nodes=N` is
    # emitted by the census pass in the same run, so a single-field edit to the
    # `e1-aux` line leaves an artifact contradicting itself where nobody was looking
    # (the exact shape this bundle exists to refuse).
    e3_objects = _int_token(text, r"^e3 label=Object nodes=(\d+)",
                            what="e3 label=Object nodes")
    if e3_objects != objects:
        raise _refuse(
            f"{EVIDENCE / ON_ARM_LOG}'s e1-aux line reports "
            f"objects_in_graph={objects} but its own `e3 label=Object "
            f"nodes={e3_objects}` line reports {e3_objects} — one run, one Object "
            "census")
    real_facts = _int_token(text, r"^e1b outcome=\S+ real_facts=(\d+)",
                            what="e1b real_facts")
    # ⛔ E1b's numerator is bounded by its OWN frozen denominator too (#10610 review
    # at `f12471075`, P3): `real_facts=100000` was accepted beside `bar=90 of 90`,
    # i.e. a numerator 1,111x its denominator — the shape `_count(ceiling=...)` was
    # added for on E2a. The `bar` token is on the same line, so it is read rather
    # than assumed, and the two must agree.
    match = re.search(r"^e1b outcome=\S+ real_facts=\d+ bar=(\d+) of (\d+)", text,
                      re.M)
    if not match:
        raise _refuse(f"{EVIDENCE / ON_ARM_LOG} carries no `e1b … bar=<required> of "
                      "<population>` line, so its numerator is the only statement of "
                      "the population it counts")
    e1b_required, e1b_population = int(match.group(1)), int(match.group(2))
    # The numerator must be inside the population the SAME LINE prints. The bar's
    # agreement with the pre-registration is checked in `score()`, where the frozen
    # record is in hand.
    _count(f"{EVIDENCE / ON_ARM_LOG}", "e1b real_facts", real_facts,
           ceiling=e1b_population)
    head = _grep(text, r"^# head: (\S+)", what="# head")
    graph = _grep(text, r"^# graph: (\S+)", what="# graph")
    corpus = _grep(text, r"^# corpus: \S+ fixtures_hash=sha256:(\S+)",
                   what="# corpus fixtures_hash")
    fused_text = _grep(text, r"^e2b outcome=\S+ fused=(\d+/\d+)",
                       what="e2b fused")
    fused, atomic = (int(part) for part in fused_text.split("/"))
    # ⛔ THE SHARE PRINTED BESIDE THE COUNTS IS AN OPERAND, NOT DECORATION (#10610
    # review at `f12471075`, P1). `fused`/`atomic_points` were read as unbounded
    # integers and the `= 0.087719` on the same line was ignored, so editing ONLY the
    # denominator (`fused=10/114` -> `10/100000`) wrote `share: 0.0001` and turned E2
    # from FAIL into PASS while the line still printed its own contradiction. The
    # reconciliation `_consistent_share` performs for E1's census, applied to E2b: the
    # ratio the log states is the ratio its own counts give.
    printed_share = _float_token(text, r"^e2b outcome=\S+ fused=\d+/\d+ = (\S+)",
                                 what="e2b share")
    if atomic <= 0:
        raise _refuse(f"{EVIDENCE / ON_ARM_LOG}'s e2b line grades {fused}/{atomic} "
                      "— a share of no population is not a measurement")
    if not 0 <= fused <= atomic:
        raise _refuse(f"{EVIDENCE / ON_ARM_LOG}'s e2b line reports {fused} fused "
                      f"of {atomic} atomic Points — a numerator above its own "
                      "denominator is not a share")
    if abs(fused / atomic - printed_share) > 1e-6:
        raise _refuse(f"{EVIDENCE / ON_ARM_LOG}'s e2b line reports "
                      f"{fused}/{atomic} = {printed_share}, but those counts give "
                      f"{fused / atomic:.6f} — the counts and the share printed "
                      "beside them cannot disagree")
    # The after arm's OWN readback, off the `# arm:` line `capstone_measure` writes
    # from `os.environ` after forcing the passes. E1's bar compares an ON run
    # against an OFF one, so an after arm whose treatment was never enabled is not
    # a "before/after pair" at all — it is two control arms, and the bar would be
    # scored over a treatment the run never applied. Read, not assumed: the log
    # carries the proof, so a driver that skips it is choosing not to look.
    after_arm_line = _grep(text, r"^# arm: (.+?)(?:\s+\(|$)",
                           what="# arm")
    after_arm = {}
    for token in after_arm_line.split():
        if "=" in token:
            key, _, value = token.partition("=")
            after_arm[key] = value
    for var in _ARM_ON_VARS:
        # ⛔ AN ALLOW-LIST, like the pipeline's (#10610 review at `f12471075`, P1).
        # This was `not in _ARM_OFF_STATES`, which accepted `off`, `no` and any garbage
        # token as ON — `is_truthy("off") is False`, so the measured passes were OFF
        # and the "before/after pair" was two control arms, greened at the value the
        # reviewer measured by changing ONE line of the log.
        if not _is_truthy(after_arm.get(var)):
            raise _refuse(
                f"{EVIDENCE / ON_ARM_LOG}'s arm readback has {var}="
                f"{after_arm.get(var)!r}, so the measured passes were not ON — "
                "an after arm that never enabled the treatment cannot be the "
                "ON half of a before/after pair")
    # …and the other direction. The measured arm is two keys ON and TWO OFF
    # (`capstone_measure.ARM_OFF`), so a log measured with the value gate armed is
    # a DIFFERENT arm — the one `E1-E2.md` reports separately — and crediting its
    # after-share to E1 scores a bar over a treatment this driver's arm never
    # declared. Presence is required here for the same reason as on the before
    # side: an absent key stringifies to "none" and would read as consent.
    #
    # ⛔ AND SINCE `#10996` THE `<unset>` SPELLING IS NOT THAT CONSENT
    # (review cycle 2, P1). `capstone_measure.measured_arm` now writes an explicit
    # `"0"` for both pinned-OFF keys (and reads it back), so this log is the ONE
    # record in the tree that still spells `TORTOISE_VALUE_GATE=<unset>` — an
    # artifact of the pre-flip producer, admissible only while BOTH the log's own
    # `# head:` and its `# graph:` name the pre-`#10996` campaign head. A log from
    # any other head must pin the key: otherwise the ON half of the pair is a run
    # whose predicate was ARMED, and E1's bar (`after <= 0.50 x before`) gets the
    # easier side of a treatment it never declared.
    legacy_licence = (
        f"legacy log @ {_PRE_FLIP_HEAD[:9]} (produced by the pre-`#10996` "
        "`capstone_measure`, which popped the pin instead of setting it; the "
        f"shipped producer writes {_DEFAULT_ON_VARS[0]}=0 and reads it back)"
        if _names_pre_flip_head(head) else None)
    for var in _ARM_OFF_VARS:
        if var not in after_arm:
            raise _refuse(
                f"{EVIDENCE / ON_ARM_LOG}'s arm readback carries no {var}, so it "
                "does not show the switch was pinned OFF — a missing key is not "
                "an unset one, and the measured arm is only the ON half of a "
                "before/after pair while its OFF switches are declared")
        if _is_truthy(after_arm.get(var)):
            raise _refuse(
                f"{EVIDENCE / ON_ARM_LOG}'s arm readback has {var}="
                f"{after_arm.get(var)!r}, but the measured arm pins it OFF — "
                "that is a different arm (the value-gate/seam pass), and "
                "crediting its after share to E1 would score a treatment this "
                "pair never declared")
        if var in _DEFAULT_ON_VARS and _is_absent_pin(after_arm.get(var)):
            _absent_default_on_refusal(f"{EVIDENCE / ON_ARM_LOG}'s arm readback",
                                       var, legacy_licence)
    return {
        "artifact": f"{EVIDENCE / ON_ARM_LOG}",
        "head": head,
        "graph": graph,
        "arm": after_arm,
        "value_gate_pin": (
            "legacy-absent"
            if any(_is_absent_pin(after_arm.get(var))
                   for var in _DEFAULT_ON_VARS) else "explicit"),
        "value_gate_pin_basis": (
            legacy_licence if any(_is_absent_pin(after_arm.get(var))
                                 for var in _DEFAULT_ON_VARS)
            else "the log pins it to an explicit falsy value"),
        "corpus_sha256": corpus,
        "objects": objects,
        "bare_definite": bare,
        "share": share,
        "real_facts": real_facts,
        "e1b_bar": (e1b_required, e1b_population),
        "fused": fused,
        "atomic_points": atomic,
    }


def e1_pairings(before_arms: list[dict], after: dict, floors: dict) -> dict:
    """E1 scored against EVERY before realization — and the aggregate stated.

    `multiplier` is the frozen `E1.multiplier` (0.50 today, PARSED out of
    `criteria[E1].threshold` by ``parse_bars`` — never typed): a pairing PASSES
    when the after share is at or under that fraction of its before share. One
    after arm paired with two before arms can disagree, and when it does the
    metric is NOT decided — reporting the passing pairing as "the" result would be
    picking the answer.
    """
    multiplier = floors["bars"]["E1"]["multiplier"]
    pairings = []
    for arm in before_arms:
        bound = multiplier * arm["share"]
        passed = after["share"] <= bound
        pairings.append({
            "before_artifact": arm["artifact"],
            "before_graph": arm["graph"],
            "before_arm": arm.get("arm"),
            "before_share": arm["share"],
            "before_objects": arm["objects"],
            "before_bare_definite": arm.get("bare_definite"),
            "after_share": after["share"],
            "after_objects": after["objects"],
            "after_bare_definite": after["bare_definite"],
            "bound": round(bound, 6),
            "ratio": round(after["share"] / arm["share"], 6),
            "passed": passed,
        })
    verdicts = {pairing["passed"] for pairing in pairings}
    decidable = len(verdicts) == 1
    below_floor = after["objects"] < floors["e1_objects"]
    outcome = ("REFUSED" if below_floor or not decidable
               else "PASS" if verdicts.pop() else "FAIL")
    return {
        "criterion": "E1",
        "bar": (f"after_share <= {multiplier} x before_share "
                f"(Objects floor {floors['e1_objects']})"),
        "outcome": outcome,
        "decidable": decidable and not below_floor,
        "below_floor": below_floor,
        "pairings": pairings,
        "before_spread_pp": round(
            abs(pairings[0]["before_share"] - pairings[-1]["before_share"]) * 100, 1),
        # `why` narrates the state the record is IN. The floor is checked FIRST
        # because it is a different finding: an after arm under the frozen floor
        # was never scoreable at any n, so saying "the pairings disagree" about it
        # reports a disagreement that did not happen and hides the real cause
        # (the pre-registration's own vocabulary: below_floor is "REFUSED, which
        # is neither a pass nor a fail").
        "why": (f"the after arm has {after['objects']} Objects, under the frozen "
                f"floor of {floors['e1_objects']} — a population that small "
                "cannot decide `after <= multiplier x before`, so the bar is "
                "REFUSED and the pairings are not read as a verdict"
                if below_floor else
                "one after realization paired against two independent before "
                "realizations gives both verdicts, so E1 is not decided at n=1 "
                "per arm — n >= 2 per arm is the next requirement"
                if not decidable else
                "every before realization agrees on the pairing"),
    }


def e2_score(repo: Path, floors: dict) -> dict:
    """E2a (frozen 14-unit denominator) + E2b (fused share) — both halves.

    The falsifier is a REFUSAL gate, not a second opinion: if injecting every
    lost unit's anchor does not move the counter to the frozen denominator, the
    counter is inert and the low number it reports is not a measurement.

    ⛔ The DENOMINATOR is pinned, not merely read: `value_units` comes off the
    record, and the record is refused unless it grades exactly the population the
    pre-registration froze (`corpus.value_bearing_units`, §3.3). The falsifier's
    agreement (below) cannot stand in for that — its two sides both come from the
    record's own population, so they move TOGETHER when the denominator drifts. A
    record grading N ≠ 14 would otherwise be scored `13/N` against a printed bar
    reading `13 of 14`, and (see the capstone row) would carry that N as its own
    denominator.
    """
    record = _read_json(repo / EVIDENCE / E2A_RECORD)
    falsifier = _read_json(repo / EVIDENCE / E2A_FALSIFIER)
    gates = _read_json(repo / EVIDENCE / GATE_DROPS)

    units = record.get("value_units")
    survived = record.get("value_survived")
    if not units or survived is None:
        raise _refuse(f"{E2A_RECORD} carries no value_units/value_survived")
    # ⛔ A COUNT, not a token (#10610 review at `75bfd9c6`). `20/14` and `inf/14`
    # both wrote `E2 PASS` before this, and `"8"` escaped as a TypeError — see
    # `_count`. The denominator is bounded by the frozen count below (whose refusal
    # names the pre-registered number, the more specific finding), so only its
    # type/finiteness is asserted here; the numerator is bounded by its own
    # denominator, which is a population relation no producer can violate.
    units_n = _count(E2A_RECORD, "value_units", units)
    survived_n = _count(E2A_RECORD, "value_survived", survived, ceiling=units_n)
    # ⛔ THE E2a RECORD IS THE RETENTION READING, so it may not be the INJECTED one
    # (#10610 review at `f12471075`, P1). `e2a_value_units.py:165` writes
    # `injected_anchor` into this record too, and `--inject-anchor` produces exactly
    # the 14/14 reading the falsifier is supposed to prove is REACHABLE — so a record
    # copied from that run states, in its own field, that it graded the injected pass,
    # and 14/14 then passes E2a's bar. Nothing read it: the round-5 fix closed this for
    # `with_anchor` only, on the falsifier. `is not False` (never a truthiness test)
    # refuses `True`, `None` and an absent key alike, on the same rule as
    # `injected_anchor is not True` one function over: a provenance that has to be
    # PRESENT to mean "uninjected" cannot be defaulted.
    if record.get("injected_anchor") is not False:
        raise _refuse(f"{E2A_RECORD} does not declare an UNINJECTED reading "
                      f"(injected_anchor={record.get('injected_anchor')!r}) — this "
                      "record IS the retention measurement, so a reading taken with "
                      "the anchor injected has already proven the counter reachable "
                      "and cannot stand in for it")
    frozen_units = floors["e2a_units"]
    # The denominator must BE the frozen one. A record that grades 20 units while
    # the pre-registration froze 14 would otherwise score GREEN on a population
    # nobody registered — the false green this bundle exists to refuse.
    if units != frozen_units:
        raise _refuse(f"{E2A_RECORD} grades {units} value-bearing units, but the "
                      f"pre-registration froze {frozen_units} (threshold "
                      f"'>= {floors['bars']['E2a']['required']} of "
                      f"{floors['bars']['E2a']['of']}') — a bar printed over a "
                      "different denominator is a number nothing froze")
    # ⛔ …and the totals must BE the census they are summed from (#10610 review at
    # `75bfd9c6`): `value_survived: 14` with `per_session` untouched (still 8)
    # scored GREEN off ONE field edit to the file a lane would reach for to unblock
    # the row. AFTER the frozen-denominator refusal, so a drifted denominator still
    # gets the message that names the pre-registered number.
    _census_reconcile(E2A_RECORD, record, declared=(units, survived))

    # …and the as-written block IS this record's reading (the bundle's README
    # reads it as `as_written 8/14 -> injected 14/14`), so its COUNTS must equal
    # the E2a record's. Checked here, AFTER the frozen-denominator refusal, so a
    # denominator defect in that block still gets the message that names the
    # pre-registered number — the more specific finding.
    falsifier_as_written = {}
    if "as_written" in falsifier:
        # ⛔ A BLOCK IS AN OBJECT OR IT IS REFUSED (#10610 review at `73382c7e6`,
        # P2): `falsifier.get("as_written") or {}` passed a non-empty string or
        # list through to `.get(...)`, which is an `AttributeError` -> exit 1.
        falsifier_as_written = _object_block(
            E2A_FALSIFIER, "as_written", falsifier.get("as_written"))
    if "as_written" in falsifier and (
            falsifier_as_written.get("value_units"),
            falsifier_as_written.get("value_survived")) != (units, survived):
        raise _refuse(f"{E2A_FALSIFIER}'s as-written block reads "
                      f"{falsifier_as_written.get('value_survived')} of "
                      f"{falsifier_as_written.get('value_units')} but "
                      f"{E2A_RECORD} reads {survived} of {units} — the "
                      "as-written block IS the E2a reading, so the two cannot "
                      "disagree")
    if "as_written" in falsifier:
        # ⛔ …and the as-written half must declare it is the UNINJECTED one (#10610
        # review at `f12471075`, P1): it says it IS the E2a reading, so an injected
        # block claiming that identity is a falsifier pass passed off as the control.
        if falsifier_as_written.get("injected_anchor") is not False:
            raise _refuse(f"{E2A_FALSIFIER}'s as-written block does not declare an "                          f"uninjected reading (injected_anchor="
                          f"{falsifier_as_written.get('injected_anchor')!r}) — the "
                          "as-written half IS the E2a reading, which is the "
                          "pre-injection measurement by definition")
        # The block's OWN totals must be its own census, the same rule the E2a
        # record is held to: the as-written half is the pre-injection reading the
        # README quotes, so its census is the population it must add up to.
        _census_reconcile(f"{E2A_FALSIFIER}'s as-written block",
                          falsifier_as_written)

    # "Absent" and "present but empty" are different records: the fallback below
    # is for a flat record that declares no block at all. An EMPTY block is a
    # record that did not write its counter, and inheriting the top-level numbers
    # there would attribute the injected 14/14 to a provenance it does not carry.
    if "with_anchor" in falsifier:
        with_anchor_block = _object_block(
            E2A_FALSIFIER, "with_anchor", falsifier.get("with_anchor"))
        if not with_anchor_block:
            raise _refuse(f"{E2A_FALSIFIER}'s with-anchor block is present but "
                          "empty — the injected reading is the block this score is "
                          "taken over, so an empty one is not the flat shape "
                          "(e2_score)")
        with_anchor = with_anchor_block
    else:
        with_anchor = falsifier
    # ⛔ The scored block must DECLARE the injection it is credited for. The
    # artifact carries `injected_anchor` precisely to say which reading this is
    # (`as_written.injected_anchor: false`, `with_anchor.injected_anchor: true`),
    # and nothing read it (#10610 review P2): the liveness proof was a COUNT
    # equality, so a block declaring `injected_anchor: false` with 14/14 was
    # accepted — the driver certifying "the counter is shown to move" on a block
    # that states, in the record's own words, that it ran no injection. That is
    # the inert-counter class the refusal below is written for, read one field
    # short. A permissive default is not an assertion: `is not True` (never a
    # truthiness test) refuses `False`, `None` and an absent key alike.
    if with_anchor.get("injected_anchor") is not True:
        raise _refuse(f"the scored block of {E2A_FALSIFIER} does not declare an "
                      f"injection (injected_anchor={with_anchor.get('injected_anchor')!r}) "
                      "— a reading that certifies the counter moves must be the "
                      "INJECTED one, or nothing establishes that the number "
                      "moved because an anchor was injected")
    injected_survived = with_anchor.get("value_survived")
    injected_units = with_anchor.get("value_units")
    # The same bound as the E2a counter it is compared to (#10610 review at
    # `75bfd9c6`): the falsifier's reading is `units/units` or it is inert, and a
    # non-number here used to reach the comparison below as a TypeError.
    injected_units_n = _count(f"{E2A_FALSIFIER}'s scored block", "value_units",
                              injected_units)
    _count(f"{E2A_FALSIFIER}'s scored block", "value_survived",
           injected_survived, ceiling=max(injected_units_n, 0))
    _census_reconcile(f"{E2A_FALSIFIER}'s scored block", with_anchor)
    # The falsifier must move the SAME population the score is taken over. A
    # falsifier that reaches its own denominator proves that SOME counter moves;
    # crediting it to the frozen 14 would print a number nothing measured.
    if injected_units != units:
        raise _refuse(f"the falsifier grades {injected_units} units but the "
                      f"score's denominator is {units} — a falsifier over a "
                      "different population cannot certify this counter")
    if injected_survived != units:
        raise _refuse(f"the falsifier reaches {injected_survived}/{units}, not "
                      f"{units}/{units} — an injected anchor that moves nothing "
                      "means the counter is inert")
    # ⛔ `is not True`, NEVER a truthiness test (#10610 review at `21b2af22`). `if not
    # gates.get("predicate_alive")` refuses `false`/`null`/absent but ACCEPTS the string
    # `"false"` — measured: a bundle with E2a at 13/14 (bar MET) and this field set to the
    # STRING `"false"` wrote `e2 = PASS`, a GREEN row whose own artifact states its positive
    # controls no longer drop. That is the exact shape the falsifier's `injected_anchor`
    # guard one function over already refuses with `is not True`.
    if gates.get("predicate_alive") is not True:
        raise _refuse("the value-gate positive controls no longer drop — the "
                      "mechanism count measures nothing")

    required = floors["bars"]["E2a"]["required"]
    passed = survived_n >= required
    return {
        "criterion": "E2",
        "bar": (f"E2a >= {required} of {frozen_units}, E2b fused share <= "
                f"{floors['bars']['E2b']['max_fused_share']}"),
        "e2a": {
            "artifact": f"{EVIDENCE / E2A_RECORD}",
            "falsifier_artifact": f"{EVIDENCE / E2A_FALSIFIER}",
            "graph": record.get("graph"),
            "value_units": units,
            "frozen_units": frozen_units,
            "value_survived": survived,
            "falsifier": {"value_units": injected_units,
                          "value_survived": injected_survived,
                          "graph": with_anchor.get("graph")},
            "required": required,
            "outcome": "PASS" if passed else "FAIL",
            "denominator_rule": "pre-registration §3.3 — a digit in the "
                                "verbatim_anchor, minus the two named exclusions",
        },
        "outcome": "PASS" if passed else "FAIL",
        "decidable": True,
    }


def score(repo: Path = _ROOT) -> dict:
    floors = frozen_denominators(repo)
    bars = floors["bars"]
    before_arms = read_off_arms(repo, floors)
    after = read_on_arm(repo)
    # …and the log's OWN `# graph:` line must name its OWN `# head:` line. The
    # legacy-pin licence and the before-arm graph check both read the head, so a
    # log whose graph names a different run would otherwise sail through every
    # later check on a head it merely shares — the same class of hole
    # `_names_head` was made a NAME test for (#10610 at `75bfd9c6`).
    if not _names_head(str(after.get("graph") or ""), after["head"]):
        raise _refuse(f"{ON_ARM_LOG}'s `# graph:` line is {after.get('graph')!r}, "
                      "which does not name its own `# head:` line "
                      f"({after['head'][:_HEAD_PREFIX]}) — one log, one run, so "
                      "a graph naming another head cannot supply this head's "
                      "after share")
    # One record, one run: the before, ON, E2a, falsifier and gate-drops records
    # must ALL name the same head (and the E2a / gate-drops pair the same graph),
    # or a number from one run is being reported under another run's corpus.
    for arm in before_arms:
        if not _names_head(str(arm.get("graph") or ""), after["head"]):
            raise _refuse(f"{arm['artifact']} was taken on graph "
                          f"{arm.get('graph')!r}, which does not name head "
                          f"{after['head'][:_HEAD_PREFIX]}")
    e2a_record = _read_json(repo / EVIDENCE / E2A_RECORD)
    falsifier_record = _read_json(repo / EVIDENCE / E2A_FALSIFIER)
    gates_record = _read_json(repo / EVIDENCE / GATE_DROPS)
    # The falsifier's TOP LEVEL is checked here like every other record (it is the
    # field a reader sees first), and BELOW its scored block is checked against
    # the E2a record's own run — removing it from this loop in favour of the block
    # check alone would let a record assert two runs with its headline unchecked.
    for name, record in ((E2A_RECORD, e2a_record),
                         (E2A_FALSIFIER, falsifier_record),
                         (GATE_DROPS, gates_record)):
        # The falsifier may declare its run at the top level or on either of its
        # two blocks; whichever it declares is checked, and a record that
        # declares TWO runs is caught by the headline (the field a reader sees
        # first) or, if the blocks disagree, by the scored-block arms below.
        #
        # ⛔ BOTH BLOCKS ARE SHAPE-CHECKED BEFORE THEY ARE READ (#10610 review at
        # `73382c7e6`, P2): `(record.get("with_anchor") or {}).get("graph")`
        # passed a non-empty STRING through to `.get` — the very `AttributeError`
        # the scored-block arms below were guarded against, still reachable from
        # HERE, which runs first. A guard applied at one of two call sites is not
        # a guard; this arm is what the cycle-3 pin caught after the first fix.
        blocks = ({}
                  if name != E2A_FALSIFIER else
                  {field: _object_block(name, field, record.get(field))
                   for field in ("with_anchor", "as_written") if field in record})
        graph = str(record.get("graph")
                    or (blocks.get("with_anchor") or {}).get("graph")
                    or (blocks.get("as_written") or {}).get("graph") or "")
        if not _names_head(graph, after["head"]):
            raise _refuse(f"{name} was taken on graph {graph!r}, which does not "
                          f"name head {after['head'][:_HEAD_PREFIX]}")
    # The AFTER arm's own `# graph:` line, not its head: an ON share from graph A
    # recorded beside E2a/gate-drops counts from graph B at the same head passes
    # every head check above (the head IS equal) while mixing two populations —
    # the exact failure the invariant is stated for, and the half that was only
    # asserted in the comment because the graph line was never parsed.
    if after["graph"] != e2a_record.get("graph"):
        raise _refuse(f"{ON_ARM_LOG} was written on graph {after['graph']!r} but "
                      f"{E2A_RECORD} counts on {e2a_record.get('graph')!r} — the "
                      "after share and the E2a counts come from different runs "
                      "of the corpus, and the head they share cannot tell them "
                      "apart")
    # The falsifier's number is read from `with_anchor` (falling back to the
    # record itself when that block is absent, exactly as `e2_score` reads it),
    # so that is the block whose graph must be checked — a record whose other
    # block names the head proves nothing about the run it was scored on.
    if "with_anchor" in falsifier_record:
        with_anchor_scored = _object_block(
            E2A_FALSIFIER, "with_anchor", falsifier_record.get("with_anchor"))
        if not with_anchor_scored:
            raise _refuse(f"{E2A_FALSIFIER}'s with-anchor block is present but "
                          "empty — the injected reading is the block this score is "
                          "taken over, so an empty one is not the flat shape "
                          "(score)")
        falsifier_scored = with_anchor_scored
    else:
        falsifier_scored = falsifier_record
    falsifier_graph = str(falsifier_scored.get("graph") or "")
    if not _names_head(falsifier_graph, after["head"]):
        raise _refuse(f"{E2A_FALSIFIER}'s scored block was taken on graph "
                      f"{falsifier_graph!r}, which does not name head "
                      f"{after['head'][:_HEAD_PREFIX]}")
    # …and naming the head is not enough. A graph name is
    # `capstone_on_<head>_<stamp>`, so TWO runs of the same head are two
    # different graphs: a scored block from the sibling run would certify the
    # E2a counter from a corpus invocation the E2a record was not taken on. The
    # scored block must therefore be the E2a record's OWN graph.
    if falsifier_graph != str(e2a_record.get("graph") or ""):
        raise _refuse(f"{E2A_FALSIFIER}'s scored block is on graph "
                      f"{falsifier_graph!r}, but {E2A_RECORD} was taken on "
                      f"{e2a_record.get('graph')!r} — the same head is not the "
                      "same run, and the falsifier must prove the counter on "
                      "the run it is credited to")
    # …and the HEADLINE too (the field a reader sees first). The head loop above
    # can only refuse a graph that does not NAME the head, so a top level
    # declaring a SIBLING run of the same head would otherwise go unchecked
    # while both blocks were required to be the E2a run.
    headline = str(falsifier_record.get("graph") or "")
    if headline and headline != str(e2a_record.get("graph") or ""):
        raise _refuse(f"{E2A_FALSIFIER}'s top-level graph is {headline!r}, but "
                      f"{E2A_RECORD} was taken on "
                      f"{e2a_record.get('graph')!r} — the record would assert "
                      "two runs, and the same head is not the same run")
    # `as_written` is the reading the E2a record was taken on: same measurement,
    # so the same graph. An ABSENT graph there is not a licence to inherit the
    # scored block's — that would assume the provenance this check exists to
    # prove, and report it as proven.
    as_written_block = None
    if "as_written" in falsifier_record:
        # ⛔ `(as_written_block or {}).get("graph")` was the same unguarded read one
        # function over: a string block passed the `or {}` and raised at `.get`
        # (#10610 review at `73382c7e6`, P2).
        as_written_block = _object_block(
            E2A_FALSIFIER, "as_written", falsifier_record.get("as_written"))
    as_written_present = "as_written" in falsifier_record
    as_written_graph = str((as_written_block or {}).get("graph") or "")
    if as_written_present and not as_written_graph:
        raise _refuse(f"{E2A_FALSIFIER}'s as-written block carries no graph, so "
                      f"it cannot be shown to be the run {E2A_RECORD} was "
                      "taken on — they are the same measurement")
    if as_written_graph and as_written_graph != str(
            e2a_record.get("graph") or ""):
        raise _refuse(f"{E2A_FALSIFIER}'s as-written block is on graph "
                      f"{as_written_graph!r} but {E2A_RECORD} is on "
                      f"{e2a_record.get('graph')!r} — they are the same "
                      "measurement and cannot come from different runs")
    if e2a_record.get("graph") != gates_record.get("graph"):
        raise _refuse(f"the E2a record and the gate-drops record come from "
                      f"different graphs — {E2A_RECORD} cites "
                      f"{e2a_record.get('graph')!r} and {GATE_DROPS} cites "
                      f"{gates_record.get('graph')!r}; one record cannot mix "
                      "them, and two runs of the SAME head are still two "
                      "graphs")
    # ⛔ THE AFTER ARM'S CENSUS IS ALSO THE GATE-DROPS RECORD'S (#10610 review at
    # `f12471075`, P0). The graph-identity check above already establishes that
    # `e1-gate-drops.json` and the ON log are the SAME RUN, and that record carries its
    # own `objects`/`bare_definite`/`object_names` census — so the after share had a
    # witness sitting in the tree and nothing compared them. Measured: editing ONE line
    # of `on-arm.log` to `objects_in_graph=78 bare_definite=10 share=0.1282…` (the
    # counts self-consistent, as `_consistent_share` requires) exited 0 with
    # `e1.outcome = PASS`, `decidable: True` — both pairings PASS — and the consumer's
    # row came back GREEN, while `e1-gate-drops.json` still said 61 Objects / 13 bare /
    # 0.2131 and the log's own `e3 label=Object nodes=61` line still said 61. A self-
    # consistent multi-field edit is the one shape no per-field reconciliation can see;
    # only a SECOND artifact about the same run can refuse it.
    for field in ("objects", "bare_definite"):
        stated = gates_record.get(field)
        if stated is None:
            raise _refuse(f"{GATE_DROPS} carries no {field}, so the one record that "
                          "WITNESSES the ON run's census cannot confirm the share "
                          "the run measured")
        if stated != after[field]:
            raise _refuse(
                f"{EVIDENCE / ON_ARM_LOG} reports {field}={after[field]} but "
                f"{GATE_DROPS} — the same run, same graph — reports {stated}: "
                "two censuses of one ON run cannot disagree")
    # ⛔ THE CENSUS LIST IS REQUIRED, NOT OPTIONAL (P2, #10610 review at `67a3b8e98`).
    # The two checks below are the ONLY witness for the ON arm's counts that is
    # independent of the two numbers they reconcile — `on-arm.log`'s `e1-aux` line and
    # `e1-gate-drops.json`'s `objects`/`bare_definite` are both plain integers, so a
    # self-consistent multi-field edit of them is refused by nothing except this list.
    # Written as `isinstance(...) and ...`, the guard was a NO-OP in exactly the state
    # an edit produces: absent, `[]`, or any non-list skipped BOTH arms and fell through
    # to `e1_pairings`. Measured on the pre-fix driver: the multi-field edit that the
    # guard was written to refuse returned `E1 PASS, decidable: true` after deleting
    # `object_names` — a CHEAPER edit than the one the guard existed for (no name list
    # to forge), and the row builder turned that record into a GREEN `#9080` E1 row.
    #
    # Absence is never a default here, which is the rule the sibling guards in this file
    # already apply: `_census_reconcile` refuses a missing `per_session`, both call sites
    # refuse a present-but-empty `with_anchor`, and a missing `arm` key is refused rather
    # than read as unset. `probes/e1_gate_drops.py` writes `object_names`
    # UNCONDITIONALLY, so an absent or empty list is always an edit, never a producer's
    # older shape.
    gate_names = gates_record.get("object_names")
    if not isinstance(gate_names, list) or not gate_names:
        raise _refuse(
            f"{GATE_DROPS} carries no `object_names` census — the one record that "
            "WITNESSES the ON run's census must carry the population it counts; an "
            "absent, empty or non-list census is the shape a cheaper edit produces, "
            "and it cannot be confirmed by anything else in the record")
    # ⛔ The MEMBERS are checked as object names, like the before arms'
    # `object_names` above (#10610 review at `2ffc5db19`, P3). `gate_bare` counts
    # `_is_bare_definite(item)` and that helper stringifies, so a list or dict
    # member silently counted as NON-bare: `object_names: [0..60]` was refused only
    # incidentally (its bare count came out 0 against the log's 13), and a member of
    # another type that happened to orchestrate to the right count would have
    # scored. This is the one census the file calls the ON run's only independent
    # witness, and its siblings — the before arms and `_unit_id_members` — already
    # refuse a member of another type.
    for member in gate_names:
        if not isinstance(member, str):
            raise _refuse(
                f"{GATE_DROPS} carries an `object_names` member {member!r}, which "
                "is not an object name — the census is the run's own list of "
                "`:Object` names, so a member of another type is an edited record")
    # ⛔ A CENSUS HAS NO DUPLICATES here either (#10610 review at `bde94f89d`, P2).
    # The before arms refuse `len(set(names)) != len(names)` — "a census no producer
    # can write" — and the E2a census gets the same rule through the partition
    # check, but `gate_names` did not. Measured on a copy of the bundle: replacing
    # this record's 61 names with the 13 bare ones plus 48 COPIES of one non-bare
    # name kept `len == after["objects"]` (61) and `gate_bare ==
    # after["bare_definite"]` (13), and `score()` proceeded at exit 0 with a
    # population no producer can emit — lowering the cost of the coupled multi-field
    # forgery this witness is here to refuse.
    if len(set(gate_names)) != len(gate_names):
        raise _refuse(
            f"{GATE_DROPS} lists the same `object_names` member twice "
            f"({len(gate_names)} entries, {len(set(gate_names))} distinct) — the "
            "census is a population of `:Object` nodes, and `e1_gate_drops.py` "
            "writes one row per node, so a duplicated name is an edited record")
    if len(gate_names) != after["objects"]:
        raise _refuse(
            f"{GATE_DROPS} carries {len(gate_names)} `object_names` beside its "
            f"own objects={gates_record.get('objects')} — the record's census "
            "and its count cannot disagree either")
    gate_bare = sum(1 for item in gate_names if _is_bare_definite(item))
    if gate_bare != after["bare_definite"] or after["objects"] != len(gate_names):
        raise _refuse(
            f"{GATE_DROPS}'s own {len(gate_names)} `object_names` ({gate_bare} "
            f"bare-definite) do not reproduce the ON arm's "
            f"{after['bare_definite']}/{after['objects']} — the run's census is "
            "the names it wrote, so a share computed over another one is not "
            "this run's share")
    e1 = e1_pairings(before_arms, after, floors)
    # ⛔ The E1b bar the log PRINTS must be the one the pre-registration froze (#10610
    # review at `f12471075`, P3): `real_facts` is a numerator, and a log printing
    # `bar=90 of 90` beside a frozen `90 of 90` is the only way a reader can check it.
    # `read_on_arm` already refused a numerator outside its own printed population;
    # this is the other half, so both statements of the bar are the frozen one.
    if tuple(after["e1b_bar"]) != (floors["bars"]["E1b"]["required"],
                                   floors["bars"]["E1b"]["of"]):
        raise _refuse(f"{EVIDENCE / ON_ARM_LOG}'s e1b line prints a bar of "
                      f"{after['e1b_bar'][0]} of {after['e1b_bar'][1]}, but the "
                      f"pre-registration froze {floors['bars']['E1b']['required']} of "
                      f"{floors['bars']['E1b']['of']} — a bar printed over a different "
                      "population is a number nothing froze")
    e2 = e2_score(repo, floors)
    if after["corpus_sha256"] != normalise_pin(floors["fixtures_hash"]):
        raise _refuse(
            f"{ON_ARM_LOG} measured corpus {after['corpus_sha256']}, not the "
            f"frozen {normalise_pin(floors['fixtures_hash'])} — a row citing a "
            "different corpus than the pre-registration froze is the defect "
            "this record exists to fix")
    e2b_floor = floors["e2b_surviving_units"]
    e2b_share = (after["fused"] / after["atomic_points"]
                 if after["atomic_points"] else 1.0)
    e2["e2b"] = {
        "fused": after["fused"],
        "atomic_points": after["atomic_points"],
        "share": round(e2b_share, 6),
        "below_floor": after["atomic_points"] < e2b_floor,
        "floor": e2b_floor,
        "artifact": after["artifact"],
    }
    # A population under the frozen floor was never scoreable, so it is REFUSED —
    # not FAIL. `capstone_measure.e2_outcome`, the row builder that consumes this
    # record, returns REFUSED → BLOCKED for the identical operands; recording FAIL
    # here made the artifact say "a scored failure" about the same numbers the row
    # beside it calls unmeasured — the row-and-its-evidence-disagree class this
    # bundle exists to refuse.
    if e2["e2b"]["below_floor"]:
        e2["outcome"] = "REFUSED"
        e2["decidable"] = False
        e2["decided_by"] = "E2b floor"
        e2["why"] = (f"the E2b population is {after['atomic_points']} atomic "
                      f"Points, under the frozen floor of {e2b_floor}, so its "
                      "fused share is REFUSED rather than scored")
    else:
        e2["outcome"] = ("PASS" if (e2["e2a"]["outcome"] == "PASS"
                                    and e2b_share <= bars["E2b"]["max_fused_share"])
                         else "FAIL")
        e2["decided_by"] = ("E2a" if e2["e2a"]["outcome"] != "PASS" else
                            "E2b" if (e2b_share > bars["E2b"]["max_fused_share"])
                            else "both")
    # ⛔ EVERY COUNT IN THE RECORD PASSES `_count` — including the gate-drops block
    # (#10610 review at `2ffc5db19`, P2; the census above and the two disarmed guards
    # were closed by `bde94f89d3`, this one was left). `objects` and `bare_definite`
    # are reconciled by the head loop and `predicate_alive` by its own `is not True`,
    # but `drops_total` was emitted as a bare `.get()` — so it was the ONE count a
    # caller could set to `Infinity` (or a string, or a negative) and still get a
    # RECORD back. Measured at this head: `Infinity`, `-Infinity`, `NaN`, `61.5`,
    # `"61"`, `true`, `null`, `62` and `-1` each scored and exited 0, and `Infinity`
    # wrote a record that is not valid STRICT JSON. It is not cosmetic:
    # `capstone_measure.py` reads it into `gate_drops_total` and renders it into the
    # scored E1 note as `reports value_gate drops <N> of <M> Objects with its positive
    # controls alive`, so an unvalidated value publishes a mechanism claim nothing
    # measured. The ceiling is this same run's `objects` (`e1_gate_drops.py`'s `measure`
    # writes `drops_total` as `len(drops)` — `null` when the Objects census is empty,
    # which the probe refuses on with exit 2 — and `drops` is keyed by object name), and the head loop has
    # already refused a record whose `objects` disagrees with the ON arm's census.
    # ⛔ The gate-drops record is REUSED, not re-read (#10610 review at
    # `1c2130dd9`, P3): `gates_record` above is the read whose graph and census were
    # already validated, and re-reading the path gave the `drops_total` ceiling and
    # `objects` a DIFFERENT parse of the same file than the census they bound.
    gate_record = gates_record
    gate_objects = _count(f"{EVIDENCE / GATE_DROPS}", "objects",
                          gate_record.get("objects"))
    # ⛔ THE CEILING WAS A BOUND, NOT A RECONCILIATION (#10610 review at
    # `6e28a20e1f`, P2). `_count(..., ceiling=gate_objects)` refuses a value
    # above the Object population, which is necessary and not sufficient: the
    # SAME record carries the drop census itself (the probe's `measure`
    # writes `value_gate_drops` as a name-keyed dict and `drops_total` as
    # `len(drops)` beside it), and nothing in this file had ever read it. So any value in
    # `[0, objects]` scored — measured at the pre-fix head, `drops_total: 61`
    # (equal to the 61-Object ceiling) beside a shipped `value_gate_drops: {}`
    # exited 0 and the record emitted `drops_total: 61`, where 62 was refused.
    # That is exactly the harm the bound was added to prevent: the number reaches
    # `capstone_measure.py`'s E1 note as `reports value_gate drops 61 of 61
    # Objects with its positive controls alive` — a mechanism claim the artifact
    # it was read from contradicts.
    #
    # The census is REQUIRED and the count must EQUAL it. A present-but-empty
    # `{}` is legitimate here (zero drops is the shipped reading) — unlike
    # `with_anchor`, where an empty block means the run never wrote its counter —
    # so the check is `len()`, not emptiness.
    #
    # ⛔ THE CENSUS IS NOT THE POPULATION (#10610 review at `05761cf29`, P1). The
    # first cut passed `ceiling=len(gate_census)` — and the census is
    # ATTACKER-SIZED, so that ceiling bound nothing while DROPPING the population
    # bound the previous head had. Measured at that head: 62 and 100 fabricated keys
    # with a matching `drops_total` were ACCEPTED, emitting `drops_total: 100` beside
    # `objects: 61` — a numerator 1.64x its own population, exactly the class
    # `_count(ceiling=...)` exists for (E1b's `real_facts=100000` beside
    # `bar=90 of 90` was a finding earlier in this same PR).
    #
    # So the ceiling is the RUN'S population again, and four things are now checked
    # against the record's OTHER statements rather than against the census's own
    # size — nothing here lets the census vouch for itself:
    #   1. it holds no more names than the record's own `objects`;
    #   2. every key IS one of this run's `object_names` (a drop of something the run
    #      never wrote is not a drop);
    #   3. every value is a STRING — the producer writes
    #      `value_gate.mechanical_verdict(name)["rule_id"]` (the probe's `measure`);
    #   4. `drops_among_bare_definite` — the record's SECOND statement of the same
    #      drops, written by the probe's `measure` as `[n for n in bare if n in
    #      drops]` on every run whose Objects census is non-empty (`null` otherwise,
    #      beside that probe's own exit-2 refusal) and read by NOTHING until now — is
    #      reconciled against the census and the names, so an internally
    #      contradictory record (a bare-definite name dropped, with an empty
    #      `drops_among_bare_definite`) cannot pass on its total alone.
    gate_census = _object_block(str(EVIDENCE / GATE_DROPS), "value_gate_drops",
                               gate_record.get("value_gate_drops"))
    if len(gate_census) > gate_objects:
        raise _refuse(
            f"{EVIDENCE / GATE_DROPS} carries {len(gate_census)} "
            f"`value_gate_drops` names beside objects={gate_objects} — a drop is "
            "an Object the run WROTE, so the census cannot be larger than the "
            "population it is drawn from")
    unknown = sorted(set(gate_census) - set(gate_names))
    if unknown:
        raise _refuse(
            f"{EVIDENCE / GATE_DROPS} records a drop for {unknown[0]!r}, which is "
            "not one of this run's `object_names` — a drop of an Object the run "
            "never wrote is a forged census, not a measurement")
    for dropped, rule_id in gate_census.items():
        if not isinstance(rule_id, str):
            raise _refuse(
                f"{EVIDENCE / GATE_DROPS} records the drop of {dropped!r} with "
                f"{rule_id!r} — the producer writes the verdict's `rule_id` "
                "string, so anything else is not this reader's shape")
    gate_drops_total = _count(f"{EVIDENCE / GATE_DROPS}", "drops_total",
                             gate_record.get("drops_total"),
                             ceiling=gate_objects)
    if gate_drops_total != len(gate_census):
        raise _refuse(
            f"{EVIDENCE / GATE_DROPS} states drops_total={gate_drops_total} but "
            f"its own `value_gate_drops` census holds {len(gate_census)} — "
            "the count IS the names it dropped, so a total that disagrees with "
            "its own census is a number edited away from the run")
    # The record's SECOND statement of the same drops, reconciled. `[]` is the
    # legitimate shipped reading — the producer writes the field on every run
    # whose Objects census is non-empty (`null` when empty, and such a record is
    # already refused on `drops_total` first) — so an ABSENT one is an edit.
    among_bare = gate_record.get("drops_among_bare_definite")
    if not isinstance(among_bare, list) or any(
            not isinstance(member, str) for member in among_bare):
        raise _refuse(
            f"{EVIDENCE / GATE_DROPS} carries no `drops_among_bare_definite` "
            "list — the producer writes it on every run whose Objects census is "
            "non-empty, as the bare-definite subset of the drops, so a record "
            "without it cannot be checked "
            "against its own census")
    expected_bare_drops = [n for n in gate_names
                           if _is_bare_definite(n) and n in gate_census]
    if among_bare != expected_bare_drops:
        raise _refuse(
            f"{EVIDENCE / GATE_DROPS} reports {len(among_bare)} "
            "`drops_among_bare_definite` names but its own census and "
            f"`object_names` give {len(expected_bare_drops)} — two statements of "
            "one run's drops disagree, so neither can be trusted")
    return {
        "schema": "e1-e2-scores/1",
        "corpus_sha256": floors["fixtures_hash"],
        "corpus_pin_source": (f"{PREREG}#corpus.fixtures_hash — re-verified "
                              f"from {ON_ARM_LOG}'s own `# corpus:` line for the "
                              "AFTER arm, and checked per run at PRODUCER time "
                              "(e1_paired_arm.py refuses to start unless the "
                              "checkout's corpus matches this pin, G1). The "
                              "BEFORE arms cannot re-prove it: see "
                              "`before_arm_provenance`"),
        # ⛔ The one operand whose corpus nothing else in this record ties to the
        # frozen pin (#10610 review P2). It travels WITH the numbers rather than
        # living only in the README, because the first DECIDED E1 will be decided
        # from these two shares — and a reader of the record, not of the bundle
        # prose, is who has to know that.
        "before_arm_provenance": {
            "status": ("unpinned"
                       if any(arm["corpus"]["unpinned"] for arm in before_arms)
                       else "pin-checked"),
            "pin_fields": list(_BEFORE_PIN_FIELDS),
            "probe_revision": None,
            "probe_revision_source": (f"{EVIDENCE / 'README.md'}"
                                       "#which-probe-revision-produced-each-artifact"),
            "arms": [arm["corpus"] for arm in before_arms],
            "why": ("`e1_paired_arm.py` writes four pin fields (`head`, "
                    "`fixtures_hash`, `object_floor`, `min_before_share`) and "
                    "refuses to run on a corpus other than the pre-registration's; "
                    "the committed before arms predate those fields, so which "
                    "corpus they measured is a PRODUCER-time fact (G1) that "
                    "nothing in the pair can re-prove. The producing revision is "
                    "disclosed in the README section cited above."),
            "limitation": (
                "E1's bar is a FRACTION of these two shares, so their corpus is "
                "part of the bar (#9076: a bar is a threshold AND its corpus). "
                "The after arm's corpus is re-verified against the frozen pin; "
                "theirs is not re-verifiable from the record — an asymmetry the "
                "treatment side alone never shows. It is not material to the "
                "shipped verdict (E1 is REFUSED at n=1, so nothing scores green "
                "off these arms); it becomes material at the `n >= 2 per arm` "
                "the README names as the next requirement, where the first "
                "DECIDED E1 will be computed from before shares whose corpus "
                "the record cannot show. Re-recording the pair at the shipped "
                "probe closes it."
                if any(arm["corpus"]["unpinned"] for arm in before_arms) else
                "closed: every before arm carries the frozen `fixtures_hash` "
                "and it matches, so the pair's corpus is re-verifiable from the "
                "record exactly as the after arm's is"),
        },
        "head": after["head"],
        "graph": after["graph"],
        # Both halves of the pair carry their arm readback, so the provenance of
        # the comparison is in the evidence rather than only in the driver that
        # checked it.
        "after_arm": after["arm"],
        # ⛔ WHAT THE PAIR'S "OFF" ACTUALLY MEANS, because `#10996` changed it
        # (review cycle 2, P1). Every artifact this record scores spells
        # `TORTOISE_VALUE_GATE` as ABSENT, which is the OFF spelling only under the
        # pre-flip code — since the flip, absence is the TREATMENT. They are read
        # as OFF under the campaign-head licence (`_PRE_FLIP_HEAD`), and this block
        # says so beside the numbers instead of leaving it in the driver. A record
        # from any other head must pin the key explicitly or it is refused.
        "value_gate_pin": {
            "default_on_keys": list(_DEFAULT_ON_VARS),
            "legacy_head": _PRE_FLIP_HEAD,
            "after": {"pin": after["value_gate_pin"],
                      "basis": after["value_gate_pin_basis"]},
            "before": [{"artifact": arm["artifact"],
                        "pin": arm["value_gate_pin"]} for arm in before_arms],
            "limitation": (
                "`TORTOISE_VALUE_GATE` is recorded as ABSENT in every arm here, "
                "and since `#10996` an absent value gate is the ON arm. These "
                "records predate the flip, so absence really was OFF when they "
                "were taken, and they are admitted only on that licence (the "
                "producer pins the key explicitly now, so a NEW record cannot "
                "express OFF this way). Re-recording the pair at the shipped "
                "probes — `TORTOISE_VALUE_GATE=0` written and read back — is "
                "what closes the residual; that is a re-take of E1's arms and "
                "belongs to #7949, not to this record.")},
        "bars_source": (
            f"{PREREG}#criteria + #denominator_floors, parsed by "
            "capstone_measure.parse_bars — the same reader the scoring probe "
            "uses, so this record and that row cannot read two bars"),
        "denominators": {
            "e1_objects": floors["e1_objects"],
            "e1_min_before_share": floors["e1_min_before_share"],
            "e2a_units": floors["e2a_units"],
            "e2b_surviving_units": floors["e2b_surviving_units"],
        },
        "e1": e1,
        "e1b": {
            "bar": f"{bars['E1b']['required']} of {bars['E1b']['of']}",
            "real_facts": after["real_facts"],
            "outcome": ("PASS" if after["real_facts"] >= bars["E1b"]["required"]
                        else "FAIL"),
            "artifact": after["artifact"],
            "note": ("a no-loss CONTROL on the scored arm, not the frozen "
                     "metric — `real_facts` is read from the ON run's own "
                     "`e1b` line, and no OFF-arm record carries it"),
        },
        "e2": e2,
        "gate_drops": {
            "artifact": f"{EVIDENCE / GATE_DROPS}",
            "objects": gate_objects,
            "drops_total": gate_drops_total,
            "predicate_alive": gate_record.get("predicate_alive"),
        },
    }


def normalise_pin(value: str) -> str:
    """The frozen pin without its ``sha256:`` prefix, for comparison."""
    return value[len("sha256:"):] if value.startswith("sha256:") else value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True,
                        help="where the score record (JSON) is written")
    parser.add_argument("--repo", default=str(_ROOT))
    args = parser.parse_args(argv)
    record = score(Path(args.repo))
    Path(args.out).write_text(json.dumps(record, indent=1) + "\n")
    e1, e2 = record["e1"], record["e2"]
    print(f"E1 {e1['outcome']} decidable={e1['decidable']} "
          f"(before spread {e1['before_spread_pp']} pp) -> {args.out}")
    for pairing in e1["pairings"]:
        print(f"   before {pairing['before_share']:.4f} -> bound "
              f"{pairing['bound']:.4f} vs after {pairing['after_share']:.4f} "
              f"= {'PASS' if pairing['passed'] else 'FAIL'} "
              f"(ratio {pairing['ratio']:.4f})")
    print(f"E2 {e2['outcome']} — E2a {e2['e2a']['value_survived']}/"
          f"{e2['e2a']['value_units']} (bar >= {e2['e2a']['required']}), "
          f"E2b fused {e2['e2b']['fused']}/{e2['e2b']['atomic_points']} = "
          f"{e2['e2b']['share']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
