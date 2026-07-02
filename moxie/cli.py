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
    from .secure import Cipher
    config = config or Config()
    config.home.mkdir(parents=True, exist_ok=True)
    store = Store(config.home / "moxie.db", cipher=Cipher.from_env())
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

    if config.kill_engaged:
        print("🛑 Kill switch engaged (~/.moxie/KILL) — everything stays a draft.\n")
    elif config.live:
        print("🔴 MOXIE_LIVE=true — approved actions WILL really send.\n")
    else:
        print("📝 Drafts mode (MOXIE_LIVE not set) — nothing will be sent.\n")

    results = Agent(config, store, audit).review(approve_fn=approve_fn)
    if not results:
        print("Nothing to review. Run  moxie scan  first.")
        return

    for action, outcome, note in results:
        icon = {"executed": "✅", "sent": "📮", "skipped": "⏭️ ",
                "denied": "🚫", "failed": "❌"}.get(outcome, "•")
        print(f"{icon} {outcome.upper()}: [{action.kind}] {action.merchant} — {note}")
    print("\nEvery step recorded. See  moxie log  /  moxie verify.")


def cmd_encrypt(args):
    from .secure import KEY_NAME, Cipher, generate_key, get_secret, keyring_available, set_secret
    config, store, audit = _ctx()

    if args.mode == "status":
        if get_secret(KEY_NAME):
            print("🔐 Encryption at rest: ON (key found). New writes are encrypted.")
        else:
            print("🔓 Encryption at rest: OFF — the local store is plaintext SQLite.\n"
                  "   Enable:  pip install \"moxie-agent[secure]\"  then  moxie encrypt on")
        return

    if args.mode == "on":
        if get_secret(KEY_NAME):
            print("Already on. (moxie encrypt status)")
            return
        try:
            key = generate_key()
        except RuntimeError as e:
            raise SystemExit(f"❌ {e}")
        if keyring_available():
            set_secret(KEY_NAME, key)
            where = "your OS keychain"
        else:
            from .dashboard import _update_env_file
            _update_env_file(config.home / ".env", {KEY_NAME: key})
            where = f"{config.home / '.env'} (install keyring to do better)"
        import os
        os.environ[KEY_NAME] = key
        cipher = Cipher(key)
        rows = store.reencrypt_all(cipher)
        from .providers import BankLink
        link = BankLink(config, cipher=cipher)
        state = BankLink(config, cipher=None).load()
        if state:
            link.save(state)
        audit.append("encryption_enabled", {"rows_migrated": rows})
        print(f"🔐 Encryption ON. Key stored in {where}.")
        print(f"   {rows} stored row(s) re-encrypted; bank tokens sealed.")
        print("   ⚠️ Losing the key means losing the data — back it up somewhere safe.")
        return

    raise SystemExit("usage: moxie encrypt on|status")


def cmd_secret(args):
    from .secure import get_secret, keyring_available, set_secret
    config, store, audit = _ctx()
    name = args.name.strip().upper()
    if args.action == "set":
        import getpass
        try:
            value = getpass.getpass(f"value for {name} (hidden): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\ncancelled")
            return
        if not value:
            print("nothing entered; nothing saved")
            return
        try:
            where = set_secret(name, value)
        except RuntimeError as e:
            raise SystemExit(f"❌ {e}")
        audit.append("secret_saved", {"name": name, "where": where})  # never the value
        print(f"✅ {name} stored in the {where}. Remove any copy from .env "
              "— the keychain now wins whenever .env doesn't set it.")
    else:  # check
        has = bool(get_secret(name))
        via_keyring = keyring_available()
        print(f"{name}: {'set' if has else 'NOT set'}"
              + ("" if via_keyring else "  (keyring not installed — env/.env only)"))


