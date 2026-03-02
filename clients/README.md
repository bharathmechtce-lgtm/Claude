# Clients

Each client gets their own folder with a standard structure. **Code never lives here** - only data, configs, and chat exports.

## Structure per Client

```
clients/
  <CLIENT_ID>/
    config/
      client_config.json    # Plug-and-play configuration
    chats/
      WhatsApp Chat *.zip   # Raw WhatsApp chat exports
    data/
      product_master.xlsx   # Product catalog
      customer_list.xlsx    # Customer list
      *.xlsx / *.csv        # Any client-specific data
```

## Adding a New Client

1. Copy `_template/` folder and rename to your client ID (e.g., `NEWCLIENT/`)
2. Fill in `config/client_config.json` with the client's details
3. Drop chat exports into `chats/`
4. Add product/customer data to `data/`
5. The core code will automatically pick up the new client via `client_loader.py`

## Current Clients

| Client ID | Name                        | ERP         | Region              |
|-----------|-----------------------------|-------------|---------------------|
| TJUK      | The Juicy Kitchen UK        | SAP B1      | India - Mumbai      |
| ACS       | Arvind/Aravind Snacks       | TBD         | India - Tamil Nadu  |
