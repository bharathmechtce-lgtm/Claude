#!/usr/bin/env python3
"""
Multi-Tenant WhatsApp Order Bot — Webhook Server (FastAPI)

Receives incoming WhatsApp messages via Meta Cloud API webhook,
routes them to the correct client context based on phone_number_id,
processes through Claude LLM with client-specific prompts/catalogs,
and sends replies back using the client's own WhatsApp credentials.

Run locally:
  uvicorn webhook.app:app --host 0.0.0.0 --port 8000 --reload

Production (Docker):
  docker compose up -d
"""

import hashlib
import hmac
import json
import logging
import os
import time

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))


def _load_dotenv(path):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


_load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

# Shared config
WEBHOOK_VERIFY_TOKEN = os.environ.get("WEBHOOK_VERIFY_TOKEN", "tjuk-bot-verify-2024")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "claude-sonnet-4-5-20250929")
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"

# Legacy single-tenant fallback
LEGACY_WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
LEGACY_PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
LEGACY_APP_SECRET = os.environ.get("WHATSAPP_APP_SECRET", "")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("order-bot")

# ---------------------------------------------------------------------------
# Client registry — built at startup from clients/ configs
# ---------------------------------------------------------------------------
# Maps phone_number_id -> client context dict
CLIENT_REGISTRY: dict = {}

# In-memory conversation state: {phone_number: {client_id, history, ...}}
conversations: dict = {}


def _build_client_registry():
    """Scan clients/ directory, load configs, build phone_number_id -> client map."""
    import sys
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
    from core.client_loader import list_clients, load_client, get_client_data_path

    global CLIENT_REGISTRY
    CLIENT_REGISTRY = {}

    clients = list_clients()
    log.info(f"Found {len(clients)} client(s): {clients}")

    for client_id in clients:
        try:
            config = load_client(client_id)
            wa_config = config.get("whatsapp_config", {})

            # Read WhatsApp credentials from env vars referenced in config
            phone_number_id = os.environ.get(wa_config.get("phone_number_id_env", ""), "")
            token = os.environ.get(wa_config.get("token_env", ""), "")
            app_secret = os.environ.get(wa_config.get("app_secret_env", ""), "")

            if not phone_number_id:
                log.warning(
                    f"Client '{client_id}': no phone_number_id configured "
                    f"(env var: {wa_config.get('phone_number_id_env', 'N/A')}). Skipping."
                )
                continue

            # Load product catalog for this client
            catalog = _load_client_catalog(client_id, config)

            # Build client-specific system prompt
            system_prompt = _build_system_prompt(client_id, config, catalog)

            CLIENT_REGISTRY[phone_number_id] = {
                "client_id": client_id,
                "client_name": config.get("client_name", client_id),
                "config": config,
                "phone_number_id": phone_number_id,
                "whatsapp_token": token,
                "app_secret": app_secret,
                "catalog": catalog,
                "catalog_by_code": {p.get("item_code", p.get("ItemCode", "")): p for p in catalog},
                "system_prompt": system_prompt,
            }
            log.info(
                f"Registered client '{client_id}' — phone_number_id={phone_number_id}, "
                f"catalog={len(catalog)} products"
            )
        except Exception as e:
            log.error(f"Failed to load client '{client_id}': {e}", exc_info=True)

    # Legacy fallback: if no clients registered but legacy env vars exist
    if not CLIENT_REGISTRY and LEGACY_PHONE_NUMBER_ID and LEGACY_WHATSAPP_TOKEN:
        log.warning("No multi-tenant clients found. Using legacy single-tenant config.")
        catalog = _load_legacy_catalog()
        CLIENT_REGISTRY[LEGACY_PHONE_NUMBER_ID] = {
            "client_id": "legacy",
            "client_name": "Legacy Client",
            "config": {},
            "phone_number_id": LEGACY_PHONE_NUMBER_ID,
            "whatsapp_token": LEGACY_WHATSAPP_TOKEN,
            "app_secret": LEGACY_APP_SECRET,
            "catalog": catalog,
            "catalog_by_code": {p.get("item_code", ""): p for p in catalog},
            "system_prompt": _build_default_system_prompt(),
        }

    log.info(f"Client registry: {len(CLIENT_REGISTRY)} client(s) active")


