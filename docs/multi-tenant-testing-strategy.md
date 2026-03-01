# Multi-Tenant Testing Strategy

**Date:** 01 March 2026
**Problem:** Akash has only 1 spare phone number (already used for TJUK). How to test multi-tenant routing without buying more numbers?

---

## The Good News: You Don't Need Multiple Real Numbers

Multi-tenant routing depends on `phone_number_id` — a Meta-assigned ID, NOT the actual phone number. You can test the routing logic without needing additional physical phones or WhatsApp numbers.

Here are 4 testing approaches, from easiest to most thorough:

---

## Approach 1: Simulated Webhook Payloads (No Extra Numbers Needed)

**Cost: Free | Effort: Low | Covers: Routing logic, config lookup, response generation**

Send fake webhook POST requests to your bot using `curl` or Postman, with different `phone_number_id` values. The bot doesn't know (or care) if the payload came from Meta or from your terminal.

### How It Works

```bash
# Simulate a message from "Client A" (phone_number_id = 111111)
curl -X POST http://localhost:8000/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "object": "whatsapp_business_account",
    "entry": [{
      "id": "WHATSAPP_BUSINESS_ACCOUNT_ID",
      "changes": [{
        "value": {
          "messaging_product": "whatsapp",
          "metadata": {
            "display_phone_number": "919999900001",
            "phone_number_id": "111111"
          },
          "messages": [{
            "from": "919876543210",
            "id": "wamid.test001",
            "timestamp": "1709270400",
            "text": { "body": "I want 5 packets of mixture" },
            "type": "text"
          }]
        },
        "field": "messages"
      }]
    }]
  }'

# Simulate a message from "Client B" (phone_number_id = 222222)
curl -X POST http://localhost:8000/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "object": "whatsapp_business_account",
    "entry": [{
      "id": "WHATSAPP_BUSINESS_ACCOUNT_ID",
      "changes": [{
        "value": {
          "messaging_product": "whatsapp",
          "metadata": {
            "display_phone_number": "919999900002",
            "phone_number_id": "222222"
          },
          "messages": [{
            "from": "919876543210",
            "id": "wamid.test002",
            "timestamp": "1709270400",
            "text": { "body": "I want 10 boxes of chips" },
            "type": "text"
          }]
        },
        "field": "messages"
      }]
    }]
  }'
```

### What You Verify

- Bot looks up `111111` → finds TJUK config → loads TJUK catalog → processes as TJUK
- Bot looks up `222222` → finds ACS config → loads ACS catalog → processes as ACS
- Bot receives unknown `333333` → logs error, does not crash
- Each reply uses the correct client's access token (check logs, don't actually send)

### Tip: Disable Actual WhatsApp Sending During Tests

Add a `DRY_RUN=true` mode to the bot:
```python
if os.getenv("DRY_RUN") == "true":
    log.info(f"[DRY_RUN] Would send to {phone} via token {token[:10]}...")
    return  # Don't call Meta API
```

This lets you test the full routing + processing pipeline without sending real WhatsApp messages (and without burning API quota).

---

## Approach 2: Meta Test Phone Numbers (Free, Limited)

**Cost: Free | Effort: Medium | Covers: End-to-end with real Meta API**

Meta provides **test phone numbers** in the WhatsApp Business API dashboard. You can use these to simulate a second client.

### How to Get Test Numbers

