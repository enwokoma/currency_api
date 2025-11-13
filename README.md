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
| `GET /rate?from=USD&to=NGN` | Return the exchange rate from one currency to another |
| `GET /convert?from=EUR&to=USD&amount=50` | Convert an amount from one currency to another |

### Auth Endpoints

| Method & path | Description |
|---------------|-------------|
| `POST /auth/free-key` | Obtain a free‑tier API key instantly |

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

The API also exposes a health check at `/health` (no auth) and
secured documentation at `/docs` and `/openapi.json` (API key
required).

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