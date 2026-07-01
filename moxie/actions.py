"""Action adapters — how an approved action actually gets carried out.

In the scaffold, execution is ALWAYS a dry-run: it returns the message that would
be sent so you can see exactly what Moxie would do. Real sending is a deliberate
TODO and must stay behind the Trust Vault (policy + approval + audit).
"""
from __future__ import annotations


def execute_action(action, dry_run: bool = True) -> dict:
    if dry_run:
        return {"sent": False, "dry_run": True, "channel": "email", "body": action.draft}
    # TODO: real delivery (SMTP / provider API) and, later, sandboxed merchant-portal
    # automation. Never enable without going through the Vault.
    raise NotImplementedError("Real sending is not implemented in the scaffold (by design).")
