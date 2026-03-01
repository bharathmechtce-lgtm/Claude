# Product Backlog — WhatsApp Ordering Bot

> **Product Owner:** Bharath
> **Last Updated:** 2026-03-01 (v0.6.0 added)
> **Current Version:** v0.1.0 (MVP)

---

## MVP (v0.1.0) — COMPLETED

- [x] WhatsApp Cloud API webhook integration
- [x] Claude AI–powered message understanding
- [x] TJUK product catalog loaded from Excel
- [x] Natural-language order parsing (product, qty, unit)
- [x] Order confirmation reply via WhatsApp
- [x] Dockerised deployment on VPS (Caddy + HTTPS)
- [x] Meta webhook verification & subscription

---

## Backlog — Prioritised

### v0.2.0 — PDF & Image Input Support
| Item | Description | Owner | Status |
|------|-------------|-------|--------|
| B-001 | Accept PDF attachments in WhatsApp and extract text for order processing | Bharath | Planned |
| B-002 | Accept scanned images (photos of POs / order lists) and run OCR / vision | Bharath | Planned |
| B-003 | Obtain historical ACS chat samples with PDF & image attachments for testing | Bharath → ACS | Action Required |
| B-004 | Evaluate Claude vision vs dedicated OCR for image inputs | Dev Team | Planned |

### v0.3.0 — Multi-Client / Multi-Tenant Support
| Item | Description | Owner | Status |
|------|-------------|-------|--------|
| B-010 | Design multi-tenant architecture (one bot, many WhatsApp Business numbers) | Akash | Discovery |
| B-011 | Per-client configuration (phone number ID, catalog, prompt) | Dev Team | Planned |
| B-012 | Client onboarding flow & admin panel (if needed) | Dev Team | Planned |
| B-013 | Isolated conversation contexts per client | Dev Team | Planned |

### v0.4.0 — Database & Client/Product Master Data
| Item | Description | Owner | Status |
|------|-------------|-------|--------|
| B-020 | Select and provision database (PostgreSQL recommended) | Dev Team | Planned |
| B-021 | Design schema: Clients, Products, Orders, Conversations | Dev Team | Planned |
| B-022 | Migrate product catalog from Excel to DB | Dev Team | Planned |
| B-023 | LLM ↔ DB integration for real-time product & customer lookup | Dev Team | Planned |
| B-024 | Order history storage & retrieval | Dev Team | Planned |

### v0.5.0 — ERP-Ready Excel Output
| Item | Description | Owner | Status |
|------|-------------|-------|--------|
| B-030 | Generate structured Excel file from parsed orders | Dev Team | Planned |
| B-031 | Match Excel columns to ERP upload template | Dev Team | Planned |
| B-032 | Add `Status` field per line item (Confirmed / Needs Review) | Dev Team | Planned |
| B-033 | Flag HIL (Human-in-the-Loop) items that need manual verification | Dev Team | Planned |
| B-034 | Delivery mechanism — email / download link / WhatsApp file reply | Dev Team | Planned |

### v0.6.0 — LLM Cost Optimisation & Model Selection
| Item | Description | Owner | Status |
|------|-------------|-------|--------|
| B-050 | Benchmark current per-message cost with Claude Sonnet on real order data | Dev Team | Planned |
| B-051 | Evaluate cheaper models (Claude Haiku, GPT-4o-mini, open-source) on accuracy vs cost | Dev Team | Planned |
| B-052 | Test tiered model strategy: fast/cheap model for simple orders, powerful model for complex/ambiguous ones | Dev Team | Planned |
| B-053 | Implement prompt optimisation — reduce token usage without losing quality | Dev Team | Planned |
| B-054 | Add usage tracking & cost dashboard (tokens per request, cost per order, cost per client) | Dev Team | Planned |
| B-055 | Define quality threshold — minimum acceptable order parsing accuracy before switching models | Bharath | Planned |
| B-056 | Load-test with projected multi-client volumes and calculate monthly cost projections | Dev Team | Planned |

### v1.0.0 — ERP Integration
| Item | Description | Owner | Status |
|------|-------------|-------|--------|
| B-040 | Identify target ERP system(s) and available APIs | Bharath / Akash | Discovery |
| B-041 | Build ERP connector (REST / SOAP / file drop) | Dev Team | Planned |
| B-042 | Push confirmed orders directly into ERP | Dev Team | Planned |
| B-043 | Pull live stock / pricing from ERP into bot responses | Dev Team | Planned |
| B-044 | End-to-end order lifecycle: WhatsApp → Bot → ERP → Confirmation | Dev Team | Planned |

---

## Action Items (Immediate)

| # | Action | Owner | Deadline |
|---|--------|-------|----------|
| 1 | Get historical ACS chats with image/PDF attachments | Bharath → ACS | TBD |
| 2 | Discuss multi-tenant architecture requirements | Akash → Claude | TBD |
| 3 | Identify target ERP system and confirm API availability | Bharath / Akash | TBD |

---

## Notes

- Each version builds on the previous — versions are indicative, not rigid.
- HIL = Human-in-the-Loop: orders the AI is not confident about get flagged for manual review.
- The backlog will be refined as ACS feedback and real-world usage data come in.
- Model cost optimisation (v0.6.0) must be completed before v1.0 launch — scaling to multiple clients on an expensive model without cost analysis is a business risk.
