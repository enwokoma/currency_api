# Currency Conversion API - Detailed Reference

This document provides full endpoint-by-endpoint details, request/response samples, and workflow notes. All payloads are JSON unless noted.

- **Base URL variable**: `{{base_url}}` (e.g., `http://localhost:8000`)
- **Headers**:
  - API key: `X-API-KEY: {{free_api_key}}` or `{{paid_api_key}}`
  - Session: `X-SESSION-TOKEN: {{session_token}}`
  - Admin token: `X-ADMIN-TOKEN: {{admin_token}}`
- **Identity rule**: Every API key must carry a unique `email`, `first_name`, and `last_name`. Attempts to reuse an email for another key return HTTP 400.
- **JSON-only core endpoints**: `/rate`, `/convert`, `/rates`, `/rate/historical`, `/convert/historical`, `/rates/historical`.
- **Postman collection**: `postman_collection.json` is regenerated on each app startup so it always reflects the current OpenAPI schema.

---

## 1) Users & Sessions

### Register - `POST /auth/register`
```json
{
  "first_name": "Ada",
  "last_name": "Lovelace",
  "email": "ada@example.com",
  "password": "StrongPass123",
  "confirm_password": "StrongPass123"
}
```
**Response 201**
```json
{
  "message": "User registered successfully. Please verify your email.",
  "user": {
    "id": "uuid",
    "email": "ada@example.com",
    "email_verified": false,
    "created_at": "2025-01-01T12:00:00Z",
    "updated_at": "2025-01-01T12:00:00Z",
    "last_login_at": null
  },
  "verification": {
    "otp": "584201",
    "token": "verify_abcd...",
    "expires_at": 1730000000.0
  }
}
```

### Verify email - `POST /auth/verify-email`
```json
{
  "email": "ada@example.com",
  "otp": "584201"
}
```
> Provide **exactly one** of `otp` or `token`. Supplying both (or neither) now returns HTTP 400.

**Response 200**
```json
{
  "user": {
    "id": "uuid",
    "email": "ada@example.com",
    "email_verified": true,
    "created_at": "2025-01-01T12:00:00Z",
    "updated_at": "2025-01-01T12:00:00Z",
    "last_login_at": "2025-01-01T12:00:00Z"
  }
}
```

### Direct verification link - `GET /auth/verify-email?email=...&token=...`
- Query params: `email`, `token`

```http
GET /auth/verify-email?email=ada@example.com&token=verify_xxx
```
**Response 200**
```json
{
  "message": "Email verified successfully.",
  "user": {
    "id": "uuid",
    "email": "ada@example.com"
  }
}
```
Use this form for “click-to-verify” links in future email integrations.

### Resend verification - `POST /auth/resend-verification`
```json
{
  "email": "ada@example.com"
}
```
**Response 200**
```json
{
  "message": "Verification code has been resent.",
  "verification": {
    "otp": "123456",
    "token": "new_verify_token",
    "expires_at": 1730003600.0
  }
}
```

### Login - `POST /auth/login`
```json
{
  "email": "ada@example.com",
  "password": "StrongPass123"
}
```
**Response 200**
```json
{
  "message": "Login successful.",
  "session_token": "session_xxx",
  "expires_at": 1730000000.0,
  "user": { "id": "uuid", "email": "ada@example.com", "first_name": "Ada", "last_name": "Lovelace" }
}
```

### Current user - `GET /user`
- Header: `X-SESSION-TOKEN: {{session_token}}`
**Response 200**
```json
{
  "user": {
    "id": "uuid",
    "email": "ada@example.com",
    "first_name": "Ada",
    "last_name": "Lovelace",
    "email_verified": true,
    "created_at": "2025-01-01T12:00:00Z",
    "updated_at": "2025-01-01T12:00:00Z",
    "last_login_at": "2025-01-01T12:00:00Z"
  }
}
```

