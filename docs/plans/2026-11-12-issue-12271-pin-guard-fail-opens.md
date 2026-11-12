<!-- research-path: the `research: v1` comment on issue #12271 (comment id 6837561208) -->
<!-- plan-review: cycles=2, status=threat-surface-covered, version=2.3.0 -->

# #12271 — the #12051 pin guard's two/three undeclared fail-open spellings

**Issue:** daniel-ospina/tortoise#12271 · **Class:** gate-enforcement (adversarial domain) · **Tier:** standard

## Confirmed problem

The `#12051` pin guard is a hermetic text model of shell semantics. Three of its
own lexical rules are narrower than the shell's, so a spelling the shell accepts is
read as something else — and because this guard's verdict IS the check, each gap is a
**false PASS on a CI guard** (category A), not a false block:

1. `_is_last_path_token` skips a `find`/`xargs` terminator by comparing the token
   **verbatim** (`tests/test_12051_ci_pg_client_matches_server.py:1677` at `9828250732`).
   `';'` and `";"` therefore become OPERANDS, the destination stops being "last", and
   the complete replacement goes GREEN. POSIX says those characters "must be quoted",
   so the guard was blind to the standard-correct spelling.
2. `_run_commands` truncated at the first whitespace-then-`#` with **no quote
   awareness** (`:1013`). A line that merely PRINTED a literal `#` lost everything
   after it — a live `rsync` on the same line never reached any rule. Its own STATED
   LIMIT (`:1008`) claimed this case "reds rather than silently passing"; measurement
   said the opposite, so the documentation was part of the defect.
3. `_strip_trailing_comment` implemented the **narrow** comment rule (whitespace only,
   `:6421`) while the same file had already replaced that rule with the wide one in a
   sibling function (`COMMENT_START_RE`, `:2764`, `#12069 review 16`). A tombstone
   written `;#`, `&&#` or `|#` — all valid shell — survived into the scan.

4. **`)$(` is not an operator boundary.** (Found in PLAN-VERIFY round 1 — P1, measured,
   on the declared surface.) The first cut put `(`/`)` in a flat
   character class, so `echo $(date)#12051; <live replacement>` lost everything after
   the `#` and the replacement was GREEN. Bash ground truth:
   `echo $(date +%s)#note ; echo AFTER` prints `<epoch>#note` **and** `AFTER` — the
   `#` is part of the same word. A **group-closing** `)` *is* an operator
   (`(echo x)#note ; echo AFTER` prints only `x`). The two are indistinguishable in a
   flat class; closers are now tracked on a stack.
