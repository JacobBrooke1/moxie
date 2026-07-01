"""Explicit, fail-safe human approval for one-way actions.

If there's no interactive terminal (e.g. running unattended), approval is DECLINED
by default — Moxie never acts silently. Stdlib only.
"""
from __future__ import annotations

import sys


def request_approval(action, prompt_fn=input) -> bool:
    if not sys.stdin.isatty():
        # Fail-safe: no human present means no consent.
        return False

    print(f"\n  Action : {action.kind} @ {action.merchant}")
    print(f"  What   : {action.description}")
    if action.est_savings:
        print(f"  Saves  : ~{getattr(action, 'currency', '$')}{action.est_savings:.2f}")
    if action.draft:
        print("  Draft  :")
        for line in action.draft.splitlines():
            print(f"         | {line}")
    print("  Note   : this CANNOT be undone once sent.")
    try:
        answer = prompt_fn("  Approve? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer in ("y", "yes")