def cmd_kill(args):
    config, store, audit = _ctx()
    if args.release:
        if config.kill_path.exists():
            config.kill_path.unlink()
            audit.append("kill_switch", {"engaged": False})
            print("🟢 Kill switch released. MOXIE_LIVE (if set) is honoured again.")
        else:
            print("Kill switch wasn't engaged.")
        return
    config.kill_path.write_text("engaged\n", encoding="utf-8")
    audit.append("kill_switch", {"engaged": True})
    print("🛑 Kill switch ENGAGED — every action is drafts-only until you run "
          "`moxie kill --release`, whatever MOXIE_LIVE says.")


def cmd_ask(args):
    config, store, audit = _ctx()
    brain = Brain(config)
    if not brain.available:
        print("The brain needs a model: set MOXIE_API_KEY in .env (bring your own "
              "Anthropic key), or go fully offline with a local model via "
              "MOXIE_MODEL=ollama:llama3.1 (needs Ollama running), or set "
              "MOXIE_OFFLINE=true to acknowledge rules-only mode.")
        return
    from .snapshot import snapshot_from_store
    question = " ".join(args.question)
    audit.append("ask", {"question": question[:200]})
    print(brain.ask(question, store.load_transactions(), store.load_actions(),
                    snapshot=snapshot_from_store(store)))


def cmd_budget(args):
    from .snapshot import format_snapshot, snapshot_from_store
    config, store, audit = _ctx()
    if not store.load_transactions():
        print("No transactions on file yet. Run  moxie scan --csv/--pdf  or  moxie sync.")
        return
    print("🦡 The money picture (figures derived from your data — you decide):\n")
    print(format_snapshot(snapshot_from_store(store)))


def cmd_receipt(args):
    from .receipts import ingest_email_receipts, match_receipt, ocr_receipt
    config, store, audit = _ctx()

    if args.list or (not args.image and not args.email):
        receipts = store.load_receipts()
        if not receipts:
            print("No receipts filed yet.  moxie receipt photo.jpg  or  moxie receipt --email")
            return
        for r in receipts:
            print(f"  {r.date}  {r.merchant:24} {r.amount:>9.2f}  [{r.source}] {r.id}")
        return

    txns = store.load_transactions()
    new = []
    if args.image:
        try:
            new.append(ocr_receipt(args.image))
        except RuntimeError as e:
            raise SystemExit(f"❌ {e}")
    if args.email:
        try:
            new += ingest_email_receipts(config)
        except RuntimeError as e:
            raise SystemExit(f"❌ {e}")

    for r in new:
        store.save_receipt(r)
        match = match_receipt(r, txns)
        matched = f" → matches {match.merchant} on {match.date}" if match else ""
        audit.append("receipt_filed", {"merchant": r.merchant, "amount": r.amount,
                                       "source": r.source,
                                       "matched": bool(match)})
        print(f"🧾 Filed: {r.merchant} {r.amount:.2f} ({r.date}, {r.source}){matched}")
    if not new:
        print("Nothing new found.")
    else:
        print("\nReceipts become evidence: disputes attach them automatically.")


def cmd_connect(args):
    from .providers import BankLink, get_provider
    config, store, audit = _ctx()

    if getattr(args, "banks", False):
        provider = get_provider(args.provider, config)
        if not hasattr(provider, "list_banks"):
            print(f"{args.provider} doesn't need a bank id.")
            return
        for b in provider.list_banks():
            print(f"  {b['id']:32}  {b['name']}")
        return

    provider = get_provider(args.provider, config)
    link = BankLink(config)

    print(f"🏦 Linking via {provider.name} (read-only — Moxie can never move money).")
    started = provider.start_link()
    if started.get("error"):
        print(f"   {started['error']}")
        return
    print(f"\n   1. Open:  {started['url']}\n   2. {started['hint']}\n")
    try:
        code = input("   Paste code (or press Enter if none): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n   Cancelled — nothing linked.")
        return
    try:
        state = provider.complete_link(code, started.get("state", {}))
    except Exception as e:
        print(f"   ❌ Link failed: {e}")
        return
    link.save(state)
    audit.append("bank_linked", {"provider": provider.name,
                                 "accounts": len(state.get("accounts", []))})
    print(f"   ✅ Linked {len(state.get('accounts', []))} account(s). Syncing…")
    cmd_sync(args)


