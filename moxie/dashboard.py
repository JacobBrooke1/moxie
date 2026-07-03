"""Moxie Dash -- the local control plane. Stdlib only.

    moxie dashboard          # -> http://127.0.0.1:8484

The dashboard is Moxie's *trusted surface* (think OpenClaw's Claw Dash or the
Hermes status page, but money-shaped):

- heartbeat, brain, Telegram, and data status at a glance
- findings with approve / skip (same Trust Vault pipeline, channel="dashboard")
- SENSITIVE SETUP LIVES HERE: API keys and Telegram pairing are entered on
  this page and written to ~/.moxie/.env -- never over chat
- guided Telegram setup: paste the BotFather token, message your bot once,
  click "detect" and Moxie finds your chat id for pairing

Security: binds to 127.0.0.1 ONLY. If Moxie runs on a remote box (a VPS, a
Mac mini in a cupboard), reach the dashboard through an SSH tunnel:
    ssh -L 8484:127.0.0.1:8484 you@your-box
Do not expose it to the open internet; it holds your keys.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__
from .agent import Agent
from .brain import Brain

ENV_KEYS = ("MOXIE_API_KEY", "TELEGRAM_BOT_TOKEN", "MOXIE_TELEGRAM_CHAT_ID")


def _update_env_file(path, updates: dict) -> None:
    """Rewrite KEY=value lines in ~/.moxie/.env, preserving everything else."""
    lines = []
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    seen = set()
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else None
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


class Dash:
    """State + API logic, separated from HTTP plumbing for testability.

    `brain_transport` / `provider_transport` are injectable fakes for tests —
    the same pattern as everywhere else in Moxie."""

    def __init__(self, config, store, audit, brain_transport=None,
                 provider_transport=None):
        self.config = config
        self.store = store
        self.audit = audit
        self.agent = Agent(config, store, audit)
        self._brain_transport = brain_transport
        self._provider_transport = provider_transport

    # ---- status ------------------------------------------------------------
    def status(self) -> dict:
        ok, bad = self.audit.verify()
        entries = self.audit.entries()
        proposed = [a for a in self.store.load_actions() if a.status == "proposed"]
        cur = getattr(proposed[0], "currency", "£") if proposed else "£"
        return {
            "version": __version__,
            "home": str(self.config.home),
            "heartbeat": {
                "alive": True,
                "last_event": entries[-1]["event"] if entries else None,
                "last_event_ts": entries[-1]["ts"] if entries else None,
                "last_daily_scan": self.store.get_meta("last_auto_scan"),
            },
            "brain": {
                "ready": Brain(self.config).available,
                "model": self.config.model,
                "offline": self.config.offline,
            },
            "telegram": {
                "token_set": bool(self.config.telegram_token),
                "paired_chat": self.config.telegram_chat_id,
            },
            "data": {
                "transactions": len(self.store.load_transactions()),
                "findings": len(proposed),
                "est_savings": round(sum(a.est_savings for a in proposed), 2),
                "currency": cur,
            },
            "audit": {"intact": ok, "entries": len(entries), "first_bad": bad},
            "actions": {
                "live": self.config.live,
                "kill": self.config.kill_engaged,
                "mode": ("kill" if self.config.kill_engaged
                         else "live" if self.config.live else "drafts"),
            },
            "bank": self._bank_status(),
            "money": self._money(),
            "setup": self.setup_state(),
        }

    # ---- onboarding (the front door) ----------------------------------------
    def setup_state(self) -> dict:
        """What the first-run wizard needs to know: what's configured, and
        whether the user has finished/skipped the wizard."""
        return {
            "brain_ready": Brain(self.config).available,
            "has_data": bool(self.store.load_transactions()),
            "telegram_paired": bool(self.config.telegram_chat_id),
            "wizard_done": self.store.get_meta("wizard_done") == "1",
        }

    def wizard_done(self) -> dict:
        self.store.set_meta("wizard_done", "1")
        self.audit.append("wizard_done", {})
        return {"ok": True}

    def brain_test(self) -> dict:
        """One tiny live call to prove the key/model works — the wizard's
        'test my key' button. Uses the injectable transport in tests."""
        brain = Brain(self.config, transport=self._brain_transport)
        if not brain.available:
            return {"ok": False,
                    "error": "no key saved yet (and no local Ollama model configured)"}
        try:
            reply = brain.ask("Reply with the single word: ready", [], [])
        except Exception as e:
            return {"ok": False, "error": f"the key didn't work: {e}"}
        self.audit.append("brain_tested", {"ok": True})
        return {"ok": True, "model": self.config.model,
                "reply": (reply or "")[:80]}

    # ---- chat (advises and navigates; NEVER executes) ------------------------
    def chat(self, message: str) -> dict:
        """Talk to Moxie about your money, grounded in your real data. The
        brain has no execute path — when the user wants to act, we point at
        the matching finding and the Vault's approval modal takes over."""
        message = (message or "").strip()
        if not message:
            return {"error": "say something"}
        brain = Brain(self.config, transport=self._brain_transport)
        if not brain.available:
            return {"error": "the brain isn't connected yet — add your API key "
                             "in Setup (or configure a local Ollama model)"}
        from .snapshot import snapshot_from_store
        txns = self.store.load_transactions()
        actions = self.store.load_actions()
        history = self.store.load_chat(limit=12)
        try:
            reply = brain.converse(message, history, txns, actions,
                                   snapshot=snapshot_from_store(self.store))
        except Exception as e:
            return {"error": f"the brain call failed: {e}"}
        self.store.save_chat("user", message)
        self.store.save_chat("assistant", reply)
        self.audit.append("dashboard_chat", {"chars": len(message)})

        # If the exchange mentions a live finding, offer the review button —
        # the human acts through the Vault, never through chat.
        blob = (message + " " + reply).lower()
        related = [
            {"id": a.id, "merchant": a.merchant, "description": a.description}
            for a in actions
            if a.status == "proposed" and a.merchant.lower() in blob
        ][:4]
        return {"reply": reply, "related": related}

    def chat_history(self) -> list:
        return self.store.load_chat(limit=40)

    def import_csv_text(self, name: str, text: str) -> dict:
        """In-browser CSV import: the file is read client-side and its text
        POSTed here — it never needs to touch disk."""
        from .connectors import import_csv_text
        if not (text or "").strip():
            return {"error": "empty file"}
        try:
            txns = import_csv_text(text)
        except ValueError as e:
            return {"error": str(e)}
        if not txns:
            return {"error": "no transactions recognised in that CSV"}
        self.store.save_transactions(txns)
        actions = self.agent.scan(txns)
        self.audit.append("csv_imported", {"name": (name or "upload.csv")[:80],
                                           "transactions": len(txns)})
        return {"transactions": len(txns), "found": len(actions),
                "suppressed": self.agent.last_suppressed}

    def load_sample_data(self) -> dict:
        """The zero-risk demo: bundled fictional data, same pipeline."""
        from .sampledata import sample_receipts, sample_transactions
        txns = sample_transactions()
        self.store.save_transactions(txns)
        for r in sample_receipts():
            self.store.save_receipt(r)
        actions = self.agent.scan(txns)
        self.audit.append("sample_data_loaded", {"transactions": len(txns)})
        return {"transactions": len(txns), "found": len(actions), "sample": True}

    def _money(self) -> "dict | None":
        from .snapshot import snapshot_from_store
        if not self.store.load_transactions():
            return None
        s = snapshot_from_store(self.store)
        return {"currency": s["currency"], "income": s["monthly_income"],
                "spent": s["spent_this_month"], "left": s["left_this_month"],
                "committed": s["committed"], "balance": s["balance"],
                "month": s["month"]}

    def _bank_status(self) -> dict:
        from .providers import BankLink
        status = BankLink(self.config).status()
        status["last_sync"] = self.store.get_meta("last_bank_sync")
        return status

    def bank_sync(self) -> dict:
        from .providers import sync
        return sync(self.config, self.store, self.audit,
                    transport=self._provider_transport)

    def bank_start(self, provider_name: str) -> dict:
        """Begin a read-only bank link from the browser: returns the consent
        URL to open and remembers the in-flight state for bank_complete."""
        from .providers import get_provider
        try:
            provider = get_provider(provider_name, self.config,
                                    transport=self._provider_transport)
        except ValueError as e:
            return {"error": str(e)}
        try:
            started = provider.start_link()
        except Exception as e:
            return {"error": f"couldn't start the link: {e}"}
        if started.get("error"):
            return started
        self._pending_link = {"provider": provider.name,
                              "state": started.get("state", {})}
        return {"provider": provider.name, "url": started.get("url", ""),
                "hint": started.get("hint", "")}

    def bank_complete(self, code: str) -> dict:
        """Exchange the consent result, persist the link, and sync once —
        the same flow as `moxie connect`, minus the terminal."""
        from .providers import BankLink, get_provider
        pending = getattr(self, "_pending_link", None)
        if not pending:
            return {"error": "start a bank link first (pick a provider and connect)"}
        provider = get_provider(pending["provider"], self.config,
                                transport=self._provider_transport)
        try:
            state = provider.complete_link(code or "", pending["state"])
        except Exception as e:
            return {"error": f"link failed: {e}"}
        BankLink(self.config).save(state)
        self.audit.append("bank_linked", {"provider": provider.name,
                                          "accounts": len(state.get("accounts", []))})
        self._pending_link = None
        out = self.bank_sync()
        out["linked"] = True
        out["provider"] = provider.name
        return out

    def findings(self) -> list:
        actions = [a for a in self.store.load_actions() if a.status == "proposed"]
        actions.sort(key=lambda a: (-a.est_savings, a.merchant))
        return [
            {"id": a.id, "kind": a.kind, "merchant": a.merchant,
             "description": a.description, "est_savings": a.est_savings,
             "currency": getattr(a, "currency", "£"), "draft": a.draft}
            for a in actions
        ]

    def resolve(self, action_id: str, approved: bool, edited_draft=None) -> dict:
        result = self.agent.resolve(action_id, approved, channel="dashboard",
                                    edited_draft=edited_draft)
        if not result:
            return {"error": "not found or already handled"}
        action, outcome, note = result
        return {"merchant": action.merchant, "outcome": outcome, "note": note,
                "reference": getattr(action, "reference", "")}

    def rescan(self) -> dict:
        txns = self.store.load_transactions()
        if not txns:
            return {"error": "no transactions on file — run moxie scan --csv/--pdf first"}
        actions = self.agent.scan(txns)
        return {"found": len(actions), "suppressed": self.agent.last_suppressed}

    def save_setup(self, form: dict) -> dict:
        import os
        updates = {k: v.strip() for k, v in form.items() if k in ENV_KEYS and v and v.strip()}
        if not updates:
            return {"error": "nothing to save"}
        _update_env_file(self.config.home / ".env", updates)
        os.environ.update(updates)          # take effect without restart
        self.audit.append("setup_saved", {"keys": sorted(updates)})  # names only, never values
        return {"saved": sorted(updates)}

    def detect_chat(self) -> dict:
        """Guided pairing: after you message your bot once, find your chat id."""
        token = self.config.telegram_token
        if not token:
            return {"error": "save a TELEGRAM_BOT_TOKEN first"}
        try:
            with urllib.request.urlopen(
                f"https://api.telegram.org/bot{token}/getUpdates", timeout=15
            ) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            return {"error": f"couldn't reach Telegram: {e}"}
        chats = {}
        for u in data.get("result", []):
            chat = (u.get("message") or {}).get("chat") or {}
            if chat.get("id"):
                chats[chat["id"]] = chat.get("first_name") or chat.get("title") or "?"
        if not chats:
            return {"error": "no messages yet — open Telegram and send your bot any message, then retry"}
        chat_id, name = list(chats.items())[-1]
        return {"chat_id": str(chat_id), "name": name}


PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Moxie Dash</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root { --bg:#0d1117; --card:#161b22; --line:#30363d; --fg:#e6edf3;
          --dim:#8b949e; --orange:#e8862e; --green:#3fb950; --red:#f85149; }
  * { box-sizing:border-box; margin:0; }
  body { background:var(--bg); color:var(--fg);
         font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; padding:28px; }
  h1 { font-size:22px; margin-bottom:4px; } h1 span{color:var(--orange);}
  .sub { color:var(--dim); margin-bottom:24px; font-size:13px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr));
          gap:14px; margin-bottom:26px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px; }
  .card h3 { font-size:12px; text-transform:uppercase; letter-spacing:.08em;
             color:var(--dim); margin-bottom:8px; }
  .big { font-size:20px; font-weight:600; }
  .ok{color:var(--green)} .warn{color:var(--orange)} .bad{color:var(--red)}
  .muted{color:var(--dim); font-size:13px;}
  h2 { font-size:15px; margin:26px 0 10px; color:var(--orange); }
  table { width:100%; border-collapse:collapse; }
  td { padding:9px 10px; border-top:1px solid var(--line); vertical-align:top; }
  button { background:transparent; border:1px solid var(--line); color:var(--fg);
           border-radius:7px; padding:5px 12px; cursor:pointer; font-size:13px; }
  button:hover { border-color:var(--orange); color:var(--orange); }
  button.primary { background:var(--orange); border-color:var(--orange); color:#0d1117; font-weight:600; }
  input { background:var(--bg); border:1px solid var(--line); color:var(--fg);
          border-radius:7px; padding:7px 10px; width:100%; font-size:13px; }
  label { font-size:12px; color:var(--dim); display:block; margin:10px 0 4px; }
  .setup { display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:14px; }
  #toast { position:fixed; bottom:20px; right:20px; background:var(--card);
           border:1px solid var(--orange); border-radius:8px; padding:10px 16px;
           display:none; font-size:13px; }
  .note { font-size:12px; color:var(--dim); margin-top:16px; }
  .wstep { border-top:1px solid var(--line); padding:16px 0; }
  .wstep h3 { font-size:14px; text-transform:none; letter-spacing:0;
              color:var(--fg); margin-bottom:6px; }
  .tick { display:inline-flex; align-items:center; justify-content:center;
          width:22px; height:22px; border-radius:50%; border:1px solid var(--line);
          font-size:12px; margin-right:8px; color:var(--dim); }
  .tick.done { background:var(--green); border-color:var(--green);
               color:#0d1117; font-weight:700; }
  .filebtn { display:inline-block; border:1px solid var(--line); border-radius:7px;
             padding:5px 12px; cursor:pointer; font-size:13px; }
  .filebtn:hover { border-color:var(--orange); color:var(--orange); }
</style></head><body>
<h1>🦡 <span>Moxie</span> Dash</h1>
<div class="sub">The trusted surface. Keys and pairing live here — never over chat. · v<span id="ver"></span></div>

<div id="wizard" style="display:none; max-width:720px; margin:10px auto 30px;">
  <div class="card" style="padding:22px">
    <h2 style="margin:0 0 6px">Welcome. Three steps and Moxie is yours.</h2>
    <div class="muted" style="margin-bottom:18px">Everything below stays on this
      machine — keys go in <code>~/.moxie</code>, never to any server of ours.</div>

    <div class="wstep" id="w1">
      <h3><span class="tick" id="w1t">1</span> Connect your Claude API key</h3>
      <div class="muted">Get one at console.anthropic.com → API keys. This powers
        the brain: triage, chat, "can I afford this?".</div>
      <label>Anthropic API key</label>
      <input id="w_api" type="password" placeholder="sk-ant-…">
      <div style="margin-top:8px">
        <button class="primary" onclick="wizKey()">Save & test key</button>
        <span class="muted" id="w1msg"></span>
      </div>
      <div class="muted" style="margin-top:6px">No key? Moxie also runs a local model
        (<code>MOXIE_MODEL=ollama:llama3.1</code>) or rules-only — you can skip this.</div>
    </div>

    <div class="wstep" id="w2">
      <h3><span class="tick" id="w2t">2</span> Get your transactions in</h3>
      <div class="muted">Read-only, local. Pick whichever you like:</div>
      <div style="margin-top:8px; display:flex; gap:8px; flex-wrap:wrap; align-items:center">
        <label class="filebtn"><input type="file" id="w_csv" accept=".csv"
          style="display:none" onchange="wizCsv(this)">Import a bank CSV…</label>
        <button onclick="wizDemo()">Try with sample data</button>
        <span class="muted" id="w2msg"></span>
      </div>
      <div class="muted" style="margin-top:6px">Your file is read in the browser and
        parsed locally — it isn't uploaded anywhere. Bank statements export CSV from
        your online banking. (Live bank linking: the Bank card, after setup.)</div>
    </div>

    <div class="wstep" id="w3">
      <h3><span class="tick" id="w3t">3</span> Telegram — optional, but great</h3>
      <div class="muted">Text Moxie like a PA and approve findings from your phone.
        Make a bot with @BotFather, paste its token:</div>
      <label>Bot token</label>
      <input id="w_tok" type="password" placeholder="123456:ABC…">
      <div style="margin-top:8px; display:flex; gap:8px; flex-wrap:wrap">
        <button onclick="wizTok()">Save token</button>
        <button onclick="wizDetect()">Detect my chat id</button>
        <button class="primary" id="w_pair" style="display:none"
          onclick="wizPair()">Pair this chat</button>
      </div>
      <div class="muted" id="w3msg" style="margin-top:6px">After saving, message your
        bot anything, then click detect.</div>
    </div>

    <div style="margin-top:18px; display:flex; gap:10px; align-items:center">
      <button class="primary" onclick="wizFinish()">Finish setup →</button>
      <a href="#" class="muted" onclick="wizFinish(); return false">skip for now</a>
    </div>
  </div>
</div>

<div id="main">
<div class="grid">
  <div class="card"><h3>Heartbeat</h3><div class="big ok" id="hb">●</div>
      <div class="muted" id="hb2"></div></div>
  <div class="card"><h3>Brain</h3><div class="big" id="brain"></div>
      <div class="muted" id="brain2"></div></div>
  <div class="card"><h3>Telegram</h3><div class="big" id="tg"></div>
      <div class="muted" id="tg2"></div></div>
  <div class="card"><h3>Data</h3><div class="big" id="data"></div>
      <div class="muted" id="data2"></div></div>
  <div class="card"><h3>Audit trail</h3><div class="big" id="audit"></div>
      <div class="muted" id="audit2"></div></div>
  <div class="card"><h3>Mode</h3><div class="big" id="mode"></div>
      <div class="muted" id="mode2"></div></div>
  <div class="card"><h3>Bank</h3><div class="big" id="bank"></div>
      <div class="muted" id="bank2"></div>
      <div style="margin-top:8px">
        <button onclick="syncBank()">sync now</button>
        <button onclick="toggleBankLink()">connect…</button>
      </div>
      <div id="bank_link_ui" style="display:none; margin-top:10px">
        <label>Provider (read-only access — Moxie can never move money)</label>
        <select id="bank_provider" style="width:100%; background:var(--bg); color:var(--fg);
                border:1px solid var(--line); border-radius:7px; padding:7px 10px; font-size:13px">
          <option value="truelayer">TrueLayer — UK default (free sandbox)</option>
          <option value="gocardless">GoCardless — most generous free tier</option>
          <option value="plaid">Plaid — strong US coverage</option>
        </select>
        <div style="margin-top:8px"><button class="primary" onclick="bankStart()">Start link</button></div>
        <div class="muted" id="bank_hint" style="margin-top:8px"></div>
        <div id="bank_step2" style="display:none">
          <label>Code from the redirect page (GoCardless/Plaid: leave blank)</label>
          <input id="bank_code" placeholder="paste code…">
          <div style="margin-top:8px"><button class="primary" onclick="bankComplete()">Complete link</button></div>
        </div>
      </div></div>
  <div class="card"><h3>This month</h3><div class="big" id="money"></div>
      <div class="muted" id="money2"></div></div>
</div>

<h2>Chat with Moxie</h2>
<div class="card" style="padding:0">
  <div id="chatlog" style="max-height:340px; overflow-y:auto; padding:14px;"></div>
  <div style="display:flex; gap:8px; padding:10px 14px; border-top:1px solid var(--line)">
    <input id="chatbox" placeholder='Ask about your money — "what should I cancel?" · "can I afford £120 trainers?"'
           onkeydown="if(event.key==='Enter') sendChat()">
    <button class="primary" onclick="sendChat()" style="white-space:nowrap">Send</button>
  </div>
  <div class="muted" style="padding:0 14px 10px">Moxie advises here; it never acts from chat.
    Anything worth doing routes you to Findings, where you approve it.</div>
</div>

<h2 id="findings_h">Findings <button onclick="rescan()" style="margin-left:8px">re-scan</button></h2>
<table id="findings"></table>

<h2>Setup</h2>
<div class="setup">
  <div class="card">
    <h3>Brain — bring your own key</h3>
    <label>Anthropic API key (console.anthropic.com)</label>
    <input id="k_api" type="password" placeholder="sk-ant-…">
    <div style="margin-top:12px"><button class="primary" onclick="save({MOXIE_API_KEY:val('k_api')})">Save key</button></div>
  </div>
  <div class="card">
    <h3>Telegram — pair your chat</h3>
    <label>1 · Bot token from @BotFather</label>
    <input id="k_tok" type="password" placeholder="123456:ABC…">
    <div style="margin-top:8px"><button onclick="save({TELEGRAM_BOT_TOKEN:val('k_tok')})">Save token</button></div>
    <label>2 · Message your bot anything, then:</label>
    <button onclick="detect()">Detect my chat id</button>
    <span class="muted" id="detected"></span>
    <div style="margin-top:8px"><button class="primary" id="pairbtn" style="display:none"
      onclick="save({MOXIE_TELEGRAM_CHAT_ID:window._chat})">Pair this chat</button></div>
    <div class="muted" style="margin-top:8px">3 · Run <code>moxie telegram</code> and text your PA.</div>
  </div>
</div>

<div class="note">Dashboard binds to 127.0.0.1 only. On a remote host use an SSH tunnel:
<code>ssh -L 8484:127.0.0.1:8484 you@host</code>. Moxie never moves money; every action needs your approval,
and nothing sends unless MOXIE_LIVE=true.</div>
</div><!-- /main -->
<div id="toast"></div>

<div id="modal" style="display:none; position:fixed; inset:0; background:rgba(0,0,0,.6);
     align-items:center; justify-content:center; z-index:10;">
  <div class="card" style="max-width:640px; width:92%;">
    <h3>Approve this action?</h3>
    <div id="m_desc" style="margin:8px 0"></div>
    <label>The draft (edit freely — it goes out under your name)</label>
    <textarea id="m_draft" rows="10" style="width:100%; background:var(--bg);
      border:1px solid var(--line); color:var(--fg); border-radius:7px;
      padding:8px; font:13px/1.4 ui-monospace,monospace;"></textarea>
    <div class="muted" style="margin:8px 0">⚠️ This cannot be undone once sent.
      In drafts mode (MOXIE_LIVE off) approving finalises the draft; nothing sends.</div>
    <button class="primary" id="m_go">Approve</button>
    <button id="m_cancel" style="margin-left:8px">Cancel</button>
  </div>
</div>

<script>
const $ = id => document.getElementById(id);
const val = id => $(id).value;
function toast(msg){ const t=$('toast'); t.textContent=msg; t.style.display='block';
  setTimeout(()=>t.style.display='none', 4000); }
async function api(path, body){
  const headers = {'X-Moxie':'1'};
  const tok = sessionStorage.getItem('moxie_token');
  if(tok) headers['Authorization'] = 'Bearer '+tok;
  const opts = body ? {method:'POST', headers, body: JSON.stringify(body)} : {headers};
  let r = await fetch(path, opts);
  if(r.status === 401){
    const t = prompt('This dash is token-protected (MOXIE_DASH_TOKEN). Enter the token:');
    if(t){ sessionStorage.setItem('moxie_token', t); return api(path, body); }
  }
  return r.json(); }

function wizardToggle(setup){
  const fresh = setup && !setup.wizard_done && !(setup.brain_ready && setup.has_data);
  $('wizard').style.display = fresh ? 'block' : 'none';
  $('main').style.display = fresh ? 'none' : 'block';
  if(!fresh) return;
  const tick = (id, done, n) => { const el=$(id);
    el.textContent = done ? '✓' : n; el.className = 'tick' + (done ? ' done' : ''); };
  tick('w1t', setup.brain_ready, '1');
  tick('w2t', setup.has_data, '2');
  tick('w3t', setup.telegram_paired, '3');
}
async function wizKey(){
  $('w1msg').textContent = ' saving…';
  const r = await api('/api/setup', {MOXIE_API_KEY: val('w_api')});
  if(r.error){ $('w1msg').textContent = ' ' + r.error; return; }
  $('w1msg').textContent = ' testing the key…';
  const t = await api('/api/brain/test', {});
  $('w1msg').textContent = t.ok ? ' ✓ key works ('+t.model+')' : ' ✗ '+t.error;
  refresh();
}
function wizCsv(input){
  const file = input.files && input.files[0];
  if(!file) return;
  $('w2msg').textContent = ' reading '+file.name+'…';
  const reader = new FileReader();
  reader.onload = async () => {
    const r = await api('/api/import/csv', {name: file.name, text: reader.result});
    $('w2msg').textContent = r.error ? ' ✗ '+r.error
      : ' ✓ '+r.transactions+' transactions, '+r.found+' finding(s)';
    refresh();
  };
  reader.readAsText(file);
}
async function wizDemo(){
  const r = await api('/api/demo', {});
  $('w2msg').textContent = r.error ? ' ✗ '+r.error
    : ' ✓ sample data loaded — '+r.found+' finding(s) to explore';
  refresh();
}
async function wizTok(){
  const r = await api('/api/setup', {TELEGRAM_BOT_TOKEN: val('w_tok')});
  $('w3msg').textContent = r.error || 'Token saved. Message your bot anything, then click detect.';
}
async function wizDetect(){
  const r = await api('/api/telegram/detect', {});
  if(r.error){ $('w3msg').textContent = r.error; return; }
  window._chat = r.chat_id;
  $('w3msg').textContent = 'Found: '+r.name+' ('+r.chat_id+') — now pair it.';
  $('w_pair').style.display = 'inline-block';
}
async function wizPair(){
  const r = await api('/api/setup', {MOXIE_TELEGRAM_CHAT_ID: window._chat});
  $('w3msg').textContent = r.error || '✓ Paired. Run `moxie telegram` (or `moxie serve`) to bring the bot online.';
  refresh();
}
async function wizFinish(){ await api('/api/wizard/done', {}); refresh(); }

function esc(s){ const d=document.createElement('div'); d.textContent=s||''; return d.innerHTML; }
function chatBubble(role, text, related){
  const mine = role==='user';
  let extra = '';
  if(related && related.length){
    extra = '<div style="margin-top:6px">' + related.map(r =>
      '<button onclick="goFinding()">Review: '+esc(r.merchant)+' →</button> ').join('') + '</div>';
  }
  return '<div style="margin:6px 0; text-align:'+(mine?'right':'left')+'">'+
    '<div style="display:inline-block; max-width:86%; padding:8px 12px; border-radius:10px; '+
    'text-align:left; white-space:pre-wrap; border:1px solid var(--line); '+
    (mine?'background:#1f2733':'background:var(--bg)')+'">'+esc(text)+extra+'</div></div>';
}
function goFinding(){
  $('findings_h').scrollIntoView({behavior:'smooth'});
  const t = $('findings'); t.style.outline = '2px solid var(--orange)';
  setTimeout(()=>t.style.outline='none', 2000);
}
async function loadChat(){
  const h = await api('/api/chat/history');
  if(Array.isArray(h) && h.length){
    $('chatlog').innerHTML = h.map(t => chatBubble(t.role, t.text)).join('');
    $('chatlog').scrollTop = $('chatlog').scrollHeight;
  } else {
    $('chatlog').innerHTML = '<div class="muted">🦡 Ask me anything about your money. '+
      'I can explain findings, weigh a purchase against what\\'s left this month, '+
      'or sharpen a cancellation draft — you approve everything in Findings.</div>';
  }
}
async function sendChat(){
  const box = $('chatbox'); const msg = box.value.trim();
  if(!msg) return;
  box.value = '';
  $('chatlog').innerHTML += chatBubble('user', msg);
  $('chatlog').innerHTML += '<div id="thinking" class="muted" style="margin:6px 0">…</div>';
  $('chatlog').scrollTop = $('chatlog').scrollHeight;
  const r = await api('/api/chat', {message: msg});
  const think = $('thinking'); if(think) think.remove();
  $('chatlog').innerHTML += chatBubble('assistant', r.error ? ('⚠️ '+r.error) : r.reply, r.related);
  $('chatlog').scrollTop = $('chatlog').scrollHeight;
}

async function refresh(){
  const s = await api('/api/status');
  wizardToggle(s.setup);
  $('ver').textContent = s.version;
  $('hb2').textContent = 'last event: ' + (s.heartbeat.last_event||'—') +
      (s.heartbeat.last_daily_scan ? ' · daily scan: '+s.heartbeat.last_daily_scan : '');
  $('brain').textContent = s.brain.ready ? 'ready' : (s.brain.offline ? 'offline mode' : 'no key');
  $('brain').className = 'big ' + (s.brain.ready ? 'ok' : 'warn');
  $('brain2').textContent = s.brain.model;
  $('tg').textContent = s.telegram.paired_chat ? 'paired' : (s.telegram.token_set ? 'token set' : 'not set up');
  $('tg').className = 'big ' + (s.telegram.paired_chat ? 'ok' : 'warn');
  $('tg2').textContent = s.telegram.paired_chat ? ('chat '+s.telegram.paired_chat) : 'see Setup below';
  $('data').textContent = s.data.transactions + ' txns';
  $('data2').textContent = s.data.findings + ' finding(s) · ~' + s.data.currency + s.data.est_savings + '/yr';
  $('audit').textContent = s.audit.intact ? 'verified' : 'TAMPERED';
  $('audit').className = 'big ' + (s.audit.intact ? 'ok' : 'bad');
  $('audit2').textContent = s.audit.entries + ' hash-chained entries';
  const m = s.actions || {mode:'drafts'};
  $('mode').textContent = m.mode==='kill' ? 'KILL SWITCH' : (m.mode==='live' ? 'LIVE' : 'drafts');
  $('mode').className = 'big ' + (m.mode==='kill' ? 'bad' : (m.mode==='live' ? 'warn' : 'ok'));
  $('mode2').textContent = m.mode==='kill' ? 'moxie kill --release to resume'
      : (m.mode==='live' ? 'approved actions really send' : 'nothing sends — set MOXIE_LIVE=true');
  const b = s.bank || {linked:false};
  $('bank').textContent = b.linked ? (b.needs_reauth ? 're-consent' : b.provider) : 'not linked';
  $('bank').className = 'big ' + (b.linked ? (b.needs_reauth ? 'bad' : 'ok') : 'warn');
  $('bank2').textContent = b.linked
      ? (b.needs_reauth ? 'consent expired — click connect… to re-consent'
         : b.accounts+' account(s)'+(b.consent_days_left!=null ? ' · consent ~'+b.consent_days_left+'d left' : '')
           +(b.last_sync ? ' · synced '+b.last_sync.slice(0,16) : ''))
      : 'click connect… (read-only) · or import a CSV — no cloud at all';
  const mo = s.money;
  if(mo){
    $('money').textContent = mo.currency + mo.left.toFixed(2) + ' left';
    $('money').className = 'big ' + (mo.left > 0 ? 'ok' : 'bad');
    $('money2').textContent = 'in ~'+mo.currency+mo.income.toFixed(0)
        +' · spent '+mo.currency+mo.spent.toFixed(0)
        +' · committed '+mo.currency+mo.committed.toFixed(0)
        +(mo.balance!=null ? ' · balance '+mo.currency+mo.balance.toFixed(0) : '');
  } else { $('money').textContent = '—'; $('money2').textContent = 'import data first'; }

  const f = await api('/api/findings');
  $('findings').innerHTML = f.length ? f.map((a,i) =>
    '<tr><td class="muted">'+(i+1)+'</td><td>'+a.description+
    '</td><td style="white-space:nowrap">~'+a.currency+a.est_savings.toFixed(2)+'/yr</td>'+
    '<td style="white-space:nowrap"><button onclick="approve(\\''+a.id+'\\')">approve</button> '+
    '<button onclick="skip(\\''+a.id+'\\')">skip</button></td></tr>').join('')
    : '<tr><td class="muted">Nothing waiting on you. Import data with: moxie scan --csv / --pdf</td></tr>';
}
async function approve(id){
  const f = (await api('/api/findings')).find(x=>x.id===id);
  const box = $('modal');
  $('m_desc').textContent = f.description;
  $('m_draft').value = f.draft || '';
  $('m_go').onclick = async () => {
    box.style.display='none';
    const r = await api('/api/resolve', {id, approved:true, draft: $('m_draft').value});
    toast(r.error || (r.outcome.toUpperCase()+': '+r.merchant+' — '+r.note)); refresh(); };
  $('m_cancel').onclick = () => { box.style.display='none'; };
  box.style.display='flex'; }
async function skip(id){
  const r = await api('/api/resolve', {id, approved:false});
  toast(r.error || ('Skipped '+r.merchant+' — remembered for 60 days')); refresh(); }
async function rescan(){ const r = await api('/api/rescan', {});
  toast(r.error || ('Re-scanned: '+r.found+' finding(s), '+r.suppressed+' snoozed')); refresh(); }
async function syncBank(){ const r = await api('/api/bank/sync', {});
  toast(r.error || ('Synced '+r.transactions+' transaction(s) from '+r.provider)); refresh(); }
function toggleBankLink(){ const u=$('bank_link_ui');
  u.style.display = u.style.display==='none' ? 'block' : 'none'; }
async function bankStart(){
  $('bank_hint').textContent = 'starting…';
  const r = await api('/api/bank/start', {provider: val('bank_provider')});
  if(r.error){ $('bank_hint').textContent = '✗ '+r.error; return; }
  $('bank_hint').innerHTML = '1 · <a href="'+r.url+'" target="_blank" rel="noopener" '+
    'style="color:var(--orange)">Open your bank consent page ↗</a><br>2 · '+esc(r.hint);
  $('bank_step2').style.display = 'block';
}
async function bankComplete(){
  const r = await api('/api/bank/complete', {code: val('bank_code')});
  if(r.error){ toast('✗ '+r.error); return; }
  toast('✓ Linked via '+r.provider+' — synced '+(r.transactions||0)+' transaction(s)');
  $('bank_link_ui').style.display='none'; refresh();
}
async function save(kv){ const r = await api('/api/setup', kv);
  toast(r.error || ('Saved: '+r.saved.join(', '))); refresh(); }
async function detect(){ const r = await api('/api/telegram/detect', {});
  if(r.error){ toast(r.error); return; }
  window._chat = r.chat_id;
  $('detected').textContent = ' found: '+r.name+' ('+r.chat_id+')';
  $('pairbtn').style.display = 'inline-block'; }
refresh(); loadChat(); setInterval(refresh, 15000);
</script></body></html>"""


def make_handler(dash: Dash):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep the console quiet
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _auth_ok(self) -> bool:
            """Optional bearer token (MOXIE_DASH_TOKEN) — belt for the
            localhost braces, and required if you insist on tunnelling."""
            import os
            token = os.environ.get("MOXIE_DASH_TOKEN", "")
            if not token:
                return True
            return self.headers.get("Authorization", "") == f"Bearer {token}"

        def do_GET(self):
            if self.path == "/" or self.path.startswith("/index"):
                body = PAGE.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path.startswith("/api/"):
                if not self._auth_ok():
                    self._json({"error": "unauthorized — send Authorization: "
                                         "Bearer <MOXIE_DASH_TOKEN>"}, 401)
                elif self.path == "/api/status":
                    self._json(dash.status())
                elif self.path == "/api/findings":
                    self._json(dash.findings())
                elif self.path == "/api/chat/history":
                    self._json(dash.chat_history())
                else:
                    self._json({"error": "not found"}, 404)
            elif self.path.startswith("/callback"):
                # OAuth landing for bank consent: show the code to paste into
                # `moxie connect` — this page never stores or logs it.
                query = urllib.parse.urlparse(self.path).query
                code = urllib.parse.parse_qs(query).get("code", [""])[0]
                body = ("<!doctype html><meta charset='utf-8'>"
                        "<body style='font:16px system-ui;padding:40px'>"
                        "<h2>🦡 Bank consent received</h2>"
                        + (f"<p>Paste this code into <code>moxie connect</code>:</p>"
                           f"<pre style='background:#eee;padding:12px'>{code}</pre>"
                           if code else
                           "<p>No code in the URL — if your provider redirects "
                           "without one (GoCardless/Plaid), just return to the "
                           "terminal and press Enter.</p>")
                        + "<p>You can close this tab.</p></body>")
                data = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            if not self._auth_ok():
                self._json({"error": "unauthorized"}, 401)
                return
            # CSRF: browsers can't attach custom headers cross-origin without
            # a preflight we never approve — so requiring one blocks drive-by
            # POSTs from malicious pages targeting 127.0.0.1.
            if self.headers.get("X-Moxie") != "1":
                self._json({"error": "missing X-Moxie header (CSRF guard)"}, 403)
                return
            length = int(self.headers.get("Content-Length") or 0)
            try:
                form = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                form = {}
            if self.path == "/api/resolve":
                self._json(dash.resolve(form.get("id", ""), bool(form.get("approved")),
                                        edited_draft=form.get("draft")))
            elif self.path == "/api/rescan":
                self._json(dash.rescan())
            elif self.path == "/api/setup":
                self._json(dash.save_setup(form))
            elif self.path == "/api/telegram/detect":
                self._json(dash.detect_chat())
            elif self.path == "/api/bank/sync":
                self._json(dash.bank_sync())
            elif self.path == "/api/bank/start":
                self._json(dash.bank_start(form.get("provider", "")))
            elif self.path == "/api/bank/complete":
                self._json(dash.bank_complete(form.get("code", "")))
            elif self.path == "/api/brain/test":
                self._json(dash.brain_test())
            elif self.path == "/api/chat":
                self._json(dash.chat(form.get("message", "")))
            elif self.path == "/api/import/csv":
                self._json(dash.import_csv_text(form.get("name", ""),
                                                form.get("text", "")))
            elif self.path == "/api/demo":
                self._json(dash.load_sample_data())
            elif self.path == "/api/wizard/done":
                self._json(dash.wizard_done())
            else:
                self._json({"error": "not found"}, 404)

    return Handler


def serve(config, store, audit, port: int = 8484, host: str = "127.0.0.1",
          dash: "Dash | None" = None):
    dash = dash or Dash(config, store, audit)
    server = ThreadingHTTPServer((host, port), make_handler(dash))
    return server


def maybe_open_browser(url: str, force: "bool | None" = None) -> bool:
    """Open the user's browser at the dashboard — the one-command front door.
    Skipped when MOXIE_NO_BROWSER is set or there's no interactive terminal
    (CI, ssh, service units), so servers never spawn browsers."""
    import os
    import sys
    import webbrowser
    if force is None:
        if os.environ.get("MOXIE_NO_BROWSER", "").lower() in ("1", "true", "yes"):
            return False
        if not (sys.stdout.isatty() and sys.stdin.isatty()):
            return False
    elif not force:
        return False
    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False


def run_dashboard(config, store, audit, port: int = 8484, open_browser=None):
    server = serve(config, store, audit, port=port)
    actual = server.server_address[1]
    url = f"http://127.0.0.1:{actual}"
    print(f"🦡 Moxie Dash: {url}   (Ctrl-C to stop)")
    print("   Remote box? Tunnel it:  ssh -L {0}:127.0.0.1:{0} you@host".format(actual))
    if maybe_open_browser(url, force=open_browser):
        print("   (opened in your browser — do everything from there)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