### Logout - `POST /auth/logout`
- Header: `X-SESSION-TOKEN: {{session_token}}`
**Response 200**
```json
{
  "message": "Logged out successfully."
}
```

---

## 2) API Keys (email/name required)

### Public free key - `POST /auth/free-key`
```json
{
  "email": "free.user@example.com",
  "first_name": "Free",
  "last_name": "User"
}
```
**Response 200**
```json
{
  "tier": "free",
  "api_key": "free_3be4c...",
  "note": "Free keys have limited quota. Upgrade for higher limits."
}
```
> **Important**: Emails are globally unique across free and paid keys. If an email already owns a key, this endpoint returns HTTP 400 instructing you to reuse or upgrade the existing key.

### Create key (session) - `POST /auth/create-key`
- Header: `X-SESSION-TOKEN: {{session_token}}`
- Body includes tier; contact defaults to the logged-in user's profile if omitted:
```json
{
  "tier": "paid",
  "email": "paid.user@example.com",
  "first_name": "Paid",
  "last_name": "User"
}
```
**Response 200**
```json
{
  "tier": "paid",
  "api_key": "<uuid>",
  "contact": {
    "email": "paid.user@example.com",
    "first_name": "Paid",
    "last_name": "User"
  },
  "active": false,
  "subscription_status": "pending",
  "note": "To enable paid limits, attach a subscription using /billing/upgrade or /billing/subscribe. The key will remain inactive until the billing process is successfully completed."
}
```

### Claim a key - `POST /user/keys/claim`
- Header: `X-SESSION-TOKEN: {{session_token}}`
- Attaches a key that was created outside the account (via `/auth/free-key` or `/billing/subscribe`). Possession of the raw key is the proof of ownership; keys are never reassigned by email match.
```json
{ "api_key": "<raw_api_key>" }
```
**Response 200**
```json
{
  "status": "claimed",
  "key_prefix": "abcd1234.",
  "tier": "free",
  "plan_code": null,
  "subscription_status": "free",
  "contact": { "email": "free.user@example.com", "first_name": "Free", "last_name": "User" }
}
```
> Returns 404 for an unknown key and 409 if the key is already attached to a different registered account. Claiming a key you already own is a no-op.

### List user keys - `GET /user/keys`
- Header: `X-SESSION-TOKEN: {{session_token}}`
> Only keys attached to the account are listed: those created with `/auth/create-key` or attached with `/user/keys/claim`.
**Response 200**
```json
{
  "keys": [
    {
      "tier": "paid",
      "active": true,
      "revoked": false,
      "plan_code": "PRO_MONTHLY",
      "pending_plan_code": null,
      "subscription_status": "active",
      "email": "paid.user@example.com",
      "first_name": "Paid",
      "last_name": "User",
      "created_at": "2025-01-01T12:00:00Z",
      "upgraded_at": "2025-01-10T10:00:00Z",
      "revoked_at": null,
      "key_prefix": "prefix1..."
    }
  ]
}
```

### Revoke my key (password-protected) - `POST /user/keys/revoke`
- Header: `X-SESSION-TOKEN: {{session_token}}`
```json
{ "api_key": "<raw_api_key>", "password": "your_password" }
```
**Response 200**
```json
{ "status": "revoked", "message": "API key has been disabled." }
```

### Regenerate my key (password-protected) - `POST /user/keys/regenerate`
- Header: `X-SESSION-TOKEN: {{session_token}}`
```json
{ "api_key": "<raw_api_key>", "password": "your_password" }
```
**Response 200**
```json
{
  "message": "API key rotated. Store the new key securely; the old key no longer works.",
  "api_key": "<new_raw_api_key>",
  "tier": "paid",
  "plan_code": "PRO_MONTHLY",
  "subscription_status": "active",
  "contact": {
    "email": "paid.user@example.com",
    "first_name": "Paid",
    "last_name": "User"
  }
}
```

