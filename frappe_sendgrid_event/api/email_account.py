import frappe
from frappe.query_builder import DocType
from pypika import Order


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def sendgrid_email_account_query(
	doctype,
	txt,
	searchfield,
	start,
	page_len,
	filters,
):
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
