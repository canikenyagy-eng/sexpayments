# PrimePay Merchant API

URL: `/api/merchant/v1`

## Authentication

```
X-Api-Key: <api_key>
```

`api_secret` shown once at terminal creation — store it. Used only to sign requests/verify webhooks, never sent in headers.

Optional request signing — `X-Signature: <hmac-sha256-hex>`. Same algorithm as webhooks (see below). Mismatch → `401`.

---

## Common Objects

### Order Object

```json
{
  "id": "uuid",
  "internalId": "your-internal-order-id",
  "userId": "your-client-id",
  "merchant_name": "My Merchant",
  "amount": 5000.00,
  "amount_usdt": 56.12,
  "fee_usdt": 0.56,
  "exchange_rate": 89.1000,
  "currency": "RUB",
  "status": "pending",
  "payment_url": "https://...",
  "created_at": "2026-01-01T10:00:00",
  "expires_at": "2026-01-01T10:30:00",
  "requisite": {
    "bank_name": "Сбер",
    "account_number": "4111222233334444",
    "account_holder": "Ivan I.",
    "payment_method": "card",
    "currency": "RUB",
    "payment_option_code": "sber",
    "payment_option_name": "Сбер"
  }
}
```

`requisite` is `null` if no requisite has been assigned yet (async mode or order just created).

### Order Statuses


| Status             | Description                                  |
| ------------------ | -------------------------------------------- |
| `created`          | Order created, awaiting requisite assignment |
| `pending`          | Requisite assigned, awaiting payment         |
| `receipt_uploaded` | Client uploaded payment receipt              |
| `success`          | Payment confirmed, order complete            |
| `disputed`         | Dispute opened                               |
| `canceled`         | Canceled by merchant or system               |
| `failed`           | Payment not received within TTL              |
| `refunded`         | Funds returned                               |


---

## Orders

### POST /orders/payin

Create a payin order.

**Request body:**

```json
{
  "amount": 5000.00,
  "currency": "RUB",
  "payment_method": "card",
  "internalId": "order-123",
  "userId": "client-456",
  "notificationUrl": "https://yoursite.com/webhook",
  "payment_option": 3,
  "issue_requisite_async": false
}
```


| Field                   | Type   | Required | Description                                                                                                                                                                                                                                          |
| ----------------------- | ------ | -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `amount`                | float  | yes      | Amount in fiat (must be > 0)                                                                                                                                                                                                                         |
| `currency`              | string | yes      | `RUB`, `AZN`                                                                                                                                                                                                                                         |
| `payment_method`        | string | yes      | `card`, `sbp`, `sim`                                                                                                                                                                                                                                 |
| `internalId`            | string | no       | Your internal order ID. Used for idempotency — if an order with this `internalId` already exists, returns an error                                                                                                                                   |
| `userId`                | string | no       | Your client ID                                                                                                                                                                                                                                       |
| `notificationUrl`       | string | no       | Webhook URL for this order (overrides merchant-level webhook URL)                                                                                                                                                                                    |
| `payment_option`        | int    | no       | Specific bank/provider ID. If omitted, the system selects automatically                                                                                                                                                                              |
| `issue_requisite_async` | bool   | no       | Default: `false`. If `false`, the request waits until a requisite is assigned (up to `requisite_search_timeout_ms`) and returns it in the response. If `true`, returns immediately with `created` status and requisites will be provided via webhook |


**Response:** `201 Created` — Order Object

---

### GET /orders/{id}

Get order info by UUID.

**Response:** `200 OK` — Order Object

---

### GET /orders/external/{external_id}

Get order info by your internal ID (`internalId`).

**Response:** `200 OK` — Order Object

---

### POST /orders/{id}/confirm-transfer

Upload a payment receipt. Use `multipart/form-data`.


| Field        | Type | Description                     |
| ------------ | ---- | ------------------------------- |
| `attachment` | file | Receipt image or PDF, max 10 MB |


**Response:** `200 OK` — Order Object

---

### POST /orders/external/{external_id}/confirm-transfer

Same as above, but addressed by `internalId`.

**Response:** `200 OK` — Order Object

---

### POST /orders/{id}/cancel

