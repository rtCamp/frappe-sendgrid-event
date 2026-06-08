"""SendGrid Event Webhook receiver.

Handles delivery event callbacks from SendGrid, verifies ECDSA signatures,
and bulk-inserts events for async processing. Designed for high throughput:
- Returns HTTP 200 immediately after insert (no Email Queue lookups in hot path)
- Uses INSERT IGNORE for idempotent deduplication on sg_event_id
- ECDSA signature verification prevents unauthorised access
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

import frappe
from frappe import _

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


@frappe.whitelist(allow_guest=True, methods=["POST"])
def handle():
	"""Receive and store SendGrid Event Webhook delivery events.

	This endpoint is called by SendGrid with batches of event data.
	It verifies the ECDSA signature, filters to delivery events, and
	bulk-inserts them for background processing. Returns 200 immediately.
	"""
	settings_doc = frappe.get_cached_doc("SendGrid Event Settings")
	if not settings_doc.enable_sendgrid_webhook:
		frappe.throw(_("SendGrid webhook is not enabled"), frappe.AuthenticationError)

	# Raw payload must be read before any transformation for correct signature verification
	raw_payload: str = frappe.request.get_data(as_text=True)
	if not raw_payload:
		frappe.throw(_("Empty request body"), frappe.ValidationError)

	# Verify ECDSA signature when a verification key is configured
	verification_key = ""
	if settings_doc.webhook_verification_key:
		verification_key = (settings_doc.get_password("webhook_verification_key") or "").strip()
	if verification_key:
		signature = frappe.request.headers.get("X-Twilio-Email-Event-Webhook-Signature", "")
		timestamp = frappe.request.headers.get("X-Twilio-Email-Event-Webhook-Timestamp", "")
		if not signature or not timestamp:
			frappe.throw(_("Missing webhook signature headers"), frappe.AuthenticationError)
		if not _verify_signature(raw_payload, signature, timestamp, verification_key):
			frappe.throw(_("Invalid webhook signature"), frappe.AuthenticationError)
	else:
		frappe.logger("sendgrid_webhook").warning(
			"SendGrid webhook received without signature verification. "
			"Set webhook_verification_key in SendGrid Event Settings."
		)

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

	# Filter to delivery events; drop any non-dict items defensively
	delivery_events = [e for e in events if isinstance(e, dict) and e.get("event") in DELIVERY_EVENTS]

	target_email_accounts = [
		row.email_account for row in settings_doc.get("email_accounts", []) if row.email_account
	]

	if delivery_events and target_email_accounts:
		_bulk_insert_events(delivery_events, target_email_accounts)

	# Return 200 immediately — processing happens in background
	return {"status": "ok", "accepted": len(delivery_events), "total": len(events)}


def _verify_signature(payload: str, signature: str, timestamp: str, public_key_b64: str) -> bool:
	"""Verify the ECDSA signature from SendGrid's Signed Event Webhook.

	Algorithm (identical to the official sendgrid-python SDK)::

	    hash  = SHA256(timestamp_bytes || payload_bytes)
	    valid = ECDSA_verify(public_key, hash, base64decode(signature))

	Returns True when valid, False for every error case — never raises.
	"""
	try:
		from cryptography.exceptions import InvalidSignature
		from cryptography.hazmat.primitives import hashes
		from cryptography.hazmat.primitives.asymmetric import ec
		from cryptography.hazmat.primitives.serialization import load_pem_public_key
	except ImportError:
		frappe.logger("sendgrid_webhook").error(
			"cryptography library unavailable; cannot verify SendGrid signature"
		)
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
		# Broad catch is intentional: fail safe on malformed key/signature
		frappe.logger("sendgrid_webhook").error("SendGrid signature verification error", exc_info=True)
		return False


def _bulk_insert_events(events: list[dict], target_email_accounts: list[str]) -> None:
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
			frappe.get_all(
				"SendGrid Event", filters={"sg_event_id": ("in", list(sg_event_ids))}, pluck="sg_event_id"
			)
		)

	# Pre-fetch email queues to avoid N+1 queries
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
				frappe.generate_hash(length=10),  # name
				sg_event_id,  # sg_event_id
				event.get("sg_message_id") or "",  # sg_message_id
				message_id,  # message_id
				email_queue_name,  # email_queue
				event.get("event") or "",  # event_type
				event.get("email") or "",  # recipient_email
				_unix_to_datetime(event.get("timestamp")),  # event_timestamp
				"Pending",  # processing_status
				event.get("status") or "",  # status_code
				(event.get("reason") or "")[:65535],  # reason
				(event.get("response") or "")[:65535],  # response
				event.get("bounce_classification") or "",  # bounce_classification
				event.get("type") or "",  # bounce_type
				int(event.get("attempt") or 0),  # attempt
				json.dumps(event, default=str),  # raw_payload
				now,  # creation
				now,  # modified
				user,  # owner
				user,  # modified_by
			]
		)

	if not rows:
		return

	frappe.db.bulk_insert(
		"SendGrid Event",
		fields=_INSERT_FIELDS,
		values=rows,
		ignore_duplicates=True,
		chunk_size=100,
	)


def _unix_to_datetime(timestamp) -> str | None:
	"""Convert a Unix epoch value to a system datetime string for MariaDB storage."""
	if not timestamp:
		return None
	try:
		dt_utc = datetime.fromtimestamp(int(timestamp), tz=UTC)
		return frappe.utils.convert_utc_to_system_timezone(dt_utc).strftime("%Y-%m-%d %H:%M:%S")
	except ValueError, TypeError, OSError:
		return None


#  Convert to system time zome
#  Cild table with apt filters on service, smtp and outgoing
#  event kitne time main aur kitne fire hote from sendgrid for sync / async
#  Name change for events to random hash
#  No change in email queue