5. **`find`'s expression tokens are not operands.** (Found in PLAN-VERIFY round 1 — P1,
   measured, on the declared surface.) Skipping only the terminator
   itself left `\)`, `\(`, `!` and `-o` free to become "the last path", so
   `find … -exec cp {} "$b/16/bin/" ';' \)` went GREEN — on the **escaped** spelling
   too. Fixed by ending the operand scan at the terminator (nothing after it can be
   the destination of the exec'd verb).

**Falsification check** (single falsifier, carried from the research block): if a
quoted `;` after `find … -exec cp {} "$b/16/bin/"` already left the guard RED, fix 1
would add rows without closing anything. Measured: GREEN → RED.

⚠️ **The fixes are COUPLED, and the per-fix claim is stated as measured, not as
isolation** (corrected after PLAN-VERIFY round 2 measured it): reverting the unquote
reds `mut-108/109/111/113` (the T5 rows also carry a quoted terminator, so they depend
on it too); reverting the per-primary model reds `mut-115/116`; reverting the quoted
-terminator rule in `_is_command_position` reds `mut-116`; reverting each
`_strip_trailing_comment` mechanism reds exactly its own unit test. **Confidence: 95.**

## Alternatives considered

- **A — widen each rule at the point it reads (chosen).** Unquote the terminator
  compare; route `_run_commands` through the existing quote-aware stripper; share one
  `COMMENT_START_CHARS` between the two comment rules. Smallest surface, each fix
  maps to a measured bypass, and fix 3 removes a *drift* class rather than one call
  site.
- **B — build a real shell lexer (rejected).** Correct in principle, but this guard is
  deliberately hermetic (no shell, no DB, no network) and its stated residuals are a
  long, reviewed list; replacing the model mid-fix would invalidate 40+ rounds of
  pinned rows and blow the change's blast radius. Better as its own issue if the
  residual list ever grows without bound.
- **C — delete the text guard and rely on the in-job `pg_dump --version` execution arm
  (rejected).** That arm is exactly what #12051 proved insufficient: it runs in the PIN
  step and is blind to a later `$GITHUB_PATH` append. Research confirmed no third-party
  workflow linter models PATH resolution (actionlint validates syntax/expressions, not
  which `pg_dump` a later step resolves).
- **D — normalise the whole line before matching (rejected).** Blanking quotes/operators
  destroys the offsets the arms need for attribution (each finding must name a step
  index and a line); the codebase's own `_mask_quotes` keeps length for this reason.

## Integration Surface Map

| Surface | Change | Test layer |
|---|---|---|
| `_is_last_path_token` | terminator compare unquotes; each `-exec` primary's own last operand decides | unit + polarity table (`mut-108/109/111/112/113/115/116`) |
| `_is_command_position` | a quoted/escaped terminator ends the walk-back | polarity table (`mut-116`) |
| `_EXEC_PRIMARY_WORDS` | `-exec`/`-execdir`/`-ok`/`-okdir` open an operand list | polarity table (`mut-115/116/117`) |
| `_run_commands` | quote-aware + operator-aware comment strip | unit (`test_a_quoted_hash_…`) + all line-unit arms |
| `_strip_trailing_comment` | word-boundary scanner: quotes, escapes, substitution/assignment/group `)`, shares `COMMENT_START_CHARS` | unit (`test_a_substitution_closer_…`, `test_an_escaped_or_assignment_…`) + fail-closed message scan |
| `_without_comment` | now DELEGATES to `_strip_trailing_comment` (one rule, not two) | graded by `test_without_comment_is_the_same_rule_as_the_stripper` (M18) — the row arms do NOT grade this, they never changed |
| **row arms over the real workflow** | **`_run_commands` output changes on 18 of 85 `run:` steps of `python-ci.yml`** (measured by diffing main vs this change over every step) — **all restorations, no step is shorter**, and the guarded job's own PIN step is one of them | full module (383 tests) |

## Wiring

| Touch point | Type | Covered by | Status |
|---|---|---|---|
| `_run_commands()` — a single definition site; every caller (row arms, `_github_path_writes`, `_executed_lines`, pin-step reads) inherits the corrected text | in-file function | single `def`; the full module exercises the callers | ✅ |
| `_is_command_position()` — `_owning_verb`'s walk-back, i.e. whether a line's verb is graded at all | in-file function | `mut-116` (quoted two-`-exec`) | ✅ |
| `_strip_trailing_comment()` — `_run_commands` and the fail-closed message scan | in-file function | both asserted by the 383-test suite | ✅ |
| `_without_comment()` — `_compared_variables` comparison path | in-file function → now delegates | graded by `test_without_comment_is_the_same_rule_as_the_stripper` (M18) — the row arms do NOT grade this, they never changed | ✅ |
| `_replaces_a_client_binary()` — row arm, polarity table, wiring tests | in-file function | `mut-108/109/111/112/113` + wiring test | ✅ |
| CI consumer — `python-ci.yml` `test (d)` runs this module | external workflow | module green locally; workflow untouched | ✅ |

## Implementation steps (each with the test that proves it)

1. Terminator compare → `text.strip("\"'") in {"+", "\\;", ";"}`, plus a CONTROL row
   that a quoted terminator on a READ stays GREEN. **Test:** `mut-108`, `mut-109`, `mut-110`.
2. `_run_commands` → `_strip_trailing_comment(line)`; rewrite the false STATED LIMIT.
   **Test:** `test_a_quoted_hash_does_not_erase_the_live_command_after_it` (4 quoted
   forms surviving + refusal of the replacement they precede).
3. `COMMENT_START_CHARS` shared by the comment rules; `_without_comment` delegates.
   **Test:** the same test's 7 operator-opener tombstone cases.
4. A substitution closer is not a comment boundary; a group closer is.
   **Test:** `test_a_substitution_closer_is_not_a_comment_boundary` (4 shapes refused,
   plus a group-closer CONTROL that must strip).
5. The operand scan ENDS at a `find`/`xargs` terminator. **Test:** `mut-111`, `mut-112`,
   `mut-113` + the grouped-READ control `mut-114`.
6. Mutation proof: revert each fix alone and show exactly its own rows red.

## Review Scope Contract

**Class:** gate-enforcement

**Risk surface** — what this change can break, each with the check that would catch it:

- **R1** A quoted terminator still becomes an operand → replacement GREEN (fail-open). → caught by `mut-108`/`mut-109` + mutation proof.
- **R2** A quoted `#` still erases the live command after it → every rule blind on that line. → caught by `test_a_quoted_hash_does_not_erase_the_live_command_after_it`.
- **R3** A tombstone after an operator (`;#`, `&&#`, `|#`) survives → a commented-out act satisfies a rule. → caught by the same test's operator cases.
- **R4** The fix OVER-refuses: blanket-refusing every quoted `;` turns reads RED. → caught by `mut-110`/`mut-114` (reads with a quoted terminator stay GREEN) and the 383-test suite.
- **R5a** **over-strip erases a live command (fail-open)** — the direction this change can genuinely move: a `)` or a token after a terminator read as inert. → caught by `test_a_substitution_closer_is_not_a_comment_boundary`, `test_an_escaped_or_assignment_closer_does_not_start_a_comment` and `mut-111/112/113`, each of which goes red if its mechanism is reverted (measured).
- **R5b** false REDs in the fail-closed message scan, because the comment rule now reaches more arms. → caught by the full module (`383 passed`) + `ruff check`.
- **R6** the find TERMINATOR model applied outside a `find`/`xargs` command → `cp -a src + "$b/16/bin/"` stops being read and the replacement goes GREEN (this was a RED→GREEN regression the first cut introduced; found by the PR review, P0). → caught by `mut-118`/`mut-119` + the CONTROL `mut-120`; reverting the `primary and` gate reds exactly those three.
- **R11** the `(` OPENER itself set the comment boundary unconditionally — so a `#` after an extglob opener or the second `(` of a glued `((` was read as a comment and the line was truncated AT the `(` (`echo hi @(#X); cp -a "$b/17/bin/." "$b/16/bin/"` and `true || ((#X)); …` went RED→GREEN; found by the PR review round 4, **P0**). → `boundary` is now derived from the FRAME (`word`/`arith` openers set False, `sub`/`group`/`=` set True); reverting it (`M17`) reds exactly `test_a_substitution_closer_is_not_a_comment_boundary`.
- **R12** `_without_comment`'s delegation to the stripper was **ungraded** — re-implementing it as the deleted flat-class regex left the suite fully green (found by the PR review round 4, P3). → `test_without_comment_is_the_same_rule_as_the_stripper` pins both directions; the revert (`M18`) reds exactly it.
- **R9** a `(` INSIDE A WORD PART opens a GROUP → a `#` that bash keeps as DATA inside `${…}`, or inside an extglob pattern, becomes a comment that erases the live line (`: ${x:-(a)#}; cp -a "$src/." "$b/16/bin/"` and `echo @(b)#x; cp -a …` went RED→GREEN in 8 of 17 spellings; found by the PR review round 3, **P0**). → `${` now pushes a `word` frame closed by `}`, and a `(` after `?*+@!` is a word part; caught by the `case`/`${%}`/`${…}`/`@(` openers in `test_a_substitution_closer_is_not_a_comment_boundary`, and reverting either the brace frame (`M13`) or the extglob rule (`M15`) reds exactly that test.
- **R10** the round-2 double-quote escape fix shipped with NO pinning row: reverting it left the suite fully green while the fail-open returned (found by the PR review round 3, P2). → the escaped-quote shape is now an opener in `test_an_escaped_or_assignment_closer_does_not_start_a_comment`; reverting the pair (`M14`) reds exactly that test.
- **R8** a `)` that bash does NOT read as a paren closer — a `case` pattern terminator (`case a in a)`), a `${x%)}` modifier, or a stray closer — POPS a substitution frame, so the substitution's real `)` closes nothing and the following `#` is read as a comment that erases the live line (`echo $(case a in a) echo hi;; esac)#note; cp -a src/. "$b/16/bin/"` went RED→GREEN; found by the PR review round 2, P0). → a `)` now closes only a **confirmed group** frame and every other `)` is not a boundary; caught by `test_a_substitution_closer_is_not_a_comment_boundary`'s `case`/`${%}` openers, and reverting the gate to `if stack: stack.pop()` reds exactly `test_a_substitution_closer_is_not_a_comment_boundary` for the mutant this lane measured (`boundary = (stack.pop() if stack else "group") == "group"`); the PR review's variant (`if stack: stack.pop()`, no `else` rewrite) reds that test **and** `test_an_escaped_or_assignment_closer_does_not_start_a_comment`, because the `else: boundary = False` branch is shared — so the claim is mutant-specific, not a property of the test file.
- **R7** a quoted/escaped `;` counted as a list separator in ANY command → `echo ';' chmod -R 755 "$b/16/bin"` becomes a false RED, and `cp -a src ';' "$b/16/bin/"` a false GREEN (found by the PR review, P1). → caught by `mut-119`/`mut-120`; the rule is now gated on the segment's own head word (`_in_find_segment`).
- **R5c** applied to the REAL workflow: `_run_commands` now restores 18 of 85 `run:` step bodies (never truncates one). → caught by diffing `_run_commands` over every step of `python-ci.yml` on main vs this change; the full module re-asserts every row arm over the real job.

**Threat classes** (gate-enforcement):

- `T1` quoted `find`/`xargs` terminator (`';'`, `";"`) → must be REFUSED when the destination is the client dir.
- `T2` quoted `#` (**either** quote style) introducing a live command on the same line → the command must be REFUSED.
- `T3` comment after an operator (`;#`, `&&#`, `|#`) → the tombstone must NOT satisfy any rule.
- `T4` substitution closer (`$(…)`, `$((…))`, `<(…)`, `>(…)`) followed by `#` → the word does NOT end, so the rest of the line must stay live; a **group** closer followed by `#` DOES end it and must be stripped. **Includes a `)` bash does not read as a closer**: a `case` pattern terminator and a `${x%)}` modifier are word parts, so they must not pop the enclosing frame (R8). Evidence: the `case`/`${%}` openers in `test_a_substitution_closer_is_not_a_comment_boundary` and mutation `M12`.
- `T5` `find` expression tokens (`\)`, `\(`, `!`, `-o …`) after a terminator → must not become "the last path"; and a **second** `-exec` primary's destination must still be read.
- `T6` escaped character or assignment-value `)` before `#` → must NOT end the word (added after round 2, measured).

**Out of scope — must NOT be raised** (note, file, do not chase): the guard's pre-existing stated residuals (extractor destinations, `sh -c` inner verbs, heredoc bodies, a separately-quoted base); `ruff format` drift that already exists on `main`; anything in the workflow YAML itself. **Backtick spans, ANSI-C `$'…'` strings, a terminator held in a variable, a `\;` token in a NON-find command, and a backslash-continued trailing comment are measured fail-opens on this surface and are FILED as #12276** — not chased here. **#12286** is the other measured residual: a `find … -exec` replacement inside `$( … )` whose OUTER word is `echo`/`printf`/`cat` is GREEN because `_is_command_position`'s walk-back crosses the `$(` and reads the outer operand-taking word — a different mechanism from every class above (nothing is erased, and the terminator IS read), measured on `main` too, filed rather than chased.

**Acceptance:** every `R<n>` has its named evidence, and no open item remains on the declared surface.

## Rounds

**Rounds:** 2 (verifiers: 1 per round, class: gate-enforcement) — **disclosure:** the PLAN-VERIFY round was dispatched AFTER the build in this lane (a continuation lane whose defect was already measured and filed); it therefore reviewed plan + built diff together, which is wider, not narrower, than the pre-code round. Round 1 returned 2 P1s (both reproduced, both fixed) plus 2 P2s and 3 P4s; round 2, a FRESH verifier over the revised whole, returned **1 P1 + 1 P2 + 1 P3 + 2 P4** — the P1 being a regression round 1's `break` introduced (`mut-115/116`: a SECOND `-exec` primary's destination stopped being read) and the P2 another over-strip (escaped and assignment-value closers). All five were reproduced here, then fixed and mutation-proved. **A separate CODE review panel then ran at the pushed head and returned 3 more findings — a second RED→GREEN regression (R8, the `case`/`${%}` `)` popping a frame), a false `<`/`>` shell claim (P3), and this document's own `covered=6` claim (P2, falsified by R8) — all reproduced, fixed and mutation-proved before the claim below was allowed to stand.** Its round 3, over the next head, returned 1 **P0** (a `(` inside `${…}`/extglob opening a group — R9), 1 **P2** (the double-quote escape fix had no pinning row — R10) and 2 P3s (this document's own mutant-specific isolation claim, and a docstring opening sentence that still stated the superseded narrow rule); all four were reproduced, fixed, and re-proved by mutation (`M12`–`M16`), and the two rows the review showed were in the wrong table (they called `_replaces_a_client_binary` directly, so they never exercised the comment stripper) were moved into the tests that drive `_run_commands`. Its round 4, over the next head, returned 1 **P0** (the `(` opener setting the boundary unconditionally — R11), 1 **P3** (`_without_comment`'s delegation ungraded — R12), and 1 **P2** it correctly declined to chase: a pre-existing fail-open where a `<`-redirect target is read as the last operand, now filed as **#12290**.
**A further review round on this head (the lane reviewing PR #12279) found and closed one more T1 spelling and one more over-strip:** a quoted/escaped terminator GLUED to a closing operator (`';')`, `\;)`, `` ';'` ``, `(find … ';')`) — which is how a `find` inside `$( … )` is actually written — was read as an operand, so the destination stopped being last and the complete replacement went GREEN; `_bare_terminator` (ONE rule, asked by both readers) drops the glued operator and then unquotes exactly ONE MATCHED PAIR, because `.strip("'\"")` collapsed the OPERAND `'";"'` to a terminator and spared the destination. Six polarity rows + a mutation proof. That round's own first cut of the `<`/`>` doc fix put the two characters into the FLAT class and was **falsified by a fresh reviewer**: `a=1; (( a >#0 )); cp -a …` and `echo ${x:->#y}; cp -a …` keep the `#` as DATA and bash runs the replacement, where `main` and the pushed head both refuse — so the characters stay OUT and `test_an_arithmetic_or_parameter_redirect_is_not_a_comment_boundary` now pins that direction (re-adding them reds it).
**`[ADVERSARIAL-BOUND] cycles=2 threats=6 covered=6 residuals=#12276,#12286,#12290`** — the exit is threat-surface coverage at the adversarial bound, not an unbounded clean, and the residual carriages (backtick spans, ANSI-C strings, a terminator in a variable, a `\;` token in a non-find command) are filed as #12276 (and #12290 for the `<`-redirect operand) rather than chased.
