# WhatsApp Order Bot — Tech Stack

## Finalized 28 February 2026

---

## Cloud Backend

| Component | Choice | Why |
|---|---|---|
| **Language** | Python 3.11+ | Most LLM libraries are Python-first. Claude SDK, Google Sheets API, WhatsApp API — all have mature Python support. Claude Code can generate Python well. |
| **Web Framework** | FastAPI | Handles webhooks (Meta sends messages here), serves Agent API. Async-native = handles concurrent messages well. Auto-generates API docs. |
| **Database** | PostgreSQL | Stores customers, products, conversations, order queue, audit log. JSONB support for flexible per-client fields. Battle-tested, free. |
| **LLM (heavy — orders + vision)** | Claude Sonnet 4.5 | For ORDER extraction, image/PDF reading, complex queries. 100% recall, 100% product match, 91% qty match in eval. ~$0.037/order. |
| **LLM (light — classify + simple)** | Claude Haiku 4.5 | For intent classification, greetings, simple price/catalog queries. ~10x cheaper than Sonnet. |
| **LLM SDK** | anthropic (Python) | Official Anthropic Python SDK — a Python library that handles authentication, request formatting, retries, image uploads. You call `client.messages.create(...)` and it talks to Claude's API. Free to install (`pip install anthropic`). The cost is in API usage, not the SDK itself. |
| **PDF extraction** | PyMuPDF (fitz) or pdf2image | Extract text from PDFs. If scanned/handwritten → convert pages to images → send to Sonnet vision. |
| **Google Sheets** | google-api-python-client | Google Sheets API v4. Append rows, update cells. Each client gets their own Sheet. |
| **WhatsApp** | Meta Cloud API (direct REST) | No BSP middleman. Direct HTTPS calls to send/receive. No SDK needed — just `requests`. |
| **Task scheduling** | APScheduler or Celery (later) | For: conversation timeout checks (every minute), heartbeat monitoring, Sheet status updates. Start with APScheduler (in-process, simple). Move to Celery if needed. |
| **Hosting** | Hetzner VPS (existing) | Cheapest option. Already familiar. Sufficient for 2 clients. Move to AWS Mumbai later if latency matters. |
| **Containerization** | Docker + docker-compose | One command to start/stop everything. New client = new config + restart. Portable to any server. |
| **SSL/Domain** | Let's Encrypt + Caddy | **Domain** (~$12/year): a URL like `botapi.yourdomain.com` that Meta sends WhatsApp messages to. Meta requires a real domain (not raw IP) with HTTPS. One domain covers both clients (use subdomains). **SSL**: Let's Encrypt gives free HTTPS certificates. Caddy auto-renews them. |

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

## LLM Model Strategy — Use the Right Model for Each Task

**Principle: Haiku first, Sonnet only when needed.** Most messages don't need the expensive model.

| Task | Model | Why | Approx Cost |
|---|---|---|---|
| **Intent classification** ("is this an order, query, or greeting?") | Haiku 4.5 | Simple classification. Haiku is fast and cheap. | ~$0.001/msg |
| **Order extraction** (parse items, match SKUs, convert quantities) | Sonnet 4.5 | The hard task. Needs high accuracy. Proven in eval. | ~$0.037/order |
| **Image / PDF processing** (handwritten lists, product photos, PO scans) | Sonnet 4.5 | Needs vision capability + accuracy | ~$0.04-0.06/image |
| **Price / catalog / delivery queries** | Haiku 4.5 | Simple lookup + format an answer | ~$0.001/query |
| **Greetings / simple replies** | Haiku 4.5 | Trivial | ~$0.0005/msg |
| **Order confirmation summary** | Haiku 4.5 | Format structured data into readable message | ~$0.001/msg |
| **Complex queries → HIL escalation** | Haiku 4.5 | Just needs to recognize it can't answer and escalate | ~$0.001/msg |

**Flow:** Every message → Haiku classifies intent → only ORDER/IMAGE/PDF/COMPLEX routes to Sonnet.

**Optimization approach:**
1. Build with Sonnet everywhere first (get accuracy right)
2. Measure actual costs per intent type with real messages
3. Swap Haiku in for easy tasks one by one
4. Keep testing — if Haiku degrades quality on a task, switch that task back to Sonnet
5. This is ongoing iteration, not a one-time decision

