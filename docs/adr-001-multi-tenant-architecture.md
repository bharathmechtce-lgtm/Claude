# ADR-001: Multi-Tenant Architecture for WhatsApp Ordering Bot

**Status:** Accepted
**Date:** 01 March 2026
**Stakeholders:** Bharath (IIC), Akash (IIC)
**Relates to:** v0.3.0 roadmap milestone

---

## Context

The MVP (v0.1.0) is built for a single client — TJUK. One set of WhatsApp API credentials lives in `.env`, and the bot handles messages for one business number only.

As IIC onboards more clients (ACS being the next), the system must support multiple WhatsApp Business numbers, each with their own product catalog, customer base, pricing, and conversation context — all without deploying separate bot instances for each client.

The core question: **How do we route incoming WhatsApp messages to the correct client context and reply using the correct credentials?**

---

## Decisions

### Decision 1: One Meta Business App Per Client

**Each client gets their own Meta Business App**, owned and managed by IIC.

| Aspect | Decision |
|--------|----------|
| Meta app ownership | IIC creates and owns all Meta apps |
| WhatsApp number | Each client brings their own business number |
| Access tokens | Each client's Meta app generates its own token |
| Webhook URL | All Meta apps point to the **same** webhook URL |

**Rationale:**
- IIC owning the Meta apps ensures full control over API access, webhook configuration, and token rotation.
- Included as part of one-time client setup cost.
- If anything changes (server migration, webhook URL update), IIC can update all apps without depending on clients.

### Decision 2: Single Bot Instance, Multi-Tenant Routing (Option A)

**One bot instance handles all clients.** Routing is done using `phone_number_id` from the webhook payload.

```
Meta Cloud API (Client 1, 2, ... N)
  │
  │  POST /webhook (all clients hit same URL)
  │
  ▼
┌─────────────────────────────────┐
│  Single Bot Instance            │
│                                 │
│  1. Extract phone_number_id     │ ← Identifies WHICH client
│     from webhook payload        │
│                                 │
│  2. Lookup client config in DB  │ ← Credentials, catalog, prompt
│                                 │
│  3. Process with client-        │
│     specific context            │
│                                 │
│  4. Reply using client's own    │
│     WhatsApp access token       │
└─────────────────────────────────┘
```

**Why Option A (shared instance) over Option B (separate instance per client):**

| Factor | Option A (Shared) | Option B (Separate) |
|--------|--------------------|---------------------|
| Infrastructure | 1 VPS, 1 container | N containers, more RAM/CPU |
| Cost | Low, fixed | Scales linearly with clients |
| Deployment | One deploy updates all | Must deploy N times |
| Isolation | Lower (shared process) | Full isolation |
| Complexity | Routing logic needed | Simple but ops-heavy |
| Suitable for | First 5–10 clients | 50+ clients or strict SLAs |

**Decision:** Start with Option A. Revisit when client count exceeds 10 or if isolation requirements emerge.

### Decision 3: Client Registry Data Model

All per-client configuration is stored in a centralized registry (database table or config files).

```
clients table:
┌─────────────┬────────────────────┬───────────────┬───────────────────┐
│ client_id   │ phone_number_id    │ access_token  │ business_name     │
├─────────────┼────────────────────┼───────────────┼───────────────────┤
│ tjuk        │ 112233445566       │ EAAx... (enc) │ TJUK Foods        │
│ acs         │ 998877665544       │ EAAy... (enc) │ Arvind Snacks     │
└─────────────┴────────────────────┴───────────────┴───────────────────┘

Additional fields per client:
  - catalog_file (path or reference to product catalog)
  - system_prompt (client-specific bot personality/instructions)
  - welcome_message (greeting text for new conversations)
  - delivery_schedule (JSON — delivery days, cutoff times)
  - modification_cutoff_hours (integer)
  - google_sheet_id (for ops visibility)
  - active (boolean — can disable a client without deleting)
  - created_at (timestamp)
  - updated_at (timestamp)
```