def _load_client_catalog(client_id, config):
    """Load product catalog for a specific client."""
    # Try client-specific catalog file first
    catalog_config = config.get("product_catalog", {})

    # Check for JSON catalog in client data dir
    data_dir = os.path.join(PROJECT_ROOT, "clients", client_id, "data")
    json_catalog = os.path.join(data_dir, "product_catalog.json")
    if os.path.exists(json_catalog):
        with open(json_catalog) as f:
            catalog = json.load(f)
        log.info(f"Client '{client_id}': loaded {len(catalog)} products from {json_catalog}")
        return catalog

    # Check for catalog in testing/benchmarks (TJUK legacy location)
    legacy_catalog = os.path.join(PROJECT_ROOT, "testing", "benchmarks", "product_catalog.json")
    if client_id == "TJUK" and os.path.exists(legacy_catalog):
        with open(legacy_catalog) as f:
            catalog = json.load(f)
        log.info(f"Client '{client_id}': loaded {len(catalog)} products from legacy catalog")
        return catalog

    log.warning(f"Client '{client_id}': no product catalog found")
    return []


def _load_legacy_catalog():
    """Load catalog from legacy location (testing/benchmarks/)."""
    catalog_path = os.path.join(PROJECT_ROOT, "testing", "benchmarks", "product_catalog.json")
    if os.path.exists(catalog_path):
        with open(catalog_path) as f:
            return json.load(f)
    return []


def _build_system_prompt(client_id, config, catalog):
    """Build a client-specific system prompt based on their config."""
    client_name = config.get("client_name", client_id)
    industry = config.get("industry", "food distribution")
    region = config.get("region", "India")
    languages = config.get("whatsapp_config", {}).get("languages", ["English"])
    lang_str = ", ".join(languages)

    prompt = f"""You are a WhatsApp order assistant for {client_name}, a {industry} company in {region}.
You are chatting 1-on-1 with a customer via WhatsApp. Be helpful, concise, and natural.

LANGUAGE RULES:
- Customers may write in {lang_str} or mix these languages.
  Understand ALL of these languages.
- Reply in the SAME language the customer uses. Default to English if unclear.

YOUR BEHAVIOR:
1. When the customer sends an order, acknowledge it naturally ("Got it!" / "Noted!" etc.)
2. Read the items and quantities they mention — confirm what you understood
3. If location/outlet is missing, ask for it
4. If a product name is ambiguous, ask for clarification
5. Handle "add" messages by merging into the current order
6. Handle "cancel" / "remove" messages by updating the order
7. Keep a RUNNING ORDER in your head — after each interaction, you know the full order state
8. Be conversational but efficient — these are busy business people

ANTI-HALLUCINATION RULES (CRITICAL — follow these strictly):
- ONLY include items the customer EXPLICITLY mentioned or asked for
- NEVER infer, suggest, or add items the customer did not ask for
- NEVER add items "they might also need" or "usually ordered together"
- When extracting the order to JSON, list ONLY the items from the conversation. Zero extras
- If in doubt whether the customer asked for something, DO NOT include it — ask instead
- Count your output items against the customer's message. If you have MORE items than the customer mentioned, you are hallucinating — remove the extras

QUANTITY CONVERSION RULES (customers speak in cases/kg/box, ERP records in PCS):

  CASE/BOX: "X case" or "X box" → quantity = X × PackSize (from catalogue)
  KG: "X kg" → quantity = X ÷ UnitWeight (from catalogue)
  DIRECT (no conversion — just count as PCS):
    "X pcs/btl/pkt/nos/block/bulk/tin/bag" → quantity = X PCS

PRODUCT MATCHING RULES:
- Match customer text to the PRODUCT CATALOGUE provided in context
- Use the catalogue item_code and item_name — do NOT invent item codes
- If a customer's product text matches multiple catalogue items, pick the one with the closest name match
- If match confidence is low, ASK for clarification rather than guessing
- If you genuinely cannot find a match, say so — do NOT fabricate a product or code
- GENERIC TERMS: When a customer uses a generic term WITHOUT specifying a brand, ask which product they want

ORDER CONFIRMATION (before finalizing):
- When the customer seems done ordering, show a COMPLETE ORDER SUMMARY
- Format: numbered list with item name, quantity, and delivery location
- Ask: "Please confirm this order, or let me know if any changes are needed"
- Only after customer confirms should you consider the order final

Respond naturally as a WhatsApp assistant. Keep responses SHORT (2-4 lines max).
Do NOT output JSON unless specifically asked. Just chat naturally."""

    return prompt


def _build_default_system_prompt():
    """Fallback system prompt for legacy single-tenant mode."""
    return _build_system_prompt("TJUK", {
        "client_name": "TJUK",
        "industry": "F&B Distribution",
        "region": "India - Mumbai",
        "whatsapp_config": {
            "languages": ["English", "Hindi", "Hinglish", "Marathi", "Gujarati"]
        },
    }, [])


