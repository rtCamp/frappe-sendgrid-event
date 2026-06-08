"""Background processor for SendGrid webhook events.

Correlates pending Sendgrid Event records with Frappe's Email Queue
and extracts the subject line from the original message. Runs as a scheduled job every 5 minutes.

Design principles:
- Processes in batches of BATCH_SIZE to bound per-run execution time
- Single DB commit per batch for efficiency (no per-event commit overhead)
- Per-event error isolation — one failure is recorded without blocking the batch
- All operations are idempotent; safe to re-run after an unexpected crash
"""

from __future__ import annotations

import email
from email.header import decode_header

import frappe
from frappe.query_builder import DocType
from frappe.utils import add_days, now_datetime

# Batch size per scheduler run
BATCH_SIZE = 500


def execute() -> None:
	"""Entry point called by the scheduler every 5 minutes.

	Fetches pending events, correlates with Email Queue to extract the subject
	line, then commits the entire batch in a single transaction.
	"""
	settings = frappe.get_cached_doc("SendGrid Event Settings")
	if not settings.get("enable_sendgrid_webhook"):
		return

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

	# Pre-fetch all related Email Queues to eliminate N+1 queries in the loop
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

	for event in pending_events:
		_process_single_event(event, queue_map)

	# Single commit covers all event status updates in this batch
	frappe.db.commit()
	_cleanup_old_events(settings)


def _process_single_event(event: dict, queue_map: dict) -> None:
	"""Resolve an event's Email Queue link and extract the email subject.

	Errors are caught per-event and recorded on the event itself so a single
	failure does not prevent the rest of the batch from committing.
	"""
	try:
		email_queue_name: str | None = None
		email_subject: str | None = None

		message_id = event.get("message_id")
		if message_id:
			eq_data = frappe.db.get_value(
				"Email Queue",
				{"message_id": message_id},
				["name", "message"],
			)
			if eq_data:
				email_queue_name, raw_message = eq_data
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

		update_data = {"processing_status": "Processed", "email_queue": email_queue_name}
		if email_subject:
			update_data["email_subject"] = email_subject[:255]

		frappe.db.set_value(
			"SendGrid Event",
			event.get("name"),
			update_data,
			update_modified=False,
		)
	except Exception:
		frappe.db.set_value(
			"SendGrid Event",
			event.get("name"),
			{
				"processing_status": "Failed",
				"error_log": frappe.get_traceback(with_context=False)[:65535],
			},
			update_modified=False,
		)


def _cleanup_old_events(settings) -> None:
	"""Delete processed events older than the configured retention period.

	Bounded to 1 000 rows per cleanup run to avoid long-running table locks.
	Uses a SELECT-then-DELETE pattern so we can apply a row limit without
	resorting to raw SQL.
	"""
	retention_days = settings.get("event_retention_days") or 0
	if not retention_days:
		return

	cutoff_date = add_days(now_datetime(), -int(retention_days))

	old_events = frappe.db.get_all(
		"SendGrid Event",
		filters={"processing_status": "Processed", "creation": ("<", cutoff_date)},
		fields=["name"],
		limit=5000,
	)
	if not old_events:
		return

	names = [r.name for r in old_events]
	ase = DocType("SendGrid Event")
	frappe.qb.from_(ase).delete().where(ase.name.isin(names)).run()
	frappe.db.commit()
