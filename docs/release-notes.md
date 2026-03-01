# Release Notes — WhatsApp Ordering Bot

---

## v0.1.0 — MVP (2026-03-01)

**Milestone: First successful end-to-end WhatsApp order processed by AI**

### What's Included
- WhatsApp Business Cloud API integration (webhook receive + send)
- Claude AI message parsing — understands natural-language orders
- TJUK product catalog (loaded from Excel reference data)
- Order confirmation replies sent back via WhatsApp
- Dockerised deployment: Node.js bot + Caddy reverse proxy
- HTTPS termination with automatic Let's Encrypt certificates
- Meta webhook verification and `messages` subscription

### Infrastructure
- VPS: Hostinger Ubuntu instance
- Domain: configured with DNS A-record → VPS
- Stack: Node.js 20 / Docker Compose / Caddy
- AI: Anthropic Claude API (claude-sonnet-4-20250514)

### Known Limitations
- Text messages only — no PDF or image support yet
- Single client / single WhatsApp Business number
- No persistent database — product catalog loaded from file at startup
- No ERP connectivity
- No structured output file (Excel) yet

### What's Next → v0.2.0
- PDF & scanned image input support
- Historical ACS chat testing with real attachments
