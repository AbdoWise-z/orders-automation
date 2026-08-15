EXTRACTION_SYSTEM_PROMPT = """\
You extract structured order data from a document that was already run \
through an OCR/document-parsing model. You are given both its Markdown \
output and spatial OCR JSON containing information such as text blocks, \
coordinates, reading order, and grouping.

Use the Markdown as the primary representation. Use the spatial OCR JSON \
when the Markdown loses the original document layout or creates ambiguity.

The Markdown includes at least one table of line items. Read it row by \
row -- each row's SKU, description, quantity, unit price, discount, VAT \
percentage and line total belong together as a single item. Do not mix \
values across rows. Use the spatial information to resolve table columns \
or rows when necessary.

For billing and delivery addresses, use the spatial information to \
determine which text belongs to each address, especially when the \
addresses are displayed in separate columns.

If a field is not present in the document, or you cannot read it with \
confidence, use null. Never invent or guess a plausible-looking value.

Return the extracted order data by calling the record_order tool. Do not \
return the order data as free-form text.
"""

TOOL_NAME = "record_order"
TOOL_DESCRIPTION = (
    "Record structured order data extracted from a parsed source "
    "document (order/purchase order). Use null for anything not "
    "present or not legible in the document -- never invent a value."
)

# Plain JSON Schema, shared by every provider. Anthropic wraps this as
# {name, description, input_schema}; OpenAI-compatible APIs (Groq,
# Ollama) wrap it as {type: "function", function: {name, description,
# parameters}}. See extraction.py.
ORDER_JSON_SCHEMA = {
        "type": "object",
        "properties": {
            "order": {
                "type": "object",
                "description": "Order-level header fields.",
                "properties": {
                    "order_date": {
                        "type": ["string", "null"],
                        "description": "ISO date, YYYY-MM-DD, if determinable.",
                    },
                    "external_reference": {"type": ["string", "null"]},
                    "customer_id": {"type": ["string", "null"]},
                    "currency": {
                        "type": ["string", "null"],
                        "description": "ISO currency code, e.g. EUR, USD.",
                    },
                },
                "required": ["order_date", "external_reference", "customer_id", "currency"],
            },
            "debtor": {
                "type": "object",
                "description": "Customer / debtor and payment details.",
                "properties": {
                    "company": {"type": ["string", "null"]},
                    "contact_name": {"type": ["string", "null"]},
                    "alias": {"type": ["string", "null"]},
                    "email": {"type": ["string", "null"]},
                    "phone": {"type": ["string", "null"]},
                    "billing_address": {
                        "type": "object",
                        "properties": {
                            "street": {"type": ["string", "null"]},
                            "zip": {"type": ["string", "null"]},
                            "city": {"type": ["string", "null"]},
                            "country": {"type": ["string", "null"]},
                        },
                        "required": ["street", "zip", "city", "country"],
                    },
                    "delivery_address": {
                        "type": "object",
                        "properties": {
                            "street": {"type": ["string", "null"]},
                            "zip": {"type": ["string", "null"]},
                            "city": {"type": ["string", "null"]},
                            "country": {"type": ["string", "null"]},
                        },
                        "required": ["street", "zip", "city", "country"],
                    },
                    "payment_method": {
                        "type": ["string", "null"],
                        "description": "e.g. Bank Transfer, Credit Card, SEPA Direct Debit.",
                    },
                    "paid_status": {
                        "type": ["string", "null"],
                        "description": "PAID or UNPAID if shown, else null.",
                    },
                    "payment_date": {"type": ["string", "null"], "description": "ISO date if shown."},
                },
                "required": [
                    "company", "contact_name", "alias", "email", "phone",
                    "billing_address", "delivery_address",
                    "payment_method", "paid_status", "payment_date",
                ],
            },
            "items": {
                "type": "array",
                "description": "One entry per line item row, in document order.",
                "items": {
                    "type": "object",
                    "properties": {
                        "sku": {"type": ["string", "null"]},
                        "description": {"type": ["string", "null"]},
                        "quantity": {"type": ["number", "null"]},
                        "unit": {"type": ["string", "null"], "description": "e.g. pcs, kg."},
                        "unit_net_price": {"type": ["number", "null"]},
                        "discount_pct": {"type": ["number", "null"]},
                        "vat_pct": {"type": ["number", "null"]},
                        "line_net_total": {"type": ["number", "null"]},
                    },
                    "required": [
                        "sku", "description", "quantity", "unit",
                        "unit_net_price", "discount_pct", "vat_pct", "line_net_total",
                    ],
                },
            },
        },
        "required": ["order", "debtor", "items"],
}

# Anthropic tool-use format.
ANTHROPIC_TOOL = {
    "name": TOOL_NAME,
    "description": TOOL_DESCRIPTION,
    "input_schema": ORDER_JSON_SCHEMA,
}

# OpenAI-compatible function-calling format (used for Groq and Ollama).
OPENAI_TOOL = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "parameters": ORDER_JSON_SCHEMA,
    },
}