Cancel an order. Only possible before the order reaches a terminal status (`success`, `failed`, `refunded`).

**Response:** `200 OK` — Order Object

---

### POST /orders/external/{external_id}/cancel

Same as above, but addressed by `internalId`.

**Response:** `200 OK` — Order Object

---

## Profile

### GET /profile/me

Get merchant profile: status, currency, current balance, active payment methods with fee rates.

**Response:** `200 OK`

```json
{
  "id": 1,
  "status": "enabled",
  "currency": "RUB",
  "webhook_url": "https://yoursite.com/webhook",
  "balance": 1234.56,
  "payment_methods": [
    {
      "method": "card",
      "fee_percentage": 2.5,
      "options": [
        {
          "id": 3,
          "name": "Sberbank",
          "currency": "RUB",
          "supported_methods": ["card"]
        }
      ]
    }
  ]
}
```

---

## Payments

### GET /payments/methods

Returns a list of supported payment method codes.

**Response:** `200 OK`

```json
["card", "sbp", "sim"]
```

---

### GET /payments/options

Returns all active banks/providers available for order creation.

**Response:** `200 OK`

```json
[
  {
    "id": 3,
    "name": "Sberbank",
    "currency": "RUB",
    "supported_methods": ["card"]
  }
]
```

---

## Rates

### GET /rates

Returns active exchange rates used for USDT conversion.

**Response:** `200 OK`

```json
[
  {
    "id": 1,
    "currency": "RUB",
    "rate": 89.10,
    "provider": "bybit",
    "is_active": true
  }
]
```

---

## Disputes

A dispute is a merchant claim against the trader on a single order. All endpoints are under `/disputes`. One open dispute per order.

A dispute can also be raised **for** you: platform may ask you to supply additional proof (PDF / video). Such a dispute carries `reason: check_suspended` and a `substatus` (`pdf_requested` / `video_requested`)

**Lifecycle:** `open` → `resolved` | `rejected`.

Opening a dispute emits a `disputed` webhook; closing it emits a `success` (resolved) or `failed` (rejected) webhook.

### Dispute Object

```json
{
  "uuid": "a3f1c2e4-5b6d-7e8f-9012-3456789abcde",
  "reason": "no_payment",
  "status": "open",
  "substatus": null,
  "initiator_type": "merchant",
  "resolution_text": null,
  "resolved_at": null,
  "created_at": "2026-06-05T10:20:30Z",
  "order_uuid": "7b2e1f04-...",
  "order_external_id": "my-order-123",
  "order_amount": 1000.00,
  "order_payment_method": "sbp",
  "evidence_count": 0
}
```

| Field | Description |
|---|---|
| `uuid` | Dispute identifier — used in `GET /disputes/{dispute_uuid}`. |
| `reason` | Dispute reason. When you open one, it's a value from the reasons table below; a dispute opened *for* you carries `check_suspended` |
| `status` | `open`, `resolved`, or `rejected`. |
| `substatus` | Outstanding proof request: `null`, `pdf_requested`, or `video_requested`. |
| `initiator_type` | Who opened it: `merchant', `admin`, or `trader` |
| `resolution_text` | Resolution note, set on close. |
| `resolved_at` | Close timestamp, `null` while open. |
| `order_uuid` / `order_external_id` | The disputed order's PrimePay UUID and your `internalId`. |
| `order_amount` / `order_payment_method` | Order amount and method. |
| `evidence_count` | Number of attached evidence files. |

---

### POST /disputes/by-order/{order_id}

Open a dispute for an order by its **UUID**.

**Content type:** `multipart/form-data`

**Form fields:**

| Field | Type | Description |
|---|---|---|
| `reason` | string | Dispute reason (see table below). **Required.** |
| `attachments` | file (repeatable) | Evidence files (images / PDF / video). Optional. |
| `evidence_urls` | string (repeatable) | Public links, downloaded server-side as evidence. Optional. |

```bash
curl --request POST \
  --url https://api.prime-pay.org/api/merchant/v1/disputes/by-order/{order_id} \
  --header 'X-Api-Key: {api_key}' \
  --form reason=no_payment \
  --form attachments=@/path/to/proof.png \
  --form 'evidence_urls=https://your-cdn.example/proof.pdf'
