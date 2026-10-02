"""undolog — the meta-package CLI.

`pip install undolog` gives you one command with two entry points:

    undolog demo                    # narrated corruption + rollback scenario
    undolog chaos --iterations 100  # the torture gate (seeded chaos runs)
"""
from .cli import main

__all__ = ["main"]
