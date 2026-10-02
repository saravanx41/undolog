"""Chaos-runner end-to-end test + CLI smoke test (Task 2.4).

The dedicated 100-iteration / 100-seed verification is the orchestrator's
job (UNDOLOG_TORTURE_RUNS); here we prove the runner works end-to-end for a
small iteration count, writes the JSON report, and exits non-zero on
injected failure.
"""
import json
import subprocess
import sys

from undolog_torture.chaos import main as chaos_main
from undolog_torture.chaos import run_chaos


def test_chaos_runner_small(engine, tmp_path):
    report = run_chaos(engine, iterations=4, seed=20240,
                       json_path=str(tmp_path / "chaos.json"))
    assert report.all_passed, [
        (it.iteration, it.problems) for it in report.iterations if not it.passed
    ]
    assert len(report.iterations) == 4
    # Fault injection actually varied across the runs.
    fault_sigs = {tuple(sorted(it.faults.items())) for it in report.iterations}
    assert len(fault_sigs) > 1
    data = json.loads((tmp_path / "chaos.json").read_text())
    assert data["all_passed"] is True
    assert len(data["runs"]) == 4
    run = data["runs"][0]
    for key in ("iteration", "seed", "thread_id", "faults", "passed",
                "problems", "world_hash", "expected_hash", "duration",
                "rows_conserved", "refunds"):
        assert key in run, key


def test_chaos_cli_smoke(engine, schema, tmp_path):
    import os
    env = dict(os.environ)
    env["DATABASE_URL"] = env.get(
        "DATABASE_URL",
        "postgresql+psycopg://undolog:undolog@localhost:5432/undolog")
    out = tmp_path / "cli.json"
    proc = subprocess.run(
        [sys.executable, "-m", "undolog_torture.chaos",
         "--iterations", "2", "--seed", "7",
         "--json", str(out), "--schema", schema],
        capture_output=True, text=True, timeout=300, env=env,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    data = json.loads(out.read_text())
    assert data["all_passed"] and len(data["runs"]) == 2


def test_chaos_runner_detects_injected_failure(engine, tmp_path, monkeypatch):
    # Sabotage: make every refund fail forever -> rollbacks incomplete ->
    # the runner must report failure and non-zero exit.
    from undolog_torture import chaos as chaos_mod
    from undolog_torture.faults import FaultConfig
    orig = chaos_mod.FaultConfig.for_seed

    def broken_for_seed(seed):
        f = orig(seed)
        return FaultConfig(comp_fail_n=f.comp_fail_n, comp_fail_forever=True,
                           capture_timeout_pct=f.capture_timeout_pct,
                           replay_n=f.replay_n)
    monkeypatch.setattr(chaos_mod.FaultConfig, "for_seed", broken_for_seed)
    report = run_chaos(engine, iterations=1, seed=1)
    assert not report.all_passed
    assert report.iterations[0].problems
