"""Task 2.3 — deterministic fault-injection framework.

Four hooks, each triggerable by env var and/or seed config:

1. ``comp_fail_n``       — the mock Stripe refund path fails N times with a
                           500-ish error, then succeeds (executor layer).
2. ``kill_point``        — process dies mid-rollback / mid-tool (subprocess
                           harness reads this; see sigchild.py).
3. ``capture_timeout_pct`` — capture_before times out on a fixed fraction of
                           calls, deterministically by seed (adapter shim).
4. ``replay_n``          — duplicate tool-call replay: identical calls are
                           re-executed N times (world + ledger must dedup).

``FaultConfig.from_env()`` reads UNDOLOG_FAULT_* variables;
``FaultConfig.for_seed(seed)`` derives a deterministic config for chaos runs.
"""
from __future__ import annotations

import os
import random
from dataclasses import dataclass
from typing import Any, Optional

ENV_COMP_FAIL_N = "UNDOLOG_FAULT_COMP_FAIL_N"
ENV_CAPTURE_TIMEOUT_PCT = "UNDOLOG_FAULT_CAPTURE_TIMEOUT_PCT"
ENV_REPLAY_N = "UNDOLOG_FAULT_REPLAY_N"
ENV_KILL_POINT = "UNDOLOG_KILL_POINT"
ENV_KILL_SLEEP_SEC = "UNDOLOG_KILL_SLEEP_SEC"

KILL_POINT_MID_TOOL = "mid_tool"
KILL_POINT_MID_COMPENSATION = "mid_compensation"


class TimeoutError_(TimeoutError):
    """Injected capture_before timeout (distinct type so tests can tell it

    apart from real adapter bugs)."""


@dataclass(frozen=True)
class FaultConfig:
    """Which faults are active for a run. All-off = pristine behavior."""

    comp_fail_n: int = 0                # refund compensations that fail before succeeding
    comp_fail_forever: bool = False     # refund compensations never succeed (exhaustion path)
    capture_timeout_pct: float = 0.0    # fraction of capture_before calls that time out
    replay_n: int = 0                   # extra identical replays of distinct scenario actions
    kill_point: Optional[str] = None    # subprocess harness only
    kill_sleep_sec: float = 5.0         # how long the child sleeps at the kill point

    # -- construction -----------------------------------------------------

    @classmethod
    def from_env(cls, env: Optional[dict] = None) -> "FaultConfig":
        e = os.environ if env is None else env
        return cls(
            comp_fail_n=int(e.get(ENV_COMP_FAIL_N, "0") or 0),
            capture_timeout_pct=float(
                e.get(ENV_CAPTURE_TIMEOUT_PCT, "0") or 0.0),
            replay_n=int(e.get(ENV_REPLAY_N, "0") or 0),
            kill_point=e.get(ENV_KILL_POINT) or None,
            kill_sleep_sec=float(e.get(ENV_KILL_SLEEP_SEC, "5") or 5.0),
        )

    @classmethod
    def for_seed(cls, seed: int) -> "FaultConfig":
        """Deterministic per-seed hook selection for chaos runs.

        comp_fail_n is capped at 2 so a rollback with max_attempts>=3 still
        completes; capture timeouts hit ~1/3 of runs; replays ~1/3.
        """
        rng = random.Random(f"undolog-faults-{seed}")
        return cls(
            comp_fail_n=rng.choice([0, 0, 1, 2]),
            capture_timeout_pct=rng.choice([0.0, 0.0, 0.2]),
            replay_n=rng.choice([0, 0, 5]),
        )


def set_env_faults(monkeypatch, **kwargs) -> None:
    """Test helper: push FaultConfig fields into the UNDOLOG_FAULT_* env."""
    mapping = {
        "comp_fail_n": ENV_COMP_FAIL_N,
        "capture_timeout_pct": ENV_CAPTURE_TIMEOUT_PCT,
        "replay_n": ENV_REPLAY_N,
        "kill_point": ENV_KILL_POINT,
        "kill_sleep_sec": ENV_KILL_SLEEP_SEC,
    }
    for key, value in kwargs.items():
        monkeypatch.setenv(mapping[key], str(value))


def clear_env_faults(monkeypatch) -> None:
    for var in (ENV_COMP_FAIL_N, ENV_CAPTURE_TIMEOUT_PCT, ENV_REPLAY_N,
                ENV_KILL_POINT, ENV_KILL_SLEEP_SEC):
        monkeypatch.delenv(var, raising=False)


# --------------------------------------------------------------------------
# Hook 3 implementation: adapter shim
# --------------------------------------------------------------------------

class FlakyCaptureAdapter:
    """Wraps a real adapter; capture_before raises TimeoutError_ on a fixed
    fraction of calls, driven by an injected RNG (deterministic per seed)."""

    def __init__(self, inner: Any, pct: float, rng: random.Random):
        self._inner = inner
        self._pct = pct
        self._rng = rng
        self.timeouts = 0

    def capture_before(self) -> dict:
        if self._pct > 0 and self._rng.random() < self._pct:
            self.timeouts += 1
            raise TimeoutError_("injected capture_before timeout")
        return self._inner.capture_before()

    def capture_after(self, result: Any) -> dict:
        return self._inner.capture_after(result)
