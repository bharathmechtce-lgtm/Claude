# Tools & Services Registry

> **Last Updated:** 2026-03-01 (MVP)
> **Update this document** whenever a new tool, service, or account is added or changed.

---

## External Services

| Service | Purpose | Account Owner | URL | Status |
|---------|---------|---------------|-----|--------|
| **Meta for Developers** | WhatsApp Cloud API | Bharath | https://developers.facebook.com/ | Active |
| **Anthropic** | Claude LLM API | Bharath | https://console.anthropic.com/ | Active |
| **Hostinger** | VPS hosting | Bharath | https://hpanel.hostinger.com/ | Active |
| **DuckDNS** | Free dynamic DNS | Bharath | https://www.duckdns.org/ | Active |
| **GitHub** | Source code repository | Bharath | https://github.com/ | Active |

---

## Tools & Frameworks

| Tool | Version | Purpose | Docs |
|------|---------|---------|------|
| **Python** | 3.11 | Application runtime | https://docs.python.org/3.11/ |
| **FastAPI** | ≥0.115 | Web framework (async) | https://fastapi.tiangolo.com/ |
| **Uvicorn** | ≥0.34 | ASGI server | https://www.uvicorn.org/ |
| **httpx** | ≥0.27 | Async HTTP client | https://www.python-httpx.org/ |
| **Docker** | Latest | Containerisation | https://docs.docker.com/ |
| **Docker Compose** | v3.8 | Container orchestration | https://docs.docker.com/compose/ |
| **Caddy** | 2 (Alpine) | Reverse proxy + auto-SSL | https://caddyserver.com/docs/ |

---

## AI / LLM Models

| Model | Model ID | Purpose | Cost (per 1M tokens) |
|-------|----------|---------|----------------------|
| Claude Sonnet 4.5 | `claude-sonnet-4-5-20250929` | Order parsing (current) | $3 in / $15 out |
| Claude Haiku 4.5 | `claude-haiku-4-5-20251001` | Classification (planned v0.6.0) | $0.80 in / $4 out |

*Model costs as of March 2026 — check Anthropic pricing page for updates.*

---

## Credentials & Secrets

> **NEVER commit secrets to the repository.**

| Secret | Location | Rotation |
|--------|----------|----------|
| `WHATSAPP_TOKEN` | `.env` on VPS | Regenerate in Meta Business Manager |
| `WHATSAPP_APP_SECRET` | `.env` on VPS | Found in Meta App Dashboard → Settings |
| `ANTHROPIC_API_KEY` | `.env` on VPS | Regenerate in Anthropic Console |
| `WEBHOOK_VERIFY_TOKEN` | `.env` on VPS | Custom string, change in both Meta + `.env` |

---

## Planned Services (Future Versions)

| Service | Version | Purpose |
|---------|---------|---------|
| **PostgreSQL** | v0.4.0 | Persistent database for clients, products, orders |
| **Google Sheets API** | v0.4.0+ | Ops dashboard / visibility (from architecture doc) |
| **SAP Business One SDK** | v1.0.0 | ERP integration (TJUK) |
| **Tally Prime API** | v1.0.0 | ERP integration (ACS) |

---

## Development Tools

| Tool | Purpose |
|------|---------|
| **Claude Code** | AI-assisted development |
| **Git** | Version control |
| **SSH** | VPS access |
| **Docker CLI** | Container management on VPS |
