import frappe
from frappe.desk.search import sanitize_searchfield
from frappe.query_builder import DocType
from frappe.utils import cint
from pypika import Order


@frappe.whitelist()
def sendgrid_email_account_query(txt: str, searchfield: str, start: int, page_len: int):
    sanitize_searchfield(searchfield)
    start = cint(start)
    page_len = cint(page_len)
    EmailAccount = DocType("Email Account")

    return (
        frappe.qb.from_(EmailAccount)
        .select(EmailAccount.name)
        .where(EmailAccount.enable_outgoing == 1)
        .where((EmailAccount.service == "SendGrid") | (EmailAccount.smtp_server.like("%sendgrid%")))
        .where(EmailAccount[searchfield].like(f"%{txt}%"))
        .orderby(EmailAccount.name, order=Order.asc)
        .limit(page_len)
        .offset(start)
    ).run()
