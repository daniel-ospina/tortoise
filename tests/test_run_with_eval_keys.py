"""Hermetic tests for tools/run-with-eval-keys.sh (#2718, incident #4860).

The defect these tests pin: eval/measurement entry points read provider keys
from the process env and do NOT load the repo `.env` — the repo's only `.env`
loader (``mcp_server._load_dotenv``) is not imported by them, and where it does
run it only fills keys that are ABSENT, never overriding an ambient var. So the
key a run bills is whatever the calling shell exported. On 2026-09-23 the sealed
#2552 write-path run silently billed the ambient fleet OpenRouter key
(``limit_remaining=0``) and returned HTTP 403 on all 7 sessions while a healthy
evals key sat in `.env` — and nothing in the run output said which key had been
used.

The wrapper makes the key source explicit:
  * it STRIPS the ambient provider keys it owns,
  * loads the repo-root `.env` with explicit override for those keys,
  * emits a source + fingerprint line per managed key,
  * ``exec``s the command.

No network, no Docker, no FalkorDB, and no real key material: every fixture is
read from a temp `.env`, so the wrapper never reads the developer's live `.env`
and never prints a real value.

Run standalone:      python3 tests/test_run_with_eval_keys.py
Run under pytest:    TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_run_with_eval_keys.py -q
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "tools" / "run-with-eval-keys.sh"

# The contract: the provider keys the evaluation/measurement paths actually
# read (tortoise/ingest.py::_PROVIDERS, tortoise/analyze.py::_LLM_PROVIDERS,
# tortoise/model_adapters.py). ANTHROPIC_API_KEY is deliberately NOT here —
# no tortoise provider reads it (hosted_api.py::_llm_provider_keys).
# Pinned against those registries by
# test_managed_keys_match_the_code_registries below, so a new provider cannot
# silently escape the launcher.
MANAGED = (
    "OPENROUTER_API_KEY",
    "DEEPSEEK_API_KEY",
    "VENICE_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
)

# Obviously-fake fixture key values (never real material).
FIXTURE_OPENROUTER = "sk-or-v1-FIXTUREevalkey0123456789abcdefghijklmnopqrstuvwxyz"
FIXTURE_DEEPSEEK = "sk-fixture-deepseek-000111222333444555666777888999"
SABOTAGE = "sk-or-v1-DEADBEEF" + "0" * 40

ENV_FIXTURE = f"""\
# a comment line — skipped
export OPENROUTER_API_KEY="{FIXTURE_OPENROUTER}"
DEEPSEEK_API_KEY={FIXTURE_DEEPSEEK}
# VENICE_API_KEY intentionally left unset (fail-closed test)
TORTOISE_DB_URI=docker://:falkordb@localhost:6379/from-env-file
EVALTEST_EXPORTED=exported-value
EVALTEST_QUOTED="quoted value with spaces"
EVALTEST_SINGLE='single quoted'
EVALTEST_INLINE=value-with-inline # trailing comment
EVALTEST_HASH_IN_VALUE=abc#notacomment
EVALTEST_PADDED = padded-value
EVALTEST_OK=1
"""


def parse_managed_keys(script: Path) -> set[str]:
    """Extract the wrapper's declared managed-key array.

    Comments inside the array are stripped first: an uppercase word in a
    comment (e.g. ``# OPENROUTER is the evals key``) is not a declared key and
    must not be read as one.
    """
    text = script.read_text(encoding="utf-8")
    match = re.search(r"_RWEK_MANAGED_KEYS=\(\s*(.*?)\s*\)", text, re.DOTALL)
    if not match:
        raise AssertionError(f"_RWEK_MANAGED_KEYS block not found in {script}")
    body = re.sub(r"^\s*#.*$", "", match.group(1), flags=re.MULTILINE)
    return set(re.findall(r"^\s*([A-Z][A-Z0-9_]*)\s*$", body, re.MULTILINE))


class RunWithEvalKeysTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="eval-keys-test-")
        self.addCleanup(self._tmp.cleanup)
        self.env_file = Path(self._tmp.name) / "fixture.env"
        self.env_file.write_text(ENV_FIXTURE, encoding="utf-8")

    # ── helpers ────────────────────────────────────────────────────────

    def base_env(self, **extra: str) -> dict[str, str]:
        """A HERMETIC env: PATH only, plus any explicit override.

        Deliberately does not inherit ``os.environ``: CI runs pytest with
        ``TORTOISE_DB_URI`` exported (the docker lane), and inheriting it made
        ``test_non_managed_var_is_filled_when_absent`` fail there.
        """
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        env.update(extra)
        return env

    def run_wrapper(
        self,
        args: list[str],
        *,
        env: dict[str, str] | None = None,
        use_fixture: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        env = dict(env if env is not None else self.base_env())
        if use_fixture:
            env["EVAL_KEYS_ENV_FILE"] = str(self.env_file)
        return subprocess.run(
            [str(WRAPPER), *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
            check=False,
        )

    # ── the wrapper exists and is runnable ─────────────────────────────

    def test_wrapper_exists_and_is_executable(self):
        self.assertTrue(WRAPPER.is_file(), f"missing {WRAPPER}")
        mode = WRAPPER.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR, "wrapper is not executable")

    def test_managed_keys_match_the_code_registries(self):
        """A provider key added to the code must not silently escape the wrapper.

        Without this pin, adding a provider to ``tortoise.ingest._PROVIDERS`` or
        ``tortoise.analyze._LLM_PROVIDERS`` would leave its ambient key un-stripped
        (the #4860 failure mode for the new provider) with a green suite.
        ``tortoise/model_adapters.py`` is covered too — both its
        ``_PROVIDER_KEY_ENV`` registry AND every ``key_env = "…"`` class
        attribute declared anywhere in the ``tortoise`` package, so a new
        adapter cannot escape either (VeniceModel is the precedent: it lives
        only here, not in ``_PROVIDERS``).
        """
        declared = parse_managed_keys(WRAPPER)
        self.assertEqual(declared, set(MANAGED))
        # Import THIS repo's package, not whatever a `tortoise` distribution put
        # in site-packages: when the file is run standalone, `sys.path[0]` is
        # `tests/`, so an installed `tortoise` shadows it — and the guard then
        # skipped itself, a drift guard failing OPEN. Pin the path, and fail if
        # the registries cannot be read at all: reddening when a provider
        # escapes the launcher is this test's whole job.
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        try:
            from tortoise.analyze import _LLM_PROVIDERS
            from tortoise.ingest import _PROVIDERS
            from tortoise.model_adapters import _PROVIDER_KEY_ENV
        except Exception as exc:
            self.fail(f"cannot read the registries from {ROOT}: {exc!r}")
        derived = {key for _url, key in _PROVIDERS.values() if key}
        derived |= set(_LLM_PROVIDERS)
        derived |= set(_PROVIDER_KEY_ENV.values())
        # every adapter class attribute in the package — recursive, so a new
        # adapter in a subpackage (not just tortoise/*.py) still reddens this
        adapters_src = "\n".join(
            p.read_text(encoding="utf-8")
            for p in sorted((ROOT / "tortoise").rglob("*.py"))
        )
        derived |= set(
            re.findall(
                r'^\s*key_env\s*=\s*"([A-Z][A-Z0-9_]*)"',
                adapters_src,
                re.MULTILINE,
            )
        )
        self.assertEqual(
            derived,
            declared,
            "the wrapper's _RWEK_MANAGED_KEYS drifted from the provider keys "
            "the code reads — manage the new key in tools/run-with-eval-keys.sh",
        )

    def test_usage_error_without_command(self):
        r = self.run_wrapper([])
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("usage:", r.stderr)

    # ── the core defect: ambient must not beat .env ────────────────────

    def test_env_value_beats_sabotaged_ambient_key(self):
        env = self.base_env(OPENROUTER_API_KEY=SABOTAGE)
        r = self.run_wrapper(["sh", "-c", 'printf "%s" "$OPENROUTER_API_KEY"'], env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, FIXTURE_OPENROUTER)
        # the sabotaged ambient value must not survive anywhere
        self.assertNotIn("DEADBEEF", r.stdout)
        self.assertNotIn("DEADBEEF", r.stderr)

    def test_deepseek_env_value_beats_sabotaged_ambient_key(self):
        env = self.base_env(DEEPSEEK_API_KEY="sk-ambient-DEADBEEF")
        r = self.run_wrapper(["sh", "-c", 'printf "%s" "$DEEPSEEK_API_KEY"'], env=env)
        self.assertEqual(r.stdout, FIXTURE_DEEPSEEK)

    def test_managed_key_absent_from_env_is_unset_fail_closed(self):
        # Ambient VENICE key exists; .env has no VENICE key. Fail closed: the
        # ambient (wrong) key must NOT be silently used, and the declaration
        # line must say so.
        env = self.base_env(VENICE_API_KEY="VENICE-ambient-DEADBEEF")
        r = self.run_wrapper(
            ["sh", "-c", "printenv VENICE_API_KEY || echo UNSET"], env=env
        )
        self.assertEqual(r.stdout.strip(), "UNSET")
        self.assertIn("VENICE_API_KEY source=unset fingerprint=none", r.stderr)

    # ── the point of the change: a knowable, non-leaking key source ────

    def test_fingerprint_line_emitted_and_hides_the_key(self):
        env = self.base_env(OPENROUTER_API_KEY=SABOTAGE)
        r = self.run_wrapper(["true"], env=env)
        self.assertEqual(r.returncode, 0, r.stderr)

        # the label names the file actually read (resolved — the wrapper uses
        # `pwd -P`, so a symlinked temp dir reports its physical path)
        resolved_env = Path(self.env_file).resolve()
        self.assertIn(
            f"OPENROUTER_API_KEY source={resolved_env} fingerprint=", r.stderr
        )
        self.assertIn(f"DEEPSEEK_API_KEY source={resolved_env} fingerprint=", r.stderr)

        # never the full key — on either stream
        self.assertNotIn(FIXTURE_OPENROUTER, r.stderr)
        self.assertNotIn(FIXTURE_OPENROUTER, r.stdout)
        self.assertNotIn(FIXTURE_DEEPSEEK, r.stderr)

        # the marker carries the length + a stable sha256 prefix, so a run's
        # key is identifiable across receipts
        digest = hashlib.sha256(FIXTURE_OPENROUTER.encode()).hexdigest()[:12]
        self.assertIn(f"sha256={digest}", r.stderr)
        self.assertIn(f"len={len(FIXTURE_OPENROUTER)}", r.stderr)
        # only the (non-secret) first 6 characters are shown
        self.assertIn(FIXTURE_OPENROUTER[:6], r.stderr)

    def test_fingerprint_is_stable_and_distinguishes_keys(self):
        r1 = self.run_wrapper(["true"])
        r2 = self.run_wrapper(["true"])
        line = "OPENROUTER_API_KEY source="
        first = next(s for s in r1.stderr.splitlines() if line in s)
        second = next(s for s in r2.stderr.splitlines() if line in s)
        self.assertEqual(first, second, "fingerprint is not stable across runs")
        # the two fixture keys must not share a fingerprint
        ds = next(s for s in r1.stderr.splitlines() if "DEEPSEEK_API_KEY source" in s)
        self.assertNotEqual(first, ds)

    def test_short_value_is_fully_redacted_including_its_hash(self):
        # A short, low-entropy value must not be recoverable from a receipt:
        # `len` plus a deterministic sha256 prefix is a brute-force oracle for
        # the characters the fingerprint does not print. The boundary is 20 — at
        # 12 only 6 characters are hidden, which is ~5.7e10 candidates against a
        # 48-bit filter (hours of GPU).
        for value in ("abc123", ("abc123" * 4)[:19]):
            with self.subTest(value=value):
                env_file = Path(self._tmp.name) / f"short-{len(value)}.env"
                env_file.write_text(
                    f"OPENROUTER_API_KEY={value}\n", encoding="utf-8"
                )
                r = self.run_wrapper(
                    ["true"],
                    env=self.base_env(EVAL_KEYS_ENV_FILE=str(env_file)),
                    use_fixture=False,
                )
                self.assertEqual(r.returncode, 0, r.stderr)
                line = next(
                    s
                    for s in r.stderr.splitlines()
                    if "OPENROUTER_API_KEY source=" in s
                )
                self.assertIn("fingerprint=<redacted>", line)
                self.assertIn("sha256=redacted", line)
                self.assertNotIn(value, r.stderr)
                self.assertNotIn(
                    hashlib.sha256(value.encode()).hexdigest()[:12], r.stderr
                )

    def test_twenty_char_value_is_fingerprinted(self):
        # The boundary is inclusive and must not creep: at 20 characters 14 stay
        # hidden (~1.2e25), which is not brute-forceable, so the key stays
        # identifiable across receipts as intended.
        value = ("abc123" * 4)[:20]
        env_file = Path(self._tmp.name) / "boundary.env"
        env_file.write_text(f"OPENROUTER_API_KEY={value}\n", encoding="utf-8")
        r = self.run_wrapper(
            ["true"],
            env=self.base_env(EVAL_KEYS_ENV_FILE=str(env_file)),
            use_fixture=False,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        line = next(
            s for s in r.stderr.splitlines() if "OPENROUTER_API_KEY source=" in s
        )
        self.assertIn("fingerprint=abc123", line)
        self.assertIn("len=20", line)
        self.assertIn(hashlib.sha256(value.encode()).hexdigest()[:12], line)
        self.assertNotIn(value, r.stderr)

    def test_inherited_xtrace_does_not_leak_the_key(self):
        # `SHELLOPTS=xtrace` in the caller's env makes bash trace the loader's
        # `export "$key=$value"` line — dumping the full key into stderr, the
        # exact channel the receipt keeps clean.
        env = self.base_env(OPENROUTER_API_KEY=SABOTAGE)
        env["SHELLOPTS"] = "xtrace"
        r = self.run_wrapper(["true"], env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(FIXTURE_OPENROUTER, r.stderr)
        self.assertNotIn(FIXTURE_OPENROUTER, r.stdout)

    # ── the deliberate never-override for everything else ──────────────

    def test_non_managed_var_is_not_clobbered(self):
        env = self.base_env(TORTOISE_DB_URI="docker://from-ambient")
        r = self.run_wrapper(["sh", "-c", 'printf "%s" "$TORTOISE_DB_URI"'], env=env)
        self.assertEqual(r.stdout, "docker://from-ambient")

    def test_non_managed_var_is_filled_when_absent(self):
        r = self.run_wrapper(["sh", "-c", 'printf "%s" "$TORTOISE_DB_URI"'])
        self.assertEqual(r.stdout, "docker://:falkordb@localhost:6379/from-env-file")

    def test_first_occurrence_of_a_duplicate_env_key_wins(self):
        # `_load_dotenv` never overrides a key it has already set, so the FIRST
        # `.env` occurrence wins — the child env must agree.
        env_file = Path(self._tmp.name) / "dup.env"
        env_file.write_text(
            "EVALTEST_DUP=first\nEVALTEST_DUP=second\n", encoding="utf-8"
        )
        r = self.run_wrapper(
            ["sh", "-c", 'printf "%s" "$EVALTEST_DUP"'],
            env=self.base_env(EVAL_KEYS_ENV_FILE=str(env_file)),
            use_fixture=False,
        )
        self.assertEqual(r.stdout, "first", r.stderr)

    def test_malformed_ambient_name_cannot_forge_an_inherited_key(self):
        # An env entry whose NAME contains a newline — expressible through
        # execve, though not by an ordinary `VAR=...` assignment — must not make
        # a legitimate `.env` key look inherited and silently drop it.
        env_file = Path(self._tmp.name) / "forge.env"
        env_file.write_text("EVALTEST_FORGE=from-dotenv\n", encoding="utf-8")
        env = self.base_env(EVAL_KEYS_ENV_FILE=str(env_file))
        env["EVALTEST_HEAD\nEVALTEST_FORGE"] = "trap"
        r = self.run_wrapper(
            ["sh", "-c", 'printf "%s" "${EVALTEST_FORGE-unset}"'],
            env=env,
            use_fixture=False,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "from-dotenv", r.stderr)

    def test_inherited_value_survives_without_an_external_env_command(self):
        # The fill-if-absent decision must not depend on an external command:
        # with a PATH that cannot resolve the old `printenv` probe, an inherited
        # non-managed var must still win over `.env`. The temp bin dir carries
        # only the `bash` symlink the wrapper's `#!/usr/bin/env bash` needs, and
        # provably no `printenv` — `PATH=/bin` is NOT sufficient, because on
        # usrmerge Linux (CI's ubuntu-latest) `/bin` IS `/usr/bin`.
        probe_free_bin = Path(self._tmp.name) / "probe-free-bin"
        probe_free_bin.mkdir()
        (probe_free_bin / "bash").symlink_to("/bin/bash")
        self.assertIsNone(
            shutil.which("printenv", path=str(probe_free_bin)),
            "this test's premise (no printenv on the PATH) does not hold",
        )
        env_file = Path(self._tmp.name) / "keep.env"
        env_file.write_text("EVALTEST_KEEP=from-dotenv\n", encoding="utf-8")
        env = self.base_env(PATH=str(probe_free_bin), EVALTEST_KEEP="ambient-wins")
        env["EVAL_KEYS_ENV_FILE"] = str(env_file)
        r = self.run_wrapper(
            ["/bin/sh", "-c", 'printf "%s" "${EVALTEST_KEEP-unset}"'],
            env=env,
            use_fixture=False,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "ambient-wins", r.stderr)

    def test_exported_function_shadows_are_neutralized(self):
        # A caller can export FUNCTIONS that shadow the builtins the strip, the
        # load and the receipt itself use (`unset`, `export`, `printf`, `set`,
        # `[`, plus the probe's `command`/`declare`/`builtin`). `bash -p`
        # imports no shell functions, so the launcher still behaves normally:
        # the ambient managed key is stripped, the `.env` value is used, and the
        # receipt is printed.
        env = self.base_env(
            EVAL_KEYS_ENV_FILE=str(self.env_file), OPENROUTER_API_KEY=SABOTAGE
        )
        for name in (
            "unset",
            "export",
            "printf",
            "set",
            "[",
            "command",
            "declare",
            "builtin",
        ):
            env[f"BASH_FUNC_{name}%%"] = "() { return 0; }"
        r = self.run_wrapper(
            # `printenv` (not `sh -c printf`) checks the child's key: the
            # caller's exported `BASH_FUNC_printf%%` is passed through to the
            # wrapped command, but the LAUNCHER's own receipt must be intact.
            ["printenv", "OPENROUTER_API_KEY"],
            env=env,
            use_fixture=False,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        # the sabotaged ambient key was stripped and the `.env` value used
        self.assertEqual(r.stdout.strip(), FIXTURE_OPENROUTER)
        self.assertNotIn("DEADBEEF", r.stdout)
        # the receipt still printed (a live shadowed `printf` would silence it)
        self.assertIn("OPENROUTER_API_KEY source=", r.stderr)

    def test_the_sanitization_marker_is_not_inherited_by_children(self):
        # `_RWEK_SANITIZED` must not leak into the wrapped command: a NESTED
        # invocation of the launcher would then skip the `bash -p` re-exec and
        # run with the caller's function shadows live — loading nothing while
        # still printing a receipt.
        env = self.base_env(EVAL_KEYS_ENV_FILE=str(self.env_file))
        env["BASH_FUNC_printf%%"] = "() { return 0; }"

        # 1. the marker itself does not reach the wrapped command
        marker = self.run_wrapper(
            ["printenv", "_RWEK_SANITIZED"], env=env, use_fixture=False
        )
        self.assertEqual(marker.stdout.strip(), "", marker.stderr)

        # 2. a nested invocation still sanitizes itself
        r = self.run_wrapper(
            [str(WRAPPER), "printenv", "OPENROUTER_API_KEY"],
            env=env,
            use_fixture=False,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(FIXTURE_OPENROUTER, r.stdout)
        self.assertIn("OPENROUTER_API_KEY source=", r.stderr)

    # ── .env parsing semantics (mirrors _load_dotenv) ──────────────────

    def test_the_strip_is_proven_not_assumed(self):
        # The `unset` of the ambient keys is an ordinary builtin; the proof that
        # it took is what makes the receipt's `source=` line trustworthy. The
        # marker path (`_RWEK_SANITIZED=1`, the caller-settable opt-out) skips
        # the re-exec, so a shadowed `unset` leaves an ambient key in place — the
        # proof must then refuse to run (exit 3) rather than print a receipt
        # attributing that value to the env file. The refusal is executed by an
        # absolute-path `"$BASH" -p -c …`, so it holds with `exit` shadowed too.
        for extra in ({}, {"BASH_FUNC_exit%%": "() { return 0; }"}):
            with self.subTest(exit_shadowed=bool(extra)):
                env = self.base_env(
                    OPENROUTER_API_KEY=SABOTAGE, _RWEK_SANITIZED="1", **extra
                )
                env["BASH_FUNC_unset%%"] = "() { return 0; }"
                r = self.run_wrapper(["true"], env=env)
                self.assertEqual(r.returncode, 3, r.stderr)
                self.assertIn("survived the ambient strip", r.stderr)
                # no receipt at all: no per-key source claim is made
                self.assertNotIn("fingerprint=", r.stderr)
                self.assertNotIn(SABOTAGE, r.stderr)

    def test_a_shadowed_exec_and_exit_still_fail_loudly(self):
        # The previous round's sentinel used `printf` + `exit`, so shadowing
        # `exit` (or both) restored the silent exit-0. The sentinel is now an
        # absolute-path `"$BASH" -p -c …`: a slash-qualified word is not a
        # function lookup, and `-p` makes the child import no exported function,
        # so its own printf is real.
        for extra in (
            {"BASH_FUNC_exec%%": "() { return 0; }"},
            {
                "BASH_FUNC_exec%%": "() { return 0; }",
                "BASH_FUNC_exit%%": "() { return 0; }",
            },
            {
                "BASH_FUNC_exec%%": "() { return 0; }",
                "BASH_FUNC_exit%%": "() { return 0; }",
                "BASH_FUNC_printf%%": "() { return 0; }",
            },
        ):
            with self.subTest(shadowed=sorted(extra)):
                r = self.run_wrapper(["true"], env=self.base_env(**extra))
                self.assertNotEqual(
                    r.returncode, 0, "a shadowed exec still looked like success"
                )
                self.assertEqual(r.returncode, 3, r.stderr)
                self.assertIn("the command did NOT run", r.stderr)

    def test_a_failed_strip_cannot_be_reported_as_the_env_file(self):
        # The strip proof's response is uninhabitable by a shadowed `exit`, so
        # the false-attribution path needs `exec` shadowed as well (then the
        # refusal cannot replace the process and the run continues). On that path
        # the receipt must stay TRUE per key — including when `export` is
        # shadowed too, which makes the loader's `export` a no-op while the
        # ambient value is still the one in the environment. Recording the file
        # as the source by INTENT (appending after the export without checking it
        # took) mislabelled exactly that case.
        file_value = "sk-or-v1-FILEVALUE0123456789abcdefghijklmnopqrst"
        env_file = Path(self._tmp.name) / "two-keys.env"
        env_file.write_text(
            f"OPENROUTER_API_KEY={file_value}\n"
            f"DEEPSEEK_API_KEY={FIXTURE_DEEPSEEK}\n",
            encoding="utf-8",
        )
        for shadow_export in (False, True):
            with self.subTest(export_shadowed=shadow_export):
                env = self.base_env(
                    EVAL_KEYS_ENV_FILE=str(env_file),
                    OPENROUTER_API_KEY=SABOTAGE,
                    _RWEK_SANITIZED="1",
                )
                env["BASH_FUNC_unset%%"] = "() { return 0; }"
                env["BASH_FUNC_exit%%"] = "() { return 0; }"
                env["BASH_FUNC_exec%%"] = "() { return 0; }"
                if shadow_export:
                    env["BASH_FUNC_export%%"] = "() { return 0; }"
                r = self.run_wrapper(["true"], env=env, use_fixture=False)
                # the outcome an attacker wants — a silent success — is gone
                self.assertEqual(r.returncode, 3, r.stderr)
                self.assertIn("NOT SANITIZED", r.stderr)
                lines = {
                    line.split()[1]: line
                    for line in r.stderr.splitlines()
                    if " source=" in line
                }
                opened = lines["OPENROUTER_API_KEY"]
                if shadow_export:
                    # the export was a no-op, so the value really IS the ambient
                    # one — it must not be attributed to the file
                    self.assertIn(f"len={len(SABOTAGE)}", opened)
                    self.assertIn("aborted", opened)
                    self.assertNotIn(f"source={env_file.resolve()}", opened)
                    self.assertIn("source=unset", lines["DEEPSEEK_API_KEY"])
                else:
                    # the export took: the file's value is what the child gets,
                    # and the file is truthfully named as its source
                    self.assertIn(f"len={len(file_value)}", opened)
                    self.assertIn(f"source={env_file.resolve()}", opened)
                    self.assertIn(
                        f"source={env_file.resolve()}", lines["DEEPSEEK_API_KEY"]
                    )
                self.assertNotIn(SABOTAGE, r.stderr)
                self.assertNotIn(file_value, r.stderr)

    def test_a_shadowed_exec_fails_loudly_instead_of_silently(self):
        # The one outcome that must never look like success is "the wrapped
        # command never ran". Two mechanisms stop it from doing so: the sentinel
        # after the final exec, and — because an absolute-path `"$BASH" -p -c`
        # is not a function lookup — the sentinel works whatever else the caller
        # shadowed.
        env = self.base_env()
        env["BASH_FUNC_exec%%"] = "() { return 0; }"
        r = self.run_wrapper(["true"], env=env)
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("the command did NOT run", r.stderr)

    def test_env_file_cannot_redirect_a_nested_invocation(self):
        # `EVAL_KEYS_ENV_FILE` is the launcher's own INPUT, not a key to
        # publish. A `.env` line naming it would be exported (fill-if-absent)
        # and a NESTED run of the launcher would then read a different file — so
        # the "a `.env` can never rewrite the launcher's own state" claim would
        # be false one level down. Synthetic repo layout, so the DEFAULT `.env`
        # path is the fixture and the developer's live `.env` is never read.
        repo = Path(self._tmp.name) / "redirect-repo"
        (repo / "tools").mkdir(parents=True)
        copied = repo / "tools" / "run-with-eval-keys.sh"
        shutil.copyfile(WRAPPER, copied)
        copied.chmod(copied.stat().st_mode | stat.S_IXUSR)
        other = repo / "other.env"
        other.write_text(f"OPENROUTER_API_KEY={SABOTAGE}\n", encoding="utf-8")
        (repo / ".env").write_text(
            f"EVAL_KEYS_ENV_FILE={other}\n"
            f"OPENROUTER_API_KEY={FIXTURE_OPENROUTER}\n",
            encoding="utf-8",
        )
        r = subprocess.run(
            [
                str(copied),
                "sh",
                "-c",
                'printf "%s" "${EVAL_KEYS_ENV_FILE:-<unset>}"',
            ],
            capture_output=True,
            text=True,
            env=self.base_env(),
            timeout=60,
            check=False,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(
            r.stdout, "<unset>", "a `.env` line published the launcher's own input"
        )
        self.assertNotIn("DEADBEEF", r.stderr)

    def test_an_export_that_does_not_export_is_not_recorded_as_the_file(self):
        # `export` is a builtin a caller can shadow, and a shadow that ASSIGNS
        # without exporting leaves the value in this shell but not in the child's
        # environment. Comparing the value alone would then certify the file as
        # the source of a key the command never received. The flag is therefore
        # read from a FRESH privileged shell (absolute path, no imported
        # functions), which a caller cannot shadow — including by shadowing
        # `builtin` itself to fake a `declare -x` line.
        for fake_builtin in (False, True):
            with self.subTest(builtin_shadowed=fake_builtin):
                env = self.base_env(
                    EVAL_KEYS_ENV_FILE=str(self.env_file), _RWEK_SANITIZED="1"
                )
                env["BASH_FUNC_export%%"] = (
                    '() { builtin printf -v "${1%%=*}" "%s" "${1#*=}"; }'
                )
                if fake_builtin:
                    env["BASH_FUNC_builtin%%"] = (
                        '() { if [ "$1" = declare ]; then shift 2; '
                        'printf "declare -x %s=fake\\n" "$1"; '
                        'else command "$@"; fi; }'
                    )
                r = self.run_wrapper(
                    ["sh", "-c", 'printf "%s" "${OPENROUTER_API_KEY-UNSET}"'],
                    env=env,
                    use_fixture=False,
                )
                self.assertEqual(r.returncode, 0, r.stderr)
                # the child never received the key…
                self.assertEqual(r.stdout, "UNSET")
                # …so no receipt line may name the file as its source
                self.assertIn("NOT SANITIZED", r.stderr)
                line = next(
                    s
                    for s in r.stderr.splitlines()
                    if "OPENROUTER_API_KEY source=" in s
                )
                self.assertIn("aborted", line)
                self.assertNotIn(f"source={self.env_file.resolve()}", line)
                self.assertNotIn(FIXTURE_OPENROUTER, r.stderr)

    def test_env_file_cannot_repoint_the_interpreter(self):
        # The fail-closed responses exec an absolute path, so a `.env`-supplied
        # `BASH` (non-exported by bash, so the loader's fill-if-absent branch
        # would happily export a value over it) must not be able to point them
        # at a program that exits 0 — which would swallow the sentinel and turn
        # a shadowed `exec` back into a silent exit-0 with the command not run.
        swallow = Path(self._tmp.name) / "swallow"
        swallow.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        swallow.chmod(0o755)
        env_file = Path(self._tmp.name) / "bash-redirect.env"
        env_file.write_text(
            f"OPENROUTER_API_KEY={FIXTURE_OPENROUTER}\nBASH={swallow}\n",
            encoding="utf-8",
        )
        env = self.base_env(EVAL_KEYS_ENV_FILE=str(env_file))
        env["BASH_FUNC_exec%%"] = "() { return 0; }"
        r = self.run_wrapper(["true"], env=env, use_fixture=False)
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("the command did NOT run", r.stderr)
        self.assertNotIn(FIXTURE_OPENROUTER, r.stderr)

    def test_tracing_refusal_survives_shadowed_set_and_exit(self):
        # The tracing refusal is the one response whose failure LEAKS a value: a
        # shadowed `set` leaves xtrace on and a shadowed `exit` then lets the run
        # continue, so the loader's `export` line prints the full key — after a
        # message claiming the run was refused. It now goes through the same
        # absolute-path child, so the refusal is real and the key never prints.
        env = self.base_env(
            EVAL_KEYS_ENV_FILE=str(self.env_file),
            _RWEK_SANITIZED="1",
        )
        env["SHELLOPTS"] = "xtrace"
        env["BASH_FUNC_set%%"] = "() { return 0; }"
        env["BASH_FUNC_exit%%"] = "() { return 0; }"
        r = self.run_wrapper(["true"], env=env, use_fixture=False)
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("refusing to run", r.stderr)
        self.assertNotIn(FIXTURE_OPENROUTER, r.stderr)
        self.assertNotIn(FIXTURE_OPENROUTER, r.stdout)

    def test_parses_export_quotes_and_comments(self):
        script = "; ".join(f'printf "%s|" "$EVALTEST_{name}"' for name in (
            "EXPORTED", "QUOTED", "SINGLE", "INLINE", "HASH_IN_VALUE",
            "PADDED", "OK",
        ))
        r = self.run_wrapper(["sh", "-c", script])
        self.assertEqual(
            r.stdout,
            "|".join([
                "exported-value",
                "quoted value with spaces",
                "single quoted",
                "value-with-inline",
                "abc#notacomment",
                "padded-value",
                "1",
            ]) + "|",
            r.stderr,
        )

    def test_env_keys_colliding_with_loader_variables_are_not_dropped(self):
        # The loader's own shell variables must not shadow a `.env` entry of
        # the same name: that silently drops config the run needs (e.g. a
        # `key=` or `value=` line), with no warning.
        names = ("line", "raw", "key", "value", "first", "last", "already", "present", "fp")
        env_file = Path(self._tmp.name) / "collide.env"
        env_file.write_text(
            "".join(f"{n}=collide-{n}\n" for n in names), encoding="utf-8"
        )
        script = "; ".join(f'printf "%s|" "${{{n}}}"' for n in names)
        r = self.run_wrapper(
            ["sh", "-c", script],
            env=self.base_env(EVAL_KEYS_ENV_FILE=str(env_file)),
            use_fixture=False,
        )
        self.assertEqual(
            r.stdout, "|".join(f"collide-{n}" for n in names) + "|", r.stderr
        )

    def test_env_cannot_rewrite_the_launchers_own_state(self):
        # A `.env` is inert DATA: it must not be able to redirect the env-file
        # path, forge the source label, or edit the managed-key set — nor reach
        # the wrapped command under those reserved names.
        env_file = Path(self._tmp.name) / "hostile.env"
        env_file.write_text(
            "\n".join(
                [
                    f'OPENROUTER_API_KEY="{FIXTURE_OPENROUTER}"',
                    "_RWEK_ENV_FILE=/etc/passwd",
                    "_RWEK_SOURCE_LABEL=/etc/shadow",
                    "_RWEK_MANAGED_KEYS=oops",
                    "_RWEK_REPO_ROOT=/tmp",
                    "_rwek_key=corrupt",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        r = self.run_wrapper(
            [
                "sh",
                "-c",
                'printf "%s|%s" "${_RWEK_ENV_FILE:-unset}" "${_rwek_key:-unset}"',
            ],
            env=self.base_env(EVAL_KEYS_ENV_FILE=str(env_file)),
            use_fixture=False,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        # the reserved names never reached the child env
        self.assertEqual(r.stdout, "unset|unset")
        # the header names the file actually read, not the one the .env asked
        # for
        resolved = Path(env_file).resolve()
        self.assertIn(f"file={resolved}", r.stderr)
        self.assertNotIn("/etc/passwd", r.stderr)
        # the source label was not forged
        self.assertIn(f"source={resolved} fingerprint=", r.stderr)
        self.assertNotIn("/etc/shadow", r.stderr)
        # and every managed key is still declared
        for key in MANAGED:
            self.assertIn(f"[eval-keys] {key} ", r.stderr)

    def test_env_key_naming_a_non_exported_shell_variable_is_still_loaded(self):
        # `_load_dotenv` judges "already set" by the process ENVIRONMENT, so a
        # `.env` key naming a bash-internal, NON-exported variable (PS4 here)
        # is still loaded. A "is this shell variable set" probe would drop it.
        env_file = Path(self._tmp.name) / "internals.env"
        env_file.write_text("PS4=TRACEprompt+\n", encoding="utf-8")
        r = self.run_wrapper(
            ["sh", "-c", 'printf "%s" "$PS4"'],
            env=self.base_env(EVAL_KEYS_ENV_FILE=str(env_file)),
            use_fixture=False,
        )
        self.assertEqual(r.stdout, "TRACEprompt+", r.stderr)

    def test_multiline_ambient_value_does_not_shadow_an_env_key(self):
        # "Already inherited" is judged by variable NAME, never by scanning a
        # dump of the environment: a multi-line ambient value containing
        # `TORTOISE_DB_URI=…` must not make the key look inherited (which would
        # silently drop the `.env` value).
        env = self.base_env()
        env["EVALTEST_DECOY"] = "x\nTORTOISE_DB_URI=evil"
        r = self.run_wrapper(["sh", "-c", 'printf "%s" "$TORTOISE_DB_URI"'], env=env)
        self.assertEqual(
            r.stdout, "docker://:falkordb@localhost:6379/from-env-file", r.stderr
        )

    # ── exec + exit-code fidelity ──────────────────────────────────────

    def test_exec_replaces_the_wrapper_process(self):
        proc = subprocess.Popen(
            [str(WRAPPER), "sh", "-c", 'printf "%s" "$$"'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**self.base_env(), "EVAL_KEYS_ENV_FILE": str(self.env_file)},
        )
        out, _ = proc.communicate(timeout=60)
        self.assertEqual(
            int(out),
            proc.pid,
            "the wrapped command runs in a child, not the exec'd wrapper",
        )

    def test_exit_code_propagates(self):
        r = self.run_wrapper(["sh", "-c", "exit 42"])
        self.assertEqual(r.returncode, 42)

    def test_args_with_spaces_are_preserved(self):
        r = self.run_wrapper(["sh", "-c", 'printf "%s\\n" "$1" "$2"', "_", "a b", "c  d"])
        self.assertEqual(r.stdout.splitlines(), ["a b", "c  d"])

    def test_stdout_of_the_command_is_not_interleaved_with_the_declaration(self):
        r = self.run_wrapper(["sh", "-c", 'printf "DATA"'])
        self.assertEqual(r.stdout, "DATA")
        self.assertNotIn("eval-keys", r.stdout)

    # ── never writes .env; missing .env fails closed ───────────────────

    def test_env_file_is_never_written(self):
        before = self.env_file.read_bytes()
        mtime_before = self.env_file.stat().st_mtime_ns
        self.run_wrapper(["true"])
        self.assertEqual(self.env_file.read_bytes(), before)
        self.assertEqual(self.env_file.stat().st_mtime_ns, mtime_before)

    def test_missing_env_file_fails_closed(self):
        env = self.base_env(OPENROUTER_API_KEY=SABOTAGE)
        env["EVAL_KEYS_ENV_FILE"] = str(Path(self._tmp.name) / "nope.env")
        r = subprocess.run(
            [str(WRAPPER), "sh", "-c", "printenv OPENROUTER_API_KEY || echo UNSET"],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
            check=False,
        )
        self.assertEqual(r.stdout.strip(), "UNSET")
        self.assertIn("WARNING", r.stderr)
        self.assertIn("OPENROUTER_API_KEY source=unset fingerprint=none", r.stderr)

    def test_env_file_that_is_a_directory_fails_closed(self):
        # `[ -r ]` is true for a directory; without the regular-file guard the
        # read fails (or takes the warning path, platform-dependent) and the
        # provenance block is skipped. Either way: fail closed, warn, no crash.
        d = Path(self._tmp.name) / "adir"
        d.mkdir()
        env = self.base_env(
            OPENROUTER_API_KEY=SABOTAGE, EVAL_KEYS_ENV_FILE=str(d)
        )
        r = self.run_wrapper(
            ["sh", "-c", "printenv OPENROUTER_API_KEY || echo UNSET"],
            env=env,
            use_fixture=False,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "UNSET")
        self.assertIn("WARNING", r.stderr)
        self.assertIn("OPENROUTER_API_KEY source=unset fingerprint=none", r.stderr)

    # ── default path is the repo-root .env ─────────────────────────────

    def test_default_env_file_is_the_repo_root_env(self):
        # A synthetic repo layout — the wrapper COPIED to <tmp>/repo/tools/…,
        # so the DEFAULT `.env` path resolves to <tmp>/repo/.env. Reading the
        # developer's live `.env` here would emit real-key fingerprints into
        # pytest's captured stderr (they would be dumped on any failure).
        repo = Path(self._tmp.name) / "repo"
        (repo / "tools").mkdir(parents=True)
        copied = repo / "tools" / "run-with-eval-keys.sh"
        shutil.copyfile(WRAPPER, copied)
        copied.chmod(copied.stat().st_mode | stat.S_IXUSR)
        (repo / ".env").write_text(
            f'OPENROUTER_API_KEY="{FIXTURE_OPENROUTER}"\n', encoding="utf-8"
        )
        r = subprocess.run(
            [str(copied), "true"],
            capture_output=True,
            text=True,
            env=self.base_env(),
            timeout=60,
            check=False,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        # the default path is named with the honest `.env` alias
        self.assertIn("OPENROUTER_API_KEY source=.env fingerprint=", r.stderr)
        self.assertIn(FIXTURE_OPENROUTER[:6], r.stderr)
        self.assertNotIn(FIXTURE_OPENROUTER, r.stderr)

    def test_symlinked_env_file_discloses_its_target(self):
        # The alias is what is opened (so the label legitimately says `.env`),
        # but the bytes come from the target — the receipt must disclose it, or
        # it could claim the evals key while a different file was read.
        base = Path(self._tmp.name).resolve()
        target = base / "pi-keys.env"
        target.write_text(
            "OPENROUTER_API_KEY=sk-or-v1-TARGETsymlinkvalue0123456\n",
            encoding="utf-8",
        )
        link = base / "linked.env"
        link.symlink_to(target)
        r = self.run_wrapper(
            ["true"],
            env=self.base_env(EVAL_KEYS_ENV_FILE=str(link)),
            use_fixture=False,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"file={link} -> {target}", r.stderr)
        self.assertNotIn("TARGETsymlinkvalue0123456", r.stderr)

    def test_relative_override_is_never_labelled_dot_env(self):
        # A relative EVAL_KEYS_ENV_FILE from a foreign cwd reads THAT file —
        # the declaration must not claim the repo-root `.env` (false
        # provenance in a receipt is the failure class this tool prevents).
        foreign = Path(self._tmp.name) / "foreign"
        foreign.mkdir()
        (foreign / ".env").write_text(
            "OPENROUTER_API_KEY=sk-or-v1-FOREIGNabcdefghijklmnop\n",
            encoding="utf-8",
        )
        env = self.base_env()
        env["EVAL_KEYS_ENV_FILE"] = ".env"
        r = subprocess.run(
            [str(WRAPPER), "true"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(foreign),
            timeout=60,
            check=False,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("OPENROUTER_API_KEY source=.env ", r.stderr)
        self.assertRegex(r.stderr, r"OPENROUTER_API_KEY source=/.+[/\\]\.env ")

    def test_symlinked_invocation_resolves_the_real_repo_root(self):
        # A symlink in a foreign directory must not make that directory the
        # repo root — the `.env` label would then name a file the wrapper did
        # not read (the `$0`-derived REPO_ROOT hole). The whole scenario is
        # synthetic (a COPY of the wrapper in a temp repo layout), so the real
        # repo-root `.env` is never read and no real-key fingerprint can reach
        # pytest's captured stderr.
        repo = Path(self._tmp.name) / "repo"
        (repo / "tools").mkdir(parents=True)
        copied = repo / "tools" / "run-with-eval-keys.sh"
        shutil.copyfile(WRAPPER, copied)
        copied.chmod(copied.stat().st_mode | stat.S_IXUSR)
        (repo / ".env").write_text(
            f'OPENROUTER_API_KEY="{FIXTURE_OPENROUTER}"\n', encoding="utf-8"
        )
        bin_dir = Path(self._tmp.name) / "bin"
        bin_dir.mkdir()
        link = bin_dir / "rwek.sh"
        link.symlink_to(copied)
        # a DECOY .env in the symlink's own directory must be ignored
        (bin_dir / ".env").write_text(
            "OPENROUTER_API_KEY=sk-or-v1-FOREIGNsymlinkvalue\n", encoding="utf-8"
        )
        r = subprocess.run(
            [str(link), "true"],
            capture_output=True,
            text=True,
            env=self.base_env(),
            cwd=str(bin_dir),
            timeout=60,
            check=False,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        # the header names the REAL repo layout's .env, not the symlink's dir
        self.assertIn(f"file={Path(repo).resolve()}/.env", r.stderr)
        self.assertNotIn("FOREIGNsymlinkvalue", r.stderr)
        self.assertIn(FIXTURE_OPENROUTER[:6], r.stderr)


if __name__ == "__main__":
    sys.exit(unittest.main(verbosity=2))
