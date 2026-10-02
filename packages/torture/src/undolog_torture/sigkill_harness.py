"""Parent-side harness for TS-06: spawn the child, wait for its kill-point
announcement on stdout, SIGKILL it, and return the exit code + transcript."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Optional

_KILL_POINT_LINE = {
    "mid_tool": "IN_TOOL",
    "mid_compensation": "IN_COMPENSATION",
}


def spawn_and_kill(schema: str, mode: str, thread_id: str,
                   kill_sleep: float = 5.0, timeout: float = 60.0
                   ) -> tuple[int, list[str]]:
    """Run the sigchild in ``mode`` against ``schema`` and SIGKILL it while
    it sleeps at the kill point.

    Returns (returncode, transcript). Raises RuntimeError if the child dies
    on its own or never reaches the kill point (harness bug, not a pass).
    """
    env = dict(os.environ)
    env["UNDOLOG_KILL_SLEEP_SEC"] = str(kill_sleep)
    env["UNDOLOG_KILL_POINT"] = mode
    cmd = [sys.executable, "-m", "undolog_torture.sigchild",
           "--mode", mode, "--schema", schema, "--thread", thread_id]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, env=env,
    )
    transcript: list[str] = []
    needle = _KILL_POINT_LINE[mode]
    deadline = time.perf_counter() + timeout
    reached = False
    assert proc.stdout is not None
    while time.perf_counter() < deadline:
        line = proc.stdout.readline()
        if line:
            transcript.append(line.strip())
            if needle in line:
                reached = True
                break
        elif proc.poll() is not None:
            break
        else:
            time.sleep(0.01)
    if not reached:
        proc.kill()
        proc.wait()
        raise RuntimeError(
            f"child never reached kill point {needle!r}: {transcript}")

    # The child sleeps kill_sleep seconds at the kill point; kill it firmly
    # inside that window.
    time.sleep(min(kill_sleep, 5.0) * 0.4)
    proc.kill()                      # SIGKILL: no cleanup, no atexit
    rc = proc.wait(timeout=10)
    for line in proc.stdout.read().splitlines():
        transcript.append(line.strip())
    return rc, transcript