### Decision 4: Token Security

Access tokens are **encrypted at rest** — not stored in plain text in `.env` or config files.

- MVP uses `.env` with a single token (acceptable for one client).
- Multi-tenant version stores tokens encrypted in PostgreSQL (using application-level encryption).
- Tokens are decrypted in memory only when making API calls.

### Decision 5: Webhook Verify Token

A **single shared verify token** is used across all Meta apps for webhook verification.

- Simpler to manage than per-client verify tokens.
- The verify token only proves the webhook URL is ours — actual message authentication uses the app-specific payload signatures.

---

## Client Onboarding Flow

When IIC signs up a new client, the following steps are performed:

```
Step 1: Create Meta Business App
        → Register client's WhatsApp Business number
        → Generate permanent ACCESS_TOKEN
        → Record PHONE_NUMBER_ID

Step 2: Register client in bot system
        → Add entry to clients table / config
        → Upload product catalog (Excel → structured data)
        → Configure system prompt, welcome message
        → Set delivery schedule + cutoff rules
        → Create Google Sheet for ops visibility

Step 3: Configure webhook in Meta App
        → URL: https://<bot-domain>/webhook
        → Verify token: <shared verify token>
        → Subscribe to messages, message_deliveries

Step 4: Load client data
        → Import customer master (phones, names, codes)
        → Import sales rep master (phones, assigned customers)
        → Import price lists
        → Import order history (for HIL baselines)

Step 5: Test
        → Send test message from client's number
        → Confirm routing, catalog lookup, reply
        → Client is live ✅
```

**Estimated onboarding time:** 2–4 hours (mostly data preparation).

---

## Message Routing Logic (Pseudocode)

```python
@app.post("/webhook")
def handle_webhook(payload):
    phone_number_id = payload["entry"][0]["changes"][0]["value"]["metadata"]["phone_number_id"]

    # Lookup client by phone_number_id
    client = get_client_by_phone_number_id(phone_number_id)

    if not client:
        log.error(f"Unknown phone_number_id: {phone_number_id}")
        return 200  # Acknowledge but don't process

    if not client.active:
        log.warn(f"Client {client.client_id} is inactive")
        return 200

    # Load client-specific context
    catalog = load_catalog(client.catalog_file)
    token = decrypt_token(client.access_token)

    # Process message with client context
    process_message(
        message=payload,
        client=client,
        catalog=catalog,
        reply_token=token
    )

    return 200
```

---

## Impact on Current Codebase (v0.1.0 → v0.3.0)

To make the multi-tenant transition smooth, the MVP code should follow these practices:

1. **Centralize config access** — Don't scatter `os.getenv("WHATSAPP_TOKEN")` across files. Use a single config loader.
2. **Centralize reply function** — One function sends WhatsApp replies. Swapping to per-client tokens later = one-place change.
3. **Keep catalog loading modular** — Catalog should be loadable from a path/reference, not hardcoded.
4. **Tag logs with client context** — Even in MVP (single client), include `client_id` in log lines for future filtering.

---

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Single instance failure affects all clients | Health checks + auto-restart (Docker restart policy). Revisit with Option B at scale. |
| Token leak exposes all clients | Encrypt tokens at rest. Rotate tokens periodically. Limit API permissions. |
| One client's high volume slows others | Rate limiting per client. Queue-based processing. |
| Meta app policy changes | IIC monitors Meta developer announcements. All apps under one Business Manager. |

---

## Alternatives Considered

### Option B: Separate Bot Instance Per Client
- Rejected for initial rollout due to higher cost and operational overhead.
- Will be reconsidered at 10+ clients or if a client requires strict data isolation (e.g., regulatory).

### Per-Client Webhook URLs (e.g., /webhook/tjuk, /webhook/acs)
- Rejected because `phone_number_id` already provides routing.
- Adding client ID to the URL is redundant and creates maintenance burden when URLs change.

---

*Approved by: Bharath, Akash — 01 March 2026*
