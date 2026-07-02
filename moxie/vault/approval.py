"""Explicit, fail-safe human approval for one-way actions.

If there's no interactive terminal (e.g. running unattended), approval is DECLINED
by default — Moxie never acts silently. The prompt also lets you EDIT the draft
before approving, because it's your name on the email. Stdlib only.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile


def _edit_in_editor(text: str) -> str:
    """Open $EDITOR on the draft; fall back to line input if there isn't one."""
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
    if editor:
        with tempfile.NamedTemporaryFile("w+", suffix=".txt", delete=False,
                                         encoding="utf-8") as f:
            f.write(text)
            path = f.name
        try:
            subprocess.call([editor, path])
            with open(path, encoding="utf-8") as f:
                return f.read().strip()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
    print("  (no $EDITOR set — retype the draft; finish with a single '.' line)")
    lines = []
    while True:
        try:
            line = input("  > ")
        except (EOFError, KeyboardInterrupt):
            break
        if line.strip() == ".":
            break
        lines.append(line)
    return "\n".join(lines).strip() or text


def request_approval(action, prompt_fn=input, edit_fn=_edit_in_editor) -> bool:
    if not sys.stdin.isatty():
        # Fail-safe: no human present means no consent.
        return False

    while True:
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
            answer = prompt_fn("  Approve? [y/N/e=edit draft] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        if answer in ("e", "edit"):
            new_draft = edit_fn(action.draft)
            if new_draft:
                action.draft = new_draft
            continue  # show the (possibly edited) card again and re-ask
        return answer in ("y", "yes")