def cmd_sync(args):
    from .providers import sync
    config, store, audit = _ctx()
    out = sync(config, store, audit)
    if out.get("error"):
        print(f"❌ {out['error']}")
        raise SystemExit(1)
    print(f"✅ Synced {out['transactions']} transaction(s) from {out['provider']}.")
    for b in out.get("balances", []):
        cur = b.get("currency", "£")
        print(f"   {b['account']}: {cur}{b.get('current') if b.get('current') is not None else '?'}"
              + (f" ({cur}{b['available']} available)" if b.get("available") is not None else ""))
    print("Run  moxie scan  to check the fresh data for problems.")


def cmd_telegram(args):
    from .telegram import run_bot
    config, store, audit = _ctx()
    run_bot(config, store, audit)


def cmd_dashboard(args):
    from .dashboard import run_dashboard
    config, store, audit = _ctx()
    run_dashboard(config, store, audit, port=args.port)


def cmd_serve(args):
    from .serve import run_serve
    config, store, audit = _ctx()
    run_serve(config, store, audit, port=args.port)


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
    config, store, audit = _ctx()
    reg = SkillRegistry()
    for d in _skill_dirs(args, config):
        if Path(d).exists():
            reg.load_dir(d)
    if not reg.skills:
        print("No skills found. Add one at  skills/<name>/SKILL.md  — see skills/README.md.")
        return
    stats = store.skill_stats()
    print(f"{len(reg.skills)} skill(s):\n")
    for s in reg.skills:
        st = stats.get(s.name, {})
        used = (f"used {st['used']}×, {st.get('sent', 0)} sent"
                + (f", {st['failed']} failed" if st.get("failed") else "")
                ) if st else "unused"
        print(f"  • {s.name}  [{s.action_type or '?'} @ {s.merchant or '?'} "
              f"via {s.channel or 'email'}]  {used}")
    print("\nContribute know-how: skills/README.md — a folder + a SKILL.md is a PR.")


