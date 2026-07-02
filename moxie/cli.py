"""Moxie command-line interface.

Commands: init · scan · review · ask · telegram · log · verify · skills · doctor
The demo runs on bundled sample data with no external dependencies.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

from . import __version__
from .agent import Agent
from .brain import Brain, ensure_instructions
from .config import Config
from .connectors import import_csv
from .sampledata import sample_receipts, sample_transactions
from .skills import SkillRegistry
from .storage import Store
from .vault import AuditLog


def _ctx(config=None):
    config = config or Config()
    config.home.mkdir(parents=True, exist_ok=True)
    store = Store(config.home / "moxie.db")
    audit = AuditLog(config.home / "audit.log")
    return config, store, audit


def _skill_dirs(args, config):
    """Look in the repo's ./skills and the user's workspace skills (OpenClaw-style)."""
    env_dir = os.environ.get("MOXIE_SKILLS")
    dirs = [env_dir or getattr(args, "dir", "skills"), str(config.home / "workspace" / "skills")]
    return [d for d in dirs if d]


def cmd_init(args):
    config = Config()
    config.save()
    config, store, audit = _ctx(config)
    (config.home / "workspace" / "skills").mkdir(parents=True, exist_ok=True)
    for receipt in sample_receipts():
        store.save_receipt(receipt)
    instructions = ensure_instructions(config)
    audit.append("init", {"home": str(config.home)})
    print(f"🦡 Moxie initialized at {config.home}")
    print(f"   Standing instructions: {instructions}  (edit me!)")
    print("Next:  moxie scan   then   moxie review")


def cmd_scan(args):
    config, store, audit = _ctx()
    if getattr(args, "pdf", None):
        from .statements import import_pdf
        txns, source = import_pdf(args.pdf), args.pdf
    elif args.csv:
        txns, source = import_csv(args.csv), args.csv
    else:
        txns, source = sample_transactions(), "built-in sample data"

    store.save_transactions(txns)
    agent = Agent(config, store, audit)
    actions = agent.scan(txns)
    print(f"Scanned {len(txns)} transactions from {source}.\n")
    if agent.last_suppressed:
        print(f"({agent.last_suppressed} finding(s) suppressed — you already decided; "
              f"they'll stay quiet for a while.)\n")
    if not actions:
        print("✅ Nothing to fix. Nice.")
        return

    total = sum(a.est_savings for a in actions)
    cur = getattr(actions[0], "currency", "$")
    print(f"Found {len(actions)} thing(s) worth ~{cur}{total:.2f}/yr:\n")
    for a in actions:
        print(f"  • [{a.kind}] {a.description}  (~{cur}{a.est_savings:.2f})")
    print("\nRun  moxie review  to act on these — you approve each one.")


def cmd_review(args):
    config, store, audit = _ctx()
    approve_fn = (lambda a: True) if args.yes else None
    if args.yes:
        print("⚠️  --yes: auto-approving every action (demo/testing only).\n")

    results = Agent(config, store, audit).review(approve_fn=approve_fn)
    if not results:
        print("Nothing to review. Run  moxie scan  first.")
        return

    for action, outcome, note in results:
        icon = {"executed": "✅", "skipped": "⏭️ ", "denied": "🚫"}.get(outcome, "•")
        print(f"{icon} {outcome.upper()}: [{action.kind}] {action.merchant} — {note}")
    print("\nEvery step recorded. See  moxie log  /  moxie verify.")


def cmd_ask(args):
    config, store, audit = _ctx()
    brain = Brain(config)
    if not brain.available:
        print("The brain needs an API key: set MOXIE_API_KEY in your environment or a "
              ".env file (bring your own Anthropic key), or set MOXIE_OFFLINE=true to "
              "acknowledge rules-only mode.")
        return
    question = " ".join(args.question)
    audit.append("ask", {"question": question[:200]})
    print(brain.ask(question, store.load_transactions(), store.load_actions()))


def cmd_telegram(args):
    from .telegram import run_bot
    config, store, audit = _ctx()
    run_bot(config, store, audit)


def cmd_log(args):
    config, store, audit = _ctx()
    entries = audit.entries()
    if not entries:
        print("Audit log is empty. Run  moxie scan / review  first.")
        return
    for e in entries:
        ts = dt.datetime.fromtimestamp(e["ts"]).strftime("%Y-%m-%d %H:%M:%S")
        print(f"{ts}  {e['event']:16}  {e['hash'][:10]}…  {e['data']}")


def cmd_verify(args):
    config, store, audit = _ctx()
    ok, bad = audit.verify()
    if ok:
        print(f"✅ Audit log intact — {len(audit.entries())} entries, hash chain verified.")
    else:
        print(f"❌ Audit log TAMPERED at entry #{bad} — chain broken.")
        raise SystemExit(1)


def cmd_skills(args):
    config = Config()
    reg = SkillRegistry()
    for d in _skill_dirs(args, config):
        if Path(d).exists():
            reg.load_dir(d)
    if not reg.skills:
        print("No skills found. Add one at  skills/<name>/SKILL.md  — see CONTRIBUTING.md.")
        return
    print(f"{len(reg.skills)} skill(s):\n")
    for s in reg.skills:
        rate = f"{s.success_rate:.0%}" if s.success_rate else "—"
        print(f"  • {s.name}  [{s.action_type or '?'} @ {s.merchant or '?'}]  success≈{rate}")


def cmd_doctor(args):
    config, store, audit = _ctx()
    print("🩺 Moxie doctor\n")

    v = sys.version_info
    ok_py = (v.major, v.minor) >= (3, 9)
    print(f"  [{'ok' if ok_py else ' X'}] Python {v.major}.{v.minor} (need ≥3.9; 3.11 recommended)")
    print(f"  [ok] Home: {config.home}")

    if config.offline:
        print("  [ok] LLM mode: offline / local model")
    elif config.api_key:
        print(f"  [ok] Brain: MOXIE_API_KEY set (model: {config.model})")
    else:
        print("  [ !] Brain: no MOXIE_API_KEY and not offline — `moxie ask` and Telegram "
              "questions won't work; set a key or MOXIE_OFFLINE=true")

    if config.telegram_token:
        paired = config.telegram_chat_id or "not paired — message the bot for your chat id"
        print(f"  [ok] Telegram: token set (chat: {paired})")
    else:
        print("  [ -] Telegram: no TELEGRAM_BOT_TOKEN (optional — see README)")

    txns = store.load_transactions()
    print(f"  [{'ok' if txns else ' -'}] Transactions on file: {len(txns)}")

    ok, bad = audit.verify()
    print(f"  [ok] Audit log intact ({len(audit.entries())} entries)" if ok
          else f"  [ X] Audit log TAMPERED at entry #{bad}")

    reg = SkillRegistry()
    for d in _skill_dirs(args, config):
        if Path(d).exists():
            reg.load_dir(d)
    print(f"  [ok] Skills loaded: {len(reg.skills)}")
    print("\nDone.")


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="moxie",
        description="The open-source money agent that acts only with your approval.",
    )
    parser.add_argument("--version", action="version", version=f"moxie {__version__}")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("init", help="set up your local ~/.moxie")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("scan", help="find issues in your transactions")
    p.add_argument("--csv", help="import a bank CSV export (headers auto-detected)")
    p.add_argument("--pdf", help="import a bank statement PDF (NatWest-style; needs pypdf)")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("review", help="approve or skip each proposed fix")
    p.add_argument("--yes", action="store_true", help="auto-approve everything (demo/testing only)")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("ask", help="ask the brain a money question (needs MOXIE_API_KEY)")
    p.add_argument("question", nargs="+", help='e.g.  moxie ask "can I afford £120 trainers?"')
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("telegram", help="run the Telegram bot + daily loop (needs TELEGRAM_BOT_TOKEN)")
    p.set_defaults(func=cmd_telegram)

    p = sub.add_parser("log", help="show the tamper-evident audit log")
    p.set_defaults(func=cmd_log)

    p = sub.add_parser("verify", help="verify the audit log hasn't been altered")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("skills", help="list installed community skills (SKILL.md)")
    p.add_argument("--dir", default="skills", help="skills directory (default: ./skills)")
    p.set_defaults(func=cmd_skills)

    p = sub.add_parser("doctor", help="diagnose your setup")
    p.add_argument("--dir", default="skills", help="skills directory (default: ./skills)")
    p.set_defaults(func=cmd_doctor)

    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return
    args.func(args)


if __name__ == "__main__":
    main()
