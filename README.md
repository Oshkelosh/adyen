# Adyen (`adyen`)

Accept payments via Adyen Checkout.

## Overview

| | |
|---|---|
| Addon ID | `adyen` |
| Category | payment |
| Version | 1.0.0 |
| Category guide | [../README.md](../README.md) |

Only **one** payment addon can be active at a time.

## Enable and configure

1. Install this package under `app/addons/payments/adyen/`
2. Open **Admin → Payments → Adyen** at `/admin/payments/adyen`
3. Enter credentials and enable **Enable this payment processor**

## Configuration schema

| Field | Type | Description |
|-------|------|-------------|
| `api_key` | secret | Adyen API key |
| `merchant_account` | string | Adyen merchant account |
| `client_key` | string | Client key for Drop-in / Components (optional) |
| `webhook_hmac_key` | secret | HMAC key for webhook verification |
| `environment` | string | `test` or `live` |
| `return_url` | string | Redirect after payment |
| `cancel_url` | string | Redirect when cancelled |

Secrets are stored in `addon_configs`, not in `.env`.

## Routes

### Public API

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/payments/adyen/checkout` | Start checkout (optional; prefer generic order checkout) |
| POST | `/api/v1/payments/adyen/webhook` | PSP webhook endpoint |

### Admin

| Method | Path | Description |
|--------|------|-------------|
| GET | `/admin/payments/adyen` | Config form |
| POST | `/admin/payments/adyen/save` | Save config |

## Core integration

- **Storefront checkout:** `POST /api/v1/orders/{order_id}/checkout` → `PaymentAddon.create_payment()` → redirect URL
- **Webhook:** `POST /api/v1/payments/adyen/webhook` → `parse_webhook()` → core `process_payment_webhook()`
- **Amounts:** smallest currency unit (cents)

## Provider setup

Register webhook URL (replace `{PUBLIC_APP_URL}` with your public base URL):

```
{PUBLIC_APP_URL}/api/v1/payments/adyen/webhook
```

Webhook signature header: **`hmacsignature`**

1. Configure Adyen Checkout in your Customer Area.
2. Add a Standard webhook with your Oshkelosh webhook URL.
3. Copy the HMAC key into admin config.

## Package layout

```
adyen/
├── README.md
├── addon.py
├── routes.py
└── templates/
```

## See also

- [Payment addon development](../README.md)
- [Oshkelosh addon guide](../../README.md)
