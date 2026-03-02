# Project Folder Structure

```
whatsapp-ordering-bot/
│
├── clients/                          # CLIENT DATA (no code here)
│   ├── README.md                     # How to add/manage clients
│   ├── _template/                    # Copy this to onboard a new client
│   │   ├── config/client_config.json
│   │   ├── chats/.gitkeep
│   │   └── data/.gitkeep
│   ├── TJUK/                         # The Juicy Kitchen UK
│   │   ├── config/client_config.json # Plug-and-play config
│   │   ├── chats/                    # WhatsApp chat exports (.zip)
│   │   └── data/                     # Product master, customer lists, scraped data
│   └── ACS/                          # Arvind/Aravind Snacks
│       ├── config/client_config.json
│       ├── chats/
│       └── data/
│
├── src/                              # ALL CODE (no data here)
│   ├── README.md
│   ├── webhook/                      # Flask webhook app
│   │   ├── app.py
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   ├── core/                         # Shared business logic
│   │   └── client_loader.py          # Reads client configs, selects adapters
│   └── client_adapters/              # ERP-specific adapters (plug-and-play)
│       ├── sap_b1_adapter.py         # SAP Business One
│       └── generic_adapter.py        # Fallback (local files)
│
├── testing/                          # ALL TESTING
│   ├── README.md
│   ├── scenarios/                    # Test execution scripts
│   │   ├── benchmark_eval.py
│   │   ├── conversational_test.py
│   │   ├── run_all_haiku.py
│   │   ├── two_agent_sim.py
│   │   └── ...
│   ├── benchmarks/                   # Test datasets & ground truth
│   │   ├── test_scenarios.json
│   │   ├── product_catalog.json
│   │   ├── HIL_Benchmark.xlsx
│   │   └── ...
│   ├── eval/                         # Evaluation harness (scoring)
│   │   ├── eval_harness.py
│   │   └── eval_harness_v2.py
│   └── results/                      # Timestamped results + log
│       ├── README.md                 # What/why/results/timestamp table
│       └── *.json / *.xlsx           # Actual result files
│
├── data_exchange/                    # USER DATA DROP ZONE
│   ├── README.md
│   ├── inbox/                        # Drop files here for processing
│   └── processed/                    # Consumed files moved here
│
├── project_management/               # PLANNING & HANDOFF
│   ├── README.md
│   ├── WHATSAPP_ORDERING_BOT_PROJECT_BRIEF.md
│   ├── CLAUDE_CODE_HANDOFF*.md
│   └── WhatsApp_Order_Bot_Plan.docx
│
├── docs/                             # TECHNICAL DOCUMENTATION
│   ├── architecture.md
│   ├── process-flow.md
│   ├── api-reference.md
│   ├── sap-b1-service-layer-integration.md
│   └── ...
│
├── docker-compose.yml                # Infrastructure (root level)
├── Caddyfile                         # Reverse proxy config
├── .env.example                      # Environment template
├── .gitignore
└── FOLDER_STRUCTURE.md               # This file
```

## Design Principles

1. **Code vs Data separation**: `src/` has all code, `clients/` has all data. Never mix them.
2. **Plug-and-play clients**: Copy `_template/`, fill in `client_config.json`, done.
3. **Same code, different adapters**: `client_loader.py` reads config and picks the right adapter functions.
4. **Testing is first-class**: Dedicated folder with scripts, data, and timestamped results.
5. **Data exchange without disruption**: `data_exchange/inbox/` is your upload zone.

## Re: SQL Error "Invalid column name 'ItmsGrpCod'"

This is noted in TJUK's `client_config.json` under `product_catalog.item_group_field`. The SAP B1 field name may differ between installations - the adapter should use the config value rather than hardcoding it.
