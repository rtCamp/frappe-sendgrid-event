# Copyright (c) 2026, rtCamp and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class SendGridAccount(Document):
    # begin: auto-generated types
    # This code is auto-generated. Do not modify anything in this block.

    from typing import TYPE_CHECKING

    if TYPE_CHECKING:
        from frappe.types import DF

        from frappe_sendgrid_event.frappe_sendgrid_event.doctype.sendgrid_email_account.sendgrid_email_account import (
            SendGridEmailAccount,
        )

        email_accounts: DF.Table[SendGridEmailAccount]
        enable_sendgrid_webhook: DF.Check
        event_retention_days: DF.Int
        webhook_verification_key: DF.Password | None
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
                "label": "Enable SendGrid Webhook",
                "type": "Check",
                "key": "enable_sendgrid_webhook",
                "width": "20rem",
            },
        ]

        rows = [
            "name",
            "enable_sendgrid_webhook",
        ]
        return {
            "columns": columns,
            "rows": rows,
        }
