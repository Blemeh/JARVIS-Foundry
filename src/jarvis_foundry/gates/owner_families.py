"""V7-S11 — owner capability families: communications, calendar,
accounts, services, purchases, finance.

Extends the S10 gate families with the owner-facing families. The
families are declarative (read-only classes pass, effectful classes deny
by default); the actual provider integrations (email/calendar/account/
payment providers) require owner credentials and land with the executor
wiring in a later slice — the gates are the executable policy front.
"""
from __future__ import annotations

from jarvis_foundry.gates import minigates

OWNER_FAMILIES: dict[str, dict[str, list[str]]] = {
    "communications": {
        "read_only_classes": ["list_channels", "list_messages",
                              "list_contacts"],
        "effectful_classes": ["send_message", "send_email",
                              "create_contact", "delete_contact"],
    },
    "calendar": {
        "read_only_classes": ["list_events", "list_calendars"],
        "effectful_classes": ["create_event", "update_event",
                              "delete_event", "accept_invite",
                              "decline_invite"],
    },
    "accounts": {
        "read_only_classes": ["list_accounts", "account_status"],
        "effectful_classes": ["create_account", "close_account",
                              "update_credentials"],
    },
    "services": {
        "read_only_classes": ["list_services", "service_status"],
        "effectful_classes": ["subscribe_service",
                              "unsubscribe_service",
                              "modify_subscription"],
    },
    "purchases": {
        "read_only_classes": ["list_orders", "order_status"],
        "effectful_classes": ["place_order", "cancel_order",
                              "request_refund"],
    },
    "finance": {
        "read_only_classes": ["balance", "list_transactions",
                              "list_budgets"],
        "effectful_classes": ["transfer", "pay_invoice",
                              "create_budget", "modify_budget"],
    },
}

# register the owner families into the shared gate table (import-time,
# idempotent): the full 12-family union is what gate_operation sees
minigates.FAMILIES.update(OWNER_FAMILIES)
