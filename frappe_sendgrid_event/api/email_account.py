import json

import frappe
from frappe.desk.search import sanitize_searchfield
from frappe.query_builder import DocType
from frappe.utils import cint
from pypika import Order


@frappe.whitelist()
def sendgrid_email_account_query(
    doctype,
    txt,
    searchfield,
    start,
    page_length,
    filters=None,
):
    """
    Custom Link-field query for SendGrid Email Accounts.

    Signature matches Frappe's search_link custom query convention:
        (doctype, txt, searchfield, start, page_length, filters)
    """

    sanitize_searchfield(searchfield)

    start = cint(start)
    page_length = cint(page_length)

    if isinstance(filters, str):
        try:
            filters = json.loads(filters)
        except Exception:
            filters = None

    EmailAccount = DocType("Email Account")

    query = (
        frappe.qb.from_(EmailAccount)
        .select(EmailAccount.name)
        .where(EmailAccount.enable_outgoing == 1)
        .where(
            (EmailAccount.service == "Sendgrid")
            | (EmailAccount.smtp_server.like("%sendgrid%"))
        )
    )

    if txt:
        query = query.where(
            EmailAccount[searchfield].like(f"%{txt}%")
        )

    rows = (
        query.orderby(EmailAccount.name, order=Order.asc)
        .limit(page_length)
        .offset(start)
        .run()
    )

    # Standard Link-field query return format
    return rows