### Revoke key by prefix (password-protected) - `POST /user/keys/revoke-by-prefix`
- Header: `X-SESSION-TOKEN: {{session_token}}`
```json
{ "key_prefix": "abcd1234", "password": "your_password" }
```
**Response 200**
```json
{ "status": "revoked", "key_prefix": "abcd1234..." }
```

### Regenerate key by prefix (password-protected) - `POST /user/keys/regenerate-by-prefix`
- Header: `X-SESSION-TOKEN: {{session_token}}`
```json
{ "key_prefix": "abcd1234", "password": "your_password" }
```
**Response 200**
```json
{
  "message": "API key rotated. Store the new key securely; the old key no longer works.",
  "api_key": "<new_raw_api_key>",
  "tier": "paid",
  "plan_code": "PRO_MONTHLY",
  "subscription_status": "active",
  "key_prefix": "abcd1234...",
  "contact": {
    "email": "paid.user@example.com",
    "first_name": "Paid",
    "last_name": "User"
  }
}
```

### Admin generate key - `POST /admin/generate-key`
- Headers: `X-ADMIN-TOKEN: {{admin_token}}`
```json
{
  "owner": "admin-owner",
  "email": "admin.generated@example.com",
  "first_name": "Admin",
  "last_name": "User",
  "plan_code": "PRO_MONTHLY",
  "activate": true
}
```
- `plan_code` is optional. Omit it and the key runs on the default paid limits (100 req/min, 10k/day) with unlimited credits. If supplied it must be a known plan, otherwise HTTP 400 `Unknown plan_code`.
- `activate` defaults to `true`; `false` issues the key inactive with `subscription_status=pending`.
**Response 200**
```json
{
  "owner": "admin-owner",
  "api_key": "<generated_api_key>",
  "plan_code": "PRO_MONTHLY",
  "contact": {
    "email": "admin.generated@example.com",
    "first_name": "Admin",
    "last_name": "User"
  },
  "active": true
}
```
### Admin list users - `GET /admin/users`
- Header: `X-ADMIN-TOKEN: {{admin_token}}`
- Query: `page` (default 1), `page_size` (default 50, max 200)
**Response 200**
```json
{
  "items": [
    {
      "id": "uuid",
      "email": "ada@example.com",
      "first_name": "Ada",
      "last_name": "Lovelace",
      "email_verified": true,
      "created_at": "2025-01-01T12:00:00Z"
    }
  ],
  "page": 1,
  "page_size": 50,
  "total": 1
}
```

### Admin revoke key - `POST /admin/revoke-key`
```json
{ "api_key": "{{free_api_key}}" }
```
**Response 200**
```json
{
  "status": "ok",
  "message": "API key revoked"
}
```

### Admin unrevoke key - `POST /admin/unrevoke-key`
- Headers: `X-ADMIN-TOKEN: {{admin_token}}`
```json
{ "api_key": "{{paid_api_key}}", "subscription_status": "active" }
```
- `subscription_status` is optional. When omitted, a revoked key is restored to a usable state: `free` for free keys, `active` for paid keys. Pass it explicitly (for example `pending`) to restore a different state.
**Response 200**
```json
{
  "status": "ok",
  "message": "API key reactivated",
  "active": true,
  "subscription_status": "active",
  "tier": "paid"
}
```

### Admin list keys - `GET /admin/keys`
- Headers: `X-ADMIN-TOKEN`
- Query: `page`, `page_size`
**Response 200**
```json
{
  "items": [
    {
      "owner": "owner-id",
      "active": true,
      "revoked": false,
      "tier": "paid",
      "plan_code": "PRO_MONTHLY",
      "pending_plan_code": null,
      "subscription_status": "active",
      "email": "paid.user@example.com",
      "first_name": "Paid",
      "last_name": "User",
      "credits": 5000,
      "credits_purchased": 5000,
      "paystack_subscription_code": "sub_xxxxxxxx",
      "created_at": "2025-01-01T12:00:00Z",
      "upgraded_at": "2025-01-10T10:00:00Z",
      "revoked_at": null,
      "key_prefix": "prefix1..."
    }
  ],
  "page": 1,
  "page_size": 50,
  "total": 2
}
```

