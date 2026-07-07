"""`moxie serve` — the always-on runner: dashboard + Telegram + daily loop.

One process, three jobs:
  * Moxie Dash on 127.0.0.1:<port> (a background thread)
  * the Telegram bot with its long-poll loop — if a token is configured
  * the daily re-scan tick — with or without Telegram, so findings appear
    on the dashboard every morning even when no chat channel is set up

Deployment recipes (systemd / launchd / Docker) live in deploy/ and
docs/HOSTING.md. The dashboard binds to loopback unless MOXIE_DASH_HOST
says otherwise (Docker needs 0.0.0.0 inside the container — set
MOXIE_DASH_TOKEN if you do that, and read HOSTING.md first).
"""
from __future__ import annotations

import os
import threading
import time

from .dashboard import serve as dash_serve
from .telegram import Bot, TelegramAPI

TICK_SECONDS = 60


def run_serve(config, store, audit, port: int = 8484, once: bool = False,
              bot_api=None) -> dict:
    """Start everything; block until Ctrl-C (or return after one pass when
    once=True — that's the testable path)."""
    from .dashboard import _emoji_safe_streams
    _emoji_safe_streams()
    host = os.environ.get("MOXIE_DASH_HOST", "127.0.0.1")
    server = dash_serve(config, store, audit, port=port, host=host)
    dash_thread = threading.Thread(target=server.serve_forever, daemon=True)
    dash_thread.start()
    actual_port = server.server_address[1]

    print(f"🦡 Moxie serving: dashboard http://{host}:{actual_port}")
    if host not in ("127.0.0.1", "localhost", "::1"):
        print("   ⚠️ Dashboard is NOT loopback-only. Token login is enforced — "
              "put TLS in front and firewall this box (docs/HOSTING.md).")

    token = config.telegram_token
    bot = Bot(config, store, audit,
              api=bot_api or (TelegramAPI(token) if token else None))
    if token:
        print("   Telegram bot: on (daily briefing loop included)")
    else:
        print("   Telegram: not configured — daily scan still runs; findings "
              "land on the dashboard")
    audit.append("serve_started", {"port": actual_port,
                                   "telegram": bool(token), "host": host})

    try:
        if bot.api is not None:
            bot.run(once=once)            # long-poll loop owns the foreground
        else:
            while True:                   # no chat channel: just tick daily
                tick = bot.daily_tick()
                if tick:
                    print("   (daily scan ran — see the dashboard)")
                if once:
                    break
                time.sleep(TICK_SECONDS)
    except KeyboardInterrupt:
        print("\n   bye.")
    finally:
        server.shutdown()
    return {"port": actual_port, "telegram": bool(token)}
