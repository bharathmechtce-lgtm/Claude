# Source Code

All executable code. No client data here.

## How Plug-and-Play Works

1. `core/client_loader.py` reads `clients/<ID>/config/client_config.json`
2. Based on `erp_config.type`, selects the right adapter from `client_adapters/`
3. All adapters expose: `create_order()`, `get_products()`, `get_customer()`
4. Webhook/bot code calls these without knowing which ERP is behind them
