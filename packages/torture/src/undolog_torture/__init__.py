"""undolog-torture: mock agent, mock APIs, fault injection, chaos runner."""
from .agent import DEFAULT_SEED, ScenarioResult, run_scenario

# Lazy chaos imports: eager ``from .chaos import ...`` here would put
# ``undolog_torture.chaos`` in sys.modules before ``python -m
# undolog_torture.chaos`` executes it (runpy double-import warning).
from .executors import (
    CrmExecutor,
    GmailExecutor,
    StripeExecutor,
    build_executors,
)
from .faults import (
    FaultConfig,
    FlakyCaptureAdapter,
    TimeoutError_,
    clear_env_faults,
    set_env_faults,
)
from .registry_ext import load_torture_registry
from .snapshot import canonical_state, hash_world, world_diff
from .verify import expected_post_rollback, verify_rollback_to_33
from .world import (
    CRMWorld,
    GmailWorld,
    StripeWorld,
    World,
    build_crm_app,
    build_gmail_app,
    build_stripe_app,
    build_worlds,
)

__all__ = [
    "DEFAULT_SEED",
    "ScenarioResult",
    "run_scenario",
    "ChaosIteration",
    "ChaosReport",
    "run_chaos",
    "CrmExecutor",
    "GmailExecutor",
    "StripeExecutor",
    "build_executors",
    "FaultConfig",
    "FlakyCaptureAdapter",
    "TimeoutError_",
    "clear_env_faults",
    "set_env_faults",
    "load_torture_registry",
    "canonical_state",
    "hash_world",
    "world_diff",
    "expected_post_rollback",
    "verify_rollback_to_33",
    "World",
    "build_worlds",
    "GmailWorld",
    "StripeWorld",
    "CRMWorld",
    "build_gmail_app",
    "build_stripe_app",
    "build_crm_app",
]


def __getattr__(name):
    if name in ("ChaosIteration", "ChaosReport", "run_chaos"):
        from . import chaos
        return getattr(chaos, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
