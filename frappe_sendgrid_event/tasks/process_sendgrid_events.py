"""Background processor for SendGrid webhook events.

Correlates pending Sendgrid Event records with Frappe's Email Queue
and extracts the subject line from the original message. Runs as a scheduled job every 5 minutes.

Design principles:
- Processes in batches of BATCH_SIZE to bound per-run execution time
- Two bulk DB updates per batch (processed / failed) — no per-event query overhead
- Per-event error isolation — one failure is recorded without blocking the batch
- All operations are idempotent; safe to re-run after an unexpected crash
"""

from __future__ import annotations

import email
from email.header import decode_header

import frappe
from frappe.utils import add_days, now_datetime

# Batch size per scheduler run
BATCH_SIZE = 500


def execute() -> None:
    """Entry point called by the scheduler every 5 minutes.

    Fetches pending events, correlates with Email Queue to extract the subject
    line, then writes processed and failed updates each in a single bulk query.
    """

    pending_events = frappe.db.get_all(
        "SendGrid Event",
        fields=[
            "name",
            "sg_event_id",
            "message_id",
        ],
        filters={"processing_status": "Pending"},
        order_by="creation asc",
        limit=BATCH_SIZE,
    )

    if not pending_events:
        return

    message_ids = {e.message_id for e in pending_events if e.message_id}
    queue_map = {}
    if message_ids:
        queues = frappe.get_all(
            "Email Queue",
            filters={"message_id": ("in", list(message_ids))},
            fields=["name", "message_id", "message"],
        )
        for q in queues:
            queue_map[q.message_id] = q

    processed_updates: dict = {}
    failed_updates: dict = {}

    for event in pending_events:
        result = _process_single_event(event, queue_map)
        if result.pop("has_error"):
            name = result.pop("name")
            failed_updates[name] = result
        else:
            name = result.pop("name")
            processed_updates[name] = result

    if processed_updates:
        frappe.db.bulk_update("SendGrid Event", processed_updates)
    if failed_updates:
        frappe.db.bulk_update("SendGrid Event", failed_updates)

    _cleanup_old_events()


def _process_single_event(event: dict, queue_map: dict) -> dict:
    """Resolve an event's Email Queue link and extract the email subject.

    Returns an update dict with a ``has_error`` flag so the caller can route the
    record into the correct bulk-update group without a per-event DB write.
    """
    try:
        email_queue_name: str | None = None
        email_subject: str | None = None

        message_id = event.get("message_id")
        if message_id and message_id in queue_map:
            eq_data = queue_map[message_id]
            email_queue_name = eq_data.name
            raw_message = eq_data.message
            if raw_message:
                msg = email.message_from_string(raw_message)
                raw_subject = msg.get("Subject") or ""

                decoded_fragments = []
                for fragment, charset in decode_header(raw_subject):
                    if isinstance(fragment, bytes):
                        charset = charset or "utf-8"
                        try:
                            fragment = fragment.decode(charset, errors="replace")
                        except LookupError:
                            fragment = fragment.decode("utf-8", errors="replace")
                    decoded_fragments.append(fragment)

                email_subject = "".join(decoded_fragments).strip()

        return {
            "name": event.get("name"),
            "processing_status": "Processed",
            "email_queue": email_queue_name,
            "email_subject": email_subject[:255] if email_subject else None,
            "has_error": False,
        }
    except Exception:
        return {
            "name": event.get("name"),
            "processing_status": "Failed",
            "error_log": frappe.get_traceback(),
            "has_error": True,
        }


def _cleanup_old_events() -> None:
    """Delete failed events older than each account's configured retention period."""
    accounts = frappe.get_all(
        "SendGrid Account",
        filters={"event_retention_days": (">", 0)},
        fields=["name", "event_retention_days"],
    )

    for account in accounts:
        cutoff_date = add_days(now_datetime(), -int(account.event_retention_days))
        frappe.db.delete(
            "SendGrid Event",
            filters={
                "sendgrid_account": account.name,
                "processing_status": "Failed",
                "creation": ("<", cutoff_date),
            },
        )
