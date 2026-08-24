"""Monetization — subscriptions via LemonSqueezy (merchant of record; pays out to Pakistan).

Webhook processing is idempotent (LemonSqueezy retries on non-2xx and can send duplicates), and
covers the full subscription lifecycle: created/updated/resumed upgrade the org to the purchased
plan; paused/cancelled/expired and refunds downgrade it to Free; a failed payment is logged but
does not immediately downgrade (LemonSqueezy handles dunning).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from urllib.parse import quote

from . import db

log = logging.getLogger("mcpwatch.billing")

STORE_URL = os.environ.get("LEMONSQUEEZY_STORE_URL", "")
WEBHOOK_SECRET = os.environ.get("LEMONSQUEEZY_WEBHOOK_SECRET", "")

VARIANT_TO_PLAN = {
    os.environ.get("LEMONSQUEEZY_VARIANT_PRO", "pro-variant"): "pro",
    os.environ.get("LEMONSQUEEZY_VARIANT_TEAM", "team-variant"): "team",
}
PLAN_TO_VARIANT = {v: k for k, v in VARIANT_TO_PLAN.items()}

_UPGRADE = {"subscription_created", "subscription_updated", "subscription_resumed", "order_created"}
_DOWNGRADE = {"subscription_cancelled", "subscription_expired", "subscription_paused",
              "order_refunded"}


def checkout_url(org: dict, user: dict, plan_key: str) -> str:
    variant = PLAN_TO_VARIANT.get(plan_key)
    if not STORE_URL or not variant:
        return f"{STORE_URL}/checkout" if STORE_URL else "#billing-not-configured"
    return (f"{STORE_URL}/buy/{variant}"
            f"?checkout[email]={quote(user['email'])}"
            f"&checkout[custom][org_id]={org['id']}"
            f"&checkout[custom][user_id]={user['id']}")


def verify_webhook(raw_body: bytes, signature: str) -> bool:
    """Constant-time HMAC-SHA256 check that a webhook came from LemonSqueezy."""
    if not WEBHOOK_SECRET or not signature:
        return False
    digest = hmac.new(WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature)


def _event_id(payload: dict) -> str:
    meta = payload.get("meta", {})
    data = payload.get("data", {}) or {}
    attrs = data.get("attributes", {}) or {}
    return f"{meta.get('event_name','')}:{data.get('id','')}:{attrs.get('updated_at','')}"


def apply_webhook_event(payload: dict) -> str | None:
    """Update an org's plan from a verified payload. Idempotent. Returns the new plan or None."""
    event_id = _event_id(payload)
    if db.webhook_seen(event_id, "lemonsqueezy"):
        log.info("duplicate webhook %s ignored", event_id)
        return None

    meta = payload.get("meta", {})
    event = meta.get("event_name", "")
    custom = meta.get("custom_data") or {}
    org_id = custom.get("org_id")
    attrs = (payload.get("data", {}) or {}).get("attributes", {}) or {}
    variant_id = str(attrs.get("variant_id", ""))
    customer_id = str(attrs.get("customer_id", "") or (payload.get("data", {}) or {}).get("id", ""))

    if not org_id or not db.get_org(org_id):
        log.warning("webhook %s: unknown or missing org_id", event)
        return None

    if event == "subscription_payment_failed":
        log.warning("payment failed for org %s (dunning; no downgrade)", org_id)
        return None

    if event in _UPGRADE:
        status = attrs.get("status", "active")
        if status in ("active", "on_trial", "paid", None):
            plan = VARIANT_TO_PLAN.get(variant_id)
            if plan:
                db.set_org_plan(org_id, plan)
                if customer_id:
                    db.set_org_billing(org_id, customer_id)
                db.audit("billing.upgrade", org_id=org_id, meta={"plan": plan, "event": event})
                return plan

    if event in _DOWNGRADE:
        db.set_org_plan(org_id, "free")
        db.audit("billing.downgrade", org_id=org_id, meta={"event": event})
        return "free"

    return None
