# Glossary

> **Last Updated:** 2026-03-01
> **Update this document** when new domain-specific terms are introduced.

---

| Term | Definition |
|------|-----------|
| **ACS** | Arvind Chettinadu Snacks — pilot client, uses Tally Prime ERP |
| **TJUK** | TJUK — pilot client (food distributor in Mumbai), uses SAP Business One |
| **IIC** | Indian Insights Company — Brady & Akash's company, building this product |
| **MVP** | Minimum Viable Product — first working version (v0.1.0, text-only ordering) |
| **HIL** | Human-in-the-Loop — items flagged for manual review when AI confidence is low |
| **SKU** | Stock Keeping Unit — unique product identifier in the catalog |
| **SalPackUn** | Sales Packing Unit — number of pieces per case/box (from SAP master data) |
| **BWeight1** | Base Weight — weight of a single unit in kg (from SAP master data) |
| **PCS** | Pieces — the base unit of measure in SAP for TJUK products |
| **ERP** | Enterprise Resource Planning — business software (SAP B1, Tally Prime) |
| **SAP B1** | SAP Business One — ERP system used by TJUK |
| **Tally Prime** | Accounting/ERP software used by ACS |
| **Meta Cloud API** | WhatsApp Business Platform API hosted by Meta (Facebook) |
| **Webhook** | HTTP callback — Meta sends incoming messages to our server via POST |
| **HMAC-SHA256** | Hash-based Message Authentication Code — used to verify webhook signatures |
| **LLM** | Large Language Model — AI model (Claude) that understands natural language |
| **Caddy** | Web server / reverse proxy with automatic HTTPS |
| **DuckDNS** | Free dynamic DNS service used for the bot's domain |
| **FastAPI** | Python web framework used for the webhook server |
| **Uvicorn** | ASGI server that runs the FastAPI application |
| **Docker Compose** | Tool for defining and running multi-container Docker applications |
| **VPS** | Virtual Private Server — the cloud machine hosting the bot |
| **OCR** | Optical Character Recognition — extracting text from images (planned v0.2.0) |
| **Multi-tenant** | Architecture where one system serves multiple clients independently (planned v0.3.0) |
| **Rate card** | Pricing document given to prospective clients |
| **Overage** | Usage charges above the included monthly order quota |
