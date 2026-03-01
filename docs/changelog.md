# Changelog

> **Update this document** with every significant code or infrastructure change.
> Format: `[Date] — [Version/Tag] — [Summary]`

---

## 2026-03-01 — v0.1.0 (MVP Launch)

### Added
- FastAPI webhook server (`webhook/app.py`)
- WhatsApp Cloud API integration (receive + send messages)
- Claude Sonnet 4.5 LLM integration for order understanding
- In-memory conversation history per phone number (last 50 messages)
- HMAC-SHA256 webhook signature verification
- System prompt with TJUK-specific quantity conversion rules
- Health check endpoint (`GET /`)
- Docker Compose setup: `bot` (Python 3.11) + `caddy` (auto-SSL)
- Caddyfile for `whatsapporderbot.duckdns.org`
- `.env`-based configuration (secrets not committed)

### Infrastructure
- Provisioned Hostinger VPS (Ubuntu)
- Configured DuckDNS domain → VPS IP
- Meta WhatsApp Business app created, webhook verified
- Subscribed to `messages` webhook field

### Documentation
- Product backlog (`docs/product-backlog.md`)
- Release notes (`docs/release-notes.md`)
- Revenue model draft (`docs/revenue-model.md`)
- Architecture design (`docs/architecture.md`)
- Tech stack decisions (`docs/tech-stack.md`)
- Project brief (`docs/project-brief.md`)
- TJUK evaluation handoff (`docs/tjuk-handoff.md`)
- Process flow (`docs/process-flow.md`)
- Deployment guide (`docs/deployment-guide.md`)
- API reference (`docs/api-reference.md`)
- Tools & services registry (`docs/tools-and-services.md`)
- Glossary (`docs/glossary.md`)
- This changelog

---

<!--
## YYYY-MM-DD — vX.Y.Z — Short description

### Added
- New features

### Changed
- Modified behaviour

### Fixed
- Bug fixes

### Infrastructure
- Deployment or infra changes

### Documentation
- Doc updates
-->