def cmd_doctor(args):
    config, store, audit = _ctx()
    print("🩺 Moxie doctor\n")

    v = sys.version_info
    ok_py = (v.major, v.minor) >= (3, 9)
    print(f"  [{'ok' if ok_py else ' X'}] Python {v.major}.{v.minor} (need ≥3.9; 3.11 recommended)")
    print(f"  [ok] Home: {config.home}")

    if config.model.lower().startswith("ollama:"):
        print(f"  [ok] Brain: local Ollama model ({config.model}) — fully offline")
    elif config.offline:
        print("  [ok] LLM mode: offline (rules only — or set MOXIE_MODEL=ollama:… "
              "for a local brain)")
    elif config.api_key:
        print(f"  [ok] Brain: MOXIE_API_KEY set (model: {config.model})")
    else:
        print("  [ !] Brain: no MOXIE_API_KEY and not offline — `moxie ask` and Telegram "
              "questions won't work; set a key, or MOXIE_MODEL=ollama:llama3.1, "
              "or MOXIE_OFFLINE=true")

    if config.telegram_token:
        paired = config.telegram_chat_id or "not paired — message the bot for your chat id"
        print(f"  [ok] Telegram: token set (chat: {paired})")
    else:
        print("  [ -] Telegram: no TELEGRAM_BOT_TOKEN (optional — see README)")

    if config.kill_engaged:
        print("  [ !] Actions: KILL SWITCH engaged — drafts only (moxie kill --release)")
    elif config.live:
        smtp_ok = all(config.smtp.get(k) for k in ("host", "user", "password"))
        print("  [ok] Actions: LIVE — approved actions really send"
              + ("" if smtp_ok else "  (but SMTP isn't configured; email sends will refuse)"))
    else:
        print("  [ok] Actions: drafts only (set MOXIE_LIVE=true to send for real)")

    from .secure import KEY_NAME, get_secret, keyring_available
    enc = bool(get_secret(KEY_NAME))
    print(f"  [{'ok' if enc else ' -'}] Encryption at rest: "
          + ("ON" if enc else "off (moxie encrypt on — needs [secure] extra)"))
    print(f"  [{'ok' if keyring_available() else ' -'}] OS keychain: "
          + ("available (moxie secret set NAME)" if keyring_available()
             else "not installed (secrets live in .env; pip install \"moxie-agent[secure]\")"))

    from .providers import BankLink
    bank = BankLink(config).status()
    if bank["linked"]:
        left = bank.get("consent_days_left")
        if bank.get("needs_reauth"):
            print(f"  [ !] Bank: {bank['provider']} consent EXPIRED — run "
                  f"`moxie connect {bank['provider']}` to re-consent")
        else:
            extra = f", consent ~{left}d left" if left is not None else ""
            print(f"  [ok] Bank: {bank['provider']} linked "
                  f"({bank['accounts']} account(s){extra})")
    else:
        print("  [ -] Bank: not linked (optional — `moxie connect truelayer` "
              "or stay no-cloud with --csv/--pdf)")

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

    p = sub.add_parser("kill", help="engage the kill switch (force drafts-only)")
    p.add_argument("--release", action="store_true", help="release the kill switch")
    p.set_defaults(func=cmd_kill)

    p = sub.add_parser("encrypt", help="encrypt the local store at rest (needs [secure] extra)")
    p.add_argument("mode", choices=["on", "status"], help="turn on, or show status")
    p.set_defaults(func=cmd_encrypt)

    p = sub.add_parser("secret", help="keep secrets in the OS keychain instead of .env")
    p.add_argument("action", choices=["set", "check"])
    p.add_argument("name", help="e.g. MOXIE_API_KEY, TELEGRAM_BOT_TOKEN, MOXIE_SMTP_PASSWORD")
    p.set_defaults(func=cmd_secret)

    p = sub.add_parser("connect", help="link a bank read-only via a provider you choose")
    p.add_argument("provider", help="truelayer (UK default) | gocardless (free tier) | plaid")
    p.add_argument("--banks", action="store_true",
                   help="list bank institution ids (gocardless)")
    p.set_defaults(func=cmd_connect)

    p = sub.add_parser("sync", help="pull fresh transactions + balances from the linked bank")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("ask", help="ask the brain a money question (needs MOXIE_API_KEY)")
    p.add_argument("question", nargs="+", help='e.g.  moxie ask "can I afford £120 trainers?"')
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("budget", help="this month's money picture: in / out / left")
    p.set_defaults(func=cmd_budget)

    p = sub.add_parser("receipt", help="file receipts: photo OCR (local) or email scan (read-only)")
    p.add_argument("image", nargs="?", help="photo of a paper receipt (needs [ocr] extra)")
    p.add_argument("--email", action="store_true",
                   help="scan your mailbox read-only via IMAP (MOXIE_IMAP_* in .env)")
    p.add_argument("--list", action="store_true", help="list filed receipts")
    p.set_defaults(func=cmd_receipt)

    p = sub.add_parser("telegram", help="run the Telegram bot + daily loop (needs TELEGRAM_BOT_TOKEN)")
    p.set_defaults(func=cmd_telegram)

    p = sub.add_parser("dashboard", help="run Moxie Dash, the local control panel (127.0.0.1)")
    p.add_argument("--port", type=int, default=8484)
    p.set_defaults(func=cmd_dashboard)

    p = sub.add_parser("serve", help="run everything 24/7: dashboard + Telegram + daily loop")
    p.add_argument("--port", type=int, default=8484)
    p.set_defaults(func=cmd_serve)

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
