# Channel Setup Guide — Biodefense Nutrition

OpenClaw natively supports **22+ messaging platforms**. This guide covers end-to-end setup for the primary channels.

> **Prerequisites**: Node >= 22 installed, OpenClaw installed (`npm install -g openclaw@latest`)

---

## Quick Start (any channel)

```bash
# 1. Copy env file and fill in your tokens
cp .env.example .env

# 2. Start the OpenClaw gateway
cd openclaw
openclaw gateway --port 18789 --verbose
```

The gateway auto-connects to every channel that has valid credentials in the environment.

---

## 1. Telegram

**Best for**: Hackathon demos, mobile-first users.

### Setup Steps

1. **Create a bot** — Open Telegram, search for `@BotFather`, send `/newbot`
2. **Name it** — e.g., "NutriShield Bot" / username: `nutrishield_bot`
3. **Copy the token** — BotFather gives you something like `7123456789:AAF...`
4. **Set the env var**:
   ```
   TELEGRAM_BOT_TOKEN=7123456789:AAF...
   ```
5. **Optional — set bot commands** via BotFather `/setcommands`:
   ```
   start - Begin onboarding
   mealplan - Get today's meal plan
   threats - Check threats in your area
   log - Quick meal log
   status - Profile summary and macros
   privacy - Explain the privacy model
   ```
6. **Start the gateway** — OpenClaw connects automatically.
7. **Test** — Send `/start` to your bot in Telegram.

### Group Chat

To use in Telegram groups:
- Add the bot to the group
- Users mention `@nutrishield_bot` or `@nutrishield` to activate it
- The config has `requireMention: true` for groups by default

---

## 2. Discord

**Best for**: Community servers, hackathon judge demos.

### Setup Steps

