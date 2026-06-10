from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

import frappe
from frappe import _
from frappe.model.naming import make_autoname
from frappe.utils import convert_utc_to_system_timezone
from frappe.utils.password import decrypt
from pypika import Table

# Delivery event types we track; engagement/account events are intentionally excluded
DELIVERY_EVENTS = frozenset({"processed", "delivered", "bounce", "deferred", "dropped"})

# Guard against pathological payloads; SendGrid batches are well below this
_MAX_EVENTS_PER_BATCH = 5_000

_INSERT_FIELDS = [
    "name",
    "sg_event_id",
    "sg_message_id",
    "message_id",
    "email_queue",
    "sendgrid_account",
    "event_type",
    "recipient_email",
    "event_timestamp",
    "processing_status",
    "status_code",
    "reason",
    "response",
    "bounce_classification",
    "bounce_type",
    "attempt",
    "raw_payload",
    "creation",
    "modified",
    "owner",
    "modified_by",
]


@frappe.whitelist(allow_guest=True, methods=["POST"])  # nosemgrep  Trusted endpoint, signature verified in code
def handle():
    """
    Receive and store SendGrid Event Webhook delivery events.

    This endpoint is called by SendGrid with batches of event data.
    It verifies the ECDSA signature, filters to delivery events, and
    bulk-inserts them for background processing.

    Returns:
        {
            "status": "ok",
            "accepted": <number of delivery events accepted>,
            "total": <total number of events in payload>
        }
    """
    raw_payload: str = frappe.request.get_data(as_text=True)
    if not raw_payload:
        frappe.throw(_("Empty request body"), frappe.ValidationError)

    sendgrid_account = get_associated_sendgrid_account()

    if not sendgrid_account:
        return {"status": "ok", "accepted": 0, "total": 0}

    # Parse and validate payload structure
    try:
        events = json.loads(raw_payload)
    except json.JSONDecodeError, TypeError:
        frappe.throw(_("Invalid JSON payload"), frappe.ValidationError)

    if not isinstance(events, list):
        frappe.throw(_("Payload must be a JSON array"), frappe.ValidationError)

    if len(events) > _MAX_EVENTS_PER_BATCH:
        frappe.throw(
            _("Payload exceeds maximum of {0} events").format(_MAX_EVENTS_PER_BATCH),
            frappe.ValidationError,
        )

    delivery_events = [e for e in events if isinstance(e, dict) and e.get("event") in DELIVERY_EVENTS]

    settings_doc = frappe.get_cached_doc("SendGrid Account", sendgrid_account)

    target_email_accounts = [row.email_account for row in settings_doc.get("email_accounts", []) if row.email_account]

    if delivery_events and target_email_accounts:
        _bulk_insert_events(delivery_events, target_email_accounts, sendgrid_account)

    return {"status": "ok", "accepted": len(delivery_events), "total": len(events)}


def _verify_signature(payload: str, signature: str, timestamp: str, account_name: str, encrypted_key: str) -> bool:
    """Verify the ECDSA signature from SendGrid's Signed Event Webhook.

    Algorithm:

        hash  = SHA256(timestamp_bytes || payload_bytes)
        valid = ECDSA_verify(public_key, hash, base64decode(signature))

    Returns True when valid, False for every error case — never raises.
    """
    public_key_b64 = decrypt(encrypted_key, key=f"SendGrid Account.{account_name}.webhook_verification_key")
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
    except ImportError:
        frappe.logger("sendgrid_webhook").error("cryptography library unavailable; cannot verify SendGrid signature")
        return False

    try:
        pem_key = "-----BEGIN PUBLIC KEY-----\n" + public_key_b64 + "\n-----END PUBLIC KEY-----"
        public_key = load_pem_public_key(pem_key.encode("utf-8"))
        decoded_signature = base64.b64decode(signature)
        timestamped_payload = (timestamp + payload).encode("utf-8")
        public_key.verify(decoded_signature, timestamped_payload, ec.ECDSA(hashes.SHA256()))
        return True
    except InvalidSignature:
        return False
    except Exception:
        frappe.logger("sendgrid_webhook").error("SendGrid signature verification error", exc_info=True)
        return False