1. Go to [Meta Developer Dashboard](https://developers.facebook.com/)
2. Open your WhatsApp Business app → WhatsApp → API Setup
3. Under "From", you'll see a Meta-provided test number
4. Each Meta app you create gets its own test number with a separate `phone_number_id`

### What This Gives You

- A second `phone_number_id` to configure as "Client B" in your system
- You can send test messages FROM the dashboard (to up to 5 recipient numbers)
- Messages go through the real Meta infrastructure → real webhook payloads arrive at your bot

### Limitations

- Test numbers can only message numbers you've added as test recipients
- You cannot receive inbound messages TO a test number from arbitrary users
- Free tier allows 1,000 conversations/month — more than enough for testing

### Setup

```
Meta App 1 (TJUK):
  Test number: +1 555-XXX-XXXX
  phone_number_id: 111111 (from Meta)
  → Configure in your client registry as "tjuk"

Meta App 2 (ACS - for testing):
  Test number: +1 555-YYY-YYYY
  phone_number_id: 222222 (from Meta)
  → Configure in your client registry as "acs"

Both apps → webhook URL: https://your-bot.com/webhook
```

---

## Approach 3: Automated Unit/Integration Tests (Best for CI/CD)

**Cost: Free | Effort: Medium | Covers: Routing logic, regression prevention**

Write automated tests that validate multi-tenant routing without any external dependencies.

### Example Test Cases

```python
import pytest

class TestMultiTenantRouting:

    def test_known_client_routes_correctly(self):
        """Message with TJUK's phone_number_id loads TJUK config"""
        payload = make_webhook_payload(phone_number_id="111111", text="5 mixture")
        client = get_client_by_phone_number_id("111111")
        assert client.client_id == "tjuk"
        assert client.business_name == "TJUK Foods"

    def test_second_client_routes_correctly(self):
        """Message with ACS's phone_number_id loads ACS config"""
        payload = make_webhook_payload(phone_number_id="222222", text="10 chips")
        client = get_client_by_phone_number_id("222222")
        assert client.client_id == "acs"
        assert client.business_name == "Arvind Snacks"

    def test_unknown_phone_number_id_handled(self):
        """Message from unknown phone_number_id does not crash"""
        payload = make_webhook_payload(phone_number_id="999999", text="hello")
        client = get_client_by_phone_number_id("999999")
        assert client is None

    def test_inactive_client_rejected(self):
        """Message from inactive client is acknowledged but not processed"""
        payload = make_webhook_payload(phone_number_id="333333", text="order")
        client = get_client_by_phone_number_id("333333")
        assert client.active == False

    def test_correct_token_used_for_reply(self):
        """Reply function uses the client-specific token, not a global one"""
        client_a = get_client_by_phone_number_id("111111")
        client_b = get_client_by_phone_number_id("222222")
        assert client_a.access_token != client_b.access_token

    def test_correct_catalog_loaded(self):
        """Each client gets their own product catalog"""
        catalog_a = load_catalog(get_client_by_phone_number_id("111111"))
        catalog_b = load_catalog(get_client_by_phone_number_id("222222"))
        # TJUK has "mixture", ACS has "chips"
        assert any("mixture" in p.name for p in catalog_a)
        assert any("chips" in p.name for p in catalog_b)
```

---

## Approach 4: Two Real Meta Apps with Akash's Number (End-to-End)

**Cost: Free | Effort: Higher | Covers: Full real-world flow**

You already have 1 number used for TJUK. To do a true end-to-end test:

1. Create a **second Meta App** (call it "ACS Test")
2. Use the **Meta-provided test number** from that second app (no need for a real ACS number yet)
3. Point both apps' webhooks to the same bot URL
4. Send a message from app 1's test interface → verify TJUK routing
5. Send a message from app 2's test interface → verify ACS routing

This proves the full pipeline works end-to-end through real Meta infrastructure.

---

## Recommended Testing Order

```
Phase 1 (Now, during development):
  → Approach 1: Simulated payloads with curl/Postman
  → Approach 3: Automated unit tests
  → Fast feedback, no Meta dependency

Phase 2 (Before going live with client #2):
  → Approach 2: Meta test numbers from second app
  → Approach 4: Full end-to-end with real Meta apps
  → Proves real webhook routing works

Phase 3 (When ACS actually onboards):
  → ACS gets their real WhatsApp Business number
  → IIC creates their Meta app, registers in bot
  → Test with real messages from ACS's number
```

---

## Summary

| Approach | Numbers Needed | What It Tests | When to Use |
|----------|---------------|---------------|-------------|
| 1. Simulated payloads | 0 | Routing logic, config lookup | Development |
| 2. Meta test numbers | 0 (Meta provides) | Real webhook flow | Pre-launch |
| 3. Automated tests | 0 | Regression, CI/CD | Always |
| 4. Two Meta apps | 1 (yours) + 1 (test) | Full end-to-end | Before client #2 |

**Bottom line:** You can fully test multi-tenant routing with zero additional phone numbers. The routing key is `phone_number_id` (a Meta-assigned string), not the physical phone number — and you can simulate that in payloads all day long.

---

*Created: 01 March 2026*