1. **Create an application** — Go to [Discord Developer Portal](https://discord.com/developers/applications) → "New Application"
2. **Create a bot** — Click "Bot" in the sidebar → "Add Bot"
3. **Enable intents** — Under "Privileged Gateway Intents", enable:
   - [x] **Message Content Intent** (required to read messages)
   - [x] **Server Members Intent** (optional)
4. **Copy the token** — Click "Reset Token" → copy it
5. **Set the env var**:
   ```
   DISCORD_BOT_TOKEN=MTIz...
   ```
6. **Generate invite link** — "OAuth2" → "URL Generator":
   - Scopes: `bot`, `applications.commands`
   - Bot Permissions: `Send Messages`, `Read Message History`, `Embed Links`, `Attach Files`, `Use Slash Commands`
   - Copy the generated URL and open it to invite the bot to your server
7. **Start the gateway** — OpenClaw connects automatically.
8. **Test** — Send `@NutriShield /start` in any channel the bot can see.

### Tips for Discord

- The bot responds to DMs (open policy for hackathon)
- In servers, mention `@NutriShield` or `@biodefense` to activate
- Use Discord threads for per-user conversations in shared channels

---

## 3. WhatsApp

**Best for**: Widest global reach, personal feel.

### Setup Steps

No developer tokens needed — OpenClaw uses Baileys (open-source WhatsApp Web protocol).

1. **Link the device**:
   ```bash
   cd openclaw
   openclaw channels login
   ```
2. **Scan the QR code** — Open WhatsApp on your phone → Settings → Linked Devices → "Link a Device" → scan the QR in terminal
3. **Start the gateway** — The WhatsApp session persists in `~/.openclaw/credentials/`
4. **Test** — Send "hello" to the linked WhatsApp number from any phone.

### Notes

- The bot uses your personal WhatsApp number (or a dedicated number)
- For the hackathon: use a spare phone/number if you don't want to mix personal chats
- Credentials persist across gateway restarts
- Group chat: add the number to a group, mention `@NutriShield`

---

## 4. Slack

**Best for**: Professional teams, workplace wellness programs.

### Setup Steps

1. **Create a Slack app** — Go to [api.slack.com/apps](https://api.slack.com/apps) → "Create New App" → "From scratch"
2. **Enable Socket Mode** — "Socket Mode" in sidebar → toggle ON → create an App-Level Token with `connections:write` scope → copy it
3. **Add Bot Token Scopes** — "OAuth & Permissions" → Bot Token Scopes:
   - `chat:write`
   - `app_mentions:read`
   - `im:history`
   - `im:read`
   - `im:write`
4. **Enable Events** — "Event Subscriptions" → toggle ON → subscribe to:
   - `app_mention`
   - `message.im`
5. **Install to workspace** — "Install App" → "Install to Workspace" → copy Bot Token
6. **Set env vars**:
   ```
   SLACK_BOT_TOKEN=xoxb-...
   SLACK_APP_TOKEN=xapp-...
   ```
7. **Start the gateway** — OpenClaw connects automatically.
8. **Test** — DM the bot or `@NutriShield /start` in a channel.

---

## 5. WebChat (Built-in — No Setup!)

**Best for**: Judge demos, quick testing, no app install needed.

WebChat is served directly from the OpenClaw Gateway. No additional configuration required.

1. **Start the gateway**:
   ```bash
   openclaw gateway --port 18789 --verbose
   ```
2. **Open in browser**: `http://localhost:18789`
3. The full NutriShield assistant is available immediately.

### For Remote Access (Demo Day)

Use Tailscale Funnel to make WebChat publicly accessible:
```bash
# In openclaw.json, add:
# "gateway": { "tailscale": { "mode": "funnel" }, "auth": { "mode": "password" } }
```
Or use an SSH tunnel / ngrok for a quick public URL.

---

## 6. Additional Channels (Optional)

Enable any of these by uncommenting in `openclaw.json` and setting env vars:

| Channel | Env Vars Needed | Notes |
|---------|----------------|-------|
| **Signal** | `signal-cli` installed | Privacy-focused, E2E encrypted |
| **Microsoft Teams** | `MSTEAMS_APP_ID` + `MSTEAMS_APP_PASSWORD` | Azure Bot Framework app |
| **Google Chat** | Service account JSON | Google Cloud Chat API project |
| **Matrix** | Homeserver URL + access token | Decentralized, self-hosted |
| **LINE** | Channel access token | Popular in Japan/SE Asia |
| **Mattermost** | Personal access token + server URL | Open-source Slack alternative |
| **IRC** | Server + channel config | Classic, lightweight |
| **iMessage** | BlueBubbles server (macOS) | Apple ecosystem |
| **Twitch** | OAuth token | Streaming communities |

See [OpenClaw Channel Docs](https://docs.openclaw.ai/channels) for each channel's full configuration.

---

## Multi-Channel at Once

All channels run **simultaneously** from a single gateway. A user can:
- Start onboarding on **Telegram** on their phone
- Continue the conversation on **Discord** in a community server
- Get proactive threat alerts on **WhatsApp**
- Demo for judges on **WebChat** in browser

Session memory persists per-user across all channels.

---

## Proactive Threat Alerts

When Celery workers detect a new threat, they POST to the OpenClaw webhook:

```bash
curl -X POST http://localhost:18789/hooks \
  -H "Authorization: Bearer $OPENCLAW_HOOKS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "threat_alert",
    "zone": "New York",
    "threat": "H5N1 detected in local poultry farms",
    "compound": "Quercetin",
    "message": "⚠️ H5N1 detected in your area. Your meal plan has been updated with Quercetin-rich foods (red onions, apples, berries) which showed 92% binding confidence."
  }'
```

This alert is automatically pushed to **all connected channels** where users in that zone are active.

---

## Security Checklist

For the hackathon demo, we use `dmPolicy: "open"` (anyone can chat). Before production:

- [ ] Switch `dmPolicy` to `"pairing"` (invite-only)
- [ ] Remove `"*"` from `allowFrom` arrays
- [ ] Set specific user IDs / phone numbers in allowlists
- [ ] Enable sandbox mode for group sessions
- [ ] Run `openclaw doctor` to check for misconfigurations

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Bot doesn't respond on Telegram | Check `TELEGRAM_BOT_TOKEN` is set; run `openclaw doctor` |
| Discord bot is offline | Verify Message Content Intent is enabled in Developer Portal |
| WhatsApp QR expired | Run `openclaw channels login` again, scan new QR |
| Slack bot not connecting | Ensure Socket Mode is ON and both `SLACK_BOT_TOKEN` + `SLACK_APP_TOKEN` are set |
| No response in groups | Mention the bot: `@NutriShield` or `@nutrishield` |
| Gateway won't start | Check Node >= 22: `node --version`; reinstall: `npm install -g openclaw@latest` |

Run diagnostics anytime:
```bash
openclaw doctor
```
