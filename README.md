# Currency Conversion API

A **high-performance, production-ready currency conversion service** built with **FastAPI**, offering:

- Free global FX data (via Fawaz Ahmed's open API — 200+ currencies, no rate limits)  
- Fallback FX provider for reliability  
- Pretty JSON formatting  
- CORS enabled for all frontend use cases  
- API key authentication  
- API key generation & revocation  
- Rate limiting (per API key, per minute & per day)  
- Secured Swagger documentation  
- Clean modular architecture  
- Logging middleware  
- Health endpoint  
- Suitable for SaaS, mobile apps, fintech products, and internal tools  

---

# 📁 Project Structure

```
currency_api/
 ├── main.py
 ├── auth.py
 ├── rate_limit.py
 ├── utils.py
 ├── keys.json
 ├── requirements.txt
 └── README.md
```

---

# ⭐ Features

### 🔐 API Key Authentication  
All protected endpoints require an API key.  
Clients may send the key via:

```
X-API-KEY: <your-api-key>
```

Or:

```
?api_key=<your-api-key>
```

---

### 🔑 API Key Management  
Admin endpoints let you generate, revoke, and list keys.  
Keys are hashed using SHA-256 and stored in `keys.json`.

---

### 📊 Rate Limiting  
- 100 requests/minute  
- 10,000 requests/day  

---

### 💱 Currency Conversion  
Uses **Fawaz Ahmed’s Exchange-API** (no rate limits, 200+ currencies).  
Falls back to **exchangerate.host**.

---

### 🎨 Pretty JSON  
All responses are indented for readability.

---

### 🌍 CORS Enabled  
Frontend frameworks can access API without restrictions.

---

### 📜 Secured Swagger Docs  
Endpoints:  
- `/docs`  
- `/openapi.json`  

Both require a valid API key.

---

### 🔍 Logging Middleware  
Example log:

```
[GET] /convert 200 18.33ms
```

---

### 🟢 Health Endpoint

```
GET /health
```

---

# 🔧 Installation

### Clone the repository
```bash
git clone https://github.com/<your-username>/currency_api.git
cd currency_api
```

### Install dependencies
```bash
pip install -r requirements.txt
```

### Set Admin Token
```bash
export ADMIN_TOKEN=your-secret-token
```

### Run the API
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

---

# 🔐 Admin Endpoints

### Generate an API Key
```
POST /admin/generate-key
```
```json
{ "owner": "Emmanuel" }
```

### Revoke a Key
```
POST /admin/revoke-key
```
```json
{ "api_key": "<key>" }
```

### List Keys
```
GET /admin/keys
```

---

# 💱 User Endpoints

### Get FX Rate
```
GET /rate?from=USD&to=NGN
```

### Convert Amount
```
GET /convert?from=EUR&to=USD&amount=50
```

---

# 🚀 Deployment

## Railway
- Deploy from GitHub
- Build command: `pip install -r requirements.txt`
- Start command:  
  ```
  uvicorn main:app --host 0.0.0.0 --port $PORT
  ```

## Render
Same as Railway.

## AWS Lambda
Use **Mangum** adapter and deploy via API Gateway.

## Docker
```
docker build -t currency_api .
docker run -p 8000:8000 -e ADMIN_TOKEN=token currency_api
```

---

# 🔒 Security Notes

- Always keep ADMIN_TOKEN private  
- Never commit raw API keys  
- Put behind Cloudflare if needed  
- Switch rate limiting to Redis in production  

---

# ❤️ Credits

Uses public FX data from:

**Fawaz Ahmed – Exchange API**  
https://github.com/fawazahmed0/exchange-api

Fallback provider: **exchangerate.host**

---