```

| Reason | Description |
|---|---|
| `unknown` | Unspecified / other reason |
| `has_payment` | Client insists the payment was made |
| `no_payment` | Payment was not received |
| `invalid_sum` | Incorrect amount |
| `invalid_requisites` | Wrong / mismatched requisites |

**Evidence rules:**
- Allowed formats: `jpg`, `jpeg`, `png`, `webp`, `pdf`, `mp4`, `mov` — validated
  by extension **and** file signature.
- Max size: 10 MB per file.
- `evidence_urls` must be public `http(s)` links (private/internal addresses are
  rejected).
- Accepted files run through receipt premoderation, as a payment receipt does.

**Idempotent on an existing open dispute.** If the order already has an open
dispute, the evidence is appended to it and that dispute is returned; `reason` is
unchanged. A closed dispute returns `400`. To append by dispute UUID instead, use
[`POST /disputes/{dispute_uuid}/receipt`](#post-disputesdispute_uuidreceipt).

**Response:** `201 Created` — [Dispute Object](#dispute-object). Same shape for a
new or an appended-to dispute; `evidence_count` reflects the stored files.

**Errors:** `422` invalid, unsafe, or oversize evidence, unknown reason, order
not disputable (non-final status or no assigned trader), or existing dispute
already closed · `400` duplicate evidence file · `404` order not found.

---

### POST /disputes/by-external/{external_id}

Same as above (multipart/form-data with `reason` + optional `attachments` /
`evidence_urls`), but the order is addressed by your `internalId`.

**Response:** `201 Created` — [Dispute Object](#dispute-object)

---

### GET /disputes

List your disputes (most recent first).

**Query parameters:** `skip` (default `0`), `limit` (default `50`, max `200`).

**Response:** `200 OK` — array of [Dispute Object](#dispute-object)

---

### GET /disputes/{dispute_uuid}

Get a single dispute by its `uuid`.

**Response:** `200 OK` — [Dispute Object](#dispute-object) · `404` if it is not
yours or does not exist.

---

### POST /disputes/{dispute_uuid}/receipt

Append a receipt to an open dispute, addressed by dispute UUID — typically to
satisfy a `pdf_requested` / `video_requested` request (signalled by the dispute
`substatus` in the webhook). The file is format-validated and runs through receipt
premoderation before it is attached. Repeatable while the dispute is open.

**Request:** `multipart/form-data`

| Field        | Type | Required | Description                          |
| ------------ | ---- | -------- | ------------------------------------ |
| `attachment` | file | yes      | Receipt file / photo (image or PDF). |

```bash
curl --request POST \
  --url https://api.prime-pay.org/api/merchant/v1/disputes/{dispute_uuid}/receipt \
  --header 'X-Api-Key: <key>' \
  --form attachment=@/path/to/receipt.pdf
