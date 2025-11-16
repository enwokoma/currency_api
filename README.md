# Currency Conversion API

FastAPI-powered currency conversion service with free and paid tiers, Email/Name-bound API keys, Paystack billing, and rate-limited JSON responses. The app stores data in local JSON files (no database required) and ships with user sessions, admin tooling, and a Postman collection for quick exploration.

---

## Highlights

- API keys are tied to a unique email, first name, and last name for clear ownership across all flows (public, user, and admin generated).
- Direct paid provisioning via `POST /billing/subscribe` (no need to create a free key first), plus upgrades for existing keys and `POST /billing/cancel` to disable a subscription on Paystack and downgrade to free.
- Currency lookups and conversions now accept **JSON bodies** (`POST /rate`, `/convert`, `/rates`) with historical endpoints (`/rate/historical`, `/convert/historical`, `/rates/historical`) available exclusively to paid keys.
- Paystack webhook handling upgrades/downgrades keys, records transactions, and captures subscription codes for cancellation.
- Per-key usage counters with `/usage`, `/user/usage`, and `/admin/usage` to see request counts, billable calls, and remaining credits.
- Built-in user management (register/verify/login/logout), session tokens, admin token-protected endpoints, and JSON-based persistence (`keys.json`, `transactions.json`, `users.json`, `sessions.json`).

---

## Quickstart

1. **Install deps**
   ```bash
   python -m venv .venv
   ./.venv/Scripts/pip install -r requirements.txt
   ```
2. **Configure environment**
   - `PAYSTACK_SECRET_KEY` (required for real billing)
   - `PAYSTACK_PLAN_BASIC`, `PAYSTACK_PLAN_PRO` (Paystack plan codes)
   - Optional: `ADMIN_TOKEN` (otherwise written to `admin_token.txt` on first run)
3. **Run the API**
   ```bash
   ./.venv/Scripts/python -m uvicorn main:app --reload
   ```
4. **Admin token**: On first start a secure token is written to `admin_token.txt`. Use it in the `X-ADMIN-TOKEN` header for admin routes.

---

## Usage Map (concise)

- **Users**: `POST /auth/register` -> `POST /auth/verify-email` -> `POST /auth/login` (stores session token in `X-SESSION-TOKEN`).
- **Key creation (requires email/name)**:
  - Public: `POST /auth/free-key` (free tier).
  - Authenticated: `POST /auth/create-key` with `tier` = `free` or `paid` (paid keys remain inactive with `subscription_status=pending` until upgraded via billing).
  - Admin: `POST /admin/generate-key` (paid, optional plan), `POST /admin/revoke-key`.
  - Listing: `GET /user/keys`, `GET /admin/keys`.
- **Billing**:
  - Create paid key + checkout: `POST /billing/subscribe` (public).
  - Upgrade existing key: `POST /billing/upgrade` (uses caller's API key contact details).
  - Cancel: `POST /billing/cancel` (paid key required).
  - Plans/transactions: `GET /plans`, `GET /transactions`, `GET /admin/transactions`.
  - Webhooks: `POST /paystack/webhook` (expects Paystack signature).
- **Core currency endpoints (JSON bodies only)**:
  - Current: `POST /rate`, `POST /convert`, `POST /rates`.
  - Historical (paid keys only): `POST /rate/historical`, `POST /convert/historical`, `POST /rates/historical`.
- **Identity rules**: Each API key email is unique; attempts to reuse an email will fail with a clear error.

Full request/response samples for every endpoint live in `DOCS.md`.

---

## Documentation & Tools

- **Detailed docs**: See [`DOCS.md`](DOCS.md) for endpoint-by-endpoint request/response bodies, headers, and workflow notes.
- **Postman collection**: Import `postman_collection.json` and set variables:
  - `base_url` (e.g., `http://localhost:8000`)
  - `admin_token`, `session_token`, `free_api_key`, `paid_api_key`, `plan_code`
  - Optional: `user_first_name`, `user_last_name`, `user_email`, `user_password` (default bodies use these). Override contact with `contact_email`, `contact_first_name`, `contact_last_name` only if you want a different billing contact.
- Lost key recovery (session + password required):
  - List prefixes: `GET /user/keys`
  - Revoke: `POST /user/keys/revoke` (raw key) or `POST /user/keys/revoke-by-prefix`
  - Rotate: `POST /user/keys/regenerate` (raw key) or `POST /user/keys/regenerate-by-prefix` (returns new raw key)

---

## Testing

The user management smoke tests have been executed with the project virtual environment:

```bash
./.venv/Scripts/python.exe test_user_management.py
```

---

## Data & Rate Limits

- Keys, users, sessions, and transactions are stored in JSON files in the project root (`keys.json`, `users.json`, `sessions.json`, `transactions.json`).
- Per-key request counters live in `usage.json` and power `/usage`, `/user/usage`, and `/admin/usage`.
- Rate limits and monthly credits are enforced per key tier/plan (free: 10 req/min, 200/day by default; paid limits derive from plan definitions in `main.py`).
- Free and paid credits reset automatically each month; pending subscriptions mark keys accordingly until Paystack confirms.
