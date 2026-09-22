#!/usr/bin/env python3
"""Run docs/runbook/w7a_gold_rank_diagnostic.py on the DOCKER substrate.

The committed diagnostic is embedded-only (it builds TortoiseSDK on a
tempfile redislite path). Under fleet load the embedded lane is the known
blocker, so this driver monkeypatches ONLY the store the diagnostic opens:
TortoiseSDK is redirected at the docker FalkorDB (TORTOISE_ASK_SHAPE_DB_URI's
substrate), one unique scratch graph per measure() call.

It changes NOTHING about the measurement: same fixture, same five questions,
same CUT=40, same LIMIT=120/LEG_DEPTH=120, same dense/backlog arms, and it
reuses the diagnostic's own measure() verbatim.
"""
from __future__ import annotations

import importlib.util
import itertools
import os
import sys

REPO = "/Users/danielospina/Documents/GitHub/tortoise"
sys.path.insert(0, REPO)

spec = importlib.util.spec_from_file_location(
    "w7a", os.path.join(REPO, "docs/runbook/w7a_gold_rank_diagnostic.py"))
w7a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w7a)

import tortoise.sdk as sdk_mod  # noqa: E402

_real = sdk_mod.TortoiseSDK
_seq = itertools.count()
_created: list[str] = []


def docker_sdk(_path):
    n = next(_seq)
    name = f"w7a_rank_{os.getpid()}_{n}"
    _created.append(name)
    os.environ["TORTOISE_DB_URI"] = (
        f"docker://:falkordb@localhost:6379/{name}")
    return _real(None)


w7a.TortoiseSDK = docker_sdk

try:
    rc = w7a.main()
finally:
    with open("/tmp/askshape-m2/w7a_graphs.txt", "w") as f:
        f.write("\n".join(_created))
sys.exit(rc)
