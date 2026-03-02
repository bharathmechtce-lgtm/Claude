"""
Client Loader - Plug-and-play client configuration system.

Reads client_config.json from the clients/ directory and provides
a unified interface for the bot to adapt its behavior per client.

Usage:
    from core.client_loader import load_client, list_clients

    # Load a specific client
    client = load_client("TJUK")
    print(client["product_catalog"]["matching_strategy"])

    # List all available clients
    for client_id in list_clients():
        print(client_id)
"""

import json
import os
from pathlib import Path

# Resolve clients/ directory relative to this file
CLIENTS_DIR = Path(__file__).resolve().parent.parent.parent / "clients"


def list_clients():
    """Return list of client IDs (folder names) that have a valid config."""
    clients = []
    if not CLIENTS_DIR.exists():
        return clients
    for entry in sorted(CLIENTS_DIR.iterdir()):
        if entry.is_dir() and not entry.name.startswith("_"):
            config_path = entry / "config" / "client_config.json"
            if config_path.exists():
                clients.append(entry.name)
    return clients


def load_client(client_id):
    """Load and return client configuration dict.

    Args:
        client_id: Folder name under clients/ (e.g. "TJUK", "ACS")

    Returns:
        dict with full client configuration

    Raises:
        FileNotFoundError: if client folder or config doesn't exist
        json.JSONDecodeError: if config file is malformed
    """
    config_path = CLIENTS_DIR / client_id / "config" / "client_config.json"
    if not config_path.exists():
        raise FileNotFoundError(
            f"No config found for client '{client_id}' at {config_path}"
        )
    with open(config_path, "r") as f:
        return json.load(f)


def get_client_data_path(client_id):
    """Return the Path to a client's data/ directory."""
    return CLIENTS_DIR / client_id / "data"


def get_client_chats_path(client_id):
    """Return the Path to a client's chats/ directory."""
    return CLIENTS_DIR / client_id / "chats"


def get_erp_adapter(client_config):
    """Return the appropriate ERP adapter function name based on client config.

    This is the plug-and-play mechanism: same core code calls different
    adapter functions based on the client's ERP type.
    """
    erp_type = client_config.get("erp_config", {}).get("type", "unknown")
    adapter_map = {
        "sap_b1": "client_adapters.sap_b1_adapter",
        "tally": "client_adapters.tally_adapter",
        "zoho": "client_adapters.zoho_adapter",
    }
    return adapter_map.get(erp_type, "client_adapters.generic_adapter")
