# Copyright (c) 2026, rtCamp and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class SendGridEvent(Document):
    # begin: auto-generated types
    # This code is auto-generated. Do not modify anything in this block.

    from typing import TYPE_CHECKING

    if TYPE_CHECKING:
        from frappe.types import DF

        attempt: DF.Int
        bounce_classification: DF.Data | None
        bounce_type: DF.Data | None
        email_queue: DF.Link | None
        email_subject: DF.Data | None
        error_log: DF.Code | None
        event_timestamp: DF.Datetime | None
        event_type: DF.Data
        message_id: DF.Data | None
        processing_status: DF.Literal["Pending", "Processed", "Failed"]
        raw_payload: DF.JSON | None
        reason: DF.Code | None
        recipient_email: DF.Data | None
        response: DF.Code | None
        sendgrid_account: DF.Link | None
        sg_event_id: DF.Data
        sg_message_id: DF.Data | None
        status_code: DF.Data | None
    # end: auto-generated types

    @staticmethod
    def default_list_data():
        columns = [
            {
                "label": "Name",
                "type": "Data",
                "key": "name",
                "width": "10rem",
            },
            {
                "label": "Recipient Email",
                "type": "Data",
                "key": "recipient_email",
                "width": "18rem",
            },
            {
                "label": "Event Type",
                "type": "Data",
                "key": "event_type",
                "width": "10rem",
            },
            {
                "label": "Event Timestamp",
                "type": "Datetime",
                "key": "event_timestamp",
                "width": "8rem",
            },
            {
                "label": "Subject",
                "type": "Data",
                "key": "subject",
                "width": "20rem",
            },
        ]

        rows = [
            "name",
            "recipient_email",
            "event_type",
            "event_timestamp",
            "subject",
        ]
        return {
            "columns": columns,
            "rows": rows,
        }
