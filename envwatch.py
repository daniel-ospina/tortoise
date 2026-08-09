"""TEMP debug plugin: simulate the test_calibration env-leak fix by restoring
TORTOISE_DB_URI after collection, so the rest of the suite can be assessed."""
import os

from pytest import hookimpl


@hookimpl(tryfirst=True)
def pytest_collection_finish(session):
    os.environ.pop("TORTOISE_DB_URI", None)
