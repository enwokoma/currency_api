# Currency Conversion API

Welcome to the **Currency Conversion API** – a high‑performance,
subscription‑aware service for converting amounts between world
currencies. The API is built with [FastAPI](https://fastapi.tiangolo.com/)
and powered by the open‑source
[Exchange‑API](https://github.com/fawazahmed0/exchange-api) project. The
upstream dataset offers over 200 currency rates (including common
cryptocurrencies and metals) and is updated daily【342527198209133†L11-L16】.

This repository packages the exchange data into a secure, rate‑limited
service with free and paid tiers, Paystack billing integration, and
pretty‑printed JSON responses. Whether you need a simple rate lookup
or a full subscription flow for your application, this API has you
covered.

---

## 🔑 Authentication & Tiers

All core endpoints require an **API key**. Keys come in two flavours:

| Tier   | How to obtain | Limits (per minute / per day) | Notes |
|--------|---------------|-------------------------------|------|
| **Free** | `POST /auth/free-key` | 10 requests per minute, 200 per day | Great for prototypes and low‑volume usage |
| **Paid** | Admin‑generated or upgraded via Paystack | Depends on plan (e.g. 100/10k or 300/50k) | Unlock higher limits and premium support |

API keys are sent via HTTP header (`X-API-KEY`) or query
parameter (`api_key`). If absent or invalid, the API returns
`401 Unauthorized`.

## 👤 User Registration & Sessions

In addition to anonymous keys, the API supports full user accounts.  Creating an account lets you manage multiple keys, upgrade plans and view your profile.  The typical flow is:

1. **Register** with `POST /auth/register` by providing your name, email and password.  The response includes a verification OTP and a token for demonstration; in production you would send these via email.
2. **Verify** your email using `POST /auth/verify-email` with either the `otp` or `token` returned during registration.  Unverified users cannot log in.
3. **Log in** via `POST /auth/login` to obtain a session token.  Include the session token in the `X‑SESSION‑TOKEN` header for subsequent user‑protected calls.
4. **Create keys** with `POST /auth/create-key`, specifying `"free"` or `"paid"` for the tier.  Keys created through this endpoint are bound to your account and can later be upgraded via the billing flow.
5. **List keys** using `GET /user/keys` and view your profile with `GET /user`.
6. **Log out** with `POST /auth/logout` to revoke the current session.

Below are request/response examples for the user endpoints.

### Register a new user

```
POST /auth/register
Content-Type: application/json

{
  "first_name": "Jane",
  "last_name": "Doe",
  "email": "jane.doe@example.com",
  "password": "Str0ngP@ss",
  "confirm_password": "Str0ngP@ss"
}
```

Response (201):

```
{
  "message": "User registered successfully. Please verify your email.",
  "user": {
    "id": "<uuid>",
    "first_name": "Jane",
    "last_name": "Doe",
    "email": "jane.doe@example.com",
    "email_verified": false,
    "created_at": "2025-01-01T12:00:00Z",
    "updated_at": "2025-01-01T12:00:00Z",
    "last_login_at": null
  },
  "verification": {
    "otp": "123456",
    "token": "verify_abc123…",
    "expires_at": 1700000000.0
  }
}
```

### Verify your email

```
POST /auth/verify-email
Content-Type: application/json

{
  "email": "jane.doe@example.com",
  "otp": "123456"
}
```

Response (200):

```
{
  "message": "Email verified successfully.",
  "user": {
    "id": "<uuid>",
    "first_name": "Jane",
    "last_name": "Doe",
    "email": "jane.doe@example.com",
    "email_verified": true,
    "created_at": "2025-01-01T12:00:00Z",
    "updated_at": "2025-01-01T12:05:00Z",
    "last_login_at": null
  }
}
```

You can also verify via the `token` returned during registration:

```
POST /auth/verify-email
{
  "email": "jane.doe@example.com",
  "token": "verify_abc123…"
}
```

### Log in

```
POST /auth/login
Content-Type: application/json

{
  "email": "jane.doe@example.com",
  "password": "Str0ngP@ss"
}
```

Response (200):

```
{
  "message": "Login successful.",
  "session_token": "session_abc123…",
  "expires_at": 1.700000012e9,
  "user": {
    "id": "<uuid>",
    "first_name": "Jane",
    "last_name": "Doe",
    "email": "jane.doe@example.com",
    "email_verified": true,
    "created_at": "2025-01-01T12:00:00Z",
    "updated_at": "2025-01-01T12:05:00Z",
    "last_login_at": "2025-01-01T12:10:00Z"
  }
}
```

### Create a key (session required)

```
POST /auth/create-key
X-SESSION-TOKEN: session_abc123…
Content-Type: application/json

{
  "tier": "free"
}
```

Response (free key):

```
{
  "tier": "free",
  "api_key": "free_ef2cabe0d5…"
}
```

To create a paid key, set the tier to `"paid"`.  Paid keys begin with a random UUID and have no `free_` prefix.

### List your keys

```
GET /user/keys
X-SESSION-TOKEN: session_abc123…
```

Response:

```
{
  "keys": [
    {
      "tier": "free",
      "active": true,
      "plan_code": null,
      "created_at": "2025-01-01T12:15:00.000",
      "upgraded_at": null,
      "key_prefix": "aed79f4c…"
    },
    {
      "tier": "paid",
      "active": true,
      "plan_code": null,
      "created_at": "2025-01-01T12:16:00.000",
      "upgraded_at": null,
      "key_prefix": "c1e452a8…"
    }
  ]
}
```

### Get your profile

```
GET /user
X-SESSION-TOKEN: session_abc123…
```

Response:

```
{
  "user": {
    "id": "<uuid>",
    "first_name": "Jane",
    "last_name": "Doe",
    "email": "jane.doe@example.com",
    "email_verified": true,
    "created_at": "2025-01-01T12:00:00Z",
    "updated_at": "2025-01-01T12:05:00Z",
    "last_login_at": "2025-01-01T12:10:00Z"
  }
}
```

### Log out

```
POST /auth/logout
X-SESSION-TOKEN: session_abc123…
```

Response:

```
{
  "message": "Logged out successfully."
}
```

### 🔐 Admin Token

Administrative endpoints (key generation, revocation, listing) are
protected by a separate **admin token**. On first run the service
generates a secure token and writes it to `admin_token.txt`; you can
also set `ADMIN_TOKEN` in your environment. Use this token in the
`X-ADMIN-TOKEN` header to call admin routes.

---

## 📑 Endpoints Overview

### Core Endpoints (API key required)

| Method & path | Description |
|---------------|-------------|
| `GET /me/usage` | Return remaining credits, rate limits and next reset for your key |
| `GET /rate?from=USD&to=NGN` | Return the exchange rate from one currency to another |
| `GET /convert?from=EUR&to=USD&amount=50` | Convert an amount from one currency to another |

### Auth & User Endpoints

This API now supports full user management in addition to API key creation.  You can either request an anonymous free key for quick prototyping or create a user account to manage multiple keys and subscription upgrades.  User‑bound keys record the owner and can later be upgraded via Paystack.  Session‑based endpoints require a valid session token in the `X‑SESSION‑TOKEN` header or `session_token` query parameter.

| Method & path | Description |
|---------------|-------------|
| `POST /auth/register` | Register a new user account.  Expects JSON with `first_name`, `last_name`, `email`, `password`, and `confirm_password`.  Returns a sanitized user record along with an OTP and verification link token. |
| `POST /auth/resend-verification` | Resend a verification code/link for an unverified email.  Send a JSON body containing the `email`.  Returns a new OTP/token. |
| `POST /auth/verify-email` | Verify your email using either an `otp` or a `token`.  Requires a JSON body with `email` and either `otp` or `token`.  Returns a sanitized user record upon success. |
| `POST /auth/login` | Log in with your verified email and password.  Expects JSON with `email` and `password`.  Returns a session token and user details. |
| `POST /auth/logout` | Log out by revoking your current session token.  Provide the token in the `X‑SESSION‑TOKEN` header. |
| `GET /user` | Retrieve your own profile information.  Requires a valid session token. |
| `POST /auth/create-key` | Create a new API key tied to your account.  Send JSON with `tier` set to `"free"` or `"paid"`.  Returns the newly generated raw key.  Requires a session token. |
| `GET /user/keys` | List all API keys you own.  Returns metadata for each key (tier, plan, creation date, etc.).  Requires a session token. |
| `POST /auth/free-key` | Obtain a free‑tier API key without registering.  Ideal for quick tests. |

### Billing Endpoints

| Method & path | Description |
|---------------|-------------|
| `GET /plans` | List available subscription plans |
| `POST /billing/upgrade` | Start a Paystack transaction to upgrade a key |
| `POST /paystack/webhook` | Receive Paystack events and apply upgrades/downgrades |

### Admin Endpoints (admin token required)

| Method & path | Description |
|---------------|-------------|
| `POST /admin/generate-key` | Generate a new paid API key |
| `POST /admin/revoke-key` | Revoke an existing API key |
| `GET /admin/keys` | List all keys with metadata |
| `GET /admin/usage` | Admin report: usage and limits for all keys |

The API also exposes a health check at `/health` (no auth) and
secured documentation at `/docs` and `/openapi.json` (API key
required), plus public documentation at `/public/docs`, `/public/redoc`
and `/public/openapi.json` that can be browsed without a key.

---

## 💡 How It Works

1. **Get a free key**: `POST /auth/free-key` returns a JSON object
   containing a `free_` prefixed API key. Use it in the `X-API-KEY` header.
2. **Hit rate limits**: Free keys are limited to 10 requests/min and
   200/day. Paid keys without a plan fall back to 100/min and
   10 000/day.
3. **List plans**: `GET /plans` shows available subscription
   options. Each plan has a price (in NGN) and custom rate limits.
4. **Upgrade via Paystack**:
   - Call `POST /billing/upgrade` with your email and the `plan_code`
     you want. The request must be authenticated with the key you wish
     to upgrade.
   - The API returns an `authorization_url` and a `reference`.
   - Redirect your user to the URL to complete payment.
   - Paystack sends a webhook to `/paystack/webhook`. On success, the
     key’s tier becomes `paid` and its plan code is stored. If
     subscription is disabled or a payment fails, the key downgrades
     back to free.

5. **Monthly credit reset**:
   Each API key has a monthly credit budget (free‑tier or plan‑specific).
   Credits reset automatically every 30 days based on a `credits_reset_at`
   timestamp stored in the key record. When the reset time is reached,
   the service replenishes the key’s credits and moves the reset
   timestamp forward. You can view your remaining credits and the
   next reset date via `GET /me/usage`.

---

## 🚀 Installation & Running Locally

1. **Clone the repo** and change into it:

   ```bash
   git clone https://github.com/<your-username>/currency_api.git
   cd currency_api
   ```

2. **Install dependencies**:

   ```bash
   pip install -r requirements.txt
   ```

3. **Set environment variables** (optional but recommended):

   - `ADMIN_TOKEN` – overrides the generated admin token.
   - `PAYSTACK_SECRET_KEY` – your Paystack secret for billing.

   For example:

   ```bash
   export ADMIN_TOKEN=super-secret-admin
   export PAYSTACK_SECRET_KEY=sk_test_xxxxxxxx
   ```

4. **Run the server**:

   ```bash
   uvicorn currency_api.main:app --reload --host 0.0.0.0 --port 8000
   ```

5. **Generate your first paid key** (if you’re the admin):

   ```bash
   curl -X POST http://localhost:8000/admin/generate-key \
     -H "X-ADMIN-TOKEN: <your-admin-token>" \
     -H "Content-Type: application/json" \
     -d '{"owner": "Alice"}'
   ```

6. **Call the API**:

   ```bash
   curl "http://localhost:8000/convert?from=USD&to=NGN&amount=100" \
     -H "X-API-KEY: <your-api-key>"
   ```

---

## 🔧 Deployment

This service is suitable for deployment on any cloud or self‑hosted
platform. For convenience, here are some guidelines:

### Railway

1. Push this repo to GitHub.
2. Create a new Railway project and link the repository.
3. Set environment variables in Railway:
   - `ADMIN_TOKEN` (optional)
   - `PAYSTACK_SECRET_KEY`
4. Use the build command `pip install -r requirements.txt` and start
   command:

   ```bash
   uvicorn currency_api.main:app --host 0.0.0.0 --port ${PORT:-8000}
   ```
5. Deploy! Railway handles HTTPS and scaling.

### Render

1. Create a Web Service from your GitHub repo.
2. Choose a Python environment; set build command `pip install -r
   requirements.txt`.
3. Set start command and environment variables as above.
4. Render automatically provisions HTTPS for you.

### AWS Lambda (serverless)

1. Install [`Mangum`](https://github.com/erm/mangum) to adapt FastAPI
   for Lambda: `pip install mangum`.
2. Add a `lambda_main.py` file:

   ```python
   from currency_api.main import app
   from mangum import Mangum

   handler = Mangum(app)
   ```

3. Package dependencies or build a container image.
4. Connect an API Gateway to the Lambda function.

### Docker / VPS

Create a `Dockerfile`:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt
ENV ADMIN_TOKEN=change-me
EXPOSE 8000
CMD ["uvicorn", "currency_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Build and run:

```bash
docker build -t currency-api .
docker run -p 8000:8000 -e ADMIN_TOKEN=super-secret -e PAYSTACK_SECRET_KEY=sk_test_xxxx currency-api
```

---

## 📒 License & Credits

This project is licensed under the MIT license. It builds upon the
open exchange data published by Fawaz Ahmed under the CC0-1.0
license【342527198209133†L11-L16】.

If you need plan customizations, cancellation flows, or additional
features such as a web dashboard, feel free to extend the code! Pull
requests are welcome.