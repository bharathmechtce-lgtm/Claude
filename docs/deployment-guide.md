# Environment & Deployment Guide

> **Last Updated:** 2026-03-01 (MVP)
> **Update this document** whenever infrastructure, env vars, or deployment steps change.

---

## Infrastructure Overview

| Component | Technology | Details |
|-----------|-----------|---------|
| VPS | Hostinger Ubuntu | Cloud server hosting all services |
| Reverse Proxy | Caddy 2 (Alpine) | Auto HTTPS via Let's Encrypt |
| Application | Python 3.11 + FastAPI | Webhook receiver + LLM caller |
| Containerisation | Docker Compose | Two containers: `bot` + `caddy` |
| Domain | `whatsapporderbot.duckdns.org` | Free DuckDNS dynamic DNS |
| SSL | Auto (Caddy) | Let's Encrypt certificates, auto-renewed |

---

## Directory Structure

```
/home/user/Claude/
├── .env                      # Secrets (never committed)
├── docker-compose.yml        # Container orchestration
├── Caddyfile                 # Reverse proxy config
├── webhook/
│   ├── Dockerfile            # Python 3.11-slim image
│   ├── app.py                # Main application
│   └── requirements.txt      # Python dependencies
├── docs/                     # Product documentation
├── scenarios/                # Test scenarios
├── ACS/                      # ACS client data
└── TJUK/                     # TJUK client data
```

---

## Environment Variables

Create a `.env` file in the project root with:

| Variable | Required | Description |
|----------|----------|-------------|
| `WHATSAPP_TOKEN` | Yes | Meta WhatsApp Cloud API access token |
| `WHATSAPP_PHONE_NUMBER_ID` | Yes | Phone number ID from Meta Business Manager |
| `WHATSAPP_APP_SECRET` | Yes | App secret for webhook signature verification |
| `WEBHOOK_VERIFY_TOKEN` | No | Token for webhook verification (default: `tjuk-bot-verify-2024`) |
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key for Claude |
| `LLM_MODEL` | No | Model ID (default: `claude-sonnet-4-5-20250929`) |

```bash
# .env example (DO NOT commit this file)
WHATSAPP_TOKEN=EAAxxxxxxx
WHATSAPP_PHONE_NUMBER_ID=123456789
WHATSAPP_APP_SECRET=abcdef123456
WEBHOOK_VERIFY_TOKEN=tjuk-bot-verify-2024
ANTHROPIC_API_KEY=sk-ant-xxxxxxx
LLM_MODEL=claude-sonnet-4-5-20250929
```

---

## Deployment Steps

### First-Time Setup

```bash
# 1. SSH into VPS
ssh user@<vps-ip>

# 2. Clone the repo
git clone <repo-url> ~/Claude
cd ~/Claude

# 3. Create .env file with secrets
nano .env

# 4. Build and start
docker compose up -d --build

# 5. Verify containers are running
docker compose ps

# 6. Check logs
docker compose logs -f bot
```

### Redeployment (After Code Changes)

```bash
# Pull latest code
cd ~/Claude
git pull origin main

# Rebuild and restart
docker compose down
docker compose up -d --build

# Verify
docker compose logs -f bot
```

### Common Operations

```bash
# View live logs
docker compose logs -f bot

# Restart bot only (no rebuild)
docker compose restart bot

# Stop everything
docker compose down

# Rebuild from scratch (clear cache)
docker compose build --no-cache
docker compose up -d

# Check container health
docker compose ps
```

---

## Domain & DNS Setup

1. Register a free domain at [DuckDNS](https://www.duckdns.org/)
2. Point the domain to VPS IP address
3. Caddy handles SSL automatically — no manual certificate setup needed

**Caddyfile:**
```
whatsapporderbot.duckdns.org {
    reverse_proxy bot:8000
}
```

---

## Meta WhatsApp Setup

1. Create an app at [Meta for Developers](https://developers.facebook.com/)
2. Add WhatsApp product to the app
3. Configure webhook URL: `https://whatsapporderbot.duckdns.org/webhook`
4. Set verify token to match `WEBHOOK_VERIFY_TOKEN` in `.env`
5. Subscribe to `messages` webhook field
6. Generate a permanent access token and set as `WHATSAPP_TOKEN`

---

## Troubleshooting

| Problem | Check |
|---------|-------|
| Bot not responding | `docker compose logs bot` — look for errors |
| Webhook verification fails | Verify `WEBHOOK_VERIFY_TOKEN` matches Meta config |
| "Missing env vars" warning | Check `.env` file exists and has all required vars |
| Claude API errors | Verify `ANTHROPIC_API_KEY` is valid and has credits |
| WhatsApp send fails | Check `WHATSAPP_TOKEN` hasn't expired |
| SSL certificate issues | `docker compose restart caddy` — Caddy auto-renews |
| Container won't start | `docker compose build --no-cache` to rebuild |
