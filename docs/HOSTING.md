# Hosting Moxie 24/7

Moxie earns its keep when it runs all the time: the daily loop re-scans your
data every morning and messages you (Telegram) or updates the dash *only*
when there's something new to decide.

One command runs everything:

```bash
moxie serve        # dashboard + Telegram bot + daily loop, one process
```

## Where to run it (in order of how much we like it)

### 1. A Mac mini (or any box) at home — the ideal host

Always-on, silent, and **your bank data never leaves a machine you own** —
which is the entire thesis of this project.

```bash
pip install moxie-agent          # or pipx install moxie-agent
moxie init && moxie doctor
cp deploy/com.moxie.serve.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/com.moxie.serve.plist
```

(On a Linux home server, use the systemd unit below instead.)

### 2. A Linux VPS — fine, with eyes open

Your data lives on a rented box, so two rules:

- **Encrypt at rest**: `pip install "moxie-agent[secure]"` then `moxie encrypt on`.
- **Never expose the dashboard.** It binds to 127.0.0.1; reach it via an SSH
  tunnel — and set `MOXIE_DASH_TOKEN` anyway, belt and braces:

```bash
# on the VPS
pip install "moxie-agent[secure]"
moxie init && moxie encrypt on
mkdir -p ~/.config/systemd/user
cp deploy/moxie.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now moxie
loginctl enable-linger $USER

# from your laptop, when you want the dash
ssh -L 8484:127.0.0.1:8484 you@your-vps    # then open http://127.0.0.1:8484
```

### 3. Docker — anywhere

```bash
docker build -t moxie .
docker run -d --name moxie \
  -v moxie-home:/home/moxie/.moxie \
  -p 127.0.0.1:8484:8484 \
  --env-file .env \
  moxie
```

Publishing on `127.0.0.1:8484` keeps the dash host-loopback-only even though
it binds 0.0.0.0 *inside* the container. The named volume holds `~/.moxie`
(store, audit log, keys) across upgrades.

## The .env that makes it useful

```bash
# the brain (pick one)
MOXIE_API_KEY=sk-ant-…                # bring your own Anthropic key
MOXIE_MODEL=ollama:llama3.1           # or: fully local via Ollama

# the channel
TELEGRAM_BOT_TOKEN=123:abc            # from @BotFather
MOXIE_TELEGRAM_CHAT_ID=42424242       # pair to YOUR chat only
MOXIE_SCAN_HOUR=8                     # morning briefing hour

# live actions (leave off until you're ready — drafts otherwise)
MOXIE_LIVE=true
MOXIE_SMTP_HOST=smtp.gmail.com
MOXIE_SMTP_USER=you@gmail.com
MOXIE_SMTP_PASSWORD=app-password      # app password, never your real one

# hardening
MOXIE_DASH_TOKEN=something-long-and-random
```

Better than `.env` for secrets (not in Docker): `moxie secret set MOXIE_API_KEY`
puts them in the OS keychain.

## Sanity checks once it's up

```bash
moxie doctor        # everything green?
moxie verify        # audit chain intact?
moxie kill          # the panic button works from any shell on the box
```

If the box will act on real money (`MOXIE_LIVE=true`), test the pipeline
first with `MOXIE_EMAIL_OVERRIDE_TO=you@example.com` — every send reroutes
to you instead of the merchant until you remove it.
