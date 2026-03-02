# Project Folder Structure

```
whatsapp-ordering-bot/
├── clients/                          # CLIENT DATA (no code)
│   ├── _template/                    # Copy to onboard a new client
│   ├── TJUK/                         # The Juicy Kitchen UK
│   │   ├── config/client_config.json # Plug-and-play config
│   │   ├── chats/                    # WhatsApp chat exports
│   │   └── data/                     # Product master, customer lists
│   └── ACS/                          # Arvind/Aravind Snacks
│       ├── config/client_config.json
│       ├── chats/
│       └── data/
│
├── src/                              # ALL CODE (no data)
│   ├── webhook/                      # Flask webhook app
│   ├── core/client_loader.py         # Reads configs, selects adapters
│   └── client_adapters/              # ERP-specific adapters
│
├── testing/                          # ALL TESTING
│   ├── scenarios/                    # Test scripts
│   ├── benchmarks/                   # Test datasets & ground truth
│   ├── eval/                         # Scoring harness
│   └── results/                      # Timestamped results + log
│
├── data_exchange/                    # USER DATA DROP ZONE
│   ├── inbox/                        # Drop files here
│   └── processed/                    # Consumed files
│
├── project_management/               # Plans, handoff notes
├── docs/                             # Technical docs
├── docker-compose.yml                # Infrastructure
└── Caddyfile                         # Reverse proxy
```

## Key Principles

1. **Code vs Data**: `src/` = code, `clients/` = data. Never mix.
2. **Plug-and-play**: Copy `_template/`, fill config, done.
3. **Same code, different adapters**: `client_loader.py` picks adapter per client.
4. **Testing first-class**: Scripts, data, and timestamped results all tracked.
5. **Data exchange**: `data_exchange/inbox/` for uploads without disruption.
