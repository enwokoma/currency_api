# Currency Conversion API - Detailed Reference

This document provides full endpoint-by-endpoint details, request/response samples, and workflow notes. All payloads are JSON unless noted.

- **Base URL variable**: `{{base_url}}` (e.g., `http://localhost:8000`)
- **Headers**:
  - API key: `X-API-KEY: {{free_api_key}}` or `{{paid_api_key}}`
  - Session: `X-SESSION-TOKEN: {{session_token}}`
  - Admin token: `X-ADMIN-TOKEN: {{admin_token}}`
- **Identity rule**: Every API key must carry a unique `email`, `first_name`, and `last_name`. Attempts to reuse an email for another key return HTTP 400.
- **JSON-only core endpoints**: `/rate`, `/convert`, `/rates`, `/rate/historical`, `/convert/historical`, `/rates/historical`.

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
  "user": { "id": "uuid", "email": "ada@example.com", "email_verified": false, "created_at": "2025-01-01T12:00:00Z", "updated_at": "2025-01-01T12:00:00Z", "last_login_at": null },
  "verification": { "otp": "584201", "token": "verify_abcd...", "expires_at": 1730000000.0 }
}
```

### Verify email - `POST /auth/verify-email`
```json
{ "email": "ada@example.com", "otp": "584201" }
```
**Response 200** - returns sanitized user with `email_verified: true`.

### Resend verification - `POST /auth/resend-verification`
```json
{ "email": "ada@example.com" }
```
**Response 200** - includes new `otp` and `token` (demo-only).

### Login - `POST /auth/login`
```json
{ "email": "ada@example.com", "password": "StrongPass123" }
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
- **Response 200** - `{ "user": { ...sanitized user... } }`

### Logout - `POST /auth/logout`
- Header: `X-SESSION-TOKEN: {{session_token}}`
- **Response 200** - `{ "message": "Logged out successfully." }`

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
**Response 200** - returns 
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
  "note": "Attach a subscription using /billing/upgrade or /billing/subscribe to enable paid limits. The key stays inactive until billing completes."
}
```

### List user keys - `GET /user/keys`
- Header: `X-SESSION-TOKEN: {{session_token}}`
- **Response 200** - keys with `tier`, `plan_code`, `pending_plan_code`, `subscription_status`, contact fields, timestamps, and a `key_prefix`.

### Revoke/regenerate my key (password-protected)
- `POST /user/keys/revoke` – body `{ "api_key": "<raw>", "password": "..." }`
- `POST /user/keys/regenerate` – body `{ "api_key": "<raw>", "password": "..." }` (returns new raw key; old key is invalidated)
- If you've misplaced the raw key, use the prefix from `/user/keys`:
  - `POST /user/keys/revoke-by-prefix` – `{ "key_prefix": "abcd1234", "password": "..." }`
  - `POST /user/keys/regenerate-by-prefix` – `{ "key_prefix": "abcd1234", "password": "..." }` (returns new raw key)
All flows confirm your password and require the session token.

### Admin generate key - `POST /admin/generate-key`
- Headers: `X-ADMIN-TOKEN: {{admin_token}}`
```json
{
  "owner": "ops",
  "email": "ops@example.com",
  "first_name": "Ops",
  "last_name": "Team",
  "plan_code": null,
  "activate": true
}
```
**Response 200** - includes raw `api_key`, `plan_code`, `active` flag, and contact info.

### Admin revoke key - `POST /admin/revoke-key`
```json
{ "api_key": "{{free_api_key}}" }
```
**Response 200** - `{ "status": "ok", "message": "API key revoked" }`

### Admin list keys - `GET /admin/keys`
- Headers: `X-ADMIN-TOKEN`
- **Response 200** - all keys with masked hashes, contact, plan/subscription status, and Paystack subscription code (if any).

---

## 3) Billing & Subscriptions

### Plans - `GET /plans`
- **Response 200** - `{ "plans": [ { "code": "BASIC_MONTHLY", "price_naira": 2000, "requests_per_minute": 100, ... }, ... ] }`

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
  "plan": { "code": "BASIC_MONTHLY", ... },
  "authorization_url": "https://checkout.paystack.com/...",
  "reference": "PS_ref_123",
  "message": "Complete payment to activate this key. It remains inactive until Paystack confirms."
}
```
> The generated key stays inactive and subscription_status=`pending` until Paystack confirms.

### Upgrade existing key - `POST /billing/upgrade`
- Header: `X-API-KEY: {{free_api_key}}`
```json
{ "plan_code": "PRO_MONTHLY", "email": "owner@example.com", "first_name": "Owner", "last_name": "Example" }
```
Contact fields overwrite the stored contact (and remain unique).  
**Response 200** - `{ "authorization_url": "...", "reference": "...", "plan": { ... } }`
- Safeguards: if the key already has the same active plan with remaining credits, a 400 is returned. Pending upgrades also block starting another upgrade.

### Cancel subscription - `POST /billing/cancel`
- Header: `X-API-KEY: {{paid_api_key}}` (must have Paystack subscription code/email token)
- **Response 200** - `{ "status": "cancelled", "message": "Subscription cancelled and key downgraded to free." }`

### Transactions
- `GET /transactions` - header `X-API-KEY: {{any_key}}` -> returns transactions for that key.
- `GET /admin/transactions` - header `X-ADMIN-TOKEN` -> returns all transactions.

### Paystack webhook - `POST /paystack/webhook`
- Validates `x-paystack-signature`, upgrades/downgrades keys, stores subscription codes, and marks transactions `success|failed|cancelled`.
- **Response 200** - `{ "status": "ok" }`
- Configure your Paystack dashboard to point webhooks at `/paystack/webhook`. The handler verifies the signature using `PAYSTACK_SECRET_KEY`. All events are recorded in `transactions.json`; you can poll `GET /transactions` (per key) or `GET /admin/transactions` to monitor incoming notifications and statuses.

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
**Response 200** - includes `rate` and `converted_amount`.

### All rates for a base - `POST /rates`
```json
{ "base_currency": "USD" }
```
**Response 200** - `{ "base": "USD", "rates": { "ngn": 1630.0, "usd": 1.0, ... } }`

### Historical rate (paid only) - `POST /rate/historical`
```json
{ "from_currency": "USD", "to_currency": "EUR", "date": "2024-12-31" }
```
**Response 200** - `{ "from": "USD", "to": "EUR", "date": "2024-12-31", "rate": 0.91 }`

### Historical conversion (paid only) - `POST /convert/historical`
```json
{ "from_currency": "GBP", "to_currency": "USD", "date": "2024-12-15", "amount": 250 }
```
**Response 200** - includes `rate` and `converted_amount`.

### Historical rates for a base (paid only) - `POST /rates/historical`
```json
{ "base_currency": "USD", "date": "2024-12-31" }
```
**Response 200** - `{ "base": "USD", "date": "2024-12-31", "rates": { ... } }`

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

- **Health**: `GET /health` -> `{ "status": "ok" }`
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
    "rate_limits": { "per_minute": 10, "per_day": 200 },
    "last_request_at": "2025-11-16T17:30:10.123",
    "key_prefix": "abcd1234."
  }
}
```
- `GET /user/usage` (header `X-SESSION-TOKEN`) lists usage for all keys owned by the logged-in user.
- `GET /admin/usage` (header `X-ADMIN-TOKEN`) lists every key with owner/contact, rate limits, credits, and usage counters.
