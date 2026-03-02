"""
SAP Business One adapter.

Implements the standard client adapter interface for SAP B1 Service Layer.
Each function receives the client_config dict for connection details.
"""


def create_order(client_config, order_data):
    """Push a parsed order into SAP B1 as a Sales Order.

    Args:
        client_config: dict from client_config.json
        order_data: dict with customer, line_items, quantities

    Returns:
        dict with order_id, status, and any SAP response
    """
    # TODO: Implement SAP B1 Service Layer POST /Orders
    raise NotImplementedError("SAP B1 order creation not yet implemented")


def get_products(client_config):
    """Fetch product catalog from SAP B1.

    Note: The field name for item group code may vary.
    Check client_config["product_catalog"]["item_group_field"]
    (e.g. "ItmsGrpCod" vs "ItemGroupCode").
    """
    # TODO: Implement SAP B1 Service Layer GET /Items
    raise NotImplementedError("SAP B1 product fetch not yet implemented")


def get_customer(client_config, phone_number):
    """Look up customer in SAP B1 by phone number.

    Args:
        client_config: dict from client_config.json
        phone_number: WhatsApp phone number string

    Returns:
        dict with customer_id, name, default_address
    """
    # TODO: Implement SAP B1 Service Layer GET /BusinessPartners
    raise NotImplementedError("SAP B1 customer lookup not yet implemented")