# ---------------------------------------------------------------------------
# Async HTTP client
# ---------------------------------------------------------------------------
http_client: httpx.AsyncClient | None = None

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="WhatsApp Order Bot (Multi-Tenant)", version="2.0.0")


@app.on_event("startup")
async def startup():
    global http_client
    http_client = httpx.AsyncClient(timeout=30)

    _build_client_registry()

    if not ANTHROPIC_KEY:
        log.warning("Missing ANTHROPIC_API_KEY — LLM calls will fail")

    active = [c["client_id"] for c in CLIENT_REGISTRY.values()]
    log.info(f"WhatsApp Order Bot started — model: {LLM_MODEL}, clients: {active}")


@app.on_event("shutdown")
async def shutdown():
    if http_client:
        await http_client.aclose()


# ---------------------------------------------------------------------------
# GET /webhook — Meta verification (shared across all clients)
# ---------------------------------------------------------------------------
@app.get("/webhook")
async def verify_webhook(request: Request):
    """Meta sends GET with hub.mode, hub.verify_token, hub.challenge."""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == WEBHOOK_VERIFY_TOKEN:
        log.info("Webhook verified successfully")
        return PlainTextResponse(challenge, status_code=200)

    log.warning("Webhook verification failed: token mismatch")
    return PlainTextResponse("Forbidden", status_code=403)


# ---------------------------------------------------------------------------
# POST /webhook — incoming messages (multi-tenant)
# ---------------------------------------------------------------------------
@app.post("/webhook")
async def handle_webhook(request: Request):
    """Receive incoming WhatsApp messages, route to correct client."""
    raw_body = await request.body()
    body = await request.json()

    if not body:
        return PlainTextResponse("OK", status_code=200)

    try:
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                metadata = value.get("metadata", {})
                phone_number_id = metadata.get("phone_number_id", "")

                # --- Multi-tenant routing ---
                client_ctx = CLIENT_REGISTRY.get(phone_number_id)

                if not client_ctx:
                    log.warning(
                        f"Unknown phone_number_id: {phone_number_id}. "
                        f"Registered: {list(CLIENT_REGISTRY.keys())}. "
                        f"Message ignored."
                    )
                    continue

                client_id = client_ctx["client_id"]

                # Validate signature using client-specific app secret
                if client_ctx["app_secret"]:
                    signature = request.headers.get("X-Hub-Signature-256", "")
                    if not _verify_signature(raw_body, signature, client_ctx["app_secret"]):
                        log.warning(f"[{client_id}] Invalid webhook signature — ignoring")
                        return PlainTextResponse("Forbidden", status_code=403)

                messages = value.get("messages", [])
                for msg in messages:
                    sender_phone = msg.get("from", "")
                    msg_type = msg.get("type", "")

                    if msg_type == "text":
                        text = msg.get("text", {}).get("body", "")
                        if text:
                            log.info(f"[{client_id}] Message from {sender_phone}: {text[:80]}")
                            await _handle_incoming_message(sender_phone, text, client_ctx)
                    else:
                        log.info(
                            f"[{client_id}] Ignoring non-text message type: {msg_type} "
                            f"from {sender_phone}"
                        )

    except Exception as e:
        log.error(f"Error processing webhook: {e}", exc_info=True)

    # Always return 200 quickly — Meta retries on non-200
    return PlainTextResponse("OK", status_code=200)


