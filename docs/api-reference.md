# API Reference

> **Last Updated:** 2026-03-01 (MVP)
> **Update this document** whenever endpoints, request/response formats, or external API usage changes.

---

## Bot Endpoints

### `GET /` — Health Check

Returns the service status and active conversation count.

**Response:**
```json
{
  "status": "ok",
  "service": "TJUK WhatsApp Order Bot",
  "active_conversations": 3
}
```

---

### `GET /webhook` — Meta Webhook Verification

Used once during webhook setup. Meta sends a verification challenge.

**Query Parameters:**
| Parameter | Description |
|-----------|-------------|
| `hub.mode` | Must be `subscribe` |
| `hub.verify_token` | Must match `WEBHOOK_VERIFY_TOKEN` env var |
| `hub.challenge` | Challenge string to return |

**Response:** Returns the challenge string as plain text (200) or "Forbidden" (403).

---

### `POST /webhook` — Incoming WhatsApp Messages

Receives incoming messages from Meta Cloud API.

**Headers:**
| Header | Description |
|--------|-------------|
| `X-Hub-Signature-256` | HMAC-SHA256 signature for payload verification |

**Request Body (from Meta):**
```json
{
  "entry": [{
    "changes": [{
      "value": {
        "messages": [{
          "from": "919876543210",
          "type": "text",
          "text": { "body": "I need 5 cases of paneer" }
        }]
      }
    }]
  }]
}
```

**Behaviour:**
1. Validates HMAC signature
2. Extracts text messages (ignores non-text for now)
3. Calls Claude with conversation history
4. Sends reply via WhatsApp
5. Always returns HTTP 200 (Meta retries on non-200)

---

## External APIs Used

### Anthropic Claude API

**Endpoint:** `https://api.anthropic.com/v1/messages`
**Method:** POST
**Auth:** `x-api-key` header

**Request:**
```json
{
  "model": "claude-sonnet-4-5-20250929",
  "max_tokens": 1024,
  "system": "<system prompt>",
  "messages": [
    {"role": "user", "content": "5 cases paneer"},
    {"role": "assistant", "content": "Got it! ..."}
  ]
}
```

**Response fields used:**
| Field | Usage |
|-------|-------|
| `content[].text` | Bot reply text |
| `usage.input_tokens` | Logged for cost tracking |
| `usage.output_tokens` | Logged for cost tracking |

**Docs:** https://docs.anthropic.com/en/api/messages

---

### WhatsApp Cloud API — Send Message

**Endpoint:** `https://graph.facebook.com/v21.0/{phone_number_id}/messages`
**Method:** POST
**Auth:** `Bearer` token

**Request:**
```json
{
  "messaging_product": "whatsapp",
  "to": "919876543210",
  "type": "text",
  "text": { "body": "Got it! 5 cases of paneer noted." }
}
```

**Docs:** https://developers.facebook.com/docs/whatsapp/cloud-api/messages/text-messages

---

## Planned Endpoints (Future)

| Version | Endpoint | Purpose |
|---------|----------|---------|
| v0.4.0 | `GET /orders` | List orders from database |
| v0.4.0 | `GET /orders/{id}` | Get order details |
| v0.5.0 | `GET /orders/{id}/export` | Download Excel file for order |
| v1.0.0 | `POST /agent/poll` | Local ERP agent polls for confirmed orders |
| v1.0.0 | `POST /agent/sync` | Agent reports ERP sync status |
