# Clients

Each client gets their own folder. **Code never lives here** - only data, configs, and chat exports.

## Adding a New Client

1. Copy `_template/` and rename to your client ID
2. Fill in `config/client_config.json`
3. Drop chat exports into `chats/`
4. Add product/customer data to `data/`
5. Core code auto-detects new clients via `client_loader.py`

## Current Clients

| Client ID | Name                        | ERP    | Region             |
|-----------|-----------------------------|--------|--------------------|
| TJUK      | The Juicy Kitchen UK        | SAP B1 | India - Mumbai     |
| ACS       | Arvind/Aravind Snacks       | TBD    | India - Tamil Nadu |