---

## 3) Billing & Subscriptions

### Plans - `GET /plans`
**Response 200**
```json
{
  "plans": [
    {
      "code": "BASIC_MONTHLY",
      "price_naira": 2000,
      "requests_per_minute": 100,
      "monthly_credits": 10000
    },
    {
      "code": "PRO_MONTHLY",
      "price_naira": 5000,
      "requests_per_minute": 500,
      "monthly_credits": 50000
    }
  ]
}
```

### Create paid key + checkout (public) - `POST /billing/subscribe`
```json
{
  "plan_code": "BASIC_MONTHLY",
  "email": "newpaid@example.com",
  "first_name": "Bill",
  "last_name": "Able",
  "owner": "public-user"   // optional tag
}
```
**Response 200**
```json
{
  "tier": "paid",
  "api_key": "30aa6ae9-6e2a-4a9d-b8b3-22...",
  "plan": { 
    "code": "BASIC_MONTHLY",
    "name": "Basic Monthly",
    "price_naira": 2000,
    "currency": "NGN",
    "requests_per_minute": 100,
    "requests_per_day": 10000,
    "monthly_credits": 5000,
    "paystack_plan_code": "plan_xxxxxxxx"
  },
  "authorization_url": "https://checkout.paystack.com/...",
  "reference": "PS_ref_123",
  "message": "Complete payment to activate this key. It remains inactive until Paystack confirms."
}
```
> The generated key stays inactive and subscription_status=`pending` until Paystack confirms.

### Upgrade existing key - `POST /billing/upgrade`
- Header: `X-API-KEY: {{free_api_key}}` (or `X-API-KEY-PREFIX: {{free_api_key_prefix}}` if the raw key is stored safely)
```json
{
  "plan_code": "PRO_MONTHLY",
  "email": "owner@example.com",
  "first_name": "Owner",
  "last_name": "Example"
}
```
Contact fields overwrite the stored contact (and remain unique).
**Response 200**
```json
{
    "authorization_url": "https://checkout.paystack.com/...",
    "reference": "...",
    "plan": {
        "code": "PRO_MONTHLY",
        "price_naira": 5000,
        "requests_per_minute": 500,
        "monthly_credits": 50000
    }
}
```
- Safeguards: if the key already has the same active plan with remaining credits, a 400 is returned. Pending upgrades also block starting another upgrade.

### Cancel subscription - `POST /billing/cancel`
- Header: `X-API-KEY: {{paid_api_key}}` (must have Paystack subscription code/email token)
**Response 200**
```json
{
  "status": "ok",
  "message": "Subscription has been cancelled. The key has been downgraded to free tier."
}
```

### Transactions
- `GET /transactions` - header `X-API-KEY: {{any_key}}` -> returns transactions for that key.
**Response 200**
```json
{
  "transactions": [
    {
      "reference": "ref_abc123",
      "api_key_hash": "hash...",
      "email": "user@example.com",
      "plan_code": "PRO_MONTHLY",
      "paystack_plan_code": "PLN_xxx",
      "amount_naira": 5000,
      "status": "success",
      "subscription_code": "SUB_xxx",
      "email_token": "ETK_xxx",
      "authorization_url": "https://checkout.paystack.com/...",
      "created_at": 1700000000.0,
      "updated_at": 1700000000.0
    }
  ]
}
```
- `GET /admin/transactions` - header `X-ADMIN-TOKEN` (query: `page`, `page_size`) -> returns all transactions, paginated.
**Response 200**
```json
{
  "items": [
    {
      "reference": "ref_abc123",
      "api_key_hash": "hash...",
      "email": "user@example.com",
      "plan_code": "PRO_MONTHLY",
      "paystack_plan_code": "PLN_xxx",
      "status": "success",
      "amount_naira": 5000,
      "subscription_code": "SUB_xxx",
      "email_token": "ETK_xxx",
      "authorization_url": "https://checkout.paystack.com/...",
      "created_at": 1700000000.0,
      "updated_at": 1700000000.0
    }
  ],
  "page": 1,
  "page_size": 50,
  "total": 2
}
```

