# 🌍 Currency Conversion API

A **production-ready, high-performance currency conversion API** built with **FastAPI**, featuring:

- Free & paid API key tiers  
- Public free-tier key auto-generation (`/auth/free-key`)  
- FX data from **Fawaz Ahmed Exchange-API** (200+ currencies, no rate limits)  
- Intelligent fallback FX provider  
- Pretty JSON output  
- Secure API key authentication  
- API key generation & revocation (Admin protected)  
- Tier-based rate limiting  
- CORS enabled  
- Logging middleware  
- Secured Swagger docs  
- Health endpoint  
- Clean modular architecture  

---

# 📁 Project Structure

```
currency_api/
 ├── main.py
 ├── auth.py
 ├── rate_limit.py
 ├── utils.py
 ├── admin_token.txt
 ├── keys.json
 ├── requirements.txt
 └── README.md
```

---

# ⭐ Features

## 🔐 API Key Authentication
All endpoints (except health & free-tier key generation) require:

```
X-API-KEY: <your-key>
```

Or:

```
?api_key=<your-key>
```

---

## 🆓 Free Tier + Paid Tier (Auto-Generated Keys)

### 🔸 **Free Tier (public)**
Generate instantly:

```
POST /auth/free-key
```

Response:
```json
{
  "tier": "free",
  "api_key": "free_91b3fa123ce94d18",
  "note": "Free-tier keys have limited quota. Upgrade for unlimited usage."
}
```

### Free Tier Limits:
| Tier   | Requests/min | Requests/day |
|--------|--------------|--------------|
| Free   | 10           | 200          |

---

### 🔹 **Paid Tier (admin-issued)**

Admin can generate:

```
POST /admin/generate-key
```

---

## 🛡️ Admin Token (Auto-Generated)
The API automatically generates a secure admin token and saves it to `admin_token.txt`.

Example:

```
admtok_8f41e0c7ee2443e498c59c32a8d1bfd1
```

Use it for all admin endpoints:

```
X-ADMIN-TOKEN: admtok_xxxxxxxxxxxxx
```

---

## 🔑 API Key Management

### ✔ Generate Paid API Key (Admin)
```
POST /admin/generate-key
Body:
{
  "owner": "Emmanuel"
}
```

### ✔ Revoke Key (Admin)
```
POST /admin/revoke-key
Body:
{
  "api_key": "..."
}
```

### ✔ List All Keys (Admin)
```
GET /admin/keys
```

---

## 💱 Currency Endpoints (Require API Key)

### ✔ Get FX Rate
```
GET /rate?from=USD&to=NGN&api_key=<key>
```

Result:
```json
{
  "from": "USD",
  "to": "NGN",
  "rate": 1630.23
}
```

### ✔ Convert Amount
```
GET /convert?from=EUR&to=USD&amount=50&api_key=<key>
```

---

## 🔁 FX Provider Fallback
1. **Primary:** Fawaz Ahmed Exchange-API  
2. **Fallback:** exchangerate.host  

---

## 🟢 Health Check
```
GET /health
```

---

# 🔧 Installation

Clone:
```bash
git clone https://github.com/<your-username>/currency_api.git
cd currency_api
```

Install dependencies:
```bash
pip install -r requirements.txt
```

Run:
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

---

# 📜 Secured Documentation

Swagger UI:
```
GET /docs?api_key=<your-key>
```

OpenAPI schema:
```
GET /openapi.json?api_key=<your-key>
```

---

# 🚀 Deployment

## Railway
- Deploy repo  
- Build: `pip install -r requirements.txt`  
- Start:  
  ```
  uvicorn main:app --host 0.0.0.0 --port $PORT
  ```
- Set environment variables if needed  

## Render
Same as Railway.

## AWS Lambda (Serverless)
- Add `lambda_handler.py`:
```python
from main import app
from mangum import Mangum
handler = Mangum(app)
```
- Deploy via API Gateway  

## Docker
```
docker build -t currency_api .
docker run -p 8000:8000 -e ADMIN_TOKEN=your-token currency_api
```

---

# 🔒 Security Recommendations

- Do **NOT** commit `admin_token.txt` or `keys.json`  
- Always rotate admin tokens when compromised  
- Use HTTPS in production  
- Use Redis if scaling horizontally  

---

# ❤️ Credits

FX data via:

**Fawaz Ahmed – Exchange API**  
https://github.com/fawazahmed0/exchange-api  

Fallback:  
**exchangerate.host**

---

# 📄 License

MIT License  
See `LICENSE` file.