```

**Response:** `200 OK` — [Dispute Object](#dispute-object); `evidence_count`
reflects the appended file.

**Errors:** `422` invalid file or dispute already closed · `400` duplicate file ·
`404` dispute not found.

---

## Withdrawals

Withdraw your USDT WORK balance to an external address

### Withdrawal Object

```json
{
  "id": 1,
  "uuid": "a3f1c2e4-5b6d-7e8f-9012-3456789abcde",
  "user_role": "merchant",
  "user_id": 5,
  "user_login": "acme",
  "merchant_id": 12,
  "merchant_name": "Acme",
  "amount": "99.00",
  "currency": "USDT",
  "destination_address": "TXxxxxxxxxxxxxxxxxxxxx",
  "fee_amount": "1.00",
  "status": "pending",
  "created_at": "2026-01-01T10:00:00Z",
  "updated_at": "2026-01-01T10:00:00Z",
  "processed_at": null,
  "processed_by_id": null,
  "rejection_reason": null
}
```

| Field | Description |
|---|---|
| `uuid` | Withdrawal identifier — used in `GET /finances/withdrawals/{uuid}`. |
| `amount` | Requested amount (string-encoded decimal). |
| `currency` | Withdrawal currency; `USDT`. |
| `destination_address` | Destination address the funds are sent to. |
| `fee_amount` | Fixed fee on top. |
| `status` | `pending`, `success`, or `rejected`. |
| `processed_at` | Processing timestamp, `null` while `pending`. |
| `rejection_reason` | Set when `status` is `rejected`. |
| `user_role` / `user_id` / `user_login` | Owner of the request (`merchant` for the API). |
| `merchant_id` / `merchant_name` | The owning merchant. |
| `processed_by_id` | Internal id of the operator who processed it; `null` while `pending`. |

| Status | Description |
|---|---|
| `pending` | Awaiting admin processing. |
| `success` | Processed and sent. |
| `rejected` | Rejected by admin (see `rejection_reason`). |

---

### POST /finances/withdrawals/create

Create a withdrawal request. `amount` is frozen from WORK
balance immediately.

**Request body:**

| Field | Type | Required | Description |
|---|---|---|---|
| `amount` | string/number | yes | Amount to withdraw. |
| `currency` | string | no | Defaults to `USDT`. |
| `destination_address` | string | yes | 10–255 chars. |

```json
{
  "amount": "100.00",
  "currency": "USDT",
  "destination_address": "TXxxxxxxxxxxxxxxxxxxxx"
}
```

**Response:** `201 Created` — [Withdrawal Object](#withdrawal-object)

**Errors:** `422` insufficient WORK balance, `amount` ≤ 0, or `destination_address` length out of range (10–255).

---

### GET /finances/withdrawals/{uuid}

Get a withdrawal request by its `uuid`.

**Response:** `200 OK` — [Withdrawal Object](#withdrawal-object) · `404` if it is
not yours or does not exist.

---

## Callbacks

### POST /callbacks/resend/order/{order_id}

Manually trigger a webhook resend for an order. `order_id` can be either the UUID or `internalId`.

**Response:** `200 OK`

```json
{ "message": "Callback queued for resend" }
```

---

## Webhooks

POST to `notificationUrl` (per-order, takes precedence) or `webhook_url` (merchant-level) on status transitions: `pending`, `success`, `failed`, `canceled`, `refunded`, `disputed`. Re-deliveries are possible — handler must be idempotent (key off `id`).

**Headers:** `Content-Type: application/json`, `X-Signature: <hmac-sha256-hex>`.

**Body:** the [Order Object](#order-object), serialised with sorted keys, no whitespace, `ensure_ascii=True`.

When the order has a dispute, the body additionally carries a `dispute` block:

```json
"dispute": { "id": "uuid", "status": "open", "reason": "no_payment", "substatus": "pdf_requested" }
```

`substatus` signals an outstanding proof request on the dispute:

| `substatus`       | Meaning                            |
| ----------------- | ---------------------------------- |
| `null`            | No request outstanding.            |
| `pdf_requested`   | PDF receipt required for the order. |
| `video_requested` | Video confirmation required.       |

On `pdf_requested` / `video_requested`, submit the proof via the dispute endpoints (see [Disputes](#disputes)). The webhook is re-delivered each time a request is raised.

**Verify** `HMAC-SHA256(api_secret, raw_body)` against `X-Signature`:

```python
import hashlib, hmac
expected = hmac.new(api_secret.encode(), raw_body, hashlib.sha256).hexdigest()
ok = hmac.compare_digest(expected, request.headers["X-Signature"])
```

Use the **raw request body bytes**, not a re-serialised JSON object — re-serialising drops sorted keys / whitespace / non-ASCII escapes and the hash won't match.

**Retries:** any `2xx` = delivered. Otherwise up to 5 retries with 60s delay, 10s per attempt. Manual re-trigger: `POST /callbacks/resend/order/{order_id}`.

---

## Errors

All errors return JSON:

```json
{
  "error": {
    "code": "not_found",
    "message": "Order not found"
  }
}
```


| HTTP | Code                    | Meaning                             |
| ---- | ----------------------- | ----------------------------------- |
| 400  | `validation_error`      | Invalid input                       |
| 400  | `conflict`              | Action not allowed in current state |
| 401  | `unauthorized`          | Missing or invalid credentials      |
| 403  | `forbidden`             | Access denied                       |
| 404  | `not_found`             | Resource not found                  |
| 500  | `internal_server_error` | Server error                        |