def _bulk_insert_events(events: list[dict], target_email_accounts: list[str], sendgrid_account: str) -> None:
    """Bulk-insert delivery events, safely handling idempotency.

    Pre-fetches existing sg_event_ids to eliminate duplicates before generating
    random hash PKs, preventing batch insert failures on unique key constraints.
    frappe.db.bulk_insert handles parameterised, chunked insertion internally.
    """
    now = frappe.utils.now()
    user = frappe.session.user or "Administrator"

    # Pre-fetch existing sg_event_ids to handle idempotency before insertion
    sg_event_ids = {e.get("sg_event_id", "").strip() for e in events if e.get("sg_event_id")}
    existing_event_ids = set()
    if sg_event_ids:
        existing_event_ids = set(
            frappe.get_all("SendGrid Event", filters={"sg_event_id": ("in", list(sg_event_ids))}, pluck="sg_event_id")
        )

    message_ids = {e.get("smtp-id", "").strip("<>") for e in events if e.get("smtp-id")}
    queue_map = {}
    if message_ids:
        queues = frappe.get_all(
            "Email Queue",
            filters={"message_id": ("in", list(message_ids)), "email_account": ("in", target_email_accounts)},
            fields=["name", "message_id"],
        )
        queue_map = {q.message_id: q.name for q in queues}

    rows: list[list] = []
    for event in events:
        sg_event_id = (event.get("sg_event_id") or "").strip()
        if not sg_event_id or sg_event_id in existing_event_ids:
            continue

        message_id = (event.get("smtp-id") or "").strip("<>")
        email_queue_name = queue_map.get(message_id)

        if not email_queue_name:
            continue

        rows.append(
            [
                make_autoname("hash", doctype="SendGrid Event"),
                sg_event_id,
                event.get("sg_message_id") or "",
                message_id,
                email_queue_name,
                sendgrid_account,
                event.get("event") or "",
                event.get("email") or "",
                _unix_to_datetime(event.get("timestamp")),
                "Pending",
                event.get("status") or "",
                (event.get("reason") or ""),
                (event.get("response") or ""),
                event.get("bounce_classification") or "",
                event.get("type") or "",
                int(event.get("attempt") or 0),
                json.dumps(event, default=str),
                now,
                now,
                user,
                user,
            ]
        )

    if not rows:
        return

    frappe.db.bulk_insert("SendGrid Event", fields=_INSERT_FIELDS, values=rows, ignore_duplicates=True)


def _unix_to_datetime(timestamp) -> str | None:
    """Convert a Unix epoch value to a system datetime string for MariaDB storage."""
    if not timestamp:
        return None
    try:
        dt_utc = datetime.fromtimestamp(int(timestamp), tz=UTC)
        return convert_utc_to_system_timezone(dt_utc)
    except ValueError, TypeError, OSError:
        return None


def get_associated_sendgrid_account() -> str | None:
    """
    Perform a join query on __Auth table with "SendGrid Account" to link
    the name of the SendGrid account to the verification key.
    Then verify if any of the accounts verification key can verify the current signature.
    If signature is valid return the name of the account, otherwise return None.
    """
    Auth = Table("__Auth")
    SendGridAccount = frappe.qb.DocType("SendGrid Account")
    account = (
        frappe.qb.from_("SendGrid Account")
        .join(Auth)
        .on(
            (Auth.doctype == "SendGrid Account")
            & (Auth.name == SendGridAccount.name)
            & (Auth.fieldname == "webhook_verification_key")
        )
        .select(SendGridAccount.name, Auth.password)
        .where(SendGridAccount.webhook_verification_key.isnotnull())
        .where(SendGridAccount.enable_sendgrid_webhook == 1)
    ).run(as_dict=True)

    for row in account:
        if row.name and _verify_signature(
            payload=frappe.request.get_data(as_text=True),
            signature=frappe.request.headers.get("X-Twilio-Email-Event-Webhook-Signature", ""),
            timestamp=frappe.request.headers.get("X-Twilio-Email-Event-Webhook-Timestamp", ""),
            account_name=row.name,
            encrypted_key=row.password,
        ):
            return row.name

    return None
