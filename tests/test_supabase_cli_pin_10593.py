"""The `setup-cli` step must not float the Supabase CLI's MAJOR version (#10593).

Measured failure, 2026-11-05T19:45:57Z, `supabase-deploy` run `42091073635` (the dispatch that
was supposed to carry `20261102000001`/`20261102000002` to production, and with them the
`point.search_keys` fix of #10620/#10593):

    [inbucket] in supabase/config.toml is no longer supported. Use [local_smtp] instead.
    Try rerunning the command with --debug to troubleshoot the error.
    ##[error]Process completed with exit code 1.

`supabase/setup-cli` was asked for `version: latest`, which resolved to **3.0.0** — a MAJOR bump —
and v3 refuses to load this repo's `supabase/config.toml` because it still declares `[inbucket]`.
The CLI therefore exits 1 at CONFIG LOAD, before `db push` contacts the database at all, so **no
migration can be applied to production by any means the workflow offers**. That is why
`deploy-hosted`'s drift gate has been red since 2026-11-03T02:53:49Z naming exactly those two
migrations: the remedy the gate prescribes (`gh workflow run supabase-deploy.yml --ref main`)
could never succeed.

Reproduced here at the time of writing, offline, against a throwaway project link — so this is a
measurement, not a reading of the log:

    $ npx supabase@3.0.0 status                         # what `latest` resolves to
    {"_tag":"Error","error":{"code":"StatusConfigLoadError","message":"[inbucket] in
      supabase/config.toml is no longer supported. Use [local_smtp] instead."}}   rc=1
    $ npx supabase@2.122.1 status                       # the pin
    WARN: config section [inbucket] is deprecated and will be removed in v3, use [local_smtp]
    {"_tag":"Error","error":{"code":"StatusDbInspectError", ...}}            rc=1  # config LOADED

⚠️ `status` is the probe above on purpose: `db push --dry-run --linked` does NOT reach config load
at all — it fails first with `ProjectRefNotLinkedError` on BOTH the old and the new config, so it
cannot discriminate and was the wrong command to cite here.

The second arm fails at its DB-inspect leg (the quoted `StatusDbInspectError`), not for want of a
token; the config gate — the thing that was blocking every apply — is gone. So the pin, not a
config rewrite, is the minimal
root-cause repair: a production deploy path must not depend on an unpinned MAJOR of a tool whose
major versions change the config schema this repo commits.

⚠️ What this does NOT say: that 3.x is bad, or that `[local_smtp]` is wrong. Migrating
`supabase/config.toml` to v3's schema is a deliberate change that needs a full local v3 run
(`supabase start`/`db reset`, the local-auth harnesses) and its own review — pinning first restores
delivery without betting production on an unvalidated config migration. This test is written so
that the two stay coupled — but the coupling is on the **MAJOR**, not on the section name: v3 also
consent-gates every hosted mutation and fails unattended without `--yes`. Keying the ceiling on the
MAJOR rather than on either SYMPTOM is what kept the guard alive through the migration — a ceiling
that asked "does the config still declare `[inbucket]`?" went INERT the day `#11104` migrated the
config, and one that asked "does every mutating command pass `--yes`?" is defeatable by a trailing
comment, a `--yes=false`, or a `--yes` belonging to another command. Both v3 prerequisites have
LANDED (`#10896`'s rename, `#11104`'s consent flags), so the ceiling is not waiting on a missing
capability: it is `#11100`'s ruling that the major moves in its own PR, with its own local
validation.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "supabase-deploy.yml"
CONFIG = ROOT / "supabase" / "config.toml"

#: A pin is an exact x.y.z. `latest`, `v2`, `2`, `2.x` all float the MAJOR, which is the defect;
#: a major-bounded range (`^2.122.1`, `~2.122`) cannot cross the major, but it still takes PATCH
#: upgrades that no diff records — and this workflow is the only door to production migrations.
#: (`v2.stable` was considered for exactly that reason and rejected: it is the same unrecorded-
#: patch-uptake trade, on a line this repo does not own. If a later lane prefers it, change this
#: regex deliberately rather than discovering it as a red in the deploy pin.)
_EXACT_PIN = re.compile(r"^\d+\.\d+\.\d+$")

#: The CLI major that changed BOTH halves of this contract at once: it raises `DbConfigLoadError`
#: on a config still carrying `[inbucket]` (line above), and it consent-gates every hosted mutation,
#: failing a non-TTY run with `HostedMutationConsentRequiredError` unless `--yes` is passed. One
#: major, two breaks — which is why the guard below is a MAJOR-ceiling, not a check on either symptom.
_V3_MAJOR = 3

#: The action this guard is about, matched the way GITHUB matches it. `owner/repo` is
#: case-INSENSITIVE — `supabase/setup-cli`, `Supabase/setup-cli` and `SUPABASE/SETUP-CLI` all resolve
#: to the same action (`git ls-remote` returns the same HEAD for all three) — so a case-sensitive
#: match lets a second step spell the action differently, install a different major, and stay
#: invisible to the reader. The ref must follow, so a DIFFERENT action whose name merely starts with
#: this one (`supabase/setup-cli-fork@v1`) is not mistaken for *the* step.
_SETUP_CLI_USES = re.compile(r"(?i)^\s*supabase/setup-cli(?:@|$)")


def _is_setup_cli_step(uses: object) -> bool:
    """True when a step's `uses:` names the `supabase/setup-cli` action — the ONE predicate.

    `_setup_cli_step` (the comparison) and `_setup_cli_step_node` (the step LOCATOR, shared with
    the literal-line check and the construct walk) both resolve "which step is this?" through this
    function, so the two readers cannot disagree about
    WHICH ACTION is the step. That is all the predicate decides — the COUNT is a separate question
    and is re-checked: one anchored step aliased twice (`- &scli …` then `- *scli`) yields two
    entries from `yaml.safe_load` but ONE node from the node walk, which de-dupes by node identity;
    `_setup_cli_step`'s `len(steps) == 1` fires first, so that shape still fails closed. Do not read
    "one predicate" as "the readers cannot disagree about anything". Measured before it was shared:
    a second step spelled `Supabase/setup-cli@v1` carrying
    `version: 3.0.0` was invisible to the case-sensitive filter, so `len(steps) == 1` held, the
    reader resolved `2.122.1`, and `_violations()` returned `[]` over a deploy job that installs a
    major-3 CLI — the exact false PASS this file exists to prevent, reached through a capital
    letter, and the same class (2) (`a pin this reader resolves differently from CI`) that the
    `with:`-key normalization arm closes for input names.
    """
    return bool(_SETUP_CLI_USES.match(str(uses)))


def _setup_cli_step(workflow_text: str) -> dict:
    """The `supabase/setup-cli` step of the migration job, as a dict."""
    doc = yaml.safe_load(workflow_text)
    assert isinstance(doc, dict), "workflow did not parse as a mapping"
    steps = [
        step
        for job in (doc.get("jobs") or {}).values()
        for step in (job.get("steps") or [])
        if _is_setup_cli_step(step.get("uses", ""))
    ]
    assert steps, "no `supabase/setup-cli` step found — the migration door changed shape"
    assert len(steps) == 1, f"expected exactly one setup-cli step, found {len(steps)}"
    return steps[0]


def _setup_cli_step_node(text: str) -> yaml.MappingNode:
    """The PARSED node for the `supabase/setup-cli` step — the one NODE locator.

    `_setup_cli_step` (the comparison) and `_literal_version_lines` (the literal-line check) must
    resolve the SAME step, or the guard can assert a pin read from a different place than CI
    installs from. They share the `_is_setup_cli_step` predicate — `_literal_version_lines` reaches
    the step through THIS node, `_setup_cli_step` through `yaml.safe_load` — so neither has a second
    dialect for WHICH ACTION the step is. That predicate is all they share: the two can still
    disagree about the COUNT (one anchored step aliased twice), which is why `_is_setup_cli_step`'s
    docstring treats the count as a separate question rather than as something this locator
    settles. (`_setup_cli_block` slices the same span, but it is NOT on the guard path —
    `_literal_version_lines` takes each line from the pin's own key node's mark, so editing the
    block slicer cannot change a verdict. Its own docstring says so.)
    """
    doc = yaml.compose(text)
    found: list[yaml.MappingNode] = []

    def walk(node: object, seen: set[int]) -> None:
        if id(node) in seen:  # an alias can point back at an ancestor
            return
        seen.add(id(node))
        if isinstance(node, yaml.MappingNode):
            for key, value in node.value:
                if (
                    isinstance(key, yaml.ScalarNode)
                    and key.value == "uses"
                    and isinstance(value, yaml.ScalarNode)
                    and _is_setup_cli_step(value.value)
                ):
                    found.append(node)
                walk(value, seen)
        elif isinstance(node, yaml.SequenceNode):
            for item in node.value:
                walk(item, seen)

    walk(doc, set())
    assert found, "no `supabase/setup-cli` step found — the migration door changed shape"
    assert len(found) == 1, f"expected exactly one setup-cli step, found {len(found)}"
    return found[0]


def _setup_cli_block(workflow_text: str) -> list[str]:
    """The setup-cli step's own source lines, sliced from the PARSED node's marks.

    ⛔ **NOT ON THE GUARD PATH — this is not the pin check, and editing it cannot change a verdict.**
    The pin check is `_literal_version_lines`, which resolves each line from the PIN'S OWN KEY NODE
    (`pin_key.start_mark.line`). Measured at #11134's head: this function's only caller in the whole
    repo is one test's landing assertion (that a mutation landed in the setup-cli step's source and
    not a neighbour's). It used to be described as owning the pin-scoping requirement, which made a
    change here look like a landed change to the guard while every test stayed green — the exact
    silent no-op the correction below removes.

    Two scoping rules for the slice, each from a demonstrated defect:
    * The block comes from the composed node's `start_mark`/`end_mark`, not from a line pattern. A
      text scan is a SECOND reader of "which step is this" with its own dialect, and it disagreed:
      measured in review, `uses : supabase/setup-cli@v1` (a space before the colon — legal YAML, and
      what PyYAML and GitHub both read as the same step) did not match the pattern, so a legitimate
      workflow was REFUSED with a message claiming the migration door had MOVED. The node cannot
      disagree with `yaml.safe_load` about which step this is, so key spelling is not a surface
      here — and `_setup_cli_step_node` is the ONE locator, shared with the construct walk and
      with `_setup_cli_step`'s predicate, so there is no second answer to "which step" to drift.
    * `with:` may legally precede `uses:`, so nothing here assumes an order.

    The guard's own requirement — that the pin be scoped to THIS step, because a file-wide search is
    satisfied by a decoy literal (review demonstrated a green path through an `actions/setup-node`
    step's `with: version: 2.122.1` while the setup-cli step carried none) — belongs to
    `_literal_version_lines`, not here. This slice answers only "where did a test's mutation land?".
    """
    node = _setup_cli_step_node(workflow_text)
    # ⛔ SLICE BY MARK INDEX, NOT BY LINE (#11134 review cycle 2, P2 — a false block). `end_mark` is
    # the first character AFTER the mapping, so at EOF it sits ON the mapping's last line — a
    # position like `(8, 26)`, not `(9, 0)`. A LINE slice `lines[start.line:end.line]` is exclusive
    # and therefore DROPS that last line, which for a step that is the file's final node is the PIN
    # ITSELF. Measured: a legal workflow ending `          version: 2.122.1` with no trailing newline
    # gave `block == ['      - uses: ...', '        with:']`, `_literal_version_lines` returned `[]`,
    # and the guard refused it with "not a literal `version:` line … write `version:` on its own
    # line" — a remedy the file already satisfied. ONE BYTE decided it: appending `\n` moved
    # `end_mark` to `(9, 0)` and the same file passed. GitHub parses either byte-identically, so
    # refusing one is a false block, and the message sent the reader after a construct that was not
    # there.
    #
    # Index slicing is byte-precise, which is also why it needs NO widening for a one-line flow
    # mapping (`- {uses: …, with: {version: …}}`): that shape yields its own line as a non-empty
    # block and is refused for the RIGHT reason — no literal `version:` line — instead of tripping
    # the "resolved to no source lines" assert below, which blamed the reader for a step it had
    # resolved perfectly.
    block = workflow_text[node.start_mark.index : node.end_mark.index].splitlines()
    # Trailing blank/comment lines belong to the NEXT step (comments are no node's children), and
    # `version:` is never inside a comment, so trimming them cannot hide a pin — it only stops this
    # block from swallowing the neighbour's prose.
    while block and (
        not block[-1].strip() or block[-1].lstrip().startswith("#")
    ):
        block.pop()
    assert block, "the setup-cli step resolved to no source lines — cannot check its pin"
    return block


#: A `version:` KEY, as the author may legally spell it: `version:`, `version :`, `'version':`,
#: `"version":`. All four are the same plain scalar key to PyYAML AND to GitHub, so none of them is
#: a divergence and refusing one would be a false block (found in review: the reader matched only the
#: canonical spelling and blamed a merge key for the rest).
_LITERAL_VERSION_LINE = re.compile(
    r"""^\s*['"]?version['"]?\s*:\s*['"]?([^'"\s#]+)['"]?\s*(?:#.*)?$"""
)


def _literal_version_lines(workflow_text: str) -> list[str]:
    """Every literal `version:` value of the setup-cli step's `with:`, in source order.

    ⛔ SCOPED TO THE PIN'S OWN KEY, NOT TO EVERY `version:` IN THE STEP BLOCK. The text scan this
    replaces counted any matching line inside the step's byte span, so a CORRECTLY pinned step that
    merely declared an env var named `version` was refused:

        with:
          version: 2.122.1
        env:
          version: 1.0.0
        -> REFUSED: "carries 2 literal `version:` lines (2.122.1, 1.0.0) — a duplicate key's
           tie-break is not measured … Pin exactly once."

    That diagnostic names a mechanism `_structural_duplicate_keys` reports as ABSENT (there is no
    duplicate key — `env.version` and `with.version` are different mappings), and prescribes a remedy
    the file already satisfies. It also contradicted this file's own rule, stated in
    `_alias_supplies_the_pin`: "A `version:` under `env:`/`secrets:` is a different key that happens
    to share a name." Measured before this fix.

    The key node's line is taken from the COMPOSED tree, so a legal spelling (`version :`,
    `'version':`) still resolves, and only lines that belong to the pin are considered. A FLOW
    mapping (`with: {version: …}`) is still refused, for the right reason: its `version:` key sits on
    the `with:` line, which is not a `version:` line of its own.
    """
    step_node = _setup_cli_step_node(workflow_text)
    pin_lines: set[int] = set()
    for key_node, value_node in step_node.value:
        if not (isinstance(key_node, yaml.ScalarNode) and key_node.value == "with"):
            continue
        if not isinstance(value_node, yaml.MappingNode):
            continue
        for pin_key, _pin_value in value_node.value:
            if isinstance(pin_key, yaml.ScalarNode) and pin_key.value == "version":
                pin_lines.add(pin_key.start_mark.line)
    lines = workflow_text.splitlines()
    found: list[str] = []
    for line_no in sorted(pin_lines):
        if line_no >= len(lines):
            continue
        if match := _LITERAL_VERSION_LINE.match(lines[line_no]):
            found.append(match.group(1))
    return found


def _structural_duplicate_keys(text: str) -> list[str]:
    """Every duplicated mapping key in the document, found STRUCTURALLY, not by text matching.

    ⛔ A text scan for `with:` cannot decide this: YAML lets the same key be spelled `"with"`,
    `'with'`, or `with :`, and review demonstrated all three hiding a second pin
    (`"with": {version: 3.0.0}` plus a literal `version: 2.122.1`) while a regex-based refusal
    reported one `with:` and stayed green. `yaml.compose()` sees the mapping, so the spelling is
    irrelevant — and the same arm then covers `uses:` and `<<` at every level for free.
    """

    def walk(node: object, path: str = "<root>") -> list[str]:
        duplicates: list[str] = []
        if isinstance(node, yaml.MappingNode):
            seen: dict[str, int] = {}
            for key_node, value_node in node.value:
                key = key_node.value if isinstance(key_node, yaml.ScalarNode) else repr(key_node)
                if key in seen:
                    duplicates.append(
                        f"{path}: duplicate key {key!r} "
                        f"(lines {seen[key] + 1} and {key_node.start_mark.line + 1})"
                    )
                else:
                    seen[key] = key_node.start_mark.line
                duplicates += walk(value_node, f"{path}.{key}")
        elif isinstance(node, yaml.SequenceNode):
            for index, item in enumerate(node.value):
                duplicates += walk(item, f"{path}[{index}]")
        return duplicates

    return walk(yaml.compose(text))


def _normalized_input_collisions(with_mapping: dict) -> list[str]:
    """Keys of an action `with:` mapping that resolve to the SAME input once GitHub normalizes them.

    `@actions/core` reads an input as `INPUT_${name.replace(/ /g, "_").toUpperCase()}`, so `version`
    and `Version` are one input to the action while being two distinct keys to PyYAML — and the
    tie-break between them is unmeasured. Measured by review on this very file: the live step plus a
    `Version: 3.0.0` line resolved to `2.122.1`, reported NO structural duplicate key, and left
    `_violations` empty — a major-3 pin, green behind a capital letter. That is class (2) of the
    declared surface ("a pin this reader resolves differently from CI"), not a cosmetic nit.

    Scoped to `with:` on purpose: the normalization belongs to action INPUTS. Applying it to every
    mapping in the document would assert something GitHub does not do (step keys, matrix keys and
    `env:` names are not case-folded this way) and would false-block on legitimate shapes.
    """
    by_input: dict[str, list[str]] = {}
    for key in with_mapping:
        by_input.setdefault(str(key).replace(" ", "_").upper(), []).append(str(key))
    return [
        f"`{'` and `'.join(keys)}` both resolve to `INPUT_{normalized}`"
        for normalized, keys in by_input.items()
        if len(keys) > 1
    ]


#: PyYAML's tag for an EXPLICIT merge key (`<<` written plain). A QUOTED `"<<"` is NOT this tag: it
#: is an ordinary string key, which PyYAML keeps and GitHub keeps too — so the structural check below
#: deliberately does not refuse it (#11134 review: an earlier `"<<" in with_` arm fired on exactly
#: that harmless spelling and claimed a merge-key mechanism it did not have).
_MERGE_TAG = "tag:yaml.org,2002:merge"


def _construct_refusals(workflow_text: str) -> list[str]:
    """Every YAML construct in the setup-cli step whose meaning for CI this reader cannot establish.

    ONE construct is refused — a **merge key** (plain `<<`, tagged `tag:yaml.org,2002:merge`), at
    EVERY depth, because `with: {version: …}` supplied by a step-level `<<:` is the same fail-open as
    one written inside `with:`: PyYAML synthesizes the mapping, so `_requested_version` reads a pin
    out of a construct the runner may never see (the action would then fall back to `latest`, 3.x).
    Measured before this walk existed: the step-level spelling returned `[]` from `_violations` —
    green — while this module declared merge keys refused outright.

    An **alias is NOT refused** — `with: *pin` is ACCEPTED, because GitHub's Actions reference
    documents anchors and aliases as supported workflow YAML, so PyYAML and the runner resolve the
    same mapping. Only the VALUE an alias carries is checked, by every arm in `_requested_version`.
    (An earlier version of this function also refused aliases; see `_alias_supplies_the_pin` for the
    correction and its citation.) Scoped to the step and walked over composed nodes, because a check
    over the parsed dict cannot see a merge key at any level (see `_setup_cli_step_node`).

    A QUOTED `"<<"` is deliberately NOT refused: it is an ordinary string key in both parsers, so it
    is not a divergence and refusing it would be a false block.
    """
    found: list[str] = []
    step_node = _setup_cli_step_node(workflow_text)
    # ⛔ The walk below starts at the node `_setup_cli_step_node` RETURNS. When the step's own `uses:`
    # (and `with:`) arrive from a step-level merge key, that node is the merge TARGET — the `<<:` sits
    # on the step mapping, which is the target's PARENT and is therefore never walked — so the merge
    # key itself escaped. Measured: that shape returned `[]` here AND from `_violations`, while the
    # docstring above claims merge keys are refused at EVERY depth. Blast radius is bounded (GitHub
    # reads no `uses:` at all, so the workflow cannot run, and the major-3 ceiling still refuses a v3
    # pin in the same shape) — but a silent `[]` is exactly what a later reader trusts, so it is
    # closed rather than recorded as a residual.
    #
    # The merge key is found from the DOCUMENT, by locating the node whose `start_mark.index` is our
    # step's: node identity does not survive a second `compose()`, and matching on the mark does.
    # This cannot be replaced by "does the returned node declare `uses:` literally?" — it does, which
    # is precisely why the merge key was invisible.
    step_start = step_node.start_mark.index
    seen: set[int] = set()

    def merge_supplies_this_step(node: object) -> bool:
        if id(node) in seen:  # an alias can point back at an ancestor
            return False
        seen.add(id(node))
        if isinstance(node, yaml.MappingNode):
            for key_node, value_node in node.value:
                if getattr(key_node, "tag", None) == _MERGE_TAG:
                    targets = (
                        value_node.value
                        if isinstance(value_node, yaml.SequenceNode)
                        else [value_node]
                    )
                    if any(
                        getattr(getattr(target, "start_mark", None), "index", None) == step_start
                        for target in targets
                    ):
                        return True
                if merge_supplies_this_step(value_node):
                    return True
        elif isinstance(node, yaml.SequenceNode):
            for item in node.value:
                if merge_supplies_this_step(item):
                    return True
        return False

    if merge_supplies_this_step(yaml.compose(workflow_text)):
        found.append(
            "the step's `uses:` arrives through a merge key (`<<:`) — GitHub Actions does not "
            "implement merge keys, so the job would see no action here at all"
        )

    def walk(node: object) -> None:
        if isinstance(node, yaml.MappingNode):
            for key_node, value_node in node.value:
                if getattr(key_node, "tag", None) == _MERGE_TAG:
                    found.append(
                        f"a merge key (`<<`) at line {key_node.start_mark.line + 1}"
                    )
                walk(value_node)
        elif isinstance(node, yaml.SequenceNode):
            for item in node.value:
                walk(item)

    walk(step_node)
    return found


def _alias_supplies_the_pin(workflow_text: str) -> bool:
    """Does an ALIAS supply this step's whole `with:`, or its `version:`?

    ⛔ NOT a refusal. GitHub Actions RESOLVES anchors and aliases — its own reference says so in as
    many words ("You can use YAML anchors and aliases to reduce repetition in your workflows … This
    is equivalent to writing the following YAML without anchors and aliases", including an
    entire-job reuse example), so `with: *pin` is a legitimate spelling of a correctly pinned step
    and PyYAML resolves it exactly as CI does. Refusing it was a FALSE BLOCK whose diagnostic
    asserted the action "would see no `version` input and fall back to `latest`" — a mechanism that
    does not exist (found in review, P2, with the doc citation; the previous arms were built on the
    opposite premise and are corrected here).

    What still matters is the pin's VALUE: an aliased `with:` has no literal `version:` line of its
    own, so the literal-line arm below must not demand one. The value read through the alias is
    checked by every other arm (exact x.y.z, the major ceiling, duplicate keys, input collisions),
    which is why accepting the spelling costs no coverage.

    ⛔ THE TEST IS NARROW ON PURPOSE (found in review, P3): it asks whether the pin ITSELF arrives
    through an alias — `with:` or `version:` whose value is an `AliasToken`, inside this step — NOT
    whether *any* `with:`/`version:` key in the step has an aliased value. Measured with the wide
    version: a step whose pin IS a literal `version:` line, plus an unrelated `version: *x` in its
    `env:`, flipped the flag to True — which switched OFF the literal-line arm for a workflow whose
    only reason to be refused was that arm. The skip must be exactly as wide as its justification.

    Detected in the TOKEN stream, because PyYAML 6 has no `AliasNode` — it substitutes the anchored
    node object, so an alias is invisible in the composed tree. `KeyToken` carries no `.value`, so
    the key's text arrives as the following `ScalarToken`.
    """
    step_node = _setup_cli_step_node(workflow_text)
    step_first, step_last = step_node.start_mark.index, step_node.end_mark.index

    def alias_reached(value: object) -> bool:
        """Did this VALUE arrive by alias? PyYAML substitutes the ANCHORED node, so the tell is its
        position: a node reached through an alias carries the marks of the ANCHOR's location, which
        is outside this step. Measured on `with: *pinned` — the composed value node's
        `start_mark.index` is the anchor's, not the alias's."""
        mark = getattr(value, "start_mark", None)
        return mark is not None and not (step_first <= mark.index < step_last)

    with_node = None
    for key_node, value_node in step_node.value:
        if isinstance(key_node, yaml.ScalarNode) and key_node.value == "with":
            if alias_reached(value_node):
                return True  # `with: *pinned` — the whole input mapping, pin included
            with_node = value_node
    if isinstance(with_node, yaml.MappingNode):
        for key_node, value_node in with_node.value:
            # `version:` INSIDE the step's own `with:` — including a flow mapping
            # (`with: {version: *pinned}`), which the textual window used to miss.
            if isinstance(key_node, yaml.ScalarNode) and key_node.value == "version":
                return alias_reached(value_node)
    return False


def _requested_version(workflow_text: str) -> str:
    """The CLI version the workflow pins, and proof that CI resolves the same value.

    ⛔ The reader has its own bypass surface, separate from the comparison (found in review): PyYAML
    implements the YAML MERGE KEY (`<<: {version: 2.122.1}`), which GitHub Actions does NOT — so a
    step could carry no `version:` line at all, PyYAML would synthesize one, and the
    `version is None` arm below would never fire while the action fell back to `latest` (3.x). The
    same class covers a duplicate `version:` key, where PyYAML takes the last one and GitHub's
    tie-break is not measured, and a CASE-VARIANT key, where PyYAML sees two distinct keys but
    the action sees one input (see `_normalized_input_collisions`). So the step must carry
    EXACTLY ONE literal `version:` line, and it must be the resolved value — UNLESS the pin is
    supplied by an alias, which is ACCEPTED: GitHub resolves anchors and aliases (see
    `_alias_supplies_the_pin`), so such a step installs exactly what this reader resolves and the
    literal-line demand would be a false block. MERGE KEYS are refused outright, STRUCTURALLY over
    the composed step node (`_construct_refusals`) — not by looking inside the parsed `with:`
    mapping, which has already flattened them.
    """
    constructs = _construct_refusals(workflow_text)
    assert not constructs, (
        "the setup-cli step carries a YAML construct whose meaning for CI this reader cannot "
        "establish: " + "; ".join(constructs) + ". GitHub's Actions reference documents anchors "
        "and aliases, NOT merge keys — so a `<<:` may be a mapping PyYAML builds and the runner "
        "never sees, and the action would fall back to `latest` (3.x) while this reader reports a "
        "pin. Write the mapping out instead of merging it."
    )
    # An alias-supplied pin is ACCEPTED: GitHub RESOLVES anchors and aliases (see
    # `_alias_supplies_the_pin`), so it installs exactly what this reader resolves. It simply has no
    # literal `version:` line of its own, so the literal-arms below are skipped for it; every other
    # check still runs on the resolved VALUE.
    alias_supplied = _alias_supplies_the_pin(workflow_text)
    step = _setup_cli_step(workflow_text)
    with_ = step.get("with") or {}
    collisions = _normalized_input_collisions(with_)
    assert not collisions, (
        "the setup-cli step's `with:` declares keys that GitHub resolves to the same input: "
        + "; ".join(collisions)
        + ". GitHub upper-cases an input name and turns spaces into underscores, so these are ONE "
        "input to the action and two keys to this reader — the tie-break is unmeasured, so the pin "
        "asserted here is not provably the one installed"
    )
    version = with_.get("version")
    assert version is not None, (
        "the setup-cli step no longer names a version — the action then falls back to its own "
        "default (the root lockfile's version, else `latest`), which is the float this pin exists "
        "to remove"
    )
    resolved = str(version).strip()
    duplicates = _structural_duplicate_keys(workflow_text)
    assert not duplicates, (
        "the workflow declares a duplicate mapping key: "
        + "; ".join(duplicates)
        + ". A duplicate key's tie-break is not measured against GitHub's parser (PyYAML takes the "
        "last value), so a second pin could be the one CI installs while this reader asserts the "
        "first. Declare each key once."
    )
    literals = _literal_version_lines(workflow_text)
    if alias_supplied:
        return resolved
    assert literals, (
        f"the pin `{resolved}` is not a literal `version:` line in the setup-cli step. This arm is a "
        "BLOCK-STYLE requirement, and the three shapes that reach it are refusals for different "
        "reasons — do not read one sentence as covering all of them. A MERGE KEY (`<<:`) building "
        "the `with:` mapping or the pin is refused before this arm by `_construct_refusals`, because "
        "PyYAML builds a mapping the runner may not see. A step written as a FLOW MAPPING "
        "(`- {uses: …, with: {version: …}}`) is refused here ONLY because this reader requires the "
        "pin on its own line — its value IS what CI reads; rewrite the step in block style if you "
        "want it accepted. A pin supplied by an ALIAS is ACCEPTED outright (see "
        "`_alias_supplies_the_pin`); reaching this message through one means the alias sits "
        "somewhere this reader could not follow."
    )
    assert len(literals) == 1, (
        f"the setup-cli step carries {len(literals)} literal `version:` lines ({', '.join(literals)}) "
        "— a duplicate key's tie-break is not measured against GitHub's parser, so the value read "
        "here is not provably the one installed. Pin exactly once."
    )
    assert literals[0] == resolved, (
        f"the setup-cli step's literal pin is `{literals[0]}` but this reader resolved `{resolved}` "
        "— the two disagree, so a construct is being resolved differently than CI would"
    )
    return resolved


def _config_declares_inbucket(config_text: str) -> bool:
    """True when `supabase/config.toml` still carries an `[inbucket]` table.

    ASK A TOML PARSER, NOT A LINE REGEX. The table header may be spelled `[inbucket]`,
    `[ inbucket ]`, `["inbucket"]`, `['inbucket']`, or declared through the dotted form
    `inbucket.enabled = true` — every one of those is the SAME table to any TOML parser, the CLI's
    included, and the CLI rejects every one of them alike. A detector that accepts only the
    canonical spelling therefore **mis-diagnoses the refusal**: it reports the consent gate instead
    of `DbConfigLoadError`, sending the reader to the `--yes` half of #11104 rather than to the
    section header that actually killed the apply. Measured: the line regex this replaces read
    `[ inbucket ]` and `["inbucket"]` as "no [inbucket] table" while `tomllib` reads both as
    `{'inbucket': …}`, so six arms failed on the WRONG message.

    ⛔ Scope, so this is not over-claimed: on THIS tree the detector no longer decides whether a
    major-3 pin is refused — the ceiling below refuses every `major >= 3` unconditionally — so a
    miss here is a wrong reason, not a false PASS. It DID decide that on `main`'s version of this
    file, where the major-3 refusal was gated on this predicate; that is what made the guard inert
    when #11104 migrated the config.

    The trailing-comment arm survives for the same reason: TOML reads `[inbucket]   # local email`
    as the same table, and so does the CLI.

    ⛔ FAILS CLOSED: a config this reader cannot parse returns True (treated as still declaring the
    table), so an unreadable `config.toml` reports the config-load reason rather than silently
    reading as "no [inbucket] table" — `db push` cannot load an unparseable config either, so the
    config-load verdict is the correct one.
    """
    try:
        declared = tomllib.loads(config_text)
    except tomllib.TOMLDecodeError:
        return True
    return "inbucket" in declared


def _violations(workflow_text: str, config_text: str) -> list[str]:
    r"""Every way the workflow's `supabase/setup-cli` step pins a CLI this repo cannot safely run.

    DECLARED THREAT SURFACE — in scope, each covered by an arm below:
      (1) a floating pin (`latest`, `v2`, `^2.122.1`, …) — any shape that is not an exact x.y.z;
      (2) a pin this reader resolves differently from CI (merge key, duplicate key, alias, or an
          input key that collides with `version` after GitHub's own normalization), so the value
          asserted is not the value installed. MERGE KEYS are refused structurally over the
          composed step node (`_construct_refusals`), at every depth — one that supplies the step's
          whole `with:` is the same fail-open as one written inside it, and a probe over the parsed
          dict sees neither. ALIASES are not a divergence (GitHub documents them), so an aliased pin
          is accepted and only its resolved VALUE is checked;
      (3) a major-3 pin — the config-load break while `[inbucket]` is declared, and the hosted-
          mutation consent gate once it is not.

    DECLARED OUT OF SCOPE (not silently assumed covered): a `run:` step that installs or invokes a
    v3 CLI by itself (`npx supabase@3.0.0 …`, `npm i -g supabase@…`). That is a code change, and it
    is reviewed as code; the mutating-command argument checks belong to #11104's own suite (`--yes`
    has LANDED on `main`, so it is carried at this pin). The
    title above names the STEP for exactly this reason — do not read it as a claim about `run:`
    bodies.

    ⛔ The ceiling is deliberately a MAJOR comparison rather than a scan of the workflow's arguments.
    An earlier draft decided v3-readiness by looking for `--yes` in the mutating commands, and review
    defeated it five ways — `--yes` named in a trailing comment, `--yes=false`, a `--yes` belonging
    to another command, one commented out after a `\` continuation, and the global-flag form
    `supabase --debug db push` (valid in the CLI, and its own error text recommends `--debug`). An
    integer comparison has no such surface: the major either moves or it does not.

    The ceiling is lifted by the PR that makes the workflow v3-ready, not by a symptom moving.
    """
    found: list[str] = []
    version = _requested_version(workflow_text)
    if not _EXACT_PIN.match(version):
        found.append(
            f"setup-cli `version: {version}` is not an exact x.y.z pin — a reference that floats "
            "the MAJOR can change the config schema under the deploy, and one that floats only "
            "the PATCH takes upgrades no diff records; this is how #10593's apply was lost"
        )
        return found
    major = int(version.split(".", 1)[0])
    if major < _V3_MAJOR:
        return found
    if _config_declares_inbucket(config_text):
        found.append(
            f"setup-cli pins {version} (major {major}) while supabase/config.toml still "
            "declares [inbucket]; CLI major 3 raises DbConfigLoadError at config load, so "
            "`db push` exits 1 before it touches the database"
        )
    else:
        found.append(
            f"setup-cli pins {version} (major {major}), but this workflow is not v3-ready: major 3 "
            "consent-gates every hosted mutation and cannot prompt in CI, so `db push` and "
            "`functions deploy` exit non-zero having applied nothing unless `--yes` is passed "
            "(HostedMutationConsentRequiredError). BOTH halves have landed — the config rename "
            "(#10896) and the consent flags (#11104) — so this refusal is not a missing "
            "capability: it is #11100's deliberate ruling that the major moves in its own PR, "
            "because the config is not yet v3-NATIVE (3.0.0 still emits the "
            "`auth.email.enable_signup`/`auth.sms.enable_signup` deprecations) and the local "
            "harnesses have not been validated under v3 (`supabase start`/`db reset` + the "
            "local-auth suites). Move the pin in the PR that lands it and lift this arm there "
            "deliberately — a red here is the ceiling working, not a bug to relax."
        )
    return found


# ── the live tree: green-on-landing proof ──────────────────────────────────────


def test_the_live_workflow_pins_a_cli_this_repos_config_can_load() -> None:
    assert WORKFLOW.exists(), f"{WORKFLOW} is missing"
    problems = _violations(
        WORKFLOW.read_text(encoding="utf-8"),
        CONFIG.read_text(encoding="utf-8"),
    )
    assert not problems, "\n".join(problems)


# ── mutation proofs: the check must go RED on each half of the defect ──────────


def test_a_floating_version_is_refused() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    config = CONFIG.read_text(encoding="utf-8")
    # The exact pre-fix line, not a paraphrase: this is what the deploy ran on.
    mutated = re.sub(
        r"(?m)^(\s*version:\s*).*$",
        r"\1latest",
        workflow,
        count=1,
    )
    assert _literal_version_lines(mutated) == ["latest"], (
        "mutation did not apply to the step's own pin — this test would be vacuous"
    )
    problems = _violations(mutated, config)
    assert problems, "a floating `version: latest` passed the check"


@pytest.mark.parametrize("floating", ["latest", "v2", "2", "2.x", "^2.122.1", "~2.122"])
def test_every_floating_shape_is_refused(floating: str) -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    mutated = re.sub(r"(?m)^(\s*version:\s*).*$", rf"\g<1>{floating}", workflow, count=1)
    assert _violations(mutated, CONFIG.read_text(encoding="utf-8")), (
        f"`version: {floating}` passed the check"
    )


def test_a_major_the_committed_config_cannot_load_is_refused() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    config = CONFIG.read_text(encoding="utf-8")
    if not _config_declares_inbucket(config):
        pytest.skip(
            "config.toml no longer declares [inbucket] — the CONFIG-LOAD half of the coupling is "
            "retired; the major-3 ceiling still guards the pin (#10896; the retirement landed in "
            "#11104)"
        )
    mutated = re.sub(
        r"(?m)^(\s*version:\s*).*$",
        r"\g<1>3.0.0",
        workflow,
        count=1,
    )
    assert "version: 3.0.0" in mutated, "mutation did not apply — this test would be vacuous"
    problems = _violations(mutated, config)
    assert problems, "pinning major 3 against an [inbucket] config passed the check"
    assert "DbConfigLoadError" in problems[0], problems[0]


def test_a_construct_this_workflow_cannot_carry_is_refused() -> None:
    """A `<<:` merge key must be REFUSED — it is the construct whose meaning for CI is unknowable.

    PyYAML implements the YAML merge key (`<<`); GitHub's Actions reference documents anchors and
    aliases but NOT merge keys, so a `<<:` may be a mapping the runner never sees — the action would
    then fall back to `latest` (3.x) while this reader reports a pin, a fail-open identical to
    #10593. ALIASES are the opposite case and are ACCEPTED (GitHub documents them; see
    `test_an_alias_that_does_not_supply_the_pin_does_not_refuse`), which is why the third spelling
    below asserts the VALUE an alias carries is still refused while the spelling is not.

    ⛔ THREE spellings, at three depths, because a dict-level probe covers none of them and the
    step-level one was measured GREEN before `_construct_refusals` existed (#11134 review, P1):

    | spelling | how PyYAML sees it | refused by |
    |---|---|---|
    | `<<:` INSIDE `with:` | `with` = `{'version': …}` | the merge tag, at depth ≥ 1 |
    | `<<:` supplying `with:` on the STEP | the step = `{'with': {'version': …}}` | the merge tag, at depth 0 |
    | `with: *pin` | the anchored mapping, followed | NOTHING — aliases are accepted (GitHub documents them); the VALUE the anchor carries is refused by the ceiling |

    The step-level row is the expensive one: it reads as green everywhere a `with`-shaped probe looks,
    because by then `with` is a perfectly ordinary mapping.

    A QUOTED `"<<"` is deliberately NOT here: it is an ordinary string key in both parsers, so
    refusing it would be a false block rather than a guard (see `_construct_refusals`).
    """
    workflow = WORKFLOW.read_text(encoding="utf-8")
    pin = _requested_version(workflow)  # the live pin, so no mutation hardcodes a version
    canonical = re.compile(r"(?m)^        with:\n          version: [^\s#]+\n")
    assert len(canonical.findall(workflow)) == 1, (
        "the setup-cli step's `with:` no longer has the canonical shape these mutations drive — "
        "update them rather than silently proving nothing"
    )

    # (a) a merge key INSIDE `with:` — no `version:` line survives in the step.
    inside = canonical.sub(f"        with:\n          <<: {{version: {pin}}}\n", workflow)
    assert f"<<: {{version: {pin}}}" in inside, "mutation (a) did not apply — vacuous"
    assert not re.search(r"(?m)^\s*version:\s*" + re.escape(pin), inside), (
        "a literal pin line survived mutation (a) — the arm would not test the merge tag"
    )
    # ⛔ The `match` names the ARM, not the verb (#11134 review P2). `match="merge key"` was
    # satisfied by the LITERALS arm's prose (which says "merge key, alias" while explaining what a
    # construct could be), so a deleted merge walk passed this case — the failure it produced came
    # from a different arm. "cannot establish" appears in the construct refusal and nowhere else.
    with pytest.raises(AssertionError, match=r"cannot establish: a merge key"):
        _requested_version(inside)

    # (b) a merge key that SUPPLIES `with:` — the shape the dict-level probe could not see at all.
    supplying = canonical.sub(f"        <<:\n          with:\n            version: {pin}\n", workflow)
    assert "        <<:\n          with:" in supplying, "mutation (b) did not apply — vacuous"
    assert not _structural_duplicate_keys(supplying), (
        "mutation (b) introduced a duplicate key — the structural-key arm would be what fires, "
        "not the merge arm this case is for"
    )
    with pytest.raises(AssertionError, match=r"cannot establish: a merge key"):
        _requested_version(supplying)

    # (c) a MAJOR-3 pin behind an ALIAS — the alias itself is legitimate (GitHub resolves anchors
    #     and aliases, see `_alias_supplies_the_pin`), so what must be refused is the VALUE it
    #     supplies. This is the fail-open direction of the alias case: if accepting the spelling had
    #     also accepted whatever major the anchor carries, it would be a hole.
    aliased = workflow.replace(
        "      - uses: supabase/setup-cli@v1",
        "      - uses: actions/checkout@v9\n"
        "        with: &pinned\n"
        f"          version: {pin}\n"
        "      - uses: supabase/setup-cli@v1",
        1,
    )
    aliased = canonical.sub("        with: *pinned\n", aliased)
    assert "with: *pinned" in aliased, "mutation (c) did not apply — vacuous"
    aliased_v3 = aliased.replace(f"          version: {pin}\n", "          version: 3.0.0\n", 1)
    assert "version: 3.0.0" in aliased_v3, "mutation (c) did not apply to the anchor — vacuous"
    # The alias spelling is ACCEPTED (GitHub resolves aliases; `_alias_supplies_the_pin`) — so the
    # assertion is on the VALUE the anchor carries, which the major ceiling must still refuse.
    assert _requested_version(aliased_v3) == "3.0.0", "the aliased pin was not read"
    assert _construct_refusals(aliased_v3) == [], (
        "the alias spelling itself is legitimate and must not be reported as a construct"
    )
    problems = _violations(aliased_v3, CONFIG.read_text(encoding="utf-8"))
    assert problems and "major 3" in problems[0], problems

    # (d) a merge key that supplies the step's OWN `uses:` as well as its `with:`. The returned node
    #     is then the merge TARGET, so the `<<:` sits on that node's PARENT and the depth walk above
    #     never sees it. Measured before this case existed: `_construct_refusals` returned `[]` and
    #     `_violations` returned `[]`, while the docstring promised refusal "at EVERY depth".
    merged_step = (
        "      - <<: &m\n"
        "          uses: supabase/setup-cli@v1\n"
        "          with:\n"
        f"            version: {pin}\n"
        "        name: merged step\n"
    )
    # Two substitutions, because a long comment block sits between the step's `- uses:` line and its
    # `with:` — so the step head is NOT contiguous text. Replacing only the `- uses:` line leaves the
    # original `with:` block behind, which then becomes a DUPLICATE `with:` key and makes the
    # structural-key arm fire instead of this one.
    merged = workflow.replace(
        "      - uses: supabase/setup-cli@v1", merged_step.rstrip("\n"), 1
    )
    merged = canonical.sub("", merged, count=1)
    assert "<<: &m" in merged and merged != workflow, "mutation (d) did not apply — vacuous"
    assert not canonical.search(merged), (
        "a canonical literal `with:`/`version:` block survived mutation (d) — the arm would be "
        "testing the literals check, not the merge key"
    )
    assert any("merge key" in refusal for refusal in _construct_refusals(merged)), (
        "the step-level merge that supplies `uses:` reached no arm — `_construct_refusals` "
        f"returned {_construct_refusals(merged)!r}"
    )
    with pytest.raises(AssertionError, match=r"cannot establish: the step's `uses:` arrives"):
        _requested_version(merged)


def test_a_duplicate_or_decoy_version_line_is_refused() -> None:
    """The pin must be the step's OWN single literal line.

    Review demonstrated two green paths through the file-wide search this arm replaces: a decoy
    literal in an unrelated step (`actions/setup-node`'s `with: version: 2.122.1`) satisfied the
    search while the setup-cli step carried none, and a duplicated `version:` key left two values
    where the reader takes one and GitHub's tie-break is unmeasured. "Some matching line exists in
    the file" was never the claim the guard needed to make.

    ⛔ The mutations below rewrite the CANONICAL step shape (`- uses:` then `with:`). If the real
    workflow is ever reordered (e.g. `with:` before `uses:`), this arm fails LOUDLY with "mutation
    did not apply" rather than quietly proving nothing — fail-closed on purpose. The live-tree arm
    still guards the reordered shape, so the reorder cannot slip through unguarded; the loud
    failure is the signal to update these mutations.
    """
    workflow = WORKFLOW.read_text(encoding="utf-8")
    # The live pin, so a MANUAL PATCH BUMP does not turn every mutation below vacuous at once. The
    # workflow says in as many words that the pin is taken up by hand (`supabase-deploy.yml`: an
    # `x.y.z` in a `with:` input is invisible to Dependabot), so a hardcoded `2.122.1` here would RED
    # this required suite on a bump that is correct and expected, with a message about a mutation
    # that did not apply (found in review, P2). The sibling mutation tests already derive it.
    pin = _requested_version(workflow)
    literal_pin = re.compile(r"(?m)^(\s*)version:\s*" + re.escape(pin) + r"\s*$")
    canonical_with = re.compile(r"(?m)^(\s*)with:\n\s*version:\s*" + re.escape(pin) + r"\s*$")

    # (a) the step pins via a flow mapping (no `version:` LINE inside it) while a decoy literal sits
    #     in an unrelated step. A file-wide search is satisfied by the decoy; the step-scoped check is
    #     not. This is the exact shape review used to turn the old search green.
    flow = canonical_with.sub(r"\1with: {version: " + pin + "}", workflow, count=1)
    assert f"with: {{version: {pin}}}" in flow, "mutation did not apply — this test would be vacuous"
    assert not re.search(r"(?m)^\s*version:\s*" + re.escape(pin) + r"\s*$", flow), (
        "a literal pin line survived the mutation — the arm would not test the scope"
    )
    flow += f"\n      - uses: actions/setup-node@v4\n        with:\n          version: {pin}\n"
    with pytest.raises(AssertionError, match="not a literal"):
        _requested_version(flow)

    # (a2) ⛔ THE WHOLE STEP ON ONE LINE (#11134 review cycle 1, P3; corrected in cycle 8). A
    # one-line flow mapping starts AND ends on the same line, so a reader that sliced the step by
    # LINE would produce an empty (or, with an `end <= start` widening, empty-but-padded) slice and
    # assert out with "resolved to no source lines" — a FALSE diagnosis that blames the reader for a
    # step it resolved perfectly. `_literal_version_lines` cannot make that mistake: it takes the
    # line from the pin's KEY NODE mark, so the read is byte-precise by construction.
    #
    # ⛔ The assertion below is on `_literal_version_lines`, NOT on `_setup_cli_block`: the block
    # slicer is not on the guard path (its only caller is a landing assertion), so reverting IT
    # changes nothing here. The reader whose reversion this case reds is `_literal_version_lines`:
    # with the pin genuinely absent it must ANSWER `[]` — "the step was found and carries no literal
    # `version:` line" — and only then may `_requested_version` refuse with `not a literal`. A
    # slicing reader that cannot resolve the step raises instead, so the two outcomes are
    # distinguishable, and that is what makes asserting the REASON load-bearing.
    one_line = workflow.replace(
        "      - uses: supabase/setup-cli@v1\n"
        "        with:\n"
        f"          version: {pin}\n"
        "        env:\n"
        "          SUPABASE_ACCESS_TOKEN: ${{ secrets.SUPABASE_ACCESS_TOKEN }}\n",
        f"      - {{uses: supabase/setup-cli@v1, with: {{version: {pin}}}}}\n",
        1,
    )
    assert f"      - {{uses: supabase/setup-cli@v1, with: {{version: {pin}}}}}\n" in one_line, (
        "mutation (a2) did not apply — vacuous. The canonical step shape changed; update this "
        "mutation rather than deleting the case"
    )
    assert _literal_version_lines(one_line) == [], (
        "a one-line step carries no literal `version:` line, so the reader must ANSWER [] rather "
        f"than fail to resolve the step — it returned {_literal_version_lines(one_line)!r}"
    )
    with pytest.raises(AssertionError, match="not a literal"):
        _requested_version(one_line)

    # (a3) ⛔ NO TRAILING NEWLINE, STEP IS THE FILE'S LAST NODE (#11134 review cycle 2, P2).
    # `end_mark` is the first character AFTER the mapping, so at EOF it sits ON the mapping's last
    # line — a position like `(8, 26)`, not `(9, 0)`. A LINE slice is exclusive and DROPS that last
    # line, which here is the PIN ITSELF, so the guard refused a legal workflow with "not a literal
    # `version:` line … write `version:` on its own line" — a remedy the file already satisfied —
    # while ONE BYTE decided it: appending `\n` moved `end_mark` to `(9, 0)` and the same file
    # passed. GitHub parses both identically, so this is a FALSE BLOCK, and the byte-precise
    # KEY-NODE read is what makes it go away. Reverting `_literal_version_lines` to a LINE slice of
    # the step reds this case — and the reversion is asserted on THAT reader, on the line below,
    # rather than on the block slicer it never calls.
    eof = (
        "name: t\non: push\njobs:\n  j:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - uses: supabase/setup-cli@v1\n"
        "        with:\n"
        f"          version: {pin}"
    )
    assert not eof.endswith("\n"), "this case must end at the pin with NO trailing newline"
    assert _literal_version_lines(eof) == [pin], (
        "the pin is the file's last line and the file has no trailing newline — a LINE slice of the "
        f"step drops exactly that line, so this must read {pin!r}: {_literal_version_lines(eof)!r}"
    )
    assert _requested_version(eof) == pin, (
        "a workflow whose LAST line is the pin, with no trailing newline, was refused — the pin's "
        "own key-node line was not read, so the guard blamed a step it had resolved correctly"
    )
    assert _violations(eof, CONFIG.read_text(encoding="utf-8")) == [], (
        "a legal, correctly pinned workflow was refused at the GUARD level over a trailing byte"
    )
    # The same file WITH the byte must behave identically — the two must not disagree, which is the
    # whole claim: the trailing newline carries no meaning.
    assert _requested_version(eof + "\n") == pin

    # (b) two literal pins in the step must be refused, not silently resolved to one.
    duplicated = literal_pin.sub(lambda mm: f"{mm.group(1)}version: latest\n{mm.group(1)}version: {pin}", workflow, count=1)
    assert _literal_version_lines(duplicated) == ["latest", pin], (
        "mutation did not apply to the step's own pins — this test would be vacuous. The check is "
        "step-scoped on purpose: a FILE-wide search is satisfied by a decoy literal in an unrelated "
        "step — the `actions/setup-node` pin mutated in directly above, which is exactly the shape "
        "review used to turn the old file-wide search green"
    )
    # Two `version:` keys in one mapping is a DUPLICATE KEY, which the structural check refuses
    # before the count does — the count remains for the case of two pins in different mappings.
    with pytest.raises(AssertionError, match="duplicate mapping key"):
        _requested_version(duplicated)

    # (c) a duplicated `with:` key must be refused: one flow mapping pinning 3.0.0 and one literal
    #     pinning the live pin leaves PyYAML taking the last, while GitHub's tie-break is unmeasured.
    twice = canonical_with.sub(
        lambda mm: f"{mm.group(1)}with: {{version: 3.0.0}}\n{mm.group(1)}with:\n{mm.group(1)}  version: {pin}",
        workflow,
        count=1,
    )
    assert twice.count("with: {version: 3.0.0}") == 1, "mutation did not apply — vacuous"
    with pytest.raises(AssertionError, match="duplicate mapping key"):
        _requested_version(twice)

    # (d) the refusal must be STRUCTURAL: YAML lets the same key be spelled `"with"`, `'with'`, or
    #     `with :`, and a text scan misses every one of them while the second pin hides behind it.
    canonical_with_text = f"        with:\n          version: {pin}"
    assert canonical_with_text in workflow, (
        "the setup-cli step's `with:` no longer has the canonical shape these mutations drive — "
        "update them rather than silently proving nothing"
    )
    for respelled in ('"with": {version: 3.0.0}', "'with': {version: 3.0.0}", "with : {version: 3.0.0}"):
        smuggled = workflow.replace(
            canonical_with_text,
            f"        {respelled}\n{canonical_with_text}",
            1,
        )
        assert respelled in smuggled, f"mutation did not apply for {respelled!r} — vacuous"
        with pytest.raises(AssertionError, match="duplicate mapping key"):
            _requested_version(smuggled)

    # (e) GitHub normalizes an input name to `INPUT_<NAME>` (upper-cased, spaces → underscores), so
    #     two keys differing only in case are ONE input to the action and TWO keys to PyYAML. Measured
    #     by review: this shape resolved to the pin, reported no duplicate key, and left
    #     `_violations` empty — a major-3 pin, green behind a capital letter.
    case_variant = workflow.replace(
        canonical_with_text,
        f"{canonical_with_text}\n          Version: 3.0.0",
        1,
    )
    assert "Version: 3.0.0" in case_variant, "mutation did not apply — this test would be vacuous"
    assert _structural_duplicate_keys(case_variant) == [], (
        "the case variant parsed as an exact duplicate key — the arm would pass for the wrong "
        "reason and never exercise the normalization"
    )
    with pytest.raises(AssertionError, match="INPUT_VERSION"):
        _requested_version(case_variant)


def test_a_legal_spelling_of_the_step_is_read_not_refused() -> None:
    """The literal-line reader must resolve the SAME step PyYAML does, in ANY legal spelling.

    ⛔ Measured in review against the line pattern this replaces: `uses : supabase/setup-cli@v1`
    (a space before the colon — legal YAML, and what PyYAML and GitHub both read as the same step)
    did not match, so a legitimate workflow was REFUSED with the message *"no `uses:
    supabase/setup-cli` line found — the migration door moved"*. That is a false block that also
    sends a maintainer hunting a diff which does not exist, and it contradicted the function's own
    stated contract. The reader now slices the PARSED node's marks, so the key's spelling is not a
    surface at all; this arm is the regression guard, and it asserts the pin is still READ — a
    refusal here would be a false block, not a guard.

    The reorder row is the sibling half of the same claim: `with:` may legally precede `uses:`, and
    nothing in the reader may assume an order.
    """
    workflow = WORKFLOW.read_text(encoding="utf-8")
    config = CONFIG.read_text(encoding="utf-8")
    pin = _requested_version(workflow)  # the live pin, so no mutation hardcodes a version

    # (a) `uses :` — a space before the colon.
    spaced = workflow.replace("- uses: supabase/setup-cli@v1", "- uses : supabase/setup-cli@v1", 1)
    assert "uses : supabase/setup-cli" in spaced, "mutation (a) did not apply — vacuous"
    assert _requested_version(spaced) == pin, "a legal `uses :` spelling was not read as the step"
    assert _literal_version_lines(spaced) == [pin], _literal_version_lines(spaced)
    assert not _violations(spaced, config), "a legal spelling produced a false violation"

    # (b) `with:` BEFORE `uses:` in the same step.
    reordered = workflow.replace(
        "      - uses: supabase/setup-cli@v1\n",
        f"      - with:\n          version: {pin}\n        uses: supabase/setup-cli@v1\n",
        1,
    )
    reordered = re.sub(
        r"(?m)^        with:\n          version: [^\s#]+\n", "", reordered, count=1
    )
    assert "- with:\n" in reordered and "uses: supabase/setup-cli@v1" in reordered, (
        "mutation (b) did not apply — vacuous"
    )
    assert _requested_version(reordered) == pin, "`with:` before `uses:` was not read as the step"
    assert not _violations(reordered, config), "the reordered step produced a false violation"

    # (c) the PIN's key, in every spelling both parsers read as the same plain key. The reader
    #     matched only the canonical `version:` and blamed "a construct (merge key, alias)" for the
    #     rest — a false block with a diagnosis the text contradicted (found in review, P2).
    canonical = f"        with:\n          version: {pin}"
    assert canonical in workflow, "the step's `with:` lost its canonical shape — update this arm"
    for respelled in (f"version : {pin}", f'"version": {pin}', f"'version': {pin}"):
        spelled = workflow.replace(canonical, f"        with:\n          {respelled}", 1)
        assert respelled in spelled, f"mutation did not apply for {respelled!r} — vacuous"
        assert _requested_version(spelled) == pin, (
            f"a legal `{respelled.split(':')[0]}` spelling was not read as the pin"
        )
        assert not _violations(spelled, config), "a legal spelling produced a false violation"


def test_an_alias_that_does_not_supply_the_pin_does_not_refuse() -> None:
    """An alias is RESOLVED by both parsers, so it is never itself a divergence — only its VALUE is.

    ⛔ Two corrections, both measured. First, the alias arm scanned for ANY `AliasToken` inside the
    step's line span, so `env: *shared` — an alias with nothing to do with `version`, beside an
    untouched literal pin — was refused. Second, and worse, it also refused `with: *pinned`, whose
    alias supplies the pin. That is not a divergence at all: GitHub's Actions reference documents
    anchors and aliases as supported workflow YAML ("This is equivalent to writing the following
    YAML without anchors and aliases", including entire-job reuse), so the step installs exactly the
    pin this reader resolves, and the refusal's explanation — the action "would see no `version`
    input and fall back to `latest`" — described a mechanism that does not exist.

    What remains a guard is the VALUE: (b2) shows an alias carrying a MAJOR-3 pin is still refused
    by the ceiling, so accepting the spelling costs no coverage on the fail-open side.
    """
    workflow = WORKFLOW.read_text(encoding="utf-8")
    pin = _requested_version(workflow)

    # (a) an `env:` supplied by an alias, pin untouched → ACCEPTED.
    # ⛔ The anchor MUST include the pin. `env_block` alone — the two lines `env:` + the token — is
    # NOT unique in this workflow: `check-migration-drift` carries the identical pair, and
    # `replace(..., 1)` rewrote THAT step. Measured: the alias landed at line 142, outside the
    # setup-cli step, so `_construct_refusals() == []` passed for a reason with nothing to do with
    # this claim — reverting the alias arm to the false block it documents left the suite GREEN.
    # Anchoring on `version: <pin>` + `env:` makes the substitution land where the arm claims, and
    # the `_setup_cli_block` assert below pins that down so it cannot silently drift again.
    env_anchor = (
        f"          version: {pin}\n"
        "        env:\n"
        "          SUPABASE_ACCESS_TOKEN: ${{ secrets.SUPABASE_ACCESS_TOKEN }}\n"
    )
    assert workflow.count(env_anchor) == 1, (
        "the setup-cli step's `with:`/`env:` is no longer a unique sequence — this mutation would "
        "land on a DIFFERENT step and prove nothing; update the anchor"
    )
    env_aliased = workflow.replace(
        env_anchor,
        f"          version: {pin}\n        env: *shared\n",
        1,
    )
    env_aliased = "env: &shared\n  SUPABASE_ACCESS_TOKEN: token\n" + env_aliased
    assert "        env: *shared" in env_aliased, "mutation (a) did not apply — vacuous"
    assert any("*shared" in line for line in _setup_cli_block(env_aliased)), (
        "mutation (a) landed on another step — this arm would prove nothing about the setup-cli "
        "step, which is the only step whose refusals it asserts"
    )
    assert _construct_refusals(env_aliased) == [], _construct_refusals(env_aliased)
    assert _requested_version(env_aliased) == pin, "an unrelated alias refused the guard"
    # ⛔ Assert the DETECTOR too (#11134 review, P3): since nothing refuses an alias any more,
    # "accepted" holds for every shape and cannot fail. `is False` is what a coarse detector — one
    # that reported any `AliasToken` in the step — would break, and that detector would silently
    # switch OFF the literal-line arm for a step whose pin is a plain literal.
    assert _alias_supplies_the_pin(env_aliased) is False, (
        "an alias on `env:` was read as supplying the pin — the detector is wider than its "
        "justification, and it would disable the literal-line arm for an unrelated alias"
    )

    # (a1) a step whose pin is a plain literal and which ALSO declares an env var literally named
    #      `version` → ACCEPTED, and only the PIN counts as a literal `version:` line.
    #      ⛔ Measured before this arm existed: `_literal_version_lines` scanned the whole step BLOCK,
    #      so this correctly pinned workflow was REFUSED with "carries 2 literal `version:` lines
    #      (…, 1.0.0) — a duplicate key's tie-break is not measured … Pin exactly once." That names a
    #      mechanism `_structural_duplicate_keys` reports as ABSENT and prescribes a remedy the file
    #      already satisfies — and it contradicted this file's own rule, quoted in
    #      `_alias_supplies_the_pin`: "A `version:` under `env:`/`secrets:` is a different key that
    #      happens to share a name." The reader now takes the pin's key node from the composed tree.
    env_same_name = workflow.replace(
        env_anchor,
        f"          version: {pin}\n        env:\n          version: 1.0.0\n",
        1,
    )
    assert "          version: 1.0.0" in env_same_name, "mutation (a1) did not apply — vacuous"
    assert _literal_version_lines(env_same_name) == [pin], (
        "an unrelated `env: version:` was counted as a second literal pin — "
        f"{_literal_version_lines(env_same_name)!r}"
    )
    assert _requested_version(env_same_name) == pin, (
        "a correctly pinned step that also sets an env var named `version` was refused"
    )
    assert _violations(env_same_name, CONFIG.read_text(encoding="utf-8")) == [], (
        "the guard refused a legal workflow over an env var that merely shares the pin's name"
    )

    # (a2) the same shape with the `env:` value supplied by an ALIAS → the detector is still FALSE.
    #      ⛔ This is the shape the narrowing was measured on, and it was asserted NOWHERE: reverting
    #      the structural detector to the previous textual-prefix-window implementation left the whole
    #      file GREEN, because the only `is False` shapes asserted (arm (a)'s `env: *shared`, arm (c)'s
    #      next-step `with:`) are not `version:`-VALUED keys, so both implementations agreed on them.
    #      A wide detector here switches OFF the literal-line arm for a step whose pin is a plain
    #      literal — the exact regression the narrowing was written for.
    aliased_env_value = "x-pin: &xpin 1.0.0\n" + workflow.replace(
        env_anchor,
        f"          version: {pin}\n        env:\n          version: *xpin\n",
        1,
    )
    assert "*xpin" in aliased_env_value, "mutation (a2) did not apply — vacuous"
    assert _alias_supplies_the_pin(aliased_env_value) is False, (
        "an ALIASED `version:` under `env:` was read as supplying the pin — the detector is wider "
        "than its justification, and it would disable the literal-line arm for a plain literal pin"
    )
    assert _requested_version(aliased_env_value) == pin, (
        "an aliased `env:` value refused a step whose pin is a plain literal"
    )

    # (b) the pin ITSELF supplied by an alias → ACCEPTED, and the RESOLVED value is what is checked.
    #     ⛔ This arm asserted a REFUSAL before the premise was corrected: GitHub's Actions reference
    #     documents anchors and aliases as supported workflow YAML ("This is equivalent to writing
    #     the following YAML without anchors and aliases", including entire-job reuse), so
    #     `with: *pinned` installs exactly the pin this reader resolves. Refusing it was a false
    #     block carrying the false explanation that the action "would see no `version` input".
    canonical = f"        with:\n          version: {pin}"
    aliased_pin = workflow.replace(
        "      - uses: supabase/setup-cli@v1\n",
        "      - uses: actions/checkout@v9\n"
        "        with: &pinned\n"
        f"          version: {pin}\n"
        "      - uses: supabase/setup-cli@v1\n",
        1,
    ).replace(canonical, "        with: *pinned", 1)
    assert "        with: *pinned" in aliased_pin, "mutation (b) did not apply — vacuous"
    assert _alias_supplies_the_pin(aliased_pin) is True, (
        "the detector missed an alias that supplies the step's whole `with:` — the literal-line arm "
        "would then fire on a legitimate spelling"
    )
    assert _requested_version(aliased_pin) == pin, "an alias-supplied pin was not read"
    assert _violations(aliased_pin, CONFIG.read_text(encoding="utf-8")) == [], (
        "a correctly pinned step written with an alias was refused — GitHub resolves aliases, so "
        "this is a false block"
    )

    # (b2) …and the VALUE is still guarded: an alias supplying a MAJOR-3 pin is refused by the
    #      ceiling, so accepting the spelling costs no coverage on the fail-open side.
    aliased_v3 = aliased_pin.replace(f"          version: {pin}\n", "          version: 3.0.0\n", 1)
    assert "version: 3.0.0" in aliased_v3, "mutation (b2) did not apply — vacuous"
    assert _requested_version(aliased_v3) == "3.0.0"
    problems = _violations(aliased_v3, CONFIG.read_text(encoding="utf-8"))
    assert problems and "major 3" in problems[0], problems

    # (c) an alias on the FOLLOWING step's own first key → ACCEPTED. The token scan's upper bound
    #     was `end_mark.line`, which is the line of the token AFTER this step — i.e. the next step's
    #     `- ` line — so an inclusive bound attributed the NEIGHBOUR's alias to this step and refused
    #     a legal workflow, naming a line this step does not own. Measured: a following step written
    #     `- with: *shared` produced "an alias (`*shared`) … at line 243" and `_requested_version`
    #     raised on a step carrying no construct at all. The step's own node span IS the bound, and
    #     `_alias_supplies_the_pin` compares against it exclusively; every reader must agree about
    #     where the step STOPS.
    after = workflow.replace(
        "      - name: Link project",
        "      - with: *shared\n        name: Link project",
        1,
    )
    assert "      - with: *shared" in after, "mutation (c) did not apply — vacuous"
    after = "x-shared: &shared\n  a: 1\n" + after
    assert _construct_refusals(after) == [], (
        "an alias on the NEXT step's first key was attributed to the setup-cli step — a false "
        f"block: {_construct_refusals(after)!r}"
    )
    assert _requested_version(after) == pin, "a construct on the following step refused the guard"
    assert _alias_supplies_the_pin(after) is False, (
        "an alias on the NEXT step's first key was read as supplying THIS step's pin — the bound "
        "regressed to a line range, so the two readers disagree about where the step stops"
    )
    assert _violations(after, CONFIG.read_text(encoding="utf-8")) == [], (
        "a legitimate workflow with a literal pin was refused at the GUARD level too, because the "
        "NEXT step's first key is an alias — the whole point is that this step carries no construct"
    )


def test_a_case_variant_second_step_cannot_pin_a_different_major() -> None:
    """GitHub resolves `owner/repo` case-INSENSITIVELY; the reader must too.

    Measured before this arm existed: inserting `- uses: Supabase/setup-cli@v1` with
    `version: 3.0.0` directly after the real pin left `_requested_version` resolving `2.122.1`,
    `len(steps) == 1` HOLDING (the case-sensitive filter never saw the second step), and
    `_violations()` returning `[]` — a major-3 CLI installed by the deploy job, GREEN. Measured
    against this box's own remote: `git ls-remote` returns the SAME HEAD for
    `supabase/setup-cli`, `Supabase/setup-cli` and `SUPABASE/SETUP-CLI`, so the action really is
    case-insensitive and the second step really is a second install.

    That is class (2) of the declared surface — "a pin this reader resolves differently from CI" —
    which is the class the `with:`-key normalization arm closes for input NAMES, left open for
    `uses:`. Both readers resolve through the one `_is_setup_cli_step` predicate, so neither can
    disagree with the other about how many steps there are.
    """
    workflow = WORKFLOW.read_text(encoding="utf-8")
    config = CONFIG.read_text("utf-8")
    # Derive the live pin, like the four sibling mutation arms already do. The workflow says the pin
    # is taken up BY HAND (`supabase-deploy.yml`: an `x.y.z` in a `with:` input is invisible to
    # Dependabot), so a hardcoded value here REDs a required suite on a bump that is correct and
    # expected — with a message claiming the MUTATION failed, which sends the reader after a diff
    # that does not exist (found in review, P2; the same defect this file already fixed elsewhere).
    pin = _requested_version(workflow)
    canonical = f"        with:\n          version: {pin}"
    assert canonical in workflow, (
        "the setup-cli step's `with:` no longer has the canonical shape these mutations drive — "
        "update them rather than silently proving nothing"
    )

    for spelling in ("Supabase/setup-cli@v1", "SUPABASE/SETUP-CLI@v1"):
        smuggled = workflow.replace(
            canonical,
            f"{canonical}\n      - uses: {spelling}\n        with:\n          version: 3.0.0",
            1,
        )
        assert spelling in smuggled, f"mutation did not apply for {spelling!r} — vacuous"
        # Each reader must refuse IN ITS OWN RIGHT — not merely be saved by the other one. A
        # single `_requested_version` assertion would pass even if only `_setup_cli_step_node`
        # grew the fix, leaving the comparison reader silently blind again.
        with pytest.raises(AssertionError, match="expected exactly one setup-cli step"):
            _setup_cli_step(smuggled)
        with pytest.raises(AssertionError, match="expected exactly one setup-cli step"):
            _setup_cli_step_node(smuggled)
        with pytest.raises(AssertionError, match="expected exactly one setup-cli step"):
            _requested_version(smuggled)
        # `_violations` reaches the same refusal through `_requested_version`, so the end-to-end
        # guard FAILS CLOSED (raises) rather than returning a violation — assert the refusal it
        # actually emits, so this arm cannot pass by asserting a shape the guard never produces.
        with pytest.raises(AssertionError, match="expected exactly one setup-cli step"):
            _violations(smuggled, config)

    # The boundary arm: a DIFFERENT action whose name merely STARTS with ours is not the step, so
    # widening the match for case-insensitivity must not swallow a fork/decoy into the count.
    decoy = workflow.replace(
        canonical,
        f"{canonical}\n      - uses: supabase/setup-cli-fork@v1\n        with:\n          version: 3.0.0",
        1,
    )
    assert "supabase/setup-cli-fork@v1" in decoy, "mutation did not apply — vacuous"
    assert _requested_version(decoy) == pin, (
        "an action whose name merely starts with `supabase/setup-cli` was counted as the step"
    )


def test_a_major_three_pin_is_refused_even_after_the_config_is_migrated() -> None:
    """This rename retires the config-load arm — the major-3 ceiling must survive it.

    Measured at the time — before this arm existed AND before `#11104` landed the consent flags: with
    the config migrated, mutating ONLY the pin to `3.0.0` made this suite report `9 passed, 1 skipped`
    — green — over a workflow that passed no `--yes`, i.e. over an applier that v3 consent-gates into
    applying zero migrations. That is the same blast radius as #10593 through a different string, so
    the ceiling is keyed on the MAJOR. (`--yes` has SINCE landed on `main`; the ceiling survives it
    deliberately — `_violations`'s own message carries the operative reason, and that reason is
    `#11100`'s ruling, not a missing flag. Read it there rather than here.)
    """
    workflow = WORKFLOW.read_text(encoding="utf-8")
    mutated = re.sub(r"(?m)^(\s*version:\s*).*$", r"\g<1>3.0.0", workflow, count=1)
    assert "version: 3.0.0" in mutated, "mutation did not apply — this test would be vacuous"
    # A PREMISE, deliberately not a "mutation" (#10896). The committed config IS migrated, so a
    # `re.sub` renaming `[inbucket]` matches nothing and its "mutation applied" guard passes on the
    # pre-existing state — a vacuous arm wearing a mutation guard's message (found in review). Stated
    # as a premise it is still load-bearing: if the rename is ever reverted this REDs, and the arm
    # that then applies is the sibling below (`test_the_coupling_moves_from_the_section_to_the_major`),
    # which builds its `[inbucket]` config from the header that really exists and asserts IT applied.
    migrated_config = CONFIG.read_text(encoding="utf-8")
    assert not _config_declares_inbucket(migrated_config), (
        "the committed config still declares [inbucket] — this arm asserts the major-3 ceiling "
        "against the MIGRATED tree; if the rename is reverted, this premise is the signal"
    )
    problems = _violations(mutated, migrated_config)
    assert problems, (
        "a major-3 pin passed against a migrated config — the v3 trap is unguarded, and a major "
        "bump would apply nothing behind HostedMutationConsentRequiredError"
    )
    assert "HostedMutationConsentRequiredError" in problems[0], problems[0]


def test_the_coupling_moves_from_the_section_to_the_major() -> None:
    """The coupling MOVES when `[inbucket]` leaves the config; it does not disappear.

    Two distinct refusals are asserted, so the guard cannot degrade into one blanket message that
    would be right for the wrong reason: an `[inbucket]` config fails on its SECTION (`DbConfigLoadError`),
    a migrated config fails on the MAJOR (`HostedMutationConsentRequiredError`). A v2 pin is
    accepted against either — the boundary is the major, not the section name.
    """
    workflow = WORKFLOW.read_text(encoding="utf-8")
    live_config = CONFIG.read_text(encoding="utf-8")
    inbucket_config = re.sub(r"(?m)^\[local_smtp\]\s*$", "[inbucket]", live_config)
    assert _config_declares_inbucket(inbucket_config), "the config mutation did not apply"
    mutated_workflow = re.sub(r"(?m)^(\s*version:\s*).*$", r"\g<1>3.0.0", workflow, count=1)
    assert "version: 3.0.0" in mutated_workflow, "mutation did not apply — this test would be vacuous"

    from_section = _violations(mutated_workflow, inbucket_config)
    assert from_section and "DbConfigLoadError" in from_section[0], from_section

    from_major = _violations(mutated_workflow, live_config)
    assert from_major and "HostedMutationConsentRequiredError" in from_major[0], from_major

    # The live tree is green: a v2 pin against the migrated config.
    assert not _violations(workflow, live_config)


@pytest.mark.parametrize(
    "header",
    ["[inbucket]", "[ inbucket ]", '["inbucket"]', "['inbucket']", "[  inbucket  ]", "\t[inbucket]"],
)
def test_a_major_three_pin_is_refused_over_every_toml_spelling_of_the_section(
    header: str,
) -> None:
    """TOML accepts the header in several spellings; the CLI rejects them all alike.

    Measured before this arm existed: the detector matched only `[inbucket]`, so a config spelling
    it `[ inbucket ]` or `["inbucket"]` — the SAME table to every TOML parser, the CLI's included —
    read as "no [inbucket] table", and a major-3 pin went GREEN over a config that still dies at
    load. That is the false PASS this file exists to prevent, reached through whitespace, and it is
    why the detector asks a parser instead of a line regex.
    """
    workflow = WORKFLOW.read_text(encoding="utf-8")
    mutated_config = re.sub(r"(?m)^\[local_smtp\]\s*$", header, CONFIG.read_text("utf-8"))
    assert header in mutated_config, "the config mutation did not apply — this test would be vacuous"
    assert _config_declares_inbucket(mutated_config), (
        f"`{header}` declares the `inbucket` table to TOML, so it must be detected"
    )
    mutated_workflow = re.sub(r"(?m)^(\s*version:\s*).*$", r"\g<1>3.0.0", workflow, count=1)
    assert "version: 3.0.0" in mutated_workflow, "mutation did not apply — this test would be vacuous"
    problems = _violations(mutated_workflow, mutated_config)
    assert problems and "DbConfigLoadError" in problems[0], problems


def test_the_dotted_declaration_of_the_section_is_detected() -> None:
    """`inbucket.enabled = true` declares the same table as a `[inbucket]` header."""
    dotted = "inbucket.enabled = true\ninbucket.port = 54324\n"
    assert "inbucket" in tomllib.loads(dotted), "the premise of this test is wrong"
    assert _config_declares_inbucket(dotted)


def test_an_unparseable_config_refuses_a_major_three_pin() -> None:
    """FAIL CLOSED: a config this reader cannot parse must not read as "no [inbucket] table".

    A detector that returns False on a parse error turns an unreadable config into permission for a
    major-3 pin — the same false PASS through a third door. `db push` cannot load an unparseable
    config either, so refusing the pin is the correct verdict, and it is asserted end to end.
    """
    workflow = WORKFLOW.read_text(encoding="utf-8")
    unparseable = "[this is not toml\n"
    with pytest.raises(tomllib.TOMLDecodeError):
        tomllib.loads(unparseable)
    assert _config_declares_inbucket(unparseable)
    mutated_workflow = re.sub(r"(?m)^(\s*version:\s*).*$", r"\g<1>3.0.0", workflow, count=1)
    assert "version: 3.0.0" in mutated_workflow, "mutation did not apply — this test would be vacuous"
    assert _violations(mutated_workflow, unparseable), (
        "an unparseable config let a major-3 pin through"
    )
