// Copyright (c) 2026, rtCamp and contributors
// For license information, please see license.txt

frappe.ui.form.on("SendGrid Event Settings", {
	setup(frm) {
		frm.set_query(
			"email_account",
			"email_accounts",
			function() {
                return {
                    query: "frappe_sendgrid_event.api.email_account.sendgrid_email_account_query"
                };
            }
		);
	}
});