### Paystack webhook - `POST /paystack/webhook`
- Validates `x-paystack-signature`, upgrades/downgrades keys, stores subscription codes, and marks transactions `success|failed|cancelled`.
**Response 200**
```json
{
  "status": "ok"
}
```
- Configure your Paystack dashboard to point webhooks at `/paystack/webhook`. The handler verifies the signature using `PAYSTACK_SECRET_KEY`. All events are recorded in `transactions.json`; you can poll `GET /transactions` (per key) or `GET /admin/transactions` to monitor incoming notifications and statuses.

### Verify Paystack transaction (client flow) - `POST /billing/verify`
- Header: `X-API-KEY: {{paid_or_pending_key}}`
- Body:
```json
{
  "reference": "PS_ref_from_paystack_receipt"
}
```
- What it does:
  - Calls Paystack `/transaction/verify/{reference}` to confirm success.
  - Applies the upgrade for the calling key (idempotent) and stores subscription identifiers.
- **Response 200**
```json
{
  "status": "verified",
  "message": "Transaction verified and applied.",
  "reference": "PS_ref_from_paystack_receipt",
  "plan_code": "BASIC_MONTHLY",
  "transaction": { "...": "updated transaction record" }
}
```

### Manual reconciliation (missed webhook) - `POST /admin/reconcile-transaction`
- Header: `X-ADMIN-TOKEN: {{admin_token}}`
- Body:
```json
{
  "reference": "PS_ref_from_paystack_receipt"
}
```
- What it does:
  - Calls Paystack `/transaction/verify/{reference}` to confirm success when a webhook never arrived.
  - Uses Paystack metadata (`api_key_hash` + `plan_code`) to upgrade the key, attach subscription_code/email_token, and mark the local transaction `success`.
  - If the reference is unknown locally, it still upgrades the key and marks pending transactions for that key as `success`.
- **Response 200**
```json
{
  "status": "reconciled",
  "key_hash": "...",
  "plan_code": "BASIC_MONTHLY",
  "subscription_code": "SUB_xxxx",
  "email_token": "ETK_xxxx",
  "key": { "...": "key record with updated subscription_status/plan_code" },
  "transaction": { "...": "updated transaction record if found" }
}
```

---

## 4) Core Currency Endpoints (JSON bodies)

### Current rate - `POST /rate`
```json
{ "from_currency": "USD", "to_currency": "NGN" }
```
**Response 200**
```json
{ "from": "USD", "to": "NGN", "rate": 1630.0 }
```

### Convert amount - `POST /convert`
```json
{ "from_currency": "EUR", "to_currency": "USD", "amount": 50 }
```
**Response 200**
```json
{
    "from": "EUR",
    "to": "USD",
    "amount": 50,
    "rate": 1.09,
    "converted_amount": 54.5
}
```

### All rates for a base - `POST /rates`
```json
{ "base_currency": "USD" }
```
**Response 200**
```json
{
  "base": "USD",
  "rates": {
    "NGN": 1630.0,
    "USD": 1.0,
    "EUR": 0.92,
    "GBP": 0.79
  }
}
```

### Historical rate (paid only) - `POST /rate/historical`
```json
{ "from_currency": "USD", "to_currency": "EUR", "date": "2024-12-31" }
```
**Response 200**
```json
{
  "from": "USD",
  "to": "EUR",
  "date": "2024-12-31",
  "rate": 0.91
}
```

