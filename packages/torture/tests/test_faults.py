"""Task 2.3 — fault-injection framework acceptance tests.

Each hook must be triggerable by env var and/or seed config, deterministic
per seed, and produce the documented effect in isolation.
"""
import random

import pytest

from undolog_torture import DEFAULT_SEED, build_worlds, run_scenario, hash_world
from undolog_torture.faults import (
    FaultConfig,
    FlakyCaptureAdapter,
    TimeoutError_,
    clear_env_faults,
    set_env_faults,
)


# --------------------------------------------------------------------------
# FaultConfig: env-var and seed construction
# --------------------------------------------------------------------------

def test_defaults_are_no_faults():
    f = FaultConfig()
    assert f.comp_fail_n == 0
    assert f.comp_fail_forever is False
    assert f.capture_timeout_pct == 0.0
    assert f.replay_n == 0
    assert f.kill_point is None


def test_from_env_reads_all_hooks(monkeypatch):
    monkeypatch.setenv("UNDOLOG_FAULT_COMP_FAIL_N", "2")
    monkeypatch.setenv("UNDOLOG_FAULT_CAPTURE_TIMEOUT_PCT", "0.2")
    monkeypatch.setenv("UNDOLOG_FAULT_REPLAY_N", "5")
    monkeypatch.setenv("UNDOLOG_KILL_POINT", "mid_compensation")
    monkeypatch.setenv("UNDOLOG_KILL_SLEEP_SEC", "1.5")
    f = FaultConfig.from_env()
    assert f.comp_fail_n == 2
    assert f.capture_timeout_pct == 0.2
    assert f.replay_n == 5
    assert f.kill_point == "mid_compensation"
    assert f.kill_sleep_sec == 1.5


def test_env_module_helpers(monkeypatch):
    set_env_faults(monkeypatch, comp_fail_n=3, capture_timeout_pct=0.4,
                   replay_n=2)
    f = FaultConfig.from_env()
    assert (f.comp_fail_n, f.capture_timeout_pct, f.replay_n) == (3, 0.4, 2)
    clear_env_faults(monkeypatch)
    assert FaultConfig.from_env() == FaultConfig()


def test_for_seed_is_deterministic_and_varied():
    a = FaultConfig.for_seed(42)
    b = FaultConfig.for_seed(42)
    c = FaultConfig.for_seed(43)
    assert a == b
    assert hash(tuple(vars(a).values())) != 0  # constructible
    # comp_fail_n stays within retry-safe bounds so chaos rollbacks succeed
    assert a.comp_fail_n in (0, 1, 2)
    assert c.comp_fail_n in (0, 1, 2)
    # at least one of a long seed range enables each hook
    hooks = {FaultConfig.for_seed(s) for s in range(200)}
    assert any(h.comp_fail_n > 0 for h in hooks)
    assert any(h.capture_timeout_pct > 0 for h in hooks)
    assert any(h.replay_n > 0 for h in hooks)


# --------------------------------------------------------------------------
# Hook 3: capture_before timeout (deterministic by seed)
# --------------------------------------------------------------------------

class _RecordingAdapter:
    def __init__(self):
        self.calls = 0

    def capture_before(self):
        self.calls += 1
        return {"ok": True}

    def capture_after(self, result):
        return {"result": result}


def test_flaky_adapter_times_out_deterministically():
    rng_a = random.Random(7)
    rng_b = random.Random(7)
    inner_a, inner_b = _RecordingAdapter(), _RecordingAdapter()
    fa = FlakyCaptureAdapter(inner_a, pct=0.2, rng=rng_a)
    fb = FlakyCaptureAdapter(inner_b, pct=0.2, rng=rng_b)
    outcomes_a, outcomes_b = [], []
    for _ in range(100):
        for shim, out in ((fa, outcomes_a), (fb, outcomes_b)):
            try:
                shim.capture_before()
                out.append(False)
            except TimeoutError_:
                out.append(True)
    assert outcomes_a == outcomes_b          # deterministic per rng seed
    assert 5 <= sum(outcomes_a) <= 35        # roughly 20%
    assert sum(outcomes_a) > 0               # hook actually fires


def test_flaky_adapter_passes_through_capture_after():
    shim = FlakyCaptureAdapter(_RecordingAdapter(), pct=0.0,
                               rng=random.Random(1))
    assert shim.capture_after({"x": 1}) == {"result": {"x": 1}}


# --------------------------------------------------------------------------
# Hook 4: duplicate replay — replaying identical calls changes nothing
# --------------------------------------------------------------------------

def test_replay_n_keeps_world_and_ledger_identical(engine):
    base = run_scenario(engine, seed=DEFAULT_SEED, thread_id="fx-replay-base")
    faults = FaultConfig(replay_n=5)
    rep = run_scenario(engine, seed=DEFAULT_SEED, thread_id="fx-replay",
                       faults=faults)
    # Replays re-execute identical calls: dedup everywhere -> no drift.
    assert hash_world(rep.final_state) == hash_world(base.final_state)
    assert len(rep.rows) == 50                       # no duplicate ledger rows
    assert [r.seq for r in rep.rows] == list(range(1, 51))


# --------------------------------------------------------------------------
# Hook interplay: capture-timeout faults in a chaos-style scenario run
# --------------------------------------------------------------------------

def test_capture_faults_make_rows_log_only_unknown(engine):
    from undolog_core.models import EntryClass
    faults = FaultConfig.for_seed(11)
    if faults.capture_timeout_pct == 0:
        faults = FaultConfig(capture_timeout_pct=0.2)
    res = run_scenario(engine, seed=11, thread_id="fx-capture",
                       faults=faults)
    log_only = [r for r in res.rows if r.log_only]
    assert log_only, "capture fault hook should fire at 20%"
    assert all(r.class_ is EntryClass.UNKNOWN for r in log_only)
    assert all(r.before_jsonb is None for r in log_only)
