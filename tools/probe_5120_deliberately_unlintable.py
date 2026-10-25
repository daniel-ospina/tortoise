"""THROWAWAY PROBE (#5120) — deliberately unlintable. Never merge this file.

Its only purpose is to make `ruff check .` report a finding that THIS lane
introduced (F401 below), so a branch dispatch of `main-lint.yml` proves the guard
still FAILS on a finding — and that the verdict step names that FAIL instead of
the old bare "Process completed with exit code 1".
"""

import os
