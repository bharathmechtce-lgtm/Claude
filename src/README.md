# Source Code

All executable code lives here. **No client data in this directory.**

## Structure

```
src/
  webhook/              # Flask webhook app (WhatsApp incoming messages)
    app.py              # Main webhook handler
    Dockerfile          # Container build
    requirements.txt    # Python dependencies
  core/                 # Shared business logic
    client_loader.py    # Plug-and-play client config loader
  client_adapters/      # ERP-specific adapter functions
    sap_b1_adapter.py   # SAP Business One integration
    generic_adapter.py  # Fallback for no-ERP clients
```

## How the Plug-and-Play System Works

1. `core/client_loader.py` reads `clients/<ID>/config/client_config.json`
2. Based on `erp_config.type`, it selects the right adapter from `client_adapters/`
3. All adapters expose the same interface: `create_order()`, `get_products()`, `get_customer()`
4. The webhook/bot code calls these functions without knowing which ERP is behind them

## Adding a New ERP Integration

1. Create `src/client_adapters/<erp_name>_adapter.py`
2. Implement: `create_order(config, data)`, `get_products(config)`, `get_customer(config, phone)`
3. Register in `core/client_loader.py` → `adapter_map`
