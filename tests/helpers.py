"""Shared test infrastructure for every ContentOS test module.

Every test module in this package subclasses NoNetworkTestCase, so a bug
can never make a test reach out to the real Instagram/Apify APIs.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Mapping, Optional, Sequence, Tuple
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / "skills" / "contentos"
SCRIPTS_DIR = SKILL_DIR / "scripts"
SCRIPT = SCRIPTS_DIR / "contentos.py"

# Let any test (or contentos.py itself, once imported rather than run as a
# script) do `from lib import codes` without each test module repeating
# this sys.path setup.
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


class NoNetworkTestCase(unittest.TestCase):
    """Test case base that forbids real network access via urllib."""

    def setUp(self) -> None:
        super().setUp()
        for target in ("urllib.request.urlopen", "urllib.request.OpenerDirector.open"):
            patcher = mock.patch(
                target, side_effect=AssertionError("network disabled in tests")
            )
            patcher.start()
            self.addCleanup(patcher.stop)


def run_cli(
    args: Sequence[str],
    cwd: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
) -> Tuple[int, str, str]:
    """Run contentos.py as a subprocess; return (returncode, stdout, stderr)."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)] + list(args),
        cwd=str(cwd) if cwd is not None else None,
        env=dict(env) if env is not None else None,
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout, result.stderr


@contextmanager
def temp_project() -> Iterator[Path]:
    """Yield a fresh temporary directory to use as a creator project root."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        yield Path(tmp_dir)
