# WhatsApp Order Bot — Tech Stack

## Finalized 28 February 2026

---

## Cloud Backend

| Component | Choice | Why |
|---|---|---|
| **Language** | Python 3.11+ | Most LLM libraries are Python-first. Claude SDK, Google Sheets API, WhatsApp API — all have mature Python support. Claude Code can generate Python well. |
| **Web Framework** | FastAPI | Handles webhooks (Meta sends messages here), serves Agent API. Async-native = handles concurrent messages well. Auto-generates API docs. |
| **Database** | PostgreSQL | Stores customers, products, conversations, order queue, audit log. JSONB support for flexible per-client fields. Battle-tested, free. |
| **LLM (extraction)** | Claude Sonnet 4.5 | 100% recall, 100% product match, 91% qty match in eval. Best accuracy. ~$0.037/order. |
| **LLM SDK** | anthropic (Python) | Official Anthropic Python SDK. Simple: `client.messages.create(...)` |
| **Google Sheets** | google-api-python-client | Google Sheets API v4. Append rows, update cells. Each client gets their own Sheet. |
| **WhatsApp** | Meta Cloud API (direct REST) | No BSP middleman. Direct HTTPS calls to send/receive. No SDK needed — just `requests`. |
| **Task scheduling** | APScheduler or Celery (later) | For: conversation timeout checks (every minute), heartbeat monitoring, Sheet status updates. Start with APScheduler (in-process, simple). Move to Celery if needed. |
| **Hosting** | Hetzner VPS (existing) | Cheapest option. Already familiar. Sufficient for 2 clients. Move to AWS Mumbai later if latency matters. |
| **Containerization** | Docker + docker-compose | One command to start/stop everything. New client = new config + restart. Portable to any server. |
| **SSL/Domain** | Let's Encrypt + Caddy | Free HTTPS certificates. Caddy auto-renews. Meta requires HTTPS for webhooks. |

---

## Local Agent (runs on client's machine)

| Component | Choice | Why |
|---|---|---|
| **Language** | Python 3.11+ | Same as cloud — one language across the project. |
| **ERP connector (TJUK)** | SAP B1 Service Layer (REST API) | SAP's built-in REST API. Agent sends POST to create Sales Orders. |
| **ERP connector (ACS)** | Tally Prime XML import | Tally accepts XML vouchers. Agent generates XML, posts to Tally's import endpoint. |
| **HTTP client** | requests | Polls cloud API, sends heartbeats, reports status. |
| **Packaging** | PyInstaller → Windows .exe | Client's IT installs once. Runs as Windows service or scheduled task. |
| **Auto-update** | Custom (check version endpoint → download → restart) | Agent checks cloud for new version. If available, downloads zip, replaces itself, restarts. |

---

## Development & Deployment

| Component | Choice | Why |
|---|---|---|
| **Version control** | Git (GitHub) | Single repo for cloud + agent + configs. |
| **Environment management** | Poetry or pip + requirements.txt | Dependency management. Poetry is cleaner but pip is simpler — start with pip. |
| **Config format** | YAML (per client) | Human-readable. One file per client (acs.yaml, tjuk.yaml). Loaded at startup. |
| **Secrets** | Environment variables (.env file) | API keys, tokens never in code or config files. Docker reads from .env. |
| **Logging** | Python logging → stdout → Docker logs | Start simple. Add Grafana + Loki later if needed. |
| **Monitoring** | Agent heartbeat table + simple alerting | Cloud tracks last heartbeat per agent. If stale > 5 min → alert ops via WhatsApp. |

---

## What We're NOT Using (and why)

| Skipped | Why |
|---|---|
| Redis | Overkill for 2 clients. PostgreSQL handles the order queue fine. Add Redis later if queue throughput is an issue. |
| Celery | Too complex for now. APScheduler handles periodic tasks in-process. |
| React/Vue frontend | Google Sheets IS the dashboard. No custom UI needed. |
| WhatsApp BSP (Gupshup, AiSensy) | Extra cost, extra dependency, extra latency. Direct Meta API is free and sufficient. |
| Multi-tenant SaaS architecture | 2 clients. Per-client config is simpler, more flexible, and easier to customize. |
| Kubernetes | Docker-compose is enough for a single VPS with 2-3 containers. |
| Message broker (RabbitMQ, Kafka) | PostgreSQL-based queue is sufficient for ~170 orders/day. |

---

## Infrastructure Cost Estimate

| Item | Monthly Cost |
|---|---|
| Hetzner VPS (CX21 or CX31) | ~$8-15 |
| Domain name | ~$1 |
| Claude Sonnet API (~170 orders/day) | ~$189 |
| Google Sheets API | Free |
| Meta WhatsApp Cloud API | Free (first 1000 conversations/month free, then ~$0.01-0.05/conversation) |
| **Total** | **~$200-210/month** |

At ₹25-40K/month per client revenue, this is profitable from client #1.

---

## Key Dependencies (Python packages)

```
# Cloud backend
fastapi              # Web framework
uvicorn              # ASGI server for FastAPI
sqlalchemy           # Database ORM
psycopg2-binary      # PostgreSQL driver
anthropic            # Claude API SDK
google-api-python-client  # Google Sheets
google-auth          # Google auth for Sheets
httpx                # Async HTTP client (for WhatsApp API calls)
pyyaml               # Config file loading
apscheduler          # Periodic task scheduling
pydantic             # Data validation (built into FastAPI)

# Local agent
requests             # HTTP client for polling cloud API
pyinstaller          # Package as Windows executable (dev dependency)
```

---

## Database Tables (Summary)

| Table | Purpose |
|---|---|
| clients | Client config (TJUK, ACS) — WhatsApp credentials, ERP type, business rules |
| customers | Customer master per client — phone numbers, codes, names |
| ship_to_addresses | Delivery addresses per customer (TJUK uses, ACS doesn't) |
| products | Product catalog per client — SKU codes, aliases, pack sizes, weights |
| order_history | Past orders per customer — for HIL checks and LLM context |
| conversations | Active conversation state per customer — state machine tracking |
| order_queue | Confirmed orders waiting for ERP sync |
| agent_heartbeats | Last heartbeat per local agent |
| audit_log | Every message received, sent, order created, synced — full trail |

---

*Last updated: 28 February 2026*
