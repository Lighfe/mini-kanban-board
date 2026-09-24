import subprocess
import time

import pytest

from jev.__main__ import run_with_timeout


def test_timeout_returns_even_if_a_grandchild_holds_the_pipes():
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_with_timeout(["sh", "-c", "sleep 30 & sleep 30"], input="", timeout=1)
    assert time.monotonic() - started < 10


def test_returns_output_and_exit_code():
    proc = run_with_timeout(["sh", "-c", "cat; exit 3"], input="hi", timeout=5)
    assert (proc.returncode, proc.stdout) == (3, "hi")
