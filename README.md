# Frappe SendGrid Event

Receive and track SendGrid email delivery events in Frappe via webhooks.

This app integrates directly with SendGrid's Event Webhook API to capture real-time email metrics (`processed`, `delivered`, `bounce`, `dropped`, `deferred`) and seamlessly links them to your Frappe `Email Queue`.

## Features & Architecture

- **High Throughput & Async Cron Processing:** The webhook endpoint is designed for massive scale. Instead of doing expensive database lookups on the fly, it verifies the ECDSA signature, deduplicates seamlessly via `INSERT IGNORE`, and bulk-inserts events as `Pending` into the `SendGrid Event` doctype. A scheduled background job (cron) asynchronously processes these pending events later, ensuring the webhook never times out.
- **Email Queue Linking:** The app parses SendGrid's `smtp-id` and correctly maps it to Frappe's `message_id`, linking the external delivery event directly back to the original `Email Queue` record.
- **Account Specific Filtering:** Built to handle environments where multiple SendGrid accounts might route to the same instance. It checks the `email_account` tied to the outgoing `Email Queue` and only logs events if they match the designated email account specified in the settings.
- **Secure by Default:** Enforces ECDSA signature verification to prevent spoofed or unauthorized data from being injected into your database.

## Setup & Configuration

1. In your Frappe site, navigate to **SendGrid Account**.
2. Create a new doc and check **Enable Sendgrid Webhook**.
3. Enter your **Webhook Verification Key** (provided by SendGrid) to secure the endpoint.
4. Select the specific **Email Account** you want to track events for. (Events tied to other email accounts will be safely ignored).
5. Log in to your SendGrid Dashboard and navigate to **Settings** > **Mail Settings** > **Event Webhook**.
6. Under **HTTP POST URL**, enter your Frappe site's webhook endpoint exactly as follows:
   `https://<your-site-domain>/api/method/frappe_sendgrid_event.api.sendgrid_webhook.handle`
7. Select the delivery events you want to track. *(Note: This app is configured to track `processed`, `delivered`, `bounce`, `deferred`, and `dropped`)*.
8. Enable the integration and save your settings.

## Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch main
bench install-app frappe_sendgrid_event
```

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/frappe_sendgrid_event
pre-commit install
```


### License

GNU AFFERO GENERAL PUBLIC LICENSE (v3)
