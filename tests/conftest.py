import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "template"


def pytest_addoption(parser):
    parser.addoption("--run-manual", action="store_true",
                     help="also run tests that need a real service, scheduler or Claude session")


def pytest_configure(config):
    config.addinivalue_line("markers", "manual: needs a real service, scheduler or Claude session")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-manual"):
        return
    skip = pytest.mark.skip(reason="manual test; run with --run-manual")
    for item in items:
        if "manual" in item.keywords:
            item.add_marker(skip)


def run_opsci(*args, cwd=None):
    """Run the CLI as a user would; returns CompletedProcess with text output."""
    return subprocess.run([sys.executable, "-m", "opsci.cli", *map(str, args)],
                          capture_output=True, text=True, cwd=cwd)


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True)