# ---------------------------------------------------------------------------
# Signature verification (per-client app secret)
# ---------------------------------------------------------------------------
def _verify_signature(payload: bytes, signature_header: str, app_secret: str) -> bool:
    """Verify X-Hub-Signature-256 from Meta using client-specific secret."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(
        app_secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature_header)


# ---------------------------------------------------------------------------
# Message processing (client-aware)
# ---------------------------------------------------------------------------
async def _handle_incoming_message(phone: str, text: str, client_ctx: dict):
    """Process incoming message with client-specific context."""
    client_id = client_ctx["client_id"]
    # Conversation key includes client_id to isolate contexts
    conv_key = f"{client_id}:{phone}"

    if conv_key not in conversations:
        conversations[conv_key] = {
            "client_id": client_id,
            "history": [],
            "last_message_time": 0,
            "order_confirmed": False,
        }

    conv = conversations[conv_key]
    conv["last_message_time"] = time.time()

    # Reset order confirmation after 30-min gap
    if conv["order_confirmed"] and (time.time() - conv.get("confirmed_at", 0)) > 1800:
        conv["order_confirmed"] = False

    # Add user message
    conv["history"].append({"role": "user", "content": text})

    # Call LLM with client-specific system prompt
    response_text = await _call_claude(conv["history"], client_ctx["system_prompt"])

    # Track confirmation state
    confirm_keywords = ["confirm this order", "please confirm", "any changes"]
    if any(kw in response_text.lower() for kw in confirm_keywords):
        conv["awaiting_confirmation"] = True

    if conv.get("awaiting_confirmation"):
        confirm_words = ["yes", "ok", "confirm", "done", "all good", "correct",
                         "perfect", "looks good", "go ahead", "confirmed"]
        text_lower = text.lower().strip()
        if any(w in text_lower for w in confirm_words):
            conv["order_confirmed"] = True
            conv["confirmed_at"] = time.time()
            conv["awaiting_confirmation"] = False
            log.info(f"[{client_id}] Order confirmed by {phone}")

    conv["history"].append({"role": "assistant", "content": response_text})

    # Send reply using CLIENT-SPECIFIC WhatsApp credentials
    await _send_whatsapp_message(phone, response_text, client_ctx)

    # Keep history manageable
    if len(conv["history"]) > 50:
        conv["history"] = conv["history"][-50:]


# ---------------------------------------------------------------------------
# Claude API caller
# ---------------------------------------------------------------------------
async def _call_claude(messages: list, system_prompt: str) -> str:
    """Call Claude API with client-specific system prompt."""
    try:
        resp = await http_client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": LLM_MODEL,
                "max_tokens": 1024,
                "system": system_prompt,
                "messages": messages,
            },
        )
        data = resp.json()

        if resp.status_code != 200:
            err = data.get("error", {}).get("message", str(data))
            log.error(f"Claude API error: {err}")
            return "Sorry, I'm having trouble processing your message. Please try again."

        text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                text += block["text"]

        in_tok = data["usage"]["input_tokens"]
        out_tok = data["usage"]["output_tokens"]
        log.info(f"Claude: {in_tok} in / {out_tok} out tokens")
        return text

    except Exception as e:
        log.error(f"Claude API exception: {e}")
        return "Sorry, I'm having trouble right now. Please try again in a moment."


# ---------------------------------------------------------------------------
# WhatsApp Cloud API — send message (per-client credentials)
# ---------------------------------------------------------------------------
async def _send_whatsapp_message(to_phone: str, text: str, client_ctx: dict):
    """Send a text message using the client's own WhatsApp credentials."""
    client_id = client_ctx["client_id"]
    phone_number_id = client_ctx["phone_number_id"]
    token = client_ctx["whatsapp_token"]

    if not token:
        log.error(f"[{client_id}] No WhatsApp token configured — cannot send reply")
        return

    if DRY_RUN:
        log.info(
            f"[DRY_RUN] [{client_id}] Would send to {to_phone} "
            f"via phone_number_id={phone_number_id}: {text[:80]}..."
        )
        return

    url = f"https://graph.facebook.com/v21.0/{phone_number_id}/messages"
    try:
        resp = await http_client.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "messaging_product": "whatsapp",
                "to": to_phone,
                "type": "text",
                "text": {"body": text},
            },
        )
        if resp.status_code == 200:
            log.info(f"[{client_id}] Reply sent to {to_phone}")
        else:
            log.error(
                f"[{client_id}] WhatsApp send failed ({resp.status_code}): {resp.text}"
            )
    except Exception as e:
        log.error(f"[{client_id}] WhatsApp send exception: {e}")


# ---------------------------------------------------------------------------
# Health check & status endpoints
# ---------------------------------------------------------------------------
@app.get("/")
async def health():
    clients = {
        pid: {
            "client_id": ctx["client_id"],
            "client_name": ctx["client_name"],
            "catalog_size": len(ctx["catalog"]),
            "has_token": bool(ctx["whatsapp_token"]),
        }
        for pid, ctx in CLIENT_REGISTRY.items()
    }
    return {
        "status": "ok",
        "service": "WhatsApp Order Bot (Multi-Tenant)",
        "model": LLM_MODEL,
        "dry_run": DRY_RUN,
        "active_clients": clients,
        "active_conversations": len(conversations),
    }


@app.get("/clients")
async def list_active_clients():
    """List all registered clients and their status."""
    result = []
    for pid, ctx in CLIENT_REGISTRY.items():
        result.append({
            "client_id": ctx["client_id"],
            "client_name": ctx["client_name"],
            "phone_number_id": pid,
            "catalog_products": len(ctx["catalog"]),
            "has_whatsapp_token": bool(ctx["whatsapp_token"]),
            "languages": ctx["config"].get("whatsapp_config", {}).get("languages", []),
        })
    return {"clients": result, "total": len(result)}
