"""
Client Adapters - Plug-and-play adapter functions per client/ERP type.

Each adapter exposes the same interface so the core bot code
can call the right functions based on client_config.json settings.

To add a new ERP integration:
1. Create: src/client_adapters/<erp_name>_adapter.py
2. Implement: create_order(), get_products(), get_customer()
3. Register in core/client_loader.py adapter_map
"""