---

## Infrastructure Cost Estimate

| Item | Monthly Cost | Notes |
|---|---|---|
| Hetzner VPS (CX21 or CX31) | ~$8-15 | Server. Cheapest real cost. |
| Domain name | ~$1 | ~$12/year. One domain, subdomains are free. |
| Claude API — Sonnet (orders + vision only) | ~$60-100 | Only for ORDER extraction + image/PDF. ~170 orders/day. |
| Claude API — Haiku (classification + queries + greetings) | ~$15-30 | Everything else. Bulk of messages, low cost per call. |
| Google Sheets API | Free | Google's free tier is generous. |
| Meta WhatsApp Cloud API | ~$0-50 | First 1000 conversations/month free. After that ~$0.01-0.05/conversation. |
| **Total** | **~$85-200/month** | |

**Note:** These are estimates. Actual costs depend on message volume, how many are orders vs queries, and how much optimization we do. We'll measure and iterate.

At ₹25-40K/month per client revenue, this is profitable from client #1.

---

## Multi-Client WhatsApp Routing — How 2 Clients Share 1 Server

Each client (TJUK, ACS) has their own WhatsApp Business number. Both point to **one server, one webhook URL**.

```
TJUK's customers                         ACS's customers
      │                                        │
      ▼                                        ▼
TJUK WhatsApp Business #               ACS WhatsApp Business #
(phone_number_id: "111111")            (phone_number_id: "222222")
      │                                        │
      └────────────┐            ┌──────────────┘
                   ▼            ▼
        ┌────────────────────────────────┐
        │  YOUR SERVER (single VPS)       │
        │  https://yourdomain.com/webhook │
        │                                 │
        │  Incoming message includes      │
        │  phone_number_id field:         │
        │    "111111" → load TJUK config  │
        │    "222222" → load ACS config   │
        │                                 │
        │  Each config has:               │
        │    - its own product catalog    │
        │    - its own customer list      │
        │    - its own price lists        │
        │    - its own Google Sheet       │
        │    - its own WhatsApp API token │
        └────────────────────────────────┘
```

**Setup per client:**
1. Client creates a Meta Business Account (or uses existing one)
2. Client registers a WhatsApp Business phone number
3. Client gives you the API token and phone_number_id
4. You add a YAML config file on your server mapping that phone_number_id to their data
5. Both clients set their webhook URL to your same server

**One server, one codebase, one webhook. The `phone_number_id` in every incoming message is the routing key.**

---

## Key Dependencies (Python packages)

```
# Cloud backend
fastapi              # Web framework
uvicorn              # ASGI server for FastAPI
sqlalchemy           # Database ORM
psycopg2-binary      # PostgreSQL driver
anthropic            # Claude API SDK (includes vision/multimodal support)
google-api-python-client  # Google Sheets
google-auth          # Google auth for Sheets
httpx                # Async HTTP client (for WhatsApp API calls)
pyyaml               # Config file loading
apscheduler          # Periodic task scheduling
pydantic             # Data validation (built into FastAPI)
pymupdf              # PDF text extraction + page-to-image conversion
pillow               # Image handling (resize before sending to Sonnet)

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
| sales_reps | Sales rep master — phone numbers, assigned customer list per client |
| ship_to_addresses | Delivery addresses per customer (TJUK uses, ACS doesn't) |
| products | Product catalog per client — SKU codes, aliases, pack sizes, weights |
| price_lists | Price data per customer or per pricing tier — for price queries |
| order_history | Past orders per customer — for HIL checks and LLM context |
| conversations | Active conversation state per customer — state machine tracking |
| order_queue | Confirmed orders waiting for ERP sync |
| agent_heartbeats | Last heartbeat per local agent |
| audit_log | Every message received, sent, order created, synced — full trail |

---

*Last updated: 28 February 2026*
*Change log:*
*- v1: Initial tech stack*
*- v2: Added vision/multimodal (Sonnet handles images), PDF extraction, sales_reps + price_lists tables, revised cost estimate*
*- v3: Two-model strategy (Haiku + Sonnet), revised cost estimate ($85-200 vs $230-270), explained LLM SDK/domain/multi-client routing*