### Historical conversion (paid only) - `POST /convert/historical`
```json
{ "from_currency": "GBP", "to_currency": "USD", "date": "2024-12-15", "amount": 250 }
```
**Response 200**
```json
{
    "from": "GBP",
    "to": "USD",
    "date": "2024-12-15",
    "amount": 250,
    "rate": 1.27,
    "converted_amount": 317.5
}
```

### Historical rates for a base (paid only) - `POST /rates/historical`
```json
{
  "base_currency": "USD",
  "date": "2024-12-31"
}
```
**Response 200**
```json
{
  "base": "USD",
  "date": "2024-12-31",
  "rates": {
    "NGN": 1600.0,
    "USD": 1.0,
    "EUR": 0.91,
    "GBP": 0.78
  }
}
```

---

## 5) Rate Limits, Credits, and Status

- **Free**: 10 req/min, 200 req/day, 200 monthly credits (reset monthly).
- **Paid (by plan)**: limits derived from plan definition; credits reset monthly when a plan defines `monthly_credits` (unlimited if `null`).
- **Subscription status**:
  - `pending`: checkout started; keys created via `/billing/subscribe` are inactive until Paystack confirms.
  - `active`: paid subscription applied.
  - `canceled`: cancellation or failed billing; key is downgraded to free and credits reset to free tier.

---

## 6) Utility

- **Health**: `GET /health`
**Response 200**
```json
{
  "status": "ok"
}
```
- **Usage snapshot**: `GET /usage` (API key), `GET /user/usage` (session), and `GET /admin/usage` report request counts, remaining credits, and rate limits.
- **Data files**: `keys.json`, `transactions.json`, `users.json`, `sessions.json`, `admin_token.txt`. These are recreated automatically if removed.

### Usage endpoints
- `GET /usage` (header `X-API-KEY`) returns usage for the calling key:
```json
{
  "usage": {
    "tier": "free",
    "subscription_status": "free",
    "remaining_credits": 198,
    "total_requests": 12,
    "billable_requests": 12,
    "rate_limits": {
      "per_minute": 10,
      "per_day": 200
    },
    "last_request_at": "2025-11-16T17:30:10.123",
    "key_prefix": "abcd1234."
  }
}
```
**Response 200**
```json
{
  "usage": [
    {
      "owner": "user-id",
      "email": "paid.user@example.com",
      "tier": "paid",
      "subscription_status": "active",
      "remaining_credits": 9988,
      "total_requests": 12,
      "billable_requests": 12,
      "rate_limits": {
        "per_minute": 500,
        "per_day": 5000
      },
      "last_request_at": "2025-11-16T17:30:10.123",
      "key_prefix": "prefix1..."
    },
    {
      "owner": "user-id",
      "email": "free.user@example.com",
      "tier": "free",
      "subscription_status": "free",
      "remaining_credits": 188,
      "total_requests": 12,
      "billable_requests": 12,
      "rate_limits": {
        "per_minute": 10,
        "per_day": 200
      },
      "last_request_at": "2025-11-16T17:30:10.123",
      "key_prefix": "prefix2..."
    }
  ]
}
```
**Response 200**
```json
{
  "items": [
    {
      "owner": "user-id",
      "email": "paid.user@example.com",
      "tier": "paid",
      "plan_code": "PRO_MONTHLY",
      "subscription_status": "active",
      "total_requests": 12,
      "billable_requests": 12,
      "credits_used": 12,
      "first_request_at": "2025-11-16T17:30:10.123",
      "last_request_at": "2025-11-16T17:45:10.123"
    },
    {
      "owner": "user-id",
      "email": "free.user@example.com",
      "tier": "free",
      "plan_code": null,
      "subscription_status": "free",
      "total_requests": 12,
      "billable_requests": 12,
      "credits_used": 12,
      "first_request_at": "2025-11-16T17:30:10.123",
      "last_request_at": "2025-11-16T17:45:10.123"
    }
  ],
  "page": 1,
  "page_size": 50,
  "total": 2
}
```
