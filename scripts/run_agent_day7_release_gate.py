#!/usr/bin/env python3
"""Execute the exact semantic tests selected for the Day 7 beta release gate."""

from __future__ import annotations

import argparse
import subprocess
import sys

from agent_day7_gate_cases import (
    BETA_EVAL_CASES,
    CHAOS_DRILLS,
    PROMPT_INJECTION_DRILLS,
    TENANCY_DRILLS,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--section",
        choices=("all", "beta", "chaos", "prompt-injection", "tenancy"),
        default="all",
    )
    args = parser.parse_args(argv)
    cases = []
    if args.section in {"all", "beta"}:
        cases.extend(BETA_EVAL_CASES)
    if args.section in {"all", "chaos"}:
        cases.extend(CHAOS_DRILLS)
    if args.section in {"all", "prompt-injection"}:
        cases.extend(PROMPT_INJECTION_DRILLS)
    if args.section in {"all", "tenancy"}:
        cases.extend(TENANCY_DRILLS)
    # Execute each semantic target once even when several named requirements
    # intentionally map to one parameterized red-team or isolation test.
    nodeids = list(dict.fromkeys(case.nodeid for case in cases))
    command = [sys.executable, "-m", "pytest", "-q", *nodeids]
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
